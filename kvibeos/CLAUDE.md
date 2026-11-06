# CLAUDE.md — KVibeOS build agent

You are building KVibeOS milestone by milestone. **PLAN.md is the source of truth. Follow it. Flag design changes. Don't improvise them.** Nobody reads your code. The tests are the only reviewer, so never weaken them.

## 1. What this project is (so you don't drift)
- A minimal **Arch Linux–based** distro: systemd, labwc (Wayland), and our Rust/GTK4 `kvibe-shell` for the Windows-style taskbar and start menu. Shipped as a UEFI live ISO plus a qcow2 VM image.
- **It is NOT a bare-metal kernel.** `no_std` does **not** apply. All Rust here is normal `std` userland on the stable toolchain pinned in `rust-toolchain.toml`. Never add `#![no_std]`, a custom target, or a kernel.
- The only bare-metal code is `harness/stub.S`, a ~30-line multiboot stub used to self-test the QEMU harness. Don't grow it.

## 2. Environment rules
1. **Everything goes through `make`.** Make targets re-exec themselves inside the `kvibe-builder` Docker container (Arch, privileged, `/dev/kvm` passed through when present). Don't install packages on the host. Don't run `mkarchiso`, `pacstrap`, or QEMU outside the builder.
2. **Never touch host block devices.** No `dd`, `sgdisk`, `mkfs`, `kvibe-install`, or `losetup` on anything except image files under `build/`. The installer is only ever run inside QEMU or against loop-mounted files in `build/`.
3. **Hermetic tests.** QEMU runs with `-nic user,restrict=on` unless the target is `make test-net`. Don't add network fetches to `make test`.
4. **Pinned base.** Arch packages come from the archive snapshot in `ARCH_SNAPSHOT` (Makefile). Bump it only in its own commit, followed by a full `make test`.
5. **Packages.** Add packages only from the official repos at the pinned date, or from a PKGBUILD in `pkgs/`. No AUR helpers. Add a one-line `# why` comment next to every package you add to `iso/packages.x86_64`.
6. **No fixed sleeps for readiness.** Poll for a marker or socket with a deadline (`tools/lib.sh: wait_for`).
7. **Shell code is event-driven.** No timers faster than 60 s (the clock aligns to the minute). No busy polling. No new daemons or enabled units: those need a `tests/services.allow` change, and that file is protected.
8. **Branding.** No Microsoft logos, icons, fonts, or wallpapers. Windows-*style* layout and behaviour only.

## 3. QEMU commands (use these exactly; `tools/qemu-run.sh` must produce them)

### 3.1 Harness self-test / early-boot debug (M0; `make test-harness`, `make debug-boot`)
This is the canonical low-level command. `-d int` only logs under TCG, so KVM is deliberately not used here.
```
timeout 5 qemu-system-x86_64 \
  -machine q35,accel=tcg -cpu max -m 128M -smp 1 \
  -kernel build/harness/stub-$(VARIANT).elf \
  -display none \
  -serial file:build/logs/serial-harness-$(VARIANT).log \
  -device isa-debug-exit,iobase=0xf4,iosize=0x04 \
  -no-reboot -d int,cpu_reset -D build/logs/qemu-harness-$(VARIANT).log
```
For `make debug-boot` (kernel dies before userspace), the shape is the same, but it boots the ISO's kernel and initramfs directly with `-kernel/-initrd -append "console=ttyS0 panic=-1 earlyprintk=serial"`, uses `timeout 120` (TCG plus `-d int` is slow and its log is large), and writes `-D build/logs/qemu-debug.log`. Use it only for diagnosis. It is never part of `make test`.

### 3.2 Live ISO test (`make test-iso SUITE=<suite>`)
`timeout 5` is too short for a Linux userspace boot, so the timeout is 90 s with KVM and 540 s under TCG. `-d int` is omitted because it floods the log and is a no-op under KVM. `-d cpu_reset` is kept to explain exit code 0.
```
timeout 90 qemu-system-x86_64 \
  -machine q35,accel=kvm:tcg -cpu max -m 4G -smp 2 \
  -drive if=pflash,format=raw,readonly=on,file=$(OVMF_CODE) \
  -drive if=pflash,format=raw,file=build/vm/OVMF_VARS.iso.fd \
  -cdrom build/out/kvibeos.iso \
  -drive file=build/test/testdisk.img,format=raw,if=virtio,readonly=on \
  -smbios type=11,value=io.systemd.credential:kvibe.test=$(SUITE) \
  -smbios type=11,value=io.systemd.boot.kernel-cmdline-extra=console=ttyS0,115200 \
  -device virtio-vga -display none \
  -qmp unix:build/vm/qmp.sock,server=on,wait=off \
  -serial file:build/logs/serial-iso-$(SUITE).log \
  -nic user,model=virtio-net-pci,restrict=on \
  -device isa-debug-exit,iobase=0xf4,iosize=0x04 \
  -no-reboot -d cpu_reset -D build/logs/qemu-iso-$(SUITE).log
```

