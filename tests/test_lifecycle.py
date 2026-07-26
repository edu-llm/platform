from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from edullm_platform.canonical import canonical_json_bytes, sha256_digest
from edullm_platform.contracts.base import ContractModel
from edullm_platform.contracts.lifecycle import (
    EVENT_ID_PREFIX,
    RUN_STATE_TRANSITIONS,
    TERMINAL_RUN_STATES,
    AttemptTerminalState,
    ConflictingLifecycleEventError,
    EventSource,
    LifecycleEvent,
    LogicalRun,
    RunState,
    SchedulerAttempt,
    deduplicate_lifecycle_events,
    is_valid_run_transition,
    new_event_id,
    run_state_for_attempt,
)
from edullm_platform.contracts.vocabulary import JobType

STABLE_RUN_ID = "run_01994f2a-1c00-7c3b-8f4d-2a5b6c7d8e9f"
PARENT_RUN_ID = "run_01994f29-0b00-7a1c-8b2d-3e4f5a6b7c8d"
STABLE_ATTEMPT_ID = "att_01994f2a-1c00-7c3b-9a1b-2c3d4e5f6a7b"
SECOND_ATTEMPT_ID = "att_01994f2b-2d00-7e4c-8a3b-4c5d6e7f8a9b"
STABLE_EVENT_ID = "evt_01994f2a-1c00-7c3b-8f4d-2a5b6c7d8e9f"
OTHER_EVENT_ID = "evt_01994f2b-2d00-7e4c-8a3b-4c5d6e7f8a9b"
SCHEDULER_JOB_ID = "3f2a9c1e-0d4b-4a6f-8c2d-7b9e1f0a5c33"

MANIFEST_DIGEST = "sha256:" + "a" * 64
CHECKPOINT_DIGEST = "sha256:" + "b" * 64
CHECKPOINT_URI = "s3://sbsandbox-intern-edullm-checkpoints/runs/parent/step-1000/"

LOGICAL_RUN_DIGEST = "sha256:d195afbf9d90b776bfc39d46683eb45f3f5463420d0464b4fee3b1b5202d680d"
SCHEDULER_ATTEMPT_DIGEST = (
    "sha256:a8727f150891357935b660adafba82b94046dc283e3aa7de9f11df1dc6259245"
)
LIFECYCLE_EVENT_DIGEST = "sha256:f49359c550ae5b516f8197705531bcd3e906de4a142407d1b1aa66c883069e42"


def logical_run_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "run_id": STABLE_RUN_ID,
        "manifest_digest": MANIFEST_DIGEST,
        "submitted_by": "frank-philote",
        "team_id": "modeling",
        "job_type": "model_pretraining",
        "created_at": "2026-07-25T12:00:00Z",
        "parent_run_id": None,
        "resumed_from": None,
    }
    payload.update(overrides)
    return payload


def resumed_run_payload(**overrides: object) -> dict[str, object]:
    payload = logical_run_payload(
        parent_run_id=PARENT_RUN_ID,
        resumed_from={"uri": CHECKPOINT_URI, "checksum": CHECKPOINT_DIGEST},
    )
    payload.update(overrides)
    return payload


def scheduler_attempt_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "attempt_id": STABLE_ATTEMPT_ID,
        "run_id": STABLE_RUN_ID,
        "attempt_ordinal": 1,
        "scheduler_job_id": SCHEDULER_JOB_ID,
        "started_at": "2026-07-25T12:00:00Z",
        "ended_at": "2026-07-25T13:30:00Z",
        "terminal_state": "failed",
    }
    payload.update(overrides)
    return payload


def lifecycle_event_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "event_id": STABLE_EVENT_ID,
        "run_id": STABLE_RUN_ID,
        "attempt_id": STABLE_ATTEMPT_ID,
        "source": "scheduler",
        "state": "running",
        "occurred_at": "2026-07-25T12:05:00Z",
    }
    payload.update(overrides)
    return payload


def reverse_mapping_order(value: object) -> object:
    if isinstance(value, dict):
        items = reversed(list(value.items()))
        return {key: reverse_mapping_order(item) for key, item in items}
    if isinstance(value, list):
        return [reverse_mapping_order(item) for item in value]
    return value


def assert_validation_error(
    error: ValidationError,
    *,
    error_type: str,
    loc: tuple[str | int, ...],
    message_fragment: str | None = None,
) -> None:
    matching_errors = [
        item for item in error.errors() if item["type"] == error_type and item["loc"] == loc
    ]
    assert matching_errors, (
        f"expected error type {error_type!r} at loc {loc!r}, got {error.errors()}"
    )
    if message_fragment is not None:
        assert any(message_fragment in item["msg"] for item in matching_errors), (
            f"expected {message_fragment!r} in {error_type!r} messages at {loc!r}, "
            f"got {[item['msg'] for item in matching_errors]}"
        )


