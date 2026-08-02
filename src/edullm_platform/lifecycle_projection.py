"""Read one Batch job state change as the Phase 0 records it implies.

Pure by default. No SDK, no clock, and no I/O unless a caller hands in something to read
the run's own output prefix with. Every other value here is a function of the EventBridge
envelope that was delivered, which is what makes a replayed delivery project to the same
bytes and what lets the whole projection be checked without an AWS account.

**The one read, and why it is not the read this module refused.** ``checkpoints`` used to
be written empty on every result, so a run that trained for hours and saved a checkpoint
and a run that saved nothing were the same record. What a checkpoint list needs is a
listing of the run's own checkpoint prefix, which is why
:func:`project_batch_state_change` takes an optional :class:`CheckpointLister` and records
nothing at all when it is not given one.

That is a different thing from the ``batch:DescribeJobs`` this module and
``infra/iam/lifecycle-lambda-role.yaml`` both argue against, and the difference is what
makes it safe. A describe answers with the job as it is when asked, so a redelivered event
would project from inputs that had moved. A finished run's output prefix does not move --
the terminal event arrives after the container has exited, and nothing writes there
afterwards -- so a replay lists the same objects and computes the same bytes. Where they
do differ, the derived key means the store already holds the first projection and refuses
the second, which is the mechanism rather than a hole in it.

**A read the role does not hold records an empty list rather than raising.** The recorder
runs as ``sbsandbox-intern-edullm-lifecycle-lambda``, which today holds four
``s3:PutObject`` grants and no read of any kind, so the listing is refused. An exception
here would dead-letter the whole delivery and lose the event, the attempt and the result
for a run that demonstrably happened, which is a far worse record than one whose checkpoint
list is empty. So every failure of the listing -- a refusal, an outage, a prefix that is not
in this platform's own bucket -- is an empty tuple, and that is the same value the field
carried before.

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
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol
from urllib.parse import urlparse

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
from .contracts.results import (
    UNPARSED_DIRECTORY_SAMPLE,
    WANDB_NAME_PATTERN,
    CheckpointListingOutcome,
    CheckpointManifest,
    CheckpointSurvey,
    ResultManifest,
    WandbRunRef,
)
from .contracts.vocabulary import RetentionClass

__all__ = [
    "BATCH_JOB_STATUSES",
    "BATCH_STATUS_PROGRESSION",
    "BATCH_STATUS_TO_RUN_STATE",
    "CANCELLATION_REASON_MARKERS",
    "CHECKPOINT_DIRECTORIES",
    "CHECKPOINT_DIR_VARIABLE",
    "EVENTBRIDGE_BATCH_DETAIL_TYPE",
    "EVENTBRIDGE_BATCH_SOURCE",
    "HUGGINGFACE_CHECKPOINT_DIRECTORY",
    "MARKER_OBJECT",
    "MAXIMUM_LISTING_PAGES",
    "OUTPUTS_BUCKET",
    "OUTPUT_PREFIX_VARIABLE",
    "STEP_DIRECTORY",
    "WANDB_ENTITY_VARIABLE",
    "WANDB_PROJECT_VARIABLE",
    "CheckpointLister",
    "LifecycleProjection",
    "UnmappedBatchStatusError",
    "UnreadableBatchEventError",
    "checkpoints_under",
    "container_output_prefix",
    "container_variable",
    "derived_event_id",
    "described_listing_checksum",
    "project_batch_event",
    "project_batch_state_change",
    "transition_is_recordable",
    "wandb_run_for",
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

#: The container environment variable ``batch_submit_request`` sets to the run's output
#: prefix, and the name this projection reads it back under. Spelled in two modules with
#: nothing connecting them, which is why a test compares the two rather than each against
#: a constant: a rename on one side leaves every result manifest silently prefix-less.
OUTPUT_PREFIX_VARIABLE: Final = "EDULLM_OUTPUT_PREFIX"

#: The one ``batch_submit_request`` sets to the directory a run writes checkpoints under,
#: which is the output prefix with ``checkpoints/`` on the end. Read back rather than
#: rebuilt here for the reason :func:`container_output_prefix` gives at length -- the value
#: recorded is then the location the job was actually pointed at, so a change to how the
#: path is derived cannot leave this record describing somewhere else. The same
#: two-modules-with-nothing-between-them seam as the variable above, and a test compares
#: the two spellings for the same reason.
CHECKPOINT_DIR_VARIABLE: Final = "EDULLM_CHECKPOINT_DIR"

#: W&B's own variable names, which ``batch_submit_request`` sets under W&B's spelling
#: because the wandb client reads them itself.
#:
#: The entity is read off the event rather than taken from ``execution.WANDB_ENTITY``, and
#: that is a decision rather than an accident. Importing that module would put the whole
#: submission path into the recorder's zip, and the value it holds is a claim about what
#: containers are told where the event carries what this container was told. They agree
#: today and a test compares them; when they stop agreeing, the record should describe the
#: run rather than the constant.
WANDB_ENTITY_VARIABLE: Final = "WANDB_ENTITY"
WANDB_PROJECT_VARIABLE: Final = "WANDB_PROJECT"

#: A checkpoint directory as OLMo-core names it, which is where the step number lives. The
#: library reads the step off the directory name and so does this, because a directory
#: written by the library carries no marker of ours to read one from.
STEP_DIRECTORY: Final = re.compile(r"^step(\d+)$")

#: The same thing as HuggingFace's ``Trainer`` names it, which is what post-training writes.
#:
#: RECORDED HERE EVEN THOUGH ``edullm_platform.checkpoints`` WILL NOT RESUME FROM ONE,
#: BECAUSE THIS FUNCTION ANSWERS A DIFFERENT QUESTION FROM THAT MODULE'S. A
#: ``CheckpointManifest`` built here is an honest description of what is under a prefix and
#: not a resume reference -- it carries no ``success_marker_uri``, so
#: ``CheckpointManifest.resume_reference`` refuses it. Describing a directory therefore
#: cannot cause a resume from it, and leaving it undescribed is what made a run that wrote
#: 200 MB indistinguishable from one that wrote nothing.
#:
#: Measured on run_019fc308-8858-706e-b1c0-a516d86147a0, which exited 0 and wrote sixteen
#: objects totalling 200,371,840 bytes into ``checkpoint-32/`` and whose result record said
#: ``"checkpoints": []``, beside run_019fc2e3 which wrote nothing and said the same.
HUGGINGFACE_CHECKPOINT_DIRECTORY: Final = re.compile(r"^checkpoint-(\d+)$")

#: The two layouts, tried in order, each paired with how the recorded URI spells the step.
#:
#: A tuple rather than one widened pattern because the URI has to be rebuildable: the record
#: names the directory it described, and ``step{n}`` and ``checkpoint-{n}`` are not
#: interchangeable in a location somebody will later try to open.
CHECKPOINT_DIRECTORIES: Final = (
    (STEP_DIRECTORY, "step{step}/"),
    (HUGGINGFACE_CHECKPOINT_DIRECTORY, "checkpoint-{step}/"),
)

#: The object whose presence means the payload beside it is whole.
#:
#: SPELLED HERE AS WELL AS IN ``edullm_platform.checkpoints``, AND THE DUPLICATION IS FORCED
#: RATHER THAN CHOSEN. That module holds the commit protocol and the reader that verifies
#: it, and importing it would carry it into the recorder's zip --
#: ``tests/test_lambda_package_closure.py`` refuses any module either handler carries whose
#: name matches a phase-specific one, and ``checkpoints`` is on that list. So the two
#: constants and :func:`described_listing_checksum` are restated, and
#: ``tests/test_phase3_lifecycle_projection.py`` compares each against its counterpart so
#: the copies cannot drift apart unnoticed.
MARKER_OBJECT: Final = "_SUCCESS"

#: How many pages of a listing this will follow before giving up on the prefix.
#:
#: A ceiling rather than an unbounded loop, because this runs inside an event handler with
#: a timeout and a store that kept answering with a continuation token would spend the whole
#: of it. Twenty pages is twenty thousand keys, against the thirteen objects a checkpoint
#: directory holds, so a run would need over fifteen hundred surviving checkpoints to reach
#: it.
#:
#: Reaching it records nothing rather than what was seen so far. S3 lists keys
#: lexicographically, so ``step1000/`` sorts before ``step200/`` and a truncated listing
#: hides an arbitrary subset rather than the oldest -- a partial list would therefore be a
#: record claiming a run wrote checkpoints it did not, in an order nothing chose.
MAXIMUM_LISTING_PAGES: Final = 20

_WANDB_NAME = re.compile(WANDB_NAME_PATTERN)

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


def _container_exit_code(attempt_detail: Mapping[str, Any]) -> int | None:
    """What the attempt's container returned, or None because it never returned.

    Read off the attempt rather than off the job. Both carry a container in AWS's published
    schema for ``BatchJobStateChange``, and on a retried job the job-level one describes
    whichever attempt Batch last folded up, so a record built from it could attribute one
    attempt's exit to another.

    Absent is a fact rather than a gap and is kept as None. A host reclaimed mid-run leaves
    no exit code because there was no exit, and the ordinary default of zero would record
    that as a clean finish. A boolean is refused for the same reason a string would be:
    ``True`` is an ``int`` in Python and would land in the record as ``1``.
    """
    container = attempt_detail.get("container")
    if not isinstance(container, Mapping):
        return None
    code = container.get("exitCode")
    if isinstance(code, bool) or not isinstance(code, int):
        return None
    return code


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


class CheckpointLister(Protocol):
    """The one S3 call this module makes, described so mypy has something to check.

    boto3 is absent at type-check time by design, so this is the seam -- the same discipline
    :mod:`edullm_platform.lifecycle_handler` uses for the write. A test supplies its own
    implementation and gets the same code path the deployed function takes, rather than a
    branch that only exists for tests.

    One call and not four. :func:`~edullm_platform.checkpoints.inspect_checkpoint` reads a
    marker and heads a payload as well, which is what lets it verify a digest; doing that
    here would need ``s3:GetObject`` across every team's output, and a recorder able to read
    what runs wrote is a wider thing than one able to see that they wrote. A listing carries
    the key, the size and the write time, which is everything
    :class:`~edullm_platform.contracts.results.CheckpointManifest` needs except a digest of
    the payload, and :func:`described_listing_checksum` is what stands in for that.
    """

    def list_objects_v2(self, **arguments: Any) -> Any: ...


def container_variable(detail: Mapping[str, Any], name: str) -> str | None:
    """What the container was told under one environment variable, or None if nothing.

    The environment is in AWS's published schema for ``BatchJobStateChange`` and the tags
    are not, which is why every fact this projection recovers about how a job was configured
    comes through here.
    """
    container = detail.get("container")
    if not isinstance(container, Mapping):
        return None
    environment = container.get("environment")
    if not isinstance(environment, list):
        return None
    for entry in environment:
        if not isinstance(entry, Mapping) or entry.get("name") != name:
            continue
        value = entry.get("value")
        if isinstance(value, str) and value:
            return value
    return None


def wandb_run_for(run_id: str, detail: Mapping[str, Any]) -> WandbRunRef | None:
    """Which W&B run this job would have published, or None because it cannot be said.

    THIS IS THE NAMING CONTRACT AND NOT A LOOKUP, WHICH IS WHY IT COSTS NOTHING. A run is
    named for its run id -- ``batch_submit_request`` passes no run name and the platform
    tells every submitter to search the project for the run id -- so the third field of a
    :class:`~edullm_platform.contracts.results.WandbRunRef` is already in hand. The other
    two are the entity and the project the container was handed, and both are in the
    event's own environment because the wandb client reads them under W&B's spelling.

    What this does not carry is the id W&B mints for the URL, and it must not pretend to.
    That id is chosen by W&B when the run is created and is unknowable from outside the
    container, which is the same reason ``submit-run.yml`` prints a name to search for
    rather than a link. The field is called ``run_id`` in the contract and holds the run
    name, which is what a reader searches with.

    None rather than a guess for a job that was never told a project. A CPU job admitted
    before the variable existed carries none, and a reference naming a project nobody set
    would send a reader to a place with nothing in it.

    A record rather than an observation, stated plainly. This says where the run would have
    reported, not that it did -- a container whose key W&B declined trains and logs nowhere
    and still produces this reference. Closing that needs the container to report back,
    which is a second source and is deliberately not invented here.
    """
    entity = container_variable(detail, WANDB_ENTITY_VARIABLE)
    project = container_variable(detail, WANDB_PROJECT_VARIABLE)
    if entity is None or project is None:
        return None
    if not all(_WANDB_NAME.fullmatch(value) for value in (entity, project, run_id)):
        return None
    try:
        return WandbRunRef(entity=entity, project=project, run_id=run_id)
    except ValueError:
        # The three patterns above are the contract's own, so this is unreachable today and
        # is here because of where it runs. A ValidationError raised inside the recorder
        # dead-letters the delivery and loses the event, the attempt and the result for a
        # run that happened, to save a field that names where its charts would have been.
        return None


def described_listing_checksum(entries: Sequence[tuple[str, int, str]]) -> str:
    """A SHA-256 over a canonical description of what a listing found.

    ``CheckpointManifest.checksum`` is typed ``Sha256Digest`` and a listing attests no
    digest of anything, so the field carries a digest of bytes composed here -- the sorted
    ``(key, size, attestation)`` of every object under the directory. That is honest about
    what it is and has the property the field is for, which is that it changes when anything
    under the directory changes.

    BYTE FOR BYTE WHAT ``edullm_platform.checkpoints.described_checksum`` PRODUCES, and
    restated rather than imported for the reason :data:`MARKER_OBJECT` gives. A test feeds
    both the same entries and compares, so the copies cannot drift into two answers to one
    question.
    """
    described = [
        {"key": key, "bytes": size, "attestation": attestation}
        for key, size, attestation in sorted(entries)
    ]
    payload = json.dumps(described, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _written_at(value: object) -> datetime | None:
    """A listing's ``LastModified`` as an instant, or None because it cannot be read.

    boto3 answers with a timezone-aware datetime and a CLI-backed store answers with the
    text it printed, so both are accepted. A naive instant is refused rather than assumed to
    be UTC, for the reason the envelope reader refuses one: read as UTC it is invisibly
    wrong until two records written different ways are compared.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None
    return None


