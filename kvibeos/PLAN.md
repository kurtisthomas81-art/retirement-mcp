# KVibeOS — Build Plan

Status: **approved architecture, pre-M0**. This file is the source of truth for the build agent.
Changes to architecture, budgets, or milestone acceptance tests require a human/strong-model
review (see §9). The build agent flags them and does not make them on its own.

---

## 0. Assumptions (made instead of asking)

None of these change the recommended path. They only change details inside it.

| # | Assumption | If wrong |
|---|---|---|
| 1 | Target is **x86-64 PCs from ~2015 onward with UEFI**. No legacy BIOS, no ARM. | ARM/Apple Silicon is a separate project. BIOS boot adds a syslinux path, so it's a small change. |
| 2 | "Gaming" means **Steam + Proton games and Windows .exe games via Wine/Proton**. | Kernel-level anti-cheat titles (Valorant, some others) **cannot** run on any Linux path. See §1.3. |
| 3 | GPU vendors: AMD and Intel fully, NVIDIA Turing (GTX 16/RTX 20) and newer. | Older NVIDIA cards get nouveau only. No gaming support. |
| 4 | Single user, home use. No enterprise features (domain join, smartcards). | — |
| 5 | The build host is any Linux with Docker. `/dev/kvm` is strongly preferred. The user's Unraid box qualifies. | Without KVM the tests still run under TCG, roughly 6× slower, and performance budgets are reported as SKIP. |

---

## 1. Feasibility and path decision

### 1.1 Comparison