def test_run_state_vocabulary_covers_the_required_states() -> None:
    assert {state.value for state in RunState} >= {
        "submitted",
        "runnable",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    }


def test_terminal_run_states_have_no_outgoing_transitions() -> None:
    assert TERMINAL_RUN_STATES == frozenset(
        {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED}
    )
    for state in TERMINAL_RUN_STATES:
        assert RUN_STATE_TRANSITIONS[state] == frozenset()


def test_every_run_state_has_a_declared_transition_set() -> None:
    assert set(RUN_STATE_TRANSITIONS) == set(RunState)


def test_infrastructure_retry_requeues_the_same_logical_run() -> None:
    assert is_valid_run_transition(RunState.RUNNING, RunState.RUNNABLE) is True
    assert is_valid_run_transition(RunState.RUNNABLE, RunState.RUNNING) is True


def test_a_terminal_run_never_reopens() -> None:
    for terminal in TERMINAL_RUN_STATES:
        for state in RunState:
            assert is_valid_run_transition(terminal, state) is False


def test_attempt_terminal_states_are_exactly_the_terminal_run_states() -> None:
    assert {state.value for state in AttemptTerminalState} == {
        state.value for state in TERMINAL_RUN_STATES
    }
    for terminal in AttemptTerminalState:
        assert run_state_for_attempt(terminal).is_terminal is True


def test_event_sources_are_distinguishable() -> None:
    assert {source.value for source in EventSource} >= {"scheduler", "platform", "operator"}


def test_logical_run_without_a_parent_is_valid() -> None:
    run = LogicalRun.model_validate(logical_run_payload())
    assert run.run_id == STABLE_RUN_ID
    assert run.parent_run_id is None
    assert run.resumed_from is None
    assert run.is_resumed is False
    assert run.job_type is JobType.MODEL_PRETRAINING


def test_resumed_run_references_its_parent_and_checkpoint() -> None:
    run = LogicalRun.model_validate(resumed_run_payload())
    assert run.is_resumed is True
    assert run.parent_run_id == PARENT_RUN_ID
    assert run.run_id != run.parent_run_id
    assert run.resumed_from is not None
    assert run.resumed_from.uri == CHECKPOINT_URI
    assert run.resumed_from.checksum == CHECKPOINT_DIGEST


@pytest.mark.parametrize(
    ("overrides", "message_fragment"),
    [
        ({"parent_run_id": PARENT_RUN_ID}, "must record both its parent run"),
        (
            {"resumed_from": {"uri": CHECKPOINT_URI, "checksum": CHECKPOINT_DIGEST}},
            "must record both its parent run",
        ),
    ],
    ids=["parent-without-checkpoint", "checkpoint-without-parent"],
)
def test_partial_resume_lineage_is_rejected(
    overrides: dict[str, object],
    message_fragment: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        LogicalRun.model_validate(logical_run_payload(**overrides))
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=(),
        message_fragment=message_fragment,
    )


def test_a_logical_run_cannot_be_its_own_parent() -> None:
    with pytest.raises(ValidationError) as exc_info:
        LogicalRun.model_validate(resumed_run_payload(parent_run_id=STABLE_RUN_ID))
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=(),
        message_fragment="must not be its own parent",
    )


def test_logical_run_rejects_an_attempt_id_as_its_parent() -> None:
    with pytest.raises(ValidationError) as exc_info:
        LogicalRun.model_validate(resumed_run_payload(parent_run_id=STABLE_ATTEMPT_ID))
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("parent_run_id",),
    )


def test_logical_run_rejects_a_resume_checkpoint_outside_the_sandbox_namespace() -> None:
    with pytest.raises(ValidationError) as exc_info:
        LogicalRun.model_validate(
            resumed_run_payload(
                resumed_from={
                    "uri": "s3://edullm-checkpoints/runs/parent/step-1000/",
                    "checksum": CHECKPOINT_DIGEST,
                }
            )
        )
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("resumed_from", "uri"),
    )


@pytest.mark.parametrize(
    "manifest_digest",
    ["", "a" * 64, "sha256:" + "A" * 64, "md5:" + "a" * 32, "sha256:" + "a" * 63],
    ids=["empty", "unprefixed", "uppercase", "md5", "truncated"],
)
def test_logical_run_requires_a_sha256_manifest_digest(manifest_digest: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        LogicalRun.model_validate(logical_run_payload(manifest_digest=manifest_digest))
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("manifest_digest",),
    )