def _listed(lister: CheckpointLister, *, bucket: str, key: str) -> list[Mapping[str, Any]]:
    """Every object under this prefix, following the continuation the store hands back.

    Raises rather than truncating when the page ceiling is reached; the caller turns that
    into an empty checkpoint list. See :data:`MAXIMUM_LISTING_PAGES` for why a partial
    listing is worse than none.
    """
    contents: list[Mapping[str, Any]] = []
    arguments: dict[str, Any] = {"Bucket": bucket, "Prefix": key}
    for _page in range(MAXIMUM_LISTING_PAGES):
        answer = lister.list_objects_v2(**arguments)
        contents.extend(
            entry for entry in (answer.get("Contents") or []) if isinstance(entry, Mapping)
        )
        token = answer.get("NextContinuationToken")
        if not answer.get("IsTruncated") or not isinstance(token, str) or not token:
            return contents
        arguments["ContinuationToken"] = token
    raise UnreadableBatchEventError(
        f"s3://{bucket}/{key} holds more than {MAXIMUM_LISTING_PAGES} pages of objects, so "
        "which checkpoints are under it cannot be read completely"
    )


def checkpoints_under(
    lister: CheckpointLister, *, prefix: str
) -> tuple[tuple[CheckpointManifest, ...], CheckpointSurvey]:
    """What a run wrote under its checkpoint prefix, and what the listing itself saw.

    One manifest per checkpoint directory, in increasing step order, which is the order
    ``ResultManifest`` requires and is also the order a reader wants -- the last entry is
    the furthest a resume could get.

    THE SECOND RETURN VALUE IS THE POINT OF THE PAIR AND NOT A CONVENIENCE. The manifests
    are a parse and the survey is an observation, and conflating them is what let one empty
    tuple mean six different things. A caller that only reads the first value learns
    nothing it did not know before; a caller that reads both can tell a prefix that was
    read and was bare from one that was never read at all.

    WHAT A LISTING CAN SAY AND WHAT IT CANNOT, because the gap is the reason this reads the
    way it does. A listing gives the key, the size and the write time of every object, so
    the step comes off the directory name, the size is the sum, the instant is the newest
    write in it, and the marker is there or it is not. It does not give the contents of a
    marker, so a prefix written by :func:`~edullm_platform.checkpoints.commit_checkpoint`
    directly -- a payload and a ``_SUCCESS`` at the checkpoint directory itself, with the
    step recorded inside the marker -- is not recorded here at all. Recording one would mean
    inventing a step number, and a step of zero in an immutable record is worse than an
    absence. Closing that needs ``s3:GetObject`` on the outputs bucket, which is a wider
    grant than this whole change asks for.

    ``success_marker_uri`` is what separates a directory a resume would load from one a
    reclaimed attempt left half written, and OLMo-core writes no marker of ours -- so these
    manifests are honest descriptions rather than resume references, and
    ``CheckpointManifest.resume_reference`` refuses them, which is correct.

    Every failure is still an empty tuple and still never raises, because this is called
    while projecting an event and an exception loses the whole lineage record for the run.
    What changed is that each failure now names itself in the survey instead of arriving as
    the same silence.
    """
    location = urlparse(prefix)
    bucket, key = location.netloc, location.path.lstrip("/")
    if location.scheme != "s3" or not bucket or not key.endswith("/"):
        return (), _survey(CheckpointListingOutcome.PREFIX_NOT_OURS)
    try:
        contents = _listed(lister, bucket=bucket, key=key)
    except UnreadableBatchEventError:
        # The page ceiling, which is this module's own refusal rather than the store's, and
        # is worth separating from a store that said no: one means the run wrote more than
        # this can read and the other means nobody was allowed to look.
        return (), _survey(CheckpointListingOutcome.TOO_MANY_PAGES)
    except Exception:  # noqa: BLE001
        # Broad because botocore's exception classes are not importable here and because the
        # set of ways a listing can fail is open. Narrowed by what it does rather than by
        # what it catches: nothing is recorded and nothing is raised. Unlike before, the
        # record now says a refusal happened rather than presenting it as an empty prefix.
        return (), _survey(CheckpointListingOutcome.REFUSED)
    try:
        return _describe_checkpoint_directories(contents, bucket, key)
    except Exception:  # noqa: BLE001
        return (), _survey(CheckpointListingOutcome.REFUSED)


