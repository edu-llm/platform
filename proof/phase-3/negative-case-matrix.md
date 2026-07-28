# Phase 3 negative-case matrix

The 22 Phase 3 acceptance criteria, mapped to the tests cited for each one by node id. Each cited node id was collected and executed by this generator before the bundle was written; a citation pytest cannot collect aborts generation rather than being printed.

This mapping is defined once, in `src/edullm_platform/phase3_criteria.py`. The acceptance gate reads the same definition and executes the same node ids, so this matrix and `tools/validate_phase3.py` cannot disagree.

Verification run: 267 tests executed, 267 passed, 0 failed, 0 errored, pytest exit code 0.

Three statuses exist and no more. **COVERED** means one or more cited tests prove the criterion as stated against the shipped configuration and all of them pass; the gate passes it. **DEFERRED** means an explicit recorded decision not to satisfy it yet, which requires both a written reason and a written trigger describing what makes it live again; the gate passes it. **GAP** is everything else, and the gate fails it. There is no in-between status, because an in-between status is what lets a gate be green and wrong at the same time.

`proving` tests prove the criterion as stated against the shipped configuration; only a COVERED criterion may cite one. `supporting` tests are cited evidence that does not amount to proof — either because they exercise the code path under a synthetic configuration that is not what ships, or because they prove only part of the claim. Both kinds are executed. A supporting citation that is renamed or deleted still fails the criterion.

| # | status | proving | supporting | check |
| --- | --- | --- | --- | --- |
| 1 | GAP | 0 | 3 | A valid run reaches SUCCEEDED. |
| 2 | GAP | 0 | 2 | Stdout and stderr are available through the recorded log stream. |
| 3 | GAP | 0 | 2 | The S3 result manifest matches the logical run and the Batch job. |
| 4 | GAP | 0 | 2 | A failed command reaches FAILED with its reason preserved. |
| 5 | GAP | 0 | 4 | Cancellation is authorized, applied, and recorded. |
| 6 | GAP | 0 | 3 | Cancelling the GitHub workflow forwards cancellation to the running job. |
| 7 | GAP | 0 | 3 | Cancelling a fan-out stops every child, not only the parent. |
| 8 | GAP | 0 | 7 | A mandatory timeout terminates a runaway job. |
| 9 | GAP | 0 | 5 | An invalid queue, job definition, role or override is rejected before submission. |
| 10 | GAP | 0 | 3 | Duplicate or ambiguous submission handling does not silently create an untracked job. |
| 11 | GAP | 0 | 5 | Event duplicates do not create conflicting terminal state. |
| 12 | GAP | 0 | 5 | No GitHub path can administer Batch or EC2. |
| 13 | GAP | 0 | 3 | The workload role cannot write to the lineage store or start anything. |
| 14 | GAP | 0 | 4 | The validator resolves the target and cannot submit; the state machine submits and cannot decide. |
| 15 | GAP | 0 | 3 | Exactly one compute profile is provisioned, and it is backed. |
| 16 | GAP | 0 | 1 | The compute environment holds no capacity when it is idle. |
| 17 | GAP | 0 | 3 | Every record written by this phase carries an S3-attested ChecksumSHA256 and a VersionId. |
| 18 | GAP | 0 | 6 | The EventBridge rule receives only our queue's events. |
| 19 | GAP | 0 | 4 | A run is traceable end to end by run id alone. |
| 20 | COVERED | 2 | 6 | The deployer's unscoped actions are exactly the measured ones, in two statements separated by why each is unscoped. |
| 21 | GAP | 0 | 7 | The networking the compute environment uses is recorded, with its terms. |
| 22 | COVERED | 4 | 6 | The image-scan decision has been answered. |

## Gaps

Read these first. A matrix that overstates coverage is worse than no matrix. Every gap here fails the acceptance gate, and each one is unfinished work rather than a recorded decision to postpone: a deferral needs a written reason and a written trigger, and neither exists for any of these. Relabelling them would turn the gate green without anything changing in the account, which is the one thing this matrix exists to make impossible to do quietly.

### Check 1 (GAP) — A valid run reaches SUCCEEDED.

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- No test substitutes for this one. What the cited tests prove is that the platform would submit the right job and would read a SUCCEEDED event as a success; the criterion is that a container ran, which only a captured Batch job detail with status SUCCEEDED, its exit code and the run id joining it to the intent and decision records can establish.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

