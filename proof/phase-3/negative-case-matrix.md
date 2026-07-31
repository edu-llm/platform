# Phase 3 negative-case matrix

The nineteen Phase 3 acceptance criteria, mapped to the tests cited for each one by node id. Each cited node id was collected and executed by this generator before the bundle was written; a citation pytest cannot collect aborts generation rather than being printed.

This mapping is defined once, in `src/edullm_platform/phase3_criteria.py`. The acceptance gate reads the same definition and executes the same node ids, so this matrix and `tools/validate_phase3.py` cannot disagree.

Verification run: 378 tests executed, 378 passed, 0 failed, 0 errored, pytest exit code 0.

Three statuses exist and no more. **COVERED** means one or more cited tests prove the criterion as stated against the shipped configuration and all of them pass; the gate passes it. **DEFERRED** means an explicit recorded decision not to satisfy it yet, which requires both a written reason and a written trigger describing what makes it live again; the gate passes it. **GAP** is everything else, and the gate fails it. There is no in-between status, because an in-between status is what lets a gate be green and wrong at the same time.

`proving` tests prove the criterion as stated against the shipped configuration; only a COVERED criterion may cite one. `supporting` tests are cited evidence that does not amount to proof — either because they exercise the code path under a synthetic configuration that is not what ships, or because they prove only part of the claim. Both kinds are executed. A supporting citation that is renamed or deleted still fails the criterion.

| # | status | proving | supporting | check |
| --- | --- | --- | --- | --- |
| 1 | COVERED | 2 | 4 | A valid run reaches SUCCEEDED. |
| 2 | COVERED | 2 | 2 | Stdout and stderr are available through the recorded log stream. |
| 3 | COVERED | 1 | 3 | The S3 result manifest matches the logical run and the Batch job. |
| 4 | COVERED | 1 | 3 | A failed command reaches FAILED with its reason preserved. |
| 8 | COVERED | 1 | 8 | A mandatory timeout terminates a runaway job. |
| 9 | COVERED | 2 | 6 | An invalid queue, job definition, role or override is rejected before submission. |
| 10 | GAP | 0 | 3 | Duplicate or ambiguous submission handling does not silently create an untracked job. |
| 11 | GAP | 0 | 5 | Event duplicates do not create conflicting terminal state. |
| 12 | GAP | 0 | 5 | No GitHub path can administer Batch or EC2. |
| 13 | GAP | 0 | 3 | The workload role cannot write to the lineage store or start anything. |
| 14 | GAP | 0 | 4 | The validator resolves the target and cannot submit; the state machine submits and cannot decide. |
| 15 | COVERED | 1 | 3 | Every provisioned compute profile is backed by a compute environment that exists and is usable. |
| 16 | COVERED | 1 | 1 | The compute environment holds no capacity when it is idle. |
| 17 | COVERED | 1 | 4 | Every record written by this phase carries an S3-attested ChecksumSHA256 and a VersionId. |
| 18 | GAP | 0 | 6 | The EventBridge rule receives only our queue's events. |
| 19 | COVERED | 2 | 6 | A run is traceable end to end by run id alone. |
| 20 | COVERED | 2 | 6 | The deployer's unscoped actions are exactly the measured ones, in two statements separated by why each is unscoped. |
| 21 | COVERED | 1 | 7 | The networking the compute environment uses is recorded, with its terms. |
| 22 | COVERED | 4 | 6 | The image-scan decision has been answered. |

## Gaps

Read these first. A matrix that overstates coverage is worse than no matrix. Every gap here fails the acceptance gate, and each one is unfinished work rather than a recorded decision to postpone: a deferral needs a written reason and a written trigger, and neither exists for any of these. Relabelling them would turn the gate green without anything changing in the account, which is the one thing this matrix exists to make impossible to do quietly.

### Check 10 (GAP) — Duplicate or ambiguous submission handling does not silently create an untracked job.

- Three mechanisms, each of which has to be observed separately: ExecutionAlreadyExists on a second start with the same name, a 412 PreconditionFailed on the binding's conditional write, and the Batch job name being the run id so a second job would be visible. The mutation that defeats all three at once is minting a fresh run id inside AWS, which no local test can see. Four runs have completed and each carried its own run id, so none of the three has ever been exercised.
- The run that closes this is cheap and specific: re-running the submit job of a workflow run that already succeeded. It downloads the same compiled submission, so the run id is reused, and StartExecution must answer ExecutionAlreadyExists -- which the workflow treats as success, and which is the counter-intuitive behaviour most worth having a captured record of.
- Nothing here needs building. Closing this means dispatching a run aimed at this case, capturing what it leaves behind with tools/capture_phase3_evidence.py --target run, committing the sanitized records under fixtures/evidence/phase-3/runs/, and citing a test that reads them. A criterion may not cite an evidence file; it cites the test.

### Check 11 (GAP) — Event duplicates do not create conflicting terminal state.

- The derivation is proved -- the same event projects to byte-identical bytes, the id comes from EventBridge rather than being minted, and deduplicate_lifecycle_events raises on same-id-different-content. What is unproved is the store's half: the same event redelivered and the conditional write refusing it, captured.
- The four completed runs wrote sixteen lifecycle records between them and EventBridge delivered each event once, so the redelivery path has never been taken. It cannot be forced by dispatching another ordinary run; what closes this is invoking the deployed recorder with an event payload already recorded, which is the redelivery path exactly, and capturing the refusal the store answers with.
- Nothing here needs building. Closing this means dispatching a run aimed at this case, capturing what it leaves behind with tools/capture_phase3_evidence.py --target run, committing the sanitized records under fixtures/evidence/phase-3/runs/, and citing a test that reads them. A criterion may not cite an evidence file; it cites the test.

