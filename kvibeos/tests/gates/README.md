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

`budget/*` IDs may be SKIP only when `/dev/kvm` is unavailable. The gate report flags this.