### 3.3 Installed-disk test (`make test-disk SUITE=<suite>`)
Same as 3.2, with these changes: replace `-cdrom …` with
`-drive file=build/vm/kvibeos.qcow2,format=qcow2,if=virtio,snapshot=on`, use `OVMF_VARS.disk.fd`, `timeout 60`, and logs `serial-disk-$(SUITE).log`.
Install tests (`make test-install`, `make test-dualboot`) attach the ISO plus a writable fixture disk (no `snapshot=on`), use `timeout 300`, then run a second boot from the fixture.

`OVMF_CODE=/usr/share/edk2/x64/OVMF_CODE.4m.fd`, `OVMF_VARS` template `/usr/share/edk2/x64/OVMF_VARS.4m.fd` (builder paths). Copy VARS fresh per test unless the test needs NVRAM persistence across boots.

## 4. Test protocol (summary; full spec in `tests/protocol.md`)
- Guest prints `KVIBE-TEST <suite>/<case> PASS|FAIL|SKIP <detail>` to `/dev/ttyS0`, then `KVIBE-RESULT PASS|FAIL`, then exits through `kvibe-vm-exit 0x10` (pass) or `0x11` (fail). Those write to port `0xf4`.
- QEMU exit → verdict: **33** PASS (also requires the `KVIBE-RESULT PASS` line), **35** FAIL, **124** TIMEOUT, **0** UNEXPECTED_RESET (read the `cpu_reset` log), anything else ERROR.
- Runner exits: 0 pass, 1 fail, 2 infra error, 77 skip. TCG multiplies timeouts by 6 and marks performance budgets SKIP, **never PASS**.
- Test hooks run only in a VM, with the `kvibe.test` SMBIOS credential, **and** with a `KVIBETEST`-labelled disk present. Never loosen this gating.

## 5. Where to look when something fails
`build/logs/serial-*.log` (guest console and test lines) → `build/logs/qemu-*.log` (`cpu_reset`/`int`) → `build/logs/journal-*.txt` (guest journal exported by testd on failure) → `build/logs/screen-*.png` (QMP screendump on failure) → `build/logs/build-*.log` (mkarchiso/makepkg/cargo).

## 6. Workflow per milestone
1. Read the milestone in PLAN.md §8: goal, files, acceptance, out-of-scope. **Touch only the listed files** (plus tests you add for them). Out-of-scope items stay out.
2. Write or extend the acceptance test first, register it in `tests/suites.txt` with the milestone number, and confirm it fails for the right reason.
3. Implement. Iterate with the fastest tier first: `make test-unit` → `make test-shell` → guest tests.
4. Done means: the milestone's acceptance command exits 0, **`make test` exits 0** (all suites ≤ `MILESTONE`), and `make budget` is green once M3+ is reached.
5. **Commit after each milestone.** Stage files by name (no `git add -A`). Message format:
   ```
   M<n>: <milestone title>

   Acceptance: <command> -> PASS
   make test: <N> suites PASS, <M> SKIP (reason)
   Budgets: boot <x>s, RAM <y>MiB, procs <z>, ISO <w>GiB
   ```
   Then `git tag m<n>`, bump `MILESTONE` in that same commit, and push.
6. Don't start milestone N+1 until N is committed.

## 7. Flag, don't improvise
**Stop and write an entry in `FLAGS.md`** (then commit with prefix `WIP M<n>:` and stop) when any of these happens:
- You need to change a **protected file**: `PLAN.md`, `CLAUDE.md`, `tests/budgets.toml`, `tests/services.allow`, `tests/protocol.md`, `tests/suites.txt` (except adding your milestone's own suite lines).
- You'd deviate from PLAN.md: a different component, toolkit, bootloader, filesystem, partition layout, language, or a new daemon.
- A hard budget can't be met by trimming or configuration.
- The same acceptance test has failed after **3 distinct fix attempts**.
- Anything touches installer partition planning (`installer/kvibe_install/plan.py`) beyond what the current milestone specifies.
- A milestone marked 🔶 in PLAN.md is about to start or has just finished. Write the review-request entry anyway.

FLAGS.md entry template:
```
## FLAG <date> M<n>: <short title>
- Observed: <what failed / what's in the way, with log excerpt paths>
- Tried: <attempts so far>
- Proposed change: <exact change and which PLAN.md section it alters>
- Impact on budgets/compat: <...>
- Blocking: yes/no
```

## 8. Never
- Never edit a test to match broken behaviour, skip a failing test, add `|| true` to test commands, or raise a budget.
- Never mark a TCG performance result as PASS.
- Never run destructive disk commands outside `build/`.
- Never add Microsoft assets or copy another OS's artwork.
- Never put secrets, tokens, or personal data in the repo or the images.
