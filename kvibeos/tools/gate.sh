#!/usr/bin/env bash
# Milestone gate: every check must pass before a milestone commit is allowed.
#   tools/gate.sh [N]          gate milestone N (default: MILESTONE + 1)
#   tools/gate.sh --fingerprint print the working-tree fingerprint (used by the commit hook)
# On success: bumps MILESTONE to N and writes build/logs/gate-m<N>.ok (fingerprint) and
# build/logs/gate-m<N>.md (report). Exit 0 only if every check passed.
source "$(dirname "$0")/lib/common.sh"

PROTECTED=(PLAN.md CLAUDE.md tests/protocol.md tests/budgets.toml tests/suites.txt tests/gates tools/gate.sh tools/lib/classify.sh tests/unit/classifier.sh .githooks)

fingerprint() {
  git ls-files -co --exclude-standard -z -- . | LC_ALL=C sort -z | xargs -0 sha256sum 2>/dev/null | sha256sum | cut -d' ' -f1
}
if [[ "${1:-}" == "--fingerprint" ]]; then fingerprint; exit 0; fi

cur="$(tr -d '[:space:]' < MILESTONE 2>/dev/null || echo -1)"
N="${1:-$((cur + 1))}"
[[ "$N" =~ ^[0-9]+$ ]] || die "bad milestone '$N'"
report="$LOGS/gate-m$N.md"
okfile="$LOGS/gate-m$N.ok"
rm -f "$okfile"
results="$LOGS/results.txt"
checklist="tests/gates/m$N.txt"

nfail=0; nwarn=0
{ echo "# Gate report — M$N"; echo; echo "Date: $(date -u +%FT%TZ)  Host KVM: $([[ -w /dev/kvm ]] && echo yes || echo no)"; echo; echo "| # | Check | Result | Detail |"; echo "|---|---|---|---|"; } > "$report"
nchecks=0
check() { # check <name> <PASS|FAIL|WARN> <detail>
  nchecks=$((nchecks+1))
  [[ "$2" == FAIL ]] && nfail=$((nfail+1))
  [[ "$2" == WARN ]] && nwarn=$((nwarn+1))
  printf '%-4s %-38s %s\n' "$2" "$1" "$3"
  echo "| $nchecks | $1 | $2 | ${3//|/\\|} |" >> "$report"
}

# --- G1: preconditions --------------------------------------------------------
[[ "$N" -eq $((cur + 1)) ]] && check "milestone order" PASS "MILESTONE=$cur, gating M$N" \
  || check "milestone order" FAIL "MILESTONE=$cur; can only gate M$((cur+1)), not M$N"
[[ -s "$checklist" ]] && check "checklist exists" PASS "$checklist" || check "checklist exists" FAIL "$checklist missing/empty"
hooks="$(git config --get core.hooksPath || true)"
[[ "$hooks" == *".githooks" ]] && check "commit hook installed" PASS "core.hooksPath=$hooks" \
  || check "commit hook installed" FAIL "run: make hooks"
nreg=$(grep -cE "^[[:space:]]*$N[[:space:]]*\|" tests/suites.txt || true)
[[ "$nreg" -gt 0 ]] && check "suites registered for M$N" PASS "$nreg suite(s)" || check "suites registered for M$N" FAIL "no tests/suites.txt rows for M$N"

# --- G2: protected files vs last REVIEW:/M<n>: commit ---------------------------
base="$(git log -1 --format=%H -E --grep='^(REVIEW|M[0-9]+):' -- . || true)"
if [[ -z "$base" ]]; then
  check "protected files" FAIL "no REVIEW:/M<n>: baseline commit found"
