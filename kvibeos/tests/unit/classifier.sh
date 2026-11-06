#!/usr/bin/env bash
# Unit test for tools/lib/classify.sh: every exit-code/log combination maps
# to the verdict tests/protocol.md specifies.
set -euo pipefail
source "$(dirname "$0")/../../tools/lib/classify.sh"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
s="$tmp/serial"; q="$tmp/qlog"
fails=0
expect() { # expect <want> <rc> <serial-text> <qlog-text>
  printf '%b' "$3" > "$s"; printf '%b' "$4" > "$q"
  local got; got="$(classify "$2" "$s" "$q")"
  if [[ "$got" == "$1" ]]; then echo "ok   rc=$2 -> $got"; else echo "FAIL rc=$2 want=$1 got=$got"; fails=$((fails+1)); fi
}
expect PASS         33  'KVIBE-RESULT PASS\n'   ''
expect NO_RESULT    33  'KVIBE-TEST x/y PASS\n' ''
expect NO_RESULT    33  '  KVIBE-RESULT PASS\n' ''          # must be at line start
expect FAIL         35  'KVIBE-RESULT FAIL\n'   ''
expect FAIL         35  ''                      ''
expect TIMEOUT      124 'KVIBE-BOOT OK\n'       ''
expect TRIPLE_FAULT 0   ''                      'CPU Reset (CPU 0)\nTriple fault\n'
expect POWEROFF     0   'KVIBE-POWEROFF\n'      'CPU Reset (CPU 0)\n'
expect RESET        0   ''                      'CPU Reset (CPU 0)\n'
expect ERROR        1   ''                      ''
expect ERROR        137 ''                      ''

# assertions
printf 'KVIBE-BOOT OK\nhello world\n' > "$s"; printf 'Triple fault\n' > "$q"
check_assertions 'has=KVIBE-BOOT OK; lacks=KVIBE-TEST; qlog=Triple fault' "$s" "$q" >/dev/null \
  && echo "ok   assertions pass" || { echo "FAIL assertions should pass"; fails=$((fails+1)); }
if check_assertions 'has=missing text' "$s" "$q" >/dev/null; then echo "FAIL has= should fail"; fails=$((fails+1)); else echo "ok   has= detects missing"; fi
if check_assertions 'lacks=hello' "$s" "$q" >/dev/null; then echo "FAIL lacks= should fail"; fails=$((fails+1)); else echo "ok   lacks= detects presence"; fi
if check_assertions 'bogus=1' "$s" "$q" >/dev/null; then echo "FAIL unknown key should fail"; fails=$((fails+1)); else echo "ok   unknown assertion rejected"; fi

[[ $fails -eq 0 ]] || { echo "classifier: $fails failure(s)"; exit 1; }
echo "classifier: all cases pass"
