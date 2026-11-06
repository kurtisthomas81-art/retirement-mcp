#!/usr/bin/env bash
# KVibeOS suite runner.
#   test.sh --upto N            every suite with milestone <= N
#   test.sh --milestone N       only suites of milestone N
#   test.sh --tier T [--suite S]
#   options: --repeat K (run the selection K times), --fresh (clear results.txt)
# Writes build/logs/results.txt. Exit: 0 pass, 1 fail, 2 infra error.
source "$(dirname "$0")/lib/common.sh"
source "$(dirname "$0")/lib/classify.sh"

upto=""; only_ms=""; tier=""; suite=""; repeat=1; fresh=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --upto) upto="$2"; shift 2 ;;
    --milestone) only_ms="$2"; shift 2 ;;
    --tier) tier="$2"; shift 2 ;;
    --suite) suite="$2"; shift 2 ;;
    --repeat) repeat="$2"; shift 2 ;;
    --fresh) fresh=1; shift ;;
    *) die "unknown argument $1" ;;
  esac
done

results="$LOGS/results.txt"
[[ $fresh -eq 1 ]] && : > "$results"
touch "$results"

trim() { local s="$1"; s="${s#"${s%%[![:space:]]*}"}"; echo "${s%"${s##*[![:space:]]}"}"; }

# Collect selected registry rows.
rows=()
while IFS= read -r line; do
  [[ -z "$(trim "$line")" || "$(trim "$line")" == \#* ]] && continue
  IFS='|' read -r ms t s exp asserts <<< "$line"
  ms="$(trim "$ms")"; t="$(trim "$t")"; s="$(trim "$s")"; exp="$(trim "$exp")"; asserts="$(trim "${asserts:-}")"
  [[ -n "$upto" && "$ms" -gt "$upto" ]] && continue
  [[ -n "$only_ms" && "$ms" -ne "$only_ms" ]] && continue
  [[ -n "$tier" && "$t" != "$tier" ]] && continue
  [[ -n "$suite" && "$s" != "$suite" ]] && continue
  rows+=("$ms|$t|$s|$exp|$asserts")
done < tests/suites.txt

[[ ${#rows[@]} -gt 0 ]] || { echo "No suites selected (upto=$upto milestone=$only_ms tier=$tier suite=$suite)"; exit 1; }

record() { echo "KVIBE-TEST $1 $2 $3" >> "$results"; }

npass=0; nfail=0; nerr=0
run_row() {
  local ms="$1" t="$2" s="$3" exp="$4" asserts="$5" id="run:$2/$3"
  local start end rc verdict detail serial qlog mode
  start=$(date +%s.%N)
  case "$t" in
    unit)
      local log="$LOGS/unit-$s.log"
      if [[ "$s" == cargo:* ]]; then
        local dir="${s#cargo:}"
        if (cd "$dir" && cargo test --quiet) >"$log" 2>&1; then rc=0; else rc=$?; fi
      else
        [[ -x "tests/unit/$s.sh" ]] || { record "$id" FAIL "missing tests/unit/$s.sh"; nerr=$((nerr+1)); return; }
        if "tests/unit/$s.sh" >"$log" 2>&1; then rc=0; else rc=$?; fi
      fi
      if [[ $rc -eq 0 ]]; then verdict=PASS; else verdict=FAIL; fi
      serial="$log"; qlog=/dev/null ;;
    harness|kernel|uefi)
      mode=kernel; [[ "$t" == uefi ]] && mode=uefi
      if tools/qemu-run.sh "$mode" "$s" >/dev/null 2>"$LOGS/stderr-$mode-$s.log"; then rc=0; else rc=$?; fi
      serial="$LOGS/serial-$mode-$s.log"; qlog="$LOGS/qemu-$mode-$s.log"
      [[ -f "$serial" ]] || : > "$serial"
      verdict="$(classify "$rc" "$serial" "$qlog")"
      # Import guest case lines (not for harness: those fail on purpose).
      if [[ "$t" != harness ]]; then
        grep '^KVIBE-TEST ' "$serial" | tr -d '\r' >> "$results" || [[ $? -eq 1 ]]
      fi ;;
    *)
      record "$id" FAIL "tier '$t' not implemented in tools/test.sh"; nerr=$((nerr+1)); return ;;
  esac
  end=$(date +%s.%N)
  local secs; secs=$(awk "BEGIN{printf \"%.1f\", $end-$start}")
  local afail=""
  if [[ "$verdict" == "$exp" ]]; then
    if ! afail="$(check_assertions "$asserts" "$serial" "$qlog")"; then verdict_ok=0; else verdict_ok=1; fi
  else
    verdict_ok=0
  fi
  detail="verdict=$verdict expect=$exp exit=$rc time=${secs}s"
  if [[ $verdict_ok -eq 1 ]]; then
    record "$id" PASS "$detail"; npass=$((npass+1))
    printf 'PASS  %-32s %s\n' "$id" "$detail"
  else
    [[ -n "$afail" ]] && detail="$detail; ${afail//$'\n'/; }"
    record "$id" FAIL "$detail"
    if [[ "$verdict" == ERROR ]]; then nerr=$((nerr+1)); else nfail=$((nfail+1)); fi
    printf 'FAIL  %-32s %s\n' "$id" "$detail"
    printf '      logs: %s %s\n' "$serial" "$qlog"
  fi
}

for ((i = 1; i <= repeat; i++)); do
  [[ $repeat -gt 1 ]] && echo "--- repeat $i/$repeat ---"
  for r in "${rows[@]}"; do
    IFS='|' read -r ms t s exp asserts <<< "$r"
    run_row "$ms" "$t" "$s" "$exp" "$asserts"
  done
done

echo "Summary: $npass passed, $nfail failed, $nerr infra errors"
[[ $nerr -gt 0 ]] && exit 2
[[ $nfail -gt 0 ]] && exit 1
exit 0
