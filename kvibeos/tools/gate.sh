#!/usr/bin/env bash
# Milestone gate: every check must pass before a milestone commit is allowed. (PROTECTED)
#   tools/gate.sh [N]            gate milestone N (default: MILESTONE + 1); bumps MILESTONE on success
#   tools/gate.sh --verify       re-check the last completed milestone from scratch (CI); no bump
#   tools/gate.sh --fingerprint  print the working-tree fingerprint (used by the commit hook)
# Writes build/logs/gate-m<N>.md (report), gate-m<N>.ok (fingerprint) and gate-m<N>-screens.html.
source "$(dirname "$0")/lib/common.sh"
source "$(dirname "$0")/lib/quality.sh"

PROTECTED=(PLAN.md CLAUDE.md tests/protocol.md tests/budgets.toml tests/suites.txt tests/gates
           tools/gate.sh tools/test.sh tools/qemu-run.sh tools/screenshot.py
           tools/lib/classify.sh tools/lib/quality.sh tests/unit/classifier.sh .githooks .github)

fingerprint() {
  git ls-files -co --exclude-standard -z -- . | LC_ALL=C sort -z | xargs -0 sha256sum 2>/dev/null | sha256sum | cut -d' ' -f1
}
if [[ "${1:-}" == "--fingerprint" ]]; then fingerprint; exit 0; fi

cur="$(tr -d '[:space:]' < MILESTONE 2>/dev/null || echo -1)"
verify=0
if [[ "${1:-}" == "--verify" ]]; then verify=1; N="$cur"; else N="${1:-$((cur + 1))}"; fi
[[ "$N" =~ ^[0-9]+$ ]] || die "bad milestone '$N' (MILESTONE=$cur)"
report="$LOGS/gate-m$N.md"
okfile="$LOGS/gate-m$N.ok"
rm -f "$okfile"
results="$LOGS/results.txt"
checklist="tests/gates/m$N.txt"

nfail=0; nwarn=0; nchecks=0
{ echo "# Gate report — M$N$([[ $verify -eq 1 ]] && echo ' (verify)')"; echo
  echo "Date: $(date -u +%FT%TZ)  Commit: $(git rev-parse --short HEAD)  Host KVM: $([[ -w /dev/kvm ]] && echo yes || echo no)"; echo
  echo "| # | Check | Result | Detail |"; echo "|---|---|---|---|"; } > "$report"
check() { # check <name> <PASS|FAIL|WARN> <detail>
  nchecks=$((nchecks+1))
  [[ "$2" == FAIL ]] && nfail=$((nfail+1))
  [[ "$2" == WARN ]] && nwarn=$((nwarn+1))
  printf '%-4s %-40s %s\n' "$2" "$1" "$3"
  echo "| $nchecks | $1 | $2 | ${3//|/\\|} |" >> "$report"
}

# Checklist directives
ids=(); scopes=(); shots=(); flake=3
if [[ -s "$checklist" ]]; then
  while IFS= read -r line; do
    line="${line%%#*}"; line="$(echo "$line" | xargs)"; [[ -z "$line" ]] && continue
    case "$line" in
      flake:*) flake="${line#flake:}" ;;
      scope:*) scopes+=("${line#scope:}") ;;
      shot:*)  shots+=("${line#shot:}") ;;
      *)       ids+=("$line") ;;
    esac
  done < "$checklist"
fi

# --- G1: preconditions --------------------------------------------------------
if [[ $verify -eq 0 ]]; then
  [[ "$N" -eq $((cur + 1)) ]] && check "milestone order" PASS "MILESTONE=$cur, gating M$N" \
    || check "milestone order" FAIL "MILESTONE=$cur; can only gate M$((cur+1)), not M$N"
  hooks="$(git config --get core.hooksPath || true)"
  [[ "$hooks" == *".githooks" ]] && check "commit hook installed" PASS "core.hooksPath=$hooks" \
    || check "commit hook installed" FAIL "run: make hooks"
