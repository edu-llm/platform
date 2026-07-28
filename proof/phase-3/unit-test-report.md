# Phase 3 unit-test report

Summarised counts only. Raw pytest output is not copied here; the commands below reproduce it in full.

## Commands a reviewer can re-run

```
uv run pytest -q
uv run ruff check .
uv run mypy
uv run python tools/export_schemas.py
uv run python tools/validate_phase3.py
uv run python tools/build_phase3_proof.py
```

## Whole suite

| measure | count |
| --- | --- |
| collected by pytest | 3273 |
| executed (excluding tests/test_phase0_proof.py, tests/test_phase1_proof.py, tests/test_phase2_proof.py, tests/test_phase3_proof.py) | 3111 |
| passed | 3111 |
| failed | 0 |
| errored | 0 |
| skipped | 0 |
| pytest exit code | 0 |

## Targeted verification run

Every test node id cited by the negative-case matrix, plus every test in the modules Phase 3 added, executed as one selection.

| measure | count |
| --- | --- |
| selected node ids | 316 |
| executed | 316 |
| passed | 316 |
| failed | 0 |
| errored | 0 |
| skipped | 0 |
| pytest exit code | 0 |

## Per-module coverage

The test modules Phase 3 added, excluding the ones that invoke a gate or this generator; those run in the reviewer's own `uv run pytest -q`.

| module | tests | result |
| --- | --- | --- |
| tests/test_phase3_account_measurements.py | 13 | pass |
| tests/test_phase3_batch_denials.py | 29 | pass |
| tests/test_phase3_batch_deployment_workflow.py | 31 | pass |
| tests/test_phase3_deployer_role.py | 14 | pass |
| tests/test_phase3_ec2_authorization.py | 16 | pass |
| tests/test_phase3_execution.py | 27 | pass |
| tests/test_phase3_golden.py | 9 | pass |
| tests/test_phase3_image_scan.py | 29 | pass |
| tests/test_phase3_infrastructure.py | 52 | pass |
| tests/test_phase3_lifecycle_projection.py | 52 | pass |
| tests/test_phase3_run_evidence.py | 36 | pass |

**A green suite is not evidence that the path works.** Phase 1 shipped one over a workflow that could not complete a run and Phase 2 shipped one over a state machine that could not complete an execution, both times because both sides of a seam were asserted and neither compared to the other. The counts above say the tests pass; `negative-case-matrix.md` says what they establish, which for most of this phase's criteria is not the criterion.
