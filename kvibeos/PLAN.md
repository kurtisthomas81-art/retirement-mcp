# KVibeOS — Build Plan (bare-metal path)

Status: **approved architecture, revision 2 (adds §11 cross-cutting requirements). M0 implemented by the reviewer; the build agent starts at M1.** This file is the source of truth for the build agent.
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
- Its own kernel: paging, preemptive scheduler, ring-3 processes, syscalls, IPC, and drivers for **serial, GOP/VBE framebuffer, PS/2, PCI, xHCI USB keyboard/mouse/mass storage, virtio-blk, AHCI, NVMe, ACPI power-off/reboot/power button**.
- A Windows-style desktop: wallpaper, bottom taskbar with Start button, window buttons, and clock; a start menu with search; draggable, focusable, minimize/maximize/close/snap windows; a file explorer.
- Bundled apps: Terminal, Notepad-style editor, Calculator, File Explorer, Image Viewer, Settings, Task Manager, and a small original puzzle game.
- Fully testable in headless QEMU from M0 onward.

### 2.2 Won't be (v1), and why
- **No existing apps.** Linux, Windows, and macOS binaries won't run. Every app is written against our own API. A WASM app runtime is a parked stretch goal (§9).
- **No GPU acceleration.** All rendering is software, into the firmware-provided framebuffer. The resolution is whatever the firmware/Limine sets on real hardware, often the panel's native 4K. The UI scales 2× automatically at ≥ 3000 px wide (override in Settings). The resolution can be changed only in QEMU, through virtio-gpu. One monitor only.
- **Keyboard layout: US QWERTY only** (owner decision).
- **Secure Boot must be off**, or the user enrolls their own keys. Limine is unsigned. Windows 11 PCs ship with Secure Boot on (see `docs/dual-boot.md`, M14).
- **Hyper-V Generation 2 VMs are unsupported** (only VMBus devices). QEMU, VirtualBox and VMware are the supported VM hosts.
- **No Wi-Fi, Bluetooth, audio, webcams, printers, or laptop power management** (battery, brightness, suspend). Networking and audio are parked stretch goals that only work on QEMU and common wired NICs.
- **Laptop touchpads probably won't work.** Many are I2C-HID, which is out of scope. USB mice and keyboards will.
- **Real-hardware coverage depends on human testing.** QEMU proves the logic. Only booting your actual PCs proves the drivers (M17).

### 2.3 Benched: gaming
No Steam, Proton, Vulkan, or GPU drivers. If gaming comes back later, the realistic route is still a Linux-based track (see git history for the Path B plan). This kernel won't grow into it.

---

## 3. Target architecture and boot

- **CPU:** x86-64, single core in v1. SMP is a parked stretch goal.
- **Firmware:** UEFI primary, legacy BIOS supported because Limine supports both. **The fast test loop boots via SeaBIOS** because it starts far faster under TCG, so the `timeout 5` budget is realistic. A separate suite boots via OVMF/UEFI every run.
- **Bootloader:** **Limine v11.4.1**, pinned by commit (`LIMINE_COMMIT` in the Makefile; v11 is the newest line with a prebuilt `-binary` branch). The Limine boot protocol is used through the **`limine` crate pinned at `=0.5.0`**, base revision 3. (0.6.x requires nightly Rust, so it is not allowed.) Two ISOs share one kernel: `kvibeos.iso` (release; 2 s boot menu with *KVibeOS*, *safe mode* (`kvibe.safe`) and *verbose boot log* (`kvibe.verbose`)) and `kvibeos-test.iso` (`timeout: 0`, used by the 5-second test loop). The release ISO is booted by the `release` test tier every run. Each ISO carries `boot/kernel.sym`, a symbol map loaded as a Limine module so panics print function names on real hardware. Limine provides the memory map, framebuffer, RSDP, higher-half direct map, kernel command line, and modules (initrd).
- **Images:**
  - `build/kvibeos.iso`: hybrid BIOS+UEFI ISO (xorriso + `limine bios-install`). Works as a live USB via `dd`/Rufus DD mode/Ventoy.
  - `build/kvibeos.img`: GPT disk image with a 64 MiB ESP (Limine + kernel + initrd) and a 64 MiB FAT32 `KVIBEDATA` partition. Also converted to `.qcow2` and `.vmdk`.
