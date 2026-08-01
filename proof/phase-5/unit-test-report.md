# Phase 5 unit-test report

Summarised counts only. Raw pytest output is not copied here; the commands below reproduce it in full.

## Commands a reviewer can re-run

```
uv run pytest -q
uv run ruff check .
uv run mypy
uv run python tools/export_schemas.py
uv run python tools/validate_phase5.py
uv run python tools/build_phase5_proof.py
```

## Whole suite

| measure | count |
| --- | --- |
| collected by pytest | 3884 |
| executed (excluding tests/test_phase0_proof.py, tests/test_phase1_proof.py, tests/test_phase2_proof.py, tests/test_phase3_proof.py, tests/test_phase5_proof.py) | 3691 |
| passed | 3691 |
| failed | 0 |
| errored | 0 |
| skipped | 0 |
| pytest exit code | 0 |

## Targeted verification run

Every test node id cited by the negative-case matrix, plus every test in the modules Phase 5 added, executed as one selection.

| measure | count |
| --- | --- |
| selected node ids | 92 |
| executed | 92 |
| passed | 92 |
| failed | 0 |
| errored | 0 |
| skipped | 0 |
| pytest exit code | 0 |

## Per-module coverage

The test modules Phase 5 added, excluding the ones that invoke a gate or this generator; those run in the reviewer's own `uv run pytest -q`.

| module | tests | result |
| --- | --- | --- |
| tests/test_phase5_criteria.py | 10 | pass |
| tests/test_phase5_infrastructure.py | 13 | pass |
| tests/test_phase5_run_evidence.py | 18 | pass |
| tests/test_phase5_team_isolation.py | 12 | pass |

**A green suite says nothing about whether anybody can use this.** That is the whole premise of the phase: every capability it measures was already technically possible and already covered by passing tests, while being unreachable by everybody except the person who wrote them. What changed is not the counts below -- it is that three of the runs behind `second-person-evidence.md` were submitted by somebody else.
