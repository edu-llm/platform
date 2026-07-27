# Phase 3 rollback rehearsal

**This document is empty, and it is empty for one reason.** Wave 5 is held: no Phase 3 stack has been applied to this account, no compute environment or job queue exists, and no Batch job has ever run here. There is nothing to record. It is generated empty rather than omitted because a bundle missing a document reads as a phase with fewer claims, and a reviewer counting what is here should count this too.

## What this document records

The rollback executed rather than argued: the job queue disabled, the compute environment observed at zero desired vCPUs, the reviewers removed from both GitHub environments, and the states role redeployed without `batch:SubmitJob`.

## What would fill it

- The rehearsal, recording the four things that make it a rehearsal rather than a description: that a submission dispatched after step 1 creates no Batch job; that a job running at step 1 still reaches a terminal state and still lands its result record; that `desiredvCpus` is observed at 0 after step 2 rather than assumed; and that a record written before step 1 is still readable afterwards.

## Criteria waiting on it

| criterion | status today |
| --- | --- |
| 16 | a gap |

Each of those is recorded in `src/edullm_platform/phase3_criteria.py` with the same account of what is missing, and `uv run python tools/validate_phase3.py` reports it. This document and that definition are two views of one fact rather than two claims.