fi
[[ ${#ids[@]} -gt 0 ]] && check "checklist exists" PASS "$checklist (${#ids[@]} IDs)" || check "checklist exists" FAIL "$checklist missing/empty"
nreg=$(grep -cE "^[[:space:]]*$N[[:space:]]*\|" tests/suites.txt || true)
[[ "$nreg" -gt 0 ]] && check "suites registered for M$N" PASS "$nreg suite(s)" || check "suites registered for M$N" FAIL "no tests/suites.txt rows for M$N"

# --- G2: protected files + scope vs the last REVIEW:/M<n>: commit -----------------
if [[ $verify -eq 1 ]]; then
  base="HEAD"
else
  base="$(git log -1 --format=%H -E --grep='^(REVIEW|M[0-9]+):' -- . || true)"
fi
if [[ -z "$base" ]]; then
  check "protected files" FAIL "no REVIEW:/M<n>: baseline commit found"
else
  changed=$( { git diff --name-only --relative "$base" -- .; git ls-files -o --exclude-standard -- .; } | sort -u)
  bad=""
  for p in "${PROTECTED[@]}"; do
    [[ "$p" == tests/suites.txt || "$p" == tests/gates ]] && continue
    echo "$changed" | grep -qE "^${p//./\\.}(/|$)" && bad="$bad $p"
  done
  if echo "$changed" | grep -qx tests/suites.txt; then
    git diff "$base" -- tests/suites.txt | grep -qE '^-[^-]' && bad="$bad tests/suites.txt(removed-lines)"
    git diff "$base" -- tests/suites.txt | grep -E '^\+[^+]' | grep -vE "^\+[[:space:]]*($N[[:space:]]*\||#|$)" -q && bad="$bad tests/suites.txt(rows-not-M$N)"
  fi
  for f in $(echo "$changed" | grep '^tests/gates/' || true); do
    if [[ "$f" != "tests/gates/m$N.txt" ]]; then bad="$bad $f"; continue; fi
    git diff "$base" -- "$f" | grep -qE '^-[^-]' && bad="$bad $f(removed-lines)"
  done
  [[ -z "$bad" ]] && check "protected files" PASS "unchanged since ${base:0:9} (appends allowed)" \
    || check "protected files" FAIL "changed:$bad — use FLAGS.md"

  # Scope: every changed file must match the milestone's scope globs or the common allowlist.
  common=(MILESTONE FLAGS.md "docs/progress/m$N.md" tests/suites.txt "tests/gates/m$N.txt" "*Cargo.lock")
  out=""
  if [[ $verify -eq 0 && ${#scopes[@]} -gt 0 ]]; then
    while IFS= read -r f; do
      [[ -z "$f" ]] && continue
      ok=0
      for g in "${common[@]}" "${scopes[@]}"; do
        # shellcheck disable=SC2053
        [[ "$f" == $g ]] && { ok=1; break; }
      done
      [[ $ok -eq 0 ]] && out="$out $f"
    done <<< "$changed"
    [[ -z "$out" ]] && check "scope (files touched)" PASS "$(echo "$changed" | grep -c . || true) file(s) within M$N scope" \
      || check "scope (files touched)" FAIL "outside M$N scope:$out — flag it if needed"
  elif [[ $verify -eq 0 ]]; then
    check "scope (files touched)" FAIL "$checklist has no scope: lines"
  fi
fi

# --- G3: open blocking flags + progress journal -------------------------------------
openflags=$(awk '/^## FLAG/{if(f&&b&&!r)n++; f=1;b=0;r=0} /Blocking: *yes/{b=1} /Status: *resolved/{r=1} END{if(f&&b&&!r)n++; print n+0}' FLAGS.md 2>/dev/null || echo 0)
[[ "$openflags" -eq 0 ]] && check "no open blocking flags" PASS "FLAGS.md" || check "no open blocking flags" FAIL "$openflags open blocking flag(s)"
journal="docs/progress/m$N.md"
jmiss=""
for h in "## Summary" "## Decisions" "## Known issues" "## Next"; do grep -qF "$h" "$journal" 2>/dev/null || jmiss="$jmiss '$h'"; done
[[ -z "$jmiss" ]] && check "progress journal" PASS "$journal" || check "progress journal" FAIL "$journal missing sections:$jmiss"

# --- G4: static checks ----------------------------------------------------------------
workspaces=()
for d in kernel user tools/xtask crates/*; do [[ -f "$d/Cargo.toml" ]] && workspaces+=("$d"); done
for d in "${workspaces[@]}"; do
  if (cd "$d" && cargo fmt --check) >"$LOGS/gate-fmt.log" 2>&1; then check "rustfmt $d" PASS "clean"; else check "rustfmt $d" FAIL "see $LOGS/gate-fmt.log"; fi
  extra=(); [[ "$d" == crates/* || "$d" == tools/xtask ]] && extra=(--all-targets)
  if (cd "$d" && cargo clippy --release "${extra[@]}" -- -D warnings) >"$LOGS/gate-clippy.log" 2>&1; then check "clippy $d" PASS "-D warnings"; else check "clippy $d" FAIL "see $LOGS/gate-clippy.log"; fi
done
rsfiles=$(git ls-files -co --exclude-standard -- '*.rs')
hits=$(grep -nE 'todo!\(|unimplemented!\(|dbg!\(|#\[ignore|#!\[allow|allow\(warnings\)' $rsfiles 2>/dev/null || true)
[[ -z "$hits" ]] && check "forbidden Rust patterns" PASS "no todo!/unimplemented!/dbg!/#[ignore]/crate-wide allow" \
  || check "forbidden Rust patterns" FAIL "$(echo "$hits" | head -3 | tr '\n' ' ')"
# Reviewer-owned (protected) tooling is excluded from this scan.
shfiles=$(git ls-files -co --exclude-standard -- 'tests/e2e/*' 'tests/fixtures/*' tools/budget.sh tools/mkiso.sh tools/mkimg.sh 2>/dev/null)
hits=$(grep -nE '\|\|[[:space:]]*true|exit[[:space:]]+0[[:space:]]*#.*skip' $shfiles 2>/dev/null || true)
[[ -z "$hits" ]] && check "no swallowed test failures" PASS "no '|| true' in test tooling" \
  || check "no swallowed test failures" FAIL "$(echo "$hits" | head -3 | tr '\n' ' ')"
ua=""; ul=""
for f in $rsfiles; do
  ua="$ua$(awk -v F="$f" '
    { lines[NR]=$0 }
    /unsafe[[:space:]]*\{|unsafe[[:space:]]+fn|unsafe[[:space:]]+impl/ {
      ok=0; for(i=NR;i>=NR-3&&i>0;i--) if (lines[i] ~ /SAFETY:/) ok=1
      if ($0 ~ /unsafe[[:space:]]+fn/) for(i=NR;i>=NR-10&&i>0;i--) if (lines[i] ~ /# Safety/) ok=1
      if (!ok) printf " %s:%d", F, NR
    }' "$f")"
  if grep -qE 'unsafe[[:space:]]*\{|unsafe[[:space:]]+(fn|impl)' "$f"; then
    case "$f" in kernel/src/arch/*|kernel/src/mm/*|kernel/src/drivers/*|kernel/src/syscall/*|user/libkv/src/sys.rs) ;; *) ul="$ul $f";; esac
  fi
done
[[ -z "$ua" ]] && check "unsafe has SAFETY comments" PASS "all blocks documented" || check "unsafe has SAFETY comments" FAIL "$ua"
[[ -z "$ul" ]] && check "unsafe location allowlist" PASS "arch/mm/drivers/syscall only" || check "unsafe location allowlist" FAIL "$ul"

# --- G5: repo hygiene -------------------------------------------------------------------
tracked_build=$(git ls-files -- build target kernel/target user/target | head -3)
[[ -z "$tracked_build" ]] && check "no build outputs tracked" PASS "build/ and target/ ignored" || check "no build outputs tracked" FAIL "$tracked_build"
big=""
while IFS= read -r -d '' f; do
  [[ -f "$f" ]] && [[ $(stat -c %s "$f") -gt 5242880 ]] && big="$big $f"
done < <(git ls-files -co --exclude-standard -z -- .)
[[ -z "$big" ]] && check "no files > 5 MiB" PASS "" || check "no files > 5 MiB" FAIL "$big"

# --- G6: clean build + full regression -------------------------------------------------
# Never test stale artifacts: remove them first, and skip testing if the build fails.
rm -f "$ISO" "$KERNEL_ELF"
: > "$results"
built=0
if make --no-print-directory iso >"$LOGS/gate-build.log" 2>&1; then built=1; check "clean build (make iso)" PASS "$(stat -c %s "$ISO") byte ISO"; else check "clean build (make iso)" FAIL "see $LOGS/gate-build.log"; fi
if [[ $built -eq 0 ]]; then
  check "full regression M0..M$N" FAIL "not run: build failed"
elif tools/test.sh --upto "$N" --fresh >"$LOGS/gate-test.log" 2>&1; then
  check "full regression M0..M$N" PASS "$(tail -1 "$LOGS/gate-test.log")"
else
  check "full regression M0..M$N" FAIL "$(tail -1 "$LOGS/gate-test.log") — see $LOGS/gate-test.log"
fi

# --- G7: test quality for host crates changed in this milestone ---------------------------
if [[ $built -eq 1 ]]; then
  for d in crates/*; do
    [[ -f "$d/Cargo.toml" ]] || continue
    if [[ $verify -eq 1 ]] || echo "${changed:-}" | grep -q "^$d/"; then
      quality_check "$d" "$results"   # emits quality/<crate>/{coverage,mutants} lines
    fi
  done
fi

# --- G8: screenshots (agent-viewable + human contact sheet) --------------------------------
if [[ $built -eq 1 && ${#shots[@]} -gt 0 ]]; then
  mkdir -p "$LOGS/screens"
  for s in "${shots[@]}"; do
    if tools/screenshot.py --suite "$s" --out "$LOGS/screens/m$N-$s.png" >"$LOGS/gate-shot-$s.log" 2>&1; then
      echo "KVIBE-TEST shot/$s PASS $LOGS/screens/m$N-$s.png" >> "$results"
    else
      echo "KVIBE-TEST shot/$s FAIL see $LOGS/gate-shot-$s.log" >> "$results"
    fi
  done
  tools/screenshot.py --contact-sheet "$LOGS/gate-m$N-screens.html" --title "KVibeOS M$N" "$LOGS"/screens/m"$N"-*.png >/dev/null 2>&1 \
    && check "screenshot contact sheet" PASS "$LOGS/gate-m$N-screens.html" \
    || check "screenshot contact sheet" FAIL "could not build contact sheet"
fi

fails=$(grep -c ' FAIL ' "$results" || true)
[[ "$fails" -eq 0 ]] && check "no FAIL lines in results" PASS "$(wc -l < "$results") result lines" || check "no FAIL lines in results" FAIL "$fails FAIL line(s): $(grep ' FAIL ' "$results" | head -3 | cut -d' ' -f2 | tr '\n' ' ')"

# --- G9: every checklist ID passed -------------------------------------------------------
missing=""; skipped=""
for id in "${ids[@]}"; do
  case "$id" in
    file:*) [[ -s "${id#file:}" ]] || missing="$missing $id"; continue ;;
  esac
  if grep -qF "KVIBE-TEST $id PASS" "$results"; then continue
  elif [[ "$id" == budget/* ]] && grep -qF "KVIBE-TEST $id SKIP" "$results" && [[ ! -w /dev/kvm ]]; then skipped="$skipped $id"
  else missing="$missing $id"; fi
done
for s in "${shots[@]}"; do grep -qF "KVIBE-TEST shot/$s PASS" "$results" || missing="$missing shot/$s"; done
if [[ ${#ids[@]} -gt 0 ]]; then
  [[ -z "$missing" ]] && check "checklist IDs (${#ids[@]} + ${#shots[@]} shots)" PASS "all passed" \
    || check "checklist IDs (${#ids[@]} + ${#shots[@]} shots)" FAIL "not PASS:$missing"
fi
[[ -n "$skipped" ]] && check "budget checks" WARN "SKIP without KVM:$skipped — re-run on a KVM host (CI) before release"

# --- G10: flake check ---------------------------------------------------------------------
if [[ $built -eq 1 && "$nreg" -gt 0 && "$flake" -gt 1 ]]; then
  if tools/test.sh --milestone "$N" --repeat $((flake - 1)) >"$LOGS/gate-flake.log" 2>&1; then
    check "flake check (${flake}x M$N suites)" PASS "stable"
  else
    check "flake check (${flake}x M$N suites)" FAIL "see $LOGS/gate-flake.log"
  fi
fi

# --- G11: budgets (from M9) ------------------------------------------------------------------
if [[ "$N" -ge 9 ]]; then
  if [[ -x tools/budget.sh ]] && tools/budget.sh >"$LOGS/gate-budget.log" 2>&1; then check "budgets" PASS "see $LOGS/gate-budget.log"; else check "budgets" FAIL "see $LOGS/gate-budget.log"; fi
fi

# --- result ---------------------------------------------------------------------------------
echo >> "$report"
if [[ $nfail -eq 0 ]]; then
  if [[ $verify -eq 1 ]]; then
    echo "**VERIFY PASSED** — M$N re-checked from scratch." >> "$report"
    echo "VERIFY PASSED: M$N ($nwarn warning(s)). Report: $report"
    exit 0
  fi
  echo "$N" > MILESTONE
  fp="$(fingerprint)"
  echo "$fp" > "$okfile"
  echo "**GATE PASSED** — M$N. MILESTONE bumped to $N. Fingerprint \`$fp\`. Commit with subject \`M$N: ...\` without further edits." >> "$report"
  echo "GATE PASSED: M$N ($nwarn warning(s)). MILESTONE=$N. Report: $report"
  echo "Commit now with subject 'M$N: <title>' — any further edit invalidates the gate."
  exit 0
else
  echo "**GATE FAILED** — $nfail check(s) failed." >> "$report"
  echo "GATE FAILED: $nfail check(s). Report: $report"
  exit 1
fi
