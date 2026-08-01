from enum import StrEnum
from typing import Annotated, Self

from pydantic import BeforeValidator, model_validator

from .base import ContractModel, parse_str_enum
from .bindings import GitHubLogin, TeamId, normalize_github_login
from .inventory import OrganizationInventory
from .policy import (
    ApprovalClass,
    ApprovalPolicy,
    ApprovalScope,
    ApprovalScopeValue,
    RequestFacts,
    classify_request,
)

__all__ = [
    "GRANTING_REASONS",
    "AuthorizationDecision",
    "AuthorizationReason",
    "evaluate_authorization",
]


class AuthorizationReason(StrEnum):
    ROUTINE_SELF_AUTHORIZED = "routine_self_authorized"
    ROUTINE_APPROVED_BY_LEAD_OR_ADMIN = "routine_approved_by_lead_or_admin"
    EXCEPTION_APPROVED_BY_ADMIN = "exception_approved_by_admin"
    EXCEPTION_SELF_APPROVED_BY_ADMIN = "exception_self_approved_by_admin"
    SUBMITTER_NOT_IN_ROSTER = "submitter_not_in_roster"
    APPROVER_NOT_IN_ROSTER = "approver_not_in_roster"
    SELF_APPROVAL_NOT_PERMITTED_FOR_MEMBER = "self_approval_not_permitted_for_member"
    APPROVER_LACKS_LEAD_OR_ADMIN_ROLE = "approver_lacks_lead_or_admin_role"
    APPROVER_LACKS_ADMIN_ROLE = "approver_lacks_admin_role"
    APPROVER_DOES_NOT_LEAD_SUBMITTER_TEAM = "approver_does_not_lead_submitter_team"
    TEAM_SCOPE_REQUIRES_TEAM_BINDINGS = "team_scope_requires_team_bindings"
    SUBMITTER_NOT_IN_CLAIMED_TEAM = "submitter_not_in_claimed_team"


GRANTING_REASONS: frozenset[AuthorizationReason] = frozenset(
    {
        AuthorizationReason.ROUTINE_SELF_AUTHORIZED,
        AuthorizationReason.ROUTINE_APPROVED_BY_LEAD_OR_ADMIN,
        AuthorizationReason.EXCEPTION_APPROVED_BY_ADMIN,
        AuthorizationReason.EXCEPTION_SELF_APPROVED_BY_ADMIN,
    }
)

ApprovalClassValue = Annotated[ApprovalClass, BeforeValidator(parse_str_enum(ApprovalClass))]
AuthorizationReasonValue = Annotated[
    AuthorizationReason, BeforeValidator(parse_str_enum(AuthorizationReason))
]


class AuthorizationDecision(ContractModel):
    submitter: GitHubLogin
    approver: GitHubLogin | None
    granted: bool
    approval_class: ApprovalClassValue
    approval_scope: ApprovalScopeValue
    claimed_team: TeamId
    team_verified: bool
    reason: AuthorizationReasonValue

    @model_validator(mode="after")
    def validate_outcome_matches_reason(self) -> Self:
        if self.granted != (self.reason in GRANTING_REASONS):
            raise ValueError("authorization outcome must match the recorded reason")
        return self


def is_organization_member(inventory: OrganizationInventory, github_login: str) -> bool:
    normalized = normalize_github_login(github_login)
    return any(member.normalized_github_login == normalized for member in inventory.members)


def holds_routine_approver_role(inventory: OrganizationInventory, github_login: str) -> bool:
    return inventory.is_admin(github_login) or inventory.is_team_lead(github_login)


def holds_exception_approver_role(inventory: OrganizationInventory, github_login: str) -> bool:
    return inventory.is_admin(github_login)


def leads_a_team_of(
    inventory: OrganizationInventory,
    *,
    approver: str,
    submitter: str,
) -> bool:
    led_team_ids = {team.team_id for team in inventory.teams_led_by(approver)}
    return any(team.team_id in led_team_ids for team in inventory.teams_for_member(submitter))


