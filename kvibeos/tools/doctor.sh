#!/usr/bin/env bash
# Check host prerequisites. Exit 2 if anything required is missing.
source "$(dirname "$0")/lib/common.sh"
missing=0
need() { if command -v "$1" >/dev/null; then printf 'ok      %-22s %s\n' "$1" "$(command -v "$1")"; else printf 'MISSING %-22s %s\n' "$1" "$2"; missing=1; fi; }
need rustup     "https://rustup.rs"
need cargo      "via rustup"
need qemu-system-x86_64 "apt: qemu-system-x86 | pacman: qemu-full"
need xorriso    "apt/pacman: xorriso"
need mformat    "apt/pacman: mtools"
need fsck.fat   "apt/pacman: dosfstools"
need sgdisk     "apt: gdisk | pacman: gptfdisk"
need git        ""
need cc         "apt: build-essential | pacman: base-devel"
need python3    ""
need addr2line  "binutils"
need strip      "binutils"
if ovmf_code >/dev/null 2>&1; then echo "ok      OVMF                   $(ovmf_code)"; else echo "MISSING OVMF                   apt: ovmf | pacman: edk2-ovmf"; missing=1; fi
[[ -w /dev/kvm ]] && echo "ok      /dev/kvm               (perf budgets enabled)" || echo "note    /dev/kvm               absent: perf budgets will SKIP"
[[ $missing -eq 0 ]] || exit 2
