from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, model_validator

from .base import ContractModel, PositiveStrictDecimal, require_ordered_sequence
from .validation import require_checkpoint_for_retries
from .workload import CheckpointContract


class RunManifest(ContractModel):
    schema_version: Literal[1]
    repository: str = Field(min_length=1)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    dataset_release: str = Field(min_length=1)
    command: Annotated[tuple[str, ...], BeforeValidator(require_ordered_sequence)] = Field(
        min_length=1, strict=False
    )
    team: str = Field(min_length=1)
    wandb_project: str = Field(min_length=1)
    workload_profile: str = Field(min_length=1)
    compute_profile: str = Field(min_length=1)
    maximum_runtime_hours: PositiveStrictDecimal = Field(gt=0)
    maximum_attempts: int = Field(ge=1)
    checkpoint: CheckpointContract | None

    @model_validator(mode="after")
    def validate_retry_checkpoint(self) -> Self:
        require_checkpoint_for_retries(
            maximum_attempts=self.maximum_attempts,
            checkpoint=self.checkpoint,
        )
        return self
