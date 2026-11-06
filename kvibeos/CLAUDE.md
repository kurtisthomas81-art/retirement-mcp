# CLAUDE.md — KVibeOS build agent

You are building KVibeOS, a bare-metal x86-64 OS in Rust, milestone by milestone. **M0 is done. Start at M1.**
**PLAN.md is the source of truth. Follow it. Flag design changes. Don't improvise them.**
Nobody reads your code. The tests are the only reviewer, so never weaken them.

## 1. Environment rules
1. **`no_std` everywhere except host tooling and tests.** `kernel/`, `user/`, and `crates/*` are `#![no_std]`. Crates use `#![cfg_attr(not(test), no_std)]` so `cargo test` runs on the host. Only `tools/xtask` and `tests/e2e` may use `std`/Python.
2. **Stable Rust**, pinned by `rust-toolchain.toml`. Kernel and user target: `x86_64-unknown-none` (soft-float, no red zone). No nightly features, no `build-std`, no custom target JSON. If you think you need one, flag it.
3. **Dependencies** only from the PLAN.md §4.4 allowlist, pinned with `=` versions. `limine` stays at `=0.5.0` (0.6.x needs nightly). Anything else needs a flag.
4. **`unsafe`** only in `kernel/src/{arch,mm,drivers,syscall}` and `user/libkv/src/sys.rs` (the gate enforces this). Every `unsafe` block gets a `// SAFETY:` comment. Use `#![deny(unsafe_op_in_unsafe_fn)]`, and run clippy with `-D warnings` in `make build`.
5. **Kernel builds with frame pointers** (`-C force-frame-pointers=yes`) so the watchdog and panic backtraces work. Keep `build/kernel.elf` unstripped for `addr2line`. Strip only the copy that goes on the ISO.
6. **Logic goes in `crates/`** (host-testable). The kernel and desktop stay thin. Write the host unit test first.
7. **No polling, no busy waits** outside early boot calibration. Idle means `hlt`. The desktop blocks on its IPC channel.
8. **Never touch host block devices.** Disk work happens only on image files under `build/`. Never `dd` to `/dev/*`.
9. **Test hooks** (isa-debug-exit, fw_cfg, watchdog) must stay gated as in PLAN.md §7. Never loosen the gate.
10. **No Microsoft assets.** Windows-*style* layout only. Fonts must be OFL. Art must be original. Every bundled font, asset or crate gets a line in `THIRD_PARTY_NOTICES.md` in the same commit.
11. **Framebuffer: never read it.** Compose into a RAM back buffer and copy only damaged rectangles to the screen (PLAN.md §6.1). Use the write-only front-buffer type in `kv-gfx`.
12. **Hardware safety features stay on:** NX, W^X, SMEP and SMAP once their milestones land. User memory is touched only through the copy routines.
13. **Keyboard: US QWERTY only.** Don't add layout switching (parked).
14. **Parsers of untrusted bytes** (ELF, USTAR, FAT32, GPT, IPC, cmdline, symbol map) get `proptest` property tests, written as dev-dependencies.
15. **Respect safe mode:** every risky driver (xHCI, AHCI, NVMe, USB storage, ACPI AML) checks `kvibe.safe` and skips itself with `KVIBE-INFO <driver>=skipped`.
16. **No project license (owner decision).** Don't add a LICENSE file or a `license` field to any Cargo.toml. Third-party components keep their own licenses in `THIRD_PARTY_NOTICES.md`.

## 2. Host prerequisites (`make doctor` checks them)
`rustup`, `qemu-system-x86_64` (≥ 8.0), OVMF/edk2, `xorriso`, `mtools`, `dosfstools` (`fsck.fat`), `gptfdisk` (`sgdisk`), `git`, `cc`, `python3`, `binutils` (`addr2line`, `nm`, `strip`), `cargo-mutants` and `cargo-llvm-cov` (gate test-quality checks), and later `virt-fw-vars` (pip `virt-firmware`, M14). Limine is fetched into `build/limine` at the pinned ref by `make limine`.

## 3. QEMU commands (`tools/qemu-run.sh` must produce exactly these)

