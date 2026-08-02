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
    #: THERE WAS A max_parallel BESIDE THIS AND REMOVING IT IS THE POINT OF ITS ABSENCE.
    #:
    #: ``SubmitJob``'s ``arrayProperties`` accepts ``size`` and nothing else, so the field
    #: had nowhere to go and ``batch_submit_request`` never sent it. It was kept for a
    #: while as the submitter's stated intent, on the argument that an approver reading a
    #: fan-out of two hundred should see what the submitter believed would run at once.
    #: That argument was wrong in the direction that costs something. A submitter who set
    #: it believed a concurrency limit existed, no limit existed, and the number they read
    #: back on the approver page confirmed the belief. A control that does nothing is worse
    #: than no control, because the absent one sends somebody to ask how concurrency is
    #: actually bounded.
    #:
    #: What bounds it is the compute environment's ``MaxvCpus`` divided by what one child
    #: reserves, and nothing in a manifest can change that.
    #:
    #: THE COST OF REMOVAL WAS CHECKED RATHER THAN ASSUMED, because a contract field is
    #: content-addressed and the note this replaces claimed the removal would change the
    #: canonical form of every manifest carrying a fan-out. It does, and no such record
    #: exists. Every intent record committed under fixtures/evidence carries
    #: ``"fanout":null``, which serializes the same whatever this model holds, so no stored
    #: digest moves. A record written with a ``max_parallel`` would now fail to load rather
    #: than load wrongly, because ContractModel forbids extra keys.
    size: int = Field(ge=2)
    index_parameter: str = Field(min_length=1)


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
