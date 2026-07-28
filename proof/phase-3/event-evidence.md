# Phase 3 EventBridge delivery evidence

**This document is empty, and it is empty because nothing has produced what it records.** The stacks are applied and four runs have completed, so the reason is no longer that the phase is undeployed -- it is that the observations this document exists to hold are not observations an ordinary run produces. What would produce them is listed below. It is generated empty rather than omitted because a bundle missing a document reads as a phase with fewer claims, and a reviewer counting what is here should count this too.

## What this document records

The EventBridge deliveries, the event ids derived from them, and the captured refusal of the replayed duplicate.

## What would fill it

- The delivery record for at least one job state change, with EventBridge's own event id beside the `evt_`-prefixed id derived from it.
- One event redelivered, and the conditional write's refusal captured as the error S3 returned.

## Criteria waiting on it

| criterion | status today |
| --- | --- |
| 11 | a gap |
| 18 | a gap |

Each of those is recorded in `src/edullm_platform/phase3_criteria.py` with the same account of what is missing, and `uv run python tools/validate_phase3.py` reports it. This document and that definition are two views of one fact rather than two claims.
