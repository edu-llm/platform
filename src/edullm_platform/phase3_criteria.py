"""The Phase 3 acceptance criteria and the tests that are cited for each one.

Phase 3 takes one manifest Phase 2 admits and turns the accepted decision into one
digest-pinned CPU container on AWS Batch, with the binding, the lifecycle events, the
attempt and the result landing write-once in the lineage bucket beside the intent and the
decision. This module records the twenty-two checks the phase must satisfy, against the
contract in :mod:`edullm_platform.criteria`.

**The phase is deployed and has run.** Every stack is applied, and four submissions have
gone GitHub to OIDC to admission to Batch to EventBridge to S3: one that succeeded, one
whose command exited three deliberately, one stopped by its own timeout, and one that
admission refused before anything could be launched. What those runs left behind is
captured, sanitized and committed under ``fixtures/evidence/phase-3/``, and thirteen of the
twenty-two criteria are covered by tests that read it. This module used to say that nothing
had been deployed and that nineteen criteria were blocked behind that; that sentence was
true when it was written and is not now.

**Nine criteria remain gaps, and they are not one gap repeated.** They fall into three
kinds, and the difference decides what closing each one costs.

Three are a component nobody has built. Criteria 5, 6 and 7 are cancellation, and no
cancellation state machine exists: every role Phase 3 declares deliberately excludes
``batch:TerminateJob``, so these need code written before a run could demonstrate anything.

Four are a scenario nobody has run. Criteria 10, 11, 12 and 13 each name an observation
that the four completed runs did not produce -- a second submission under one run id, a
redelivered EventBridge event, a committed capture of the denial matrix the submit job
already executes, and the workload matrix running from inside a container. None of them
needs new infrastructure; each needs a run aimed at it, and criterion 13's also needs an
image carrying the probe.

Two are an observation the per-run captures cannot make. Criteria 14 and 18 are about the
store and the roles taken as a whole rather than about any one run: whether the two Phase 2
roles the validator and state machine actually hold still match their templates, and
whether every lifecycle record in the bucket belongs to a run this platform submitted. A
capture scoped to one run id can say nothing about either by construction.

They are ``GAP`` and not ``DEFERRED``, and the distinction is the whole point of having two
words. A deferral is a decision not to do something, with a written trigger that makes it
live again; the ``team_verified`` deferral Phase 0 and Phase 2 both carry is one, because
nothing about it is unfinished -- the configuration is empty on purpose. These nine are
unfinished work, and recording them as deferrals would make ``tools/validate_phase3.py``
exit 0 against a phase that cannot yet stop a job it has started. **The gate exiting 1
today is the report working.**

**A criterion cites a test, never an evidence file.** The thirteen covered criteria cite
tests in ``tests/test_phase3_run_evidence.py``, which read the committed captures through
:mod:`edullm_platform.phase3_capture` and hold them to agreeing with one another. Those
records expire: every one is a ``FreshEvidenceModel``, and thirty days after the capture
they stop loading, the citations fail, and these criteria are gaps again with the gate red.
That is the window working rather than a defect to route around -- a deployed role can be
widened in a console, and the only thing that establishes it has not been is somebody going
and looking again.

**One committed record is permanently broken, and the criteria say so rather than route
around it.** Three of the four runs were submitted before the ``"Result": null`` fix in the
admission ASL and carry a whole admission payload where a fan-out size belongs. The lineage
store is write-once, so no future capture repairs them. They are recorded as attested,
versioned and unloadable, and the runs holding one are reported as not traceable end to
end -- which is why criterion 19 rests on the one run whose binding is clean, and why the
capture withholds the corrupt bodies rather than committing an approver's name and a CVE
dump to establish something the attestation already says.

**Three of the plan's checks are stated differently here, and the difference is deliberate
rather than drift.**

Check 20 says the deployer's ``"*"``-scoped actions are "exactly the six measured ones".
The deployer now carries a *second* unscoped statement, for ten read-only ``ec2:Describe*``
actions. That is not a seventh measured action smuggled into the first statement: EC2
describes are account-wide by the service's own model rather than by the resource-type
probe's finding, and folding two different justifications into one statement would make the
next reader believe the probe measured all sixteen. The criterion below therefore states
both statements and what separates them.

Check 21 assumed the compute environment would run in a VPC belonging to somebody else,
and said the borrowed VPC would be the phase's largest known limitation. It is not
borrowed. The ``L-F678F1CE`` increase from five to ten was filed and applied on 2026-07-27,
``infra/batch-network.yaml`` creates our own VPC unconditionally, and the deployed
environment's own VPC, subnets and security groups are now captured from the account rather
than read off the template.

Check 9 is about an invalid queue, job definition, role or override being rejected before
submission. What was actually exercised is the override: a manifest naming ``gpu-1xa10g``,
a profile the catalog prices and no compute environment backs. The criterion below is
covered on that basis and its ``scope_limits`` says which of the four the run reached, so
that a reader does not take one refusal as four.

**Thirteen of the twenty-two are pilot-blocking, and five of those thirteen are still gaps,
so this phase is not pilot-ready.** The master plan resolves Phase 3 into eleven checks and
marks six; those eleven are criteria 1 to 11 here, in order, so that part of the split is
the plan's markers carried across rather than a judgement. The remaining eleven criteria
had no counterpart in the plan's list, and seven of them are marked here: the four that
keep a GitHub path, a container, a validator and a state machine inside their own
authority; the one that stops an idle compute environment billing; the one that attests
what the lineage store holds; and the one that makes a run traceable by run id alone, which
is the phase's gate restated as an assertion. The five still open are 10, 12, 13, 14 and
18.

The four that are not marked are worth naming because saying no is what makes the marker
mean anything. Criterion 15 is a placement question whose harmful half already lives in
criterion 9. Criterion 20 constrains the deployment role the build team uses rather than
anything a pilot user can reach. Criterion 21 records the networking for a later reader.
Criterion 22 is the one place the ladder's four harms come out under-inclusive, and its
``scope_limits`` says so rather than stretching one of the four to cover it.
"""

from __future__ import annotations

from typing import Final

from edullm_platform.criteria import (
    CriterionSpec,
    CriterionStatus,
    validate_criterion_specs,
)

__all__ = [
    "CAPTURE_A_RUN_AIMED_AT_IT",
    "NEEDS_A_COMPONENT_BUILT",
    "PHASE3_CRITERION_COUNT",
    "TEMPLATE_NOT_CAPTURE",
    "THE_CAPTURES_EXPIRE",
    "phase3_criteria",
]

PHASE3_CRITERION_COUNT: Final = 22

