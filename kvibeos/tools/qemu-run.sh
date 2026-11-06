#!/usr/bin/env bash
# The ONLY place QEMU command lines are built (CLAUDE.md §3).
# Usage: qemu-run.sh kernel <suite>|none   SeaBIOS + TCG, timeout 5, -d int,cpu_reset
#        qemu-run.sh uefi   <suite>        OVMF + TCG, timeout 20
#        qemu-run.sh release <suite>       release ISO (boot menu, default entry) via OVMF, timeout 25
#        qemu-run.sh shot   <suite>        SeaBIOS + TCG + QMP socket, timeout 30 (tools/screenshot.py)
#        qemu-run.sh interactive           windowed (or VNC :0), KVM if available
#        qemu-run.sh debug  [suite]        paused, gdb stub on :1234
# Tests boot the test ISO; `release` and `interactive` boot the release ISO.
# KVIBE_CMDLINE=...  injects test-only kernel options via fw_cfg opt/kvibe/cmdline (gated test mode).
# Exit status: QEMU's own (33 pass, 35 fail, 124 timeout, 0 reset, ...).
source "$(dirname "$0")/lib/common.sh"

mode="${1:?mode}"; suite="${2:-none}"
[[ -f "$ISO" && -f "$TEST_ISO" ]] || die "$ISO / $TEST_ISO missing (run make iso)"

testcfg=()
[[ "$suite" != "none" ]] && testcfg=(-fw_cfg "name=opt/kvibe/test,string=$suite")
[[ -n "${KVIBE_CMDLINE:-}" ]] && testcfg+=(-fw_cfg "name=opt/kvibe/cmdline,string=$KVIBE_CMDLINE")

serial="$LOGS/serial-$mode-$suite.log"
qlog="$LOGS/qemu-$mode-$suite.log"
rm -f "$serial" "$qlog"

case "$mode" in
  kernel)
    cmd=(timeout 5 qemu-system-x86_64
      -machine q35,accel=tcg -cpu max -m 256M -smp 1
      -cdrom "$TEST_ISO" -boot d
      "${testcfg[@]}"
      -vga std -display none
      -serial "file:$serial"
      -device isa-debug-exit,iobase=0xf4,iosize=0x04
      -no-reboot -d int,cpu_reset -D "$qlog")
    ;;
  uefi)
    vars="$VMDIR/OVMF_VARS-$suite.fd"
    cp "$(ovmf_vars)" "$vars"
    cmd=(timeout 20 qemu-system-x86_64
      -machine q35,accel=tcg -cpu max -m 256M -smp 1
      -drive "if=pflash,format=raw,readonly=on,file=$(ovmf_code)"
      -drive "if=pflash,format=raw,file=$vars"
      -cdrom "$TEST_ISO" -boot d
      "${testcfg[@]}"
      -vga std -display none
      -serial "file:$serial"
      -device isa-debug-exit,iobase=0xf4,iosize=0x04
      -no-reboot -d cpu_reset -D "$qlog")
    ;;
  release)
    vars="$VMDIR/OVMF_VARS-release-$suite.fd"
    cp "$(ovmf_vars)" "$vars"
    cmd=(timeout 25 qemu-system-x86_64
      -machine q35,accel=tcg -cpu max -m 256M -smp 1
      -drive "if=pflash,format=raw,readonly=on,file=$(ovmf_code)"
      -drive "if=pflash,format=raw,file=$vars"
      -cdrom "$ISO" -boot d
      "${testcfg[@]}"
      -vga std -display none
      -serial "file:$serial"
      -device isa-debug-exit,iobase=0xf4,iosize=0x04
      -no-reboot -d cpu_reset -D "$qlog")
    ;;
  shot)
    cmd=(timeout 30 qemu-system-x86_64
      -machine q35,accel=tcg -cpu max -m 256M -smp 1
      -cdrom "$TEST_ISO" -boot d
      "${testcfg[@]}"
      -vga std -display none
      -qmp "unix:$VMDIR/qmp-$suite.sock,server=on,wait=off"
      -serial "file:$serial"
      -device isa-debug-exit,iobase=0xf4,iosize=0x04
      -no-reboot -d cpu_reset -D "$qlog")
    ;;
  interactive)
    accel=tcg; [[ -w /dev/kvm ]] && accel=kvm
    display=(-display gtk); [[ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] && display=(-display none -vnc 127.0.0.1:0)
    cmd=(qemu-system-x86_64 -machine "q35,accel=$accel" -cpu max -m 512M
      -cdrom "$ISO" -boot d -vga std "${display[@]}" -serial stdio
      -device qemu-xhci -device usb-kbd -device usb-tablet)
    ;;
  debug)
    cmd=(qemu-system-x86_64 -machine q35,accel=tcg -cpu max -m 256M -smp 1
      -cdrom "$TEST_ISO" -boot d "${testcfg[@]}" -vga std -display none
      -serial "file:$serial" -device isa-debug-exit,iobase=0xf4,iosize=0x04
      -no-reboot -d int,cpu_reset -D "$qlog" -s -S)
    echo "gdb stub on :1234 — e.g. gdb -batch -ex 'target remote :1234' -ex 'bt' $KERNEL_ELF"
    ;;
  *) die "unknown mode $mode" ;;
esac

printf '%q ' "${cmd[@]}" > "$LOGS/cmd-$mode-$suite.txt"; echo >> "$LOGS/cmd-$mode-$suite.txt"
set +e
"${cmd[@]}"
rc=$?
set -e
exit $rc
