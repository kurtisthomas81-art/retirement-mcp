# KVibeOS test protocol (PROTECTED)

## Guest output (COM1 serial)
| Line | Meaning |
|---|---|
| `KVIBE-BOOT start` / `KVIBE-BOOT OK` | Kernel entered / Limine handshake done |
| `KVIBE-INFO <k>=<v>` | Informational |
| `KVIBE-TESTMODE suite=<name>` | Gated test mode entered |
| `KVIBE-TEST <suite>/<case> PASS\|FAIL\|SKIP <detail>` | One case result |
| `KVIBE-RESULT PASS\|FAIL` | Suite result (a suite with zero cases is FAIL) |
| `KVIBE-PANIC <message>` | Kernel panic |
| `KVIBE-POWEROFF` / `KVIBE-REBOOT` | Deliberate ACPI power-off / reset (M13) |
| `KVIBE-METRIC <name>=<value>` | Budget metric (M3+) |
| `KVIBE-UI {json}` / `KVIBE-INPUT ...` | Desktop/input events for E2E (M7+) |

## Test-mode gate
Test mode is entered only if **all** hold: CPUID hypervisor bit set, QEMU fw_cfg signature `"QEMU"`, and fw_cfg file `opt/kvibe/test` non-empty. Only then may the kernel write to isa-debug-exit (port `0xf4`).

## Exit codes
Guest writes `0x10` (pass) / `0x11` (fail) to port `0xf4`; QEMU exits with `(v<<1)|1`.

| QEMU exit | Extra evidence | Verdict |
|---|---|---|
| 33 | `KVIBE-RESULT PASS` at line start | PASS |
| 33 | no result line | NO_RESULT (failure) |
| 35 | — | FAIL |
| 124 | — | TIMEOUT |
| 0 | `Triple fault` in QEMU `-d cpu_reset` log | TRIPLE_FAULT |
| 0 | `KVIBE-POWEROFF` on serial | POWEROFF |
| 0 | neither | RESET |
| other | — | ERROR (infra) |

A suite passes only if its verdict equals the expected verdict in `tests/suites.txt` **and** every assertion holds. Classification lives in `tools/lib/classify.sh` and is itself unit-tested (`tests/unit/classifier.sh`).

## Results file
`build/logs/results.txt`, one line per check: `KVIBE-TEST <id> PASS|FAIL|SKIP <detail>`. `make gate` checks each milestone's required IDs (`tests/gates/m<N>.txt`) against this file.

## Runner exit codes
`0` all pass · `1` a check failed · `2` infrastructure error.