EXECUTION = "tests/test_phase3_execution.py"
PROJECTION = "tests/test_phase3_lifecycle_projection.py"
INFRA = "tests/test_phase3_infrastructure.py"
DEPLOYER = "tests/test_phase3_deployer_role.py"
DENIALS = "tests/test_phase3_batch_denials.py"
EC2 = "tests/test_phase3_ec2_authorization.py"
SCAN = "tests/test_phase3_image_scan.py"
MEASUREMENTS = "tests/test_phase3_account_measurements.py"
DEPLOY_WORKFLOW = "tests/test_phase3_batch_deployment_workflow.py"
SUBMIT_WORKFLOW = "tests/test_phase2_submit_run_workflow.py"
DECISIONS = "tests/test_open_decisions.py"
#: The tests that read the committed captures of the four completed runs. Every covered
#: criterion that is about the account rather than about a template cites this module.
RUN_EVIDENCE = "tests/test_phase3_run_evidence.py"

#: What closes a criterion whose scenario simply has not been run. Written once because it
#: is the same instruction four times, and a reader who has met it once should not have to
#: check whether the fourth wording differs. ``gaps`` is a tuple the gate joins with a
#: space, so this is a sibling element rather than a concatenation.
CAPTURE_A_RUN_AIMED_AT_IT: Final = (
    "Nothing here needs building. Closing this means dispatching a run aimed at this case, "
    "capturing what it leaves behind with tools/capture_phase3_evidence.py --target run, "
    "committing the sanitized records under fixtures/evidence/phase-3/runs/, and citing a "
    "test that reads them. A criterion may not cite an evidence file; it cites the test."
)

#: What closes a criterion that has no mechanism behind it yet. Distinct from the sentence
#: above because the two are different amounts of work, and a reader deciding what to do
#: next is choosing between them.
NEEDS_A_COMPONENT_BUILT: Final = (
    "This one cannot be closed by running anything, because the mechanism does not exist. "
    "It needs a cancellation path built and deployed first -- a state machine holding "
    "batch:TerminateJob, which no role Phase 3 declares is permitted today -- and only then "
    "a run that exercises it and a test over the capture."
)

#: Why the covered criteria are not settled forever. Attached to the scope limits of the
#: criteria that rest on captures rather than to their gaps, because it is a property of
#: the evidence rather than a reason the criterion is open.
THE_CAPTURES_EXPIRE: Final = (
    "This rests on a committed capture, and a capture is a statement about one moment. "
    "Every record is a FreshEvidenceModel, so thirty days after it was taken it stops "
    "loading, the cited tests fail and this criterion is a gap again with the gate red. The "
    "run does not need repeating -- every object is still in a write-once store -- so what "
    "renews it is re-running the capture, which is what the expiry is asking for."
)

#: Why a template citation is not a deployed-role citation. Phase 1 draws the same
#: distinction for its two roles. Phase 3's four are now captured and compared; the two the
#: validator and the state machine hold belong to Phase 2's registry and are not.
TEMPLATE_NOT_CAPTURE: Final = (
    "A citation that reads a committed CloudFormation template reads what the account will "
    "be asked for rather than what it holds, and a role widened in a console leaves every "
    "such citation green. The four roles in role_drift.PHASE3_ROLE_TEMPLATES are now "
    "captured from the account and compared, so that gap is closed for them. The two this "
    "criterion is actually about are registered in PHASE2_ROLE_TEMPLATES and are not part "
    "of this phase's capture."
)


def _ids(module: str, name: str, *params: str) -> tuple[str, ...]:
    """Node ids for one test, with its parametrizations spelled out.

    A parametrized test collects only under its full node id, so citing the bare name names
    nothing at all -- which the gate reports as ``cited_test_missing`` rather than passing.
    Building them here also keeps every citation on one line, which matters because a node
    id split across two lines inside a tuple is implicit string concatenation.
    """
    if not params:
        return (f"{module}::{name}",)
    return tuple(f"{module}::{name}[{param}]" for param in params)