def belongs_to_claimed_team(
    inventory: OrganizationInventory,
    *,
    submitter: str,
    claimed_team: str,
) -> bool:
    return any(team.team_id == claimed_team for team in inventory.teams_for_member(submitter))


def evaluate_authorization(
    submitter: str,
    approver: str | None,
    request: RequestFacts,
    policy: ApprovalPolicy,
    inventory: OrganizationInventory,
) -> AuthorizationDecision:
    approval_class = classify_request(request, policy.thresholds)
    # ASKED OF THIS SUBMITTER, NOT OF THE FILE. This read ``bool(inventory.team_bindings
    # .teams)``, so the first group anybody declared switched checking on for the whole
    # organization at once, and every submitter whose own group was not yet written down was
    # refused with submitter_not_in_claimed_team. That included platform admins, and it made
    # declaring one group an all-or-nothing edit: safe only on the day all thirty-five
    # assignments landed together, which is the day that never comes. Asking whether this
    # submitter's membership is recorded gives the same answer once everybody is on a group,
    # and in the meantime lets groups be filled in one at a time. A submitter with no
    # recorded group is authorized exactly as before and the decision records team_verified
    # false, which is what that flag has meant since it was added.
    membership_is_knowable = bool(inventory.teams_for_member(submitter))
    team_verified = membership_is_knowable and belongs_to_claimed_team(
        inventory,
        submitter=submitter,
        claimed_team=request.claimed_team,
    )

    def decision(reason: AuthorizationReason) -> AuthorizationDecision:
        return AuthorizationDecision(
            submitter=submitter,
            approver=approver,
            granted=reason in GRANTING_REASONS,
            approval_class=approval_class,
            approval_scope=policy.approval_scope,
            claimed_team=request.claimed_team,
            team_verified=team_verified,
            reason=reason,
        )

    if not is_organization_member(inventory, submitter):
        return decision(AuthorizationReason.SUBMITTER_NOT_IN_ROSTER)
    if approver is not None and not is_organization_member(inventory, approver):
        return decision(AuthorizationReason.APPROVER_NOT_IN_ROSTER)
    if membership_is_knowable and not team_verified:
        return decision(AuthorizationReason.SUBMITTER_NOT_IN_CLAIMED_TEAM)

    deciding_approver = submitter if approver is None else approver
    self_authorized = normalize_github_login(deciding_approver) == normalize_github_login(submitter)

    if self_authorized and not holds_routine_approver_role(inventory, submitter):
        return decision(AuthorizationReason.SELF_APPROVAL_NOT_PERMITTED_FOR_MEMBER)

    if approval_class is ApprovalClass.EXCEPTION:
        if not holds_exception_approver_role(inventory, deciding_approver):
            return decision(AuthorizationReason.APPROVER_LACKS_ADMIN_ROLE)
        if self_authorized:
            return decision(AuthorizationReason.EXCEPTION_SELF_APPROVED_BY_ADMIN)
        return decision(AuthorizationReason.EXCEPTION_APPROVED_BY_ADMIN)

    if self_authorized:
        return decision(AuthorizationReason.ROUTINE_SELF_AUTHORIZED)
    if not holds_routine_approver_role(inventory, deciding_approver):
        return decision(AuthorizationReason.APPROVER_LACKS_LEAD_OR_ADMIN_ROLE)
    if policy.approval_scope is ApprovalScope.TEAM and not inventory.is_admin(deciding_approver):
        if not inventory.team_bindings.teams:
            return decision(AuthorizationReason.TEAM_SCOPE_REQUIRES_TEAM_BINDINGS)
        if not leads_a_team_of(inventory, approver=deciding_approver, submitter=submitter):
            return decision(AuthorizationReason.APPROVER_DOES_NOT_LEAD_SUBMITTER_TEAM)
    return decision(AuthorizationReason.ROUTINE_APPROVED_BY_LEAD_OR_ADMIN)