- **Dual-boot (M14):** the installer copies `\EFI\kvibeos\` (Limine EFI binary, `limine.conf`, kernel, initrd, about 10 MiB total) onto the **existing ESP**. `limine.conf` has a "Windows" entry that chainloads `\EFI\Microsoft\Boot\bootmgfw.efi`. The installer never modifies `\EFI\Microsoft\**` or `\EFI\BOOT\BOOTX64.EFI`. The boot menu entry is created with a provided one-liner: `bcdedit` from Windows, or `efibootmgr` from Linux. Writing UEFI NVRAM from our own OS is parked (risky). Before installing alongside Windows, the installer warns the user to have their **BitLocker recovery key** (BitLocker volumes are detected by their `-FVE-FS-` signature), because boot changes can trigger a recovery prompt. Secure Boot and Windows Fast Startup are covered in `docs/dual-boot.md`.
- **Updates (M14):** `installer --update` installs the new kernel/initrd next to the current one and keeps the old pair as a **"KVibeOS (previous version)"** boot entry. A bad build is always one menu choice away from a working one.
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
├─ PLAN.md  CLAUDE.md  Makefile  MILESTONE  FLAGS.md  rust-toolchain.toml  THIRD_PARTY_NOTICES.md
├─ .github/workflows/gate.yml   # CI: make verify on every push, with KVM (protected)
├─ boot/limine.conf             # release ISO menu: normal / safe mode / verbose (timeout 2)
├─ boot/limine-test.conf        # test ISO (timeout 0); test-only cmdline via fw_cfg opt/kvibe/cmdline
├─ docs/progress/m<N>.md        # milestone journal: Summary / Decisions / Known issues / Next (gate-required)
├─ kernel/                      # bin crate; .cargo/config.toml sets target x86_64-unknown-none
│  ├─ linker.ld  build.rs
│  └─ src/{main.rs, arch/, mm/, task/, ipc/, fs/, drivers/, syscall/, test/, log.rs, panic.rs}
├─ crates/                      # host-testable no_std libs (cargo test on host)
│  ├─ kv-bootcfg/  # kernel command-line parsing (kvibe.safe, kvibe.verbose, ...)
│  ├─ kv-symbols/  # symbol-map parsing + address lookup for in-kernel backtraces
│  ├─ kv-input/    # scancode/HID usage → key events, US QWERTY keymap, key repeat
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
│  ├─ qemu-run.sh  # the ONLY place QEMU command lines are built (CLAUDE.md §3)       (protected)
│  ├─ test.sh      # suite runner; writes build/logs/results.txt                          (protected)
│  ├─ gate.sh  lib/{classify,quality}.sh  screenshot.py                               (protected)
│  ├─ mkiso.sh  mkimg.sh  budget.sh  doctor.sh
├─ tests/
│  ├─ protocol.md  suites.txt  budgets.toml  gates/m<N>.txt   # protected
│  ├─ unit/        # host check scripts (may emit KVIBE-TEST host/<check> lines)
│  ├─ e2e/         # Python 3 stdlib: QMP client, serial tailer, input scripts, screendump checks
│  └─ fixtures/    # disk recipes: blank, fat32-known, windows-like-esp, ...
└─ build/                       # gitignored outputs + logs
```

### 4.4 Dependencies (allowlist; anything else goes through a flag)
`limine` (=0.5.0), `x86_64`, `spin`, `bitflags`, `uart_16550`, `acpi` (rust-osdev, including its AML support; verify the current crate layout when first used), `pc-keyboard`, `linked_list_allocator`, `fontdue` (no_std + alloc), `log`. Host-only (tools/tests): `png`, `qoi`, anything in `tools/xtask`, and **`proptest` as a dev-dependency, which is required for every parser** (ELF, USTAR, FAT32, GPT, IPC messages, command line, symbol map) because those read untrusted bytes. **No `std` in `kernel/`, `crates/` (non-test), or `user/`.**

---

## 5. Resource budgets (`tests/budgets.toml`, protected; enforced by `make budget` from M9)

Measured **under KVM** (`PERF=1`) on q35, 1 vCPU, `-m 256M`, at the idle desktop. The baseline is 1280×800; RAM and compositing are also measured at 1920×1080 and 3840×2160 (2× UI scale), because many real PCs hand the OS a native-4K framebuffer. Without KVM, budgets report **SKIP, never PASS**. The kernel reports its own metrics on serial as `KVIBE-METRIC <name>=<value>`.

| Metric | Measured by | Target | Hard limit |
|---|---|---|---|
| Kernel entry → desktop first frame | TSC delta, `KVIBE-METRIC boot_ms` | ≤ 250 ms | ≤ 500 ms |
| QEMU start → desktop (wall, SeaBIOS) | harness clock | ≤ 1.5 s | ≤ 3 s |
| RAM in use at idle desktop (1280×800) | frame-allocator used frames | ≤ 24 MiB | ≤ 40 MiB |
| RAM in use at idle desktop (1920×1080) | same | ≤ 32 MiB | ≤ 48 MiB |
| RAM in use at idle desktop (3840×2160, 2×) | same | ≤ 80 MiB | ≤ 112 MiB |
| Minimum RAM to reach desktop | boot test with `-m 64M` / `-m 128M` | 64 MiB | 128 MiB |
| Idle CPU (10 s, no input) | `hlt`-time accounting | ≥ 99.5 % idle | ≥ 99 % idle |
| Full-screen composite (1280×800) | TSC around repaint | ≤ 8 ms | ≤ 16 ms |
| Full-screen composite (3840×2160) | same | ≤ 16 ms | ≤ 33 ms |
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
| Scaling | Integer UI scale 1× or 2×; auto 2× at ≥ 3000 px wide; fonts rasterized at the scaled size; icons ship @1x and @2x |
| Input | US QWERTY; key repeat (500 ms delay, 30/s); scroll wheel (PS/2 IntelliMouse + HID); double-click 500 ms |
| Clipboard | Text copy/paste between apps (Ctrl+C/X/V) owned by the desktop process (M10) |
| Screenshot | PrtSc saves the screen to `KVIBEDATA/Pictures/` (M12) |
| Resilience | An app crash or out-of-memory kills only that app. If the desktop crashes, `init` restarts it within 1 s (M10) |

### 6.1 Framebuffer rules (real-hardware performance)
- **Never read the framebuffer.** VRAM reads are extremely slow on real GPUs, though not in QEMU. Compose everything into a back buffer in RAM and copy only damaged rectangles to the screen. `kv-gfx` exposes the front buffer as a **write-only** type so this can't happen by accident.
- **Map the framebuffer write-combining** (PAT) once the kernel owns its page tables (M2). Uncached framebuffer writes are many times slower.
- Repaint only damaged rectangles. A full-screen repaint is the exception (resolution change, wallpaper change).

