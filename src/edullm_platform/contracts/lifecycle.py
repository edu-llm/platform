from collections.abc import Iterable
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    BeforeValidator,
    Field,
    model_validator,
)

from ..canonical import sha256_digest
from .base import (
    ContractModel,
    Sha256Digest,
    UtcTimestamp,
    parse_str_enum,
)
from .bindings import GitHubLogin, TeamId
from .identity import AttemptId, RunId, generate_uuid7
from .vocabulary import JobTypeValue
from .workload import CHECKPOINT_DESTINATION_PREFIX_PATTERN

UUID_TEXT_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
EVENT_ID_PREFIX = "evt_"
EVENT_ID_PATTERN = f"^{EVENT_ID_PREFIX}{UUID_TEXT_PATTERN}$"
SCHEDULER_JOB_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9:/_.-]{0,511}$"

SandboxS3Prefix = Annotated[str, Field(pattern=CHECKPOINT_DESTINATION_PREFIX_PATTERN)]
EventId = Annotated[str, Field(pattern=EVENT_ID_PATTERN)]
SchedulerJobId = Annotated[str, Field(pattern=SCHEDULER_JOB_ID_PATTERN)]


class RunState(StrEnum):
    SUBMITTED = "submitted"
    RUNNABLE = "runnable"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_RUN_STATES


class AttemptTerminalState(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EventSource(StrEnum):
    SCHEDULER = "scheduler"
    PLATFORM = "platform"
    WORKLOAD = "workload"
    OPERATOR = "operator"


RUN_STATE_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.SUBMITTED: frozenset({RunState.RUNNABLE, RunState.FAILED, RunState.CANCELLED}),
    RunState.RUNNABLE: frozenset({RunState.RUNNING, RunState.FAILED, RunState.CANCELLED}),
    RunState.RUNNING: frozenset(
        {RunState.RUNNABLE, RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED}
    ),
    RunState.SUCCEEDED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.CANCELLED: frozenset(),
}

TERMINAL_RUN_STATES: frozenset[RunState] = frozenset(
    state for state, successors in RUN_STATE_TRANSITIONS.items() if not successors
)

RunStateValue = Annotated[RunState, BeforeValidator(parse_str_enum(RunState))]
AttemptTerminalStateValue = Annotated[
    AttemptTerminalState, BeforeValidator(parse_str_enum(AttemptTerminalState))
]
EventSourceValue = Annotated[EventSource, BeforeValidator(parse_str_enum(EventSource))]


def is_valid_run_transition(current: RunState, proposed: RunState) -> bool:
    return proposed in RUN_STATE_TRANSITIONS[current]


def run_state_for_attempt(terminal_state: AttemptTerminalState) -> RunState:
    return RunState(terminal_state.value)


def new_event_id() -> str:
    return f"{EVENT_ID_PREFIX}{generate_uuid7()}"


class ConflictingLifecycleEventError(ValueError):
    def __init__(self, event_id: str) -> None:
        super().__init__(
            f"lifecycle event {event_id!r} was delivered twice with different content"
        )
        self.event_id = event_id


class CheckpointRef(ContractModel):
    uri: SandboxS3Prefix
    checksum: Sha256Digest


class LogicalRun(ContractModel):
    schema_version: Literal[1]
    run_id: RunId
    manifest_digest: Sha256Digest
    submitted_by: GitHubLogin
    team_id: TeamId
    job_type: JobTypeValue
    created_at: UtcTimestamp
    parent_run_id: RunId | None
    resumed_from: CheckpointRef | None

    @model_validator(mode="after")
    def validate_resume_lineage(self) -> Self:
        if self.parent_run_id == self.run_id:
            raise ValueError("a logical run must not be its own parent")
        if (self.parent_run_id is None) != (self.resumed_from is None):
            raise ValueError(
                "a resumed run must record both its parent run and the checkpoint it resumed from"
            )
        return self

    @property
    def is_resumed(self) -> bool:
        return self.parent_run_id is not None


class SchedulerAttempt(ContractModel):
    schema_version: Literal[1]
    attempt_id: AttemptId
    run_id: RunId
    attempt_ordinal: int = Field(ge=1)
    scheduler_job_id: SchedulerJobId
    started_at: UtcTimestamp
    ended_at: UtcTimestamp
    terminal_state: AttemptTerminalStateValue

    @model_validator(mode="after")
    def validate_attempt_window(self) -> Self:
        if self.ended_at < self.started_at:
            raise ValueError("an attempt must not end before it starts")
        return self

    @property
    def run_state(self) -> RunState:
        return run_state_for_attempt(self.terminal_state)


class LifecycleEvent(ContractModel):
    schema_version: Literal[1]
    event_id: EventId
    run_id: RunId
    attempt_id: AttemptId | None
    source: EventSourceValue
    state: RunStateValue
    occurred_at: UtcTimestamp

    @property
    def deduplication_key(self) -> str:
        return self.event_id

    def is_duplicate_of(self, other: "LifecycleEvent") -> bool:
        return self.deduplication_key == other.deduplication_key


def deduplicate_lifecycle_events(
    events: Iterable[LifecycleEvent],
) -> tuple[LifecycleEvent, ...]:
    digest_by_event_id: dict[str, str] = {}
    unique: list[LifecycleEvent] = []
    for event in events:
        digest = sha256_digest(event)
        recorded = digest_by_event_id.get(event.deduplication_key)
        if recorded is None:
            digest_by_event_id[event.deduplication_key] = digest
            unique.append(event)
        elif recorded != digest:
            raise ConflictingLifecycleEventError(event.deduplication_key)
    return tuple(unique)
