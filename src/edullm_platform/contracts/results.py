from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, model_validator

from .base import (
    ContractModel,
    Sha256Digest,
    UtcTimestamp,
    parse_str_enum,
    require_ordered_sequence,
)
from .bindings import WandbEntity
from .identity import AttemptId, RunId
from .lifecycle import (
    AttemptTerminalState,
    AttemptTerminalStateValue,
    CheckpointRef,
    SandboxS3Prefix,
)
from .vocabulary import RetentionClassValue

WANDB_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"

#: How many unparsed directory names one survey records.
#:
#: Enough to identify a layout and not enough to make a lineage record grow with the size of
#: whatever confused it. Three names is one glance at a pattern; a hundred is a log.
UNPARSED_DIRECTORY_SAMPLE = 3

#: The bucket a workload writes its own output to. Never the lineage bucket, whose entire
#: property is that only the platform writes to it.
OUTPUTS_BUCKET = "sbsandbox-intern-edullm-outputs"


def output_prefix(*, team: str, run_id: str, bucket: str = OUTPUTS_BUCKET) -> str:
    """Where one run's output goes, derived once and read by everything that needs it.

    THIS FUNCTION EXISTS BECAUSE THREE PLACES ANSWERED THIS QUESTION AND TWO AGREED. Until
    it was written, the result manifest recorded ``{bucket}/{run_id}/``, the workload role
    permitted ``{bucket}/teams/data-prep/runs/*``, and the job definition told the container
    ``teams/data-prep/runs/``. The container and the role agreed with each other, so nothing
    failed; the lineage record simply described a location the workload was not permitted to
    write to and had never been told about.

    It survived Phase 3 because the only command ever run there printed a line and wrote
    nothing, so no reader ever followed the prefix to see whether anything was at the end of
    it. A training run writes checkpoints, which is why the drift stops being free here.

    Both segments earn their place. ``teams/{team}`` is what makes cross-team isolation
    expressible in IAM at all -- a prefix condition can be written against it, and the
    Phase 4 check that a workload role cannot reach another team's prefix has nothing to
    say without it. ``runs/{run_id}`` is what makes the result manifest's
    ``output_prefixes`` a claim a reader can verify rather than a decoration.

    The team is not defaulted. A default would be a run whose output is filed under
    somebody else's team on the one path where the team was not resolved, and the
    attribution would be wrong in a store nothing rewrites.
    """
    if not team:
        raise ValueError("an output prefix needs the team the run is charged to")
    if not run_id:
        raise ValueError("an output prefix needs the run it belongs to")
    return f"s3://{bucket}/teams/{team}/runs/{run_id}/"


class CheckpointNotResumableError(ValueError):
    pass


class WandbRunRef(ContractModel):
    entity: WandbEntity
    project: str = Field(pattern=WANDB_NAME_PATTERN)
    run_id: str = Field(pattern=WANDB_NAME_PATTERN)


class CheckpointManifest(ContractModel):
    schema_version: Literal[1]
    uri: SandboxS3Prefix
    step: int = Field(ge=0)
    epoch: int | None = Field(ge=0)
    created_at: UtcTimestamp
    size_bytes: int = Field(gt=0)
    checksum: Sha256Digest
    success_marker_uri: str | None = Field(min_length=1)

    @model_validator(mode="after")
    def validate_success_marker(self) -> Self:
        if self.success_marker_uri is None:
            return self
        if not self.success_marker_uri.startswith(self.uri):
            raise ValueError("a success marker must be written inside its own checkpoint prefix")
        if self.success_marker_uri.endswith("/"):
            raise ValueError("a success marker must name an object, not a prefix")
        return self

    @property
    def is_resumable(self) -> bool:
        return self.success_marker_uri is not None

    def resume_reference(self) -> CheckpointRef:
        if not self.is_resumable:
            raise CheckpointNotResumableError(
                f"checkpoint {self.uri} carries no success marker and must not be resumed from"
            )
        return CheckpointRef(uri=self.uri, checksum=self.checksum)


