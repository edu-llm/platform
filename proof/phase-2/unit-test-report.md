# Phase 2 unit-test report

Summarised counts only. Raw pytest output is not copied here; the commands below reproduce it in full.

## Commands a reviewer can re-run

```
uv run pytest -q
uv run ruff check .
uv run mypy
uv run python tools/export_schemas.py
uv run python tools/validate_phase2.py
uv run python tools/build_phase2_proof.py
```

## Whole suite

| measure | count |
| --- | --- |
| collected by pytest | 4680 |
| executed (excluding tests/test_phase0_proof.py, tests/test_phase1_proof.py, tests/test_phase2_proof.py, tests/test_phase3_proof.py, tests/test_phase5_proof.py) | 4487 |
| passed | 4484 |
| failed | 3 |
| errored | 0 |
| skipped | 0 |
| pytest exit code | 1 |

## Targeted verification run

Every test node id cited by the negative-case matrix, plus every test in the modules Phase 2 added, executed as one selection.

| measure | count |
| --- | --- |
| selected node ids | 661 |
| executed | 661 |
| passed | 660 |
| failed | 1 |
| errored | 0 |
| skipped | 0 |
| pytest exit code | 1 |

## Per-module coverage

The test modules Phase 2 added, excluding the ones that invoke a gate or this generator; those run in the reviewer's own `uv run pytest -q`.

| module | tests | result |
| --- | --- | --- |
| tests/test_capture_phase2_evidence_cli.py | 8 | see below |
| tests/test_phase2_admission.py | 58 | see below |
| tests/test_phase2_admission_denials.py | 106 | see below |
| tests/test_phase2_admission_deployment_workflow.py | 20 | see below |
| tests/test_phase2_admission_handler.py | 12 | see below |
| tests/test_phase2_admission_records.py | 83 | see below |
| tests/test_phase2_dataset_registry.py | 40 | see below |
| tests/test_phase2_github_evidence.py | 14 | see below |
| tests/test_phase2_infrastructure.py | 37 | see below |
| tests/test_phase2_lambda_package.py | 11 | see below |
| tests/test_phase2_lineage_evidence.py | 12 | see below |
| tests/test_phase2_probe_tools.py | 35 | see below |
| tests/test_phase2_submission.py | 97 | see below |
| tests/test_phase2_submit_run_workflow.py | 121 | see below |

**A green suite is not evidence that the path works.** Phase 1 shipped one over a workflow that could not complete a run, because every assertion compared the literal text of expressions rather than checking whether they named anything real. The counts above say the tests pass; `negative-case-matrix.md` says what they establish, which for eight of this phase's criteria is not the criterion.

## Failures

- tests/test_phase2_lambda_package.py::test_the_released_zip_is_the_one_this_tree_builds
