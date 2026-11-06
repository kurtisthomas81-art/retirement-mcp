# Milestone gate checklists (PROTECTED)

`tests/gates/m<N>.txt` lists every check ID that must PASS before milestone N can be committed.
Written from PLAN.md §9 acceptance criteria at review time. The build agent may **append** IDs
to the current milestone's file. It may never remove or edit IDs.

Line forms:
- `<suite>/<case>`: guest case line, e.g. `smoke/serial`
- `run:<tier>/<suite>`: runner verdict for a whole suite
- `e2e/<scenario>/<check>`, `storage-<ctrl>/<case>`, `host/<check>`, `budget/<metric>`: emitted by the E2E driver, storage verifier, host checks and `tools/budget.sh` into `build/logs/results.txt`
- `file:<path>`: the file must exist and be non-empty (human-produced evidence, M16)
- `flake:<K>`: run this milestone's suites K times in the flake check (default 3)
- `scope:<glob>`: files this milestone may touch (`*` matches across `/`). Common files are always allowed: MILESTONE, FLAGS.md, docs/progress/m<N>.md, tests/suites.txt, tests/gates/m<N>.txt, Cargo.lock
- `shot:<suite>[@MARKER]`: boot <suite> under QMP, wait for MARKER (default `KVIBE-SCREEN-READY`), save a non-blank PNG to build/logs/screens/ and add it to the contact sheet
- Test-quality IDs `quality/<crate>/{coverage,mutants}` are emitted by the gate itself (≥ 80% line coverage, ≥ 80% mutation score)

`budget/*` IDs may be SKIP only when `/dev/kvm` is unavailable. The gate report flags this.
