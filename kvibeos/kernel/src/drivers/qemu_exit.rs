//! QEMU isa-debug-exit device (iobase 0xf4). QEMU exit status = (value << 1) | 1.
use crate::arch::port::outl;

const ISA_DEBUG_EXIT: u16 = 0xf4;

/// Write `code` to the isa-debug-exit port. Callers must be in gated test mode.
pub fn write(code: u32) {
    // SAFETY: only reached from test::exit, which requires gated test mode
    // (hypervisor + fw_cfg + opt/kvibe/test), so the device is QEMU's.
    unsafe { outl(ISA_DEBUG_EXIT, code) };
}