else
  bad=""
  for p in "${PROTECTED[@]}"; do
    [[ "$p" == tests/suites.txt || "$p" == tests/gates ]] && continue
    git diff --quiet "$base" -- "$p" 2>/dev/null || bad="$bad $p"
    [[ -n "$(git ls-files -o --exclude-standard -- "$p")" ]] && bad="$bad $p(untracked)"
  done
  # suites.txt: append-only, new rows only for milestone N
  if ! git diff --quiet "$base" -- tests/suites.txt; then
    git diff "$base" -- tests/suites.txt | grep -qE '^-[^-]' && bad="$bad tests/suites.txt(removed-lines)"
    git diff "$base" -- tests/suites.txt | grep -E '^\+[^+]' | grep -vE "^\+[[:space:]]*($N[[:space:]]*\||#|$)" -q && bad="$bad tests/suites.txt(rows-not-M$N)"
  fi
  # gates: only m<N>.txt may change, append-only
  for f in $(git diff --name-only "$base" -- tests/gates; git ls-files -o --exclude-standard -- tests/gates); do
    if [[ "$f" != "tests/gates/m$N.txt" && "$f" != "kvibeos/tests/gates/m$N.txt" ]]; then bad="$bad $f"; continue; fi
    git diff "$base" -- "tests/gates/m$N.txt" | grep -qE '^-[^-]' && bad="$bad $f(removed-lines)"
  done
  [[ -z "$bad" ]] && check "protected files" PASS "unchanged since ${base:0:9} (appends allowed)" \
    || check "protected files" FAIL "changed:$bad — use FLAGS.md"
fi

# --- G3: open blocking flags ------------------------------------------------------
openflags=$(awk '/^## FLAG/{if(f&&b&&!r)n++; f=1;b=0;r=0} /Blocking: *yes/{b=1} /Status: *resolved/{r=1} END{if(f&&b&&!r)n++; print n+0}' FLAGS.md 2>/dev/null || echo 0)
[[ "$openflags" -eq 0 ]] && check "no open blocking flags" PASS "FLAGS.md" || check "no open blocking flags" FAIL "$openflags open blocking flag(s)"

# --- G4: static checks ------------------------------------------------------------
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
# tools/gate.sh itself is reviewer-owned (protected) and excluded from this scan.
shfiles=$(git ls-files -co --exclude-standard -- 'tests/*' tools/test.sh tools/lib/classify.sh tools/budget.sh tools/qemu-run.sh 2>/dev/null)
hits=$(grep -nE '\|\|[[:space:]]*true|exit[[:space:]]+0[[:space:]]*#.*skip' $shfiles 2>/dev/null || true)
[[ -z "$hits" ]] && check "no swallowed test failures" PASS "no '|| true' in test tooling" \
  || check "no swallowed test failures" FAIL "$(echo "$hits" | head -3 | tr '\n' ' ')"
