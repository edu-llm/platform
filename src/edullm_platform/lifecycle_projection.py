"""Read one Batch job state change as the Phase 0 records it implies.

Pure. No SDK, no I/O, no clock. Every value here is a function of the EventBridge envelope
that was delivered, which is what makes a replayed delivery project to the same bytes and
what lets the whole projection be checked without an AWS account.

**Batch reports seven statuses onto a vocabulary of six, so the mapping loses two.**
:class:`~.contracts.lifecycle.RunState` has ``submitted``, ``runnable``, ``running``,
``succeeded``, ``failed`` and ``cancelled``. Batch reports ``SUBMITTED``, ``PENDING``,
``RUNNABLE``, ``STARTING``, ``RUNNING``, ``SUCCEEDED`` and ``FAILED``. ``PENDING`` and
``STARTING`` have nowhere of their own to go, and the collapse is written down here rather
than left to be inferred, because a status silently dropped makes the event stream look
gapped rather than coarse.

``PENDING`` collapses to ``submitted``. Batch uses it for a job whose dependencies have not
finished, which is a job that has been accepted and is not yet eligible for placement --
exactly what ``submitted`` means and exactly what ``runnable`` does not.

``STARTING`` collapses to ``runnable``. Batch has chosen a host and is pulling the image;
none of the workload's own code has run. Collapsing it upward to ``running`` would be the
comfortable choice and it overstates: a job that dies during the image pull would then be
recorded as a workload that ran and failed, which is the wrong thing to go looking at. The
direction that understates is the safe one here, because the question this stream is read
to answer is whether anything executed.

The collapse is a table rather than a chain of ``if`` statements so that a status Batch
adds later is detectably unmapped -- :class:`UnmappedBatchStatusError` -- instead of falling
through to a default and being recorded as something it is not.

**Batch has no cancelled status, and this module does not guess at one.** A terminated job
reports ``FAILED`` with a ``statusReason`` carrying whatever the caller of ``TerminateJob``
supplied. Cancellation is therefore detected by a marker this platform writes --
:data:`CANCELLATION_REASON_MARKERS` -- and by nothing else. What that misses is real and
is the point: a job stopped from the console, by another principal, or by AWS itself
carries a reason we did not write and is recorded as ``failed``. That understates a human
decision and never invents one, which is the direction to be wrong in for a record that
cannot be edited afterwards. The alternative -- searching arbitrary ``statusReason`` text
for the word "cancelled" -- would let a workload whose own error message mentions
cancellation be recorded as a run somebody stopped. The markers are matched as prefixes for
the same reason: a reason that *begins* with one was written by the cancellation path,
where one that merely contains it may have been written by anything.

**The event id is derived from EventBridge's, never minted.** ``EVENT_ID_PATTERN`` is
``evt_<uuid>`` and EventBridge event ids are UUIDs, so ``"evt_" + id`` is legal and stable
across redeliveries. That is what makes a replayed event compute the same S3 key and meet
the store's ``IfNoneMatch: "*"`` -- deduplication becomes a property of the store rather
than of this module's care. Minting the id with ``new_event_id()`` is the natural thing to
write, it passes every single-delivery test, and it silently breaks that.

**The attempt id is derived too, and has to be composed rather than hashed.**
``AttemptId`` is patterned ``att_<uuid7>``, so an arbitrary digest will not do: the version
and variant nibbles are fixed by the pattern. It is built from the attempt's own start
instant in the UUIDv7 timestamp field -- which keeps ids time-ordered, as that format
intends -- and from a digest of the run id, the Batch job id and the attempt ordinal in the
rest. Nothing in it comes from the delivery, so the ``RUNNING`` event and the ``SUCCEEDED``
event for one attempt name the same attempt, which is what the lineage join depends on.

**Ordering cannot be enforced from one event, and this module does not pretend to.**
:func:`~.contracts.lifecycle.is_valid_run_transition` compares two states, and a single
Batch state change carries only the one it arrived at -- Batch does not report what the job
was before. Enforcing ordering would need the store to be read first, which would make the
projection impure and would make a redelivery arriving out of order into a failure rather
than a duplicate. What *is* checkable without any of that is the collapse itself: every
consecutive pair in :data:`BATCH_STATUS_PROGRESSION` must project to a pair the transition
table permits, and :func:`transition_is_recordable` is what a test uses to say so.

**A terminal event yields an attempt and a result; a non-terminal one yields neither.**
``SchedulerAttempt`` requires ``started_at``, ``ended_at`` and ``terminal_state``, so it
cannot honestly be written before a job stops, and a ``ResultManifest`` written on
``RUNNING`` records an outcome for a job that is still going. The one terminal case that
yields neither is a job stopped before any attempt began -- cancelled out of the queue --
which has no attempt window to describe and therefore no outcome to attribute to an
attempt. Its lifecycle event is still written, and that is the whole of what happened.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from .contracts.identity import (
    ATTEMPT_ID_PREFIX,
    COUNTER_BITS,
    COUNTER_SHIFT,
    MAXIMUM_UNIX_TS_MS,
    RUN_ID_REGEX,
    TAIL_BITS,
    UNIX_TS_MS_SHIFT,
    UUID7_VARIANT,
    UUID7_VERSION,
    VARIANT_SHIFT,
    VERSION_SHIFT,
)
from .contracts.lifecycle import (
    EVENT_ID_PREFIX,
    AttemptTerminalState,
    EventSource,
    LifecycleEvent,
    RunState,
    SchedulerAttempt,
    is_valid_run_transition,
)
from .contracts.results import ResultManifest
from .contracts.vocabulary import RetentionClass

__all__ = [
    "BATCH_JOB_STATUSES",
    "BATCH_STATUS_PROGRESSION",
    "BATCH_STATUS_TO_RUN_STATE",
    "CANCELLATION_REASON_MARKERS",
    "EVENTBRIDGE_BATCH_DETAIL_TYPE",
    "EVENTBRIDGE_BATCH_SOURCE",
    "OUTPUTS_BUCKET",
    "LifecycleProjection",
    "UnmappedBatchStatusError",
    "UnreadableBatchEventError",
    "derived_event_id",
    "project_batch_event",
    "project_batch_state_change",
    "transition_is_recordable",
]

#: What the EventBridge rule is scoped to. Checked again here rather than trusted, because
#: the rule is one deploy away from being widened and a foreign job's name parsed as a run
#: id would put another team's identifier into this project's lineage store.
EVENTBRIDGE_BATCH_SOURCE: Final = "aws.batch"
EVENTBRIDGE_BATCH_DETAIL_TYPE: Final = "Batch Job State Change"

#: Where a run's own output goes. Not the lineage bucket: that store's entire property is
#: that only the platform writes to it, and a workload role holding ``s3:PutObject`` there
#: would end that. Overridable so the recorder can be pointed at a deployment's own bucket
#: without this module acquiring a way to read one.
OUTPUTS_BUCKET: Final = "sbsandbox-intern-edullm-outputs"

#: Every status Batch reports for a job, and nothing else. A status outside this set is an
#: error rather than a default, which is what makes an eighth one detectable.
BATCH_JOB_STATUSES: Final = (
    "SUBMITTED",
    "PENDING",
    "RUNNABLE",
    "STARTING",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
)

#: The collapse, as data. See the module docstring for why PENDING lands on ``submitted``
#: and STARTING on ``runnable`` rather than the other way round.
BATCH_STATUS_TO_RUN_STATE: Final[Mapping[str, RunState]] = {
    "SUBMITTED": RunState.SUBMITTED,
    "PENDING": RunState.SUBMITTED,
    "RUNNABLE": RunState.RUNNABLE,
    "STARTING": RunState.RUNNABLE,
    "RUNNING": RunState.RUNNING,
    "SUCCEEDED": RunState.SUCCEEDED,
    "FAILED": RunState.FAILED,
}

#: The order Batch moves a job through, as consecutive pairs. One linear progression with a
#: terminal branch, which is the whole of what Batch's own state machine offers. This is
#: what makes the collapse checkable against the transition table without a store to read.
BATCH_STATUS_PROGRESSION: Final = (
    ("SUBMITTED", "PENDING"),
    ("PENDING", "RUNNABLE"),
    ("RUNNABLE", "STARTING"),
    ("STARTING", "RUNNING"),
    ("RUNNING", "SUCCEEDED"),
    ("RUNNING", "FAILED"),
)

#: How this platform's cancellation path words the reason it hands ``TerminateJob``. A
#: refusal to guess: nothing else in a ``statusReason`` is read as cancellation, and the
#: cost of that is written down in the module docstring.
CANCELLATION_REASON_MARKERS: Final = ("edullm:cancelled",)

#: Batch reports instants as epoch milliseconds. Composed with a timedelta rather than
#: divided into a float, because 1e-3 is not representable and a record whose timestamp is
#: a millisecond off the one Batch reported is a record that does not join cleanly.
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


class UnmappedBatchStatusError(ValueError):
    """Batch reported a status this projection has no ``RunState`` for.

    Raised rather than defaulted. A default would record the new status as whichever state
    the default names, and the first anybody would learn of it is a lineage store holding
    runs that were never in the state their events say they were.
    """


class UnreadableBatchEventError(ValueError):
    """The delivery is not a Batch job state change for a run this platform started.

    Covers a foreign event source, a detail type the rule should never have matched, and a
    job name that is not a run id. The last is the one that matters in a shared account:
    the rule is scoped to our queue, and if that scoping is ever lost, a foreign job's name
    would otherwise be written into this project's lineage store as a run id.
    """


@dataclass(frozen=True)
class LifecycleProjection:
    """What one delivery says, in the contracts the lineage store holds.

    ``attempt`` and ``result`` are ``None`` for every non-terminal state, and for the one
    terminal state with no attempt to describe. ``event`` is never ``None``: a delivery
    that could be read at all says at least that the run reached a state.
    """

    event: LifecycleEvent
    attempt: SchedulerAttempt | None
    result: ResultManifest | None


def derived_event_id(eventbridge_event_id: str) -> str:
    """``evt_`` plus the id EventBridge delivered, which is a UUID and so already legal.

    Deliberately not a function of the event's content. Two deliveries of one event carry
    one id, and that is the whole mechanism: the same id computes the same S3 key, and the
    conditional write refuses the second.
    """
    return f"{EVENT_ID_PREFIX}{eventbridge_event_id.strip()}"


def transition_is_recordable(before: RunState, after: RunState) -> bool:
    """Whether one projected state may follow another in a record.

    A repeat is not a transition. Two Batch statuses that collapse to one ``RunState``
    produce two events carrying the same state, and the transition table has nothing to say
    about that -- it enumerates the states a run may *move* to. Treating a repeat as illegal
    would make the collapse this module chose look like a bug in it.
    """
    return before is after or is_valid_run_transition(before, after)


def _run_state_for(status: str, *, cancelled: bool) -> RunState:
    state = BATCH_STATUS_TO_RUN_STATE.get(status)
    if state is None:
        raise UnmappedBatchStatusError(
            f"Batch reported job status {status!r}, which no RunState is mapped to; the "
            "collapse in BATCH_STATUS_TO_RUN_STATE has to say where it goes"
        )
    if cancelled and state is RunState.FAILED:
        return RunState.CANCELLED
    return state


def _is_cancellation(detail: Mapping[str, Any], attempt: Mapping[str, Any] | None) -> bool:
    """Whether this platform's cancellation path is what stopped the job.

    Both the job's reason and the terminating attempt's are read, because ``TerminateJob``
    sets the job-level one and Batch copies its own account of the stop onto the attempt.
    Either carrying the marker is enough; neither carrying it is recorded as a failure.
    """
    reasons = [detail.get("statusReason")]
    if attempt is not None:
        reasons.append(attempt.get("statusReason"))
    return any(
        isinstance(reason, str) and reason.lstrip().startswith(marker)
        for reason in reasons
        for marker in CANCELLATION_REASON_MARKERS
    )


def _instant(value: object) -> datetime | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return _EPOCH + timedelta(milliseconds=value)


def _last_attempt(detail: Mapping[str, Any]) -> tuple[int, Mapping[str, Any]] | None:
    """The attempt this event is about, and its one-based ordinal.

    The last, because Batch appends: an event delivered after a retry describes the attempt
    that just ended, and the earlier ones already have records of their own.
    """
    attempts = detail.get("attempts")
    if not isinstance(attempts, list):
        return None
    usable = [
        (ordinal, attempt)
        for ordinal, attempt in enumerate(attempts, start=1)
        if isinstance(attempt, Mapping) and _instant(attempt.get("startedAt")) is not None
    ]
    if not usable:
        return None
    return usable[-1]


def _derive_attempt_id(
    *,
    run_id: str,
    scheduler_job_id: str,
    attempt_ordinal: int,
    started_at_ms: int,
) -> str:
    """A UUIDv7-shaped attempt id that is a function of the attempt and nothing else.

    The timestamp field carries the attempt's own start, so ids sort the way that format
    promises. The counter and tail come from a digest of the three things that identify the
    attempt, so two deliveries about one attempt name it identically and two attempts of one
    job never collide.
    """
    seed = hashlib.sha256(
        "\n".join((run_id, scheduler_job_id, str(attempt_ordinal))).encode("utf-8")
    ).digest()
    entropy = int.from_bytes(seed, "big")
    value = uuid.UUID(
        int=(
            (min(max(started_at_ms, 0), MAXIMUM_UNIX_TS_MS) << UNIX_TS_MS_SHIFT)
            | (UUID7_VERSION << VERSION_SHIFT)
            | (((entropy >> TAIL_BITS) & ((1 << COUNTER_BITS) - 1)) << COUNTER_SHIFT)
            | (UUID7_VARIANT << VARIANT_SHIFT)
            | (entropy & ((1 << TAIL_BITS) - 1))
        )
    )
    return f"{ATTEMPT_ID_PREFIX}{value}"


def _terminal_state(state: RunState) -> AttemptTerminalState | None:
    if not state.is_terminal:
        return None
    return AttemptTerminalState(state.value)


def _required_text(detail: Mapping[str, Any], field: str) -> str:
    value = detail.get(field)
    if not isinstance(value, str) or not value:
        raise UnreadableBatchEventError(
            f"a Batch job state change must carry {field!r} as a non-empty string"
        )
    return value


def project_batch_state_change(
    *,
    eventbridge_event_id: str,
    detail: Mapping[str, Any],
    occurred_at: datetime,
    output_bucket: str = OUTPUTS_BUCKET,
    retention_class: RetentionClass = RetentionClass.STANDARD,
) -> LifecycleProjection:
    """Project one ``Batch Job State Change`` detail onto the Phase 0 contracts.

    ``occurred_at`` is the envelope's ``time`` rather than anything in the detail. Batch's
    own instants say when the job was created, started and stopped, and no one of them is
    the instant of *this* state change -- there is no ``pendingAt``. The envelope's time is
    when EventBridge saw the change, which is the closest fact anybody has to it, and using
    it uniformly means the events of one run are ordered by one clock.
    """
    run_id = _required_text(detail, "jobName")
    if RUN_ID_REGEX.fullmatch(run_id) is None:
        raise UnreadableBatchEventError(
            "the Batch job name is not a run id, so this event describes a job this "
            "platform did not submit"
        )
    scheduler_job_id = _required_text(detail, "jobId")
    status = _required_text(detail, "status")

    last = _last_attempt(detail)
    state = _run_state_for(
        status, cancelled=_is_cancellation(detail, None if last is None else last[1])
    )

    attempt: SchedulerAttempt | None = None
    result: ResultManifest | None = None
    attempt_id: str | None = None
    terminal_state = _terminal_state(state)

    if last is not None:
        ordinal, attempt_detail = last
        started_at_ms = int(attempt_detail["startedAt"])
        attempt_id = _derive_attempt_id(
            run_id=run_id,
            scheduler_job_id=scheduler_job_id,
            attempt_ordinal=ordinal,
            started_at_ms=started_at_ms,
        )
        if terminal_state is not None:
            # Batch records the stop on the attempt; the job-level instant is read only
            # when it does not, which happens for a job stopped between the attempt being
            # recorded and the container reporting. Neither present means there is no
            # window to describe, and inventing one would put a duration in an immutable
            # record that nothing measured.
            ended_at = _instant(attempt_detail.get("stoppedAt")) or _instant(
                detail.get("stoppedAt")
            )
            if ended_at is not None:
                attempt = SchedulerAttempt(
                    schema_version=1,
                    attempt_id=attempt_id,
                    run_id=run_id,
                    attempt_ordinal=ordinal,
                    scheduler_job_id=scheduler_job_id,
                    started_at=_EPOCH + timedelta(milliseconds=started_at_ms),
                    ended_at=ended_at,
                    terminal_state=terminal_state,
                )
                result = ResultManifest(
                    schema_version=1,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    outcome=terminal_state,
                    # The prefix a run writes under, recorded for every outcome rather
                    # than only for a success. It names where anything this run produced
                    # would be, which is as true of a job that failed halfway as of one
                    # that finished, and a reader chasing partial output of a failed run
                    # would otherwise have nothing to follow.
                    output_prefixes=(f"s3://{output_bucket}/{run_id}/",),
                    checkpoints=(),
                    # Phase 3 runs one CPU container with no W&B and no checkpoint. Both
                    # are left null rather than filled with an empty shape, so a later
                    # phase adding them is visible as a change in the record.
                    wandb_run=None,
                    retention_class=retention_class,
                    completed_at=ended_at,
                )

    event = LifecycleEvent(
        schema_version=1,
        event_id=derived_event_id(eventbridge_event_id),
        run_id=run_id,
        attempt_id=attempt_id,
        source=EventSource.SCHEDULER,
        state=state,
        occurred_at=occurred_at,
    )
    return LifecycleProjection(event=event, attempt=attempt, result=result)


def project_batch_event(
    envelope: Mapping[str, Any],
    *,
    output_bucket: str = OUTPUTS_BUCKET,
    retention_class: RetentionClass = RetentionClass.STANDARD,
) -> LifecycleProjection:
    """Project a whole EventBridge envelope, checking it is one this rule should deliver.

    The source and detail type are checked here as well as in the rule pattern. Two places
    rather than one because the rule is a deployed artifact and this is a committed one:
    the recorder is reachable by anything with permission to write to its queue, and a
    projection that trusted the envelope would parse whatever arrived.
    """
    if envelope.get("source") != EVENTBRIDGE_BATCH_SOURCE:
        raise UnreadableBatchEventError(
            f"a lifecycle delivery must come from {EVENTBRIDGE_BATCH_SOURCE!r}"
        )
    if envelope.get("detail-type") != EVENTBRIDGE_BATCH_DETAIL_TYPE:
        raise UnreadableBatchEventError(
            f"a lifecycle delivery must be a {EVENTBRIDGE_BATCH_DETAIL_TYPE!r}"
        )
    event_id = envelope.get("id")
    if not isinstance(event_id, str) or not event_id:
        raise UnreadableBatchEventError(
            "the delivery carries no EventBridge event id, and the lifecycle event id is "
            "derived from it rather than minted"
        )
    occurred_at = envelope.get("time")
    if not isinstance(occurred_at, str):
        raise UnreadableBatchEventError("the delivery carries no EventBridge time")
    detail = envelope.get("detail")
    if not isinstance(detail, Mapping):
        raise UnreadableBatchEventError("the delivery carries no Batch job detail")
    try:
        parsed = datetime.fromisoformat(occurred_at)
    except ValueError as exc:
        raise UnreadableBatchEventError(
            "the delivery's time is not an RFC 3339 date-time"
        ) from exc
    if parsed.tzinfo is None:
        # Refused here rather than assumed to be UTC. A naive instant read as UTC is the
        # kind of wrong that is invisible until somebody compares two records written in
        # different ways, by which time the store cannot be corrected.
        raise UnreadableBatchEventError("the delivery's time carries no UTC offset")
    return project_batch_state_change(
        eventbridge_event_id=event_id,
        detail=detail,
        occurred_at=parsed,
        output_bucket=output_bucket,
        retention_class=retention_class,
    )
