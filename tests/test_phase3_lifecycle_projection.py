"""What one Batch state change becomes, and what it must not become.

Three of these tests exist because the natural implementation passes the other ones. A
projection that minted its own event id, wrote a result on every event it saw, and mapped
whatever Batch said onto the nearest-sounding state would satisfy any test that projected
one delivery and looked at the shape of the answer. What catches each of those is
projecting the *same* delivery twice, projecting a non-terminal one, and reading the
mapping as a whole rather than one entry at a time.

The mapping test is the seam this module is really about. Both sides are read from the
thing itself: the statuses from Batch's own vocabulary as this module records it, and the
legality from the Phase 0 transition table. A test that asserted the table's contents by
hand would report agreement it never checked.

The last section covers the handler, and only for the decisions the projection cannot make:
the four S3 keys, which the state machine and the lifecycle role both depend on being
spelled exactly this way, and what happens to a delivery that will not go.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.canonical import canonical_json_bytes
from edullm_platform.config import load_yaml
from edullm_platform.contracts.execution import ExecutionTarget
from edullm_platform.contracts.identity import ATTEMPT_ID_REGEX
from edullm_platform.contracts.lifecycle import (
    EVENT_ID_PATTERN,
    AttemptTerminalState,
    EventSource,
    RunState,
    deduplicate_lifecycle_events,
    new_event_id,
)
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.vocabulary import RetentionClass
from edullm_platform.execution import batch_submit_request
from edullm_platform.lifecycle_handler import (
    LifecycleEventError,
    binding_key,
    handler,
)
from edullm_platform.lifecycle_projection import (
    BATCH_JOB_STATUSES,
    BATCH_STATUS_PROGRESSION,
    BATCH_STATUS_TO_RUN_STATE,
    CANCELLATION_REASON_MARKERS,
    EVENTBRIDGE_BATCH_DETAIL_TYPE,
    EVENTBRIDGE_BATCH_SOURCE,
    OUTPUT_PREFIX_VARIABLE,
    OUTPUTS_BUCKET,
    UnmappedBatchStatusError,
    UnreadableBatchEventError,
    derived_event_id,
    project_batch_event,
    project_batch_state_change,
    transition_is_recordable,
)

RUN_ID = "run_0198f0a1-2b3c-7d4e-8f01-23456789abcd"
BATCH_JOB_ID = "3f9d1f1e-6b18-4a63-9c0d-2f6d4a1b8c70"
EVENTBRIDGE_EVENT_ID = "9d2f0e5a-1c4b-4c8e-9a3d-7f5b2e6c1a04"
OCCURRED_AT = "2026-07-27T20:15:30Z"
#: The same instant as a datetime, for the tests that call the projection directly rather
#: than through the envelope reader that parses it.
OCCURRED_AT_INSTANT = datetime.fromisoformat(OCCURRED_AT)

#: 2026-07-27T20:05:00Z and six minutes later, both before the envelope's own time, as
#: Batch's epoch milliseconds. Integers, because that is what Batch sends and because a
#: projection that divided them into a float would move every instant it wrote.
STARTED_AT_MS = 1_785_182_700_000
STOPPED_AT_MS = 1_785_183_060_000

CANCELLATION_REASON = f"{CANCELLATION_REASON_MARKERS[0]}: the workflow run was cancelled"

TEAM = "platform"
#: Exactly what contracts/results.py::output_prefix builds and batch_submit_request sends.
#: Written out rather than imported so that a change to that function shows up here as a
#: failure to compare rather than as two sides moving together.
CONTAINER_OUTPUT_PREFIX = f"s3://{OUTPUTS_BUCKET}/teams/{TEAM}/runs/{RUN_ID}/"


def attempt_block(**overrides: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "container": {"exitCode": 0, "taskArn": "arn:aws:ecs:us-east-1:123456789012:task/x"},
        "startedAt": STARTED_AT_MS,
        "stoppedAt": STOPPED_AT_MS,
    }
    block.update(overrides)
    return block


def detail(status: str, **overrides: Any) -> dict[str, Any]:
    """One Batch Job State Change detail, in the shape EventBridge delivers it.

    Attempts are absent by default and supplied per test, because whether the array is
    there is the difference between an event that yields records and one that does not.
    """
    payload: dict[str, Any] = {
        "jobArn": f"arn:aws:batch:us-east-1:123456789012:job/{BATCH_JOB_ID}",
        "jobName": RUN_ID,
        "jobId": BATCH_JOB_ID,
        "jobQueue": "arn:aws:batch:us-east-1:123456789012:job-queue/sbsandbox-intern-edullm-cpu",
        "status": status,
        "createdAt": STARTED_AT_MS - 5_000,
        # THE TOP-LEVEL CONTAINER, WHICH THESE FIXTURES DID NOT HAVE AND THE REAL EVENT
        # ALWAYS DOES. AWS lists it among the required properties of BatchJobStateChange,
        # and its absence here is why nothing noticed that the projection was inventing an
        # output prefix instead of reading the one the container was handed: a fixture
        # cannot contradict a literal it does not carry.
        #
        # The environment is the one batch_submit_request sends, in the shape it sends it.
        "container": {
            "image": "123456789012.dkr.ecr.us-east-1.amazonaws.com/x@sha256:" + "0" * 64,
            "environment": [
                {"name": "EDULLM_RUN_ID", "value": RUN_ID},
                {"name": "EDULLM_TEAM", "value": TEAM},
                {"name": "EDULLM_OUTPUT_PREFIX", "value": CONTAINER_OUTPUT_PREFIX},
            ],
        },
    }
    payload.update(overrides)
    return payload


def envelope(status: str, **overrides: Any) -> dict[str, Any]:
    return {
        "version": "0",
        "id": EVENTBRIDGE_EVENT_ID,
        "detail-type": EVENTBRIDGE_BATCH_DETAIL_TYPE,
        "source": EVENTBRIDGE_BATCH_SOURCE,
        "account": "123456789012",
        "time": OCCURRED_AT,
        "region": "us-east-1",
        "resources": [],
        "detail": detail(status, **overrides),
    }


def project(status: str, **overrides: Any) -> Any:
    return project_batch_event(envelope(status, **overrides))


def succeeded() -> Any:
    return project("SUCCEEDED", attempts=[attempt_block()])


# ---------------------------------------------------------------------------------------
# The collapse, read as a whole
# ---------------------------------------------------------------------------------------


def test_every_batch_status_is_mapped_and_nothing_else_is() -> None:
    """Mutation: delete a status from the mapping.

    A status Batch reports and this module does not map is a gap in the event stream that
    nothing else complains about -- the job carries on, the terminal record still lands,
    and the run looks as though it skipped a state it was in.
    """
    assert set(BATCH_STATUS_TO_RUN_STATE) == set(BATCH_JOB_STATUSES)


def test_an_unmapped_status_is_refused_rather_than_defaulted() -> None:
    """Mutation: fall back to a default state for anything unrecognised.

    A default records an eighth status Batch adds later as whichever state the default
    names, and the first anybody learns of it is a store holding runs that were never in
    the state their events say they were.
    """
    with pytest.raises(UnmappedBatchStatusError):
        project("PROVISIONING", attempts=[])


def test_the_collapse_projects_batch_progression_onto_legal_transitions() -> None:
    """Mutation: map RUNNING to submitted, or STARTING to succeeded.

    Read from both sides: the progression is Batch's, the legality is the Phase 0
    transition table's. A pair that the table forbids means the collapse would write an
    event stream describing a run moving backwards.
    """
    for before_status, after_status in BATCH_STATUS_PROGRESSION:
        before = BATCH_STATUS_TO_RUN_STATE[before_status]
        after = BATCH_STATUS_TO_RUN_STATE[after_status]
        assert transition_is_recordable(before, after), (
            f"{before_status} -> {after_status} projects to {before.value} -> {after.value}, "
            "which the run-state transition table does not permit"
        )


def test_the_progression_covers_every_status_the_mapping_holds() -> None:
    """The legality check is only worth as much as the progression it walks.

    Mutation: drop a status from BATCH_STATUS_PROGRESSION. The pair test would still pass
    and would no longer be checking the status somebody removed.
    """
    walked = {status for pair in BATCH_STATUS_PROGRESSION for status in pair}

    assert walked == set(BATCH_JOB_STATUSES)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("SUBMITTED", RunState.SUBMITTED),
        ("PENDING", RunState.SUBMITTED),
        ("RUNNABLE", RunState.RUNNABLE),
        ("STARTING", RunState.RUNNABLE),
        ("RUNNING", RunState.RUNNING),
        ("SUCCEEDED", RunState.SUCCEEDED),
        ("FAILED", RunState.FAILED),
    ],
)
def test_the_two_lossy_statuses_collapse_downward_rather_than_upward(
    status: str,
    expected: RunState,
) -> None:
    """Mutation: map STARTING to running.

    STARTING is Batch pulling the image; none of the workload's own code has run. Recording
    it as running would make a job that died during the pull read as a workload that ran
    and failed, which sends the next reader to the wrong logs.
    """
    assert BATCH_STATUS_TO_RUN_STATE[status] is expected


# ---------------------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------------------


def test_a_replayed_event_projects_to_a_byte_identical_record() -> None:
    """Mutation: mint the event id with ``new_event_id()`` instead of deriving it.

    That is the natural thing to write, it passes every single-delivery test, and it
    silently breaks deduplication: a redelivery would compute a different S3 key, meet no
    conditional write, and land a second event for something that happened once.
    """
    first = project("RUNNING", attempts=[attempt_block(stoppedAt=None)])
    second = project("RUNNING", attempts=[attempt_block(stoppedAt=None)])

    assert canonical_json_bytes(first.event) == canonical_json_bytes(second.event)
    assert first.event.event_id != new_event_id()


def test_the_event_id_is_the_eventbridge_id_and_is_a_legal_event_id() -> None:
    projected = succeeded()

    assert projected.event.event_id == f"evt_{EVENTBRIDGE_EVENT_ID}"
    assert projected.event.event_id == derived_event_id(EVENTBRIDGE_EVENT_ID)
    assert re.fullmatch(EVENT_ID_PATTERN, projected.event.event_id), (
        "EventBridge ids are UUIDs, which is the whole reason the derived id is legal; a "
        "delivery whose id were not one would have to be minted and would stop deduplicating"
    )


def test_two_deliveries_of_one_event_deduplicate_rather_than_conflict() -> None:
    """The second mechanism, and the one that works when the store is not involved.

    Mutation: derive the event id from the detail rather than from the delivery. Two
    genuinely different events about one job would then collide, and
    ``deduplicate_lifecycle_events`` would raise on records that were both true.
    """
    first = project("RUNNING", attempts=[attempt_block(stoppedAt=None)])
    second = project("RUNNING", attempts=[attempt_block(stoppedAt=None)])

    assert deduplicate_lifecycle_events((first.event, second.event)) == (first.event,)


def test_one_attempt_gets_one_id_whichever_event_describes_it() -> None:
    """Mutation: derive the attempt id from anything the delivery supplies.

    The RUNNING event and the SUCCEEDED event for one attempt have to name the same
    attempt, or the result manifest joins to nothing.
    """
    running = project("RUNNING", attempts=[attempt_block(stoppedAt=None)])
    finished = succeeded()

    assert running.event.attempt_id == finished.event.attempt_id
    assert finished.attempt is not None
    assert ATTEMPT_ID_REGEX.fullmatch(finished.attempt.attempt_id)


def test_two_attempts_of_one_job_get_different_ids() -> None:
    """Mutation: derive the attempt id from the run and the job alone.

    A retried job would then write both attempts to one key, and the conditional write
    would refuse the second -- losing the record of the attempt that actually succeeded.
    """
    retried = project(
        "SUCCEEDED",
        attempts=[
            attempt_block(startedAt=STARTED_AT_MS - 60_000, stoppedAt=STARTED_AT_MS - 30_000),
            attempt_block(),
        ],
    )
    first_only = project("SUCCEEDED", attempts=[attempt_block()])

    assert retried.attempt is not None
    assert first_only.attempt is not None
    assert retried.attempt.attempt_ordinal == 2
    assert retried.attempt.attempt_id != first_only.attempt.attempt_id


# ---------------------------------------------------------------------------------------
# What a terminal event yields, and what a non-terminal one does not
# ---------------------------------------------------------------------------------------


def test_a_terminal_event_yields_an_attempt_whose_window_is_not_inverted() -> None:
    """Mutation: swap ``startedAt`` and ``stoppedAt``.

    The contract's own validator catches it, and this test is what reaches the validator:
    without a terminal projection there is no SchedulerAttempt for it to run on.
    """
    projected = succeeded()

    assert projected.attempt is not None
    assert projected.attempt.started_at <= projected.attempt.ended_at
    assert projected.attempt.scheduler_job_id == BATCH_JOB_ID
    assert projected.attempt.terminal_state is AttemptTerminalState.SUCCEEDED


def test_a_terminal_event_reads_its_instants_from_batch_rather_than_a_clock() -> None:
    """Mutation: use ``datetime.now()`` for either end of the window.

    A projection with a clock in it stops being replayable, and the duration in the record
    becomes the time between the job stopping and the recorder running.
    """
    projected = succeeded()

    assert projected.attempt is not None
    assert projected.attempt.started_at == datetime(2026, 7, 27, 20, 5, tzinfo=UTC)
    assert (projected.attempt.ended_at - projected.attempt.started_at).total_seconds() == 360


@pytest.mark.parametrize("status", ["SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING"])
def test_a_non_terminal_event_yields_no_attempt_and_no_result(status: str) -> None:
    """Mutation: emit a ResultManifest on RUNNING.

    That records an outcome for a job that is still going, into a store that cannot be
    corrected -- and because the key is ``result/{run_id}.json``, the conditional write
    would then refuse the real outcome when it arrived.
    """
    projected = project(status, attempts=[attempt_block(stoppedAt=None)])

    assert projected.attempt is None
    assert projected.result is None
    assert projected.event.state.is_terminal is False


def test_a_successful_run_records_where_its_output_went() -> None:
    """A succeeded result with no output prefix is refused by the contract.

    Mutation: point the prefix at the lineage bucket, which is the bucket the workload role
    must not be able to write to at all.
    """
    projected = succeeded()

    assert projected.result is not None
    assert projected.result.output_prefixes == (CONTAINER_OUTPUT_PREFIX,)
    assert projected.result.retention_class is RetentionClass.STANDARD
    # Both still empty, and both are limitations rather than descriptions. A Batch state
    # change carries what the container was configured with, never what its process did,
    # so the checkpoint it wrote and the W&B run it published are unreachable from here.
    assert projected.result.wandb_run is None
    assert projected.result.checkpoints == ()


def test_the_prefix_recorded_is_the_prefix_the_container_was_handed() -> None:
    """Reads BOTH sides. Mutation: rebuild the prefix here from the run id and the bucket.

    That is what this did, and the literal was wrong for the entire life of the phase
    without anything failing. ``batch_submit_request`` tells the container
    ``teams/{team}/runs/{run_id}/``; the projection recorded ``{run_id}/``. Two answers to
    where a run writes, one of them in an immutable lineage record, and the workload role
    does not even permit the one lineage named. Nothing caught it because no run had
    written an object -- the CPU smoke prints and exits.

    Rebuilding it here would need the team, and the event does not carry one: AWS's
    published schema for BatchJobStateChange lists attempts, container, createdAt,
    dependsOn, jobDefinition, jobId, jobName, jobQueue, parameters, retryStrategy and
    status, and no tags. So the value is read from the container the event describes,
    which is the only place it exists and also the only place that cannot disagree with
    what the job actually ran with.
    """
    manifest = load_yaml(
        Path(__file__).resolve().parents[1] / "fixtures" / "manifests" / "cpu-routine.yaml",
        RunManifest,
    )
    account = "123456789012"
    target = ExecutionTarget(
        compute_profile=manifest.compute_profile,
        region="us-east-1",
        job_queue_arn=f"arn:aws:batch:us-east-1:{account}:job-queue/q",
        job_definition_arn=f"arn:aws:batch:us-east-1:{account}:job-definition/d",
        execution_role_arn=f"arn:aws:iam::{account}:role/e",
        workload_role_arn=f"arn:aws:iam::{account}:role/w",
        log_group="/aws/batch/g",
    )
    submitted = batch_submit_request(
        manifest=manifest,
        target=target,
        run_id=RUN_ID,
        job_definition=target.job_definition_arn,
    )
    told = {
        entry["Name"]: entry["Value"]
        for entry in submitted["ContainerOverrides"]["Environment"]
    }[OUTPUT_PREFIX_VARIABLE]

    # The submitter's own event, carrying exactly what the submitter's own request sends.
    # Comparing the projection against a constant would prove the projection self
    # consistent; comparing it against the request is what holds the two modules together.
    delivered = detail("SUCCEEDED", attempts=[attempt_block()])
    delivered["container"] = {
        "environment": [{"name": OUTPUT_PREFIX_VARIABLE, "value": told}]
    }
    projected = project_batch_state_change(
        eventbridge_event_id=EVENTBRIDGE_EVENT_ID,
        detail=delivered,
        occurred_at=OCCURRED_AT_INSTANT,
    )

    assert told.startswith(f"s3://{OUTPUTS_BUCKET}/teams/{manifest.team}/runs/{RUN_ID}/")
    assert projected.result is not None
    assert projected.result.output_prefixes == (told,)


@pytest.mark.parametrize(
    ("label", "environment"),
    [
        ("the variable is absent", [{"name": "EDULLM_RUN_ID", "value": RUN_ID}]),
        (
            "the prefix names somebody else's bucket",
            [{"name": OUTPUT_PREFIX_VARIABLE, "value": "s3://not-ours/teams/x/runs/y/"}],
        ),
    ],
)
def test_a_succeeded_job_whose_output_cannot_be_located_is_refused(
    label: str, environment: list[dict[str, str]]
) -> None:
    """Mutation: fall back to a plausible prefix, or record an empty one.

    ResultManifest already refuses a succeeded run with no output prefix, and is right to:
    a run that finished and cannot be found is not a run anybody can use. Every job this
    platform submits is handed the variable by batch_submit_request, so a succeeded job
    without a readable one inside our own bucket is an event this record cannot honestly
    complete.

    Refusing is loud -- the event retries and then dead-letters, where an alarm is already
    watching -- and loud is the right volume. A fallback literal would make the write
    succeed and would be indistinguishable from a correct record, which is exactly the
    defect this change removes. An empty tuple would trip the contract anyway, one layer
    later and with a worse message.

    A foreign bucket is refused on the same terms rather than recorded: the value is read
    from the event, so it is only as trustworthy as whoever set the job definition, and
    naming a location this platform neither controls nor can read back would be the record
    endorsing it.
    """
    unlocatable = detail("SUCCEEDED", attempts=[attempt_block()])
    unlocatable["container"] = {"environment": environment}

    with pytest.raises(UnreadableBatchEventError, match=OUTPUT_PREFIX_VARIABLE):
        project_batch_state_change(
            eventbridge_event_id=EVENTBRIDGE_EVENT_ID,
            detail=unlocatable,
            occurred_at=OCCURRED_AT_INSTANT,
        )


def test_a_failed_job_with_no_prefix_is_still_recorded() -> None:
    """Mutation: refuse every job whose prefix cannot be read, not only the succeeded ones.

    A failed run may have produced nothing and the contract permits it to name nowhere. The
    reason to record it anyway is that the failure itself is the evidence -- refusing here
    would dead-letter the event and leave no lineage at all for a run that demonstrably
    happened, which is a worse outcome than a result manifest with an empty prefix list.
    """
    failed = detail("FAILED", attempts=[attempt_block(container={"exitCode": 1})])
    failed["container"] = {"environment": [{"name": "EDULLM_RUN_ID", "value": RUN_ID}]}

    projected = project_batch_state_change(
        eventbridge_event_id=EVENTBRIDGE_EVENT_ID,
        detail=failed,
        occurred_at=OCCURRED_AT_INSTANT,
    )

    assert projected.result is not None
    assert projected.result.output_prefixes == ()


def test_the_result_joins_to_the_attempt_and_the_attempt_to_the_batch_job() -> None:
    """The three-way join criterion 3 asserts over committed captures, at its source.

    Mutation: break any one of the three. Each side is individually plausible and only the
    comparison catches it.
    """
    projected = succeeded()

    assert projected.attempt is not None
    assert projected.result is not None
    assert projected.result.run_id == projected.attempt.run_id == projected.event.run_id
    assert projected.result.attempt_id == projected.attempt.attempt_id
    assert projected.attempt.scheduler_job_id == BATCH_JOB_ID


def test_a_failed_run_records_the_failure_rather_than_the_nearest_success() -> None:
    """Mutation: project every terminal state to succeeded.

    A ResultManifest with outcome ``succeeded`` also has to carry an output prefix, so the
    mutation would pass a shape check; only the outcome itself catches it.
    """
    projected = project("FAILED", attempts=[attempt_block(container={"exitCode": 1})])

    assert projected.event.state is RunState.FAILED
    assert projected.result is not None
    assert projected.result.outcome is AttemptTerminalState.FAILED


def test_a_job_stopped_before_any_attempt_began_still_records_that_it_stopped() -> None:
    """There is no window to describe, and inventing one would be worse than omitting it.

    Mutation: fall back to the job's ``createdAt`` as the attempt start, which puts a
    duration in an immutable record that nothing measured.
    """
    projected = project("FAILED", attempts=[], statusReason=CANCELLATION_REASON)

    assert projected.event.state is RunState.CANCELLED
    assert projected.attempt is None
    assert projected.result is None


# ---------------------------------------------------------------------------------------
# Cancellation, which Batch does not have a status for
# ---------------------------------------------------------------------------------------


def test_a_termination_this_platform_asked_for_is_recorded_as_cancelled() -> None:
    """Batch reports FAILED for a terminated job; the reason is the only signal.

    Mutation: read every FAILED as a failure, which would make criterion 5's cancelled
    lifecycle event impossible to produce.
    """
    projected = project(
        "FAILED",
        attempts=[attempt_block(statusReason=CANCELLATION_REASON)],
        statusReason=CANCELLATION_REASON,
    )

    assert projected.event.state is RunState.CANCELLED
    assert projected.result is not None
    assert projected.result.outcome is AttemptTerminalState.CANCELLED


def test_a_failure_that_merely_mentions_cancellation_is_still_a_failure() -> None:
    """Mutation: search the reason for the word rather than matching this platform's marker.

    A workload whose own error text mentions a cancelled request would then be recorded as
    a run somebody stopped, which is a claim about a human decision that nobody made.
    """
    projected = project(
        "FAILED",
        attempts=[attempt_block(container={"exitCode": 1})],
        statusReason="Essential container in task exited: the upload was cancelled",
    )

    assert projected.event.state is RunState.FAILED


def test_a_termination_from_outside_this_platform_understates_rather_than_guesses() -> None:
    """What the marker-only detection misses, asserted so it stays a decision.

    Mutation: none -- this records a limitation. A job stopped from the console carries a
    reason this platform did not write and is recorded as failed. That understates a human
    decision and never invents one, which is the direction to be wrong in for a record that
    cannot be edited afterwards.
    """
    projected = project(
        "FAILED",
        attempts=[attempt_block()],
        statusReason="Terminated by user through the console",
    )

    assert projected.event.state is RunState.FAILED


def test_only_a_failed_job_can_be_read_as_cancelled() -> None:
    """Mutation: apply the marker to any status.

    A SUCCEEDED job whose reason happened to carry the marker would be recorded as
    cancelled, which would say a run that produced output never finished.
    """
    projected = project(
        "SUCCEEDED",
        attempts=[attempt_block()],
        statusReason=CANCELLATION_REASON,
    )

    assert projected.event.state is RunState.SUCCEEDED


# ---------------------------------------------------------------------------------------
# What the projection refuses to read
# ---------------------------------------------------------------------------------------


def test_every_event_names_the_scheduler_as_its_source() -> None:
    """Mutation: record these as ``platform``.

    The platform's own events are the ones the state machine writes. Attributing Batch's to
    the platform would make the two indistinguishable in the stream.
    """
    assert succeeded().event.source is EventSource.SCHEDULER


def test_the_event_time_is_the_delivery_time_and_carries_an_offset() -> None:
    projected = succeeded()

    assert projected.event.occurred_at == datetime(2026, 7, 27, 20, 15, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "damage",
    [
        {"source": "aws.ecs"},
        {"detail-type": "ECS Task State Change"},
        {"id": ""},
        {"time": "not a timestamp"},
        {"time": "2026-07-27T20:15:30"},
        {"detail": "not an object"},
    ],
    ids=[
        "foreign-source",
        "foreign-detail-type",
        "no-id",
        "unparseable-time",
        "naive-time",
        "no-detail",
    ],
)
def test_a_delivery_that_is_not_ours_is_refused(damage: dict[str, Any]) -> None:
    """Mutation: trust the envelope because the rule is scoped.

    The rule is a deployed artifact one edit away from being widened, and this function is
    reachable by anything that can write to its queue. In a shared account an unscoped
    ``aws.batch`` pattern delivers other teams' job state changes.
    """
    with pytest.raises(UnreadableBatchEventError):
        project_batch_event({**envelope("RUNNING", attempts=[]), **damage})


def test_a_job_whose_name_is_not_a_run_id_is_refused() -> None:
    """Mutation: accept the job name as the run id whatever it looks like.

    That is what an account-wide rule pattern would feed this function, and the result
    would be another team's identifier written into this project's lineage store.
    """
    with pytest.raises(UnreadableBatchEventError):
        project("RUNNING", jobName="somebody-elses-job", attempts=[])


def test_the_envelope_is_read_from_json_exactly_as_sqs_delivers_it() -> None:
    """The recorder receives the envelope as a string inside an SQS body.

    Mutation: parse the detail with anything that coerces types. Batch's instants are
    integers and a parser that turned them into floats would move every timestamp.
    """
    body = json.dumps(envelope("SUCCEEDED", attempts=[attempt_block()]))
    projected = project_batch_event(json.loads(body))

    assert projected.attempt is not None
    assert projected.attempt.started_at == datetime(2026, 7, 27, 20, 5, tzinfo=UTC)


def test_a_detail_missing_the_fields_a_record_needs_is_refused() -> None:
    """Mutation: default a missing job id to an empty string.

    A SchedulerAttempt with no scheduler job id joins to nothing, and the contract would
    refuse it -- as a validation error from inside the handler rather than as a delivery
    this module could say was unreadable.
    """
    with pytest.raises(UnreadableBatchEventError):
        project_batch_state_change(
            eventbridge_event_id=EVENTBRIDGE_EVENT_ID,
            detail={"jobName": RUN_ID, "status": "RUNNING"},
            occurred_at=datetime(2026, 7, 27, 20, 15, 30, tzinfo=UTC),
        )


# ---------------------------------------------------------------------------------------
# The handler around it, which is where the key layout is decided
# ---------------------------------------------------------------------------------------


class RecordingStore:
    """An object store that records what it was asked to write, and can refuse.

    ``refuse_with`` returns whatever error the test wants for a named key, which is how the
    conditional-write refusal is reached without botocore being importable here -- the same
    duck-typed shape the handler reads.
    """

    def __init__(self, refuse: dict[str, Exception] | None = None) -> None:
        self.written: list[dict[str, Any]] = []
        self._refuse = refuse or {}

    def put_object(self, **arguments: Any) -> Any:
        self.written.append(arguments)
        error = self._refuse.get(arguments["Key"])
        if error is not None:
            raise error
        return {}

    @property
    def keys(self) -> list[str]:
        return [written["Key"] for written in self.written]


def conflict() -> Exception:
    error = RuntimeError("PreconditionFailed")
    error.response = {  # type: ignore[attr-defined]
        "Error": {"Code": "PreconditionFailed"},
        "ResponseMetadata": {"HTTPStatusCode": 412},
    }
    return error


def sqs_batch(*envelopes: dict[str, Any]) -> dict[str, Any]:
    return {
        "Records": [
            {"messageId": f"message-{index}", "body": json.dumps(payload)}
            for index, payload in enumerate(envelopes)
        ]
    }


def test_the_handler_writes_the_four_keys_the_rest_of_phase_three_reads() -> None:
    """Mutation: change any prefix.

    The state machine writes ``binding/{run_id}.json`` and the lifecycle role is scoped to
    exactly these four prefixes, so a rename here detaches this function from a grant that
    still permits the old name -- and the failure is an AccessDenied in a Lambda log rather
    than anything a template review would show.
    """
    store = RecordingStore()
    projected = succeeded()

    handler(sqs_batch(envelope("SUCCEEDED", attempts=[attempt_block()])), store=store)

    assert projected.attempt is not None
    assert store.keys == [
        f"events/{RUN_ID}/evt_{EVENTBRIDGE_EVENT_ID}.json",
        f"attempt/{RUN_ID}/{projected.attempt.attempt_id}.json",
        f"result/{RUN_ID}.json",
    ]
    assert binding_key(RUN_ID) == f"binding/{RUN_ID}.json", (
        "the fourth key is the state machine's to write, and it is spelled here so a "
        "rename cannot pass a review that only read this function"
    )


def test_the_event_is_written_before_the_records_that_depend_on_it() -> None:
    """Mutation: write the result first.

    A result present with no attempt beside it is an outcome attributed to an attempt
    nobody recorded, which is what a partial write would leave if the order were reversed.
    """
    store = RecordingStore()

    handler(sqs_batch(envelope("SUCCEEDED", attempts=[attempt_block()])), store=store)

    assert [key.split("/", 1)[0] for key in store.keys] == ["events", "attempt", "result"]


def test_the_stored_bytes_are_the_canonical_ones_rather_than_a_re_encoding() -> None:
    """Mutation: serialize with ``model_dump_json`` or ``json.dumps`` of a dict.

    What the store holds has to be byte-identical to what any reader would hash, or a
    record cannot be verified without knowing how it was written.
    """
    store = RecordingStore()
    projected = succeeded()

    handler(sqs_batch(envelope("SUCCEEDED", attempts=[attempt_block()])), store=store)

    assert store.written[0]["Body"] == canonical_json_bytes(projected.event)


def test_every_write_is_conditional_so_a_replay_cannot_overwrite_anything() -> None:
    """Mutation: drop ``IfNoneMatch``.

    The bucket policy refuses an unconditional write from every principal, so this would
    fail closed -- but it would fail as an AccessDenied that reads like a role problem, and
    the deduplication this design depends on would be the bucket's alone.
    """
    store = RecordingStore()

    handler(sqs_batch(envelope("SUCCEEDED", attempts=[attempt_block()])), store=store)

    assert all(written["IfNoneMatch"] == "*" for written in store.written)


def test_every_write_asks_the_store_to_attest_a_digest_over_what_it_received() -> None:
    """Mutation: drop ``ChecksumAlgorithm``. This is not hypothetical; it shipped.

    The first run through the whole path wrote three records from this handler, and
    HeadObject returned a VersionId and no ``ChecksumSHA256`` for every one of them, while
    the five records the state machine writes each came back attested. The ASL sets
    ``ChecksumAlgorithm`` on all five and this handler set it on none.

    Nothing anywhere reports it. Omitting the field is not an error, costs nothing at write
    time, and leaves an object that reads exactly like an attested one until a reader asks
    for the digest and finds no field -- by which point the bytes it would have attested
    are the only copy. It fails here rather than there because the deployed bucket cannot
    supply the algorithm on the writer's behalf and no bucket setting exists that would.
    """
    store = RecordingStore()

    handler(sqs_batch(envelope("SUCCEEDED", attempts=[attempt_block()])), store=store)

    assert store.written, "a test over every write must observe at least one"
    assert all(written["ChecksumAlgorithm"] == "SHA256" for written in store.written)


def test_a_redelivered_event_is_refused_by_the_store_and_that_is_success() -> None:
    """Mutation: treat the 412 as a failure.

    "Event duplicates do not create conflicting terminal state" is a property of the store.
    A handler that dead-lettered the refusal would turn the mechanism into an alarm.
    """
    store = RecordingStore(refuse={f"events/{RUN_ID}/evt_{EVENTBRIDGE_EVENT_ID}.json": conflict()})

    answer = handler(sqs_batch(envelope("SUCCEEDED", attempts=[attempt_block()])), store=store)

    assert answer == {"batchItemFailures": []}
    assert len(store.keys) == 3, "the refusal of one key must not abandon the other two"


def test_a_batch_in_which_nothing_survived_fails_the_invocation() -> None:
    """Mutation: return the failure list and nothing else.

    The deployed event source mapping is ``BatchSize: 1`` and does not configure
    ``ReportBatchItemFailures``, so a returned list is an ordinary return: the message is
    deleted and the delivery is lost with no retry and no dead-letter. Raising is what makes
    the retry happen under the mapping that exists.
    """
    store = RecordingStore(
        refuse={f"events/{RUN_ID}/evt_{EVENTBRIDGE_EVENT_ID}.json": RuntimeError("throttled")}
    )

    with pytest.raises(RuntimeError, match="throttled"):
        handler(sqs_batch(envelope("SUCCEEDED", attempts=[attempt_block()])), store=store)


def test_one_unreadable_delivery_does_not_take_a_batch_of_good_ones_with_it() -> None:
    """Mutation: raise whenever anything failed.

    At a batch size above one, that would redeliver the good records indefinitely and
    eventually dead-letter records that were written correctly the first time.
    """
    store = RecordingStore()
    good = envelope("SUCCEEDED", attempts=[attempt_block()])
    bad = {**envelope("RUNNING", attempts=[]), "source": "aws.ecs"}

    answer = handler(sqs_batch(bad, good), store=store)

    assert answer == {"batchItemFailures": [{"itemIdentifier": "message-0"}]}
    assert store.keys[0].startswith(f"events/{RUN_ID}/")


def test_a_record_the_queue_did_not_identify_fails_the_whole_invocation() -> None:
    """A partial failure can only be reported for a message that has an id.

    Mutation: skip it silently, which loses the delivery with no retry and no alarm.
    """
    good = {"messageId": "message-1", "body": json.dumps(envelope("SUCCEEDED", attempts=[]))}
    nameless = {"body": json.dumps({"source": "aws.ecs"})}

    with pytest.raises(UnreadableBatchEventError):
        handler({"Records": [nameless, good]}, store=RecordingStore())


def test_an_event_that_is_not_an_sqs_batch_is_refused() -> None:
    """Mutation: accept a bare EventBridge envelope as well.

    Decision D6 put a queue between the rule and this function. A handler that also read
    the direct shape would keep working if somebody attached it as a Lambda target, which
    is the change D6 exists to make visible.
    """
    with pytest.raises(LifecycleEventError):
        handler(envelope("RUNNING", attempts=[]), store=RecordingStore())
