# Phase 3 cancellation and timeout evidence

**This document is empty, and it is empty for one reason.** Wave 5 is held: no Phase 3 stack has been applied to this account, no compute environment or job queue exists, and no Batch job has ever run here. There is nothing to record. It is generated empty rather than omitted because a bundle missing a document reads as a phase with fewer claims, and a reviewer counting what is here should count this too.

## What this document records

The cancelled single job, the cancelled fan-out with both children, and the timed-out job, each with the reason Batch recorded.

## What would fill it

- A cancellation path that can terminate a job. There is none: every Phase 3 role deliberately excludes `batch:TerminateJob`, and the state machine the plan routes cancellation through has not been written. This section needs a component built before it needs a run.
- A two-cell array job terminated at the parent, with both child job ids observed terminal rather than the parent alone.
- A job whose command sleeps past `attemptDurationSeconds`, observed FAILED with the timeout reason.

## Criteria waiting on it

| criterion | status today |
| --- | --- |
| 5 | a gap |
| 6 | a gap |
| 7 | a gap |
| 8 | a gap |

Each of those is recorded in `src/edullm_platform/phase3_criteria.py` with the same account of what is missing, and `uv run python tools/validate_phase3.py` reports it. This document and that definition are two views of one fact rather than two claims.
