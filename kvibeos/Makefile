# KVibeOS — top-level entry points. See PLAN.md (design) and CLAUDE.md (agent rules).
# Every real target runs inside the kvibe-builder container. Outside it, targets re-exec there.

SHELL         := /bin/bash
.SHELLFLAGS   := -eu -o pipefail -c
.DEFAULT_GOAL := help

ARCH_SNAPSHOT ?= 2026/11/01
BUILDER_IMAGE ?= kvibe-builder:latest
BUILD         ?= build
OUT           := $(BUILD)/out
LOGS          := $(BUILD)/logs
VMDIR         := $(BUILD)/vm
ISO           := $(OUT)/kvibeos.iso
QCOW          := $(VMDIR)/kvibeos.qcow2
SUITE         ?= smoke
FIXTURE       ?= blank
VARIANT       ?= pass
MILESTONE_N   := $(shell cat MILESTONE 2>/dev/null || echo 0)

export ARCH_SNAPSHOT BUILD OUT LOGS VMDIR ISO QCOW SUITE FIXTURE VARIANT MILESTONE_N

# ---------------------------------------------------------------------------
# Builder re-exec: `make X` on the host == `make X` inside the container.
# ---------------------------------------------------------------------------
ifneq ($(KVIBE_IN_BUILDER),1)
KVM_FLAG := $(if $(wildcard /dev/kvm),--device /dev/kvm,)
DOCKER_RUN := docker run --rm --privileged $(KVM_FLAG) \
	-v $(CURDIR):/src -w /src \
	-v kvibe-pacman-cache:/var/cache/pacman/pkg \
	-v kvibe-cargo:/root/.cargo/registry \
	-e KVIBE_IN_BUILDER=1 -e ARCH_SNAPSHOT=$(ARCH_SNAPSHOT) \
	-e SUITE=$(SUITE) -e FIXTURE=$(FIXTURE) -e VARIANT=$(VARIANT)

INNER_TARGETS := build iso vmimage test test-harness test-unit test-shell test-iso \
                 test-disk test-install test-dualboot test-net budget debug-boot release

$(INNER_TARGETS): builder
	$(DOCKER_RUN) $(BUILDER_IMAGE) make $@

run run-disk: builder
	$(DOCKER_RUN) -p 127.0.0.1:5900:5900 $(BUILDER_IMAGE) make $@

builder:
	docker build -t $(BUILDER_IMAGE) --build-arg ARCH_SNAPSHOT=$(ARCH_SNAPSHOT) tools/builder

else
# ---------------------------------------------------------------------------
# Inside the builder.
# `need` fails clearly if a milestone's script doesn't exist yet.
# ---------------------------------------------------------------------------
need = @test -x $(1) || { echo "ERROR: $(1) not implemented yet (see PLAN.md $(2))" >&2; exit 2; }

builder:
	@true

dirs:
	@mkdir -p $(OUT) $(LOGS) $(VMDIR) $(BUILD)/test $(BUILD)/repo

## build: compile Rust workspace + build [kvibe] pacman packages
build: dirs
	$(call need,tools/build-shell.sh,M5)
	tools/build-shell.sh 2>&1 | tee $(LOGS)/build-shell.log
	$(call need,tools/build-pkgs.sh,M1)
	tools/build-pkgs.sh 2>&1 | tee $(LOGS)/build-pkgs.log

## iso: build the UEFI live ISO -> build/out/kvibeos.iso
iso: build
	$(call need,tools/build-iso.sh,M1)
	tools/build-iso.sh 2>&1 | tee $(LOGS)/build-iso.log

## vmimage: install the ISO into a qcow2 via the installer -> build/vm/kvibeos.qcow2 (+ .vmdk)
vmimage: iso
	$(call need,tools/make-vmimage.sh,M2)
	tools/make-vmimage.sh 2>&1 | tee $(LOGS)/build-vmimage.log

## run: boot the live ISO interactively (VNC on 127.0.0.1:5900)
run: iso
	$(call need,tools/qemu-run.sh,M0)
	tools/qemu-run.sh interactive $(ISO)

## run-disk: boot the installed VM image interactively (VNC on 127.0.0.1:5900)
run-disk: vmimage
	$(call need,tools/qemu-run.sh,M0)
	tools/qemu-run.sh interactive-disk $(QCOW)

## test: run every suite registered for milestones <= MILESTONE (full regression)
test: dirs
	$(call need,tools/test.sh,M0)
	tools/test.sh --upto $(MILESTONE_N)

## test-harness: QEMU harness self-test (multiboot stub, timeout 5, -d int,cpu_reset)
test-harness: dirs
	$(call need,tools/test.sh,M0)
	tools/test.sh --suite harness

test-unit: dirs
	$(call need,tools/test.sh,M0)
	tools/test.sh --tier unit

test-shell: dirs
	$(call need,tools/test.sh,M0)
	tools/test.sh --tier shell

test-iso: iso
	$(call need,tools/test.sh,M0)
	tools/test.sh --tier iso --suite $(SUITE)

test-disk: vmimage
	$(call need,tools/test.sh,M0)
	tools/test.sh --tier disk --suite $(SUITE)

test-install: iso
	$(call need,tools/test.sh,M0)
	tools/test.sh --tier install --fixture $(FIXTURE)

test-dualboot: iso
	$(call need,tools/test.sh,M0)
	tools/test.sh --tier install --fixture windows-like --suite install-alongside

## test-net: opt-in tests that need the internet (Steam bootstrap, Flathub, NVIDIA)
test-net: vmimage
	$(call need,tools/test.sh,M0)
	tools/test.sh --tier net

## budget: measure boot/RAM/CPU/procs/size against tests/budgets.toml
budget: vmimage
	$(call need,tools/budget.sh,M3)
	tools/budget.sh | tee $(LOGS)/budget.txt

## debug-boot: TCG boot of the ISO kernel with -d int,cpu_reset (diagnosis only)
debug-boot: iso
	$(call need,tools/qemu-run.sh,M0)
	tools/qemu-run.sh debug $(ISO)

release: dirs
	$(call need,tools/release.sh,M14)
	tools/release.sh

endif

# ---------------------------------------------------------------------------
# Host-or-builder targets
# ---------------------------------------------------------------------------
clean:
	rm -rf $(BUILD)/work $(BUILD)/logs $(BUILD)/test $(BUILD)/harness

distclean:
	rm -rf $(BUILD)

help:
	@echo "KVibeOS make targets (milestone $(MILESTONE_N)):"
	@echo "  build        compile shell + [kvibe] packages"
	@echo "  iso          build live ISO -> $(ISO)"
	@echo "  vmimage      build installed qcow2 -> $(QCOW)"
	@echo "  run          boot ISO (VNC :5900)        run-disk  boot qcow2 (VNC :5900)"
	@echo "  test         full regression up to MILESTONE"
	@echo "  test-harness | test-unit | test-shell | test-iso SUITE=x | test-disk SUITE=x"
	@echo "  test-install FIXTURE=x | test-dualboot | test-net | budget | debug-boot"
	@echo "  builder | release | clean | distclean"

.PHONY: builder dirs build iso vmimage run run-disk test test-harness test-unit test-shell \
        test-iso test-disk test-install test-dualboot test-net budget debug-boot release \
        clean distclean help
