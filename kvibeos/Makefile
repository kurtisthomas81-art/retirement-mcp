# KVibeOS — bare-metal Rust OS. See PLAN.md (design) and CLAUDE.md (agent rules).
# Core targets: build, run, test, iso. Milestone completion: make gate.

SHELL         := /bin/bash
.SHELLFLAGS   := -eu -o pipefail -c
.DEFAULT_GOAL := help

# --- Pins -------------------------------------------------------------------
# Limine v11.4.1 (newest release line that ships a prebuilt -binary branch).
LIMINE_REPO   ?= https://github.com/limine-bootloader/limine.git
LIMINE_BRANCH ?= v11.x-binary
LIMINE_COMMIT ?= 5be26a73d7b7b4d4477d18be94e1d16e615adf56

BUILD         := build
LOGS          := $(BUILD)/logs
VMDIR         := $(BUILD)/vm
KERNEL_ELF    := $(BUILD)/kernel.elf
INITRD        := $(BUILD)/initrd.tar
ISO           := $(BUILD)/kvibeos.iso
TEST_ISO      := $(BUILD)/kvibeos-test.iso
IMG           := $(BUILD)/kvibeos.img
LIMINE_DIR    := $(BUILD)/limine
SUITE         ?= smoke
SCENARIO      ?= desktop_basic
FIXTURE       ?= fat32-known
CTRL          ?= virtio
# MILESTONE = last completed milestone (-1 before M0). `make test` covers it plus the one in progress.
MILESTONE_N   := $(shell cat MILESTONE 2>/dev/null || echo -1)
CURRENT_N     := $(shell echo $$(( $(MILESTONE_N) + 1 )))
M             ?= $(CURRENT_N)

export BUILD LOGS VMDIR KERNEL_ELF INITRD ISO TEST_ISO IMG LIMINE_DIR

need = @test -x $(1) || { echo "ERROR: $(1) not implemented yet (PLAN.md $(2))" >&2; exit 2; }

dirs:
	@mkdir -p $(LOGS) $(VMDIR) $(BUILD)/test

## doctor: check host tools
doctor:
	tools/doctor.sh

## hooks: install the commit gate hook (required by make gate)
hooks:
	git config core.hooksPath "$$(git rev-parse --show-prefix).githooks"
	@echo "core.hooksPath = $$(git config core.hooksPath)"

## limine: fetch + build the pinned Limine binary release
limine: $(LIMINE_DIR)/.pinned-$(LIMINE_COMMIT)
$(LIMINE_DIR)/.pinned-$(LIMINE_COMMIT):
	rm -rf $(LIMINE_DIR)
	git clone --quiet --branch=$(LIMINE_BRANCH) $(LIMINE_REPO) $(LIMINE_DIR)
	git -C $(LIMINE_DIR) -c advice.detachedHead=false checkout --quiet $(LIMINE_COMMIT)
	$(MAKE) --no-print-directory -C $(LIMINE_DIR) >/dev/null
	touch $@

## build: kernel (+ userspace/initrd from M5), clippy -D warnings
build: dirs
	cd kernel && cargo clippy --release --quiet -- -D warnings && cargo build --release --quiet
	cp kernel/target/x86_64-unknown-none/release/kernel $(KERNEL_ELF)
	@if [ -d user ]; then \
	  (cd user && cargo clippy --release --quiet -- -D warnings && cargo build --release --quiet) && \
	  cargo run --quiet --release --manifest-path tools/xtask/Cargo.toml -- initrd --out $(INITRD); \
	fi

## iso: hybrid BIOS+UEFI live ISO -> build/kvibeos.iso
iso: build limine
	tools/mkiso.sh

## img: GPT disk image (ESP + KVIBEDATA) -> build/kvibeos.img
img: build limine
	$(call need,tools/mkimg.sh,M14)
	tools/mkimg.sh

## run: boot the ISO in a window (VNC 127.0.0.1:5900 if no display)
run: iso
	tools/qemu-run.sh interactive

## debug: boot paused with gdb stub on :1234
debug: iso
	tools/qemu-run.sh debug $(SUITE)

## test: every suite up to the milestone in progress
test: iso
	tools/test.sh --upto $(CURRENT_N) --fresh

test-harness: iso
	tools/test.sh --tier harness

test-unit:
	tools/test.sh --tier unit --upto $(CURRENT_N)

test-kernel: iso
	tools/test.sh --tier kernel --suite $(SUITE)

test-uefi: iso
	tools/test.sh --tier uefi --upto $(CURRENT_N)

test-e2e: iso
	tools/test.sh --tier e2e --suite $(SCENARIO)

test-storage: iso
	tools/test.sh --tier storage --suite $(CTRL)

test-install: iso img
	tools/test.sh --tier install --upto $(CURRENT_N)

## budget: resource budgets (KVM; SKIP without /dev/kvm)
budget: iso
	$(call need,tools/budget.sh,M9)
	tools/budget.sh

## gate: ALL checks for milestone M (default: next). Must pass before an "M<n>:" commit.
gate: dirs
	tools/gate.sh $(M)

## verify: re-check the last completed milestone from scratch (what CI runs)
verify: dirs
	tools/gate.sh --verify

## screenshot: boot SUITE and save build/logs/screens/<SUITE>.png (the agent can open it)
screenshot: iso
	tools/screenshot.py --suite "$(SUITE)" --out $(LOGS)/screens/$(SUITE).png

clean:
	rm -rf $(LOGS) $(VMDIR) $(BUILD)/test $(BUILD)/iso_root $(KERNEL_ELF) $(INITRD) $(ISO) $(TEST_ISO) $(IMG)
	cd kernel && cargo clean --quiet
	if [ -d user ]; then cd user && cargo clean --quiet; fi

distclean: clean
	rm -rf $(BUILD)

help:
	@echo "KVibeOS — completed milestone: $(MILESTONE_N), in progress: M$(CURRENT_N)"
	@echo "  build | iso | img | run | debug | doctor | hooks | limine"
	@echo "  test                    every suite up to M$(CURRENT_N)"
	@echo "  gate [M=n]              all milestone checks; required before an 'M<n>:' commit"
	@echo "  verify                  re-check the last completed milestone (CI)"
	@echo "  screenshot SUITE=x      PNG of the screen when SUITE prints KVIBE-SCREEN-READY"
	@echo "  test-harness | test-unit | test-kernel SUITE=x | test-uefi"
	@echo "  test-e2e SCENARIO=x | test-storage CTRL=virtio|ahci|nvme | test-install"
	@echo "  budget (KVM) | clean | distclean"

.PHONY: dirs doctor hooks limine build iso img run debug test test-harness test-unit test-kernel \
        test-uefi test-e2e test-storage test-install budget gate verify screenshot clean distclean help