### Check 12 (GAP) — No GitHub path can administer Batch or EC2.

- The matrix has now run, and that is the change since this criterion was last written. It executes in the submit job before the one call that session makes, against a real admission session issued through a protected environment, and it attempts all four Batch actions Phase 3 makes meaningful. Every one of the four completed submissions passed it; a submission whose matrix fails does not proceed.
- What is missing is a committed record of it. The matrix writes its result to a GitHub Actions artifact with a thirty-day retention, so the evidence exists, is unexpired, and lives somewhere this repository does not read and cannot cite. A criterion may not cite an artifact URL any more than it may cite an evidence file, so this stays open until the artifact is captured into fixtures/evidence/phase-3/ and a test reads it.
- Nothing here needs building. Closing this means dispatching a run aimed at this case, capturing what it leaves behind with tools/capture_phase3_evidence.py --target run, committing the sanitized records under fixtures/evidence/phase-3/runs/, and citing a test that reads them. A criterion may not cite an evidence file; it cites the test.

### Check 13 (GAP) — The workload role cannot write to the lineage store or start anything.

- Half of this is now closed and it is worth being precise about which. The deployed workload role is captured from the account and compared to the template that declares it, with no drift, so the mutation this criterion used to be most exposed to -- somebody widening the role in the console, leaving every template test green -- is caught. What the comparison establishes is that the deployed policy grants no lineage write and no way to start anything.
- What a policy says and what AWS refuses are different claims, and only the second is a denial. The workload matrix runs from inside the container under the job role, so it cannot run until a job runs it, and the four completed runs all ran commands that printed a line. Closing this also needs the probe present in the research image, which this repository does not build, so it is a change in another repository before it is a run here.
- A limitation found while capturing, recorded here rather than left for Phase 4 to discover. The deployed role permits s3:PutObject under teams/*/runs/*, not teams/{team}/runs/*, so the workload role can write into any team's output prefix. The template agrees, so this is deliberate rather than drift, and for a single-team pilot nothing is misattributed. It does mean the cross-team isolation output_prefix() says the teams/ segment exists to make expressible is not expressed yet, and the Phase 4 check that a workload role cannot reach another team's prefix would fail against this role today.
- Nothing here needs building. Closing this means dispatching a run aimed at this case, capturing what it leaves behind with tools/capture_phase3_evidence.py --target run, committing the sanitized records under fixtures/evidence/phase-3/runs/, and citing a test that reads them. A criterion may not cite an evidence file; it cites the test.

### Check 14 (GAP) — The validator resolves the target and cannot submit; the state machine submits and cannot decide.

- A citation that reads a committed CloudFormation template reads what the account will be asked for rather than what it holds, and a role widened in a console leaves every such citation green. The four roles in role_drift.PHASE3_ROLE_TEMPLATES are now captured from the account and compared, so that gap is closed for them. The two this criterion is actually about are registered in PHASE2_ROLE_TEMPLATES and are not part of this phase's capture.
- The separation is proved of the committed templates and of the ASL, and four runs have exercised it end to end: the validator resolved a target and the state machine made the SubmitJob call, which is in its execution history. What that does not establish is the negative half -- that the validator could not have submitted -- because a role that never tried looks exactly like a role that could not.
- The mutation is giving the validator Lambda batch:SubmitJob. It would work, and it would move the launch out of the state machine's execution history, so the record of what started a job would stop describing what started it. That is the lineage harm in its purest form and it is exactly the kind a console edit makes.
- Closing this needs the two roles the criterion is about captured and compared, the way the four Phase 3 roles now are. They are sbsandbox-intern-edullm-admission-lambda and sbsandbox-intern-edullm-admission-states, both registered in PHASE2_ROLE_TEMPLATES, so the capture belongs to Phase 2's evidence and its freshness window rather than being copied into this phase's fixtures.

### Check 18 (GAP) — The EventBridge rule receives only our queue's events.

- The pattern names the queue the compute stack creates, compared across both files, and the projection refuses a delivery that is not ours as a second line. Four runs have now been delivered through the deployed rule and every lifecycle record they produced carries their own run id, so the rule is demonstrably delivering what it should.
- What is unproved is the other direction, and it is the half the criterion is actually about: that no lifecycle record exists whose run id is *not* ours. A capture scoped to one run id cannot establish that by construction -- it only ever reads keys under the run it was asked for, so it would report a clean result in a store full of somebody else's events. This is a shared account and a rule matching every Batch event in it would write records under run ids we never submitted.
- Closing this needs a different shape of capture rather than another run: an inventory of the whole lineage bucket, with every events/ key's run id checked against the intents this platform wrote. That is one list call and a comparison, and it belongs beside the per-run captures rather than inside one.

## Checks

### Check 1 — A valid run reaches SUCCEEDED.

**Status: COVERED**

Scope:

- Proved of one run, and one is the right number for this claim. The criterion is that a valid run can reach SUCCEEDED, which one container reaching it establishes; that every valid run does is a reliability question no finite number of runs answers.
- The proving citation is Batch's answer rather than the platform's. A result record saying succeeded is this platform's own projection of an event and would say the same thing if the projection were wrong, so what is asserted is the service reporting SUCCEEDED with exit code 0 for a job whose name is the run id.
- This rests on a committed capture, and a capture is a statement about one moment. Every record is a FreshEvidenceModel, so thirty days after it was taken it stops loading, the cited tests fail and this criterion is a gap again with the gate red. The run does not need repeating -- every object is still in a write-once store -- so what renews it is re-running the capture, which is what the expiry is asking for.

Proving tests (2), all executed and passing:

- `tests/test_phase3_run_evidence.py::test_a_real_container_ran_to_succeeded_under_the_run_id_that_asked_for_it`
- `tests/test_phase3_run_evidence.py::test_every_committed_run_capture_holds`

Supporting tests (4), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_execution.py::test_the_promoted_profile_resolves_to_the_deployed_queue_and_job_definition`
- `tests/test_phase3_execution.py::test_the_job_name_is_the_run_id_so_batch_is_a_third_join`
- `tests/test_phase3_lifecycle_projection.py::test_a_successful_run_records_where_its_output_went`
- `tests/test_phase3_run_evidence.py::test_the_admission_execution_is_named_for_the_run_it_admitted`

### Check 2 — Stdout and stderr are available through the recorded log stream.

**Status: COVERED**

Scope:

- Not pilot-blocking, and the plan's own call. Losing the logs of a run that otherwise behaved costs the user their diagnosis rather than their money or their record: the job ran, the result still joins to it, and the spend happened either way.
- Proved on a run that succeeded and one that failed, deliberately. Logs are read when something went wrong, so a configuration that delivered them only for a clean exit would satisfy this criterion and be useless.
- What is asserted is a line the container printed, fetched back out of the stream the job recorded. The mutation this criterion exists to catch is recording the log group rather than the stream -- it reads as complete and resolves to every job on the queue -- so the stream being different from the group is asserted beside the content.
- This rests on a committed capture, and a capture is a statement about one moment. Every record is a FreshEvidenceModel, so thirty days after it was taken it stops loading, the cited tests fail and this criterion is a gap again with the gate red. The run does not need repeating -- every object is still in a write-once store -- so what renews it is re-running the capture, which is what the expiry is asking for.

Proving tests (2), all executed and passing:

- `tests/test_phase3_run_evidence.py::test_the_container_output_is_readable_through_the_stream_the_job_recorded`
- `tests/test_phase3_run_evidence.py::test_the_failing_container_printed_its_own_line_before_exiting`

Supporting tests (2), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_infrastructure.py::test_the_log_group_the_config_names_is_the_one_the_container_writes_to`
- `tests/test_phase3_infrastructure.py::test_execution_targets_config_names_exactly_what_the_templates_create`

### Check 3 — The S3 result manifest matches the logical run and the Batch job.

**Status: COVERED**

Scope:

- Three links rather than two ends. The result names an attempt, the attempt names a scheduler job, and the job's name is the run id; a check of only the first and last would pass while the middle pointed at somebody else's container, which is exactly what a result manifest describing the wrong job looks like. The supporting citation is the mutation case that holds the middle link to being checked.
- The committed results record their output location under the older outputs/{run_id}/ prefix rather than the teams/{team}/runs/{run_id}/ shape output_prefix() now produces. These runs predate that fix and wrote no output, so what is asserted is that the location was recorded and names the run, not that it has the shape the code now emits.
- This rests on a committed capture, and a capture is a statement about one moment. Every record is a FreshEvidenceModel, so thirty days after it was taken it stops loading, the cited tests fail and this criterion is a gap again with the gate red. The run does not need repeating -- every object is still in a write-once store -- so what renews it is re-running the capture, which is what the expiry is asking for.

Proving tests (1), all executed and passing:

- `tests/test_phase3_run_evidence.py::test_the_result_the_attempt_and_the_batch_job_each_name_the_next`

Supporting tests (3), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_lifecycle_projection.py::test_the_result_joins_to_the_attempt_and_the_attempt_to_the_batch_job`
- `tests/test_phase3_lifecycle_projection.py::test_one_attempt_gets_one_id_whichever_event_describes_it`
- `tests/test_phase3_run_evidence.py::test_an_attempt_naming_another_job_does_not_hold`

### Check 4 — A failed command reaches FAILED with its reason preserved.

**Status: COVERED**

Scope:

- The exit code is what makes this a preserved reason rather than a recorded outcome. A command that exits three and a job the scheduler kills are both failed in the lineage store; only the container's exit code separates them, and the supporting citation holds the two apart so that this criterion and the timeout one cannot come to rest on the same observation.
- This rests on a committed capture, and a capture is a statement about one moment. Every record is a FreshEvidenceModel, so thirty days after it was taken it stops loading, the cited tests fail and this criterion is a gap again with the gate red. The run does not need repeating -- every object is still in a write-once store -- so what renews it is re-running the capture, which is what the expiry is asking for.

Proving tests (1), all executed and passing:

- `tests/test_phase3_run_evidence.py::test_a_deliberate_non_zero_exit_reached_failed_with_the_code_preserved`

Supporting tests (3), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_lifecycle_projection.py::test_a_failed_run_records_the_failure_rather_than_the_nearest_success`
- `tests/test_phase3_lifecycle_projection.py::test_a_job_stopped_before_any_attempt_began_still_records_that_it_stopped`
- `tests/test_phase3_run_evidence.py::test_a_timeout_and_a_non_zero_exit_are_distinguishable_in_the_record`

### Check 8 — A mandatory timeout terminates a runaway job.

**Status: COVERED**

Scope:

- Two halves, and the supporting citations carry the first: the submit request always sends a Timeout, for every fixture including the one with no explicit runtime. That is the half that usually rots and it is also the half that cannot fail visibly, because a duration Batch ignores looks identical in the request.
- The proving citation is the service acting on it. A command that would have run 600 seconds was given 180 and Batch stopped it, reporting 'Job attempt duration exceeded timeout' and no container exit code. The absent exit code is the load-bearing part: a job the scheduler killed never got to return a status, so anything else there would mean the command finished on its own and the timeout was a coincidence.
- Proved at 180 seconds rather than at a production bound. What that establishes is that Batch enforces the number this platform sends; that a particular workload's bound is the right one is a different question and not this criterion's.
- This rests on a committed capture, and a capture is a statement about one moment. Every record is a FreshEvidenceModel, so thirty days after it was taken it stops loading, the cited tests fail and this criterion is a gap again with the gate red. The run does not need repeating -- every object is still in a write-once store -- so what renews it is re-running the capture, which is what the expiry is asking for.

Proving tests (1), all executed and passing:

- `tests/test_phase3_run_evidence.py::test_a_runaway_job_was_stopped_by_the_timeout_the_manifest_asked_for`

Supporting tests (8), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_execution.py::test_every_submit_carries_a_timeout_including_the_shortest_manifest`
- `tests/test_phase3_execution.py::test_the_timeout_sent_to_batch_is_the_manifest_runtime_in_seconds[1-3600]`
- `tests/test_phase3_execution.py::test_the_timeout_sent_to_batch_is_the_manifest_runtime_in_seconds[2-7200]`
- `tests/test_phase3_execution.py::test_the_timeout_sent_to_batch_is_the_manifest_runtime_in_seconds[13-46800]`
- `tests/test_phase3_execution.py::test_the_timeout_sent_to_batch_is_the_manifest_runtime_in_seconds[0.5-1800]`
- `tests/test_phase3_execution.py::test_the_runtime_bound_is_rounded_down_rather_than_up`
- `tests/test_phase3_infrastructure.py::test_the_job_definition_carries_a_timeout_and_a_retry_floor_of_its_own`
- `tests/test_phase3_run_evidence.py::test_a_timeout_and_a_non_zero_exit_are_distinguishable_in_the_record`

### Check 9 — An invalid queue, job definition, role or override is rejected before submission.

**Status: COVERED**

Scope:

- One of the four the criterion names was exercised, and it is worth saying which rather than letting one refusal read as four. A live submission overrode the compute profile to gpu-1xa10g, which on 2026-07-28 was priced in the catalog and backed by nothing, and admission refused it with reason no_execution_target before submission. An invalid queue, job definition or role has not been submitted to the account; those three are unreachable through the dispatch form, which resolves all three from deployed configuration the submitter never sees.
- THAT PROFILE IS NOW PROVISIONED, so the refusal above is not reproducible by repeating it. Phase 4 promoted gpu-1xa10g and built the compute environment, queue and job definition behind it, which is the outcome the refusal was pointing at rather than a contradiction of it. The evidence still holds because it is a record of something that happened: the capture carries the decision, the reason code and the absence of any Batch job. What stopped being true is only the present tense, and this note exists because nothing else in the bundle would have caught that -- the status guard reads numbered claims, and a sentence describing an account is not one. Reproducing the refusal today needs a profile that is still unprovisioned; the catalog prices ten.
- The absence of a Batch job is half the claim and it is recorded rather than implied. ListJobs answers one status at a time, so an absence established without naming the statuses searched is an absence established nowhere -- and the case that matters is a refused submission sitting in RUNNABLE, which a search of the terminal statuses would miss. The capture records all seven and the supporting citation fails if it stops.
- This rests on a committed capture, and a capture is a statement about one moment. Every record is a FreshEvidenceModel, so thirty days after it was taken it stops loading, the cited tests fail and this criterion is a gap again with the gate red. The run does not need repeating -- every object is still in a write-once store -- so what renews it is re-running the capture, which is what the expiry is asking for.

Proving tests (2), all executed and passing:

- `tests/test_phase3_run_evidence.py::test_a_profile_with_nowhere_to_run_was_refused_and_started_nothing`
- `tests/test_phase3_run_evidence.py::test_a_refused_run_wrote_its_intent_and_decision_and_nothing_past_them`

Supporting tests (6), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_execution.py::test_every_provisioned_profile_is_backed_and_every_target_names_a_provisioned_one`
- `tests/test_phase3_execution.py::test_a_priced_but_unprovisioned_profile_has_nowhere_to_go`
- `tests/test_phase3_execution.py::test_an_unprovisioned_profile_is_a_refusal_rather_than_a_crash`
- `tests/test_phase3_execution.py::test_the_two_ways_of_having_nowhere_to_run_are_distinguishable_in_the_record`
- `tests/test_phase3_infrastructure.py::test_execution_targets_config_names_exactly_what_the_templates_create`
- `tests/test_phase3_run_evidence.py::test_a_refusal_whose_run_started_a_job_anyway_does_not_hold`

### Check 10 — Duplicate or ambiguous submission handling does not silently create an untracked job.

**Status: GAP**

Gap:

- Three mechanisms, each of which has to be observed separately: ExecutionAlreadyExists on a second start with the same name, a 412 PreconditionFailed on the binding's conditional write, and the Batch job name being the run id so a second job would be visible. The mutation that defeats all three at once is minting a fresh run id inside AWS, which no local test can see. Four runs have completed and each carried its own run id, so none of the three has ever been exercised.
- The run that closes this is cheap and specific: re-running the submit job of a workflow run that already succeeded. It downloads the same compiled submission, so the run id is reused, and StartExecution must answer ExecutionAlreadyExists -- which the workflow treats as success, and which is the counter-intuitive behaviour most worth having a captured record of.
- Nothing here needs building. Closing this means dispatching a run aimed at this case, capturing what it leaves behind with tools/capture_phase3_evidence.py --target run, committing the sanitized records under fixtures/evidence/phase-3/runs/, and citing a test that reads them. A criterion may not cite an evidence file; it cites the test.

No test proves this check.

Supporting tests (3), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_execution.py::test_the_job_name_is_the_run_id_so_batch_is_a_third_join`
- `tests/test_phase3_infrastructure.py::test_the_binding_write_is_conditional_and_checksummed_like_every_lineage_write`
- `tests/test_phase2_submit_run_workflow.py::test_an_execution_that_already_exists_under_this_run_id_is_a_success`

### Check 11 — Event duplicates do not create conflicting terminal state.

**Status: GAP**

Gap:

- The derivation is proved -- the same event projects to byte-identical bytes, the id comes from EventBridge rather than being minted, and deduplicate_lifecycle_events raises on same-id-different-content. What is unproved is the store's half: the same event redelivered and the conditional write refusing it, captured.
- The four completed runs wrote sixteen lifecycle records between them and EventBridge delivered each event once, so the redelivery path has never been taken. It cannot be forced by dispatching another ordinary run; what closes this is invoking the deployed recorder with an event payload already recorded, which is the redelivery path exactly, and capturing the refusal the store answers with.
- Nothing here needs building. Closing this means dispatching a run aimed at this case, capturing what it leaves behind with tools/capture_phase3_evidence.py --target run, committing the sanitized records under fixtures/evidence/phase-3/runs/, and citing a test that reads them. A criterion may not cite an evidence file; it cites the test.

Scope:

- Not pilot-blocking, and it argued hard for the marker, because a conflicting terminal state is a lineage defect. What decides it the other way is visibility: a duplicate event produces a record that disagrees with itself and can be seen to, where every criterion marked in this phase produces a record that looks fine and is wrong.

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

- The matrix has now run, and that is the change since this criterion was last written. It executes in the submit job before the one call that session makes, against a real admission session issued through a protected environment, and it attempts all four Batch actions Phase 3 makes meaningful. Every one of the four completed submissions passed it; a submission whose matrix fails does not proceed.
- What is missing is a committed record of it. The matrix writes its result to a GitHub Actions artifact with a thirty-day retention, so the evidence exists, is unexpired, and lives somewhere this repository does not read and cannot cite. A criterion may not cite an artifact URL any more than it may cite an evidence file, so this stays open until the artifact is captured into fixtures/evidence/phase-3/ and a test reads it.
- Nothing here needs building. Closing this means dispatching a run aimed at this case, capturing what it leaves behind with tools/capture_phase3_evidence.py --target run, committing the sanitized records under fixtures/evidence/phase-3/runs/, and citing a test that reads them. A criterion may not cite an evidence file; it cites the test.

Scope:

- Pilot-blocking, and it has no counterpart in the plan's Phase 3 check list -- it is Phase 2's gate sentence, carried here because this is the first phase in which administering Batch means launching something that bills. Its absence lets a GitHub path start compute outside admission altogether, which is unbounded spend with no intent record in front of it.

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

- Half of this is now closed and it is worth being precise about which. The deployed workload role is captured from the account and compared to the template that declares it, with no drift, so the mutation this criterion used to be most exposed to -- somebody widening the role in the console, leaving every template test green -- is caught. What the comparison establishes is that the deployed policy grants no lineage write and no way to start anything.
- What a policy says and what AWS refuses are different claims, and only the second is a denial. The workload matrix runs from inside the container under the job role, so it cannot run until a job runs it, and the four completed runs all ran commands that printed a line. Closing this also needs the probe present in the research image, which this repository does not build, so it is a change in another repository before it is a run here.
- A limitation found while capturing, recorded here rather than left for Phase 4 to discover. The deployed role permits s3:PutObject under teams/*/runs/*, not teams/{team}/runs/*, so the workload role can write into any team's output prefix. The template agrees, so this is deliberate rather than drift, and for a single-team pilot nothing is misattributed. It does mean the cross-team isolation output_prefix() says the teams/ segment exists to make expressible is not expressed yet, and the Phase 4 check that a workload role cannot reach another team's prefix would fail against this role today.
- Nothing here needs building. Closing this means dispatching a run aimed at this case, capturing what it leaves behind with tools/capture_phase3_evidence.py --target run, committing the sanitized records under fixtures/evidence/phase-3/runs/, and citing a test that reads them. A criterion may not cite an evidence file; it cites the test.

Scope:

- Pilot-blocking, and one of the criteria the plan's own status block names as missing from its check list. The workload role is the identity a researcher's container runs as. If it can write to the lineage store it can forge the record of what it did; if it can start something it can spend outside the admission path. Attribution, lineage and money in one role.

No test proves this check.

Supporting tests (3), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_infrastructure.py::test_the_workload_role_can_neither_reach_lineage_nor_start_anything`
- `tests/test_phase3_infrastructure.py::test_the_workload_role_writes_only_under_a_runs_prefix_of_the_outputs_bucket`
- `tests/test_phase3_batch_denials.py::test_a_repository_outside_this_project_is_a_setup_failure`

### Check 14 — The validator resolves the target and cannot submit; the state machine submits and cannot decide.

**Status: GAP**

Gap:

- A citation that reads a committed CloudFormation template reads what the account will be asked for rather than what it holds, and a role widened in a console leaves every such citation green. The four roles in role_drift.PHASE3_ROLE_TEMPLATES are now captured from the account and compared, so that gap is closed for them. The two this criterion is actually about are registered in PHASE2_ROLE_TEMPLATES and are not part of this phase's capture.
- The separation is proved of the committed templates and of the ASL, and four runs have exercised it end to end: the validator resolved a target and the state machine made the SubmitJob call, which is in its execution history. What that does not establish is the negative half -- that the validator could not have submitted -- because a role that never tried looks exactly like a role that could not.
- The mutation is giving the validator Lambda batch:SubmitJob. It would work, and it would move the launch out of the state machine's execution history, so the record of what started a job would stop describing what started it. That is the lineage harm in its purest form and it is exactly the kind a console edit makes.
- Closing this needs the two roles the criterion is about captured and compared, the way the four Phase 3 roles now are. They are sbsandbox-intern-edullm-admission-lambda and sbsandbox-intern-edullm-admission-states, both registered in PHASE2_ROLE_TEMPLATES, so the capture belongs to Phase 2's evidence and its freshness window rather than being copied into this phase's fixtures.

Scope:

- Pilot-blocking, and it has no counterpart in the plan's check list. The mutation it exists to catch -- giving the validator Lambda batch:SubmitJob -- would work, and would move the launch out of the state machine's execution history, so the record of what started a job would stop describing what started it. A launch nothing recorded is the lineage harm in its purest form.

No test proves this check.

Supporting tests (4), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_infrastructure.py::test_the_states_role_gains_batch_and_ecr_reads_and_no_way_to_stop_a_job`
- `tests/test_phase3_infrastructure.py::test_the_validator_payload_is_built_field_by_field_and_never_forwarded`
- `tests/test_phase3_infrastructure.py::test_submit_to_batch_passes_the_request_through_and_names_no_field_of_it`
- `tests/test_phase3_infrastructure.py::test_the_recorder_role_writes_lineage_and_cannot_make_anything_happen`

### Check 15 — Every provisioned compute profile is backed by a compute environment that exists and is usable.

**Status: COVERED**

Scope:

- Not pilot-blocking, and this is the judgement call in the phase most worth arguing with. The harmful half of this seam -- refusing a manifest that names a profile with nowhere to run, before anything is submitted -- belongs to the rejection check above and is marked there. What is left here is placement: a profile that is priced and not backed produces a job that cannot start, which is visible, bills nothing while it waits, and is bounded by the queue. Availability rather than harm.
- Two claims, and the supporting citations carry the first: the set of profiles the catalog marks provisioned is exactly the set of profiles the execution targets back, compared from both files in both directions. The proving citation carries the second -- that the environment named actually exists, is VALID and ENABLED, and is the one the job queue routes to. A template creating a compute environment is a request; an environment can be created and land INVALID, in which case every job queued to it waits forever with no error anywhere.
- The proving citation reads Phase 3's committed capture, so it speaks for the CPU environment and for no other. Phase 4's GPU environment is covered by the supporting config-to-config comparison and by the deploy-time verification, and not yet by a capture of its own. That is the honest limit of this criterion after a second profile was promoted.
- A VALID environment is still not evidence a job can run, which is why criterion 1 is separate from this one and rests on a container having actually reached SUCCEEDED.
- This rests on a committed capture, and a capture is a statement about one moment. Every record is a FreshEvidenceModel, so thirty days after it was taken it stops loading, the cited tests fail and this criterion is a gap again with the gate red. The run does not need repeating -- every object is still in a write-once store -- so what renews it is re-running the capture, which is what the expiry is asking for.

Proving tests (1), all executed and passing:

- `tests/test_phase3_run_evidence.py::test_exactly_one_compute_environment_backs_the_one_provisioned_profile`

Supporting tests (3), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_execution.py::test_every_provisioned_profile_is_backed_and_every_target_names_a_provisioned_one`
- `tests/test_phase3_execution.py::test_every_target_names_infrastructure_this_project_owns`
- `tests/test_phase3_infrastructure.py::test_the_compute_environment_holds_no_capacity_when_it_is_idle`

### Check 16 — The compute environment holds no capacity when it is idle.

**Status: COVERED**

Scope:

- Pilot-blocking, and one of the criteria the plan's own status block names as missing from its check list. It is the only entry in the phase whose absence bills money continuously with nothing running: an environment left holding vCPUs while idle pays for hardware nobody asked for, in a shared account that is already carrying a four-figure capacity charge somebody else made.
- minvCpus and desiredvCpus are different facts and the second is closer to the claim. minvCpus is what the template asks for and is asserted from the template by the supporting citation; desiredvCpus is what the scheduler is asking for. The proving citation reads both from the deployed environment while nothing was queued, and both were zero.
- DESIREDVCPUS ALONE DOES NOT ESTABLISH THIS, WHICH THIS CRITERION USED TO ASSUME. Measured on 2026-07-28 against Phase 4's first GPU run: the job reached SUCCEEDED at 22:33:48Z, desiredvCpus read zero by 22:34:47Z, and the g5.xlarge it had started ran until 22:41:5xZ. For those seven minutes the number this criterion rested on said the environment held nothing while an instance was on the bill. ecs:list-container-instances read zero over the same window, because the agent deregisters before the host goes away. Both signals answer a neighbouring question -- what the scheduler wants, and what the cluster can place onto -- and neither answers what is being paid for. So the record now carries live_instance_count, attributed by the auto scaling group tag Batch puts on its own instances, and the proving citation asserts all three.
- The environment demonstrably does scale, which is what makes the reading worth anything. It was observed holding 32 vCPUs while the timeout run was in flight and back at zero afterwards, so a capture taken at the wrong moment records the non-zero figure and fails this rather than quietly reading as idle.
- The capture is of the CPU environment. Phase 4's GPU environment is the one the seven-minute window was measured on and is not committed here, so what this criterion covers is the environment Phase 3 built. Saying otherwise would be the same error the paragraph above records.
- This rests on a committed capture, and a capture is a statement about one moment. Every record is a FreshEvidenceModel, so thirty days after it was taken it stops loading, the cited tests fail and this criterion is a gap again with the gate red. The run does not need repeating -- every object is still in a write-once store -- so what renews it is re-running the capture, which is what the expiry is asking for.

Proving tests (1), all executed and passing:

- `tests/test_phase3_run_evidence.py::test_the_compute_environment_held_no_capacity_after_the_runs_finished`

Supporting tests (1), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_infrastructure.py::test_the_compute_environment_holds_no_capacity_when_it_is_idle`

### Check 17 — Every record written by this phase carries an S3-attested ChecksumSHA256 and a VersionId.

**Status: COVERED**

Scope:

- Pilot-blocking, on the same reasoning as the equivalent Phase 2 criterion and deliberately worded to match it, because the same property recorded two ways in two phases is how a split like this rots. The conditional write refuses an overwrite, so attestation and versioning are the second line rather than the first; what settles it is that no limitations page helps. There is nothing a pilot user can do with the sentence that the objects recording their run are neither attested nor versioned, and an object that has been altered reads exactly like one that has not.
- The writers asking for a checksum and S3 having stored one are different claims. The supporting citations carry the first, from the ASL and the templates; the proving citation carries the second, from HeadObject with checksum mode enabled against every object all four runs wrote. This is distinct from the canonical manifest hash and is recorded as such, because a reader who conflated them would think one proved the other.
- Attestation is not integrity of content, and the corrupt bindings are the proof of that. Three objects here are attested, versioned and intact -- S3 holds exactly the bytes it was sent -- and are refused by the contract that defines what a binding is. The supporting citation records them, so a reader cannot take this criterion as saying the store holds nothing malformed.
- This rests on a committed capture, and a capture is a statement about one moment. Every record is a FreshEvidenceModel, so thirty days after it was taken it stops loading, the cited tests fail and this criterion is a gap again with the gate red. The run does not need repeating -- every object is still in a write-once store -- so what renews it is re-running the capture, which is what the expiry is asking for.

Proving tests (1), all executed and passing:

- `tests/test_phase3_run_evidence.py::test_every_lineage_object_carries_an_s3_attested_checksum_and_version`

Supporting tests (4), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_infrastructure.py::test_the_binding_write_is_conditional_and_checksummed_like_every_lineage_write`
- `tests/test_phase3_lifecycle_projection.py::test_every_write_is_conditional_so_a_replay_cannot_overwrite_anything`
- `tests/test_phase3_lifecycle_projection.py::test_the_stored_bytes_are_the_canonical_ones_rather_than_a_re_encoding`
- `tests/test_phase3_run_evidence.py::test_the_bindings_written_before_the_asl_fix_are_recorded_as_permanently_corrupt`

### Check 18 — The EventBridge rule receives only our queue's events.

**Status: GAP**

Gap:

- The pattern names the queue the compute stack creates, compared across both files, and the projection refuses a delivery that is not ours as a second line. Four runs have now been delivered through the deployed rule and every lifecycle record they produced carries their own run id, so the rule is demonstrably delivering what it should.
- What is unproved is the other direction, and it is the half the criterion is actually about: that no lifecycle record exists whose run id is *not* ours. A capture scoped to one run id cannot establish that by construction -- it only ever reads keys under the run it was asked for, so it would report a clean result in a store full of somebody else's events. This is a shared account and a rule matching every Batch event in it would write records under run ids we never submitted.
- Closing this needs a different shape of capture rather than another run: an inventory of the whole lineage bucket, with every events/ key's run id checked against the intents this platform wrote. That is one list call and a comparison, and it belongs beside the per-run captures rather than inside one.

Scope:

- Pilot-blocking, and one of the criteria the plan's own status block names as missing from its check list. This is a shared account, and a rule that matched every Batch event in it would write lifecycle records under run ids that are not ours. The store would then hold entries describing somebody else's job, which is the lineage record corrupted by construction rather than by accident.

No test proves this check.

Supporting tests (6), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_infrastructure.py::test_the_event_rule_matches_the_job_queue_the_compute_stack_creates`
- `tests/test_phase3_infrastructure.py::test_the_queue_the_states_role_may_submit_to_is_the_queue_that_exists`
- `tests/test_phase3_infrastructure.py::test_the_queue_accepts_deliveries_only_from_our_own_rule_in_our_own_account`
- `tests/test_phase3_lifecycle_projection.py::test_a_delivery_that_is_not_ours_is_refused[foreign-source]`
- `tests/test_phase3_lifecycle_projection.py::test_a_delivery_that_is_not_ours_is_refused[foreign-detail-type]`
- `tests/test_phase3_lifecycle_projection.py::test_a_job_whose_name_is_not_a_run_id_is_refused`

### Check 19 — A run is traceable end to end by run id alone.

**Status: COVERED**

Scope:

- Pilot-blocking, and it is this phase's gate restated as an assertion. Eleven artifacts have to resolve from one run id and agree; where they do not, the platform has run something it cannot account for afterwards, which is attribution and lineage lost together. It is also the one check that fails when another passes for the wrong reason, so leaving it out of the pilot set would let the rung open on the strength of the very checks it exists to cross-examine.
- Eleven artifacts, named rather than counted: a GitHub workflow run, a CloudTrail AssumeRoleWithWebIdentity, a Step Functions execution, an intent, a decision, a binding, at least one event, an attempt, a result, a Batch job id and a log stream. They are enumerated in phase3_capture.TRACEABLE_ARTIFACTS and asserted by name, because a check that counted eleven would go on passing after somebody removed one and added another.
- Proved of one run of the four, and which one is not arbitrary. The other three were submitted before the ASL fix and carry bindings that will never load, so ten of their eleven artifacts resolve and the eleventh cannot -- and they are reported as not traceable rather than as nearly traceable. That is the criterion working: an unbroken chain is the claim, and a chain missing a link is not a chain.
- The session is joined through the StartExecution call that names this run id, not by recency. Every submission assumes the same role, so a capture that took the most recent session would name one belonging to a different run and still look complete; the subject claim carrying :environment:run-approval-lead is what additionally shows it was issued past the approval gate.
- This rests on a committed capture, and a capture is a statement about one moment. Every record is a FreshEvidenceModel, so thirty days after it was taken it stops loading, the cited tests fail and this criterion is a gap again with the gate red. The run does not need repeating -- every object is still in a write-once store -- so what renews it is re-running the capture, which is what the expiry is asking for.

Proving tests (2), all executed and passing:

- `tests/test_phase3_run_evidence.py::test_one_run_id_resolves_to_all_eleven_artifacts`
- `tests/test_phase3_run_evidence.py::test_the_session_that_started_the_run_came_through_the_approval_gate`

Supporting tests (6), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_execution.py::test_the_job_name_is_the_run_id_so_batch_is_a_third_join`
- `tests/test_phase3_infrastructure.py::test_the_binding_record_the_state_machine_writes_is_the_contract_it_claims_to_be`
- `tests/test_phase3_lifecycle_projection.py::test_the_handler_writes_the_four_keys_the_rest_of_phase_three_reads`
- `tests/test_phase3_lifecycle_projection.py::test_the_result_joins_to_the_attempt_and_the_attempt_to_the_batch_job`
- `tests/test_phase3_run_evidence.py::test_the_admission_execution_is_named_for_the_run_it_admitted`
- `tests/test_phase3_run_evidence.py::test_a_job_whose_name_is_not_the_run_id_does_not_hold`

### Check 20 — The deployer's unscoped actions are exactly the measured ones, in two statements separated by why each is unscoped.

**Status: COVERED**

Scope:

- Not pilot-blocking, and the call took an argument. A deployment role scoped more widely than measured is a real exposure in a shared account -- it is how somebody else's stack gets deleted. What keeps it off the pilot list is who can reach it: this role is assumed by the deployment workflow and by nothing a pilot user touches, so its absence widens the build team's own path rather than the path being opened. The scoping itself is asserted by the citations beside this one; what this criterion adds is that the list of unscoped actions cannot grow without somebody measuring the addition.
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
- `tests/test_phase3_deployer_role.py::test_pass_role_names_whole_roles_and_never_a_prefix`
- `tests/test_phase3_ec2_authorization.py::test_the_declared_action_list_matches_the_probes_actually_built`

### Check 21 — The networking the compute environment uses is recorded, with its terms.

**Status: COVERED**

Scope:

- Not pilot-blocking. What is missing is a record of which VPC, subnets and security group the environment ends up on, and somebody who wants it can read it off the deployed stack. Nothing is spent, lost or misattributed by its absence; what is lost is a later reader's ability to reconstruct the placement without opening a console, which is a reviewer's need rather than a pilot user's.
- The terms are not the ones the plan expected, and they are better. The plan assumed a borrowed VPC and called it the phase's largest known limitation; the L-F678F1CE quota increase from five to ten was filed and applied on 2026-07-27, and infra/batch-network.yaml creates our own VPC unconditionally. Ownership is settled, so what this criterion records is a placement rather than a dependency to carry.
- The proving citation reads the deployed environment rather than the template. A stack applied from a laptop can land somewhere other than where its template says, and a record copied from the template would agree with itself forever; these are the VPC, subnet and security group ids the environment is actually configured with, read back from the account.
- This rests on a committed capture, and a capture is a statement about one moment. Every record is a FreshEvidenceModel, so thirty days after it was taken it stops loading, the cited tests fail and this criterion is a gap again with the gate red. The run does not need repeating -- every object is still in a write-once store -- so what renews it is re-running the capture, which is what the expiry is asking for.

Proving tests (1), all executed and passing:

- `tests/test_phase3_run_evidence.py::test_the_networking_the_compute_environment_uses_is_recorded`

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

- Not pilot-blocking, and this is the one place in the phase where the ladder's test comes out under-inclusive, which is worth recording rather than stretching one of its four harms to cover it. Running a container with unreviewed critical findings is a security exposure; it is not money, data, attribution or the lineage record, which are the four the test names. So the test answers no, and the reservation is written down here so that the next person applying it to a vulnerability question knows it has been applied once and did not obviously fit.
- The mutation this criterion exists to catch is leaving the open-decisions entry in place and marking the criterion covered, which is the exact shape of a question settled by accident. So the proving citation is that the entry is gone *and* that the answer is enforced somewhere, rather than either alone.
- The answer went a way the register did not list as obvious: block unless an exception is recorded, enforced at admission rather than at publish, because ECR scans after the push and a publish-time refusal would leave that commit permanently unpublishable. The four criticals in the only published image are carried by a recorded exception naming that digest, which is a decision somebody took in writing rather than a threshold quietly set above them.
- It is enforced in code and configuration this repository commits and admission reads. What this criterion does not say is that the scan gate has ever refused a real submission: the four completed runs all named an image whose four criticals are carried by a recorded exception, so the gate was evaluated and passed every time. A refusal on scan findings has not been observed, and the rejection that has been -- an unprovisioned compute profile -- travelled a different path.

Proving tests (4), all executed and passing:

- `tests/test_open_decisions.py::test_the_scan_question_is_gone_because_it_was_answered`
- `tests/test_phase3_image_scan.py::test_the_shipped_policy_blocks_on_criticals`
- `tests/test_phase3_image_scan.py::test_the_shipped_policy_names_the_denial_condition`
- `tests/test_phase3_image_scan.py::test_the_denial_condition_is_wired_to_the_fact`

Supporting tests (6), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase3_image_scan.py::test_a_blocking_finding_without_an_exception_is_refused`
- `tests/test_phase3_image_scan.py::test_a_blocking_finding_with_a_recorded_exception_runs`
- `tests/test_phase3_image_scan.py::test_no_scan_at_all_is_refused_rather_than_assumed_clean`
- `tests/test_phase3_image_scan.py::test_the_shipped_registry_covers_what_the_published_images_actually_carry`
- `tests/test_phase3_image_scan.py::test_the_shipped_registry_excepts_nothing_it_does_not_explain`
- `tests/test_phase3_image_scan.py::test_both_production_callers_evaluate_the_scan_gate`