### Check 2 (GAP) — Stdout and stderr are available through the recorded log stream.

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- The mutation this criterion exists to catch is recording the log *group* rather than the stream: it reads as complete and resolves to no single job. Only fetching a recorded stream back and finding the line the container printed distinguishes the two, and no container has printed one.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

### Check 3 (GAP) — The S3 result manifest matches the logical run and the Batch job.

- This is the one criterion of the twenty that needs no live call, and it still cannot be proved: it is a test over committed captures, and nothing is committed under fixtures/evidence/phase-3/ except the account measurements. The cited test proves the three joins hold in a projection built from a synthetic event, which is the mechanism rather than the record.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

### Check 4 (GAP) — A failed command reaches FAILED with its reason preserved.

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- The mutation is projecting every terminal state to succeeded, which the cited test does catch. What it cannot establish is that Batch reports a non-zero container exit the way this projection reads it, which needs a job that deliberately exits non-zero and a captured detail carrying its statusReason and exit code.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

### Check 5 (GAP) — Cancellation is authorized, applied, and recorded.

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- Nothing in this account may terminate a job today, and that is not only a deploy away. The plan routes cancellation through a state machine that holds batch:TerminateJob; no such state machine is written, and every role Phase 3 declares deliberately excludes the action. So this criterion needs a component built as well as a run observed.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

### Check 6 (GAP) — Cancelling the GitHub workflow forwards cancellation to the running job.

- It does not forward it, and the workflow now says so where an operator will read it. The submit job's if: cancelled() step records the run id and points at the runbook; it stops nothing, because the admission role holds no batch:TerminateJob and the cancellation state machine the plan describes has not been built.
- Even once it is built, this check is as much about GitHub's grace period being long enough as about the wiring, and the grace period is bounded, not configurable, and not guaranteed to be reached at all. That half can only be answered by cancelling a real dispatched run mid-job.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

### Check 7 (GAP) — Cancelling a fan-out stops every child, not only the parent.

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- The mutation is asserting only the parent, which is what a single DescribeJobs on the parent id returns and which would pass while both children ran on. Distinguishing them needs a two-cell array job, terminated at the parent, with both child job ids observed terminal.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

### Check 8 (GAP) — A mandatory timeout terminates a runaway job.

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- The testable half is done and is the half that usually rots: the submit request always carries a Timeout, for every fixture including the one with no explicit runtime, so making the block conditional fails. What is unproved is that Batch acts on it -- a job whose command sleeps past attemptDurationSeconds, observed FAILED with the timeout reason.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

### Check 9 (GAP) — An invalid queue, job definition, role or override is rejected before submission.

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- The refusal is proved locally and the seam between the catalog and the targets file is read from both sides. What is unproved is that it happens inside AWS: a manifest naming a priced-but-unprovisioned profile reaching a decision with accepted false, and no Batch job existing for that run id.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

### Check 10 (GAP) — Duplicate or ambiguous submission handling does not silently create an untracked job.

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- Three mechanisms, each of which has to be observed separately: ExecutionAlreadyExists on a second start with the same name, a 412 PreconditionFailed on the binding's conditional write, and the Batch job name being the run id so a second job would be visible. The mutation that defeats all three at once is minting a fresh run id inside AWS, which no local test can see.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

### Check 11 (GAP) — Event duplicates do not create conflicting terminal state.

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- The derivation is proved -- the same event projects to byte-identical bytes, the id comes from EventBridge rather than being minted, and deduplicate_lifecycle_events raises on same-id-different-content. What is unproved is the store's half: the same event redelivered and the conditional write refusing it, captured.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

### Check 12 (GAP) — No GitHub path can administer Batch or EC2.

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- The matrix is written, wired into the submit job before the one call that session makes, and attempts all four actions Phase 3 makes meaningful. It has never run: it needs a real admission session, which needs a dispatched submission through a protected environment. Until then this criterion rests on templates, and a role widened in the console leaves every one of them green.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

### Check 13 (GAP) — The workload role cannot write to the lineage store or start anything.

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- A citation here reads a committed CloudFormation template, which is what the account will be asked for rather than what it holds. The four Phase 3 roles are registered in role_drift.PHASE3_ROLE_TEMPLATES and none has been deployed, so the comparison that catches a role widened in the console has nothing to compare.
- The workload matrix runs from inside the container under the job role, so it cannot run before a job does. The mutation it exists to catch -- widening the workload role's S3 scope to the bucket rather than the prefix -- is caught by the template test today and would not be caught by it after somebody edited the deployed role in the console.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

