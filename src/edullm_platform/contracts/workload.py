from decimal import Decimal, InvalidOperation, localcontext
from typing import Annotated, Any, ClassVar, Literal, Self

from pydantic import BeforeValidator, Field, computed_field, model_validator

from .base import (
    MAX_DECIMAL_DIGITS,
    SANDBOX_BUCKET_PREFIX,
    ContractModel,
    PositiveStrictDecimal,
    StrictDecimal,
    require_ordered_sequence,
)
from .validation import require_checkpoint_for_retries

#: One EC2 instance type, held to exactly one dot with a family in front of it and a size
#: behind it.
#:
#: THE FAMILY MAY CARRY HYPHENS AND IT COULD NOT UNTIL 2026-08-07. This read
#: ``^[a-z][a-z0-9]*\.[a-z0-9]+$``, which is every instance type AWS had sold this account and
#: not the ones it now sells: ``p6-b200.48xlarge`` and ``p6-b300.48xlarge`` put the accelerator
#: in the family name, and under the old pattern a catalog naming either was refused at load
#: with a validation error rather than at review with an argument. The hyphen groups are spelled
#: out rather than folded into the character class so that a trailing or doubled hyphen is still
#: refused; ``[a-z0-9-]*`` would have admitted ``p6-.48xlarge``.
#:
#: Still exactly one dot, which is the property :func:`edullm_platform.precision.instance_family`
#: rests on when it splits a family off the front.
INSTANCE_TYPE_PATTERN = r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*\.[a-z0-9]+$"
CHECKPOINT_DESTINATION_PREFIX_PATTERN = (
    rf"^s3://{SANDBOX_BUCKET_PREFIX}[a-z0-9](?:[a-z0-9.-]{{0,44}}[a-z0-9])?/.+/$"
)


def compute_maximum_compute_cost_usd(
    hourly_rate_usd: Decimal,
    nodes: int,
    maximum_runtime_hours: Decimal,
    maximum_attempts: int,
    cells: int = 1,
) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = MAX_DECIMAL_DIGITS * 4
        try:
            product = (
                hourly_rate_usd * nodes * maximum_runtime_hours * maximum_attempts * cells
            )
            quantized = product.quantize(Decimal("0.01"))
        except InvalidOperation as exc:
            raise ValueError("maximum compute cost exceeds representable precision") from exc
    _, digits, _ = quantized.as_tuple()
    if len(digits) > MAX_DECIMAL_DIGITS:
        raise ValueError("maximum compute cost exceeds representable precision")
    return quantized


class CostInputs(ContractModel):
    hourly_rate_usd: StrictDecimal = Field(gt=0)
    nodes: int = Field(gt=0)
    maximum_runtime_hours: StrictDecimal = Field(gt=0)
    maximum_attempts: int = Field(ge=1)
    cells: int = Field(default=1, ge=1)

    @model_validator(mode="before")
    @classmethod
    def reconcile_a_recorded_total(cls, data: Any) -> Any:
        """Accept a serialized total back, and only if it still adds up.

        ``maximum_compute_cost_usd`` is a computed field, so pydantic writes it out and
        then refuses it on the way back in, because ``extra="forbid"``. That made every
        decision record in the lineage store unreadable by the model that wrote it -- an
        immutable store whose contents no longer parse is a store nobody can audit, which
        is the one thing it exists for. Found by reading real records back rather than by
        round-tripping a fixture.

        Dropping the field from the record instead would have been the smaller change and
        the wrong one: a reader without this code should be able to see what a run was
        approved to cost. So it stays written, and it is checked rather than trusted. A
        recorded total that disagrees with the inputs beside it is refused here, which
        makes an edited record fail to load instead of loading with a figure nothing
        supports.
        """
        if not isinstance(data, dict) or "maximum_compute_cost_usd" not in data:
            return data
        remaining = dict(data)
        recorded = remaining.pop("maximum_compute_cost_usd")
        candidate = cls(**remaining)
        if Decimal(str(recorded)) != candidate.maximum_compute_cost_usd:
            raise ValueError(
                "the recorded maximum_compute_cost_usd does not match the inputs recorded "
                f"beside it: {recorded} against {candidate.maximum_compute_cost_usd}"
            )
        return remaining

    @model_validator(mode="after")
    def validate_maximum_compute_cost(self) -> Self:
        compute_maximum_compute_cost_usd(
            self.hourly_rate_usd,
            self.nodes,
            self.maximum_runtime_hours,
            self.maximum_attempts,
            self.cells,
        )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def maximum_compute_cost_usd(self) -> Decimal:
        return compute_maximum_compute_cost_usd(
            self.hourly_rate_usd,
            self.nodes,
            self.maximum_runtime_hours,
            self.maximum_attempts,
            self.cells,
        )


