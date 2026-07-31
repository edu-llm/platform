from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BeforeValidator, Field, model_validator

from .base import (
    SHA256_DIGEST_PATTERN,
    ContractModel,
    PositiveStrictDecimal,
    require_ordered_sequence,
)
from .bindings import SLUG_PATTERN
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
    # THE ONE GROUPING KEY THAT REGISTERS NOTHING, AND THE ONLY FIELD HERE HELD TO A SHAPE
    # WITHOUT A REGISTRY BEHIND IT.
    #
    # `team`, `dataset_release`, `workload_profile` and `repository` are all closed sets:
    # each names something reviewed, so each is a dropdown and adding one is a pull request.
    # Grouping runs carries no consequence anybody needs to review, and making somebody file
    # a pull request to say "these six are the context-length sweep" is governance with
    # nothing on the other side of it. It is also the only thing that works -- a
    # workflow_dispatch `choice` reads its options from the default branch, so a dropdown
    # could not be extended from a branch even if the review were wanted.
    #
    # Patterned anyway, and the reason is grouping rather than safety. Nothing downstream
    # breaks on "Context Length Sweep"; the grouping does, quietly, because this value lands
    # in a Batch tag and a W&B run group and two people typing the same words with different
    # capitals get two groups that look like one.
    #
    # Required rather than defaulted. A default is a group whose membership means "nobody
    # said", and a cost query cannot tell that apart from a real one.
    project: str = Field(min_length=1, pattern=SLUG_PATTERN)
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
