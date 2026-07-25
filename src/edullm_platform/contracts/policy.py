from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, model_validator

from .base import ContractModel, PositiveStrictDecimal, StrictDecimal, require_ordered_sequence

DeniedOutrightCondition = Literal[
    "unregistered_repository",
    "unregistered_dataset",
    "unregistered_compute_profile",
    "mutable_repository_revision",
    "mutable_image_reference",
]


class ApprovalClass(StrEnum):
    ROUTINE = "routine"
    EXCEPTION = "exception"


class PolicyThresholds(ContractModel):
    routine_maximum_cost_usd: StrictDecimal = Field(ge=0)
    routine_maximum_runtime_hours: PositiveStrictDecimal = Field(gt=0)
    routine_maximum_attempts: int = Field(ge=1)


class RequestFacts(ContractModel):
    repository_registered: bool
    dataset_registered: bool
    compute_profile_registered: bool
    immutable_revision: bool
    immutable_image: bool
    estimated_cost_usd: StrictDecimal = Field(ge=0)
    maximum_runtime_hours: StrictDecimal = Field(gt=0)
    maximum_attempts: int = Field(ge=1)


class ApprovalPolicy(ContractModel):
    thresholds: PolicyThresholds
    routine_approver_role: str = Field(min_length=1)
    exception_approver_roles: Annotated[
        tuple[str, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)
    denied_outright: Annotated[
        tuple[DeniedOutrightCondition, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)

    @model_validator(mode="after")
    def validate_exception_approvals_are_stronger(self) -> Self:
        if self.routine_approver_role in self.exception_approver_roles:
            raise ValueError(
                "routine approver role must not satisfy exception approval on its own"
            )
        return self


def classify_request(
    facts: RequestFacts,
    thresholds: PolicyThresholds,
) -> ApprovalClass:
    if (
        facts.repository_registered
        and facts.dataset_registered
        and facts.compute_profile_registered
        and facts.immutable_revision
        and facts.immutable_image
        and facts.estimated_cost_usd <= thresholds.routine_maximum_cost_usd
        and facts.maximum_runtime_hours <= thresholds.routine_maximum_runtime_hours
        and facts.maximum_attempts <= thresholds.routine_maximum_attempts
    ):
        return ApprovalClass.ROUTINE
    return ApprovalClass.EXCEPTION
