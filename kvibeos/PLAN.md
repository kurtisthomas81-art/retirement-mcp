# KVibeOS — Build Plan (bare-metal path)

Status: **approved architecture, pre-M0.** This file is the source of truth for the build agent.
Architecture, budget, or acceptance-test changes go through `FLAGS.md` and a stronger-model or human review (§10). They are never improvised.

Decision log: Path B (Arch-based distro) was planned first. The owner chose **Path A, a bare-metal Rust OS**, as the more fun project. **Gaming is benched** (§2.3).

---

## 1. Vision, re-ranked for this path

| # | Priority | Status on this path |
|---|---|---|
| 1 | Minimal resource use (idle RAM, CPU, background work) | ✅ Core strength: tens of MiB, ~99% idle |
| 2 | Fast boot, tiny disk footprint | ✅ Core strength: sub-second kernel→desktop, a few MiB on disk |
| 3 | Simple, polished, easy, Windows-style layout | ✅ for our own apps. ❌ "works with lots of apps" (see §2.2) |
| 4 | Dual-boot friendly, standard EFI | ✅ Limine on the existing ESP, chainloads Windows, fits in a 100 MB ESP |
| 5 | Bootable live/VM image | ✅ Hybrid ISO + raw/qcow2 disk image |
| — | Gaming | ⏸ Benched (no GPU acceleration, no Steam/Proton) |

## 2. What this OS will and won't be, stated plainly

### 2.1 Will be
- A UEFI (and legacy BIOS) bootable x86-64 OS, written in Rust `no_std`, booted by Limine.
- Its own kernel: paging, preemptive scheduler, ring-3 processes, syscalls, IPC, and drivers for **serial, GOP/VBE framebuffer, PS/2, PCI, xHCI USB keyboard/mouse, virtio-blk, AHCI, NVMe, ACPI power-off/reboot**.
- A Windows-style desktop: wallpaper, bottom taskbar with Start button, window buttons, and clock; a start menu with search; draggable, focusable, minimize/maximize/close/snap windows; a file explorer.
- Bundled apps: Terminal, Notepad-style editor, Calculator, File Explorer, Image Viewer, Settings, Task Manager, and a small original puzzle game.
- Fully testable in headless QEMU from M0 onward.

### 2.2 Won't be (v1), and why
- **No existing apps.** Linux, Windows, and macOS binaries won't run. Every app is written against our own API. A WASM app runtime is a parked stretch goal (§9).
- **No GPU acceleration.** All rendering is software, into the firmware-provided framebuffer. The resolution is whatever the firmware/Limine sets on real hardware. It can be changed only in QEMU, through virtio-gpu.
- **No Wi-Fi, Bluetooth, audio, webcams, printers, or laptop power management** (battery, brightness, suspend). Networking and audio are parked stretch goals that only work on QEMU and common wired NICs.
- **Laptop touchpads probably won't work.** Many are I2C-HID, which is out of scope. USB mice and keyboards will.
- **Real-hardware coverage depends on human testing.** QEMU proves the logic. Only booting your actual PCs proves the drivers (M16).

### 2.3 Benched: gaming
No Steam, Proton, Vulkan, or GPU drivers. If gaming comes back later, the realistic route is still a Linux-based track (see git history for the Path B plan). This kernel won't grow into it.

---

## 3. Target architecture and boot

- **CPU:** x86-64, single core in v1. SMP is a parked stretch goal.
- **Firmware:** UEFI primary, legacy BIOS supported because Limine supports both. **The fast test loop boots via SeaBIOS** because it starts far faster under TCG, so the `timeout 5` budget is realistic. A separate suite boots via OVMF/UEFI every run.
- **Bootloader:** **Limine**, pinned binary release, using the Limine boot protocol through the `limine` crate. Limine provides the memory map, framebuffer, RSDP, higher-half direct map, kernel command line, and modules (initrd).
- **Images:**
  - `build/kvibeos.iso`: hybrid BIOS+UEFI ISO (xorriso + `limine bios-install`). Works as a live USB via `dd`/Rufus DD mode/Ventoy.
  - `build/kvibeos.img`: GPT disk image with a 64 MiB ESP (Limine + kernel + initrd) and a 64 MiB FAT32 `KVIBEDATA` partition. Also converted to `.qcow2` and `.vmdk`.