def _survey(
    outcome: CheckpointListingOutcome,
    *,
    objects_seen: int = 0,
    bytes_seen: int = 0,
    unparsed: Sequence[str] = (),
) -> CheckpointSurvey:
    return CheckpointSurvey(
        schema_version=1,
        outcome=outcome,
        objects_seen=objects_seen,
        bytes_seen=bytes_seen,
        unparsed_directories=tuple(sorted(unparsed)[:UNPARSED_DIRECTORY_SAMPLE]),
    )


def _describe_checkpoint_directories(
    contents: Sequence[Mapping[str, Any]],
    bucket: str,
    key: str,
) -> tuple[tuple[CheckpointManifest, ...], CheckpointSurvey]:
    """Describe one layout's directories, and say what the listing saw either way.

    ONE LAYOUT PER PREFIX, CHOSEN BY WHICH ONE IS THERE, RATHER THAN BOTH MERGED. The two
    patterns cannot match the same directory name, but they can both appear under one
    prefix, and their step numbers share a namespace: ``step32/`` and ``checkpoint-32/``
    together would produce two manifests at step 32, which ``ResultManifest`` refuses as a
    duplicate and which would have been caught by the broad handler above and turned back
    into the empty list this whole change exists to stop. OLMo-core wins that tie because
    it is the layout this platform can actually resume from, and the other is then reported
    as unparsed, which is true and visible rather than silently dropped.
    """
    objects_seen = 0
    bytes_seen = 0
    directories: set[str] = set()
    per_layout: list[dict[int, dict[str, tuple[int, datetime]]]] = [
        {} for _ in CHECKPOINT_DIRECTORIES
    ]
    for entry in contents:
        relative = str(entry.get("Key", "")).removeprefix(key)
        if not relative:
            continue
        size = entry.get("Size")
        clean_size = size if isinstance(size, int) and not isinstance(size, bool) else 0
        objects_seen += 1
        bytes_seen += clean_size
        directory, separator, member = relative.partition("/")
        if not separator or not member:
            # An object sitting at the prefix root rather than in a directory, which is what
            # a HuggingFace final save_model leaves. Counted, so the survey sees it, and not
            # describable as a checkpoint because there is no step to name it by.
            continue
        directories.add(directory)
        written = _written_at(entry.get("LastModified"))
        if written is None or not isinstance(size, int) or isinstance(size, bool):
            continue
        for index, (pattern, _) in enumerate(CHECKPOINT_DIRECTORIES):
            matched = pattern.fullmatch(directory)
            if matched is not None:
                per_layout[index].setdefault(int(matched.group(1)), {})[member] = (size, written)
                break

    for index, (pattern, template) in enumerate(CHECKPOINT_DIRECTORIES):
        manifests = _manifests_for(per_layout[index], bucket=bucket, key=key, template=template)
        if not manifests:
            continue
        unparsed = sorted(name for name in directories if pattern.fullmatch(name) is None)
        return manifests, _survey(
            CheckpointListingOutcome.LISTED,
            objects_seen=objects_seen,
            bytes_seen=bytes_seen,
            unparsed=unparsed,
        )

    return (), _survey(
        CheckpointListingOutcome.LISTED,
        objects_seen=objects_seen,
        bytes_seen=bytes_seen,
        unparsed=sorted(directories),
    )


