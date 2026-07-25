from decimal import Decimal, InvalidOperation, localcontext
from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, computed_field, model_validator

from .base import (
    MAX_DECIMAL_DIGITS,
    ContractModel,
    PositiveStrictDecimal,
    StrictDecimal,
    require_ordered_sequence,
)
from .validation import require_checkpoint_for_retries


def compute_maximum_compute_cost_usd(
    hourly_rate_usd: Decimal,
    nodes: int,
    maximum_runtime_hours: Decimal,
    maximum_attempts: int,
) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = MAX_DECIMAL_DIGITS * 4
        try:
            product = (
                hourly_rate_usd * nodes * maximum_runtime_hours * maximum_attempts
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

    @model_validator(mode="after")
    def validate_maximum_compute_cost(self) -> Self:
        compute_maximum_compute_cost_usd(
            self.hourly_rate_usd,
            self.nodes,
            self.maximum_runtime_hours,
            self.maximum_attempts,
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
        )


class CheckpointContract(ContractModel):
    interval_minutes: int = Field(gt=0)
    destination_prefix: str = Field(pattern=r"^s3://[^/]+/.+/$")
    resume_required: bool


class ComputeProfile(ContractModel):
    name: str = Field(min_length=1)
    accelerator: Literal["cpu", "gpu"]
    nodes: int = Field(gt=0)
    hourly_rate_usd: PositiveStrictDecimal = Field(gt=0)
    pricing_source: str = Field(min_length=1)
    pricing_observed_at: str = Field(min_length=1)


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