**Testability is a design requirement.** In test mode the desktop emits structured events on serial: `KVIBE-UI {"ev":"window_opened","id":3,"title":"Calculator"}`, `focus`, `moved`, `minimized`, `start_open`, `frame_presented`, and so on. Apps can emit `KVIBE-UI {"ev":"app_state",...}`, for example the calculator's display value. Tests drive **real input** through QMP (`send-key`, `input-send-event` with an absolute `usb-tablet` pointer) and check outcomes through these events plus QMP `screendump`.

---

## 7. Hardware strategy

| Area | v1 support | How it's verified |
|---|---|---|
| Boot | UEFI GOP + legacy BIOS via Limine | QEMU SeaBIOS + OVMF + release-ISO suites; M17 human boot on real PCs |
| Display | Firmware framebuffer (any GPU), virtio-gpu mode-set in QEMU only | QEMU `-vga std` and `virtio-gpu` |
| Keyboard/mouse | PS/2, USB xHCI + HID boot protocol (+ absolute tablet) | QEMU `-device qemu-xhci,usb-kbd,usb-mouse,usb-tablet` and PS/2 |
| Storage | virtio-blk, AHCI, NVMe, **USB mass storage (BOT/SCSI, M15)**; GPT; FAT32 | QEMU `virtio-blk-pci`, `ich9-ahci`+`ide-hd`, `nvme`, `usb-storage` |
| Power | ACPI S5 shutdown via AML, reset via ACPI reset register / keyboard controller fallback, ACPI power button → clean shutdown | QEMU: poweroff exit + `KVIBE-POWEROFF`; QMP `system_powerdown` |
| Time | CMOS RTC read; TSC calibrated against HPET/PIT | QEMU |
| Not supported | Wi-Fi, BT, audio, GPU acceleration, I2C-HID touchpads, battery, suspend, multi-monitor, non-US layouts, Secure Boot (on), Hyper-V Gen 2 | — |

**Real-hardware safety rules**
- Test hooks (`isa-debug-exit` writes, fw_cfg reads, the test watchdog) run **only** when CPUID reports a hypervisor (KVM or TCG signature) **and** QEMU fw_cfg's `"QEMU"` signature is present **and** `opt/kvibe/test` is set.
- The **shared ESP is treated as read-only** except for pre-allocated, fixed-size files (`\kvibe\log.txt`, `\kvibe\settings.toml`). Those are overwritten in place with no cluster allocation and no directory changes. Full FAT32 writes happen only on our own `KVIBEDATA` partition. This keeps a bug in our FAT driver from corrupting Windows' boot files.
- Logs from real-hardware boots go to the ESP log file, so you can retrieve them from Windows or Linux and share them. **On a live USB that ESP is the USB stick itself, which is why USB mass storage (M15) comes before real-hardware bring-up.** A panic draws an on-screen panic screen with a **symbolized** backtrace (function names from `boot/kernel.sym`).
- **Safe mode** (`kvibe.safe`, a boot menu entry) skips every risky driver (xHCI, AHCI, NVMe, USB storage, ACPI AML) and uses default settings. A driver that hangs on some PC can't make the OS unbootable, and you can still grab logs.
- **Hardware report:** every boot logs `KVIBE-HW` lines (CPU model, memory map, ACPI tables, PCI vendor/device IDs, framebuffer mode) to serial and the ESP log. You boot once, share the log, and the agent adds support for exactly your hardware (`docs/hw-report-howto.md`, M15).

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
   **Release** tier: boots the *release* ISO (menu, default entry) via OVMF with `timeout 25`, so the image users get is tested on every run.
4. **E2E GUI** (`make test-e2e`): Python driver with QMP input, serial `KVIBE-UI` events, and screendump checks (coarse region colours, no pixel-perfect goldens). `timeout 30` per scenario.
5. **Storage/installer** (`make test-storage`, `make test-install`): fixture disks built with mtools/sgdisk on the host and verified with `mtools`, `fsck.fat -n`, and SHA-256 after QEMU exits.
6. **Budget** (`make budget`, `PERF=1`, KVM): §5.

Rules: wait for markers with a deadline, never a fixed sleep. A suite that flakes twice in 20 runs is a bug, not a retry candidate.

### 8.3 Test quality (tests that test the tests)
- **Mutation testing** (`cargo-mutants`, stable Rust): every host crate changed in a milestone must have a **mutation score ≥ 80%**, meaning ≥ 80% of deliberately injected bugs are caught. This catches tests that run code without asserting anything. Coverage alone can't (proven during review: a test with no assertions had 100% coverage and a 0% mutation score).
- **Coverage** (`cargo-llvm-cov`): ≥ 80% line coverage per changed host crate.
- **Property tests** (`proptest`): required for every parser of untrusted bytes (§4.4).
- **Screenshots:** `make screenshot SUITE=x` saves a PNG the agent can open and inspect. Each milestone's `shot:` checklist lines produce a contact sheet (`build/logs/gate-m<N>-screens.html`) for the owner to skim. A single-colour screenshot fails.

### 8.4 Independent CI
`.github/workflows/gate.yml` runs `make verify` (the full gate for the last completed milestone, from scratch) on every push, with KVM, so performance budgets get real numbers. It uploads the ISO, logs and screenshots as artifacts, so the owner can download and try every milestone. Results produced on the agent's machine are never the only evidence. (It takes effect once KVibeOS is its own repository.)

### 8.5 Reviewer-owned test infrastructure
`tools/test.sh`, `tools/qemu-run.sh`, `tools/screenshot.py`, `tools/gate.sh` and `tools/lib/*` are protected. They decide what counts as passing. New test tiers (`e2e` before M7, `budget` before M9, `storage` before M11, `install` before M14) are added by the reviewer in a `REVIEW:` commit. The agent requests them, or any QEMU option change, through `FLAGS.md` with the exact proposed change.

---

## 9. Milestones