### Check 14 (GAP) — The validator resolves the target and cannot submit; the state machine submits and cannot decide.

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- A citation here reads a committed CloudFormation template, which is what the account will be asked for rather than what it holds. The four Phase 3 roles are registered in role_drift.PHASE3_ROLE_TEMPLATES and none has been deployed, so the comparison that catches a role widened in the console has nothing to compare.
- The separation is proved of the committed templates and of the ASL. The criterion is about deployed roles, and the mutation -- giving the Lambda batch:SubmitJob, which would work and would move the launch out of the execution history -- is exactly the kind a console edit makes and a template test cannot see.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

### Check 15 (GAP) — Exactly one compute profile is provisioned, and it is backed.

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- The seam is closed: exactly one profile is provisioned in the catalog and exactly one target backs it, compared from both files. 'Backed' also means the environment exists and is usable, which is a DescribeComputeEnvironments showing it VALID and ENABLED -- and a VALID environment is still not evidence a job can run, which is why criterion 1 is separate from this one.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

### Check 16 (GAP) — The compute environment holds no capacity when it is idle.

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- minvCpus is 0 in the template and the test fails if it is raised. The criterion is about the account: desiredvCpus observed at 0 after the live matrix has finished, which is the reading that would catch an environment that scaled up and did not come back down.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

### Check 17 (GAP) — Every record written by this phase carries an S3-attested ChecksumSHA256 and a VersionId.

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- The writers ask for the checksum; whether S3 attested one is a fact about the object. It needs HeadObject --checksum-mode ENABLED against the binding, one event, the attempt and the result, captured. This is distinct from the canonical manifest hash and has to be recorded as such, because a reader who conflated them would think one proved the other.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

### Check 18 (GAP) — The EventBridge rule receives only our queue's events.

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- The pattern names the queue the compute stack creates, compared across both files, and the projection refuses a delivery that is not ours as a second line. The criterion also asks that no lifecycle record exist whose run id is not ours, which is a statement about what the deployed rule actually delivered in a shared account.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

### Check 19 (GAP) — A run is traceable end to end by run id alone.

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- This is the gate restated as an executable assertion and it is the one check that fails if any other passes for the wrong reason: one run id resolving to eleven artifacts -- a GitHub run URL, a CloudTrail AssumeRoleWithWebIdentity, a Step Functions execution, an intent, a decision, a binding, at least one event, an attempt, a result, a Batch job id and a log stream -- all present and all agreeing. Six of the eleven have never been written.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

### Check 21 (GAP) — The networking the compute environment uses is recorded, with its terms.

- The terms are not the ones the plan expected, and they are better. The plan assumed a borrowed VPC and called it the phase's largest known limitation; the L-F678F1CE quota increase from five to ten was filed and applied on 2026-07-27, and infra/batch-network.yaml creates our own VPC unconditionally. Ownership is settled, and the committed measurements record the quota, the request id, the zones and which of them offers the instance type.
- What is missing is the other half of the criterion's words: the networking the compute environment *uses*. The network stack is not deployed, so no VPC, subnet or security-group id exists to record, and the committed placement record describes the interim candidate VPC these probes were aimed at rather than one this project owns.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

## Checks

### Check 1 — A valid run reaches SUCCEEDED.

**Status: GAP**

Gap:

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- No test substitutes for this one. What the cited tests prove is that the platform would submit the right job and would read a SUCCEEDED event as a success; the criterion is that a container ran, which only a captured Batch job detail with status SUCCEEDED, its exit code and the run id joining it to the intent and decision records can establish.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

No test proves this check.

Supporting tests (3), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_execution.py::test_the_promoted_profile_resolves_to_the_deployed_queue_and_job_definition`
- `tests/test_phase3_execution.py::test_the_job_name_is_the_run_id_so_batch_is_a_third_join`
- `tests/test_phase3_lifecycle_projection.py::test_a_successful_run_records_where_its_output_went`

### Check 2 — Stdout and stderr are available through the recorded log stream.

**Status: GAP**

Gap:

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- The mutation this criterion exists to catch is recording the log *group* rather than the stream: it reads as complete and resolves to no single job. Only fetching a recorded stream back and finding the line the container printed distinguishes the two, and no container has printed one.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

No test proves this check.

Supporting tests (2), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_infrastructure.py::test_the_log_group_the_config_names_is_the_one_the_container_writes_to`
- `tests/test_phase3_infrastructure.py::test_execution_targets_config_names_exactly_what_the_templates_create`

