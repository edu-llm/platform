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

INSTANCE_TYPE_PATTERN = r"^[a-z][a-z0-9]*\.[a-z0-9]+$"
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
    name: str = Field(min_length=1)
    repository: str = Field(min_length=1)
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
        profile_by_name = {profile.name: profile for profile in self.compute_profiles}
        for workload in self.workloads:
            if workload.compute_profile not in profile_names:
                raise ValueError(f"unknown compute profile: {workload.compute_profile}")
        workload_accelerators = {
            profile_by_name[workload.compute_profile].accelerator for workload in self.workloads
        }
        if workload_accelerators != {"cpu", "gpu"}:
            raise ValueError("representative CPU and GPU workloads are required")
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