### Milestone gate: nothing moves forward until every check passes
A milestone is done **only** when `make gate` passes. `tests/gates/m<N>.txt` is the binding acceptance list: every check ID derived from the acceptance text below. The gate is enforced by a git `commit-msg` hook, which refuses an `M<N>:` commit unless the gate passed for exactly the tree being committed. `make gate` checks, in order:

1. **Order:** gating `MILESTONE + 1` only (no skipping), the checklist exists, the hook is installed, and suites are registered for M<N>.
2. **Protected files:** unchanged since the last `REVIEW:`/`M<n>:` commit. `tests/suites.txt` and `tests/gates/m<N>.txt` are append-only, and only for M<N>.
3. **Scope:** every changed file matches the milestone's `scope:` globs (plus MILESTONE, FLAGS.md, the journal, suite/checklist appends, Cargo.lock).
4. **No open blocking flags** in `FLAGS.md`, and the **progress journal** `docs/progress/m<N>.md` has Summary / Decisions / Known issues / Next.
5. **Static:** `rustfmt --check` and `clippy -D warnings` per workspace. No `todo!`/`unimplemented!`/`dbg!`/`#[ignore]`/crate-wide `allow`. No `|| true` in test tooling. Every `unsafe` has a `SAFETY:` comment and sits only in `arch/`, `mm/`, `drivers/`, `syscall/`, or `libkv/src/sys.rs`.
6. **Hygiene:** no tracked build outputs, no files > 5 MiB.
7. **Clean build + full regression** of M0..M<N> (stale artifacts deleted first; nothing is tested if the build fails).
8. **Test quality** for host crates changed in M<N>: coverage ≥ 80%, mutation score ≥ 80% (§8.3).
9. **Screenshots** for every `shot:` line, non-blank, plus a contact sheet.
10. **Zero FAIL lines** in `results.txt`, and **every checklist ID PASS**. `budget/*` may be SKIP only without KVM, reported as a warning (CI then supplies the real numbers).
11. **Flake check:** M<N>'s suites run 3× more (20× at M16).
12. **Budgets** (M9+).

`make verify` re-runs the same checks for the last completed milestone without bumping anything. CI runs it on every push.

On success the gate bumps `MILESTONE`, writes a report (`build/logs/gate-m<N>.md`), and fingerprints the tree. Any edit after that invalidates the gate. Commit subjects: `M<N>: …` (gated), `WIP M<N>: …` (cannot touch protected files), `REVIEW: …` (stronger-model/human reviews only).

### M0 — Toolchain, Limine, harness self-test ✅ (done by the reviewer)
- **Goal:** `make doctor` checks host tools. `make iso` builds a Limine ISO with a kernel that prints `KVIBE-BOOT OK` on COM1. The harness verdicts are proven correct. The gate and commit hook are in place.
- **Files:** `Makefile`, `rust-toolchain.toml`, `kernel/{Cargo.toml,.cargo/config.toml,linker.ld,build.rs}`, `kernel/src/{main.rs,log.rs}`, `kernel/src/arch/x86_64/{cpu,port}.rs`, `kernel/src/drivers/{serial,fwcfg,qemu_exit}.rs`, `kernel/src/test/{mod,exit,harness,smoke}.rs`, `boot/limine.conf`, `tools/{doctor,qemu-run,test,mkiso,gate}.sh`, `tools/lib/{common,classify}.sh`, `tests/{protocol.md,suites.txt,budgets.toml}`, `tests/unit/classifier.sh`, `tests/gates/*`, `.githooks/commit-msg`, `.gitignore`.
- **Acceptance:** `make gate M=0`, i.e. every ID in `tests/gates/m0.txt`. Verdicts: `harness-pass`→33/PASS, `harness-fail`→35/FAIL, `harness-panic`→35/FAIL, `harness-hang`→124/TIMEOUT, `harness-triplefault`→0/TRIPLE_FAULT (`Triple fault` in the `-d` log), `none` (no fw_cfg key)→124 with `KVIBE-BOOT OK` and no `KVIBE-TESTMODE`/`KVIBE-TEST` lines, `smoke` via SeaBIOS and via OVMF→PASS, and the classifier unit test passes.
- **Measured:** a TCG boot to the test result takes 0.4 s under SeaBIOS and 3.3 s under OVMF. The ISO is 3.8 MiB.
- **Out of scope:** interrupts, memory management, graphics.

### M1 — CPU foundation and diagnostics
- **Goal:** GDT+TSS with IST stacks, IDT with all exception handlers, a panic handler (serial + framebuffer text with a **symbolized** frame-pointer backtrace, using the `boot/kernel.sym` Limine module parsed by `kv-symbols`), framebuffer text console with an embedded bitmap font, a parsed memory map log, a version string (`KVIBE-INFO version=<semver>+<git-hash>`), and kernel command-line parsing in `kv-bootcfg` (`kvibe.safe`, `kvibe.verbose`; in gated test mode, extra options come from fw_cfg `opt/kvibe/cmdline`). In test mode, emit `KVIBE-SCREEN-READY` when the screen is final.
- **Files:** `kernel/src/arch/x86_64/{gdt,idt,exceptions}.rs`, `kernel/src/{panic,log}.rs`, `kernel/src/drivers/fbcon.rs`, `kernel/src/test/{mod,boot}.rs`, `crates/{kv-bootcfg,kv-symbols}/`, `assets/fonts/`.
- **Acceptance (`tests/gates/m1.txt`):** `int3` returns; a deliberate page fault in test mode reports the correct CR2, then recovers by skipping the instruction; deliberate kernel stack overflow hits the double-fault handler on its IST stack (no triple fault); a framebuffer checksum after printing a known string matches across 3 boots; `boot-panic` prints a backtrace with function names (no host addr2line); the `boot` suite passes on the test ISO, via OVMF, and on the release ISO; screenshots of the console and the panic screen are non-blank.
- **Out of scope:** heap, timers.
- 🔶 **Stronger-model review after M1:** memory map / higher-half layout before M2.