### 3.1 Kernel test suite (fast loop; `make test-kernel SUITE=<suite>`)
SeaBIOS + TCG, deterministic, works without KVM. `-d int` logs only under TCG, which is why KVM is not used here.
```
timeout 5 qemu-system-x86_64 \
  -machine q35,accel=tcg -cpu max -m 256M -smp 1 \
  -cdrom build/kvibeos.iso -boot d \
  -fw_cfg name=opt/kvibe/test,string=$(SUITE) \
  -vga std -display none \
  -serial file:build/logs/serial-kernel-$(SUITE).log \
  -device isa-debug-exit,iobase=0xf4,iosize=0x04 \
  -no-reboot -d int,cpu_reset -D build/logs/qemu-kernel-$(SUITE).log
```
Variants, built only by `qemu-run.sh` flags and otherwise identical:
- **UEFI** (`make test-uefi`): add `-drive if=pflash,format=raw,readonly=on,file=$(OVMF_CODE) -drive if=pflash,format=raw,file=build/vm/OVMF_VARS.fd` and use `timeout 20`.
- **E2E** (`make test-e2e`): add `-qmp unix:build/vm/qmp.sock,server=on,wait=off -device qemu-xhci -device usb-kbd -device usb-tablet`, use `timeout 30`, and the Python driver tails the serial log.
- **Storage**: add `-drive file=build/test/<fixture>.img,format=raw,if=none,id=d0` plus one of `-device virtio-blk-pci,drive=d0` | `-device ich9-ahci,id=ahci -device ide-hd,drive=d0,bus=ahci.0` | `-device nvme,drive=d0,serial=kv0`. Use `timeout 10`.
- **Perf** (`PERF=1`, budgets only): `accel=kvm`, drop `-d int` (it's a no-op under KVM), keep `-d cpu_reset`.
- **Release** (`release` tier): boots the *release* ISO (boot menu, default entry) via OVMF, `timeout 25`. All other test modes boot `build/kvibeos-test.iso`.
- **Shot** (`make screenshot SUITE=x`): adds a QMP socket, `timeout 30`. Waits for `KVIBE-SCREEN-READY` on serial, then saves `build/logs/screens/<suite>.png`. **Open that PNG to check your UI work visually.**
- **Test-only kernel options:** the sixth column of `tests/suites.txt` (or `KVIBE_CMDLINE=...`) is passed as fw_cfg `opt/kvibe/cmdline`. The kernel honours it only in gated test mode.
- **Debug** (`make debug`): add `-s -S`, no timeout. Get a backtrace non-interactively with `gdb -batch -ex 'target remote :1234' -ex 'bt' build/kernel.elf`.

### 3.2 Exit-code mapping (full spec: `tests/protocol.md`)
| Exit | Verdict |
|---|---|
| 33 | PASS (also requires a `KVIBE-RESULT PASS` serial line) |
| 35 | FAIL (assertion, test-mode panic, or watchdog; backtrace is on serial) |
| 124 | TIMEOUT (hang with interrupts off): read the tail of `qemu-<mode>-<suite>.log` |
| 0 | UNEXPECTED_RESET (triple fault): find the `cpu_reset` line and the last exception before it in the `-d int` log |
| other | ERROR (infra) |

Runner exits: 0 pass, 1 fail, 2 infra error, 77 skip. **Without KVM, perf budgets are SKIP, never PASS.**

## 4. Debugging order when something fails
1. `build/logs/serial-<mode>-<suite>.log` (mode = kernel or uefi): `KVIBE-TEST` lines, panic message, backtrace.
2. Symbolize: `addr2line -f -C -e build/kernel.elf <addr>...` (the runner does this automatically for backtrace lines).
3. `build/logs/qemu-<mode>-<suite>.log`: last `v=` interrupt vectors and `cpu_reset` dumps (`check_exception old: ... new 0xe` = page fault, `0x8` = double fault).
4. `build/logs/screen-<scenario>.png` for E2E failures (QMP screendump on failure).
5. `make debug SUITE=<suite>` + `gdb -batch` for hangs.

## 5. Workflow per milestone
0. Once per clone: `make doctor && make hooks`.
1. Read the milestone in PLAN.md §9, PLAN.md §11, **its checklist `tests/gates/m<N>.txt`**, and the previous milestone's journal `docs/progress/m<N-1>.md`. Every ID in the checklist must end up as `KVIBE-TEST <id> PASS` in `build/logs/results.txt`, and every `shot:` line must produce a non-blank screenshot. **Touch only files matching the checklist's `scope:` lines** (the gate enforces this). Out-of-scope items stay out.
2. Write the tests first. Register suites in `tests/suites.txt` (rows for M<N> only) and emit exactly the checklist IDs. Confirm they fail for the right reason.
3. Implement with the fastest tier first: `make test-unit` → `make test-kernel SUITE=x` → `make test-e2e`. `make test` runs everything up to the milestone in progress. For UI work, `make screenshot SUITE=x` and look at the PNG.
   Host crates need **≥ 80% line coverage and ≥ 80% mutation score** (`cargo llvm-cov`, `cargo mutants`). Surviving mutants in `build/logs/mutants-<crate>/mutants.out/missed.txt` show exactly which assertions are missing.
   Host check scripts in `tests/unit/` report individual checks by printing `KVIBE-TEST host/<check> PASS|FAIL <detail>`.
3b. Write `docs/progress/m<N>.md` with `## Summary`, `## Decisions`, `## Known issues` and `## Next`. The next session (possibly you, with no memory of this one) starts from it.
4. **Done means `make gate` passes.** It runs every check in PLAN.md §9 "Milestone gate" and bumps `MILESTONE` itself. Don't edit `MILESTONE` by hand. If the gate fails, read `build/logs/gate-m<N>.md`, fix, and re-run. Never commit an `M<N>:` subject without a passing gate (the hook refuses it anyway).
5. **Commit immediately after the gate passes, with no edits in between.** Stage every file the gate saw, by name. Message:
   ```
   M<n>: <milestone title>

   Acceptance: <command> -> PASS
   make test: <N> suites PASS, <M> SKIP (<reason>)
   Budgets: boot <x>ms, RAM <y>MiB, idle <z>%, ISO <w>MiB   (from M9)
   ```
   Paste the gate summary line into the body, then push.
6. Don't start milestone N+1 until N is committed.

## 6. Flag, don't improvise
**Stop, add an entry to `FLAGS.md`, commit with prefix `WIP M<n>:`, and stop working** when any of these happens:
- A change is needed to a **protected file**: `PLAN.md`, `CLAUDE.md`, `tests/protocol.md`, `tests/budgets.toml`, `tests/gates/*` and `tests/suites.txt` (appending your own milestone's lines is allowed), `tools/gate.sh`, `tools/test.sh`, `tools/qemu-run.sh`, `tools/screenshot.py`, `tools/lib/{classify,quality}.sh`, `tests/unit/classifier.sh`, `.githooks/`, `.github/`. **New test tiers or QEMU options** (e2e before M7, budget before M9, storage before M11, install before M14) are requests: put the exact proposed change in the flag, and the reviewer adds it.
- You'd deviate from PLAN.md: a new dependency, nightly Rust, a different bootloader, a design that moves logic out of `crates/`, a new background process, or work the milestone marks out-of-scope.
- A hard budget can't be met.
- The same acceptance test has failed after **3 distinct fix attempts**.
- Any change to FAT write paths or installer disk writes outside what the milestone specifies.
- A milestone marked 🔶 is about to start or has just finished. Write the review-request entry.
- The gate fails on a check you believe is wrong. Never work around the gate or hook (`--no-verify`, editing `tools/gate.sh`, unsetting `core.hooksPath`).

Template:
```
## FLAG <date> M<n>: <title>
- Observed: <failure + log paths/excerpts>
- Tried: <attempts>
- Proposed change: <exact change, PLAN.md section affected>
- Budget/compat impact: <...>
- Blocking: yes/no
```

## 7. Never
- Never use `git commit --no-verify` or change `core.hooksPath`.
- Never edit a test to match broken behaviour, delete or skip a failing test, add `|| true` to test commands, or loosen a budget or tolerance.
- Never replace `timeout 5` with a longer timeout for `test-kernel`. If a suite is too slow, split it or flag it.
- Never mark a TCG performance number as PASS.
- Never write to disks outside `build/`.
- Never commit secrets, personal data, or non-OFL/non-original assets.
- Never read back from the framebuffer.
