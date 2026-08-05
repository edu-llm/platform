from decimal import Decimal
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
    AUTOMATIC_BELOW_APPROVAL_THRESHOLDS = "automatic_below_approval_thresholds"
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
    #: NOTHING RETURNS THIS ANY MORE AND IT MUST NOT BE DELETED.
    #:
    #: :func:`evaluate_authorization` stopped refusing on a mis-claimed team, for the
    #: reasons recorded there. Four decision records were written with this reason before
    #: that, one of them a committed fixture under ``fixtures/evidence/``, and the reason
    #: is parsed back out of stored JSON by ``AuthorizationReasonValue``. Removing the
    #: member would make those four records unreadable by the code that wrote them, which
    #: is the one thing an audit trail may not do.
    #:
    #: It is also still a live refusal code, spelled from here rather than typed:
    #: ``cli.preflight._check_team`` reads ``.value`` off this member for the local check
    #: that asks the same question before anything is spent. So the word a submitter meets
    #: is unchanged and only the place it is asked has moved.
    SUBMITTER_NOT_IN_CLAIMED_TEAM = "submitter_not_in_claimed_team"


GRANTING_REASONS: frozenset[AuthorizationReason] = frozenset(
    {
        AuthorizationReason.AUTOMATIC_BELOW_APPROVAL_THRESHOLDS,
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
    *,
    # The hourly rate of the profile the request names, passed through to classification
    # because RequestFacts cannot carry it. Keyword-only and required for the reason
    # classify_request states: which approver is sufficient turns on this value, so a
    # default would be a default answer to "may a team lead release a p5.48xlarge".
    hourly_rate_usd: Decimal,
) -> AuthorizationDecision:
    approval_class = classify_request(
        request, policy.thresholds, hourly_rate_usd=hourly_rate_usd
    )
    # RECORDED, NOT ENFORCED, AND THE FLAG IS NOW THE WHOLE OF WHAT HAPPENS.
    #
    # This used to refuse below when the roster recorded a group for the submitter and the
    # manifest named a different one. It was the only reason in this enum that ever denied
    # anybody, it denied four times, and all four were real researchers whose approval a
    # lead or an admin had already spent. Two of the four are twenty-six seconds apart,
    # which is one person retrying and meeting the same wall.
    #
    # WHY REMOVING IT IS SAFE, AND THE REASON IS ITS POSITION RATHER THAN ITS SUBJECT. This
    # function runs inside admission, which is downstream of the approval gate, so this
    # refusal never once prevented a submission from committing money. By the time it
    # spoke, a lead had already said yes. All it could do was waste the yes. The question
    # is still asked twice where it is free: the form's `team` dropdown offers only the
    # eight declared ids, and ``cli.preflight._check_team`` compares the roster against the
    # claim before anything is dispatched, using ``belongs_to_claimed_team`` below so there
    # is one spelling of the comparison rather than two.
    #
    # WHAT STAYS IS THE OBSERVATION. ``team_verified`` is on every decision record, it is
    # false on 79 of the 158 written so far, and it is now the only thing a mismatched
    # claim produces. Read it to find a run whose attribution nothing established;
    # ``tools/build_phase2_proof.py`` and ``tools/build_phase5_proof.py`` already print it
    # per run, and the nightly report does not surface it yet.
    #
    # ASKED OF THIS SUBMITTER, NOT OF THE FILE, and that stays true of the flag as it was
    # of the refusal. It read ``bool(inventory.team_bindings.teams)`` once, so the first
    # group anybody declared switched checking on for the whole organization at once and
    # every submitter whose own group was not yet written down came out false. Asking
    # whether this submitter's membership is recorded gives the same answer once everybody
    # is on a group and lets groups be filled in one at a time in the meantime.
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

    # BELOW THE ROSTER CHECKS, AND THAT POSITION IS THE WHOLE OF WHAT AUTO-APPROVAL DOES
    # NOT WEAKEN. The two refusals above have already run: an off-roster submitter and an
    # off-roster approver are both refused before this line, for an automatic run exactly
    # as for any other. What this returns early from is the approver question underneath --
    # who released it, and whether they were allowed to -- because for this class the
    # answer is nobody, by policy.
    #
    # Placed here rather than above the self-approval test, which is the mistake it would be
    # easy to make. An automatic run reaches admission with no approver, so the fall-through
    # below would set deciding_approver to the submitter, read it as self-approval, and
    # refuse an ordinary member with self_approval_not_permitted_for_member -- turning the
    # cheapest runs on the platform from unattended into impossible.
    #
    # The approver is normally None here because the workflow asks GitHub for one only when
    # a gate had reviewers. If a caller supplies one anyway it has already been roster-
    # checked above and is recorded as given; it grants nothing that this class did not
    # already grant.
    if approval_class is ApprovalClass.AUTOMATIC:
        return decision(AuthorizationReason.AUTOMATIC_BELOW_APPROVAL_THRESHOLDS)

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