### M2 — Memory management
- **Goal:** bitmap frame allocator, 4-level page-table manager, kernel heap with `alloc`, guard pages for kernel stacks. **Memory protection:** EFER.NXE on, kernel `.text` read-only + executable, `.rodata`/`.data`/heap/stacks non-executable (W^X). PAT programmed with a write-combining entry, and the framebuffer remapped write-combining (§6.1).
- **Files:** `kernel/src/mm/{frame,paging,heap,mod}.rs`, `kernel/src/test/mm.rs`.
- **Acceptance (`SUITE=mm`):** 10k mixed Box/Vec allocations with a free count back at baseline (no leak); map/unmap round-trip; writing past a stack guard page faults as expected; writing to kernel `.text` and executing from `.data` both fault; the framebuffer PTE uses the WC PAT index; the `boot` suite passes with `-m 64M` and `-m 4G`.
- **Out of scope:** user address spaces.

### M3 — Interrupts and time
- **Goal:** ACPI MADT parsing, LAPIC + IOAPIC (PIC masked), TSC calibrated via HPET or PIT, 100 Hz APIC timer, `hlt` idle loop with idle accounting, CMOS RTC, test watchdog with an in-kernel symbolized backtrace. Start the **hardware report**: `KVIBE-HW` lines for CPU model, memory map and ACPI table list.
- **Files:** `kernel/src/arch/x86_64/{apic,ioapic,timer,tsc}.rs`, `kernel/src/drivers/{acpi,rtc}.rs`, `kernel/src/test/{time,watchdog}.rs`.
- **Acceptance (`SUITE=time`):** ticks increase monotonically. Over 300 ticks, TSC-measured elapsed time is within ±25% of expected (TCG timing is coarse). Idle accounting reports ≥ 95% under TCG with no load. The watchdog test hangs with interrupts on, expects exit 35, and expects a backtrace line naming `watchdog_hang_target` (symbolized by the kernel itself).
- **Out of scope:** scheduler.

### M4 — Kernel threads and scheduler
- **Goal:** kernel threads, preemptive round-robin, sleep, wait queues, an interrupt-safe `IrqMutex`.
- **Files:** `kernel/src/task/{thread,sched,wait,mod}.rs`, `kernel/src/sync.rs`, `kernel/src/test/sched.rs`.
- **Acceptance (`SUITE=sched`):** 4 threads each increment their own counter for 200 ticks and all make progress. Producer/consumer through a wait queue completes 10k items. A sleep of 50 ticks wakes between 50 and 60. 2-second lock stress with a timer-interrupt-heavy workload completes without a watchdog trip.
- **Out of scope:** user mode, SMP.
- 🔶 **Stronger-model review:** locking and interrupt-safety design.

### M5 — Userspace
- **Goal:** ring 3, `syscall`/`sysret`, per-process address spaces, ELF loader, initrd (USTAR) as a Limine module, `libkv` runtime, `/init`, process exit/wait, and user-pointer validation. **Isolation:** SMEP and SMAP on (kernel touches user memory only inside explicit `stac`/`clac` copy routines), user stacks and heaps non-executable, per-process memory limits, and out-of-memory kills the offending process instead of panicking.
- **Files:** `kernel/src/{syscall/,task/process.rs,fs/initrd.rs}`, `crates/{kv-abi,kv-elf,kv-ustar}/`, `user/{libkv,init}/`, `user/apps/crashtest/`, `tools/xtask` (initrd packing).
- **Acceptance:** `cargo test -p kv-elf -p kv-ustar` passes. `SUITE=user`: `/init` prints through a syscall and spawns `hello`, which exits 7, and the kernel reports exit code 7. `crashtest null` dies with a page fault while the kernel and init keep running. A syscall with a kernel-space pointer returns `EFAULT`. 100 spawn/exit cycles return the free-frame count to baseline. SMEP/SMAP are set in CR4, and a deliberate kernel read of a user pointer outside the copy routines faults. Executing from a user stack faults. A process that allocates without limit is killed and the kernel keeps running. `kv-elf`/`kv-ustar` have property tests.
- **Out of scope:** files beyond the initrd, IPC.
- 🔶 **Stronger-model review:** syscall ABI and user-memory validation (the security boundary).

### M6 — IPC, shared memory, VFS
- **Goal:** message channels (send/recv/blocking), shared-memory regions, handles, VFS over the initrd, file syscalls.
- **Files:** `kernel/src/{ipc/,fs/vfs.rs}`, `crates/kv-abi/`, `user/libkv/src/{ipc,fs}.rs`, `user/apps/ipctest/`.
- **Acceptance (`SUITE=ipc`):** a 10k-message ping-pong between two processes completes. A shared region written by A is read by B with the expected checksum. A dead peer produces `EPIPE`, not a hang. `read("/apps/calc.toml")` matches the initrd content.
- **Out of scope:** writable filesystems.