def test_logical_run_rejects_an_unknown_job_type() -> None:
    with pytest.raises(ValidationError) as exc_info:
        LogicalRun.model_validate(logical_run_payload(job_type="quantum_annealing"))
    assert_validation_error(exc_info.value, error_type="value_error", loc=("job_type",))


def test_scheduler_attempt_validates_a_complete_payload() -> None:
    attempt = SchedulerAttempt.model_validate(scheduler_attempt_payload())
    assert attempt.run_id == STABLE_RUN_ID
    assert attempt.attempt_id == STABLE_ATTEMPT_ID
    assert attempt.attempt_ordinal == 1
    assert attempt.terminal_state is AttemptTerminalState.FAILED


def test_scheduler_attempt_rejects_an_attempt_id_where_a_run_id_belongs() -> None:
    with pytest.raises(ValidationError) as exc_info:
        SchedulerAttempt.model_validate(scheduler_attempt_payload(run_id=STABLE_ATTEMPT_ID))
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("run_id",),
    )


def test_scheduler_attempt_rejects_a_run_id_where_an_attempt_id_belongs() -> None:
    with pytest.raises(ValidationError) as exc_info:
        SchedulerAttempt.model_validate(scheduler_attempt_payload(attempt_id=STABLE_RUN_ID))
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("attempt_id",),
    )


def test_scheduler_attempt_rejects_a_swapped_identifier_pair_on_both_fields() -> None:
    with pytest.raises(ValidationError) as exc_info:
        SchedulerAttempt.model_validate(
            scheduler_attempt_payload(run_id=STABLE_ATTEMPT_ID, attempt_id=STABLE_RUN_ID)
        )
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("run_id",),
    )
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("attempt_id",),
    )


@pytest.mark.parametrize(
    "run_id",
    ["", "modeling-run-7", SCHEDULER_JOB_ID, "run_not-a-uuid"],
    ids=["empty", "slug", "scheduler-job-id", "malformed-uuid"],
)
def test_scheduler_attempt_rejects_a_bare_string_run_reference(run_id: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        SchedulerAttempt.model_validate(scheduler_attempt_payload(run_id=run_id))
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("run_id",),
    )


@pytest.mark.parametrize(
    "scheduler_job_id",
    ["", " ", "-leading-dash", "job id with spaces"],
    ids=["empty", "whitespace", "leading-dash", "spaces"],
)
def test_scheduler_attempt_requires_a_usable_scheduler_job_id(scheduler_job_id: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        SchedulerAttempt.model_validate(
            scheduler_attempt_payload(scheduler_job_id=scheduler_job_id)
        )
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("scheduler_job_id",),
    )


def test_scheduler_attempt_accepts_a_batch_job_arn() -> None:
    arn = "arn:aws:batch:us-east-2:job/edullm-queue/" + SCHEDULER_JOB_ID
    attempt = SchedulerAttempt.model_validate(scheduler_attempt_payload(scheduler_job_id=arn))
    assert attempt.scheduler_job_id == arn
    assert attempt.run_state is RunState.FAILED


@pytest.mark.parametrize("attempt_ordinal", [0, -1, -1000])
def test_scheduler_attempt_rejects_an_ordinal_below_one(attempt_ordinal: int) -> None:
    with pytest.raises(ValidationError) as exc_info:
        SchedulerAttempt.model_validate(
            scheduler_attempt_payload(attempt_ordinal=attempt_ordinal)
        )
    assert_validation_error(
        exc_info.value,
        error_type="greater_than_equal",
        loc=("attempt_ordinal",),
    )


def test_scheduler_attempt_accepts_a_retry_ordinal_above_one() -> None:
    attempt = SchedulerAttempt.model_validate(
        scheduler_attempt_payload(attempt_id=SECOND_ATTEMPT_ID, attempt_ordinal=2)
    )
    assert attempt.attempt_ordinal == 2
    assert attempt.run_id == STABLE_RUN_ID


def test_scheduler_attempt_rejects_an_end_before_its_start() -> None:
    with pytest.raises(ValidationError) as exc_info:
        SchedulerAttempt.model_validate(
            scheduler_attempt_payload(ended_at="2026-07-25T11:59:59Z")
        )
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=(),
        message_fragment="must not end before it starts",
    )


def test_scheduler_attempt_rejects_a_non_terminal_state() -> None:
    with pytest.raises(ValidationError) as exc_info:
        SchedulerAttempt.model_validate(scheduler_attempt_payload(terminal_state="running"))
    assert_validation_error(exc_info.value, error_type="value_error", loc=("terminal_state",))


