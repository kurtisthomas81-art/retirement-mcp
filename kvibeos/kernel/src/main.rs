//! KVibeOS kernel entry point.
#![no_std]
#![no_main]
#![deny(unsafe_op_in_unsafe_fn)]

mod arch;
mod drivers;
mod log;
mod test;

use core::panic::PanicInfo;
use limine::BaseRevision;
use limine::request::{BootloaderInfoRequest, RequestsEndMarker, RequestsStartMarker};

/// Limine base revision this kernel is written against.
pub const LIMINE_BASE_REVISION: u64 = 3;

#[used]
#[unsafe(link_section = ".limine_requests_start")]
static REQUESTS_START: RequestsStartMarker = RequestsStartMarker::new();

#[used]
#[unsafe(link_section = ".limine_requests")]
pub static BASE_REVISION: BaseRevision = BaseRevision::with_revision(LIMINE_BASE_REVISION);

#[used]
#[unsafe(link_section = ".limine_requests")]
pub static BOOTLOADER_INFO: BootloaderInfoRequest = BootloaderInfoRequest::new();

#[used]
#[unsafe(link_section = ".limine_requests_end")]
static REQUESTS_END: RequestsEndMarker = RequestsEndMarker::new();

#[unsafe(no_mangle)]
extern "C" fn kmain() -> ! {
    drivers::serial::init();
    kprintln!("KVIBE-BOOT start");

    if !BASE_REVISION.is_supported() {
        panic!(
            "Limine base revision {} not supported (bootloader reports {:?})",
            LIMINE_BASE_REVISION,
            BASE_REVISION.loaded_revision()
        );
    }
    if let Some(info) = BOOTLOADER_INFO.get_response() {
        kprintln!("KVIBE-INFO bootloader={} {}", info.name(), info.version());
    }
    kprintln!("KVIBE-BOOT OK");

    test::run_if_requested();

    arch::cpu::halt_forever();
}

#[panic_handler]
fn panic(info: &PanicInfo) -> ! {
    kprintln!("KVIBE-PANIC {}", info);
    if test::active() {
        test::exit::fail();
    }
    arch::cpu::halt_forever();
}