### Check 3 — The S3 result manifest matches the logical run and the Batch job.

**Status: GAP**

Gap:

- This is the one criterion of the twenty that needs no live call, and it still cannot be proved: it is a test over committed captures, and nothing is committed under fixtures/evidence/phase-3/ except the account measurements. The cited test proves the three joins hold in a projection built from a synthetic event, which is the mechanism rather than the record.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

No test proves this check.

Supporting tests (2), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_lifecycle_projection.py::test_the_result_joins_to_the_attempt_and_the_attempt_to_the_batch_job`
- `tests/test_phase3_lifecycle_projection.py::test_one_attempt_gets_one_id_whichever_event_describes_it`

### Check 4 — A failed command reaches FAILED with its reason preserved.

**Status: GAP**

Gap:

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- The mutation is projecting every terminal state to succeeded, which the cited test does catch. What it cannot establish is that Batch reports a non-zero container exit the way this projection reads it, which needs a job that deliberately exits non-zero and a captured detail carrying its statusReason and exit code.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

No test proves this check.

Supporting tests (2), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_lifecycle_projection.py::test_a_failed_run_records_the_failure_rather_than_the_nearest_success`
- `tests/test_phase3_lifecycle_projection.py::test_a_job_stopped_before_any_attempt_began_still_records_that_it_stopped`

### Check 5 — Cancellation is authorized, applied, and recorded.

**Status: GAP**

Gap:

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- Nothing in this account may terminate a job today, and that is not only a deploy away. The plan routes cancellation through a state machine that holds batch:TerminateJob; no such state machine is written, and every role Phase 3 declares deliberately excludes the action. So this criterion needs a component built as well as a run observed.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

No test proves this check.

Supporting tests (4), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_lifecycle_projection.py::test_a_termination_this_platform_asked_for_is_recorded_as_cancelled`
- `tests/test_phase3_lifecycle_projection.py::test_a_failure_that_merely_mentions_cancellation_is_still_a_failure`
- `tests/test_phase3_lifecycle_projection.py::test_a_termination_from_outside_this_platform_understates_rather_than_guesses`
- `tests/test_phase3_infrastructure.py::test_the_states_role_gains_batch_and_ecr_reads_and_no_way_to_stop_a_job`

### Check 6 — Cancelling the GitHub workflow forwards cancellation to the running job.

**Status: GAP**

Gap:

- It does not forward it, and the workflow now says so where an operator will read it. The submit job's if: cancelled() step records the run id and points at the runbook; it stops nothing, because the admission role holds no batch:TerminateJob and the cancellation state machine the plan describes has not been built.
- Even once it is built, this check is as much about GitHub's grace period being long enough as about the wiring, and the grace period is bounded, not configurable, and not guaranteed to be reached at all. That half can only be answered by cancelling a real dispatched run mid-job.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

No test proves this check.

Supporting tests (3), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase2_submit_run_workflow.py::test_the_cancellation_step_runs_only_on_a_cancellation_and_last`
- `tests/test_phase2_submit_run_workflow.py::test_the_cancellation_step_neither_claims_to_stop_a_job_nor_can`
- `tests/test_phase2_submit_run_workflow.py::test_the_cancellation_notice_is_written_where_a_person_will_find_it`

### Check 7 — Cancelling a fan-out stops every child, not only the parent.

**Status: GAP**

Gap:

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- The mutation is asserting only the parent, which is what a single DescribeJobs on the parent id returns and which would pass while both children ran on. Distinguishing them needs a two-cell array job, terminated at the parent, with both child job ids observed terminal.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

No test proves this check.

