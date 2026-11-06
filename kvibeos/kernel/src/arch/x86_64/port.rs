//! x86 port I/O.
use core::arch::asm;

/// Write a byte to an I/O port.
///
/// # Safety
/// Port writes can reconfigure hardware; the caller must own the device behind `port`.
pub unsafe fn outb(port: u16, value: u8) {
    // SAFETY: caller guarantees the port write is valid for the device.
    unsafe {
        asm!("out dx, al", in("dx") port, in("al") value, options(nomem, nostack, preserves_flags))
    };
}

/// Read a byte from an I/O port.
///
/// # Safety
/// Port reads can have side effects; the caller must own the device behind `port`.
pub unsafe fn inb(port: u16) -> u8 {
    let value: u8;
    // SAFETY: caller guarantees the port read is valid for the device.
    unsafe {
        asm!("in al, dx", out("al") value, in("dx") port, options(nomem, nostack, preserves_flags))
    };
    value
}

/// Write a 16-bit word to an I/O port.
///
/// # Safety
/// See [`outb`].
pub unsafe fn outw(port: u16, value: u16) {
    // SAFETY: caller guarantees the port write is valid for the device.
    unsafe {
        asm!("out dx, ax", in("dx") port, in("ax") value, options(nomem, nostack, preserves_flags))
    };
}

/// Write a 32-bit dword to an I/O port.
///
/// # Safety
/// See [`outb`].
pub unsafe fn outl(port: u16, value: u32) {
    // SAFETY: caller guarantees the port write is valid for the device.
    unsafe {
        asm!("out dx, eax", in("dx") port, in("eax") value, options(nomem, nostack, preserves_flags))
    };
}
