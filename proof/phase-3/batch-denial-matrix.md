# Phase 3 denial matrices

Two matrices, one per identity, and they are in different states. The admission matrix has run: it executes inside the submit job against a real admission session issued through a protected environment, before the one call that session makes, and every completed submission passed it. The workload matrix has not, because it runs from inside the container under the job role, and every command run there so far has printed a line and exited.

Having run is not the same as being recorded here. The admission matrix writes its result to a GitHub Actions artifact with a thirty-day retention, which is somewhere this repository does not read and cannot cite, so the check that rests on it stays open until the artifact is captured into the evidence tree and a test reads it.

That distinction is the whole reason these matrices exist. Every other test of these roles reads a committed CloudFormation template, which is what the account was asked for rather than what it holds -- and a role widened in the console leaves every one of them green. The four roles this phase creates are now also captured from the account and compared, which closes that gap for them; the matrices remain the only thing that shows AWS refusing a call rather than a policy declining to permit one.

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

The two halves fall short for different reasons and neither is that the phase is undeployed. The admission matrix has run against real sessions and its result is a GitHub Actions artifact this repository cannot cite; the workload matrix has never run, because it executes inside the container and no command run there has invoked it.

Criteria 12 and 13 rest on this and are gaps. What fills it is each matrix's record committed under `fixtures/evidence/phase-3/` and a test that reads it -- with the CloudTrail event id of each refusal, so a reviewer can look any of them up in the account. For the admission half that is a capture of an artifact that already exists; for the workload half it is a container image carrying the probe, which this repository does not build.
