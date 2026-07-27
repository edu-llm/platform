# Phase 3 denial matrices

Two matrices, one per identity, and neither has ever run. The admission matrix needs a real admission session, which needs a dispatched submission through a protected environment; the workload matrix runs from inside the container under the job role, so it cannot run before a job does. Both are written, wired and tested against recorded CLI output, and both are claims about templates until a session answers them.

That distinction is the whole reason these matrices exist. Every other test of these roles reads a committed CloudFormation template, which is what the account was asked for rather than what it holds -- and a role widened in the console leaves every one of them green.

## The admission session, attempted before the one call it may make

Run in `submit-run.yml` under the environment-scoped session, after the approval gate and immediately before `StartExecution`. `batch:SubmitJob` was probed in Phase 1 against a queue that did not exist; the other three were hypothetical until this phase gave the account a queue, a job definition and jobs to describe.

| action | why a permitted call would still change nothing |
| --- | --- |
| `batch:SubmitJob` | the queue and the job definition named do not exist |
| `batch:TerminateJob` | the job id is well formed and nothing minted it |
| `batch:RegisterJobDefinition` | it would create a definition under this project's own denial-probe name, which nothing submits to |
| `batch:DescribeJobs` | a describe of an absent job reads nothing |

## The workload session, attempted from inside the container

| action | what a refusal establishes |
| --- | --- |
| `s3:PutObject` on the lineage bucket | the workload cannot forge an intent, a decision or a binding. Aimed at the real bucket with `--if-none-match '*'`, because an invented bucket is answered `NoSuchBucket` before anybody is authorized |
| `batch:SubmitJob` | a workload cannot launch compute outside admission |
| `states:StartExecution` | a workload cannot start an admission execution of its own |
| `ecr:PutImage` | a workload cannot publish an image. Aimed at a repository beside the registered one, never at it |

Both lists are read from `edullm_platform.batch_denials` rather than written here, so adding a probe or renaming an action changes this document rather than leaving it behind. Today they are four and four actions respectively.

## What choosing a probe has cost

Read this before adding one. Each entry is a rule some probe broke, with what taught it, because a rule with no incident attached reads as caution and gets skipped. Phase 1's list and Phase 2's both still apply; these are what the Batch and workload matrices added. Neither was learned from a run -- there are no credentials in the environment they were written in -- so each records what the templates and the services' documented behaviour say, and names the way it would fail if that turns out to be wrong.

### A read whose absent target answers with an empty result rather than an error is the strongest probe available, and it is worth going looking for one.

**Learned from.** Choosing the batch:DescribeJobs probe. Every other entry in these two matrices trades something -- an unmeasured assumption about authorization order, or a permitted call that creates an object. DescribeJobs trades nothing: an absent job id comes back as an empty jobs array with exit status zero, so a permitted call is unambiguous and inert at the same time.

Phase 1's first lesson is usually read as a warning, and it is also a search criterion. The lesson says a probe whose target may not exist can be answered by existence instead of by authorization; the corollary is that an action whose absent target is answered by an empty result rather than by an error cannot be, because there is no not-found path for it to take.

Three of the four Phase 3 admission probes had to accept a cost, and the reason this one did not is that it is a list-shaped read. When a matrix has to cover a service, the read actions are worth enumerating before the write ones: several of them have this property, and the entry that uses one is the entry that will still be conclusive in a year.

### A probe that would create something is written down as one, with what bounds it and what does not.

**Learned from.** The batch:RegisterJobDefinition probe. Batch has no dry run for it, and every form of the call that reaches authorization also registers a revision if it is allowed.

This is Phase 2's s3:PutObject lesson meeting a second service, and the answer is the same shape. What is bounded: the name is under this project's prefix and says what it is; no job queue references the definition, so nothing can be run on it; and batch:DeregisterJobDefinition removes it. What is not bounded: one revision exists until somebody deregisters it, and Batch keeps deregistered revisions visible, so the trace is permanent even after the cleanup.

The alternative considered and rejected was a deliberately malformed container-properties document, so that a permitted call would fail after authorization. Whether Batch validates the document before or after it authorizes the request has not been measured, and guessing wrong makes the entry permanently unprovable rather than merely costly -- which is the trade Phase 2 already refused when it chose a real conditional write over a deliberately wrong content digest.

### When a probe must aim away from the real resource to stay inert, the claim it can make gets narrower, and the narrower claim is what goes in the record.

**Learned from.** The workload matrix's states:StartExecution probe. Aiming it at the real admission state machine would start an admission execution if the role were widened, so it names a machine beside it that nothing creates.

What the probe proves is that the workload role cannot start that ARN. What the criterion wants is that it cannot start anything. The two coincide today because the workload role's policy names no states action at all, so the refusal is an implicit deny that would answer the same for any ARN -- but a role widened to hold states:StartExecution on the admission machine alone would be refused here and reported as narrow.

The gap is recorded rather than closed because closing it means a permitted probe starting a real execution of the machine that admits runs. Phase 1's framing applies unchanged: a weaker claim that is always safe beats a stronger one bought with a call that does something.

## Why this document is not evidence yet

**This document is empty, and it is empty for one reason.** Wave 5 is held: no Phase 3 stack has been applied to this account, no compute environment or job queue exists, and no Batch job has ever run here. There is nothing to record. It is generated empty rather than omitted because a bundle missing a document reads as a phase with fewer claims, and a reviewer counting what is here should count this too.

Criteria 12 and 13 rest on it and are gaps. What fills it is one live run of each matrix, its record uploaded as a workflow artifact, committed under `fixtures/evidence/phase-3/`, and a test that reads it -- with the CloudTrail event id of each refusal, so a reviewer can look any of them up in the account.
