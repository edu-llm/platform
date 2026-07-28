# Phase 2 deployed-role drift

**This document is empty, and it is empty for one reason.** What it would describe has already happened, and nothing captured it. Phase 2's path went end to end on 2026-07-27, and its three roles were deployed from a laptop the same day; what `tools/capture_phase2_evidence.py` records is the state that left behind -- the lineage objects, the execution list, the GitHub configuration -- and not the artifact this document is for. It is generated empty rather than omitted because a bundle missing a document reads as a phase with fewer claims, and a reviewer counting what is here should count this too.

## What this document records

The three roles Phase 2 creates and the amended deployer, each compared against the committed template that declares it, in both directions.

## What would fill it

- The three Phase 2 roles captured from the account and committed, then added to `role_drift.COMMITTED_ROLE_TEMPLATES` so the comparison Phase 1 runs for its two roles runs for these as well.
- A comparison that re-runs, in place of the one somebody did once by eye. Both roles behind criterion 19 were read back from IAM by hand on 2026-07-27 and matched, with the Lambda role carrying CloudWatch Logs on its own log group and no S3 action of any kind. Reading a role by hand establishes what was true that afternoon; nothing re-checks it, and that difference is the whole of what this document is for.

## Criteria waiting on it

| criterion | status today |
| --- | --- |
| 6 | a gap |
| 19 | a gap |

Each of those is recorded in `src/edullm_platform/phase2_criteria.py` with the same account of what is missing, and `uv run python tools/validate_phase2.py` reports it. This document and that definition are two views of one fact rather than two claims.
