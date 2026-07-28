# Phase 3 deployed-role drift

**This document is empty, and it is empty for one reason.** Wave 5 is held: no Phase 3 stack has been applied to this account, no compute environment or job queue exists, and no Batch job has ever run here. There is nothing to record. It is generated empty rather than omitted because a bundle missing a document reads as a phase with fewer claims, and a reviewer counting what is here should count this too.

## What this document records

The four new roles and the two amendments, compared against the templates that declare them.

## What would fill it

- The four Phase 3 roles deployed from a laptop, then captured with `tools/capture_phase3_evidence.py` and committed.
- The two amended roles re-captured. The Phase 1 deployer capture is behind its template today and the difference is recorded as a pending amendment in `edullm_platform.pending_amendments`; that record has to be deleted in the same change as the re-capture, because its findings are compared for equality.

## Criteria waiting on it

| criterion | status today |
| --- | --- |
| 13 | a gap |
| 14 | a gap |

Each of those is recorded in `src/edullm_platform/phase3_criteria.py` with the same account of what is missing, and `uv run python tools/validate_phase3.py` reports it. This document and that definition are two views of one fact rather than two claims.
