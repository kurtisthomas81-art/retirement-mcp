//! Test-mode exit codes. QEMU exit status = (value << 1) | 1.
use crate::{arch::cpu, drivers::qemu_exit};

pub const PASS: u32 = 0x10; // -> exit 33
pub const FAIL: u32 = 0x11; // -> exit 35

fn exit(code: u32) -> ! {
    // Refuse to touch the port outside gated test mode (could be real hardware).
    if super::active() {
        qemu_exit::write(code);
    }
    cpu::halt_forever();
}

pub fn pass() -> ! {
    exit(PASS)
}

pub fn fail() -> ! {
    exit(FAIL)
}