Supporting tests (3), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_execution.py::test_a_fan_out_submits_its_size_and_nothing_else_changes`
- `tests/test_phase3_execution.py::test_a_single_container_submits_no_array_properties`
- `tests/test_phase3_infrastructure.py::test_a_fan_out_binding_records_its_size_and_a_single_container_records_none`

### Check 8 — A mandatory timeout terminates a runaway job.

**Status: GAP**

Gap:

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- The testable half is done and is the half that usually rots: the submit request always carries a Timeout, for every fixture including the one with no explicit runtime, so making the block conditional fails. What is unproved is that Batch acts on it -- a job whose command sleeps past attemptDurationSeconds, observed FAILED with the timeout reason.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

No test proves this check.

Supporting tests (7), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_execution.py::test_every_submit_carries_a_timeout_including_the_shortest_manifest`
- `tests/test_phase3_execution.py::test_the_timeout_sent_to_batch_is_the_manifest_runtime_in_seconds[1-3600]`
- `tests/test_phase3_execution.py::test_the_timeout_sent_to_batch_is_the_manifest_runtime_in_seconds[2-7200]`
- `tests/test_phase3_execution.py::test_the_timeout_sent_to_batch_is_the_manifest_runtime_in_seconds[13-46800]`
- `tests/test_phase3_execution.py::test_the_timeout_sent_to_batch_is_the_manifest_runtime_in_seconds[0.5-1800]`
- `tests/test_phase3_execution.py::test_the_runtime_bound_is_rounded_down_rather_than_up`
- `tests/test_phase3_infrastructure.py::test_the_job_definition_carries_a_timeout_and_a_retry_floor_of_its_own`

### Check 9 — An invalid queue, job definition, role or override is rejected before submission.

**Status: GAP**

Gap:

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- The refusal is proved locally and the seam between the catalog and the targets file is read from both sides. What is unproved is that it happens inside AWS: a manifest naming a priced-but-unprovisioned profile reaching a decision with accepted false, and no Batch job existing for that run id.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

No test proves this check.

Supporting tests (5), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_execution.py::test_every_provisioned_profile_is_backed_and_every_target_names_a_provisioned_one`
- `tests/test_phase3_execution.py::test_a_priced_but_unprovisioned_profile_has_nowhere_to_go`
- `tests/test_phase3_execution.py::test_an_unprovisioned_profile_is_a_refusal_rather_than_a_crash`
- `tests/test_phase3_execution.py::test_the_two_ways_of_having_nowhere_to_run_are_distinguishable_in_the_record`
- `tests/test_phase3_infrastructure.py::test_execution_targets_config_names_exactly_what_the_templates_create`

### Check 10 — Duplicate or ambiguous submission handling does not silently create an untracked job.

**Status: GAP**

Gap:

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- Three mechanisms, each of which has to be observed separately: ExecutionAlreadyExists on a second start with the same name, a 412 PreconditionFailed on the binding's conditional write, and the Batch job name being the run id so a second job would be visible. The mutation that defeats all three at once is minting a fresh run id inside AWS, which no local test can see.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

No test proves this check.

Supporting tests (3), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_execution.py::test_the_job_name_is_the_run_id_so_batch_is_a_third_join`
- `tests/test_phase3_infrastructure.py::test_the_binding_write_is_conditional_and_checksummed_like_every_lineage_write`
- `tests/test_phase2_submit_run_workflow.py::test_an_execution_that_already_exists_under_this_run_id_is_a_success`

### Check 11 — Event duplicates do not create conflicting terminal state.

**Status: GAP**

Gap:

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- The derivation is proved -- the same event projects to byte-identical bytes, the id comes from EventBridge rather than being minted, and deduplicate_lifecycle_events raises on same-id-different-content. What is unproved is the store's half: the same event redelivered and the conditional write refusing it, captured.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

No test proves this check.