### M7 — Input
- **Goal:** PS/2 keyboard and mouse (IntelliMouse scroll wheel), PCI enumeration (added to the hardware report: vendor/device IDs), xHCI controller + HID boot keyboard/mouse + `usb-tablet`, an input event queue exposed to userspace. `kv-input` turns scancodes/HID usages into key events with the **US QWERTY** keymap (shift, caps lock, punctuation) and key repeat. Safe mode skips xHCI.
- **Files:** `kernel/src/drivers/{ps2,pci,xhci/,hid}.rs`, `user/libkv/src/input.rs`, `tests/e2e/{qmp.py,serial.py,input_basic.py}`.
- **Acceptance (`make test-e2e SCENARIO=input_basic`):** under each of the configs `ps2` and `xhci`, QMP `send-key a` produces `KVIBE-INPUT key=a down/up`, and a tablet absolute move to (640,400) produces a pointer event at (640,400) ±1. Hot-plugging the USB keyboard through QMP `device_add` is detected. Shifted symbols (`!@#`) map correctly, holding a key repeats it, and scroll-wheel events arrive under both configs. With `kvibe.safe`, xHCI is skipped and PS/2 still works.
- **Out of scope:** USB storage, hubs beyond QEMU's root hub, I2C-HID.
- 🔶 **Stronger-model review:** the xHCI driver (complex, and the most likely real-hardware failure).

### M8 — Graphics and font libraries
- **Goal:** `kv-gfx` (surfaces, fill, blit, alpha, rounded rects, damage-rect union, QOI decode, a write-only front-buffer type, integer UI scaling) and `kv-font` (fontdue + glyph cache + layout). Asset pipeline PNG → QOI, with @1x and @2x icons. Kernel maps the framebuffer into a privileged process.
- **Files:** `crates/{kv-gfx,kv-font}/`, `tools/xtask/src/assets.rs`, `assets/`, `kernel/src/syscall/fb.rs`.
- **Acceptance:** `cargo test -p kv-gfx -p kv-font` passes, including golden **hashes** of deterministic renders (integer math, so exact). `SUITE=fb`: a user process maps the framebuffer and draws a test pattern, and a host QMP screendump region check matches.
- **Out of scope:** windows.

### M9 — Desktop: compositor, window manager, taskbar
- **Goal:** the `desktop` process: wallpaper, cursor, windows (open, focus, drag, z-order, close, minimize, maximize, snap), taskbar with window buttons and clock, `kv-proto` protocol, a `KVIBE-UI` event stream, `KVIBE-METRIC boot_ms`. Correct at 1280×800, 1920×1080 and 3840×2160 (auto 2×: taskbar 88 physical px at 4K).
- **Files:** `crates/{kv-wm,kv-proto}/`, `user/desktop/`, `user/libkv/src/window.rs`, `user/apps/hello-window/`, `tests/e2e/{desktop_basic,window_ops}.py`, `tools/budget.sh`, `tests/budgets.toml`.
- **Acceptance:** `cargo test -p kv-wm` passes (hit-testing, focus order, snap geometry, minimize/restore). E2E: 2 windows open and their taskbar buttons appear; dragging a title bar 200 px produces `moved` with the correct delta; a taskbar click minimizes and then restores; dragging to the left edge snaps to half-screen; the screendump's bottom 44 px matches the taskbar colour (88 px at 4K). **`make budget` is now enforced**, including the 1080p and 4K RAM and composite budgets.
- **Out of scope:** start menu, real apps.

### M10 — Start menu, `kv-ui`, first apps
- **Goal:** start menu (search, app list, power), the `kv-ui` widget toolkit, and the Terminal (built-in commands: `ls cat echo ps mem clear help dmesg`), Notepad, and Calculator apps. **Text clipboard** (Ctrl+C/X/V) between apps. `init` restarts the desktop within 1 s if it crashes.
- **Files:** `crates/kv-ui/`, `user/desktop/src/start/`, `user/apps/{terminal,notepad,calc}/`, `tests/e2e/{start_menu,calc,crash_isolation}.py`.
- **Acceptance:** Super produces `start_open`. Typing "calc" + Enter produces `window_opened title=Calculator`. Clicking `2 + 3 =` produces `app_state display=5`. Launching `crashtest` from the menu kills only that process, its window disappears, and the desktop keeps responding (a subsequent click produces an event). Copy in one Notepad, paste in another, and the text matches. Killing the desktop process brings it back (`KVIBE-UI desktop_ready` again) with apps relaunchable. Budgets green.
- **Out of scope:** file dialogs, persistent settings.

### M11 — Storage
- **Goal:** virtio-blk, AHCI, NVMe drivers; GPT; block cache; FAT32 read/write (`kv-fat`); the shared-ESP write restriction from §7.
- **Files:** `kernel/src/drivers/{virtio_blk,ahci,nvme}.rs`, `kernel/src/fs/{gpt,fat,cache}.rs`, `crates/kv-fat/`, `tests/fixtures/fat32-known.sh`, `kernel/src/test/storage.rs`.
- **Acceptance:** `cargo test -p kv-fat` passes against images made with `mkfs.fat`. `make test-storage` runs once per controller type (virtio, ahci, nvme): read known files and match hashes; write a 1 MiB file plus nested directories; after QEMU exits, the host verifies with `mcopy` + `sha256sum` and `fsck.fat -n` reports clean. A test that the ESP-mode mount rejects cluster allocation (`EROFS`) passes.
- **Out of scope:** other filesystems, USB storage, journaling.
- 🔶 **Stronger-model review before and after:** FAT write path (data corruption risk on real disks).

### M12 — File Explorer, dialogs, Image Viewer, settings persistence
- **Goal:** Explorer (navigate, open, rename, delete, new folder, on `KVIBEDATA` only), common open/save dialogs, Image Viewer (QOI/BMP), `settings.toml` persistence, and **PrtSc** saving a screenshot to `KVIBEDATA/Pictures/`.
- **Files:** `user/apps/{explorer,imageview}/`, `crates/kv-ui/src/dialogs/`, `user/libkv/src/settings.rs`, `tests/e2e/{explorer,save_file}.py`.
- **Acceptance:** E2E: in Explorer, open `docs/readme.txt` from a fixture; it opens in Notepad; edit and save it; the host verifies the new content after exit. Accent colour set in settings survives a reboot (two-boot test on the same disk).
- **Out of scope:** drag-and-drop between windows, image clipboard.

