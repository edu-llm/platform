# Phase 3 Batch execution evidence

**This document is empty, and it is empty for one reason.** Wave 5 is held: no Phase 3 stack has been applied to this account, no compute environment or job queue exists, and no Batch job has ever run here. There is nothing to record. It is generated empty rather than omitted because a bundle missing a document reads as a phase with fewer claims, and a reviewer counting what is here should count this too.

## What this document records

The successful and failed Batch job ids, their compute environment, queue and job definition, the attempts array, the container exit codes, and the instance each job actually ran on.

## What would fill it

- One accepted run carried through to SUCCEEDED, and one whose command exits non-zero carried through to FAILED.
- `aws batch describe-jobs` for each, captured and sanitized by field projection rather than by scanning afterwards: a Batch job detail carries the full container command and environment.

## Criteria waiting on it

| criterion | status today |
| --- | --- |
| 1 | a gap |
| 4 | a gap |
| 15 | a gap |
| 16 | a gap |

Each of those is recorded in `src/edullm_platform/phase3_criteria.py` with the same account of what is missing, and `uv run python tools/validate_phase3.py` reports it. This document and that definition are two views of one fact rather than two claims.