Supporting tests (5), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_lifecycle_projection.py::test_a_replayed_event_projects_to_a_byte_identical_record`
- `tests/test_phase3_lifecycle_projection.py::test_the_event_id_is_the_eventbridge_id_and_is_a_legal_event_id`
- `tests/test_phase3_lifecycle_projection.py::test_two_deliveries_of_one_event_deduplicate_rather_than_conflict`
- `tests/test_phase3_lifecycle_projection.py::test_a_redelivered_event_is_refused_by_the_store_and_that_is_success`
- `tests/test_phase3_infrastructure.py::test_the_recorder_role_holds_no_batch_action_at_all`

### Check 12 — No GitHub path can administer Batch or EC2.

**Status: GAP**

Gap:

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- The matrix is written, wired into the submit job before the one call that session makes, and attempts all four actions Phase 3 makes meaningful. It has never run: it needs a real admission session, which needs a dispatched submission through a protected environment. Until then this criterion rests on templates, and a role widened in the console leaves every one of them green.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

No test proves this check.

Supporting tests (5), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase2_submit_run_workflow.py::test_the_submit_job_gained_no_aws_capability_when_phase_three_arrived`
- `tests/test_phase2_submit_run_workflow.py::test_the_batch_matrix_attempts_every_action_phase_three_makes_meaningful`
- `tests/test_phase2_submit_run_workflow.py::test_the_workflow_makes_exactly_these_aws_calls_in_exactly_this_order`
- `tests/test_phase3_batch_denials.py::test_each_matrix_names_the_role_it_is_a_claim_about`
- `tests/test_phase3_batch_deployment_workflow.py::test_the_workflow_never_submits_a_job_and_never_asks_to`

### Check 13 — The workload role cannot write to the lineage store or start anything.

**Status: GAP**

Gap:

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- A citation here reads a committed CloudFormation template, which is what the account will be asked for rather than what it holds. The four Phase 3 roles are registered in role_drift.PHASE3_ROLE_TEMPLATES and none has been deployed, so the comparison that catches a role widened in the console has nothing to compare.
- The workload matrix runs from inside the container under the job role, so it cannot run before a job does. The mutation it exists to catch -- widening the workload role's S3 scope to the bucket rather than the prefix -- is caught by the template test today and would not be caught by it after somebody edited the deployed role in the console.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

No test proves this check.

Supporting tests (3), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_infrastructure.py::test_the_workload_role_can_neither_reach_lineage_nor_start_anything`
- `tests/test_phase3_infrastructure.py::test_the_workload_role_writes_only_under_its_own_team_prefix`
- `tests/test_phase3_batch_denials.py::test_a_repository_outside_this_project_is_a_setup_failure`

### Check 14 — The validator resolves the target and cannot submit; the state machine submits and cannot decide.

**Status: GAP**

Gap:

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- A citation here reads a committed CloudFormation template, which is what the account will be asked for rather than what it holds. The four Phase 3 roles are registered in role_drift.PHASE3_ROLE_TEMPLATES and none has been deployed, so the comparison that catches a role widened in the console has nothing to compare.
- The separation is proved of the committed templates and of the ASL. The criterion is about deployed roles, and the mutation -- giving the Lambda batch:SubmitJob, which would work and would move the launch out of the execution history -- is exactly the kind a console edit makes and a template test cannot see.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

No test proves this check.

Supporting tests (4), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_infrastructure.py::test_the_states_role_gains_batch_and_ecr_reads_and_no_way_to_stop_a_job`
- `tests/test_phase3_infrastructure.py::test_the_validator_payload_is_built_field_by_field_and_never_forwarded`
- `tests/test_phase3_infrastructure.py::test_submit_to_batch_passes_the_request_through_and_names_no_field_of_it`
- `tests/test_phase3_infrastructure.py::test_the_recorder_role_writes_lineage_and_cannot_make_anything_happen`

### Check 15 — Exactly one compute profile is provisioned, and it is backed.

**Status: GAP**

Gap:

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- The seam is closed: exactly one profile is provisioned in the catalog and exactly one target backs it, compared from both files. 'Backed' also means the environment exists and is usable, which is a DescribeComputeEnvironments showing it VALID and ENABLED -- and a VALID environment is still not evidence a job can run, which is why criterion 1 is separate from this one.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

No test proves this check.

Supporting tests (3), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_execution.py::test_every_provisioned_profile_is_backed_and_every_target_names_a_provisioned_one`
- `tests/test_phase3_execution.py::test_every_target_names_infrastructure_this_project_owns`
- `tests/test_phase3_infrastructure.py::test_the_compute_environment_holds_no_capacity_when_it_is_idle`

### Check 16 — The compute environment holds no capacity when it is idle.

**Status: GAP**

Gap:

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- minvCpus is 0 in the template and the test fails if it is raised. The criterion is about the account: desiredvCpus observed at 0 after the live matrix has finished, which is the reading that would catch an environment that scaled up and did not come back down.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

No test proves this check.

Supporting tests (1), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_infrastructure.py::test_the_compute_environment_holds_no_capacity_when_it_is_idle`

### Check 17 — Every record written by this phase carries an S3-attested ChecksumSHA256 and a VersionId.

