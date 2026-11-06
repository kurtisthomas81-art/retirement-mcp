//! Harness self-test behaviours (M0). Each produces a distinct QEMU outcome
//! so tools/test.sh can prove its verdict classification is correct.
use super::{finish, report};
use crate::arch::cpu;

pub fn run(suite: &str) -> ! {
    match suite {
        "harness-pass" => {
            report(suite, "exit", true, "deliberate pass");
            finish();
        }
        "harness-fail" => {
            report(suite, "exit", false, "deliberate failure");
            finish();
        }
        "harness-panic" => panic!("deliberate panic (harness-panic)"),
        "harness-hang" => cpu::hang_interrupts_off(),
        "harness-triplefault" => cpu::triple_fault(),
        other => {
            report(other, "lookup", false, "unknown harness suite");
            finish();
        }
    }
}