def _manifests_for(
    under: Mapping[int, Mapping[str, tuple[int, datetime]]],
    *,
    bucket: str,
    key: str,
    template: str,
) -> tuple[CheckpointManifest, ...]:
    manifests: list[CheckpointManifest] = []
    for step in sorted(under):
        members = under[step]
        total = sum(size for size, _ in members.values())
        if total <= 0:
            # A directory of empty objects is a write that started and produced nothing, and
            # the contract requires a positive size. Left out rather than recorded as zero.
            continue
        uri = f"s3://{bucket}/{key}{template.format(step=step)}"
        manifests.append(
            CheckpointManifest(
                schema_version=1,
                uri=uri,
                step=step,
                # A listing cannot say which epoch a step belongs to, and the directory name
                # does not carry one. None is the honest answer rather than a derived guess.
                epoch=None,
                created_at=max(written for _, written in members.values()),
                size_bytes=total,
                checksum=described_listing_checksum(
                    [(name, size, "listing") for name, (size, _) in members.items()]
                ),
                success_marker_uri=f"{uri}{MARKER_OBJECT}" if MARKER_OBJECT in members else None,
            )
        )
    return tuple(manifests)


def container_output_prefix(detail: Mapping[str, Any]) -> str | None:
    """The output prefix this job's container was actually given, or None if it was not.

    READ FROM THE EVENT RATHER THAN REBUILT, AND THE DIFFERENCE IS THE WHOLE POINT. This
    used to be a literal here -- ``s3://{bucket}/{run_id}/`` -- which was wrong in a way
    nothing could see until a run wrote something. The container is told
    ``teams/{team}/runs/{run_id}/`` by ``batch_submit_request``, so the result manifest
    claimed one location while the checkpoint went to another, and the workload role does
    not even permit the one lineage named.

    Rebuilding it here would need the team, and the obvious source for that was the
    ``edullm:team`` job tag. AWS's published schema for ``BatchJobStateChange`` settles
    that: the detail carries attempts, container, createdAt, dependsOn, jobDefinition,
    jobId, jobName, jobQueue, parameters, retryStrategy and status -- and **no tags**. A
    recorder written against that assumption would have found nothing.

    ``container.environment`` is in the schema, so the value the container was handed is
    readable directly. That is better than any reconstruction: what gets recorded is the
    location the job was actually pointed at, so the manifest and the container cannot
    disagree even if the derivation changes underneath them.

    None rather than a guess when it is absent. A job submitted by hand, or by a future
    path that forgets the variable, has no prefix anybody can name -- and an empty
    ``output_prefixes`` says so, where a plausible literal would not.
    """
    value = container_variable(detail, OUTPUT_PREFIX_VARIABLE)
    if value is not None and value.startswith("s3://"):
        return value
    return None