**Status: GAP**

Gap:

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- The writers ask for the checksum; whether S3 attested one is a fact about the object. It needs HeadObject --checksum-mode ENABLED against the binding, one event, the attempt and the result, captured. This is distinct from the canonical manifest hash and has to be recorded as such, because a reader who conflated them would think one proved the other.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

No test proves this check.

Supporting tests (3), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_infrastructure.py::test_the_binding_write_is_conditional_and_checksummed_like_every_lineage_write`
- `tests/test_phase3_lifecycle_projection.py::test_every_write_is_conditional_so_a_replay_cannot_overwrite_anything`
- `tests/test_phase3_lifecycle_projection.py::test_the_stored_bytes_are_the_canonical_ones_rather_than_a_re_encoding`

### Check 18 — The EventBridge rule receives only our queue's events.

**Status: GAP**

Gap:

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- The pattern names the queue the compute stack creates, compared across both files, and the projection refuses a delivery that is not ours as a second line. The criterion also asks that no lifecycle record exist whose run id is not ours, which is a statement about what the deployed rule actually delivered in a shared account.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

No test proves this check.

Supporting tests (6), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_infrastructure.py::test_the_event_rule_matches_the_job_queue_the_compute_stack_creates`
- `tests/test_phase3_infrastructure.py::test_the_queue_the_states_role_may_submit_to_is_the_queue_that_exists`
- `tests/test_phase3_infrastructure.py::test_the_queue_accepts_deliveries_only_from_our_own_rule_in_our_own_account`
- `tests/test_phase3_lifecycle_projection.py::test_a_delivery_that_is_not_ours_is_refused[foreign-source]`
- `tests/test_phase3_lifecycle_projection.py::test_a_delivery_that_is_not_ours_is_refused[foreign-detail-type]`
- `tests/test_phase3_lifecycle_projection.py::test_a_job_whose_name_is_not_a_run_id_is_refused`

### Check 19 — A run is traceable end to end by run id alone.

**Status: GAP**

Gap:

- Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute environment, no job queue, no job definition, no EventBridge rule, no recorder and no outputs bucket, and no Batch job has ever run here. Every artifact this criterion would be proved from is an observation of infrastructure that does not exist.
- This is the gate restated as an executable assertion and it is the one check that fails if any other passes for the wrong reason: one run id resolving to eleven artifacts -- a GitHub run URL, a CloudTrail AssumeRoleWithWebIdentity, a Step Functions execution, an intent, a decision, a binding, at least one event, an attempt, a result, a Batch job id and a log stream -- all present and all agreeing. Six of the eleven have never been written.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

No test proves this check.

Supporting tests (4), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_execution.py::test_the_job_name_is_the_run_id_so_batch_is_a_third_join`
- `tests/test_phase3_infrastructure.py::test_the_binding_record_the_state_machine_writes_is_the_contract_it_claims_to_be`
- `tests/test_phase3_lifecycle_projection.py::test_the_handler_writes_the_four_keys_the_rest_of_phase_three_reads`
- `tests/test_phase3_lifecycle_projection.py::test_the_result_joins_to_the_attempt_and_the_attempt_to_the_batch_job`

### Check 20 — The deployer's unscoped actions are exactly the measured ones, in two statements separated by why each is unscoped.

**Status: COVERED**

Scope:

- Stated in two statements rather than the plan's one, and the difference is the point. Six actions are on "*" because the resource-type probe -- run with its ValidateTemplate and DescribeStacks controls -- found they support no resource-level permission. Ten read-only ec2:Describe* actions are on "*" because EC2 describes are account-wide by the service's own model. Folding them together would let a reader believe the probe measured all sixteen, and would let an unmeasured action join the measured list without anybody noticing.
- This reads the committed template, which is the right thing to read: the criterion is about what the repository asks for, and the failure it prevents is a half-finished stack found afterwards, which this repository has now hit twice. Whether the deployed role matches the template is criterion 14's problem and is a gap.

Proving tests (2), all executed and passing:

- `tests/test_phase3_deployer_role.py::test_the_six_measured_actions_are_on_star_and_in_a_statement_of_their_own`
- `tests/test_phase3_deployer_role.py::test_the_only_other_unscoped_statement_is_the_read_only_ec2_describes`

Supporting tests (6), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_deployer_role.py::test_every_scoped_phase3_arn_carries_the_project_prefix_or_is_a_named_exception`
- `tests/test_phase3_deployer_role.py::test_the_network_scope_can_change_egress_and_can_never_open_a_port`
- `tests/test_phase3_deployer_role.py::test_the_batch_scopes_cover_all_three_resource_types_the_stack_creates`
- `tests/test_phase3_deployer_role.py::test_iam_pass_role_is_still_the_only_iam_action_the_whole_role_holds`
- `tests/test_phase3_deployer_role.py::test_pass_role_names_four_whole_roles_and_never_a_prefix`
- `tests/test_phase3_ec2_authorization.py::test_the_declared_action_list_matches_the_probes_actually_built`

