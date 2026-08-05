from decimal import Decimal
from typing import Literal, Self

from pydantic import Field, model_validator

from .authorization import (
    GRANTING_REASONS,
    ApprovalClassValue,
    AuthorizationDecision,
    AuthorizationReasonValue,
    evaluate_authorization,
)
from .base import ContractModel
from .bindings import SLUG_PATTERN, GitHubLogin
from .inventory import OrganizationInventory
from .policy import ApprovalPolicy, ApprovalScopeValue, RequestFacts

__all__ = [
    "AuthorizationScenario",
    "ExpectedAuthorization",
    "ScenarioActor",
]


class ScenarioActor(ContractModel):
    github_login: GitHubLogin
    admin: bool
    team_lead: bool

    def matches_roster(self, inventory: OrganizationInventory) -> bool:
        return (
            inventory.is_admin(self.github_login) == self.admin
            and inventory.is_team_lead(self.github_login) == self.team_lead
        )


class ExpectedAuthorization(ContractModel):
    granted: bool
    approval_class: ApprovalClassValue
    approval_scope: ApprovalScopeValue
    reason: AuthorizationReasonValue

    @model_validator(mode="after")
    def validate_expectation_matches_reason(self) -> Self:
        if self.granted != (self.reason in GRANTING_REASONS):
            raise ValueError("expected authorization outcome must match the expected reason")
        return self

    def matches(self, decision: AuthorizationDecision) -> bool:
        return (
            decision.granted == self.granted
            and decision.approval_class is self.approval_class
            and decision.approval_scope is self.approval_scope
            and decision.reason is self.reason
        )


class AuthorizationScenario(ContractModel):
    schema_version: Literal[1]
    scenario: str = Field(min_length=1, pattern=SLUG_PATTERN)
    submitter: ScenarioActor
    approver: ScenarioActor | None
    request: RequestFacts
    expected: ExpectedAuthorization

    def decide(
        self,
        policy: ApprovalPolicy,
        inventory: OrganizationInventory,
        *,
        # A scenario states RequestFacts and no compute profile, so the rate classification
        # now also turns on has to be supplied by whoever evaluates the row. It is a
        # parameter rather than a field on this model because a field would change the
        # model's structural digest, which fixtures/goldens/contract-models.json records.
        #
        # Every shipped scenario is about the roster and the approver, not about price, so
        # callers pass a rate below the ceiling and the rows keep classifying on the four
        # request bounds as they always did.
        hourly_rate_usd: Decimal,
    ) -> AuthorizationDecision:
        return evaluate_authorization(
            self.submitter.github_login,
            None if self.approver is None else self.approver.github_login,
            self.request,
            policy,
            inventory,
            hourly_rate_usd=hourly_rate_usd,
        )
