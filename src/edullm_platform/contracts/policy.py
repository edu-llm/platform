from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, model_validator

from .base import (
    ContractModel,
    PositiveStrictDecimal,
    StrictDecimal,
    parse_str_enum,
    require_ordered_sequence,
)
from .bindings import TeamId
from .image_scan import ImageScanPolicy

DeniedOutrightCondition = Literal[
    "unregistered_repository",
    "unregistered_dataset",
    "unregistered_compute_profile",
    "mutable_repository_revision",
    "mutable_image_reference",
    "image_scan_findings_unreviewed",
]


class ApprovalClass(StrEnum):
    ROUTINE = "routine"
    EXCEPTION = "exception"


class ApprovalScope(StrEnum):
    ORGANIZATION = "organization"
    TEAM = "team"


ApprovalScopeValue = Annotated[ApprovalScope, BeforeValidator(parse_str_enum(ApprovalScope))]


class PolicyThresholds(ContractModel):
    routine_maximum_cost_usd: StrictDecimal = Field(ge=0)
    routine_maximum_runtime_hours: PositiveStrictDecimal = Field(gt=0)
    routine_maximum_attempts: int = Field(ge=1)
    routine_maximum_fanout_size: int = Field(ge=1)
    routine_maximum_parallelism: int = Field(ge=1)


class RequestFacts(ContractModel):
    claimed_team: TeamId
    repository_registered: bool
    dataset_registered: bool
    compute_profile_registered: bool
    immutable_revision: bool
    immutable_image: bool
    #: Whether this image's scan findings have been seen: clean of the severities policy
    #: blocks on, or carrying a recorded exception. Required rather than defaulted, and
    #: deliberately so -- a security fact with a default is a security fact that is true
    #: whenever somebody forgets, and there are only three places in the tree that build
    #: one of these. See ``contracts/image_scan.py`` for what the answer means.
    image_scan_reviewed: bool
    estimated_cost_usd: StrictDecimal = Field(ge=0)
    maximum_runtime_hours: StrictDecimal = Field(gt=0)
    maximum_attempts: int = Field(ge=1)
    fanout_size: int = Field(default=1, ge=1)
    fanout_parallelism: int = Field(default=1, ge=1)


POLICY_VERSION_PATTERN = r"^v[1-9][0-9]*$"


class ApprovalPolicy(ContractModel):
    #: Which reviewed policy produced a decision. A decision record that named only the
    #: outcome would be uninterpretable once the thresholds moved: a later reader could not
    #: tell an approval that was routine under the rules of its day from one that would be
    #: an exception under today's. Monotonic rather than a date, because two amendments on
    #: one day are ordinary and two dates that collide are not orderable.
    policy_version: str = Field(pattern=POLICY_VERSION_PATTERN)
    thresholds: PolicyThresholds
    #: Which scan severities require a recorded exception before a digest may run. Part of
    #: the policy rather than of the build, because the answer to "may this image run" is
    #: a policy question and the enforcement point is admission. See
    #: ``contracts/image_scan.py`` for why it is not enforced at publish.
    image_scan: ImageScanPolicy
    approval_scope: ApprovalScopeValue
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
        and facts.image_scan_reviewed
        and facts.estimated_cost_usd <= thresholds.routine_maximum_cost_usd
        and facts.maximum_runtime_hours <= thresholds.routine_maximum_runtime_hours
        and facts.maximum_attempts <= thresholds.routine_maximum_attempts
        and facts.fanout_size <= thresholds.routine_maximum_fanout_size
        and facts.fanout_parallelism <= thresholds.routine_maximum_parallelism
    ):
        return ApprovalClass.ROUTINE
    return ApprovalClass.EXCEPTION