### Check 21 — The networking the compute environment uses is recorded, with its terms.

**Status: GAP**

Gap:

- The terms are not the ones the plan expected, and they are better. The plan assumed a borrowed VPC and called it the phase's largest known limitation; the L-F678F1CE quota increase from five to ten was filed and applied on 2026-07-27, and infra/batch-network.yaml creates our own VPC unconditionally. Ownership is settled, and the committed measurements record the quota, the request id, the zones and which of them offers the instance type.
- What is missing is the other half of the criterion's words: the networking the compute environment *uses*. The network stack is not deployed, so no VPC, subnet or security-group id exists to record, and the committed placement record describes the interim candidate VPC these probes were aimed at rather than one this project owns.
- Closing this means running the Wave 5 live matrix, capturing the named artifact with tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test that reads it. A criterion may not cite an evidence file; it cites the test.

No test proves this check.

Supporting tests (7), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_account_measurements.py::test_the_capture_is_committed_and_inside_its_freshness_window`
- `tests/test_phase3_account_measurements.py::test_the_vpc_quota_has_room_for_a_vpc_we_own`
- `tests/test_phase3_account_measurements.py::test_the_subnet_list_excludes_any_zone_that_does_not_offer_the_instance_type`
- `tests/test_phase3_account_measurements.py::test_the_capture_records_how_it_was_measured`
- `tests/test_phase3_infrastructure.py::test_the_vpc_is_created_unconditionally_because_the_quota_landed`
- `tests/test_phase3_infrastructure.py::test_the_subnets_exclude_the_zone_that_cannot_hold_the_instance_type`
- `tests/test_phase3_infrastructure.py::test_the_compute_environment_places_into_exactly_the_subnets_the_network_exports`

### Check 22 — The image-scan decision has been answered.

**Status: COVERED**

Scope:

- The mutation this criterion exists to catch is leaving the open-decisions entry in place and marking the criterion covered, which is the exact shape of a question settled by accident. So the proving citation is that the entry is gone *and* that the answer is enforced somewhere, rather than either alone.
- The answer went a way the register did not list as obvious: block unless an exception is recorded, enforced at admission rather than at publish, because ECR scans after the push and a publish-time refusal would leave that commit permanently unpublishable. The four criticals in the only published image are carried by a recorded exception naming that digest, which is a decision somebody took in writing rather than a threshold quietly set above them.
- It is enforced in code and configuration this repository commits and admission reads. Nothing here says the enforcement has ever refused a real submission, which is criterion 9's territory and is a gap.

Proving tests (4), all executed and passing:

- `tests/test_open_decisions.py::test_the_scan_question_is_gone_because_it_was_answered`
- `tests/test_phase3_image_scan.py::test_the_shipped_policy_blocks_on_criticals`
- `tests/test_phase3_image_scan.py::test_the_shipped_policy_names_the_denial_condition`
- `tests/test_phase3_image_scan.py::test_the_denial_condition_is_wired_to_the_fact`

Supporting tests (6), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_image_scan.py::test_a_blocking_finding_without_an_exception_is_refused`
- `tests/test_phase3_image_scan.py::test_a_blocking_finding_with_a_recorded_exception_runs`
- `tests/test_phase3_image_scan.py::test_no_scan_at_all_is_refused_rather_than_assumed_clean`
- `tests/test_phase3_image_scan.py::test_the_shipped_registry_covers_the_only_published_image`
- `tests/test_phase3_image_scan.py::test_the_shipped_registry_excepts_nothing_it_does_not_explain`
- `tests/test_phase3_image_scan.py::test_both_production_callers_evaluate_the_scan_gate`