# unsafe: confined to allowed dirs, each with a SAFETY comment
ua=""; ul=""
for f in $rsfiles; do
  awk -v F="$f" '
    { lines[NR]=$0 }
    /unsafe[[:space:]]*\{|unsafe[[:space:]]+fn|unsafe[[:space:]]+impl/ {
      ok=0; for(i=NR;i>=NR-3&&i>0;i--) if (lines[i] ~ /SAFETY:/) ok=1
      if ($0 ~ /unsafe[[:space:]]+fn/) for(i=NR;i>=NR-10&&i>0;i--) if (lines[i] ~ /# Safety/) ok=1
      if (!ok) print F ":" NR
    }' "$f" >> "$LOGS/gate-unsafe.tmp"
  if grep -qE 'unsafe[[:space:]]*\{|unsafe[[:space:]]+(fn|impl)' "$f"; then
    case "$f" in kernel/src/arch/*|kernel/src/mm/*|kernel/src/drivers/*|kernel/src/syscall/*|user/libkv/src/sys.rs|*kernel/src/arch/*|*kernel/src/mm/*|*kernel/src/drivers/*|*kernel/src/syscall/*) ;; *) ul="$ul $f";; esac
  fi
done
ua="$(cat "$LOGS/gate-unsafe.tmp" 2>/dev/null | tr '\n' ' ')"; rm -f "$LOGS/gate-unsafe.tmp"
[[ -z "$ua" ]] && check "unsafe has SAFETY comments" PASS "all blocks documented" || check "unsafe has SAFETY comments" FAIL "$ua"
[[ -z "$ul" ]] && check "unsafe location allowlist" PASS "arch/mm/drivers/syscall only" || check "unsafe location allowlist" FAIL "$ul"

# --- G5: repo hygiene -------------------------------------------------------------------
tracked_build=$(git ls-files -- build target kernel/target user/target | head -3)
[[ -z "$tracked_build" ]] && check "no build outputs tracked" PASS "build/ and target/ ignored" || check "no build outputs tracked" FAIL "$tracked_build"
big=$(git ls-files -co --exclude-standard -z -- . | xargs -0 -r find 2>/dev/null -maxdepth 0 -size +5M | head -3)
[[ -z "$big" ]] && check "no files > 5 MiB" PASS "" || check "no files > 5 MiB" FAIL "$big"

# --- G6: clean build + full regression -------------------------------------------------
if make --no-print-directory iso >"$LOGS/gate-build.log" 2>&1; then check "clean build (make iso)" PASS "$(stat -c %s "$ISO") byte ISO"; else check "clean build (make iso)" FAIL "see $LOGS/gate-build.log"; fi
if tools/test.sh --upto "$N" --fresh >"$LOGS/gate-test.log" 2>&1; then
  check "full regression M0..M$N" PASS "$(tail -1 "$LOGS/gate-test.log")"
else
  check "full regression M0..M$N" FAIL "$(tail -1 "$LOGS/gate-test.log") — see $LOGS/gate-test.log"
fi
fails=$(grep -c ' FAIL ' "$results" || true)
[[ "$fails" -eq 0 ]] && check "no FAIL lines in results" PASS "$(wc -l < "$results") result lines" || check "no FAIL lines in results" FAIL "$fails FAIL line(s) in $results"

# --- G7: every checklist ID passed -------------------------------------------------------
missing=""; skipped=""; flake=3
if [[ -s "$checklist" ]]; then
  while IFS= read -r id; do
    id="${id%%#*}"; id="$(echo "$id" | xargs)"; [[ -z "$id" ]] && continue
    case "$id" in
      flake:*) flake="${id#flake:}"; continue ;;
      file:*)  [[ -s "${id#file:}" ]] || missing="$missing $id"; continue ;;
    esac
    if grep -qF "KVIBE-TEST $id PASS" "$results"; then continue
    elif [[ "$id" == budget/* ]] && grep -qF "KVIBE-TEST $id SKIP" "$results" && [[ ! -w /dev/kvm ]]; then skipped="$skipped $id"
    else missing="$missing $id"; fi
  done < "$checklist"
  total=$(grep -cvE '^[[:space:]]*(#|$|flake:)' "$checklist")
  [[ -z "$missing" ]] && check "checklist IDs ($total)" PASS "all passed" || check "checklist IDs ($total)" FAIL "not PASS:$missing"
  [[ -n "$skipped" ]] && check "budget checks" WARN "SKIP without KVM:$skipped — re-run gate on a KVM host before release"
fi

# --- G8: flake check -------------------------------------------------------------------
if [[ "$nreg" -gt 0 && "$flake" -gt 1 ]]; then
  if tools/test.sh --milestone "$N" --repeat $((flake - 1)) >"$LOGS/gate-flake.log" 2>&1; then
    check "flake check (${flake}x M$N suites)" PASS "stable"
  else
    check "flake check (${flake}x M$N suites)" FAIL "see $LOGS/gate-flake.log"
  fi
fi

# --- G9: budgets (from M9) ------------------------------------------------------------------
if [[ "$N" -ge 9 ]]; then
  if [[ -x tools/budget.sh ]] && tools/budget.sh >"$LOGS/gate-budget.log" 2>&1; then check "budgets" PASS "see $LOGS/gate-budget.log"; else check "budgets" FAIL "see $LOGS/gate-budget.log"; fi
fi

# --- result ---------------------------------------------------------------------------------
echo >> "$report"
if [[ $nfail -eq 0 ]]; then
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