| | **A. Bare-metal Rust kernel** | **B. Minimal Arch-based distro + custom shell** | **C. Hybrid (A as learning, B as product)** |
|---|---|---|---|
| Effort to a usable desktop | Years. A framebuffer window manager in QEMU takes months. A usable OS takes much longer. | Weeks to a few months of agent milestones. | B's effort plus A's effort, with one agent split between them. |
| Real hardware | GOP framebuffer, maybe AHCI/NVMe, PS/2 or basic USB HID. **No Wi-Fi, no GPU acceleration, no audio on most machines, no Bluetooth.** | Practically everything the Linux kernel and linux-firmware support. That is the broadest driver base outside Windows. | Same as B for the product. |
| GPU / gaming | No GPU acceleration in any realistic timeframe (no Mesa, Vulkan, or DXVK). At most, software-rendered toy games. | Mesa (RADV/ANV/NVK), NVIDIA proprietary, Vulkan, DXVK/VKD3D through Proton. Most of the Steam catalog runs. | Same as B. |
| App compatibility | None. Every app would be written from scratch. Running Linux or Windows apps would need a syscall/ABI layer, which is a multi-year project. | Native Linux apps, Flatpak (Flathub), AppImage, Steam/Proton, Wine/umu for .exe. | Same as B. |
| Boot / footprint | Excellent (sub-second, a few MB), because it does almost nothing. | Good: ≤ 6 s to desktop, ≤ 4 GiB installed. Not "tiny" in absolute terms, because firmware, Mesa, and a browser dominate. | Same as B. |
| Dual-boot / live / VM | Live/VM is easy. Dual-boot is easy (it's just an EFI app). | Standard: EFI, systemd-boot, ISO, and qcow2 are all well-trodden. | Same as B. |
| Risk for an unattended agent | **Very high.** Low-level bugs (paging, interrupts, allocators) hide easily, and the agent can't tell "booted" from "works". | **Moderate.** Most risk is in integration and in hardware the agent can't see. Both can be mitigated with tests and human hardware gates. | High. Two codebases, and the learning track adds nothing to the product. |
| Fit with vision priorities 1–5 | Meets 2, 4, 5. **Fails 1 and 3.** | **Meets 1–5**, with the caveats in §1.3. | Meets 1–5 through B, at higher cost. |

### 1.2 Recommendation: **Path B — a minimal Arch-based distro with a custom Windows-style shell.**

- **Arch over Buildroot.** Steam/Proton needs glibc plus 32-bit multilib libraries, and Arch has them out of the box. Arch also gives binary packages, so ISO builds take minutes instead of the hour-plus a source build needs. That matters a lot when the agent rebuilds and tests in a loop. Arch also has current Mesa and kernels, which games need, and `mkarchiso` (the official, scriptable ISO builder). Buildroot would make us maintain every component ourselves and build an image-based updater. Debian has Mesa that is too old for new games. Fedora is viable but ships heavier defaults that we would have to remove.
- **Keep systemd. Don't write a tiny init.** logind (seat and GPU access for Wayland), udev (plug-and-play), user sessions, socket/D-Bus activation (services that cost nothing until used), systemd-boot, and VM credentials (used by our test harness) all come with it. Steam, Flatpak, portals and PipeWire all assume these pieces. A custom init would save about 10–15 MB of RAM and break compatibility. The resource budget is enforced through a **service whitelist** instead (§3).
- **Don't write a compositor. Use labwc.** labwc is a wlroots stacking compositor with server-side decorations, edge snapping, Alt-Tab, XWayland, and the foreign-toplevel and virtual-input protocols we need for testing. All the Windows-style chrome we write ourselves lives in one Rust/GTK4 shell process (§4).
- **Path C is not recommended.** If you want the bare-metal Rust kernel as a hobby, start it as a separate repo later. It does not feed the product.

### 1.3 What the vision can and can't achieve on Path B — stated plainly

**Can:**
- A familiar layout: bottom taskbar, start menu with search, windowed apps with min/max/close, snapping, Alt-Tab, file explorer, system tray, quick settings.
- Idle RAM of about 350 MiB and about 20–25 processes, against several GB on Windows.
- Desktop in ≤ 6 s from the bootloader on an NVMe SSD.
- Most of the Steam library through Proton, plus Windows .exe apps and games through umu/Proton, Flatpak apps, and AppImages.
- Standard dual-boot next to Windows on the same ESP, a live ISO you write to USB, and a qcow2/VMDK VM image.

**Can't (or only partially):**
- **Games with kernel-level anti-cheat that don't allow Linux** (Valorant, some major shooters) will not run. No Linux distro can fix this. Dual-boot exists for those games.
- **Windows desktop apps through Wine are partial.** Simple apps and older software usually work. Microsoft Office (desktop), Adobe Creative Cloud, and some drivers/utilities do not.
- **The disk footprint is small, not tiny.** About 3–4 GiB installed before Steam. Firmware, GPU drivers and the browser are the floor.
- **Secure Boot on** requires extra work: enrolling our own keys, or disabling Secure Boot. Some Windows games now require Secure Boot, so dual-booters may have to toggle it. This is scheduled late (M16) and needs stronger-model review.
- **Pre-Turing NVIDIA** gets no gaming support.
- **Shrinking an existing Windows partition is not done by our installer in v1.** The user shrinks it in Windows Disk Management, and we install into the free space.
- **Real-hardware quirks** (GPU drivers, Wi-Fi, suspend/resume, firmware boot-order resets) are invisible to QEMU. They need a human test pass on real machines (M15).

---

## 2. Target architecture and boot

### 2.1 Platform
- x86-64 (x86-64-v2 baseline for compatibility), UEFI only.
- Base: Arch Linux official repos, **pinned to an Arch Linux Archive snapshot date** for reproducible builds (`ARCH_SNAPSHOT` in the Makefile). Our own packages come from a local `[kvibe]` pacman repo built from `pkgs/`.
- Kernel: stock Arch `linux`. No custom kernel compile, because it's too slow for the agent loop and too risky for hardware support. `linux-zen` is the only allowed swap, and only through a flag.
- Firmware: Arch's split `linux-firmware-*` packages, all vendors included for plug-and-play.
- Filesystem (installed): **btrfs with zstd:1 compression**. Subvolumes are `@` and `@home`. Snapper takes a snapshot before every update. Swap is zram (zram-generator) with no swap partition.

### 2.2 Boot method
| Image | Bootloader | Notes |
|---|---|---|
| Live ISO (USB/DVD/VM) | systemd-boot (archiso `uefi.systemd-boot.esp` + `.eltorito`) | Hybrid ISO that works with `dd`, Rufus (DD mode), and Ventoy. |
| Installed | **systemd-boot + XBOOTLDR partition + unified kernel images (UKI)** | Only the ~150 KB systemd-boot binary goes on the ESP. Kernels/UKIs go on our own 1 GiB XBOOTLDR partition. That avoids the classic problem where Windows' 100 MB ESP is too small for kernels. systemd-boot auto-detects Windows Boot Manager, so no os-prober is needed. |
| VM image | Same as Installed | Produced by running our installer inside QEMU (`make vmimage`), so the VM image always tests the installer too. Exported as qcow2 and VMDK. |

Why not GRUB or Limine: GRUB works, but it is bigger, slower, and has more moving parts (os-prober, config generation). Limine has to read kernels from FAT, which leaves the small-ESP problem unsolved. systemd-boot with XBOOTLDR solves it cleanly and the UKI path boots fastest.

### 2.3 Dual-boot approach
- **Install into existing free space only.** The installer **never** resizes or writes to NTFS, BitLocker, or other OS partitions. Tests verify this with checksums.
- Reuse the existing ESP. Install `\EFI\systemd\systemd-bootx64.efi`. Never overwrite `\EFI\Microsoft\*`. Overwrite `\EFI\BOOT\BOOTX64.EFI` only if it doesn't exist.
- Create a "KVibeOS" NVRAM boot entry and put it first in BootOrder. Windows stays reachable from the systemd-boot menu (`auto-windows`) and from the firmware boot menu.
- If Windows is detected, set the RTC to local time (the Windows default) to avoid the clock-skew problem. Warn the user if BitLocker or hibernation/Fast Startup is detected.
- Fallback documentation for when firmware or Windows Update resets BootOrder (`bcdedit` one-liner, firmware menu key).

### 2.4 Live ISO and VM
- The live ISO autologs into the full desktop and has an "Install KVibeOS" entry in the start menu.
- Persistence is out of scope for v1.
- Test hooks are gated so they can never run on real hardware (§6.2).

---

## 3. Resource budgets (measurable, enforced by `make budget`)

All numbers live in `tests/budgets.toml`, which is a **protected file**. Measured in QEMU/KVM with q35, 2 vCPU, 4 GiB RAM, virtio-vga, the installed qcow2 image, and no apps open, 60 s after the `KVIBE_READY` marker. Each budget has a **hard limit (test fails)** and a **target (reported)**.

| Metric | How measured | Target | Hard limit |
|---|---|---|---|
| Boot to desktop (VM) | `CLOCK_BOOTTIME` at the first panel frame (`KVIBE_READY t=…` in the journal) | ≤ 4.0 s | ≤ 6.0 s |
| Boot to desktop (real NVMe, bootloader→desktop) | Same marker, human-run in M15 | ≤ 5 s | ≤ 8 s |
| Idle RAM used | `MemTotal − MemAvailable` | ≤ 350 MiB | ≤ 500 MiB |
| Shell process RSS (`kvibe-shell`) | `/proc/<pid>/status` VmRSS | ≤ 40 MiB | ≤ 60 MiB |
| Userspace processes at idle | `ps` excluding kernel threads | ≤ 25 | ≤ 30 |
| Idle CPU | Non-idle share of `/proc/stat` over 30 s | ≤ 0.5 % | ≤ 1.0 % |
| Enabled units | `systemctl list-unit-files --state=enabled` (system + user) ⊆ `tests/services.allow` | exact | any extra unit fails the test |
| ISO size | `stat` | ≤ 1.2 GiB | ≤ 1.5 GiB |
| Installed root, apparent | `du -sx --apparent-size /` (minus /home), pre-Steam | ≤ 3.0 GiB | ≤ 4.0 GiB |
| Installed root, on disk | `compsize /` | ≤ 2.0 GiB | ≤ 2.5 GiB |
| ISO build time (warm cache) | wall clock | ≤ 5 min | ≤ 10 min |

**Default service set.** This list is the initial content of `services.allow`:
system: `systemd-journald` (persistent, capped at 50 MB), `systemd-udevd`, `systemd-logind`, `dbus-broker`, `NetworkManager` (iwd backend), `systemd-resolved`, `systemd-timesyncd`, `greetd`, `bluetooth` (starts only when an adapter appears through `bluetooth.target`), `fstrim.timer`, `snapper-cleanup.timer`, `systemd-zram-setup@zram0`.
user: `pipewire`, `wireplumber`, `pipewire-pulse` (socket-activated). Portals and mako are D-Bus activated, so they don't run until something needs them.
**Excluded by default**: cups (installed on demand from Settings), avahi, ModemManager, packagekit, file indexers, geoclue, cron, firewalld (we use a static nftables ruleset loaded once), and any update-checking daemon.

Code rules that support the budgets: the shell is event-driven with no polling, and the clock wakes once a minute, aligned to the minute. No new daemon is added without a `services.allow` change, which counts as a design change.

---

## 4. Architecture and module layout

### 4.1 Runtime stack
```
UEFI → systemd-boot → UKI (linux + initramfs) → systemd
  → greetd (autologin) → labwc (Wayland, XWayland on demand)
      ├─ kvibe-shell   (Rust/GTK4, one process: taskbar, start menu, tray, quick settings, clock)
      ├─ swaybg        (wallpaper)
      ├─ mako          (notifications, D-Bus activated)
      └─ on demand: Thunar, foot, Firefox, kvibe-settings, Steam, games
  user services: PipeWire/WirePlumber, xdg-desktop-portal(-wlr,-gtk)
```
Alternative session in greetd: **"Game Mode"**, which runs `gamescope -e -- steam -gamepadui` (a console-style full-screen session).

### 4.2 Repository layout
```
kvibeos/
├─ PLAN.md  CLAUDE.md  Makefile  MILESTONE  FLAGS.md
├─ tools/
│  ├─ builder/Dockerfile        # Arch builder: archiso, qemu, edk2-ovmf, rust, mtools, labwc, grim, ...
│  ├─ qemu-run.sh               # one QEMU entry point; modes: harness|iso|disk|install|debug|interactive
│  ├─ test.sh                   # suite runner; runs every suite whose milestone ≤ $(cat MILESTONE)
│  ├─ build-iso.sh  build-pkgs.sh  build-shell.sh  make-vmimage.sh  mk-testdisk.sh
│  └─ budget.sh                 # collects metrics from guest JSON, compares to budgets.toml
├─ harness/stub.S               # ~30-line multiboot stub: serial print + isa-debug-exit (harness self-test)
├─ iso/                         # archiso profile
│  ├─ profiledef.sh  packages.x86_64  pacman.conf
│  ├─ efiboot/loader/{loader.conf,entries/*.conf}
│  └─ airootfs/                 # live-only overlay (autologin, installer launcher)
├─ rootfs/                      # files shared by live + installed (packaged as kvibe-defaults)
│  └─ etc/{sysctl.d,udev/rules.d,greetd,xdg/labwc,nftables.conf,systemd/zram-generator.conf,...}
├─ pkgs/                        # PKGBUILDs → local [kvibe] repo
│  ├─ kvibe-shell/  kvibe-settings/  kvibe-installer/  kvibe-defaults/  kvibe-theme/  kvibe-testd/
├─ shell/                       # Rust workspace (std, stable toolchain)
│  ├─ kvibe-shell/              # panel + start menu + tray + quick settings
│  ├─ kvibe-settings/           # GUI + `--cli` mode over the same backend
│  ├─ kvibe-core/               # shared: desktop entries, D-Bus clients (NM, UPower, logind, wpctl)
│  └─ kvibe-wltest/             # test client: list toplevels, virtual pointer/keyboard, screencopy, shell state
├─ installer/                   # Python 3 (stdlib only) backend + answer-file schema; GUI frontend in M12
│  ├─ kvibe_install/  schema/answers.schema.json  fixtures/
├─ tests/
│  ├─ budgets.toml  services.allow  suites.txt  protocol.md        # protected
│  ├─ guest/<suite>.sh          # runs inside the VM via kvibe-testd
│  ├─ host/<suite>.sh           # runs in the builder (headless labwc, cargo test, pytest)
│  └─ fixtures/                 # disk-image recipes (fake Windows ESP + NTFS, existing Linux, ...)
└─ build/                       # gitignored: out/, work/, vm/, logs/, repo/
```

### 4.3 Language choices
- **Rust (std, stable, pinned with `rust-toolchain.toml`)** for the shell, settings, and test client. The compiler errors are a strong signal for an agent that nobody reviews. Crates: `gtk4`, `gtk4-layer-shell`, `wayland-client` + `wayland-protocols-wlr`, `zbus`, `freedesktop-desktop-entry`, `serde`/`toml`.
- **Python 3, stdlib only** for the installer backend. Python is already on the ISO and the backend is easy to unit test with pytest on disk-image fixtures.
- **Bash** for build and test glue, using `set -euo pipefail` and shellcheck-clean.
- **No Qt in the base image.** Qt would add ~100 MB and a second toolkit. Every base app is GTK3/GTK4.

---

## 5. Desktop/shell design (Windows-style UX on a budget)

| Windows feature | How it's achieved | Cost |
|---|---|---|
| Window frames with min/max/close on the right, double-click to maximize | labwc server-side decorations with a custom `kvibe` openbox theme | config only |
| Snap to edges / Super+Arrow | labwc snapping and keybinds | config only |
| Alt-Tab | labwc window switcher | config only |
| Super opens Start; Super+E Explorer; Super+L lock; Super+D show desktop; PrtSc screenshot | labwc keybinds (`onRelease` for a bare Super press) calling `kvibe-shell --toggle-start`, `thunar`, `swaylock`, shell's minimize-all, `grim` | config + small IPC |
| Taskbar (bottom, 44 px): Start, pinned apps, open windows, tray, quick settings, clock/date | `kvibe-shell` layer-shell surface. Windows come from `wlr-foreign-toplevel-management` (activate/minimize/close) | **our code** |
| Start menu: search-first, pinned grid, all apps, power (sleep/restart/shut down/log out) | `kvibe-shell` popup layer surface. Apps from XDG `.desktop` files. Power through logind D-Bus | **our code** |
| System tray | StatusNotifierItem host in `kvibe-shell` (Steam, Discord, etc. use SNI) | **our code** |
| Quick settings (volume, Wi-Fi, Bluetooth, battery, brightness) | `kvibe-shell` popup over NM/UPower/WirePlumber/logind | **our code** |
| Notifications | mako, themed to match | config only |
| File Explorer | Thunar (+ thunar-volman, gvfs for USB/phones/trash) | package |
| Settings app | `kvibe-settings` (display via wlr-output-management + kanshi persistence, network, sound, Bluetooth, default apps, updates, power, add printer) | **our code** |
| Right-click desktop menu | labwc root menu | config only |
| Task manager (Ctrl+Shift+Esc) | `btop` in `foot` for v1 | package |
| Default apps | foot (terminal), mousepad (text), imv (images), mpv (media), xarchiver, Firefox | packages |

Theme: one accent colour, adw-gtk3 for GTK3 apps plus libadwaita defaults, Inter/Noto fonts, and an original wallpaper and logo. **No Microsoft logos, icons, or fonts. "Windows-style" means the layout and behaviour only.**

Testability is a design requirement. `kvibe-shell` exposes a read-only state socket (`$XDG_RUNTIME_DIR/kvibe-shell.sock`, JSON: taskbar buttons, start-menu open/closed, tray items, popups). Tests drive the shell with **real input** through virtual pointer and keyboard (`kvibe-wltest`), then check the outcome through foreign-toplevel and the state socket. Tests never call internal functions to simulate a click.

---

## 6. Hardware and app compatibility strategy

### 6.1 Hardware
| Area | Strategy | v1 scope |
|---|---|---|
| GPU AMD/Intel | Mesa (radeonsi, iris, RADV, ANV) + lib32 variants when Steam is installed | ✅ |
| GPU NVIDIA Turing+ | Default: Mesa NVK + nouveau/GSP (works offline). Proprietary `nvidia-open` + `nvidia-utils` (+lib32) is offered by the installer and Settings when a network is available, chosen by PCI ID. | ✅ (proprietary needs network) |
| GPU NVIDIA pre-Turing | nouveau, desktop only | ⚠️ no gaming |
| Wi-Fi / Ethernet / BT | Kernel + all split firmware, NetworkManager (iwd backend), BlueZ | ✅ except Broadcom `wl`-only chips |
| Audio | PipeWire + WirePlumber, SOF firmware for modern laptops | ✅ |
| Gamepads | Kernel `xpad`/`hid-playstation`/`hid-nintendo` + `game-devices-udev` + Steam's udev rules | ✅ except the Xbox wireless dongle (xone) |
| External displays / hotplug | labwc + kanshi profiles | ✅ |
| USB storage / phones | udisks2 + gvfs (+ gvfs-mtp) through Thunar | ✅ |
| Printers | Driverless IPP (cups + ipp-usb), installed on demand | ✅ on demand |
| Laptop power | Kernel defaults + logind lid handling, UPower for battery | ✅ basic |
| VM guests | qemu-guest-agent / open-vm-tools / virtualbox-guest-utils installed on first boot only when `systemd-detect-virt` matches | ✅ |

### 6.2 Apps
| Kind | Mechanism |
|---|---|
| Steam games | Native Steam (multilib), installed on demand from Welcome/Start: "Get Steam". Proton enabled by default. `vm.max_map_count=2147483642` and `kernel.split_lock_mitigate=0` sysctls. `gamemode` and `mangohud` available. |
| Windows .exe | `kvibe-run-exe` as the MIME handler for `application/x-ms-dos-executable`. It uses **umu-launcher** with Proton in a default prefix `~/.local/share/kvibe/prefix`. Vendor a PKGBUILD if it isn't in the official repos. |
| Other launchers (Epic/GOG/etc.) | Heroic, Lutris, and Bottles through Flatpak |
| Linux desktop apps | Flatpak with Flathub preconfigured, plus pacman for curated native packages |
| AppImage | `fuse2` included so AppImages just run |
| Android | Out of scope |

---

## 7. Test architecture (how the agent knows something works)

### 7.1 Exit-code protocol (`tests/protocol.md`, protected)
- The guest prints `KVIBE-TEST <suite>/<case> PASS|FAIL|SKIP <detail>` lines to `/dev/ttyS0`, then `KVIBE-RESULT PASS|FAIL`.
- The guest exits QEMU by writing to **isa-debug-exit** (port `0xf4`). QEMU's exit status is `(value << 1) | 1`.

| QEMU exit | Meaning | Runner verdict |
|---|---|---|
| 33 (`0x10`) | guest says PASS | PASS (also requires a `KVIBE-RESULT PASS` line) |
| 35 (`0x11`) | guest says FAIL | FAIL |
| 124 | `timeout` killed QEMU (hang) | FAIL (TIMEOUT) |
| 0 | guest reset/rebooted (`-no-reboot` turns a reset into an exit) or a kernel panic with `panic=-1` | FAIL (UNEXPECTED_RESET), check `-d cpu_reset` log |
| other | QEMU/infra error | ERROR (runner exits 2) |

Runner exit codes: `0` pass, `1` fail, `2` infra error, `77` skip.

### 7.2 Test-hook gating (security)
`kvibe-testd.service` ships in every image, so we test what we ship. It runs **only if all three** hold:
1. `systemd-detect-virt --vm` succeeds;
2. a systemd credential `kvibe.test` exists, passed only through QEMU SMBIOS type 11 (`-smbios type=11,value=io.systemd.credential:kvibe.test=<suite>`);
3. a block device labelled `KVIBETEST` is present. This is a host-built FAT image holding `tests/guest/`, so tests change without rebuilding the ISO.

`console=ttyS0` is injected the same way (`io.systemd.boot.kernel-cmdline-extra`) so release boot entries stay clean. If M1 finds that the systemd-boot/stub SMBIOS cmdline mechanism doesn't work, the fallback is `console=ttyS0,115200 console=tty0` in the default entries, and that fallback must be flagged.

### 7.3 Test tiers (fast to slow)
1. **Host unit**: `cargo test`, `pytest installer/` (seconds).
2. **Host shell**: headless labwc in the builder (`WLR_BACKENDS=headless WLR_RENDERER=pixman`) running `kvibe-shell` and `kvibe-wltest` (seconds). This is the shell's main development loop.
3. **Guest ISO**: boot the ISO in QEMU+OVMF and run a suite (≤ 90 s KVM).
4. **Guest disk**: boot the installed qcow2 with `snapshot=on` so it stays pristine (≤ 60 s KVM).
5. **Install/dual-boot**: ISO + fixture disks, then reboot into the result (≤ 5 min KVM).
6. **Budget**: guest disk run that emits metrics JSON, compared against `budgets.toml`.
7. **Network** (`make test-net`, opt-in): Steam bootstrap, Flathub, NVIDIA package resolution.

Under TCG (no `/dev/kvm`) all timeouts are ×6 and performance budgets report SKIP.

Screenshots: QMP `screendump` from the host, which doesn't depend on the guest, plus `grim` in the guest. Image checks are coarse and robust (region mean colour, non-blank, a panel-height strip that differs from the wallpaper). There is no pixel-perfect golden matching.

Readiness: always poll for a marker with a deadline. Never use a fixed `sleep`.

---

## 8. Milestones

Rules for every milestone: the acceptance command must exit 0 **and** all earlier suites must still pass (`make test` runs every suite with milestone ≤ `MILESTONE`). Then commit, tag `mN`, and bump `MILESTONE`.

### M0 — Builder and harness self-test
- **Goal:** a reproducible build container and a QEMU harness whose verdict classification is itself tested.
- **Files:** `tools/builder/Dockerfile`, `tools/qemu-run.sh`, `tools/test.sh`, `harness/stub.S`, `tests/protocol.md`, `tests/suites.txt`, `.gitignore`, `MILESTONE`.
- **Acceptance (`make test-harness`):** the multiboot stub is built in four variants and booted with the exact debug command (CLAUDE.md §3.1, `timeout 5`, `-no-reboot -d int,cpu_reset`). Expected verdicts: `pass`→33/PASS, `fail`→35/FAIL, `hang`→124/TIMEOUT, `triplefault`→0/UNEXPECTED_RESET with a `cpu_reset` line in the log. All four must match.
- **Out of scope:** Linux, ISO, packages.

### M1 — Bootable live ISO (console)
- **Goal:** `make iso` produces a UEFI ISO that boots to a root console in QEMU. `kvibe-testd` runs gated guest suites.
- **Files:** `iso/*`, `pkgs/kvibe-testd/`, `tools/build-iso.sh`, `tools/build-pkgs.sh`, `tools/mk-testdisk.sh`, `tests/guest/smoke.sh`.
- **Acceptance (`make test-iso SUITE=smoke`):** exit 33. Serial shows `KVIBE-TEST smoke/pid1 PASS` (PID 1 is systemd), `smoke/kernel PASS` (`uname -r` matches the package), and `smoke/gate PASS`. A second run **without** the SMBIOS credential must time out with no testd activity in the serial log. A host-side assertion checks this "gate negative" case.
- **Out of scope:** graphics, installer, size trimming.
- 🔶 **Strong-model checkpoint after M1:** review the gating mechanism and the boot entries.

### M2 — Installer backend, whole disk, and VM image
- **Goal:** `kvibe-install --answers file.json` installs to a blank disk: GPT, ESP 512 MiB, XBOOTLDR 1 GiB, btrfs `@`/`@home`, systemd-boot, UKI, NVRAM entry. `make vmimage` produces `build/vm/kvibeos.qcow2`.
- **Files:** `installer/**`, `pkgs/kvibe-installer/`, `tools/make-vmimage.sh`, `tests/guest/install-whole.sh`, `tests/guest/disk-smoke.sh`.
- **Acceptance:** (a) `pytest installer/` passes, including answer-schema validation and partition-plan unit tests against loop-file images. (b) `make test-install FIXTURE=blank` exits 33. (c) `make test-disk SUITE=disk-smoke` boots the result with a persistent OVMF_VARS copy, and `bootctl status` shows systemd-boot, the root fs is btrfs, and compression is zstd.
- **Out of scope:** dual-boot, GUI, user accounts beyond one answer-file user, encryption.
- 🔶 **Strong-model review before starting M2** (partition layout, safety interlocks: the installer refuses to run on non-VM hardware without the GUI confirmation token introduced in M12).

### M3 — Lean baseline and budget enforcement (console)
- **Goal:** trim packages, enable zram and the sysctls, apply the service whitelist, and get `budget.sh` working.
- **Files:** `iso/packages.x86_64`, `rootfs/etc/**`, `pkgs/kvibe-defaults/`, `tools/budget.sh`, `tests/guest/budget.sh`, `tests/services.allow`, `tests/budgets.toml` (initial values come from §3; the console-only interim limits are RAM ≤ 200 MiB and processes ≤ 20).
- **Acceptance (`make budget`):** every console-phase budget is within its hard limit, enabled units ⊆ `services.allow`, ISO ≤ 1.5 GiB, and a metrics report is written to `build/logs/budget.json`.
- **Out of scope:** desktop budgets (they become active in M5).

### M4 — Graphical session
- **Goal:** greetd autologin into labwc with the kvibe theme, wallpaper, XWayland on demand, and keybinds.
- **Files:** `rootfs/etc/greetd/`, `rootfs/etc/xdg/labwc/{rc.xml,menu.xml,autostart,environment}`, `pkgs/kvibe-theme/`, `tests/guest/session.sh`, `shell/kvibe-wltest/` (toplevel listing and screencopy).
- **Acceptance (`make test-disk SUITE=session` and `SUITE=session` on the ISO):** labwc's Wayland socket appears within 10 s. `kvibe-wltest toplevels` lists a launched `foot`. An X11 client (`xeyes` from the test disk, or `xterm`) maps through XWayland. A QMP screendump is non-blank and its centre matches the wallpaper colour within a tolerance.
- **Out of scope:** panel, start menu.

### M5 — `kvibe-shell` taskbar
- **Goal:** bottom taskbar with Start button, open-window buttons (activate/minimize on click), and a clock. State socket. `KVIBE_READY` marker.
- **Files:** `shell/kvibe-shell/**`, `shell/kvibe-core/**`, `shell/kvibe-wltest/` (virtual pointer), `pkgs/kvibe-shell/`, `tests/host/shell-taskbar.sh`, `tests/guest/desktop.sh`.
- **Acceptance:** (a) `make test-shell` in headless labwc: open 2 clients, state shows 2 buttons, a virtual-pointer click on button 1 activates that toplevel (foreign-toplevel `activated`), and a second click minimizes it. (b) Guest: `KVIBE_READY` is logged and the screendump's bottom 44 px strip differs from the wallpaper. (c) `make budget` with the desktop budgets now active: boot, RAM, RSS, processes, and CPU are within hard limits.
- **Out of scope:** tray, quick settings, pinned apps (M6).

### M6 — Start menu
- **Goal:** search-first start menu, pinned apps (also pinned on the taskbar), an all-apps list, and a power menu. Super key toggles it.
- **Files:** `shell/kvibe-shell/src/start/**`, `rootfs/etc/xdg/labwc/rc.xml`, `tests/host/shell-start.sh`.
- **Acceptance (`make test-shell`):** a virtual Super press opens the menu (state `start_open=true`). Typing "foot" and pressing Enter maps a foot toplevel and closes the menu. Esc closes it. A desktop-entry parsing unit test covers `NoDisplay`, `Hidden`, `OnlyShowIn`, localized names, and `TryExec`. The power-menu "Log out" action, run in the guest with a polkit-free logind call, ends the session and greetd restarts it (guest test).
- **Out of scope:** web search, recent files, start-menu customization UI.

### M7 — Core apps, file explorer, defaults, portals
- **Goal:** Thunar with gvfs/udisks2, the default apps, MIME defaults, xdg-desktop-portal-wlr/-gtk, Firefox, Flatpak with the Flathub remote, fuse2.
- **Files:** `iso/packages.x86_64`, `rootfs/etc/xdg/mimeapps.list`, `rootfs/usr/share/xdg-desktop-portal/kvibe-portals.conf`, `tests/guest/apps.sh`.
- **Acceptance:** `xdg-open` on a `.txt`, `.png`, `.mp4`, and a folder opens mousepad, imv, mpv, and Thunar respectively (checked with `kvibe-wltest toplevels` app_ids). A portal FileChooser D-Bus call returns a handle. Hot-adding a USB disk through QMP `device_add usb-storage` makes it appear in `gio mount -l` and auto-mount. `flatpak remotes` lists flathub. Budgets are still green.
- **Out of scope:** office suite, Flatpak app installs (network).

### M8 — Tray, quick settings, notifications
- **Goal:** SNI tray host, a quick-settings popup (volume, Wi-Fi list, Bluetooth toggle, battery, brightness), and themed mako.
- **Files:** `shell/kvibe-shell/src/{tray,quick}/**`, `shell/kvibe-core/src/{nm,upower,audio}.rs`, `tests/host/shell-tray.sh`, `tests/guest/quick.sh`.
- **Acceptance:** a test SNI item (built into `kvibe-wltest sni-item`) appears in the state socket and responds to a click. In the guest, setting the volume slider through virtual input changes `wpctl get-volume`. With a QEMU `virtio-net` device, NM's connection shows in quick-settings state. `notify-send` produces a mako surface (the layer appears in a toplevel/layer dump).
- **Out of scope:** VPN UI, per-app volume.

### M9 — Settings app
- **Goal:** `kvibe-settings` with pages for Display, Network, Sound, Bluetooth, Default apps, Power, Updates (placeholder), About, and Add printer. Each page has a `--cli` equivalent over the shared backend.
- **Files:** `shell/kvibe-settings/**`, `pkgs/kvibe-settings/`, `tests/guest/settings.sh`.
- **Acceptance:** `kvibe-settings --cli display set --mode 1280x720` changes the mode reported by `wlr-randr`, and it persists across a guest reboot (kanshi). `--cli default-apps set text org.gnome.Mousepad` updates `mimeapps.list`. The GUI opens and a screendump is non-blank. The settings process exits when its window closes, so it adds no idle cost.
- **Out of scope:** accounts, themes picker, accessibility beyond GTK defaults.

### M10 — Gaming stack
- **Goal:** multilib repo enabled, Vulkan loader and Mesa (+lib32 pulled in with Steam), on-demand `kvibe-get steam`, gamemode, mangohud, gamescope "Game Mode" greetd session, `kvibe-run-exe` with umu-launcher, controller udev rules, NVIDIA detection logic.
- **Files:** `iso/pacman.conf`, `pkgs/kvibe-defaults/`, `pkgs/kvibe-gaming/` (scripts + `.desktop` + MIME), `rootfs/etc/sysctl.d/80-gaming.conf`, `tests/guest/gaming.sh`, `tests/fixtures/hello-exe/` (mingw-built test .exe, built in the builder).
- **Acceptance (offline):** `vulkaninfo --summary` succeeds (lavapipe/virtio). `vkcube --c 200` exits 0 under both Wayland and XWayland. `gamescope --backend headless -- vkcube --c 100` exits 0. `kvibe-run-exe hello.exe` writes the expected file through Proton/umu, with the Proton runtime staged on the test disk for offline use. The sysctls apply. The NVIDIA selector unit test maps PCI IDs to the expected package set. Budgets still green, with no new services. **Network (`make test-net`):** `kvibe-get steam` installs and the Steam bootstrap starts.
- **Out of scope:** real-GPU FPS benchmarks (M15), anti-cheat, the Xbox wireless dongle.
- 🔶 **Strong-model review:** NVIDIA strategy and multilib size impact.

### M11 — Dual-boot install
- **Goal:** the installer's "Install alongside" mode uses existing free space and reuses the ESP.
- **Files:** `installer/kvibe_install/{probe,plan,bootloader}.py`, `tests/fixtures/{windows-like,linux-existing,small-esp}.sh`, `tests/guest/install-alongside.sh`.
- **Fixtures (built in the builder, no Windows image needed):** a GPT disk with a 100 MB ESP holding `EFI/Microsoft/Boot/bootmgfw.efi` (a dummy EFI binary that prints "WINDOWS-STUB" to serial and exits through isa-debug-exit `0x12`), an MSR partition, an NTFS partition with known content (mkntfs), and free space. Plus an "existing Linux" fixture and a "BitLocker signature" fixture.
- **Acceptance (`make test-dualboot`):** after install, the SHA-256 of the NTFS and MSR partitions and of `EFI/Microsoft/**` is unchanged. A XBOOTLDR partition exists and the ESP gained only `EFI/systemd/**` and `loader/**`. `bootctl list` shows an `auto-windows` entry. Booting the systemd-boot Windows entry reaches "WINDOWS-STUB" (exit 37). The default entry reaches `KVIBE_READY`. The BitLocker fixture makes the installer refuse the alongside mode with a clear message and exit non-zero, without writing anything.
- **Out of scope:** resizing partitions, booting real Windows, encryption.
- 🔶 **Strong-model review before and after** (data-loss risk).

### M12 — Installer GUI and live-session integration
- **Goal:** a GTK4 frontend (Rust, in `shell/kvibe-installer-gui/`) that writes an answer file and runs the backend, plus a "Install KVibeOS" start-menu entry on the live ISO only. The frontend issues the confirmation token required to touch real disks.
- **Files:** `shell/kvibe-installer-gui/**`, `iso/airootfs/usr/share/applications/kvibe-install.desktop`, `tests/host/installer-gui.sh`.
- **Acceptance:** driving the GUI with `kvibe-wltest` virtual input in headless labwc, with the backend in `--dry-run`, produces an answer file byte-identical to `tests/fixtures/answers-expected.json`. On the ISO, the start-menu entry exists, and running it on the blank fixture end-to-end (GUI-driven) yields a bootable disk.
- **Out of scope:** translations, accessibility audit.

### M13 — Updates, snapshots, first-run Welcome
- **Goal:** `kvibe-update` takes a snapper pre-snapshot, then runs pacman + flatpak updates, then a post-snapshot. `kvibe-rollback` restores from the live ISO. A Welcome app offers "Get Steam", "Firefox ready", NVIDIA driver, "Add printer", and Flathub.
- **Files:** `pkgs/kvibe-update/`, `shell/kvibe-settings/src/updates.rs`, `pkgs/kvibe-welcome/` (or a settings page), `tests/guest/update.sh`.
- **Acceptance:** against a local fixture repo with a newer `kvibe-defaults`, the update creates a pre/post snapshot pair, the package version changes, and `kvibe-rollback --to pre` from the ISO returns the old version after a reboot. Welcome opens on the first login only.
- **Out of scope:** automatic background updates (that would mean a daemon, which is a budget conflict), delta updates.
- 🔶 **Policy decision for a human/strong model:** installed systems use live Arch mirrors with snapshots, or a pinned kvibe-tested snapshot channel. The default is pinned-and-advanced-after-tests, and that needs hosting.

### M14 — Release pipeline
- **Goal:** `make release` produces the ISO, qcow2, VMDK, SHA256SUMS, and a budget report markdown. It runs the full `make test` from clean.
- **Files:** `tools/release.sh`, `Makefile`.
- **Acceptance:** `make distclean && make release` exits 0. All suites pass. `build/out/` contains every artifact and the checksums verify.
- **Out of scope:** hosting, signing (M16).

### M15 — Real-hardware validation (human-in-the-loop)
- **Goal:** a checklist run by a person on at least 3 machines (AMD GPU desktop, Intel laptop, NVIDIA RTX desktop with Windows dual-boot). Checks: boot time, idle RAM, Wi-Fi, audio, suspend/resume, external monitor, a gamepad, 3 Proton games within ±5% FPS of stock Arch, and Windows still boots.
- **Files:** `docs/hw-checklist.md`, `docs/hw-results/*.md`.
- **Acceptance:** results recorded. Any failure becomes a FLAGS.md item. The agent fixes only what can be reproduced or tested in QEMU.

### M16 — Secure Boot (optional)
- **Goal:** `sbctl` key generation and enrollment that keeps Microsoft keys (`-m`), with signed UKIs and systemd-boot. Alternatively, a shim-based path.
- 🔶 **Strong model required.** A mistake here can brick boot on some firmware.

---

## 9. Risks and escalation points

| Risk | Mitigation | Escalate to a stronger model / human when |
|---|---|---|
| Agent weakens tests to get green | Protected files (`PLAN.md`, `CLAUDE.md`, `tests/budgets.toml`, `tests/services.allow`, `tests/protocol.md`, `tests/suites.txt`). Changes go through FLAGS.md only. | Any protected-file change is needed |
| Arch rolling breakage | Builds pinned to `ARCH_SNAPSHOT`. Advance the date only in its own commit with a full `make test`. | A snapshot bump fails and the cause isn't one package |
| Installer data loss | Fixture images only. The installer refuses non-VM hardware without the GUI token. The agent never touches host block devices. | M2 and M11, before and after; any change to `installer/kvibe_install/plan.py` |
| GUI tests flaky | Marker-based waits, coarse image checks, state socket | A suite flakes more than once in 10 runs |
| Budget breach needing architecture change (toolkit swap, dropping a component) | Measure and report. Don't swap on your own. | Any hard limit can't be met by config/trim |
| Hardware-only bugs | M15 human pass | Every M15 failure |
| Agent loops on the same failure | 3 distinct attempts per failing acceptance test, then stop | Always, at the 3-attempt limit |
| Licensing/branding | No Microsoft assets. Steam and NVIDIA installed on demand, not redistributed on the ISO. | Any new bundled proprietary component |
| Secure Boot / firmware quirks | Deferred to M16 | Always |

Points where a stronger model must come back: **after M1, before M2, M10 NVIDIA review, before and after M11, M13 update policy, M16, any FLAGS.md entry, and any budget breach.**
