# Phase 3 lineage record evidence

**This document is empty, and it is empty for one reason.** Wave 5 is held: no Phase 3 stack has been applied to this account, no compute environment or job queue exists, and no Batch job has ever run here. There is nothing to record. It is generated empty rather than omitted because a bundle missing a document reads as a phase with fewer claims, and a reviewer counting what is here should count this too.

## What this document records

The binding, event, attempt and result URIs with their VersionId and ChecksumSHA256, joined to the Phase 2 intent and decision for the same run id.

## What would fill it

- `aws s3api head-object --checksum-mode ENABLED` for the binding, one event, the attempt and the result.
- The intent and decision records Phase 2 wrote for the same run id, so the join is shown rather than asserted.

## Criteria waiting on it

| criterion | status today |
| --- | --- |
| 3 | a gap |
| 17 | a gap |
| 19 | a gap |

Each of those is recorded in `src/edullm_platform/phase3_criteria.py` with the same account of what is missing, and `uv run python tools/validate_phase3.py` reports it. This document and that definition are two views of one fact rather than two claims.
