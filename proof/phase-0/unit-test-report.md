# Phase 0 unit-test report

Summarised counts only. Raw pytest output is not copied here; the commands below reproduce it in full.

## Commands a reviewer can re-run

```
uv run pytest -q
uv run ruff check .
uv run mypy src
uv run python tools/export_schemas.py
uv run python tools/validate_phase0.py
uv run python tools/build_phase0_proof.py
```

## Whole suite

| measure | count |
| --- | --- |
| collected by pytest | 3229 |
| executed (excluding tests/test_phase0_proof.py, tests/test_phase1_proof.py, tests/test_phase2_proof.py, tests/test_phase3_proof.py) | 3067 |
| passed | 3067 |
| failed | 0 |
| errored | 0 |
| skipped | 0 |
| pytest exit code | 0 |

## Targeted verification run

Every test node id cited by the negative-case matrix, plus every test parametrised over one of the nine fixtures, executed as one selection.

| measure | count |
| --- | --- |
| selected node ids | 254 |
| executed | 254 |
| passed | 254 |
| failed | 0 |
| errored | 0 |
| skipped | 0 |
| pytest exit code | 0 |

## Per-fixture coverage

Tests parametrised over each fixture by name. A fixture with no parametrised tests would show zero here.

| fixture | parametrised tests | result |
| --- | --- | --- |
| admin-exception.yaml | 9 | pass |
| lead-self-authorization.yaml | 9 | pass |
| member-approval.yaml | 9 | pass |
| cpu-routine.yaml | 13 | pass |
| gpu-exception.yaml | 12 | pass |
| gpu-routine.yaml | 13 | pass |
| multiseed-routine.yaml | 13 | pass |
| olmo-branch-routine.yaml | 13 | pass |
| sagemaker-routine.yaml | 13 | pass |
