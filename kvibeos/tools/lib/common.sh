# Shared helpers for KVibeOS tools. Source, don't execute.
set -euo pipefail
KV_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD="${BUILD:-build}"
LOGS="${LOGS:-$BUILD/logs}"
VMDIR="${VMDIR:-$BUILD/vm}"
ISO="${ISO:-$BUILD/kvibeos.iso}"            # release ISO (boot menu)
TEST_ISO="${TEST_ISO:-$BUILD/kvibeos-test.iso}" # test ISO (no menu delay)
KERNEL_ELF="${KERNEL_ELF:-$BUILD/kernel.elf}"
LIMINE_DIR="${LIMINE_DIR:-$BUILD/limine}"
cd "$KV_ROOT"
mkdir -p "$LOGS" "$VMDIR"

die() { echo "ERROR: $*" >&2; exit 2; }

# First existing file from a list of candidates.
first_file() { for f in "$@"; do [[ -f "$f" ]] && { echo "$f"; return 0; }; done; return 1; }

ovmf_code() {
  first_file /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/edk2/x64/OVMF_CODE.4m.fd \
             /usr/share/OVMF/OVMF_CODE.fd /usr/share/edk2-ovmf/x64/OVMF_CODE.fd \
    || die "OVMF_CODE not found (install ovmf / edk2-ovmf)"
}
ovmf_vars() {
  first_file /usr/share/OVMF/OVMF_VARS_4M.fd /usr/share/edk2/x64/OVMF_VARS.4m.fd \
             /usr/share/OVMF/OVMF_VARS.fd /usr/share/edk2-ovmf/x64/OVMF_VARS.fd \
    || die "OVMF_VARS not found (install ovmf / edk2-ovmf)"
}
