#!/usr/bin/env bash
# Build the hybrid BIOS+UEFI live ISO with Limine.
source "$(dirname "$0")/lib/common.sh"

[[ -f "$KERNEL_ELF" ]] || die "$KERNEL_ELF missing (run make build)"
[[ -x "$LIMINE_DIR/limine" ]] || die "$LIMINE_DIR/limine missing (run make limine)"

root="$BUILD/iso_root"
rm -rf "$root"
mkdir -p "$root/boot/limine" "$root/EFI/BOOT"

# Stripped kernel on the ISO; build/kernel.elf keeps symbols for addr2line.
strip --strip-debug -o "$root/boot/kernel" "$KERNEL_ELF"
cp boot/limine.conf "$root/boot/limine/limine.conf"
cp "$LIMINE_DIR"/limine-bios.sys "$LIMINE_DIR"/limine-bios-cd.bin \
   "$LIMINE_DIR"/limine-uefi-cd.bin "$root/boot/limine/"
cp "$LIMINE_DIR/BOOTX64.EFI" "$root/EFI/BOOT/"

xorriso -as mkisofs -R -r -J \
  -b boot/limine/limine-bios-cd.bin -no-emul-boot -boot-load-size 4 -boot-info-table \
  -hfsplus -apm-block-size 2048 \
  --efi-boot boot/limine/limine-uefi-cd.bin -efi-boot-part --efi-boot-image \
  --protective-msdos-label "$root" -o "$ISO" >"$LOGS/xorriso.log" 2>&1 || die "xorriso failed (see $LOGS/xorriso.log)"
[[ -f "$ISO" ]] || die "xorriso did not produce $ISO"
"$LIMINE_DIR/limine" bios-install "$ISO" >"$LOGS/limine-install.log" 2>&1 || die "limine bios-install failed"
echo "ISO: $ISO ($(stat -c %s "$ISO") bytes)"