class CheckpointListingOutcome(StrEnum):
    """Why ``ResultManifest.checkpoints`` holds what it holds.

    AN EMPTY LIST CARRIED SIX MEANINGS AND A READER COULD NOT SEPARATE THEM. No lister was
    supplied, the listing was refused, the prefix genuinely held nothing, the layout was one
    the matcher does not read, the listing ran past its page ceiling, or the prefix named a
    bucket this platform does not own. All six serialized as ``"checkpoints": []``, so a run
    that trained for hours and saved 200 MB and a run that saved nothing produced the same
    record in the one field that exists to tell them apart.

    Every member below except ``LISTED`` means the list is empty for a reason that is not
    "nothing was written". ``LISTED`` with an empty list, and only that combination, is the
    statement that the prefix was read and was genuinely bare.
    """

    #: The listing completed and what is recorded is what is there.
    LISTED = "listed"
    #: No lister was passed in, so nothing was asked. The ordinary shape for a projection
    #: built off an event without a store behind it.
    NOT_ATTEMPTED = "not_attempted"
    #: The event carried no ``EDULLM_CHECKPOINT_DIR``, so there is no prefix to read.
    NO_PREFIX_DECLARED = "no_prefix_declared"
    #: The prefix is not an ``s3://`` directory inside this platform's own outputs bucket.
    PREFIX_NOT_OURS = "prefix_not_ours"
    #: The store refused or failed. Until the lifecycle role carried ``s3:ListBucket`` this
    #: was the live answer for every run, and it read as an empty list.
    REFUSED = "refused"
    #: More pages than the ceiling, so a partial answer was discarded rather than recorded.
    TOO_MANY_PAGES = "too_many_pages"


CheckpointListingOutcomeValue = Annotated[
    CheckpointListingOutcome, BeforeValidator(parse_str_enum(CheckpointListingOutcome))
]


