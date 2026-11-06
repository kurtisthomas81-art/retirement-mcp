//! 16550 UART on COM1, polled. Safe on machines without a serial port:
//! presence is probed with loopback, and writes are bounded.
use crate::arch::port::{inb, outb};
use core::sync::atomic::{AtomicBool, Ordering};

const COM1: u16 = 0x3F8;
const LSR_THR_EMPTY: u8 = 1 << 5;
const MAX_SPIN: u32 = 100_000;

static PRESENT: AtomicBool = AtomicBool::new(false);

/// Initialise COM1 at 115200 8N1. Detects absence via loopback test.
pub fn init() {
    // SAFETY: COM1 standard ports; the kernel is the only user of the UART.
    unsafe {
        outb(COM1 + 1, 0x00); // disable UART interrupts
        outb(COM1 + 3, 0x80); // DLAB on
        outb(COM1, 0x01); // divisor low  (115200 baud)
        outb(COM1 + 1, 0x00); // divisor high
        outb(COM1 + 3, 0x03); // 8N1, DLAB off
        outb(COM1 + 2, 0xC7); // FIFO on, clear, 14-byte threshold
        outb(COM1 + 4, 0x1E); // loopback mode for presence test
        outb(COM1, 0xAE);
        if inb(COM1) != 0xAE {
            return; // no UART: stay silent
        }
        outb(COM1 + 4, 0x0F); // normal mode, OUT1/OUT2, RTS/DTR
    }
    PRESENT.store(true, Ordering::Relaxed);
}

fn write_byte(b: u8) {
    // SAFETY: COM1 was initialised and found present.
    unsafe {
        let mut spins = 0;
        while inb(COM1 + 5) & LSR_THR_EMPTY == 0 {
            spins += 1;
            if spins > MAX_SPIN {
                return;
            }
        }
        outb(COM1, b);
    }
}

/// Zero-sized writer used by `kprint!`.
pub struct SerialWriter;

impl core::fmt::Write for SerialWriter {
    fn write_str(&mut self, s: &str) -> core::fmt::Result {
        if !PRESENT.load(Ordering::Relaxed) {
            return Ok(());
        }
        for b in s.bytes() {
            if b == b'\n' {
                write_byte(b'\r');
            }
            write_byte(b);
        }
        Ok(())
    }
}
