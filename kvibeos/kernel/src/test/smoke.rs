//! M0 smoke suite: proves boot path, serial, Limine handshake and the test gate.
use super::{finish, report};
use crate::{BASE_REVISION, BOOTLOADER_INFO, LIMINE_BASE_REVISION, arch::cpu};

pub fn run() -> ! {
    const S: &str = "smoke";
    // Reaching this line means serial output works (the runner reads it).
    report(S, "serial", true, "COM1 output reaches the host");

    report(
        S,
        "limine-base-revision",
        BASE_REVISION.is_supported(),
        if LIMINE_BASE_REVISION == 3 {
            "requested 3"
        } else {
            "unexpected revision constant"
        },
    );

    let name_ok = BOOTLOADER_INFO
        .get_response()
        .map(|i| i.name() == "Limine")
        .unwrap_or(false);
    report(S, "bootloader-info", name_ok, "bootloader name is Limine");

    report(
        S,
        "hypervisor-gate",
        cpu::hypervisor_present(),
        "CPUID hypervisor bit set",
    );
    finish();
}
