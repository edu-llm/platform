from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BeforeValidator, Field, model_validator

from .base import (
    SHA256_DIGEST_PATTERN,
    ContractModel,
    PositiveStrictDecimal,
    require_ordered_sequence,
)
from .validation import require_checkpoint_for_retries, require_startable_program
from .workload import CheckpointContract

COMMIT_SHA_PATTERN = r"^[0-9a-f]{40}$"
IMAGE_DIGEST_PATTERN = SHA256_DIGEST_PATTERN


class FanOut(ContractModel):
    size: int = Field(ge=2)
    max_parallel: int = Field(ge=1)
    index_parameter: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_parallelism_within_size(self) -> Self:
        if self.max_parallel > self.size:
            raise ValueError("fan-out parallelism must not exceed fan-out size")
        return self


class RunManifest(ContractModel):
    schema_version: Literal[1]
    repository: str = Field(min_length=1)
    commit_sha: str = Field(pattern=COMMIT_SHA_PATTERN)
    image_digest: str = Field(pattern=IMAGE_DIGEST_PATTERN)
    dataset_release: str = Field(min_length=1)
    command: Annotated[
        tuple[str, ...],
        BeforeValidator(require_ordered_sequence),
        AfterValidator(require_startable_program),
    ] = Field(min_length=1, strict=False)
    team: str = Field(min_length=1)
    wandb_project: str = Field(min_length=1)
    workload_profile: str = Field(min_length=1)
    compute_profile: str = Field(min_length=1)
    maximum_runtime_hours: PositiveStrictDecimal = Field(gt=0)
    maximum_attempts: int = Field(ge=1)
    checkpoint: CheckpointContract | None
    fanout: FanOut | None = None

    @model_validator(mode="after")
    def validate_retry_checkpoint(self) -> Self:
        require_checkpoint_for_retries(
            maximum_attempts=self.maximum_attempts,
            checkpoint=self.checkpoint,
        )
        return self
