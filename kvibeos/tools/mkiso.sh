#!/usr/bin/env bash
# Build the hybrid BIOS+UEFI live ISO with Limine.
source "$(dirname "$0")/lib/common.sh"

[[ -f "$KERNEL_ELF" ]] || die "$KERNEL_ELF missing (run make build)"
[[ -x "$LIMINE_DIR/limine" ]] || die "$LIMINE_DIR/limine missing (run make limine)"

# Symbol map module for in-kernel panic symbolization: "<addr-hex> <name>" per line, sorted.
nm --defined-only -C "$KERNEL_ELF" | awk '$2 ~ /^[tTwW]$/ {print $1, $3}' | sort > "$BUILD/kernel.sym"

# build_iso <limine.conf> <output.iso>
build_iso() {
  local conf="$1" out="$2" root="$BUILD/iso_root"
  rm -rf "$root"
  mkdir -p "$root/boot/limine" "$root/EFI/BOOT"
  # Stripped kernel on the ISO; build/kernel.elf keeps symbols for addr2line.
  strip --strip-debug -o "$root/boot/kernel" "$KERNEL_ELF"
  cp "$BUILD/kernel.sym" "$root/boot/kernel.sym"
  cp "$conf" "$root/boot/limine/limine.conf"
  cp THIRD_PARTY_NOTICES.md "$root/"
  cp "$LIMINE_DIR"/limine-bios.sys "$LIMINE_DIR"/limine-bios-cd.bin \
     "$LIMINE_DIR"/limine-uefi-cd.bin "$root/boot/limine/"
  cp "$LIMINE_DIR/BOOTX64.EFI" "$root/EFI/BOOT/"
  rm -f "$out"
  xorriso -as mkisofs -R -r -J \
    -b boot/limine/limine-bios-cd.bin -no-emul-boot -boot-load-size 4 -boot-info-table \
    -hfsplus -apm-block-size 2048 \
    --efi-boot boot/limine/limine-uefi-cd.bin -efi-boot-part --efi-boot-image \
    --protective-msdos-label "$root" -o "$out" >"$LOGS/xorriso.log" 2>&1 || die "xorriso failed (see $LOGS/xorriso.log)"
  [[ -f "$out" ]] || die "xorriso did not produce $out"
  "$LIMINE_DIR/limine" bios-install "$out" >"$LOGS/limine-install.log" 2>&1 || die "limine bios-install failed"
  echo "ISO: $out ($(stat -c %s "$out") bytes)"
}

build_iso boot/limine.conf "$ISO"
build_iso boot/limine-test.conf "$TEST_ISO"
