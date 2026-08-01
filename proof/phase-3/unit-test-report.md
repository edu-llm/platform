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
| collected by pytest | 4201 |
| executed (excluding tests/test_phase0_proof.py, tests/test_phase1_proof.py, tests/test_phase2_proof.py, tests/test_phase3_proof.py, tests/test_phase5_proof.py) | 4008 |
| passed | 4006 |
| failed | 2 |
| errored | 0 |
| skipped | 0 |
| pytest exit code | 1 |

## Targeted verification run

Every test node id cited by the negative-case matrix, plus every test in the modules Phase 3 added, executed as one selection.

| measure | count |
| --- | --- |
| selected node ids | 420 |
| executed | 420 |
| passed | 419 |
| failed | 1 |
| errored | 0 |
| skipped | 0 |
| pytest exit code | 1 |

## Per-module coverage

The test modules Phase 3 added, excluding the ones that invoke a gate or this generator; those run in the reviewer's own `uv run pytest -q`.

| module | tests | result |
| --- | --- | --- |
| tests/test_capture_phase3_evidence_cli.py | 18 | see below |
| tests/test_phase3_account_measurements.py | 13 | see below |
| tests/test_phase3_batch_denials.py | 29 | see below |
| tests/test_phase3_batch_deployment_workflow.py | 37 | see below |
| tests/test_phase3_deployer_role.py | 14 | see below |
| tests/test_phase3_ec2_authorization.py | 16 | see below |
| tests/test_phase3_execution.py | 74 | see below |
| tests/test_phase3_golden.py | 9 | see below |
| tests/test_phase3_image_scan.py | 47 | see below |
| tests/test_phase3_infrastructure.py | 57 | see below |
| tests/test_phase3_lifecycle_package.py | 3 | see below |
| tests/test_phase3_lifecycle_projection.py | 61 | see below |
| tests/test_phase3_run_evidence.py | 37 | see below |

**A green suite is not evidence that the path works.** Phase 1 shipped one over a workflow that could not complete a run and Phase 2 shipped one over a state machine that could not complete an execution, both times because both sides of a seam were asserted and neither compared to the other. The counts above say the tests pass; `negative-case-matrix.md` says what they establish, which for most of this phase's criteria is not the criterion.

## Failures

- tests/test_phase3_lifecycle_package.py::test_the_released_zip_is_the_one_this_tree_builds
