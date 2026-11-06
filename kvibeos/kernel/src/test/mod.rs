//! In-kernel test mode. Active only when ALL of these hold:
//! 1. CPUID reports a hypervisor,
//! 2. the QEMU fw_cfg signature is present,
//! 3. fw_cfg file `opt/kvibe/test` names a suite.
//!
//! Output protocol (tests/protocol.md):
//!   KVIBE-TEST <suite>/<case> PASS|FAIL|SKIP <detail>
//!   KVIBE-RESULT PASS|FAIL
pub mod exit;
mod harness;
mod smoke;

use crate::{arch, drivers::fwcfg, kprintln};
use core::sync::atomic::{AtomicBool, AtomicU32, Ordering};

static ACTIVE: AtomicBool = AtomicBool::new(false);
static FAILURES: AtomicU32 = AtomicU32::new(0);
static CASES: AtomicU32 = AtomicU32::new(0);

pub fn active() -> bool {
    ACTIVE.load(Ordering::Relaxed)
}

/// Record one test case result.
pub fn report(suite: &str, case: &str, ok: bool, detail: &str) {
    CASES.fetch_add(1, Ordering::Relaxed);
    if !ok {
        FAILURES.fetch_add(1, Ordering::Relaxed);
    }
    kprintln!(
        "KVIBE-TEST {}/{} {} {}",
        suite,
        case,
        if ok { "PASS" } else { "FAIL" },
        detail
    );
}

/// Print the suite result and exit QEMU. A suite with zero cases is a failure.
pub fn finish() -> ! {
    let ok = FAILURES.load(Ordering::Relaxed) == 0 && CASES.load(Ordering::Relaxed) > 0;
    kprintln!("KVIBE-RESULT {}", if ok { "PASS" } else { "FAIL" });
    if ok { exit::pass() } else { exit::fail() }
}

/// Enter test mode if (and only if) the gate conditions hold. Never returns in test mode.
pub fn run_if_requested() {
    if !arch::cpu::hypervisor_present() || !fwcfg::present() {
        return;
    }
    let mut buf = [0u8; 64];
    let Some(n) = fwcfg::read_file("opt/kvibe/test", &mut buf) else {
        return;
    };
    let Ok(raw) = core::str::from_utf8(&buf[..n]) else {
        return;
    };
    let suite = raw.trim_matches(|c: char| c == '\0' || c.is_whitespace());
    if suite.is_empty() {
        return;
    }

    ACTIVE.store(true, Ordering::Relaxed);
    kprintln!("KVIBE-TESTMODE suite={}", suite);
    match suite {
        "smoke" => smoke::run(),
        s if s.starts_with("harness-") => harness::run(s),
        other => {
            report(other, "lookup", false, "unknown suite");
            finish();
        }
    }
}
