# Test-quality checks for host crates (PROTECTED). Source, don't execute.
# quality_check <crate-dir> <results-file>
#   coverage: line coverage >= QUALITY_MIN_COVERAGE (cargo llvm-cov)
#   mutants:  mutation score >= QUALITY_MIN_MUTATION (cargo mutants; caught / (caught + missed))
# Emits:  KVIBE-TEST quality/<crate>/coverage PASS|FAIL <pct>
#         KVIBE-TEST quality/<crate>/mutants  PASS|FAIL <score>
QUALITY_MIN_COVERAGE="${QUALITY_MIN_COVERAGE:-80}"
QUALITY_MIN_MUTATION="${QUALITY_MIN_MUTATION:-80}"

quality_check() {
  local dir="$1" results="$2" name pct score caught missed log logs
  name="$(basename "$dir")"
  logs="$KV_ROOT/$LOGS"; [[ "$LOGS" == /* ]] && logs="$LOGS"
  mkdir -p "$logs"
  log="$logs/quality-$name.log"
  : > "$log"

  if ! command -v cargo-llvm-cov >/dev/null; then
    echo "KVIBE-TEST quality/$name/coverage FAIL cargo-llvm-cov not installed (cargo install --locked cargo-llvm-cov)" >> "$results"
  else
    pct=$(cd "$dir" && cargo llvm-cov --summary-only --json </dev/null 2>>"$log" | python3 -c 'import json,sys; print(round(json.load(sys.stdin)["data"][0]["totals"]["lines"]["percent"],1))' 2>>"$log")
    if [[ -n "$pct" ]] && awk "BEGIN{exit !($pct >= $QUALITY_MIN_COVERAGE)}"; then
      echo "KVIBE-TEST quality/$name/coverage PASS ${pct}% lines (min $QUALITY_MIN_COVERAGE%)" >> "$results"
    else
      echo "KVIBE-TEST quality/$name/coverage FAIL ${pct:-?}% lines (min $QUALITY_MIN_COVERAGE%) see $log" >> "$results"
    fi
  fi

  if ! command -v cargo-mutants >/dev/null; then
    echo "KVIBE-TEST quality/$name/mutants FAIL cargo-mutants not installed (cargo install --locked cargo-mutants)" >> "$results"
  else
    rm -rf "$logs/mutants-$name"
    (cd "$dir" && cargo mutants --no-shuffle --output "$logs/mutants-$name") </dev/null >>"$log" 2>&1
    caught=$(grep -c . "$logs/mutants-$name/mutants.out/caught.txt" 2>/dev/null)
    missed=$(grep -c . "$logs/mutants-$name/mutants.out/missed.txt" 2>/dev/null)
    caught="${caught:-0}"; missed="${missed:-0}"
    if [[ $((caught + missed)) -eq 0 ]]; then
      echo "KVIBE-TEST quality/$name/mutants PASS no viable mutants" >> "$results"
    else
      score=$((100 * caught / (caught + missed)))
      if [[ $score -ge $QUALITY_MIN_MUTATION ]]; then
        echo "KVIBE-TEST quality/$name/mutants PASS ${score}% ($caught caught, $missed missed)" >> "$results"
      else
        echo "KVIBE-TEST quality/$name/mutants FAIL ${score}% ($caught caught, $missed missed; min $QUALITY_MIN_MUTATION%) see $logs/mutants-$name/mutants.out/missed.txt" >> "$results"
      fi
    fi
  fi
}
