# KVibeOS — bare-metal Rust OS. See PLAN.md (design) and CLAUDE.md (agent rules).
# Requested core targets: build, run, test, iso. The rest are helpers used by the test loop.

SHELL         := /bin/bash
.SHELLFLAGS   := -eu -o pipefail -c
.DEFAULT_GOAL := help

# --- Pins (M0: replace LIMINE_REF with an exact commit hash of the chosen vN.x-binary branch) ---
LIMINE_REPO   ?= https://github.com/limine-bootloader/limine.git
LIMINE_REF    ?= v9.x-binary

BUILD         := build
LOGS          := $(BUILD)/logs
VMDIR         := $(BUILD)/vm
KERNEL_ELF    := $(BUILD)/kernel.elf
INITRD        := $(BUILD)/initrd.tar
ISO           := $(BUILD)/kvibeos.iso
IMG           := $(BUILD)/kvibeos.img
LIMINE_DIR    := $(BUILD)/limine
SUITE         ?= smoke
SCENARIO      ?= desktop_basic
FIXTURE       ?= fat32-known
CTRL          ?= virtio
PERF          ?= 0
MILESTONE_N   := $(shell cat MILESTONE 2>/dev/null || echo 0)

export BUILD LOGS VMDIR KERNEL_ELF INITRD ISO IMG LIMINE_DIR SUITE SCENARIO FIXTURE CTRL PERF MILESTONE_N

# `need` fails clearly when a milestone's script isn't written yet.
need = @test -x $(1) || { echo "ERROR: $(1) not implemented yet (PLAN.md $(2))" >&2; exit 2; }

dirs:
	@mkdir -p $(LOGS) $(VMDIR) $(BUILD)/test

## doctor: check host tools
doctor:
	$(call need,tools/doctor.sh,M0)
	tools/doctor.sh

## limine: fetch + build the pinned Limine binary release
limine: $(LIMINE_DIR)/limine
$(LIMINE_DIR)/limine:
	rm -rf $(LIMINE_DIR)
	git clone --depth=1 --branch=$(LIMINE_REF) $(LIMINE_REPO) $(LIMINE_DIR) 2>/dev/null \
	  || { git clone $(LIMINE_REPO) $(LIMINE_DIR) && git -C $(LIMINE_DIR) checkout $(LIMINE_REF); }
	$(MAKE) -C $(LIMINE_DIR)

## build: kernel + userspace + initrd (clippy -D warnings)
build: dirs
	cd kernel && cargo clippy --release -- -D warnings && cargo build --release
	cp kernel/target/x86_64-unknown-none/release/kernel $(KERNEL_ELF)
	@if [ -d user ]; then \
	  (cd user && cargo clippy --release -- -D warnings && cargo build --release) && \
	  cargo run --quiet --release --manifest-path tools/xtask/Cargo.toml -- initrd --out $(INITRD); \
	else echo "note: user/ not present yet (arrives in M5)"; fi

## iso: hybrid BIOS+UEFI live ISO -> build/kvibeos.iso
iso: build limine
	$(call need,tools/mkiso.sh,M0)
	tools/mkiso.sh 2>&1 | tee $(LOGS)/mkiso.log

## img: GPT disk image (ESP + KVIBEDATA) -> build/kvibeos.img (+ .qcow2/.vmdk)
img: build limine
	$(call need,tools/mkimg.sh,M14)
	tools/mkimg.sh 2>&1 | tee $(LOGS)/mkimg.log

## run: boot the ISO with a window (GTK/SDL), KVM if available, USB tablet + keyboard
run: iso
	$(call need,tools/qemu-run.sh,M0)
	tools/qemu-run.sh interactive

## debug: boot paused with gdb stub on :1234 (use gdb -batch, see CLAUDE.md §3.1)
debug: iso
	$(call need,tools/qemu-run.sh,M0)
	tools/qemu-run.sh debug

## test: full regression: every suite with milestone <= MILESTONE
test: dirs
	$(call need,tools/test.sh,M0)
	tools/test.sh --upto $(MILESTONE_N)

## test-harness: harness self-test (pass/fail/hang/triple-fault/no-test-mode)
test-harness: iso
	$(call need,tools/test.sh,M0)
	tools/test.sh --tier harness

## test-unit: cargo test over host-testable crates + xtask
test-unit:
	$(call need,tools/test.sh,M0)
	tools/test.sh --tier unit

## test-kernel: one in-kernel suite via SeaBIOS/TCG with timeout 5
test-kernel: iso
	$(call need,tools/test.sh,M0)
	tools/test.sh --tier kernel --suite $(SUITE)

## test-uefi: smoke boot via OVMF (ISO, and disk image once it exists)
test-uefi: iso
	$(call need,tools/test.sh,M0)
	tools/test.sh --tier uefi

## test-e2e: QMP-driven GUI scenario
test-e2e: iso
	$(call need,tools/test.sh,M0)
	tools/test.sh --tier e2e --scenario $(SCENARIO)

## test-storage: storage suite against a fixture disk on a given controller
test-storage: iso
	$(call need,tools/test.sh,M0)
	tools/test.sh --tier storage --fixture $(FIXTURE) --ctrl $(CTRL)

## test-install: installer scenarios (whole disk + alongside fake Windows ESP)
test-install: iso img
	$(call need,tools/test.sh,M0)
	tools/test.sh --tier install

## budget: resource budgets under KVM (SKIP without /dev/kvm)
budget: iso
	$(call need,tools/budget.sh,M9)
	PERF=1 tools/budget.sh | tee $(LOGS)/budget.txt

clean:
	rm -rf $(LOGS) $(VMDIR) $(BUILD)/test $(KERNEL_ELF) $(INITRD) $(ISO) $(IMG)
	cd kernel && cargo clean 2>/dev/null || true
	[ -d user ] && (cd user && cargo clean) || true

distclean: clean
	rm -rf $(BUILD)

help:
	@echo "KVibeOS (milestone $(MILESTONE_N))"
	@echo "  build | iso | img | run | debug | doctor | limine"
	@echo "  test                    full regression up to MILESTONE"
	@echo "  test-harness | test-unit | test-kernel SUITE=x | test-uefi"
	@echo "  test-e2e SCENARIO=x | test-storage FIXTURE=x CTRL=virtio|ahci|nvme | test-install"
	@echo "  budget (KVM) | clean | distclean"

.PHONY: dirs doctor limine build iso img run debug test test-harness test-unit test-kernel \
        test-uefi test-e2e test-storage test-install budget clean distclean help