def test_two_attempts_of_one_logical_run_share_the_run_id() -> None:
    first = SchedulerAttempt.model_validate(scheduler_attempt_payload())
    second = SchedulerAttempt.model_validate(
        scheduler_attempt_payload(
            attempt_id=SECOND_ATTEMPT_ID,
            attempt_ordinal=2,
            terminal_state="succeeded",
        )
    )
    assert first.run_id == second.run_id
    assert first.attempt_id != second.attempt_id
    assert sha256_digest(first) != sha256_digest(second)


def test_lifecycle_event_validates_a_complete_payload() -> None:
    event = LifecycleEvent.model_validate(lifecycle_event_payload())
    assert event.event_id == STABLE_EVENT_ID
    assert event.source is EventSource.SCHEDULER
    assert event.state is RunState.RUNNING
    assert event.attempt_id == STABLE_ATTEMPT_ID


def test_lifecycle_event_may_describe_a_run_without_an_attempt() -> None:
    event = LifecycleEvent.model_validate(
        lifecycle_event_payload(attempt_id=None, source="platform", state="submitted")
    )
    assert event.attempt_id is None
    assert event.source is EventSource.PLATFORM


@pytest.mark.parametrize(
    "event_id",
    [STABLE_RUN_ID, STABLE_ATTEMPT_ID, "01994f2a-1c00-7c3b-8f4d-2a5b6c7d8e9f", ""],
    ids=["run-id", "attempt-id", "unprefixed-uuid", "empty"],
)
def test_lifecycle_event_rejects_identifiers_of_another_kind(event_id: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        LifecycleEvent.model_validate(lifecycle_event_payload(event_id=event_id))
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("event_id",),
    )


def test_new_event_id_is_prefixed_and_unique() -> None:
    event_ids = [new_event_id() for _ in range(100)]
    assert all(event_id.startswith(EVENT_ID_PREFIX) for event_id in event_ids)
    assert len(set(event_ids)) == len(event_ids)
    event = LifecycleEvent.model_validate(lifecycle_event_payload(event_id=event_ids[0]))
    assert event.event_id == event_ids[0]


def test_redelivered_event_is_identifiable_as_a_duplicate() -> None:
    first = LifecycleEvent.model_validate(lifecycle_event_payload())
    redelivered = LifecycleEvent.model_validate(lifecycle_event_payload())
    assert first.deduplication_key == redelivered.deduplication_key
    assert first.is_duplicate_of(redelivered) is True
    assert sha256_digest(first) == sha256_digest(redelivered)


def test_distinct_events_are_not_duplicates() -> None:
    first = LifecycleEvent.model_validate(lifecycle_event_payload())
    second = LifecycleEvent.model_validate(
        lifecycle_event_payload(event_id=OTHER_EVENT_ID, state="succeeded")
    )
    assert first.deduplication_key != second.deduplication_key
    assert first.is_duplicate_of(second) is False


def test_at_least_once_delivery_collapses_to_one_record_per_event_id() -> None:
    first = LifecycleEvent.model_validate(lifecycle_event_payload())
    second = LifecycleEvent.model_validate(
        lifecycle_event_payload(event_id=OTHER_EVENT_ID, state="succeeded")
    )
    stream = (first, second, first, second, first)
    assert deduplicate_lifecycle_events(stream) == (first, second)
    assert deduplicate_lifecycle_events(()) == ()


def test_reused_event_id_with_different_content_is_rejected_not_silently_dropped() -> None:
    first = LifecycleEvent.model_validate(lifecycle_event_payload())
    conflicting = LifecycleEvent.model_validate(lifecycle_event_payload(state="succeeded"))
    assert first.is_duplicate_of(conflicting) is True
    with pytest.raises(ConflictingLifecycleEventError) as exc_info:
        deduplicate_lifecycle_events((first, conflicting))
    assert exc_info.value.event_id == STABLE_EVENT_ID
    assert isinstance(exc_info.value, ValueError)


@pytest.mark.parametrize(
    ("model_type", "payload_factory"),
    [
        (LogicalRun, logical_run_payload),
        (SchedulerAttempt, scheduler_attempt_payload),
        (LifecycleEvent, lifecycle_event_payload),
    ],
    ids=["logical-run", "scheduler-attempt", "lifecycle-event"],
)
def test_unknown_schema_version_fails_closed(
    model_type: type[ContractModel],
    payload_factory: Callable[..., dict[str, object]],
) -> None:
    for schema_version in (0, 2, "1", None):
        with pytest.raises(ValidationError) as exc_info:
            model_type.model_validate(payload_factory(schema_version=schema_version))
        assert_validation_error(
            exc_info.value,
            error_type="literal_error",
            loc=("schema_version",),
        )


