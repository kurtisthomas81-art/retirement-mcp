//! QEMU fw_cfg (legacy I/O interface). Used only to read test parameters.
//! Callers must check `crate::arch::cpu::hypervisor_present()` first so that
//! these ports are never touched on real hardware.
use crate::arch::port::{inb, outw};

const SELECTOR: u16 = 0x510;
const DATA: u16 = 0x511;
const SEL_SIGNATURE: u16 = 0x0000;
const SEL_FILE_DIR: u16 = 0x0019;

fn select(key: u16) {
    // SAFETY: fw_cfg selector port; only called after the hypervisor check.
    unsafe { outw(SELECTOR, key) };
}

fn read_u8() -> u8 {
    // SAFETY: fw_cfg data port; only called after the hypervisor check.
    unsafe { inb(DATA) }
}

fn read_be_u32() -> u32 {
    let mut v = 0u32;
    for _ in 0..4 {
        v = (v << 8) | u32::from(read_u8());
    }
    v
}

fn read_be_u16() -> u16 {
    (u16::from(read_u8()) << 8) | u16::from(read_u8())
}

/// True if the fw_cfg signature reads "QEMU".
pub fn present() -> bool {
    select(SEL_SIGNATURE);
    let sig = [read_u8(), read_u8(), read_u8(), read_u8()];
    &sig == b"QEMU"
}

/// Read fw_cfg file `name` into `buf`. Returns the number of bytes copied.
pub fn read_file(name: &str, buf: &mut [u8]) -> Option<usize> {
    select(SEL_FILE_DIR);
    let count = read_be_u32();
    for _ in 0..count {
        let size = read_be_u32() as usize;
        let sel = read_be_u16();
        let _reserved = read_be_u16();
        let mut fname = [0u8; 56];
        for b in fname.iter_mut() {
            *b = read_u8();
        }
        let len = fname.iter().position(|&b| b == 0).unwrap_or(fname.len());
        if &fname[..len] == name.as_bytes() {
            select(sel);
            let n = size.min(buf.len());
            for b in buf[..n].iter_mut() {
                *b = read_u8();
            }
            return Some(n);
        }
    }
    None
}