class CheckpointSurvey(ContractModel):
    """What the listing saw, as distinct from what the matcher could parse out of it.

    AN OBJECT COUNT AND A PARSED LIST ARE DIFFERENT FACTS, WHICH IS THE WHOLE REASON THIS
    EXISTS. ``checkpoints`` is the second one and it is the one that goes empty for six
    different reasons. ``objects_seen`` is the first, and it is what separates the two
    cases that matter most: zero objects under a prefix that was successfully read is a run
    that saved nothing, and sixteen objects under the same prefix is a run that saved and
    whose layout nothing here recognised.

    ``unparsed_directories`` names what was skipped, bounded, because the count alone says
    something is wrong and the names say what. A record reading ``checkpoint-32`` tells the
    next reader it is a HuggingFace layout in one glance, where a bare count sends them to
    the bucket.
    """

    schema_version: Literal[1]
    outcome: CheckpointListingOutcomeValue
    #: Every object under the prefix, whatever directory it sat in and whether or not the
    #: matcher understood it. Zero when the outcome is anything other than ``LISTED``,
    #: because nothing was counted rather than nothing was there.
    objects_seen: int = Field(ge=0)
    #: Their total size. A run that wrote 200 MB and a run that wrote four empty files are
    #: both "objects present" and only one of them saved a model.
    bytes_seen: int = Field(ge=0)
    #: Directory names directly under the prefix that no layout matched, sorted, and capped
    #: at :data:`UNPARSED_DIRECTORY_SAMPLE` so a pathological prefix cannot make this record
    #: unbounded. Empty when everything was understood.
    unparsed_directories: Annotated[
        tuple[str, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(default=(), strict=False)

    @model_validator(mode="after")
    def validate_survey(self) -> Self:
        if self.outcome is not CheckpointListingOutcome.LISTED and (
            self.objects_seen or self.bytes_seen or self.unparsed_directories
        ):
            raise ValueError(
                "only a completed listing may report what it saw; every other outcome means "
                "nothing was counted"
            )
        if len(set(self.unparsed_directories)) != len(self.unparsed_directories):
            raise ValueError("unparsed directory names must be unique")
        if len(self.unparsed_directories) > UNPARSED_DIRECTORY_SAMPLE:
            raise ValueError(
                f"at most {UNPARSED_DIRECTORY_SAMPLE} unparsed directory names are recorded"
            )
        if self.bytes_seen and not self.objects_seen:
            raise ValueError("bytes cannot have been seen without an object to hold them")
        return self

    @property
    def wrote_something_unrecognised(self) -> bool:
        """Objects are here and nothing was parsed out of them.

        The state that was invisible while an empty list was the only signal, and the one
        that costs the most, because every other field on the record says the run succeeded.
        """
        return self.outcome is CheckpointListingOutcome.LISTED and self.objects_seen > 0


class EvalMetric(ContractModel):
    """One number an evaluation produced, and enough beside it to compare two runs.

    ``instances`` is here because a score over ten instances and a score over 1,172 are
    different claims and both serialize as a float. The eval team's own sweep script keeps it
    for the same reason.
    """

    task: str = Field(min_length=1)
    key: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
    value: float
    instances: int = Field(ge=1)


class EvalMetrics(ContractModel):
    """What a run scored, as distinct from what it wrote.

    Which keys make a valid cross-run comparison is a property of the metric and not of this
    record: see system-overview.md, "Where everything is seen", for the one column that is safe
    to compare across projects and the one that is not. This model carries what the harness
    reported and makes no claim about which of it is comparable.
    """

    schema_version: Literal[1]
    #: Which harness produced these. Not an enum: a second harness is a registration, and a
    #: closed set here would refuse a record before anyone could add one.
    harness: str = Field(min_length=1)
    metrics: Annotated[tuple[EvalMetric, ...], BeforeValidator(require_ordered_sequence)] = Field(
        min_length=1, strict=False
    )

    @model_validator(mode="after")
    def validate_metrics(self) -> Self:
        named = [(entry.task, entry.key) for entry in self.metrics]
        if named != sorted(set(named)):
            raise ValueError(
                "eval metrics must be recorded once each in ascending order of task and key"
            )
        return self


class ResultManifest(ContractModel):
    schema_version: Literal[1]
    run_id: RunId
    attempt_id: AttemptId
    outcome: AttemptTerminalStateValue
    output_prefixes: Annotated[
        tuple[SandboxS3Prefix, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(default=(), strict=False)
    checkpoints: Annotated[
        tuple[CheckpointManifest, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(default=(), strict=False)
    wandb_run: WandbRunRef | None
    #: What the container returned, when it returned at all.
    #:
    #: HOW A RUN FAILED, WHICH THIS RECORD COULD NOT PREVIOUSLY SAY. ``outcome`` carries
    #: ``failed`` and nothing else, so every failure read the same: a program that raised,
    #: one killed for running out of memory, and one whose machine went away were one word
    #: in the only record that outlives the job. Batch stops listing a job some days after
    #: it ends, so by the time somebody asks, the exit code is gone from the account and
    #: this is the only place it could have been.
    #:
    #: None rather than a number when the container never reported one, which is the
    #: ordinary shape for a job whose host was reclaimed: there was no exit, so there is no
    #: code, and a zero there would read as a clean finish. Optional rather than required
    #: because every result record already written carries no such field, and they are
    #: immutable.
    exit_code: int | None = None
    #: What the listing behind ``checkpoints`` actually saw, or None on a record written
    #: before the field existed.
    #:
    #: Optional and defaulted for the same reason ``exit_code`` above is: every result
    #: record already in the lineage store carries no such field and none of them can be
    #: rewritten. A required field here would make the whole history unreadable by the
    #: contract that describes it.
    checkpoint_survey: CheckpointSurvey | None = None
    #: What this run scored, or None on a run that scored nothing and on every record written
    #: before the field existed.
    #:
    #: Optional and defaulted for the reason ``exit_code`` and ``checkpoint_survey`` above are:
    #: the lineage store already holds result records carrying no such key and none of them can
    #: be rewritten. None is also the right answer for a corpus validation, a tokenization and a
    #: smoke test, which have no eval metrics to emit and are not failing to produce them --
    #: system-overview.md, "Where everything is seen", says so in its own voice.
    eval_metrics: EvalMetrics | None = None
    retention_class: RetentionClassValue
    completed_at: UtcTimestamp

    @model_validator(mode="after")
    def validate_terminal_record(self) -> Self:
        if len(set(self.output_prefixes)) != len(self.output_prefixes):
            raise ValueError("output prefixes must be unique")
        locations = [checkpoint.uri for checkpoint in self.checkpoints]
        if len(set(locations)) != len(locations):
            raise ValueError("checkpoint locations must be unique")
        steps = [checkpoint.step for checkpoint in self.checkpoints]
        if steps != sorted(set(steps)):
            raise ValueError("checkpoints must be recorded in strictly increasing step order")
        if self.outcome is AttemptTerminalState.SUCCEEDED and not self.output_prefixes:
            raise ValueError("a succeeded run must record at least one output prefix")
        return self

    @property
    def resumable_checkpoints(self) -> tuple[CheckpointManifest, ...]:
        return tuple(checkpoint for checkpoint in self.checkpoints if checkpoint.is_resumable)

    def latest_resumable_checkpoint(self) -> CheckpointManifest | None:
        resumable = self.resumable_checkpoints
        if not resumable:
            return None
        return resumable[-1]