def phase3_criteria() -> tuple[CriterionSpec, ...]:
    """The twenty-two Phase 3 acceptance criteria, in the phase plan's order."""
    specs = (
        CriterionSpec(
            number="1",
            statement="A valid run reaches SUCCEEDED.",
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(RUN_EVIDENCE, "test_a_real_container_ran_to_succeeded_under_the_run_id_that_asked_for_it"),
                *_ids(RUN_EVIDENCE, "test_every_committed_run_capture_holds"),
            ),
            supporting_node_ids=(
                *_ids(EXECUTION, "test_the_promoted_profile_resolves_to_the_deployed_queue_and_job_definition"),
                *_ids(EXECUTION, "test_the_job_name_is_the_run_id_so_batch_is_a_third_join"),
                *_ids(PROJECTION, "test_a_successful_run_records_where_its_output_went"),
                *_ids(RUN_EVIDENCE, "test_the_admission_execution_is_named_for_the_run_it_admitted"),
            ),
            scope_limits=(
                (
                    "Proved of one run, and one is the right number for this claim. The "
                    "criterion is that a valid run can reach SUCCEEDED, which one container "
                    "reaching it establishes; that every valid run does is a reliability "
                    "question no finite number of runs answers."
                ),
                (
                    "The proving citation is Batch's answer rather than the platform's. A "
                    "result record saying succeeded is this platform's own projection of an "
                    "event and would say the same thing if the projection were wrong, so what "
                    "is asserted is the service reporting SUCCEEDED with exit code 0 for a job "
                    "whose name is the run id."
                ),
                THE_CAPTURES_EXPIRE,
            ),
        ),
        CriterionSpec(
            number="2",
            statement="Stdout and stderr are available through the recorded log stream.",
            status=CriterionStatus.COVERED,
            proving_node_ids=(
                *_ids(RUN_EVIDENCE, "test_the_container_output_is_readable_through_the_stream_the_job_recorded"),
                *_ids(RUN_EVIDENCE, "test_the_failing_container_printed_its_own_line_before_exiting"),
            ),
            supporting_node_ids=(
                *_ids(INFRA, "test_the_log_group_the_config_names_is_the_one_the_container_writes_to"),
                *_ids(INFRA, "test_execution_targets_config_names_exactly_what_the_templates_create"),
            ),
            scope_limits=(
                (
                    "Not pilot-blocking, and the plan's own call. Losing the logs of a run that "
                    "otherwise behaved costs the user their diagnosis rather than their money or "
                    "their record: the job ran, the result still joins to it, and the spend happened "
                    "either way."
                ),
                (
                    "Proved on a run that succeeded and one that failed, deliberately. Logs are "
                    "read when something went wrong, so a configuration that delivered them only "
                    "for a clean exit would satisfy this criterion and be useless."
                ),
                (
                    "What is asserted is a line the container printed, fetched back out of the "
                    "stream the job recorded. The mutation this criterion exists to catch is "
                    "recording the log group rather than the stream -- it reads as complete and "
                    "resolves to every job on the queue -- so the stream being different from "
                    "the group is asserted beside the content."
                ),
                THE_CAPTURES_EXPIRE,
            ),
        ),
        CriterionSpec(
            number="3",
            statement="The S3 result manifest matches the logical run and the Batch job.",
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(RUN_EVIDENCE, "test_the_result_the_attempt_and_the_batch_job_each_name_the_next"),
            ),
            supporting_node_ids=(
                *_ids(PROJECTION, "test_the_result_joins_to_the_attempt_and_the_attempt_to_the_batch_job"),
                *_ids(PROJECTION, "test_one_attempt_gets_one_id_whichever_event_describes_it"),
                *_ids(RUN_EVIDENCE, "test_an_attempt_naming_another_job_does_not_hold"),
            ),
            scope_limits=(
                (
                    "Three links rather than two ends. The result names an attempt, the attempt "
                    "names a scheduler job, and the job's name is the run id; a check of only "
                    "the first and last would pass while the middle pointed at somebody else's "
                    "container, which is exactly what a result manifest describing the wrong "
                    "job looks like. The supporting citation is the mutation case that holds "
                    "the middle link to being checked."
                ),
                (
                    "The committed results record their output location under the older "
                    "outputs/{run_id}/ prefix rather than the teams/{team}/runs/{run_id}/ shape "
                    "output_prefix() now produces. These runs predate that fix and wrote no "
                    "output, so what is asserted is that the location was recorded and names "
                    "the run, not that it has the shape the code now emits."
                ),
                THE_CAPTURES_EXPIRE,
            ),
        ),
        CriterionSpec(
            number="4",
            statement="A failed command reaches FAILED with its reason preserved.",
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(RUN_EVIDENCE, "test_a_deliberate_non_zero_exit_reached_failed_with_the_code_preserved"),
            ),
            supporting_node_ids=(
                *_ids(PROJECTION, "test_a_failed_run_records_the_failure_rather_than_the_nearest_success"),
                *_ids(PROJECTION, "test_a_job_stopped_before_any_attempt_began_still_records_that_it_stopped"),
                *_ids(RUN_EVIDENCE, "test_a_timeout_and_a_non_zero_exit_are_distinguishable_in_the_record"),
            ),
            scope_limits=(
                (
                    "The exit code is what makes this a preserved reason rather than a recorded "
                    "outcome. A command that exits three and a job the scheduler kills are both "
                    "failed in the lineage store; only the container's exit code separates them, "
                    "and the supporting citation holds the two apart so that this criterion and "
                    "the timeout one cannot come to rest on the same observation."
                ),
                THE_CAPTURES_EXPIRE,
            ),
        ),
        CriterionSpec(
            number="5",
            statement="Cancellation is authorized, applied, and recorded.",
            status=CriterionStatus.GAP,
            supporting_node_ids=(
                *_ids(PROJECTION, "test_a_termination_this_platform_asked_for_is_recorded_as_cancelled"),
                *_ids(PROJECTION, "test_a_failure_that_merely_mentions_cancellation_is_still_a_failure"),
                *_ids(PROJECTION, "test_a_termination_from_outside_this_platform_understates_rather_than_guesses"),
                *_ids(INFRA, "test_the_states_role_gains_batch_and_ecr_reads_and_no_way_to_stop_a_job"),
            ),
            scope_limits=(
                (
                    "Not pilot-blocking, and the plan's own call. It is bounded by the mandatory "
                    "timeout, which is marked: with a timeout in force, the absence of cancellation "
                    "costs the remainder of one job rather than an open-ended amount, and that bound "
                    "is the whole of what makes this gap survivable."
                ),
            ),
            gaps=(
                (
                    "Nothing in this account may terminate a job today, and the deploy did not "
                    "change that. The plan routes cancellation through a state machine holding "
                    "batch:TerminateJob; no such state machine is written, and every role "
                    "Phase 3 declares deliberately excludes the action. Four runs have "
                    "completed and none of them could have been stopped."
                ),
                NEEDS_A_COMPONENT_BUILT,
            ),
        ),
        CriterionSpec(
            number="6",
            statement="Cancelling the GitHub workflow forwards cancellation to the running job.",
            status=CriterionStatus.GAP,
            supporting_node_ids=(
                *_ids(SUBMIT_WORKFLOW, "test_the_cancellation_step_runs_only_on_a_cancellation_and_last"),
                *_ids(SUBMIT_WORKFLOW, "test_the_cancellation_step_neither_claims_to_stop_a_job_nor_can"),
                *_ids(SUBMIT_WORKFLOW, "test_the_cancellation_notice_is_written_where_a_person_will_find_it"),
            ),
            scope_limits=(
                (
                    "Not pilot-blocking, and the plan requires this one on the pilot limitations "
                    "page in exactly its own words: cancelling the GitHub workflow does not stop the "
                    "Batch job. The harm is not the missing mechanism, it is that the default belief "
                    "is wrong -- somebody who cancels in GitHub believes they have stopped the "
                    "spend. Correcting the belief is what makes the gap survivable, and it is the "
                    "clearest case in the phase of a limitation that works only because a reader can "
                    "act on it."
                ),
            ),
            gaps=(
                (
                    "It does not forward it, and the workflow now says so where an operator "
                    "will read it. The submit job's if: cancelled() step records the run id and "
                    "points at the runbook; it stops nothing, because the admission role holds "
                    "no batch:TerminateJob and the cancellation state machine the plan "
                    "describes has not been built."
                ),
                (
                    "Even once it is built, this check is as much about GitHub's grace period "
                    "being long enough as about the wiring, and the grace period is bounded, "
                    "not configurable, and not guaranteed to be reached at all. That half can "
                    "only be answered by cancelling a real dispatched run mid-job."
                ),
                NEEDS_A_COMPONENT_BUILT,
            ),
        ),
        CriterionSpec(
            number="7",
            statement="Cancelling a fan-out stops every child, not only the parent.",
            status=CriterionStatus.GAP,
            supporting_node_ids=(
                *_ids(EXECUTION, "test_a_fan_out_submits_its_size_and_nothing_else_changes"),
                *_ids(EXECUTION, "test_a_single_container_submits_no_array_properties"),
                *_ids(INFRA, "test_a_fan_out_binding_records_its_size_and_a_single_container_omits_the_key"),
            ),
            scope_limits=(
                (
                    "Not pilot-blocking. A pilot user running a fan-out before this passes is "
                    "running one they intend to let finish, which is a limitation a reader can act "
                    "on, and the per-child timeout bounds what happens when they forget. Batch "
                    "imposes no timeout on an array parent, so the bound is per cell rather than on "
                    "the sweep."
                ),
            ),
            gaps=(
                (
                    "The mutation is asserting only the parent, which is what a single "
                    "DescribeJobs on the parent id returns and which would pass while both "
                    "children ran on. Distinguishing them needs a two-cell array job, "
                    "terminated at the parent, with both child job ids observed terminal. All "
                    "four completed runs were single containers, so no array job has ever been "
                    "submitted here, and none of them could have been terminated anyway."
                ),
                NEEDS_A_COMPONENT_BUILT,
            ),
        ),
        CriterionSpec(
            number="8",
            statement="A mandatory timeout terminates a runaway job.",
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(RUN_EVIDENCE, "test_a_runaway_job_was_stopped_by_the_timeout_the_manifest_asked_for"),
            ),
            supporting_node_ids=(
                *_ids(EXECUTION, "test_every_submit_carries_a_timeout_including_the_shortest_manifest"),
                *_ids(EXECUTION, "test_the_timeout_sent_to_batch_is_the_manifest_runtime_in_seconds", "1-3600", "2-7200", "13-46800", "0.5-1800"),
                *_ids(EXECUTION, "test_the_runtime_bound_is_rounded_down_rather_than_up"),
                *_ids(INFRA, "test_the_job_definition_carries_a_timeout_and_a_retry_floor_of_its_own"),
                *_ids(RUN_EVIDENCE, "test_a_timeout_and_a_non_zero_exit_are_distinguishable_in_the_record"),
            ),
            scope_limits=(
                (
                    "Two halves, and the supporting citations carry the first: the submit "
                    "request always sends a Timeout, for every fixture including the one with "
                    "no explicit runtime. That is the half that usually rots and it is also the "
                    "half that cannot fail visibly, because a duration Batch ignores looks "
                    "identical in the request."
                ),
                (
                    "The proving citation is the service acting on it. A command that would "
                    "have run 600 seconds was given 180 and Batch stopped it, reporting 'Job "
                    "attempt duration exceeded timeout' and no container exit code. The absent "
                    "exit code is the load-bearing part: a job the scheduler killed never got "
                    "to return a status, so anything else there would mean the command finished "
                    "on its own and the timeout was a coincidence."
                ),
                (
                    "Proved at 180 seconds rather than at a production bound. What that "
                    "establishes is that Batch enforces the number this platform sends; that a "
                    "particular workload's bound is the right one is a different question and "
                    "not this criterion's."
                ),
                THE_CAPTURES_EXPIRE,
            ),
        ),
        CriterionSpec(
            number="9",
            statement=(
                "An invalid queue, job definition, role or override is rejected before "
                "submission."
            ),
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(RUN_EVIDENCE, "test_a_profile_with_nowhere_to_run_was_refused_and_started_nothing"),
                *_ids(RUN_EVIDENCE, "test_a_refused_run_wrote_its_intent_and_decision_and_nothing_past_them"),
            ),
            supporting_node_ids=(
                *_ids(EXECUTION, "test_every_provisioned_profile_is_backed_and_every_target_names_a_provisioned_one"),
                *_ids(EXECUTION, "test_a_priced_but_unprovisioned_profile_has_nowhere_to_go"),
                *_ids(EXECUTION, "test_an_unprovisioned_profile_is_a_refusal_rather_than_a_crash"),
                *_ids(EXECUTION, "test_the_two_ways_of_having_nowhere_to_run_are_distinguishable_in_the_record"),
                *_ids(INFRA, "test_execution_targets_config_names_exactly_what_the_templates_create"),
                *_ids(RUN_EVIDENCE, "test_a_refusal_whose_run_started_a_job_anyway_does_not_hold"),
            ),
            scope_limits=(
                (
                    "One of the four the criterion names was exercised, and it is worth saying "
                    "which rather than letting one refusal read as four. A live submission "
                    "overrode the compute profile to gpu-1xa10g -- priced in the catalog, "
                    "backed by nothing -- and admission refused it with reason "
                    "no_execution_target before submission. An invalid queue, job definition or "
                    "role has not been submitted to the account; those three are unreachable "
                    "through the dispatch form, which resolves all three from deployed "
                    "configuration the submitter never sees."
                ),
                (
                    "The absence of a Batch job is half the claim and it is recorded rather "
                    "than implied. ListJobs answers one status at a time, so an absence "
                    "established without naming the statuses searched is an absence established "
                    "nowhere -- and the case that matters is a refused submission sitting in "
                    "RUNNABLE, which a search of the terminal statuses would miss. The capture "
                    "records all seven and the supporting citation fails if it stops."
                ),
                THE_CAPTURES_EXPIRE,
            ),
        ),
        CriterionSpec(
            number="10",
            statement=(
                "Duplicate or ambiguous submission handling does not silently create an "
                "untracked job."
            ),
            status=CriterionStatus.GAP,
            pilot_blocking=True,
            supporting_node_ids=(
                *_ids(EXECUTION, "test_the_job_name_is_the_run_id_so_batch_is_a_third_join"),
                *_ids(INFRA, "test_the_binding_write_is_conditional_and_checksummed_like_every_lineage_write"),
                *_ids(SUBMIT_WORKFLOW, "test_an_execution_that_already_exists_under_this_run_id_is_a_success"),
            ),
            gaps=(
                (
                    "Three mechanisms, each of which has to be observed separately: "
                    "ExecutionAlreadyExists on a second start with the same name, a 412 "
                    "PreconditionFailed on the binding's conditional write, and the Batch job "
                    "name being the run id so a second job would be visible. The mutation that "
                    "defeats all three at once is minting a fresh run id inside AWS, which no "
                    "local test can see. Four runs have completed and each carried its own run "
                    "id, so none of the three has ever been exercised."
                ),
                (
                    "The run that closes this is cheap and specific: re-running the submit job "
                    "of a workflow run that already succeeded. It downloads the same compiled "
                    "submission, so the run id is reused, and StartExecution must answer "
                    "ExecutionAlreadyExists -- which the workflow treats as success, and which "
                    "is the counter-intuitive behaviour most worth having a captured record of."
                ),
                CAPTURE_A_RUN_AIMED_AT_IT,
            ),
        ),
        CriterionSpec(
            number="11",
            statement="Event duplicates do not create conflicting terminal state.",
            status=CriterionStatus.GAP,
            supporting_node_ids=(
                *_ids(PROJECTION, "test_a_replayed_event_projects_to_a_byte_identical_record"),
                *_ids(PROJECTION, "test_the_event_id_is_the_eventbridge_id_and_is_a_legal_event_id"),
                *_ids(PROJECTION, "test_two_deliveries_of_one_event_deduplicate_rather_than_conflict"),
                *_ids(PROJECTION, "test_a_redelivered_event_is_refused_by_the_store_and_that_is_success"),
                *_ids(INFRA, "test_the_recorder_role_holds_no_batch_action_at_all"),
            ),
            scope_limits=(
                (
                    "Not pilot-blocking, and it argued hard for the marker, because a conflicting "
                    "terminal state is a lineage defect. What decides it the other way is "
                    "visibility: a duplicate event produces a record that disagrees with itself and "
                    "can be seen to, where every criterion marked in this phase produces a record "
                    "that looks fine and is wrong."
                ),
            ),
            gaps=(
                (
                    "The derivation is proved -- the same event projects to byte-identical "
                    "bytes, the id comes from EventBridge rather than being minted, and "
                    "deduplicate_lifecycle_events raises on same-id-different-content. What is "
                    "unproved is the store's half: the same event redelivered and the "
                    "conditional write refusing it, captured."
                ),
                (
                    "The four completed runs wrote sixteen lifecycle records between them and "
                    "EventBridge delivered each event once, so the redelivery path has never "
                    "been taken. It cannot be forced by dispatching another ordinary run; what "
                    "closes this is invoking the deployed recorder with an event payload "
                    "already recorded, which is the redelivery path exactly, and capturing the "
                    "refusal the store answers with."
                ),
                CAPTURE_A_RUN_AIMED_AT_IT,
            ),
        ),
        CriterionSpec(
            number="12",
            statement="No GitHub path can administer Batch or EC2.",
            status=CriterionStatus.GAP,
            pilot_blocking=True,
            supporting_node_ids=(
                *_ids(SUBMIT_WORKFLOW, "test_the_submit_job_gained_no_aws_capability_when_phase_three_arrived"),
                *_ids(SUBMIT_WORKFLOW, "test_the_batch_matrix_attempts_every_action_phase_three_makes_meaningful"),
                *_ids(SUBMIT_WORKFLOW, "test_the_workflow_makes_exactly_these_aws_calls_in_exactly_this_order"),
                *_ids(DENIALS, "test_each_matrix_names_the_role_it_is_a_claim_about"),
                *_ids(DEPLOY_WORKFLOW, "test_the_workflow_never_submits_a_job_and_never_asks_to"),
            ),
            scope_limits=(
                (
                    "Pilot-blocking, and it has no counterpart in the plan's Phase 3 check list -- "
                    "it is Phase 2's gate sentence, carried here because this is the first phase in "
                    "which administering Batch means launching something that bills. Its absence "
                    "lets a GitHub path start compute outside admission altogether, which is "
                    "unbounded spend with no intent record in front of it."
                ),
            ),
            gaps=(
                (
                    "The matrix has now run, and that is the change since this criterion was "
                    "last written. It executes in the submit job before the one call that "
                    "session makes, against a real admission session issued through a "
                    "protected environment, and it attempts all four Batch actions Phase 3 "
                    "makes meaningful. Every one of the four completed submissions passed it; "
                    "a submission whose matrix fails does not proceed."
                ),
                (
                    "What is missing is a committed record of it. The matrix writes its result "
                    "to a GitHub Actions artifact with a thirty-day retention, so the evidence "
                    "exists, is unexpired, and lives somewhere this repository does not read "
                    "and cannot cite. A criterion may not cite an artifact URL any more than it "
                    "may cite an evidence file, so this stays open until the artifact is "
                    "captured into fixtures/evidence/phase-3/ and a test reads it."
                ),
                CAPTURE_A_RUN_AIMED_AT_IT,
            ),
        ),
        CriterionSpec(
            number="13",
            statement="The workload role cannot write to the lineage store or start anything.",
            status=CriterionStatus.GAP,
            pilot_blocking=True,
            supporting_node_ids=(
                *_ids(INFRA, "test_the_workload_role_can_neither_reach_lineage_nor_start_anything"),
                *_ids(
                    INFRA,
                    "test_the_workload_role_writes_only_under_a_runs_prefix_of_the_outputs_bucket",
                ),
                *_ids(DENIALS, "test_a_repository_outside_this_project_is_a_setup_failure"),
            ),
            scope_limits=(
                (
                    "Pilot-blocking, and one of the criteria the plan's own status block names as "
                    "missing from its check list. The workload role is the identity a researcher's "
                    "container runs as. If it can write to the lineage store it can forge the record "
                    "of what it did; if it can start something it can spend outside the admission "
                    "path. Attribution, lineage and money in one role."
                ),
            ),
            gaps=(
                (
                    "Half of this is now closed and it is worth being precise about which. The "
                    "deployed workload role is captured from the account and compared to the "
                    "template that declares it, with no drift, so the mutation this criterion "
                    "used to be most exposed to -- somebody widening the role in the console, "
                    "leaving every template test green -- is caught. What the comparison "
                    "establishes is that the deployed policy grants no lineage write and no "
                    "way to start anything."
                ),
                (
                    "What a policy says and what AWS refuses are different claims, and only the "
                    "second is a denial. The workload matrix runs from inside the container "
                    "under the job role, so it cannot run until a job runs it, and the four "
                    "completed runs all ran commands that printed a line. Closing this also "
                    "needs the probe present in the research image, which this repository does "
                    "not build, so it is a change in another repository before it is a run here."
                ),
                (
                    "A limitation found while capturing, recorded here rather than left for "
                    "Phase 4 to discover. The deployed role permits s3:PutObject under "
                    "teams/*/runs/*, not teams/{team}/runs/*, so the workload role can write "
                    "into any team's output prefix. The template agrees, so this is deliberate "
                    "rather than drift, and for a single-team pilot nothing is misattributed. "
                    "It does mean the cross-team isolation output_prefix() says the teams/ "
                    "segment exists to make expressible is not expressed yet, and the Phase 4 "
                    "check that a workload role cannot reach another team's prefix would fail "
                    "against this role today."
                ),
                CAPTURE_A_RUN_AIMED_AT_IT,
            ),
        ),
        CriterionSpec(
            number="14",
            statement=(
                "The validator resolves the target and cannot submit; the state machine "
                "submits and cannot decide."
            ),
            status=CriterionStatus.GAP,
            pilot_blocking=True,
            supporting_node_ids=(
                *_ids(INFRA, "test_the_states_role_gains_batch_and_ecr_reads_and_no_way_to_stop_a_job"),
                *_ids(INFRA, "test_the_validator_payload_is_built_field_by_field_and_never_forwarded"),
                *_ids(INFRA, "test_submit_to_batch_passes_the_request_through_and_names_no_field_of_it"),
                *_ids(INFRA, "test_the_recorder_role_writes_lineage_and_cannot_make_anything_happen"),
            ),
            scope_limits=(
                (
                    "Pilot-blocking, and it has no counterpart in the plan's check list. The "
                    "mutation it exists to catch -- giving the validator Lambda batch:SubmitJob -- "
                    "would work, and would move the launch out of the state machine's execution "
                    "history, so the record of what started a job would stop describing what started "
                    "it. A launch nothing recorded is the lineage harm in its purest form."
                ),
            ),
            gaps=(
                TEMPLATE_NOT_CAPTURE,
                (
                    "The separation is proved of the committed templates and of the ASL, and "
                    "four runs have exercised it end to end: the validator resolved a target "
                    "and the state machine made the SubmitJob call, which is in its execution "
                    "history. What that does not establish is the negative half -- that the "
                    "validator could not have submitted -- because a role that never tried "
                    "looks exactly like a role that could not."
                ),
                (
                    "The mutation is giving the validator Lambda batch:SubmitJob. It would "
                    "work, and it would move the launch out of the state machine's execution "
                    "history, so the record of what started a job would stop describing what "
                    "started it. That is the lineage harm in its purest form and it is exactly "
                    "the kind a console edit makes."
                ),
                (
                    "Closing this needs the two roles the criterion is about captured and "
                    "compared, the way the four Phase 3 roles now are. They are "
                    "sbsandbox-intern-edullm-admission-lambda and "
                    "sbsandbox-intern-edullm-admission-states, both registered in "
                    "PHASE2_ROLE_TEMPLATES, so the capture belongs to Phase 2's evidence and "
                    "its freshness window rather than being copied into this phase's fixtures."
                ),
            ),
        ),
        CriterionSpec(
            number="15",
            statement="Exactly one compute profile is provisioned, and it is backed.",
            status=CriterionStatus.COVERED,
            proving_node_ids=(
                *_ids(RUN_EVIDENCE, "test_exactly_one_compute_environment_backs_the_one_provisioned_profile"),
            ),
            supporting_node_ids=(
                *_ids(EXECUTION, "test_every_provisioned_profile_is_backed_and_every_target_names_a_provisioned_one"),
                *_ids(EXECUTION, "test_every_target_names_infrastructure_this_project_owns"),
                *_ids(INFRA, "test_the_compute_environment_holds_no_capacity_when_it_is_idle"),
            ),
            scope_limits=(
                (
                    "Not pilot-blocking, and this is the judgement call in the phase most worth "
                    "arguing with. The harmful half of this seam -- refusing a manifest that names a "
                    "profile with nowhere to run, before anything is submitted -- belongs to the "
                    "rejection check above and is marked there. What is left here is placement: a "
                    "profile that is priced and not backed produces a job that cannot start, which "
                    "is visible, bills nothing while it waits, and is bounded by the queue. "
                    "Availability rather than harm."
                ),
                (
                    "Two claims, and the supporting citations carry the first: exactly one "
                    "profile is provisioned in the catalog and exactly one target backs it, "
                    "compared from both files. The proving citation carries the second -- that "
                    "the environment named actually exists, is VALID and ENABLED, and is the "
                    "one the job queue routes to. A template creating a compute environment is "
                    "a request; an environment can be created and land INVALID, in which case "
                    "every job queued to it waits forever with no error anywhere."
                ),
                (
                    "A VALID environment is still not evidence a job can run, which is why "
                    "criterion 1 is separate from this one and rests on a container having "
                    "actually reached SUCCEEDED."
                ),
                THE_CAPTURES_EXPIRE,
            ),
        ),
        CriterionSpec(
            number="16",
            statement="The compute environment holds no capacity when it is idle.",
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(RUN_EVIDENCE, "test_the_compute_environment_held_no_capacity_after_the_runs_finished"),
            ),
            supporting_node_ids=(
                *_ids(INFRA, "test_the_compute_environment_holds_no_capacity_when_it_is_idle"),
            ),
            scope_limits=(
                (
                    "Pilot-blocking, and one of the criteria the plan's own status block names as "
                    "missing from its check list. It is the only entry in the phase whose absence "
                    "bills money continuously with nothing running: an environment left holding "
                    "vCPUs while idle pays for hardware nobody asked for, in a shared account that "
                    "is already carrying a four-figure capacity charge somebody else made."
                ),
                (
                    "minvCpus and desiredvCpus are different facts and only the second can "
                    "catch this. minvCpus is what the template asks for and is asserted from "
                    "the template by the supporting citation; desiredvCpus is what the "
                    "environment is actually holding, and it is the one that can be non-zero "
                    "while nothing runs. The proving citation reads it from the deployed "
                    "environment after all four runs had finished, and it was zero."
                ),
                (
                    "The environment demonstrably does scale, which is what makes the reading "
                    "worth anything. It was observed holding 32 vCPUs while the timeout run was "
                    "in flight and back at zero afterwards, so a capture taken at the wrong "
                    "moment records the non-zero figure and fails this rather than quietly "
                    "reading as idle."
                ),
                THE_CAPTURES_EXPIRE,
            ),
        ),
        CriterionSpec(
            number="17",
            statement=(
                "Every record written by this phase carries an S3-attested ChecksumSHA256 and "
                "a VersionId."
            ),
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(RUN_EVIDENCE, "test_every_lineage_object_carries_an_s3_attested_checksum_and_version"),
            ),
            supporting_node_ids=(
                *_ids(INFRA, "test_the_binding_write_is_conditional_and_checksummed_like_every_lineage_write"),
                *_ids(PROJECTION, "test_every_write_is_conditional_so_a_replay_cannot_overwrite_anything"),
                *_ids(PROJECTION, "test_the_stored_bytes_are_the_canonical_ones_rather_than_a_re_encoding"),
                *_ids(RUN_EVIDENCE, "test_the_bindings_written_before_the_asl_fix_are_recorded_as_permanently_corrupt"),
            ),
            scope_limits=(
                (
                    "Pilot-blocking, on the same reasoning as the equivalent Phase 2 criterion and "
                    "deliberately worded to match it, because the same property recorded two ways in "
                    "two phases is how a split like this rots. The conditional write refuses an "
                    "overwrite, so attestation and versioning are the second line rather than the "
                    "first; what settles it is that no limitations page helps. There is nothing a "
                    "pilot user can do with the sentence that the objects recording their run are "
                    "neither attested nor versioned, and an object that has been altered reads "
                    "exactly like one that has not."
                ),
                (
                    "The writers asking for a checksum and S3 having stored one are different "
                    "claims. The supporting citations carry the first, from the ASL and the "
                    "templates; the proving citation carries the second, from HeadObject with "
                    "checksum mode enabled against every object all four runs wrote. This is "
                    "distinct from the canonical manifest hash and is recorded as such, because "
                    "a reader who conflated them would think one proved the other."
                ),
                (
                    "Attestation is not integrity of content, and the corrupt bindings are the "
                    "proof of that. Three objects here are attested, versioned and intact -- S3 "
                    "holds exactly the bytes it was sent -- and are refused by the contract that "
                    "defines what a binding is. The supporting citation records them, so a "
                    "reader cannot take this criterion as saying the store holds nothing "
                    "malformed."
                ),
                THE_CAPTURES_EXPIRE,
            ),
        ),
        CriterionSpec(
            number="18",
            statement="The EventBridge rule receives only our queue's events.",
            status=CriterionStatus.GAP,
            pilot_blocking=True,
            supporting_node_ids=(
                *_ids(INFRA, "test_the_event_rule_matches_the_job_queue_the_compute_stack_creates"),
                *_ids(INFRA, "test_the_queue_the_states_role_may_submit_to_is_the_queue_that_exists"),
                *_ids(INFRA, "test_the_queue_accepts_deliveries_only_from_our_own_rule_in_our_own_account"),
                *_ids(PROJECTION, "test_a_delivery_that_is_not_ours_is_refused", "foreign-source", "foreign-detail-type"),
                *_ids(PROJECTION, "test_a_job_whose_name_is_not_a_run_id_is_refused"),
            ),
            scope_limits=(
                (
                    "Pilot-blocking, and one of the criteria the plan's own status block names as "
                    "missing from its check list. This is a shared account, and a rule that matched "
                    "every Batch event in it would write lifecycle records under run ids that are "
                    "not ours. The store would then hold entries describing somebody else's job, "
                    "which is the lineage record corrupted by construction rather than by accident."
                ),
            ),
            gaps=(
                (
                    "The pattern names the queue the compute stack creates, compared across "
                    "both files, and the projection refuses a delivery that is not ours as a "
                    "second line. Four runs have now been delivered through the deployed rule "
                    "and every lifecycle record they produced carries their own run id, so the "
                    "rule is demonstrably delivering what it should."
                ),
                (
                    "What is unproved is the other direction, and it is the half the criterion "
                    "is actually about: that no lifecycle record exists whose run id is *not* "
                    "ours. A capture scoped to one run id cannot establish that by "
                    "construction -- it only ever reads keys under the run it was asked for, so "
                    "it would report a clean result in a store full of somebody else's events. "
                    "This is a shared account and a rule matching every Batch event in it would "
                    "write records under run ids we never submitted."
                ),
                (
                    "Closing this needs a different shape of capture rather than another run: "
                    "an inventory of the whole lineage bucket, with every events/ key's run id "
                    "checked against the intents this platform wrote. That is one list call and "
                    "a comparison, and it belongs beside the per-run captures rather than "
                    "inside one."
                ),
            ),
        ),
        CriterionSpec(
            number="19",
            statement="A run is traceable end to end by run id alone.",
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(RUN_EVIDENCE, "test_one_run_id_resolves_to_all_eleven_artifacts"),
                *_ids(RUN_EVIDENCE, "test_the_session_that_started_the_run_came_through_the_approval_gate"),
            ),
            supporting_node_ids=(
                *_ids(EXECUTION, "test_the_job_name_is_the_run_id_so_batch_is_a_third_join"),
                *_ids(INFRA, "test_the_binding_record_the_state_machine_writes_is_the_contract_it_claims_to_be"),
                *_ids(PROJECTION, "test_the_handler_writes_the_four_keys_the_rest_of_phase_three_reads"),
                *_ids(PROJECTION, "test_the_result_joins_to_the_attempt_and_the_attempt_to_the_batch_job"),
                *_ids(RUN_EVIDENCE, "test_the_admission_execution_is_named_for_the_run_it_admitted"),
                *_ids(RUN_EVIDENCE, "test_a_job_whose_name_is_not_the_run_id_does_not_hold"),
            ),
            scope_limits=(
                (
                    "Pilot-blocking, and it is this phase's gate restated as an assertion. Eleven "
                    "artifacts have to resolve from one run id and agree; where they do not, the "
                    "platform has run something it cannot account for afterwards, which is "
                    "attribution and lineage lost together. It is also the one check that fails when "
                    "another passes for the wrong reason, so leaving it out of the pilot set would "
                    "let the rung open on the strength of the very checks it exists to "
                    "cross-examine."
                ),
                (
                    "Eleven artifacts, named rather than counted: a GitHub workflow run, a "
                    "CloudTrail AssumeRoleWithWebIdentity, a Step Functions execution, an "
                    "intent, a decision, a binding, at least one event, an attempt, a result, a "
                    "Batch job id and a log stream. They are enumerated in "
                    "phase3_capture.TRACEABLE_ARTIFACTS and asserted by name, because a check "
                    "that counted eleven would go on passing after somebody removed one and "
                    "added another."
                ),
                (
                    "Proved of one run of the four, and which one is not arbitrary. The other "
                    "three were submitted before the ASL fix and carry bindings that will never "
                    "load, so ten of their eleven artifacts resolve and the eleventh cannot -- "
                    "and they are reported as not traceable rather than as nearly traceable. "
                    "That is the criterion working: an unbroken chain is the claim, and a chain "
                    "missing a link is not a chain."
                ),
                (
                    "The session is joined through the StartExecution call that names this run "
                    "id, not by recency. Every submission assumes the same role, so a capture "
                    "that took the most recent session would name one belonging to a different "
                    "run and still look complete; the subject claim carrying "
                    ":environment:run-approval-lead is what additionally shows it was issued "
                    "past the approval gate."
                ),
                THE_CAPTURES_EXPIRE,
            ),
        ),
        CriterionSpec(
            number="20",
            statement=(
                "The deployer's unscoped actions are exactly the measured ones, in two "
                "statements separated by why each is unscoped."
            ),
            status=CriterionStatus.COVERED,
            proving_node_ids=(
                *_ids(DEPLOYER, "test_the_six_measured_actions_are_on_star_and_in_a_statement_of_their_own"),
                *_ids(DEPLOYER, "test_the_only_other_unscoped_statement_is_the_read_only_ec2_describes"),
            ),
            supporting_node_ids=(
                *_ids(DEPLOYER, "test_every_scoped_phase3_arn_carries_the_project_prefix_or_is_a_named_exception"),
                *_ids(DEPLOYER, "test_the_network_scope_can_change_egress_and_can_never_open_a_port"),
                *_ids(DEPLOYER, "test_the_batch_scopes_cover_all_three_resource_types_the_stack_creates"),
                *_ids(DEPLOYER, "test_iam_pass_role_is_still_the_only_iam_action_the_whole_role_holds"),
                *_ids(DEPLOYER, "test_pass_role_names_four_whole_roles_and_never_a_prefix"),
                *_ids(EC2, "test_the_declared_action_list_matches_the_probes_actually_built"),
            ),
            scope_limits=(
                (
                    "Not pilot-blocking, and the call took an argument. A deployment role scoped "
                    "more widely than measured is a real exposure in a shared account -- it is how "
                    "somebody else's stack gets deleted. What keeps it off the pilot list is who can "
                    "reach it: this role is assumed by the deployment workflow and by nothing a "
                    "pilot user touches, so its absence widens the build team's own path rather than "
                    "the path being opened. The scoping itself is asserted by the citations beside "
                    "this one; what this criterion adds is that the list of unscoped actions cannot "
                    "grow without somebody measuring the addition."
                ),
                (
                    "Stated in two statements rather than the plan's one, and the difference is "
                    "the point. Six actions are on \"*\" because the resource-type probe -- run "
                    "with its ValidateTemplate and DescribeStacks controls -- found they support "
                    "no resource-level permission. Ten read-only ec2:Describe* actions are on "
                    "\"*\" because EC2 describes are account-wide by the service's own model. "
                    "Folding them together would let a reader believe the probe measured all "
                    "sixteen, and would let an unmeasured action join the measured list without "
                    "anybody noticing."
                ),
                (
                    "This reads the committed template, which is the right thing to read: the "
                    "criterion is about what the repository asks for, and the failure it "
                    "prevents is a half-finished stack found afterwards, which this repository "
                    "has now hit twice. Whether the deployed role matches the template is "
                    "criterion 14's problem and is a gap."
                ),
            ),
        ),
        CriterionSpec(
            number="21",
            statement=(
                "The networking the compute environment uses is recorded, with its terms."
            ),
            status=CriterionStatus.COVERED,
            proving_node_ids=(
                *_ids(RUN_EVIDENCE, "test_the_networking_the_compute_environment_uses_is_recorded"),
            ),
            supporting_node_ids=(
                *_ids(MEASUREMENTS, "test_the_capture_is_committed_and_inside_its_freshness_window"),
                *_ids(MEASUREMENTS, "test_the_vpc_quota_has_room_for_a_vpc_we_own"),
                *_ids(MEASUREMENTS, "test_the_subnet_list_excludes_any_zone_that_does_not_offer_the_instance_type"),
                *_ids(MEASUREMENTS, "test_the_capture_records_how_it_was_measured"),
                *_ids(INFRA, "test_the_vpc_is_created_unconditionally_because_the_quota_landed"),
                *_ids(INFRA, "test_the_subnets_exclude_the_zone_that_cannot_hold_the_instance_type"),
                *_ids(INFRA, "test_the_compute_environment_places_into_exactly_the_subnets_the_network_exports"),
            ),
            scope_limits=(
                (
                    "Not pilot-blocking. What is missing is a record of which VPC, subnets and "
                    "security group the environment ends up on, and somebody who wants it can read "
                    "it off the deployed stack. Nothing is spent, lost or misattributed by its "
                    "absence; what is lost is a later reader's ability to reconstruct the placement "
                    "without opening a console, which is a reviewer's need rather than a pilot "
                    "user's."
                ),
                (
                    "The terms are not the ones the plan expected, and they are better. The "
                    "plan assumed a borrowed VPC and called it the phase's largest known "
                    "limitation; the L-F678F1CE quota increase from five to ten was filed and "
                    "applied on 2026-07-27, and infra/batch-network.yaml creates our own VPC "
                    "unconditionally. Ownership is settled, so what this criterion records is a "
                    "placement rather than a dependency to carry."
                ),
                (
                    "The proving citation reads the deployed environment rather than the "
                    "template. A stack applied from a laptop can land somewhere other than "
                    "where its template says, and a record copied from the template would agree "
                    "with itself forever; these are the VPC, subnet and security group ids the "
                    "environment is actually configured with, read back from the account."
                ),
                THE_CAPTURES_EXPIRE,
            ),
        ),
        CriterionSpec(
            number="22",
            statement="The image-scan decision has been answered.",
            status=CriterionStatus.COVERED,
            proving_node_ids=(
                *_ids(DECISIONS, "test_the_scan_question_is_gone_because_it_was_answered"),
                *_ids(SCAN, "test_the_shipped_policy_blocks_on_criticals"),
                *_ids(SCAN, "test_the_shipped_policy_names_the_denial_condition"),
                *_ids(SCAN, "test_the_denial_condition_is_wired_to_the_fact"),
            ),
            supporting_node_ids=(
                *_ids(SCAN, "test_a_blocking_finding_without_an_exception_is_refused"),
                *_ids(SCAN, "test_a_blocking_finding_with_a_recorded_exception_runs"),
                *_ids(SCAN, "test_no_scan_at_all_is_refused_rather_than_assumed_clean"),
                *_ids(SCAN, "test_the_shipped_registry_covers_the_only_published_image"),
                *_ids(SCAN, "test_the_shipped_registry_excepts_nothing_it_does_not_explain"),
                *_ids(SCAN, "test_both_production_callers_evaluate_the_scan_gate"),
            ),
            scope_limits=(
                (
                    "Not pilot-blocking, and this is the one place in the phase where the ladder's "
                    "test comes out under-inclusive, which is worth recording rather than stretching "
                    "one of its four harms to cover it. Running a container with unreviewed critical "
                    "findings is a security exposure; it is not money, data, attribution or the "
                    "lineage record, which are the four the test names. So the test answers no, and "
                    "the reservation is written down here so that the next person applying it to a "
                    "vulnerability question knows it has been applied once and did not obviously "
                    "fit."
                ),
                (
                    "The mutation this criterion exists to catch is leaving the open-decisions "
                    "entry in place and marking the criterion covered, which is the exact shape "
                    "of a question settled by accident. So the proving citation is that the "
                    "entry is gone *and* that the answer is enforced somewhere, rather than "
                    "either alone."
                ),
                (
                    "The answer went a way the register did not list as obvious: block unless "
                    "an exception is recorded, enforced at admission rather than at publish, "
                    "because ECR scans after the push and a publish-time refusal would leave "
                    "that commit permanently unpublishable. The four criticals in the only "
                    "published image are carried by a recorded exception naming that digest, "
                    "which is a decision somebody took in writing rather than a threshold "
                    "quietly set above them."
                ),
                (
                    "It is enforced in code and configuration this repository commits and "
                    "admission reads. What this criterion does not say is that the scan gate "
                    "has ever refused a real submission: the four completed runs all named an "
                    "image whose four criticals are carried by a recorded exception, so the "
                    "gate was evaluated and passed every time. A refusal on scan findings has "
                    "not been observed, and the rejection that has been -- an unprovisioned "
                    "compute profile -- travelled a different path."
                ),
            ),
        ),
    )
    validate_criterion_specs(specs)
    if len(specs) != PHASE3_CRITERION_COUNT:
        raise ValueError(
            f"Phase 3 records {PHASE3_CRITERION_COUNT} criteria; this definition has "
            f"{len(specs)}"
        )
    return specs