- **Dual-boot (M14):** the installer copies `\EFI\kvibeos\` (Limine EFI binary, `limine.conf`, kernel, initrd, about 10 MiB total) onto the **existing ESP**. `limine.conf` has a "Windows" entry that chainloads `\EFI\Microsoft\Boot\bootmgfw.efi`. The installer never modifies `\EFI\Microsoft\**` or `\EFI\BOOT\BOOTX64.EFI`. The boot menu entry is created with a provided one-liner: `bcdedit` from Windows, or `efibootmgr` from Linux. Writing UEFI NVRAM from our own OS is parked (risky).
- **Toolchain:** **stable Rust**, pinned in `rust-toolchain.toml`, targeting the tier-2 `x86_64-unknown-none` target, which ships prebuilt `core`/`alloc`, so no nightly or `build-std` is needed. Interrupt stubs use `global_asm!`/naked functions, which are stable. Kernel and userspace are **soft-float** (that target's default), so v1 needs no FPU/SSE state save.

---

## 4. Architecture and module layout

### 4.1 Runtime structure
```
Limine ─▶ kernel (ring 0)
           ├─ arch/x86_64: GDT/TSS/IST, IDT, LAPIC/IOAPIC, timer, syscall entry, paging
           ├─ mm: frame allocator (bitmap), VMM (4-level), kernel heap, user address spaces
           ├─ task: processes, threads, preemptive round-robin scheduler, wait queues
           ├─ ipc: message channels + shared-memory regions (window buffers)
           ├─ fs: VFS, initrd (USTAR, read-only), FAT32 (r/w), block cache
           ├─ drivers: serial, framebuffer, PS/2, PCI, xHCI+HID, virtio-blk, AHCI, NVMe, ACPI, RTC, fw_cfg
           └─ test: in-kernel test registry, watchdog, isa-debug-exit (VM only)
        ─▶ /init (ring 3) ─▶ desktop (ring 3): compositor + window manager + taskbar + start menu
                           ─▶ apps (ring 3), each in its own address space; a crash kills only that app
