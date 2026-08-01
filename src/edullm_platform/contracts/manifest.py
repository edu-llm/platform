from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BeforeValidator, Field, model_validator

from .base import (
    SHA256_DIGEST_PATTERN,
    ContractModel,
    PositiveStrictDecimal,
    require_ordered_sequence,
)
from .validation import (
    require_a_shell_command_that_kept_its_quotes,
    require_checkpoint_for_retries,
    require_startable_program,
)
from .workload import CheckpointContract

COMMIT_SHA_PATTERN = r"^[0-9a-f]{40}$"
IMAGE_DIGEST_PATTERN = SHA256_DIGEST_PATTERN


class FanOut(ContractModel):
    size: int = Field(ge=2)
    #: DECLARED AND NOT ENFORCED, AND BATCH IS THE REASON RATHER THAN AN OVERSIGHT HERE.
    #:
    #: ``SubmitJob``'s ``arrayProperties`` accepts ``size`` and nothing else, confirmed
    #: against the API reference on 2026-08-01. There is no cap on how many children of an
    #: array job run at once, so this value has nowhere to go and ``batch_submit_request``
    #: correctly does not send it. What actually bounds concurrency is the compute
    #: environment's ``MaxvCpus`` divided by what one child reserves.
    #:
    #: Kept rather than removed, because it is the submitter's stated intent and an
    #: approver reading a fan-out of two hundred should see what the submitter believed
    #: would run at once. Removing it would also change the canonical form of every
    #: manifest carrying a fan-out, and those records are immutable. What was wrong was
    #: presenting it as a control, which the form and the approver summary now do not.
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
        # The two quoting mistakes are opposites and both reach an instance without a rule
        # here: quoting that survived the form field arrives as one token holding the whole
        # line, and quoting that was lost leaves a shell with a one-word command and the
        # program's arguments trailing behind it.
        AfterValidator(require_a_shell_command_that_kept_its_quotes),
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
