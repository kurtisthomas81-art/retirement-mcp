# Verdict classification for one QEMU run (tests/protocol.md). Source, don't execute.
#
# classify <exit-code> <serial-log> <qemu-log>  -> prints one verdict:
#   PASS | FAIL | NO_RESULT | TIMEOUT | TRIPLE_FAULT | POWEROFF | RESET | ERROR
classify() {
  local rc="$1" serial="$2" qlog="$3"
  case "$rc" in
    33)
      if grep -q '^KVIBE-RESULT PASS' "$serial" 2>/dev/null; then echo PASS; else echo NO_RESULT; fi ;;
    35)  echo FAIL ;;
    124) echo TIMEOUT ;;
    0)
      if grep -q 'Triple fault' "$qlog" 2>/dev/null; then echo TRIPLE_FAULT
      elif grep -q '^KVIBE-POWEROFF' "$serial" 2>/dev/null; then echo POWEROFF
      else echo RESET; fi ;;
    *)   echo ERROR ;;
  esac
}

# check_assertions <assertions> <serial-log> <qemu-log>
# assertions: ';'-separated list of  has=TEXT | lacks=TEXT | qlog=TEXT
# Prints failing assertions (one per line); returns 1 if any failed.
check_assertions() {
  local spec="$1" serial="$2" qlog="$3" failed=0 a key val
  local IFS=';'
  for a in $spec; do
    a="${a#"${a%%[![:space:]]*}"}"; a="${a%"${a##*[![:space:]]}"}"
    [[ -z "$a" ]] && continue
    key="${a%%=*}"; val="${a#*=}"
    case "$key" in
      has)   grep -qF -- "$val" "$serial" 2>/dev/null || { echo "missing in serial: $val"; failed=1; } ;;
      lacks) if grep -qF -- "$val" "$serial" 2>/dev/null; then echo "unexpected in serial: $val"; failed=1; fi ;;
      qlog)  grep -qF -- "$val" "$qlog" 2>/dev/null || { echo "missing in qemu log: $val"; failed=1; } ;;
      *)     echo "unknown assertion: $a"; failed=1 ;;
    esac
  done
  return "$failed"
}
