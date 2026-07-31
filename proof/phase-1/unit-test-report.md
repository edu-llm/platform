# Phase 1 unit-test report

Summarised counts only. Raw pytest output is not copied here; the commands below reproduce it in full.

## Commands a reviewer can re-run

```
uv run pytest -q
uv run ruff check .
uv run mypy
uv run python tools/export_schemas.py
uv run python tools/validate_phase1.py
uv run python tools/build_phase1_proof.py
```

## Whole suite

| measure | count |
| --- | --- |
| collected by pytest | 3807 |
| executed (excluding tests/test_phase0_proof.py, tests/test_phase1_proof.py, tests/test_phase2_proof.py, tests/test_phase3_proof.py, tests/test_phase5_proof.py) | 3626 |
| passed | 3626 |
| failed | 0 |
| errored | 0 |
| skipped | 0 |
| pytest exit code | 0 |

## Targeted verification run

Every test node id cited by the negative-case matrix, plus every test in the modules Phase 1 added, executed as one selection.

| measure | count |
| --- | --- |
| selected node ids | 423 |
| executed | 423 |
| passed | 423 |
| failed | 0 |
| errored | 0 |
| skipped | 0 |
| pytest exit code | 0 |

## Per-module coverage

The test modules Phase 1 added, excluding the two that invoke a gate or this generator; those run in the reviewer's own `uv run pytest -q`.

| module | tests | result |
| --- | --- | --- |
| tests/test_capture_phase1_evidence_cli.py | 24 | pass |
| tests/test_capture_phase1_run_evidence.py | 18 | pass |
| tests/test_phase1_deployed_roles.py | 21 | pass |
| tests/test_phase1_deployer_role.py | 22 | pass |
| tests/test_phase1_ecr_deployment_workflow.py | 7 | pass |
| tests/test_phase1_evidence.py | 197 | pass |
| tests/test_phase1_golden.py | 5 | pass |
| tests/test_phase1_infrastructure.py | 12 | pass |
| tests/test_phase1_preconditions.py | 1 | pass |
| tests/test_phase1_rebuild_comparison.py | 30 | pass |
| tests/test_phase1_role_drift.py | 42 | pass |
| tests/test_phase1_run_evidence.py | 18 | pass |