```
The desktop process owns the framebuffer (mapped into it by the kernel) and composites the window buffers. Apps draw into a shared-memory buffer and send `Damage` messages. The desktop routes input events to the focused window. This is the same split as Windows' Explorer + DWM, in one process.

### 4.2 Key rule: logic lives in host-testable crates
Everything that isn't hardware access lives in `crates/*` as `#![cfg_attr(not(test), no_std)]` libraries. They run under `cargo test` on the host in seconds. The kernel and desktop are thin shells around them. That includes window-manager logic, hit-testing, snapping, damage tracking, the graphics primitives, the font cache, FAT32, USTAR, ELF parsing, the IPC protocol, and the widget toolkit. This is the agent's main fast feedback loop.

### 4.3 Repository layout
```
kvibeos/
├─ PLAN.md  CLAUDE.md  Makefile  MILESTONE  FLAGS.md  rust-toolchain.toml
├─ boot/limine.conf             # release config (timeout 3); tests use the same file + fw_cfg
├─ kernel/                      # bin crate; .cargo/config.toml sets target x86_64-unknown-none
│  ├─ linker.ld  build.rs
│  └─ src/{main.rs, arch/, mm/, task/, ipc/, fs/, drivers/, syscall/, test/, log.rs, panic.rs}
├─ crates/                      # host-testable no_std libs (cargo test on host)
│  ├─ kv-abi/      # syscall numbers, error codes, shared structs (kernel <-> user)
│  ├─ kv-proto/    # desktop <-> app IPC messages (serialize/deserialize, versioned)
│  ├─ kv-gfx/      # surfaces, fill/blit/alpha, rounded rects, damage rects, QOI decode
│  ├─ kv-font/     # fontdue wrapper + glyph cache + text layout
│  ├─ kv-wm/       # window stack, focus, hit-test, drag, snap, minimize/maximize, taskbar model
│  ├─ kv-ui/       # widgets: button, label, textbox, list, scroll, menu, dialog
│  ├─ kv-fat/      # FAT32 over a BlockDevice trait
│  ├─ kv-ustar/    # initrd reader
│  └─ kv-elf/      # ELF64 loader logic
├─ user/                        # userspace; .cargo/config.toml target + user linker script
│  ├─ libkv/       # runtime: _start, syscalls, allocator, kv::fs, kv::ipc, kv::window
│  ├─ init/  desktop/
│  └─ apps/{terminal,notepad,calc,explorer,imageview,settings,taskmgr,puzzle,crashtest}/
├─ assets/                      # OFL font (e.g. Inter), original wallpaper/icons (PNG → QOI at build)
├─ tools/
│  ├─ xtask/       # host Rust bin: png→qoi, initrd packing, image building helpers
│  ├─ qemu-run.sh  # the ONLY place QEMU command lines are built (CLAUDE.md §3)
│  ├─ test.sh      # suite runner; runs every suite with milestone ≤ MILESTONE
│  ├─ mkiso.sh  mkimg.sh  budget.sh  doctor.sh
├─ tests/
│  ├─ protocol.md  suites.txt  budgets.toml        # protected
│  ├─ e2e/         # Python 3 stdlib: QMP client, serial tailer, input scripts, screendump checks
│  └─ fixtures/    # disk recipes: blank, fat32-known, windows-like-esp, ...
└─ build/                       # gitignored outputs + logs
```

### 4.4 Dependencies (allowlist; anything else goes through a flag)
`limine`, `x86_64`, `spin`, `bitflags`, `uart_16550`, `acpi` (rust-osdev, including its AML support; verify the current crate layout when first used), `pc-keyboard`, `linked_list_allocator`, `fontdue` (no_std + alloc), `log`. Host-only (tools/tests): `png`, `qoi`, anything in `tools/xtask`. **No `std` in `kernel/`, `crates/` (non-test), or `user/`.**

---

## 5. Resource budgets (`tests/budgets.toml`, protected; enforced by `make budget` from M9)

Measured **under KVM** (`PERF=1`) on q35, 1 vCPU, `-m 256M`, 1280×800 framebuffer, at the idle desktop. Without KVM, budgets report **SKIP, never PASS**. The kernel reports its own metrics on serial as `KVIBE-METRIC <name>=<value>`.

| Metric | Measured by | Target | Hard limit |
|---|---|---|---|
| Kernel entry → desktop first frame | TSC delta, `KVIBE-METRIC boot_ms` | ≤ 250 ms | ≤ 500 ms |
| QEMU start → desktop (wall, SeaBIOS) | harness clock | ≤ 1.5 s | ≤ 3 s |
| RAM in use at idle desktop | frame-allocator used frames | ≤ 24 MiB | ≤ 40 MiB |
| Minimum RAM to reach desktop | boot test with `-m 64M` / `-m 128M` | 64 MiB | 128 MiB |
| Idle CPU (10 s, no input) | `hlt`-time accounting | ≥ 99.5 % idle | ≥ 99 % idle |
| Full-screen composite | TSC around repaint | ≤ 8 ms | ≤ 16 ms |
| Input → presented frame | TSC from input IRQ to present | ≤ 8 ms | ≤ 16 ms |
| Kernel ELF (stripped) | `stat` | ≤ 1 MiB | ≤ 2 MiB |
| initrd | `stat` | ≤ 6 MiB | ≤ 12 MiB |
| ISO | `stat` | ≤ 16 MiB | ≤ 32 MiB |
| Installed files on ESP | sum of `\EFI\kvibeos\**` | ≤ 12 MiB | ≤ 24 MiB (must fit beside Windows in a 100 MB ESP) |
| Background processes at idle | kernel process count | init + desktop (2) | 3 |

Design rules behind the budgets: a 100 Hz timer (tickless is a later optimisation), the desktop sleeps on its IPC channel when idle, the clock redraws once per minute, there's no polling anywhere, and repaints are damage-rect only.

---

## 6. Desktop/shell design (Windows-style UX on a budget)

| Feature | Implementation |
|---|---|
| Wallpaper | QOI image scaled once at startup into a cached surface |
| Taskbar (bottom, 44 px) | Start button, one button per window (click: focus or minimize), clock and date on the right |
| Start menu | Popup surface: search box (focused on open), app list from `/apps/*.toml` manifests, Power (Restart / Shut down) |
| Windows | Server-side decorations drawn by the desktop: title bar, minimize/maximize/close on the right, drag to move, double-click to maximize, drag to the left/right edge to snap half-screen, a resize grip in the bottom-right corner |
| Keyboard | Super: Start · Alt+Tab: cycle windows · Super+Left/Right: snap · Super+E: Explorer · Alt+F4: close · Ctrl+Shift+Esc: Task Manager |
| Cursor | Software cursor, composited last, damage-tracked |
| Theme | One accent colour, light/dark, an embedded OFL font at 2 sizes, original icons. **No Microsoft assets or logos.** Windows-*style* layout only |
| Apps | Built on `kv-ui` widgets, so every app inherits the same look |

**Testability is a design requirement.** In test mode the desktop emits structured events on serial: `KVIBE-UI {"ev":"window_opened","id":3,"title":"Calculator"}`, `focus`, `moved`, `minimized`, `start_open`, `frame_presented`, and so on. Apps can emit `KVIBE-UI {"ev":"app_state",...}`, for example the calculator's display value. Tests drive **real input** through QMP (`send-key`, `input-send-event` with an absolute `usb-tablet` pointer) and check outcomes through these events plus QMP `screendump`.

---

## 7. Hardware strategy

| Area | v1 support | How it's verified |
|---|---|---|
| Boot | UEFI GOP + legacy BIOS via Limine | QEMU SeaBIOS + OVMF suites; M16 human boot on real PCs |
| Display | Firmware framebuffer (any GPU), virtio-gpu mode-set in QEMU only | QEMU `-vga std` and `virtio-gpu` |
| Keyboard/mouse | PS/2, USB xHCI + HID boot protocol (+ absolute tablet) | QEMU `-device qemu-xhci,usb-kbd,usb-mouse,usb-tablet` and PS/2 |
| Storage | virtio-blk, AHCI, NVMe; GPT; FAT32 | QEMU `virtio-blk-pci`, `ich9-ahci`+`ide-hd`, `nvme` |
| Power | ACPI S5 shutdown via AML, reset via ACPI reset register / keyboard controller fallback | QEMU: poweroff exit + `KVIBE-POWEROFF` marker |
| Time | CMOS RTC read; TSC calibrated against HPET/PIT | QEMU |
| Not supported | Wi-Fi, BT, audio, GPU acceleration, I2C-HID touchpads, battery, suspend, USB storage (parked) | — |

**Real-hardware safety rules**
- Test hooks (`isa-debug-exit` writes, fw_cfg reads, the test watchdog) run **only** when CPUID reports a hypervisor (KVM or TCG signature) **and** QEMU fw_cfg's `"QEMU"` signature is present **and** `opt/kvibe/test` is set.
- The **shared ESP is treated as read-only** except for pre-allocated, fixed-size files (`\kvibe\log.txt`, `\kvibe\settings.toml`). Those are overwritten in place with no cluster allocation and no directory changes. Full FAT32 writes happen only on our own `KVIBEDATA` partition. This keeps a bug in our FAT driver from corrupting Windows' boot files.
- Logs from real-hardware boots go to the ESP log file, so you can retrieve them from Windows or Linux and share them. A panic draws an on-screen panic screen with a backtrace.

---

## 8. Test architecture

### 8.1 Exit-code protocol (`tests/protocol.md`, protected)
- In test mode the kernel reads the suite name from fw_cfg `opt/kvibe/test`, runs it, and prints `KVIBE-TEST <suite>/<case> PASS|FAIL|SKIP <detail>`, then `KVIBE-RESULT PASS|FAIL`.
- It exits through **isa-debug-exit** at port `0xf4`: write `0x10` for pass and `0x11` for fail. QEMU's exit status is `(v<<1)|1`.

| QEMU exit | Meaning | Verdict |
|---|---|---|
| 33 | guest PASS (also requires a `KVIBE-RESULT PASS` line) | PASS |
| 35 | guest FAIL (assertion, panic in test mode, or watchdog) | FAIL |
| 124 | `timeout` fired: hang with interrupts off, or before the watchdog was armed | FAIL (TIMEOUT); read the `-d int` log tail |
| 0 | guest reset or power-off. With `-no-reboot` a triple fault exits 0 | FAIL (UNEXPECTED_RESET), unless the suite expects power-off **and** `KVIBE-POWEROFF` was printed **and** the `-d cpu_reset` log shows no reset |
| other | QEMU/infra error | ERROR |

- **Test-mode watchdog:** if a suite overruns its tick budget, the timer interrupt prints the interrupted RIP and a frame-pointer backtrace (kernel built with `force-frame-pointers`), then exits `0x11`. The harness symbolizes addresses with `addr2line -e build/kernel.elf`.
- Runner exit codes: `0` pass, `1` fail, `2` infra error, `77` skip.

### 8.2 Tiers
1. **Host unit** (`make test-unit`): `cargo test` over `crates/*` and `tools/xtask`. Seconds.
2. **Kernel suites** (`make test-kernel`): boot ISO via SeaBIOS under TCG using the exact command in CLAUDE.md §3.1 with **`timeout 5`**. One QEMU run per suite.
3. **UEFI boot** (`make test-uefi`): the same smoke suite via OVMF, ISO and disk image. `timeout 20`, because OVMF under TCG is slow.
4. **E2E GUI** (`make test-e2e`): Python driver with QMP input, serial `KVIBE-UI` events, and screendump checks (coarse region colours, no pixel-perfect goldens). `timeout 30` per scenario.
5. **Storage/installer** (`make test-storage`, `make test-install`): fixture disks built with mtools/sgdisk on the host and verified with `mtools`, `fsck.fat -n`, and SHA-256 after QEMU exits.
6. **Budget** (`make budget`, `PERF=1`, KVM): §5.

Rules: wait for markers with a deadline, never a fixed sleep. A suite that flakes twice in 20 runs is a bug, not a retry candidate.

---

## 9. Milestones

Every milestone: its acceptance command exits 0, **and `make test` exits 0** (all suites with milestone ≤ `MILESTONE`), and from M9 on, `make budget` is green when KVM is available. Then commit, tag `mN`, and bump `MILESTONE`.

### M0 — Toolchain, Limine, harness self-test
- **Goal:** `make doctor` checks host tools. `make iso` builds a Limine ISO with a kernel that prints `KVIBE-BOOT OK` on COM1. The harness verdicts are proven correct.
- **Files:** `Makefile`, `rust-toolchain.toml`, `kernel/{Cargo.toml,.cargo/config.toml,linker.ld,build.rs,src/main.rs,src/test/exit.rs,src/drivers/{serial,fwcfg}.rs}`, `boot/limine.conf`, `tools/{doctor,qemu-run,test,mkiso}.sh`, `tests/{protocol.md,suites.txt}`, `.gitignore`.
- **Acceptance (`make test-harness`):** four fw_cfg-selected kernel behaviours produce the expected verdicts: `harness-pass`→33, `harness-fail`→35, `harness-hang` (`cli; hlt` loop)→124, `harness-triplefault` (load a null IDT, then `int3`)→0 with a `cpu_reset` line in the `-d` log. A fifth run with **no** fw_cfg key must print `KVIBE-BOOT OK` and **not** write to `0xf4` (timeout 124 is the expected result, and the serial log must contain no `KVIBE-TEST` lines).
- **Out of scope:** interrupts, memory management, graphics.

### M1 — CPU foundation and diagnostics
- **Goal:** GDT+TSS with IST stacks, IDT with all exception handlers, a panic handler (serial + framebuffer text with backtrace), framebuffer text console with an embedded bitmap font, and a parsed memory map log.
- **Files:** `kernel/src/arch/x86_64/{gdt,idt,exceptions}.rs`, `kernel/src/{panic,log}.rs`, `kernel/src/drivers/fbcon.rs`, `kernel/src/test/{mod,boot}.rs`.
- **Acceptance (`make test-kernel SUITE=boot`):** `int3` returns; a deliberate page fault in test mode reports the correct CR2, then recovers by skipping the instruction; deliberate kernel stack overflow hits the double-fault handler on its IST stack (no triple fault); a framebuffer checksum after printing a known string matches across 3 boots; a host check confirms the QMP screendump is non-blank.
- **Out of scope:** heap, timers.
- 🔶 **Stronger-model review after M1:** memory map / higher-half layout before M2.

### M2 — Memory management
- **Goal:** bitmap frame allocator, 4-level page-table manager, kernel heap with `alloc`, guard pages for kernel stacks.
- **Files:** `kernel/src/mm/{frame,paging,heap,mod}.rs`, `kernel/src/test/mm.rs`.
- **Acceptance (`SUITE=mm`):** 10k mixed Box/Vec allocations with a free count back at baseline (no leak); map/unmap round-trip; writing past a stack guard page faults as expected; the `boot` suite passes with `-m 64M` and `-m 4G`.
- **Out of scope:** user address spaces.

### M3 — Interrupts and time
- **Goal:** ACPI MADT parsing, LAPIC + IOAPIC (PIC masked), TSC calibrated via HPET or PIT, 100 Hz APIC timer, `hlt` idle loop with idle accounting, CMOS RTC, test watchdog.
- **Files:** `kernel/src/arch/x86_64/{apic,ioapic,timer,tsc}.rs`, `kernel/src/drivers/{acpi,rtc}.rs`, `kernel/src/test/{time,watchdog}.rs`.
- **Acceptance (`SUITE=time`):** ticks increase monotonically. Over 300 ticks, TSC-measured elapsed time is within ±25% of expected (TCG timing is coarse). Idle accounting reports ≥ 95% under TCG with no load. The watchdog test hangs with interrupts on, expects exit 35, and expects a backtrace line containing the symbol `watchdog_hang_target` after `addr2line`.
- **Out of scope:** scheduler.

### M4 — Kernel threads and scheduler
- **Goal:** kernel threads, preemptive round-robin, sleep, wait queues, an interrupt-safe `IrqMutex`.
- **Files:** `kernel/src/task/{thread,sched,wait,mod}.rs`, `kernel/src/sync.rs`, `kernel/src/test/sched.rs`.
- **Acceptance (`SUITE=sched`):** 4 threads each increment their own counter for 200 ticks and all make progress. Producer/consumer through a wait queue completes 10k items. A sleep of 50 ticks wakes between 50 and 60. 2-second lock stress with a timer-interrupt-heavy workload completes without a watchdog trip.
- **Out of scope:** user mode, SMP.
- 🔶 **Stronger-model review:** locking and interrupt-safety design.

### M5 — Userspace
- **Goal:** ring 3, `syscall`/`sysret`, per-process address spaces, ELF loader, initrd (USTAR) as a Limine module, `libkv` runtime, `/init`, process exit/wait, and user-pointer validation.
- **Files:** `kernel/src/{syscall/,task/process.rs,fs/initrd.rs}`, `crates/{kv-abi,kv-elf,kv-ustar}/`, `user/{libkv,init}/`, `user/apps/crashtest/`, `tools/xtask` (initrd packing).
- **Acceptance:** `cargo test -p kv-elf -p kv-ustar` passes. `SUITE=user`: `/init` prints through a syscall and spawns `hello`, which exits 7, and the kernel reports exit code 7. `crashtest null` dies with a page fault while the kernel and init keep running. A syscall with a kernel-space pointer returns `EFAULT`. 100 spawn/exit cycles return the free-frame count to baseline.
- **Out of scope:** files beyond the initrd, IPC.
- 🔶 **Stronger-model review:** syscall ABI and user-memory validation (the security boundary).

### M6 — IPC, shared memory, VFS
- **Goal:** message channels (send/recv/blocking), shared-memory regions, handles, VFS over the initrd, file syscalls.
- **Files:** `kernel/src/{ipc/,fs/vfs.rs}`, `crates/kv-abi/`, `user/libkv/src/{ipc,fs}.rs`, `user/apps/ipctest/`.
- **Acceptance (`SUITE=ipc`):** a 10k-message ping-pong between two processes completes. A shared region written by A is read by B with the expected checksum. A dead peer produces `EPIPE`, not a hang. `read("/apps/calc.toml")` matches the initrd content.
- **Out of scope:** writable filesystems.

### M7 — Input
- **Goal:** PS/2 keyboard and mouse, PCI enumeration, xHCI controller + HID boot keyboard/mouse + `usb-tablet`, an input event queue exposed to userspace.
- **Files:** `kernel/src/drivers/{ps2,pci,xhci/,hid}.rs`, `user/libkv/src/input.rs`, `tests/e2e/{qmp.py,serial.py,input_basic.py}`.
- **Acceptance (`make test-e2e SCENARIO=input_basic`):** under each of the configs `ps2` and `xhci`, QMP `send-key a` produces `KVIBE-INPUT key=a down/up`, and a tablet absolute move to (640,400) produces a pointer event at (640,400) ±1. Hot-plugging the USB keyboard through QMP `device_add` is detected.
- **Out of scope:** USB storage, hubs beyond QEMU's root hub, I2C-HID.
- 🔶 **Stronger-model review:** the xHCI driver (complex, and the most likely real-hardware failure).

### M8 — Graphics and font libraries
- **Goal:** `kv-gfx` (surfaces, fill, blit, alpha, rounded rects, damage-rect union, QOI decode) and `kv-font` (fontdue + glyph cache + layout). Asset pipeline PNG → QOI. Kernel maps the framebuffer into a privileged process.
- **Files:** `crates/{kv-gfx,kv-font}/`, `tools/xtask/src/assets.rs`, `assets/`, `kernel/src/syscall/fb.rs`.
- **Acceptance:** `cargo test -p kv-gfx -p kv-font` passes, including golden **hashes** of deterministic renders (integer math, so exact). `SUITE=fb`: a user process maps the framebuffer and draws a test pattern, and a host QMP screendump region check matches.
- **Out of scope:** windows.

### M9 — Desktop: compositor, window manager, taskbar
- **Goal:** the `desktop` process: wallpaper, cursor, windows (open, focus, drag, z-order, close, minimize, maximize, snap), taskbar with window buttons and clock, `kv-proto` protocol, a `KVIBE-UI` event stream, `KVIBE-METRIC boot_ms`.
- **Files:** `crates/{kv-wm,kv-proto}/`, `user/desktop/`, `user/libkv/src/window.rs`, `user/apps/hello-window/`, `tests/e2e/{desktop_basic,window_ops}.py`, `tools/budget.sh`, `tests/budgets.toml`.
- **Acceptance:** `cargo test -p kv-wm` passes (hit-testing, focus order, snap geometry, minimize/restore). E2E: 2 windows open and their taskbar buttons appear; dragging a title bar 200 px produces `moved` with the correct delta; a taskbar click minimizes and then restores; dragging to the left edge snaps to half-screen; the screendump's bottom 44 px matches the taskbar colour. **`make budget` is now enforced.**
- **Out of scope:** start menu, real apps.

### M10 — Start menu, `kv-ui`, first apps
- **Goal:** start menu (search, app list, power), the `kv-ui` widget toolkit, and the Terminal (built-in commands: `ls cat echo ps mem clear help`), Notepad, and Calculator apps.
- **Files:** `crates/kv-ui/`, `user/desktop/src/start/`, `user/apps/{terminal,notepad,calc}/`, `tests/e2e/{start_menu,calc,crash_isolation}.py`.
- **Acceptance:** Super produces `start_open`. Typing "calc" + Enter produces `window_opened title=Calculator`. Clicking `2 + 3 =` produces `app_state display=5`. Launching `crashtest` from the menu kills only that process, its window disappears, and the desktop keeps responding (a subsequent click produces an event). Budgets green.
- **Out of scope:** file dialogs, persistent settings.

### M11 — Storage
- **Goal:** virtio-blk, AHCI, NVMe drivers; GPT; block cache; FAT32 read/write (`kv-fat`); the shared-ESP write restriction from §7.
- **Files:** `kernel/src/drivers/{virtio_blk,ahci,nvme}.rs`, `kernel/src/fs/{gpt,fat,cache}.rs`, `crates/kv-fat/`, `tests/fixtures/fat32-known.sh`, `kernel/src/test/storage.rs`.
- **Acceptance:** `cargo test -p kv-fat` passes against images made with `mkfs.fat`. `make test-storage` runs once per controller type (virtio, ahci, nvme): read known files and match hashes; write a 1 MiB file plus nested directories; after QEMU exits, the host verifies with `mcopy` + `sha256sum` and `fsck.fat -n` reports clean. A test that the ESP-mode mount rejects cluster allocation (`EROFS`) passes.
- **Out of scope:** other filesystems, USB storage, journaling.
- 🔶 **Stronger-model review before and after:** FAT write path (data corruption risk on real disks).

### M12 — File Explorer, dialogs, Image Viewer, settings persistence
- **Goal:** Explorer (navigate, open, rename, delete, new folder, on `KVIBEDATA` only), common open/save dialogs, Image Viewer (QOI/BMP), `settings.toml` persistence.
- **Files:** `user/apps/{explorer,imageview}/`, `crates/kv-ui/src/dialogs/`, `user/libkv/src/settings.rs`, `tests/e2e/{explorer,save_file}.py`.
- **Acceptance:** E2E: in Explorer, open `docs/readme.txt` from a fixture; it opens in Notepad; edit and save it; the host verifies the new content after exit. Accent colour set in settings survives a reboot (two-boot test on the same disk).
- **Out of scope:** drag-and-drop between windows, a clipboard (unless trivial).

### M13 — Settings, Task Manager, power
- **Goal:** Settings (accent, light/dark, clock format, display info, virtio-gpu resolution in QEMU), Task Manager (processes, memory, CPU %, end task), ACPI shutdown/restart from the start menu.
- **Files:** `user/apps/{settings,taskmgr}/`, `kernel/src/drivers/{acpi_power,virtio_gpu}.rs`, `tests/e2e/{power,taskmgr,resolution}.py`.
- **Acceptance:** Start → Shut down makes QEMU exit 0 with `KVIBE-POWEROFF` and no reset in the `cpu_reset` log. Restart produces `KVIBE-REBOOT` and a reset. The Task Manager process list matches the kernel's `KVIBE-METRIC procs`, and End Task on `hello-window` closes it. A virtio-gpu config changes resolution to 1920×1080 and the screendump size matches.
- **Out of scope:** sleep/hibernate, battery.

### M14 — Installer and dual-boot
- **Goal:** an `installer` app on the live ISO with two modes. *Whole disk*: GPT, ESP, and `KVIBEDATA`. *Alongside*: copy to the existing ESP and append nothing outside `\EFI\kvibeos\`. Plus the `bcdedit`/`efibootmgr` helper text and scripts on the ISO.
- **Files:** `user/apps/installer/`, `boot/limine-installed.conf`, `tools/mkimg.sh`, `tests/fixtures/{blank,windows-like-esp}.sh`, `tests/e2e/{install_whole,install_alongside}.py`, `iso-extras/{add-bootentry-windows.cmd,add-bootentry-linux.sh}`.
- **Fixture:** a GPT disk with a 100 MB ESP holding `EFI/Microsoft/Boot/bootmgfw.efi`, a tiny EFI stub that prints `WINDOWS-STUB` and exits `0x12` through isa-debug-exit, plus an `EFI/BOOT/BOOTX64.EFI` copy and a dummy NTFS partition with known content.
- **Acceptance:** after the alongside install, SHA-256 of `EFI/Microsoft/**`, `EFI/BOOT/**`, and the NTFS partition is unchanged; the ESP total fits within the §5 budget; `fsck.fat -n` is clean. The host adds a boot entry with `virt-fw-vars` (simulating `bcdedit`). Booting it reaches the desktop, and choosing the "Windows" Limine entry reaches `WINDOWS-STUB` (exit 37). The whole-disk install boots under both OVMF and SeaBIOS.
- **Out of scope:** resizing partitions, writing NVRAM from our OS, Secure Boot.
- 🔶 **Stronger-model review before and after.**

### M15 — Polish and performance pass
- **Goal:** meet every §5 *target* (not just the hard limits) where feasible; add a puzzle game; refine the theme and icons; tidy startup.
- **Files:** as needed in `user/` and `crates/`. Kernel changes only for performance, each with a before/after `make budget`.
- **Acceptance:** `make budget` meets all hard limits, and the report lists target misses with reasons. Full `make test` passes 20 times in a row (flake check).

### M16 — Real-hardware bring-up (human-in-the-loop)
- **Goal:** boot from USB on at least 3 PCs (one with Windows dual-boot). Collect `\kvibe\log.txt` from each.
- **Files:** `docs/hw-checklist.md`, `docs/hw-results/*.md`.
- **Acceptance:** results recorded for boot, display, keyboard, mouse, NVMe/AHCI read, shutdown, and Windows still booting. Each failure becomes a FLAGS.md entry. The agent fixes only what it can reproduce in QEMU, or what it can fix with a clear log-driven change confirmed by a human re-test.

### Parked (after v1; each needs a stronger-model design review first)
SMP · networking (virtio-net/e1000 + smoltcp) · HDA audio · USB mass storage · WASM app runtime (`wasmi`) for portable third-party apps · Intel/AMD native modesetting · UEFI NVRAM writes from our OS · Secure Boot signing · SSE/FPU in userspace · gaming (see §2.3).

---

## 10. Risks and escalation

| Risk | Mitigation | Escalate when |
|---|---|---|
| Subtle kernel memory/concurrency bugs no test catches | Logic in host-tested crates; `unsafe` confined to `arch/`, `mm/`, and `drivers/`, each block with a `// SAFETY:` comment; frame-count leak checks in every suite; 20× flake runs at M15 | A crash can't be reproduced deterministically |
| Agent weakens tests | Protected files; FLAGS.md process | Any protected-file change |
| Real-hardware driver failures invisible to QEMU | Firmware framebuffer (no GPU driver); log to ESP; M16 human pass | Each M16 failure |
| Data loss on a shared ESP or disks | ESP write restriction (§7); installer only writes `\EFI\kvibeos\`; fixture hash checks | M11, M14; any change to `kv-fat` write paths |
| TCG timing flakiness | Coarse tolerances, marker waits, perf only under KVM | A suite flakes twice in 20 runs |
| Agent loops on a failure | 3 distinct attempts, then flag | Always |
| Scope creep (networking, a browser, ...) | Parked list | Any request to unpark |

Stronger-model checkpoints: **after M1, M4, M5, M7, before and after M11, before and after M14, any FLAGS.md entry, any budget breach, any unparking.**
