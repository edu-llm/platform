from collections.abc import Mapping
from typing import Annotated, Any, Literal, Self

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
from .vocabulary import InputRoleValue
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


class RunInput(ContractModel):
    """One thing a run reads, and what it is to that run.

    Two fields and not three. The role is what the platform checks and the reference is what it
    resolves, and there is no third field naming a kind: a kind is derivable from the reference
    through the registry, and a second copy of it here would be the first place a manifest could
    disagree with the file that resolves it.

    ``reference`` is a registry ``reference_id`` or a ``run_`` id, and which one it is is read
    from its shape rather than declared. Resolution is not this layer's job: a contract that
    knew how to reach S3 would be a contract the admission validator's zip could not hold.
    """

    role: InputRoleValue
    reference: str = Field(min_length=1)


class RunManifestV2(ContractModel):
    """A run manifest whose inputs carry roles.

    A SECOND MODEL AND NOT AN EDIT TO THE FIRST. RunManifest is hashed whole and its digest is
    what an approver releases and what the lineage store keeps. Adding a field to it moves the
    digest of every record ever written -- measured, at submission.py's note on `experiment` --
    so the version-one model stays exactly as it is and this stands beside it.

    Every field is RunManifest's except that ``dataset_release`` becomes ``inputs``. Nothing
    else changed, deliberately: a version bump that also rearranged unrelated fields would make
    the diff between the two unreadable, and the reader below has to be obviously exhaustive.
    """

    schema_version: Literal[2]
    repository: str = Field(min_length=1)
    commit_sha: str = Field(pattern=COMMIT_SHA_PATTERN)
    image_digest: str = Field(pattern=IMAGE_DIGEST_PATTERN)
    inputs: Annotated[tuple[RunInput, ...], BeforeValidator(require_ordered_sequence)] = Field(
        min_length=1, strict=False
    )
    command: Annotated[
        tuple[str, ...],
        BeforeValidator(require_ordered_sequence),
        AfterValidator(require_startable_program),
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

    @model_validator(mode="after")
    def validate_inputs(self) -> Self:
        named = [(entry.role, entry.reference) for entry in self.inputs]
        if named != sorted(set(named)):
            raise ValueError(
                "a run's inputs must be named once each in ascending order of role and "
                "reference, so two manifests describing the same run hash alike"
            )
        return self


AnyRunManifest = RunManifest | RunManifestV2


def read_run_manifest(document: Mapping[str, Any]) -> AnyRunManifest:
    """Parse a stored manifest at whichever version it was written.

    NO FALLBACK BETWEEN THE TWO, AND THAT IS THE POINT. A reader that tried version two and fell
    back to version one on failure would accept a v2 document whose ``inputs`` block was
    malformed by dropping the block entirely -- recording a run as having read nothing, in the
    one store that cannot be corrected afterwards. The version in the document selects the model
    and a parse failure is a parse failure.
    """
    version = document.get("schema_version")
    if version == 1:
        return RunManifest.model_validate(document)
    if version == 2:
        return RunManifestV2.model_validate(document)
    raise ValueError(
        f"run manifest schema version {version!r} is not one this platform reads; "
        "the versions in the lineage store are 1 and 2"
    )
