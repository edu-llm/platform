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
    ) -> AuthorizationDecision:
        """This row's outcome, from the facts it states and the reviewed configuration.

        It took an ``hourly_rate_usd`` and does not, which is the one change v5 made to
        this model's surface. A scenario states ``RequestFacts`` and names no compute
        profile, so while classification turned on the rate of the profile a request named
        there was nothing on the row to read it from and every caller had to supply one.
        The rate ceiling is gone, so a row is now decided entirely by what it states.
        """
        return evaluate_authorization(
            self.submitter.github_login,
            None if self.approver is None else self.approver.github_login,
            self.request,
            policy,
            inventory,
        )
