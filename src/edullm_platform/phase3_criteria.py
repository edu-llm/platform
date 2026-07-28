"""The Phase 3 acceptance criteria and the tests that are cited for each one.

Phase 3 takes one manifest Phase 2 admits and turns the accepted decision into one
digest-pinned CPU container on AWS Batch, with the binding, the lifecycle events, the
attempt and the result landing write-once in the lineage bucket beside the intent and the
decision. This module records the twenty-two checks the phase must satisfy, against the
contract in :mod:`edullm_platform.criteria`.

**Twenty of the twenty-two are gaps, and the reason is one sentence: nothing has been
deployed.** Wave 5 -- the laptop IAM stacks, the CI stacks, and the live matrix that
exercises them -- is deliberately held. No Batch job has ever run in this account, no
compute environment exists, no lifecycle event has been delivered, and no Phase 3 record
has been written to S3. Nineteen of the twenty-two checks name something that can only be
established by observing that, and the twentieth needs committed captures of it.

So they are ``GAP`` and not ``DEFERRED``, and the distinction is the whole point of having
two words. A deferral is a decision not to do something, with a written trigger that makes
it live again; the ``team_verified`` deferral Phase 0 and Phase 2 both carry is one,
because nothing about it is unfinished -- the configuration is empty on purpose. Phase 3's
live checks are not postponed. They are unfinished work with a deploy in front of them, and
recording them as deferrals would make ``tools/validate_phase3.py`` exit 0 against a phase
whose central claim -- that a container ran -- nothing has ever observed. **The gate exiting
1 today is the report working.**

**Which criteria close only when the live matrix runs.** Every criterion here except 20 and
22. Concretely, in the order the plan's Wave 5 reaches them: 1, 2, 3, 4, 10, 17 and 19 close
on one accepted run to ``SUCCEEDED`` and its capture; 8 on a job stopped by its timeout; 5,
6 and 7 on the three cancellation cases; 11 and 18 on a redelivered EventBridge event; 9 and
15 on a refused unprovisioned profile and a ``DescribeComputeEnvironments`` against the live
environment; 12 and 13 on the two denial matrices run from real sessions; 14 and 16 on
re-capturing the deployed roles and reading the idle environment back; and 21 on recording
the networking the environment ends up on. Each one's ``gaps`` says what specifically.

**What is covered rests on committed artifacts.** Criterion 20 reads a CloudFormation
template this repository commits, and 22 reads a decision that has been answered and moved
to where it is enforced. Neither depends on the account, which is why neither waits.

**Two of the plan's checks are stated differently here than in the plan, and the difference
is deliberate rather than drift.**

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
and ``infra/batch-network.yaml`` creates our own VPC unconditionally. So the terms the
criterion has to record are different terms -- ownership settled rather than a dependency
to carry -- and the part that remains open is narrower: the environment is not deployed, so
nothing records the ids it actually uses.

**Thirteen of the twenty-two are pilot-blocking, and every one of them is a gap, so this
phase is not pilot-ready.** The master plan resolves Phase 3 into eleven checks and marks
six; those eleven are criteria 1 to 11 here, in order, so that part of the split is the
plan's markers carried across rather than a judgement. The remaining eleven criteria had no
counterpart in the plan's list, and seven of them are marked here: the four that keep a
GitHub path, a container, a validator and a state machine inside their own authority; the
one that stops an idle compute environment billing; the one that attests what the lineage
store holds; and the one that makes a run traceable by run id alone, which is the phase's
gate restated as an assertion.

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
    "NEEDS_THE_LIVE_MATRIX",
    "NOTHING_IS_DEPLOYED",
    "PHASE3_CRITERION_COUNT",
    "TEMPLATE_NOT_CAPTURE",
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

#: Why nineteen criteria are gaps. Written once because it is the same sentence nineteen
#: times, and a reader who has met it once should not have to check whether the nineteenth
#: wording differs. ``gaps`` is a tuple the gate joins with a space, so this is a sibling
#: element rather than a concatenation.
NOTHING_IS_DEPLOYED: Final = (
    "Wave 5 is held. No Phase 3 stack has been applied to the account: there is no compute "
    "environment, no job queue, no job definition, no EventBridge rule, no recorder and no "
    "outputs bucket, and no Batch job has ever run here. Every artifact this criterion "
    "would be proved from is an observation of infrastructure that does not exist."
)

#: What closes a criterion once the deploy has happened. Separate from the sentence above
#: because they are different facts: one is why the criterion is open, the other is the
#: work. A reader deciding what to do next needs the second.
NEEDS_THE_LIVE_MATRIX: Final = (
    "Closing this means running the Wave 5 live matrix, capturing the named artifact with "
    "tools/capture_phase3_evidence.py, sanitizing it through the existing SecretFreeStr and "
    "account-id redaction, committing it under fixtures/evidence/phase-3/, and citing a test "
    "that reads it. A criterion may not cite an evidence file; it cites the test."
)

#: Why a template citation is not a deployed-role citation. Phase 1 draws the same
#: distinction for its two roles, and Phase 3's four have no capture at all yet.
TEMPLATE_NOT_CAPTURE: Final = (
    "A citation here reads a committed CloudFormation template, which is what the account "
    "will be asked for rather than what it holds. The four Phase 3 roles are registered in "
    "role_drift.PHASE3_ROLE_TEMPLATES and none has been deployed, so the comparison that "
    "catches a role widened in the console has nothing to compare."
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
            status=CriterionStatus.GAP,
            pilot_blocking=True,
            supporting_node_ids=(
                *_ids(EXECUTION, "test_the_promoted_profile_resolves_to_the_deployed_queue_and_job_definition"),
                *_ids(EXECUTION, "test_the_job_name_is_the_run_id_so_batch_is_a_third_join"),
                *_ids(PROJECTION, "test_a_successful_run_records_where_its_output_went"),
            ),
            gaps=(
                NOTHING_IS_DEPLOYED,
                (
                    "No test substitutes for this one. What the cited tests prove is that the "
                    "platform would submit the right job and would read a SUCCEEDED event as a "
                    "success; the criterion is that a container ran, which only a captured "
                    "Batch job detail with status SUCCEEDED, its exit code and the run id "
                    "joining it to the intent and decision records can establish."
                ),
                NEEDS_THE_LIVE_MATRIX,
            ),
        ),
        CriterionSpec(
            number="2",
            statement="Stdout and stderr are available through the recorded log stream.",
            status=CriterionStatus.GAP,
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
            ),
            gaps=(
                NOTHING_IS_DEPLOYED,
                (
                    "The mutation this criterion exists to catch is recording the log *group* "
                    "rather than the stream: it reads as complete and resolves to no single "
                    "job. Only fetching a recorded stream back and finding the line the "
                    "container printed distinguishes the two, and no container has printed one."
                ),
                NEEDS_THE_LIVE_MATRIX,
            ),
        ),
        CriterionSpec(
            number="3",
            statement="The S3 result manifest matches the logical run and the Batch job.",
            status=CriterionStatus.GAP,
            pilot_blocking=True,
            supporting_node_ids=(
                *_ids(PROJECTION, "test_the_result_joins_to_the_attempt_and_the_attempt_to_the_batch_job"),
                *_ids(PROJECTION, "test_one_attempt_gets_one_id_whichever_event_describes_it"),
            ),
            gaps=(
                (
                    "This is the one criterion of the twenty that needs no live call, and it "
                    "still cannot be proved: it is a test over committed captures, and nothing "
                    "is committed under fixtures/evidence/phase-3/ except the account "
                    "measurements. The cited test proves the three joins hold in a projection "
                    "built from a synthetic event, which is the mechanism rather than the "
                    "record."
                ),
                NEEDS_THE_LIVE_MATRIX,
            ),
        ),
        CriterionSpec(
            number="4",
            statement="A failed command reaches FAILED with its reason preserved.",
            status=CriterionStatus.GAP,
            pilot_blocking=True,
            supporting_node_ids=(
                *_ids(PROJECTION, "test_a_failed_run_records_the_failure_rather_than_the_nearest_success"),
                *_ids(PROJECTION, "test_a_job_stopped_before_any_attempt_began_still_records_that_it_stopped"),
            ),
            gaps=(
                NOTHING_IS_DEPLOYED,
                (
                    "The mutation is projecting every terminal state to succeeded, which the "
                    "cited test does catch. What it cannot establish is that Batch reports a "
                    "non-zero container exit the way this projection reads it, which needs a "
                    "job that deliberately exits non-zero and a captured detail carrying its "
                    "statusReason and exit code."
                ),
                NEEDS_THE_LIVE_MATRIX,
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
                NOTHING_IS_DEPLOYED,
                (
                    "Nothing in this account may terminate a job today, and that is not only a "
                    "deploy away. The plan routes cancellation through a state machine that "
                    "holds batch:TerminateJob; no such state machine is written, and every "
                    "role Phase 3 declares deliberately excludes the action. So this criterion "
                    "needs a component built as well as a run observed."
                ),
                NEEDS_THE_LIVE_MATRIX,
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
                NEEDS_THE_LIVE_MATRIX,
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
                NOTHING_IS_DEPLOYED,
                (
                    "The mutation is asserting only the parent, which is what a single "
                    "DescribeJobs on the parent id returns and which would pass while both "
                    "children ran on. Distinguishing them needs a two-cell array job, "
                    "terminated at the parent, with both child job ids observed terminal."
                ),
                NEEDS_THE_LIVE_MATRIX,
            ),
        ),
        CriterionSpec(
            number="8",
            statement="A mandatory timeout terminates a runaway job.",
            status=CriterionStatus.GAP,
            pilot_blocking=True,
            supporting_node_ids=(
                *_ids(EXECUTION, "test_every_submit_carries_a_timeout_including_the_shortest_manifest"),
                *_ids(EXECUTION, "test_the_timeout_sent_to_batch_is_the_manifest_runtime_in_seconds", "1-3600", "2-7200", "13-46800", "0.5-1800"),
                *_ids(EXECUTION, "test_the_runtime_bound_is_rounded_down_rather_than_up"),
                *_ids(INFRA, "test_the_job_definition_carries_a_timeout_and_a_retry_floor_of_its_own"),
            ),
            gaps=(
                NOTHING_IS_DEPLOYED,
                (
                    "The testable half is done and is the half that usually rots: the submit "
                    "request always carries a Timeout, for every fixture including the one "
                    "with no explicit runtime, so making the block conditional fails. What is "
                    "unproved is that Batch acts on it -- a job whose command sleeps past "
                    "attemptDurationSeconds, observed FAILED with the timeout reason."
                ),
                NEEDS_THE_LIVE_MATRIX,
            ),
        ),
        CriterionSpec(
            number="9",
            statement=(
                "An invalid queue, job definition, role or override is rejected before "
                "submission."
            ),
            status=CriterionStatus.GAP,
            pilot_blocking=True,
            supporting_node_ids=(
                *_ids(EXECUTION, "test_every_provisioned_profile_is_backed_and_every_target_names_a_provisioned_one"),
                *_ids(EXECUTION, "test_a_priced_but_unprovisioned_profile_has_nowhere_to_go"),
                *_ids(EXECUTION, "test_an_unprovisioned_profile_is_a_refusal_rather_than_a_crash"),
                *_ids(EXECUTION, "test_the_two_ways_of_having_nowhere_to_run_are_distinguishable_in_the_record"),
                *_ids(INFRA, "test_execution_targets_config_names_exactly_what_the_templates_create"),
            ),
            gaps=(
                NOTHING_IS_DEPLOYED,
                (
                    "The refusal is proved locally and the seam between the catalog and the "
                    "targets file is read from both sides. What is unproved is that it happens "
                    "inside AWS: a manifest naming a priced-but-unprovisioned profile reaching "
                    "a decision with accepted false, and no Batch job existing for that run id."
                ),
                NEEDS_THE_LIVE_MATRIX,
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
                NOTHING_IS_DEPLOYED,
                (
                    "Three mechanisms, each of which has to be observed separately: "
                    "ExecutionAlreadyExists on a second start with the same name, a 412 "
                    "PreconditionFailed on the binding's conditional write, and the Batch job "
                    "name being the run id so a second job would be visible. The mutation that "
                    "defeats all three at once is minting a fresh run id inside AWS, which no "
                    "local test can see."
                ),
                NEEDS_THE_LIVE_MATRIX,
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
                NOTHING_IS_DEPLOYED,
                (
                    "The derivation is proved -- the same event projects to byte-identical "
                    "bytes, the id comes from EventBridge rather than being minted, and "
                    "deduplicate_lifecycle_events raises on same-id-different-content. What is "
                    "unproved is the store's half: the same event redelivered and the "
                    "conditional write refusing it, captured."
                ),
                NEEDS_THE_LIVE_MATRIX,
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
                NOTHING_IS_DEPLOYED,
                (
                    "The matrix is written, wired into the submit job before the one call that "
                    "session makes, and attempts all four actions Phase 3 makes meaningful. It "
                    "has never run: it needs a real admission session, which needs a dispatched "
                    "submission through a protected environment. Until then this criterion "
                    "rests on templates, and a role widened in the console leaves every one of "
                    "them green."
                ),
                NEEDS_THE_LIVE_MATRIX,
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
                NOTHING_IS_DEPLOYED,
                TEMPLATE_NOT_CAPTURE,
                (
                    "The workload matrix runs from inside the container under the job role, so "
                    "it cannot run before a job does. The mutation it exists to catch -- "
                    "widening the workload role's S3 scope to the bucket rather than the "
                    "prefix -- is caught by the template test today and would not be caught by "
                    "it after somebody edited the deployed role in the console."
                ),
                NEEDS_THE_LIVE_MATRIX,
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
                NOTHING_IS_DEPLOYED,
                TEMPLATE_NOT_CAPTURE,
                (
                    "The separation is proved of the committed templates and of the ASL. The "
                    "criterion is about deployed roles, and the mutation -- giving the Lambda "
                    "batch:SubmitJob, which would work and would move the launch out of the "
                    "execution history -- is exactly the kind a console edit makes and a "
                    "template test cannot see."
                ),
                NEEDS_THE_LIVE_MATRIX,
            ),
        ),
        CriterionSpec(
            number="15",
            statement="Exactly one compute profile is provisioned, and it is backed.",
            status=CriterionStatus.GAP,
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
            ),
            gaps=(
                NOTHING_IS_DEPLOYED,
                (
                    "The seam is closed: exactly one profile is provisioned in the catalog and "
                    "exactly one target backs it, compared from both files. 'Backed' also means "
                    "the environment exists and is usable, which is a DescribeComputeEnvironments "
                    "showing it VALID and ENABLED -- and a VALID environment is still not "
                    "evidence a job can run, which is why criterion 1 is separate from this one."
                ),
                NEEDS_THE_LIVE_MATRIX,
            ),
        ),
        CriterionSpec(
            number="16",
            statement="The compute environment holds no capacity when it is idle.",
            status=CriterionStatus.GAP,
            pilot_blocking=True,
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
            ),
            gaps=(
                NOTHING_IS_DEPLOYED,
                (
                    "minvCpus is 0 in the template and the test fails if it is raised. The "
                    "criterion is about the account: desiredvCpus observed at 0 after the live "
                    "matrix has finished, which is the reading that would catch an environment "
                    "that scaled up and did not come back down."
                ),
                NEEDS_THE_LIVE_MATRIX,
            ),
        ),
        CriterionSpec(
            number="17",
            statement=(
                "Every record written by this phase carries an S3-attested ChecksumSHA256 and "
                "a VersionId."
            ),
            status=CriterionStatus.GAP,
            pilot_blocking=True,
            supporting_node_ids=(
                *_ids(INFRA, "test_the_binding_write_is_conditional_and_checksummed_like_every_lineage_write"),
                *_ids(PROJECTION, "test_every_write_is_conditional_so_a_replay_cannot_overwrite_anything"),
                *_ids(PROJECTION, "test_the_stored_bytes_are_the_canonical_ones_rather_than_a_re_encoding"),
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
            ),
            gaps=(
                NOTHING_IS_DEPLOYED,
                (
                    "The writers ask for the checksum; whether S3 attested one is a fact about "
                    "the object. It needs HeadObject --checksum-mode ENABLED against the "
                    "binding, one event, the attempt and the result, captured. This is distinct "
                    "from the canonical manifest hash and has to be recorded as such, because "
                    "a reader who conflated them would think one proved the other."
                ),
                NEEDS_THE_LIVE_MATRIX,
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
                NOTHING_IS_DEPLOYED,
                (
                    "The pattern names the queue the compute stack creates, compared across "
                    "both files, and the projection refuses a delivery that is not ours as a "
                    "second line. The criterion also asks that no lifecycle record exist whose "
                    "run id is not ours, which is a statement about what the deployed rule "
                    "actually delivered in a shared account."
                ),
                NEEDS_THE_LIVE_MATRIX,
            ),
        ),
        CriterionSpec(
            number="19",
            statement="A run is traceable end to end by run id alone.",
            status=CriterionStatus.GAP,
            pilot_blocking=True,
            supporting_node_ids=(
                *_ids(EXECUTION, "test_the_job_name_is_the_run_id_so_batch_is_a_third_join"),
                *_ids(INFRA, "test_the_binding_record_the_state_machine_writes_is_the_contract_it_claims_to_be"),
                *_ids(PROJECTION, "test_the_handler_writes_the_four_keys_the_rest_of_phase_three_reads"),
                *_ids(PROJECTION, "test_the_result_joins_to_the_attempt_and_the_attempt_to_the_batch_job"),
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
            ),
            gaps=(
                NOTHING_IS_DEPLOYED,
                (
                    "This is the gate restated as an executable assertion and it is the one "
                    "check that fails if any other passes for the wrong reason: one run id "
                    "resolving to eleven artifacts -- a GitHub run URL, a CloudTrail "
                    "AssumeRoleWithWebIdentity, a Step Functions execution, an intent, a "
                    "decision, a binding, at least one event, an attempt, a result, a Batch job "
                    "id and a log stream -- all present and all agreeing. Six of the eleven "
                    "have never been written."
                ),
                NEEDS_THE_LIVE_MATRIX,
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
            status=CriterionStatus.GAP,
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
            ),
            gaps=(
                (
                    "The terms are not the ones the plan expected, and they are better. The "
                    "plan assumed a borrowed VPC and called it the phase's largest known "
                    "limitation; the L-F678F1CE quota increase from five to ten was filed and "
                    "applied on 2026-07-27, and infra/batch-network.yaml creates our own VPC "
                    "unconditionally. Ownership is settled, and the committed measurements "
                    "record the quota, the request id, the zones and which of them offers the "
                    "instance type."
                ),
                (
                    "What is missing is the other half of the criterion's words: the "
                    "networking the compute environment *uses*. The network stack is not "
                    "deployed, so no VPC, subnet or security-group id exists to record, and "
                    "the committed placement record describes the interim candidate VPC these "
                    "probes were aimed at rather than one this project owns."
                ),
                NEEDS_THE_LIVE_MATRIX,
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
                    "admission reads. Nothing here says the enforcement has ever refused a real "
                    "submission, which is criterion 9's territory and is a gap."
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
