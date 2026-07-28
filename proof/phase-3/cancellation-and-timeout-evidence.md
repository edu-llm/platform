# Phase 3 cancellation and timeout evidence

Two halves of one document and they are in opposite states. The timeout has been observed stopping a real job. Cancellation has not been observed at all, because there is nothing to observe: no component in this account may terminate a job.

## The timeout, which fired

| fact | value |
| --- | --- |
| run | `run_019fa9a6-4460-7095-a358-a1552e250f1b` |
| attempt duration sent to Batch | 180s |
| ran for | 213s |
| status | FAILED |
| reason Batch gave | Job attempt duration exceeded timeout |
| container exit | none — the scheduler stopped it |

The absent exit code is the load-bearing part. A job the scheduler killed never got to return a status, so anything in that field would mean the command finished on its own and the timeout was a coincidence.

## Cancellation, which does not exist

Every Phase 3 role deliberately excludes `batch:TerminateJob`, and the state machine the plan routes cancellation through has not been written. So this half needs a component built before it needs a run, and the three checks waiting on it say so rather than describing a capture somebody could take.

The bound that makes the absence survivable is the timeout above. With a mandatory attempt duration in force and demonstrably enforced, the cost of being unable to cancel is the remainder of one job rather than an open-ended amount.

Cancelling the GitHub workflow does not stop the Batch job. The submit job records that where an operator will read it rather than implying otherwise by silence, and it is on the pilot limitations page in those words.
