# Phase 3 rollback rehearsal

**The rehearsal has not been performed.** Rolling back this phase means disabling the job queue, letting the compute environment drain to zero desired vCPUs, removing the reviewers from both GitHub environments, and redeploying the states role without `batch:SubmitJob`. Each of those has been written down; none has been executed, and a rollback nobody has run is a plan rather than a rehearsal.

What would make it a rehearsal rather than a description is recording four things: that a submission dispatched after the queue is disabled creates no Batch job; that a job already running still reaches a terminal state and still lands its result record; that `desiredvCpus` is observed at zero afterwards rather than assumed; and that a record written before the rollback is still readable after it.

## Why no check is waiting on this

This document used to carry the check that the compute environment holds no capacity when idle, on the reasoning that draining it was part of the rollback. That check closed a different way: the environment was observed at zero desired vCPUs after four runs had finished, in the ordinary course of running them, which is the same reading taken without tearing anything down.

So the rehearsal is still worth doing, and nothing in the acceptance list is waiting for it. That is recorded here rather than quietly dropped, because work nobody is blocked on is exactly the kind that stops being done and then stops being remembered.

| fact | value |
| --- | --- |
| rehearsal performed | **no** |
| desired vCPUs when last observed | 0 |
| observed | 2026-07-28 |