class CheckpointContract(ContractModel):
    interval_minutes: int = Field(gt=0)
    destination_prefix: str = Field(pattern=CHECKPOINT_DESTINATION_PREFIX_PATTERN)
    resume_required: bool


class ComputeProfile(ContractModel):
    name: str = Field(min_length=1)
    instance_type: str = Field(pattern=INSTANCE_TYPE_PATTERN)
    accelerator: Literal["cpu", "gpu"]
    nodes: int = Field(gt=0)
    hourly_rate_usd: PositiveStrictDecimal = Field(gt=0)
    pricing_source: str = Field(min_length=1)
    pricing_observed_at: str = Field(min_length=1)
    provisioned: bool


class WorkloadProfile(ContractModel):
    """A policy preset, which is a repository, two bounds and a checkpoint contract.

    IT NAMED A MACHINE AND THE NAMING WAS A FICTION, WHICH IS WHY THE FIELD IS GONE.
    ``compute_profile`` sat here and ``SubmissionInputs.compute_profile`` overrode it
    unconditionally, so what a run landed on was whatever the form said and this value was
    read only when the form left the field empty. Nothing refused a disagreement between
    the two, and nothing ever could: an override that is refused when it differs is not an
    override. So a researcher reading ``olmo-core-train-1gpu`` believed the name bound the
    run to one A10G while the dropdown beside it outranked the name in silence.

    Removing the field rather than enforcing it is the choice that follows from who owns
    the answer. The machine is the submitter's decision, made per run and priced per run;
    what a workload can honestly fix is the contract the codebase keeps -- how long it may
    take, how many attempts it may have, and whether an interrupted attempt has somewhere
    to resume from. Two entries differing only in a machine were therefore two spellings of
    one policy, and the catalog now carries one of each.

    :class:`~edullm_platform.contracts.manifest.RunManifest` is unaffected and keeps its
    own ``compute_profile``. It records what a run was submitted with, which is a different
    question from what a preset declares, and its serialized shape is content addressed.
    """

    name: str = Field(min_length=1)
    repository: str = Field(min_length=1)
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


class WorkloadCatalog(ContractModel):
    compute_profiles: Annotated[
        tuple[ComputeProfile, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=2, strict=False)
    workloads: Annotated[
        tuple[WorkloadProfile, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=2, strict=False)

    @model_validator(mode="after")
    def validate_catalog(self) -> Self:
        profile_names = {profile.name for profile in self.compute_profiles}
        if len(profile_names) != len(self.compute_profiles):
            raise ValueError("compute profile names must be unique")
        workload_names = {workload.name for workload in self.workloads}
        if len(workload_names) != len(self.workloads):
            raise ValueError("workload names must be unique")
        # ASKED OF THE PROFILES BECAUSE A WORKLOAD NO LONGER NAMES ONE. This required a
        # workload on a CPU profile and a workload on a GPU profile, through the join
        # WorkloadProfile.compute_profile used to carry. The property it was establishing
        # is that this platform can run both kinds of work, which the profiles answer on
        # their own, so it is asked where the accelerator is declared. A catalog that
        # priced only CPU shapes would offer no GPU on the submission form at all.
        accelerators = {profile.accelerator for profile in self.compute_profiles}
        if accelerators != {"cpu", "gpu"}:
            raise ValueError("representative CPU and GPU compute profiles are required")
        return self


class ComputeProfileResolutionError(ValueError):
    reason_code: ClassVar[str]


class UnregisteredComputeProfileError(ComputeProfileResolutionError):
    reason_code: ClassVar[str] = "unregistered_compute_profile"


class UnprovisionedComputeProfileError(ComputeProfileResolutionError):
    reason_code: ClassVar[str] = "unprovisioned_compute_profile"


def resolve_compute_profile_for_execution(
    catalog: WorkloadCatalog,
    profile_name: str,
) -> ComputeProfile:
    profile_by_name = {profile.name: profile for profile in catalog.compute_profiles}
    profile = profile_by_name.get(profile_name)
    if profile is None:
        raise UnregisteredComputeProfileError(
            f"unregistered compute profile: {profile_name!r}"
        )
    if not profile.provisioned:
        raise UnprovisionedComputeProfileError(
            f"compute profile {profile_name!r} is priced in the catalog but no compute "
            f"environment is provisioned for {profile.instance_type}"
        )
    return profile