@pytest.mark.parametrize(
    ("model_type", "payload_factory"),
    [
        (LogicalRun, resumed_run_payload),
        (SchedulerAttempt, scheduler_attempt_payload),
        (LifecycleEvent, lifecycle_event_payload),
    ],
    ids=["logical-run", "scheduler-attempt", "lifecycle-event"],
)
def test_reordering_input_fields_does_not_change_the_digest(
    model_type: type[ContractModel],
    payload_factory: Callable[..., dict[str, object]],
) -> None:
    payload = payload_factory()
    reordered = reverse_mapping_order(payload)
    assert list(reordered) != list(payload)
    baseline = model_type.model_validate(payload)
    shuffled = model_type.model_validate(reordered)
    assert canonical_json_bytes(baseline) == canonical_json_bytes(shuffled)
    assert sha256_digest(baseline) == sha256_digest(shuffled)


@pytest.mark.parametrize(
    ("model_type", "payload_factory", "expected_digest"),
    [
        (LogicalRun, logical_run_payload, LOGICAL_RUN_DIGEST),
        (SchedulerAttempt, scheduler_attempt_payload, SCHEDULER_ATTEMPT_DIGEST),
        (LifecycleEvent, lifecycle_event_payload, LIFECYCLE_EVENT_DIGEST),
    ],
    ids=["logical-run", "scheduler-attempt", "lifecycle-event"],
)
def test_digest_is_stable_across_repeated_validation(
    model_type: type[ContractModel],
    payload_factory: Callable[..., dict[str, object]],
    expected_digest: str,
) -> None:
    first = model_type.model_validate(payload_factory())
    second = model_type.model_validate(payload_factory())
    assert sha256_digest(first) == sha256_digest(second)
    assert sha256_digest(first) == expected_digest


@pytest.mark.parametrize(
    "created_at",
    [
        "2026-07-25T12:00:00Z",
        "2026-07-25T12:00:00+00:00",
        "2026-07-25T07:00:00-05:00",
        "2026-07-25T21:00:00+09:00",
        datetime(2026, 7, 25, 12, 0, tzinfo=UTC),
    ],
    ids=["zulu", "utc-offset", "chicago", "tokyo", "python-datetime"],
)
def test_equal_instants_hash_identically_regardless_of_offset(created_at: object) -> None:
    baseline = LogicalRun.model_validate(logical_run_payload())
    run = LogicalRun.model_validate(logical_run_payload(created_at=created_at))
    assert run.created_at.tzinfo == UTC
    assert sha256_digest(run) == sha256_digest(baseline)


def test_timestamps_serialize_as_utc_with_fixed_precision() -> None:
    run = LogicalRun.model_validate(logical_run_payload(created_at="2026-07-25T07:00:00-05:00"))
    assert b'"created_at":"2026-07-25T12:00:00.000000Z"' in canonical_json_bytes(run)


def test_sub_second_precision_survives_the_round_trip() -> None:
    run = LogicalRun.model_validate(logical_run_payload(created_at="2026-07-25T12:00:00.123456Z"))
    assert b'"created_at":"2026-07-25T12:00:00.123456Z"' in canonical_json_bytes(run)
    assert sha256_digest(run) != LOGICAL_RUN_DIGEST


@pytest.mark.parametrize(
    ("created_at", "error_type"),
    [
        ("2026-07-25T12:00:00", "value_error"),
        ("2026-07-25", "value_error"),
        ("not-a-timestamp", "value_error"),
        (datetime(2026, 7, 25, 12, 0, tzinfo=UTC).replace(tzinfo=None), "value_error"),
        (1785000000, "datetime_type"),
        (None, "datetime_type"),
    ],
    ids=["naive-string", "date-only", "garbage", "naive-datetime", "epoch-int", "none"],
)
def test_ambiguous_timestamps_are_rejected(created_at: object, error_type: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        LogicalRun.model_validate(logical_run_payload(created_at=created_at))
    assert_validation_error(exc_info.value, error_type=error_type, loc=("created_at",))


def test_lifecycle_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError) as exc_info:
        LogicalRun.model_validate(logical_run_payload(priority="high"))
    assert_validation_error(exc_info.value, error_type="extra_forbidden", loc=("priority",))


def test_lifecycle_models_are_frozen() -> None:
    run = LogicalRun.model_validate(logical_run_payload())
    with pytest.raises(ValidationError) as exc_info:
        run.run_id = PARENT_RUN_ID
    assert_validation_error(exc_info.value, error_type="frozen_instance", loc=("run_id",))