def _checkpoints_written(
    detail: Mapping[str, Any],
    *,
    output_bucket: str,
    checkpoint_lister: CheckpointLister | None,
) -> tuple[tuple[CheckpointManifest, ...], CheckpointSurvey]:
    """What is under this job's checkpoint prefix, and why that is what it says.

    Bounded by the bucket this platform owns, exactly as ``output_prefixes`` is and for the
    same reason. The prefix comes off the event, so it is only as trustworthy as whoever set
    the job definition, and a checkpoint list assembled from a foreign bucket would be this
    record vouching for objects nothing here controls or can read back.

    The three refusals here are separated in the survey rather than collapsed, because they
    have different owners. Nothing to list with is this platform's own wiring, no declared
    prefix is the job definition, and a prefix in somebody else's bucket is the submission.
    """
    if checkpoint_lister is None:
        return (), _survey(CheckpointListingOutcome.NOT_ATTEMPTED)
    prefix = container_variable(detail, CHECKPOINT_DIR_VARIABLE)
    if prefix is None:
        return (), _survey(CheckpointListingOutcome.NO_PREFIX_DECLARED)
    if not prefix.startswith(f"s3://{output_bucket}/"):
        return (), _survey(CheckpointListingOutcome.PREFIX_NOT_OURS)
    return checkpoints_under(checkpoint_lister, prefix=prefix)


