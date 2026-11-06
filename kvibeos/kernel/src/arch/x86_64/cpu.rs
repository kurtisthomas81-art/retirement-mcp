//! CPU control helpers.
use core::arch::asm;
use core::arch::x86_64::__cpuid;

/// Disable interrupts and halt forever.
pub fn halt_forever() -> ! {
    loop {
        // SAFETY: cli/hlt only stop this CPU; no memory is touched.
        unsafe { asm!("cli", "hlt", options(nomem, nostack)) };
    }
}

/// True if CPUID reports we run under a hypervisor (leaf 1, ECX bit 31).
/// QEMU TCG and KVM both set it. Real hardware does not.
pub fn hypervisor_present() -> bool {
    #[allow(unused_unsafe)]
    // SAFETY: CPUID is always available on x86-64 and has no side effects.
    let leaf1 = unsafe { __cpuid(1) };
    leaf1.ecx & (1 << 31) != 0
}

/// Deliberately triple-fault the CPU (test harness only):
/// load an empty IDT, then raise an exception that cannot be delivered.
pub fn triple_fault() -> ! {
    #[repr(C, packed)]
    struct Idtr {
        limit: u16,
        base: u64,
    }
    let idtr = Idtr { limit: 0, base: 0 };
    // SAFETY: intentionally fatal; used only by the gated test harness.
    unsafe {
        asm!("lidt [{}]", "int3", in(reg) &idtr, options(nostack));
    }
    halt_forever();
}

/// Hang with interrupts disabled (test harness only).
pub fn hang_interrupts_off() -> ! {
    halt_forever();
}
