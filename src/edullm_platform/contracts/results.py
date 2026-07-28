from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, model_validator

from .base import ContractModel, Sha256Digest, UtcTimestamp, require_ordered_sequence
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