def project_batch_state_change(
    *,
    eventbridge_event_id: str,
    detail: Mapping[str, Any],
    occurred_at: datetime,
    output_bucket: str = OUTPUTS_BUCKET,
    retention_class: RetentionClass = RetentionClass.STANDARD,
    checkpoint_lister: CheckpointLister | None = None,
) -> LifecycleProjection:
    """Project one ``Batch Job State Change`` detail onto the Phase 0 contracts.

    ``occurred_at`` is the envelope's ``time`` rather than anything in the detail. Batch's
    own instants say when the job was created, started and stopped, and no one of them is
    the instant of *this* state change -- there is no ``pendingAt``. The envelope's time is
    when EventBridge saw the change, which is the closest fact anybody has to it, and using
    it uniformly means the events of one run are ordered by one clock.

    ``checkpoint_lister`` defaults to ``None``, and the default is what keeps this function
    pure for every caller that does not need the field. Without one, ``checkpoints`` is
    empty; with one, it is what the run left under its own checkpoint prefix. See the module
    docstring for why this read does not have the redelivery problem a describe would.
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

    # Bounded by the bucket this platform owns, which is what ``output_bucket`` is for now
    # that the prefix is read rather than assembled. A container pointed somewhere else did
    # not write this platform's output, and a result manifest naming a foreign bucket would
    # be this record endorsing a location nothing here controls or can read back.
    written_under = container_output_prefix(detail)
    if written_under is not None and not written_under.startswith(f"s3://{output_bucket}/"):
        written_under = None
    if written_under is None and _terminal_state(state) is AttemptTerminalState.SUCCEEDED:
        # REFUSED RATHER THAN RECORDED EMPTY, because ResultManifest already says a
        # succeeded run must name where it wrote and it is right to. Every job this
        # platform submits is handed the variable by batch_submit_request, so a succeeded
        # job without a readable one inside our own bucket is not a job whose output this
        # record can honestly locate.
        #
        # This is loud on purpose. The event goes round the retry loop and then to the
        # dead-letter queue, where an alarm is already watching, which is the behaviour a
        # run that succeeded and cannot be found deserves. The alternative -- recording a
        # plausible prefix so the write succeeds -- is precisely the defect being removed.
        raise UnreadableBatchEventError(
            "a succeeded Batch job carried no readable "
            f"{OUTPUT_PREFIX_VARIABLE} inside {output_bucket}, so where its output went "
            "cannot be recorded"
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
                checkpoints_written, checkpoint_survey = _checkpoints_written(
                    detail,
                    output_bucket=output_bucket,
                    checkpoint_lister=checkpoint_lister,
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
                    #
                    # Read out of the event rather than rebuilt here; see
                    # container_output_prefix for why, and for what the schema does and
                    # does not carry. Empty when the job carried no prefix, because a
                    # location nobody named is not a location this record should invent.
                    output_prefixes=tuple(prefix for prefix in (written_under,) if prefix),
                    exit_code=_container_exit_code(attempt_detail),
                    # WHAT THE RUN PRODUCED, WHICH THIS RECORD COULD NOT PREVIOUSLY SAY.
                    #
                    # Both fields were written empty on every result, so a run that trained
                    # for hours and wrote a 762 MB checkpoint and a run that saved nothing
                    # were the same record. The comment that stood here said neither could
                    # be known from an event, and it was half right. The W&B run is
                    # knowable, because the entity and the project are in the event's own
                    # container environment and the naming contract supplies the third
                    # field -- see wandb_run_for. The checkpoint list is not in the event,
                    # and it is not guessed at either; it is read from the prefix the
                    # container was told to write to, and only when a caller supplied
                    # something to read with.
                    #
                    # Recorded for a failed run as well as a succeeded one, deliberately.
                    # An attempt reclaimed at hour eleven of twelve has checkpoints, and
                    # they are the whole reason a retry is worth paying for -- a record
                    # that listed them only on success would be silent about exactly the
                    # runs somebody needs to resume.
                    checkpoints=checkpoints_written,
                    # What the listing behind that field saw, which is a separate fact from
                    # what it parsed. An empty list beside `objects_seen: 16` is a run that
                    # saved in a layout nothing here reads; beside `objects_seen: 0` and
                    # `outcome: listed` it is a run that genuinely saved nothing; beside
                    # `outcome: refused` it is nobody having looked.
                    checkpoint_survey=checkpoint_survey,
                    wandb_run=wandb_run_for(run_id, detail),
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
    checkpoint_lister: CheckpointLister | None = None,
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
        raise UnreadableBatchEventError("the delivery's time is not an RFC 3339 date-time") from exc
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
        checkpoint_lister=checkpoint_lister,
    )