### M13 — Settings, Task Manager, power
- **Goal:** Settings (accent, light/dark, clock format, UI scale 1×/2×/auto, display info, virtio-gpu resolution in QEMU), Task Manager (processes, memory, CPU %, end task), ACPI shutdown/restart from the start menu and **ACPI power button → clean shutdown**, a **Log Viewer** app (kernel log ring buffer), and **About** (version + git hash).
- **Files:** `user/apps/{settings,taskmgr}/`, `kernel/src/drivers/{acpi_power,virtio_gpu}.rs`, `tests/e2e/{power,taskmgr,resolution}.py`.
- **Acceptance:** Start → Shut down makes QEMU exit 0 with `KVIBE-POWEROFF` and no reset in the `cpu_reset` log. Restart produces `KVIBE-REBOOT` and a reset. The Task Manager process list matches the kernel's `KVIBE-METRIC procs`, and End Task on `hello-window` closes it. A virtio-gpu config changes resolution to 1920×1080 and the screendump size matches. QMP `system_powerdown` (power button) produces `KVIBE-POWEROFF`. Log Viewer shows the boot log. About shows the same version and hash as `KVIBE-INFO version=`.
- **Out of scope:** sleep/hibernate, battery.

### M14 — Installer and dual-boot
- **Goal:** an `installer` app on the live ISO with three modes. *Whole disk*: GPT, ESP, and `KVIBEDATA`. *Alongside*: copy to the existing ESP and append nothing outside `\EFI\kvibeos\`, with a BitLocker warning when a BitLocker volume is present. *Update*: install a new version and keep the previous one as a boot entry. Plus the `bcdedit`/`efibootmgr` helper scripts on the ISO and `docs/dual-boot.md` (Secure Boot off or own keys, BitLocker recovery key first, disable Fast Startup).
- **Files:** `user/apps/installer/`, `boot/limine-installed.conf`, `tools/mkimg.sh`, `tests/fixtures/{blank,windows-like-esp}.sh`, `tests/e2e/{install_whole,install_alongside}.py`, `iso-extras/{add-bootentry-windows.cmd,add-bootentry-linux.sh}`.
- **Fixture:** a GPT disk with a 100 MB ESP holding `EFI/Microsoft/Boot/bootmgfw.efi`, a tiny EFI stub that prints `WINDOWS-STUB` and exits `0x12` through isa-debug-exit, plus an `EFI/BOOT/BOOTX64.EFI` copy and a dummy NTFS partition with known content.
- **Acceptance:** after the alongside install, SHA-256 of `EFI/Microsoft/**`, `EFI/BOOT/**`, and the NTFS partition is unchanged; the ESP total fits within the §5 budget; `fsck.fat -n` is clean. The host adds a boot entry with `virt-fw-vars` (simulating `bcdedit`). Booting it reaches the desktop, and choosing the "Windows" Limine entry reaches `WINDOWS-STUB` (exit 37). The whole-disk install boots under both OVMF and SeaBIOS. A fixture with a BitLocker signature shows the warning and requires confirmation. After `--update`, the previous-version entry exists and boots the old build.
- **Out of scope:** resizing partitions, writing NVRAM from our OS, Secure Boot.
- 🔶 **Stronger-model review before and after.**

### M15 — Real-hardware readiness
- **Goal:** everything needed so a real-PC boot produces useful evidence. USB mass storage (BOT/SCSI over xHCI) so a live USB can write `\kvibe\log.txt` (pre-allocated file, in-place writes only) to its own ESP. The full hardware report and any panic go into that log. Safe mode skips every risky driver and still reaches the desktop. On-screen panic screen with a symbolized backtrace. Docs: `docs/hw-report-howto.md` (how to boot, where the log is, how to share it) and `docs/vm-compat.md` (QEMU / VirtualBox / VMware settings; Hyper-V Gen 2 unsupported).
- **Files:** `kernel/src/drivers/usb_storage.rs`, `kernel/src/` (log sink, safe mode), `crates/`, `boot/`, `tests/e2e/`, `tests/fixtures/`, `docs/`.
- **Acceptance (`tests/gates/m15.txt`):** a `usb-storage` fixture is read and written and verified on the host. Booting from a USB-attached image writes a log file containing `KVIBE-HW` lines, and after a deliberate panic the log contains the panic and backtrace. `kvibe.safe` reaches the desktop with risky drivers reported as skipped. Screenshots of the panic screen and safe mode.
- **Out of scope:** USB hubs beyond the root hub, UAS, other filesystems.
- 🔶 **Stronger-model review after:** USB storage write path.

### M16 — Polish and performance pass
- **Goal:** meet every §5 *target* (not just the hard limits) where feasible; add a puzzle game; refine the theme and icons; a boot splash with a progress bar; tidy startup. Add an in-kernel **sampling profiler** (timer-IRQ RIP histogram, symbolized, `KVIBE-PROFILE` lines) to guide optimization. **Reproducible builds:** building the ISO twice from the same commit gives byte-identical output.
- **Files:** as needed in `user/`, `crates/`, `assets/`. Kernel changes only for performance, each with a before/after `make budget`.
- **Acceptance:** `make budget` meets all hard limits (including 1080p and 4K), and `build/logs/budget-targets.md` lists target misses with reasons. The reproducible-ISO and profiler-report checks pass. The full suite passes the 20× flake check. Screenshots of the splash and the desktop at 1080p and 4K.

### M17 — Real-hardware bring-up (human-in-the-loop)
- **Goal:** boot from USB on at least 3 PCs (one with Windows dual-boot). Collect `\kvibe\log.txt` from each.
- **Files:** `docs/hw-checklist.md`, `docs/hw-results/*.md`.
- **Acceptance:** results recorded for boot, display, keyboard, mouse, NVMe/AHCI read, shutdown, and Windows still booting. Each failure becomes a FLAGS.md entry. The agent fixes only what it can reproduce in QEMU, or what it can fix with a clear log-driven change confirmed by a human re-test.

### Parked (after v1; each needs a stronger-model design review first)
SMP · networking (virtio-net/e1000 + smoltcp) · HDA audio · WASM app runtime (`wasmi`) for portable third-party apps · Intel/AMD native modesetting · UEFI NVRAM writes from our OS (including a "Restart into Windows" button via `BootNext`) · Secure Boot signing · KASLR + RDRAND entropy · SSE/FPU in userspace · non-US keyboard layouts · multi-monitor · gaming (see §2.3).

---

## 10. Risks and escalation

| Risk | Mitigation | Escalate when |
|---|---|---|
| Subtle kernel memory/concurrency bugs no test catches | Logic in host-tested crates; `unsafe` confined to `arch/`, `mm/`, and `drivers/`, each block with a `// SAFETY:` comment; frame-count leak checks in every suite; NX/W^X/SMEP/SMAP turn corruption into clean faults; mutation testing on host crates; 20× flake runs at M16 | A crash can't be reproduced deterministically |
| Agent weakens tests | Protected files and tooling; mutation score ≥ 80%; scope check; independent CI re-runs the gate | Any protected-file change |
| Real-hardware driver failures invisible to QEMU | Firmware framebuffer (no GPU driver); safe mode; hardware report; log to boot media incl. live USB (M15); M17 human pass | Each M17 failure |
| Data loss on a shared ESP or disks | ESP write restriction (§7); installer only writes `\EFI\kvibeos\`; fixture hash checks | M11, M14; any change to `kv-fat` write paths |
| TCG timing flakiness | Coarse tolerances, marker waits, perf only under KVM | A suite flakes twice in 20 runs |
| Agent loops on a failure | 3 distinct attempts, then flag | Always |
| Scope creep (networking, a browser, ...) | Parked list | Any request to unpark |

| Slow framebuffer on real GPUs | Write-only front buffer, WC mapping, damage-only repaint (§6.1) | 4K composite budget breach |
| Dual-boot user locked out (Secure Boot, BitLocker) | `docs/dual-boot.md`; BitLocker detection + warning; previous-version boot entry | Any report of a failed Windows boot |

Stronger-model checkpoints: **after M1, M4, M5, M7, before and after M11, before and after M14, after M15, any FLAGS.md entry, any budget breach, any unparking, and before M7/M9/M11/M14 to add the e2e/budget/storage/install test tiers (§8.5).**

---

## 11. Cross-cutting requirements added in revision 2

| # | Requirement | Where | Checked by |
|---|---|---|---|
| 1 | USB mass storage so live-USB boots can write logs | M15 | `storage-usb/*`, `e2e/live_usb_log/*` |
| 2 | 4K: auto 2× UI scale; budgets at 1080p and 4K | M8, M9, M16 | `fb/scale-auto-2x-at-4k`, `e2e/desktop_4k/*`, `budget/*_4k*` |
| 3 | Never read VRAM; framebuffer write-combining | M2, M8 | `mm/pat-write-combining`, `fb/front-buffer-write-only` |
| 4 | Secure Boot + BitLocker guidance and detection | M14 | `install/alongside/bitlocker-warning`, `file:docs/dual-boot.md` |
| 5 | Safe mode + hardware report | M1 (cmdline), M3/M7 (report), M15 | `boot/cmdline-parsed`, `hw/*`, `safe-mode/*` |
| 6 | In-kernel symbolized backtraces | M1, M3 | `boot-panic/symbolized-backtrace`, `watchdog/backtrace-symbolized` |
| 7 | NX, W^X, SMEP, SMAP | M2, M5 | `mm/nx-enabled`, `mm/kernel-*`, `user/smep-enabled`, `user/smap-enabled`, `user/stack-heap-nx` |
| 8 | OOM kills the app; desktop auto-restart | M5, M10 | `user/oom-kills-process`, `e2e/crash_isolation/desktop-crash-restarts` |
| 9 | US QWERTY, key repeat, scroll wheel, clipboard | M7, M10 | `e2e/input_basic-*/*`, `e2e/clipboard/*` |
| 10 | Update with previous-version rollback entry | M14 | `install/update/*` |
| 11 | Third-party license notices on every image | M0 onward | `THIRD_PARTY_NOTICES.md` (copied onto the ISO) |
| 12 | Independent CI with KVM; ISO artifacts per push | now (active once own repo) | `.github/workflows/gate.yml` |
| 13 | Test tooling protected; tiers added by reviewer | now | gate "protected files", commit hook |
| 14 | Mutation ≥ 80%, coverage ≥ 80%, property tests for parsers | every milestone with host crates | `quality/<crate>/*` |
| 15 | Scope check against milestone file globs | every milestone | gate "scope" |
| 16 | Screenshots for the agent; contact sheet for the owner | M1 onward | `shot:` lines |
| 17 | Progress journal for session continuity | every milestone | gate "progress journal" |
| — | Great-to-haves folded in: PrtSc (M12), log viewer + `dmesg` (M10/M13), ACPI power button (M13), version string (M1/M13), boot splash + profiler + reproducible ISO (M16), VM compatibility doc (M15) | as listed | their checklist IDs |
