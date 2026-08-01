import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from edullm_platform.canonical import canonical_json_bytes, sha256_digest
from edullm_platform.config import load_yaml
from edullm_platform.contracts.authorization import (
    GRANTING_REASONS,
    AuthorizationDecision,
    AuthorizationReason,
    evaluate_authorization,
)
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.policy import (
    ApprovalClass,
    ApprovalPolicy,
    ApprovalScope,
    RequestFacts,
    classify_request,
)
from edullm_platform.contracts.repository_registry import RepositoryRegistry
from edullm_platform.manifest_helpers import compute_manifest_maximum_cost
from edullm_platform.phase0_gate import (
    expected_manifest_classification,
    request_facts_from_manifest,
)
from tests.test_manifest import (
    REPRESENTATIVE_MANIFEST_FILENAMES,
    load_representative_manifest,
    load_workload_catalog,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

ADMIN_AND_LEAD = "philote-dev"
ADMIN_WITHOUT_LEAD = "BritishAmericqn"
LEAD_WITHOUT_ADMIN = "ericrcwu001"
OTHER_LEAD_WITHOUT_ADMIN = "hiyasvyas"
PLAIN_MEMBER = "caiiris"
OTHER_PLAIN_MEMBER = "nzhao721"
UNKNOWN_LOGIN = "not-a-member"

MEMORY_SPLIT_LEAD = "ericrcwu001"
MEMORY_SPLIT_MEMBER = "caiiris"
CURRICULUM_LEAD = "meric233"
CURRICULUM_MEMBER = "nzhao721"
TEAMLESS_ADMIN = "philote-dev"

MEMORY_SPLIT_TEAM = "memory-split"
CURRICULUM_TEAM = "curriculum"
UNBOUND_TEAM = "not-a-team"


def load_organization_inventory() -> OrganizationInventory:
    return load_yaml(PROJECT_ROOT / "config" / "organization.yaml", OrganizationInventory)


def load_approval_policy() -> ApprovalPolicy:
    return load_yaml(PROJECT_ROOT / "config" / "policy.yaml", ApprovalPolicy)


def load_dataset_registry() -> DatasetRegistry:
    return load_yaml(PROJECT_ROOT / "config" / "datasets.yaml", DatasetRegistry)


def load_repository_registry() -> RepositoryRegistry:
    return load_yaml(PROJECT_ROOT / "config" / "repositories.yaml", RepositoryRegistry)


def approval_policy_payload(approval_scope: ApprovalScope) -> dict[str, object]:
    payload: dict[str, object] = load_approval_policy().model_dump(mode="json")
    payload["approval_scope"] = approval_scope.value
    return payload


def approval_policy_with_scope(approval_scope: ApprovalScope) -> ApprovalPolicy:
    return ApprovalPolicy.model_validate(approval_policy_payload(approval_scope))


def request_facts_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "claimed_team": MEMORY_SPLIT_TEAM,
        "repository_registered": True,
        "dataset_registered": True,
        "compute_profile_registered": True,
        "immutable_revision": True,
        "immutable_image": True,
        "image_scan_reviewed": True,
        "estimated_cost_usd": "100",
        "maximum_runtime_hours": "6",
        "maximum_attempts": 2,
    }
    payload.update(overrides)
    return payload


def routine_facts(**overrides: object) -> RequestFacts:
    return RequestFacts.model_validate(request_facts_payload(**overrides))


def exception_facts(**overrides: object) -> RequestFacts:
    return routine_facts(**{"estimated_cost_usd": "5000", **overrides})


def two_team_inventory_payload(
    *,
    memory_split_members: list[str] | None = None,
) -> dict[str, object]:
    return {
        "admins": [TEAMLESS_ADMIN],
        "team_leads": [MEMORY_SPLIT_LEAD, CURRICULUM_LEAD],
        "members": [
            {"github_login": TEAMLESS_ADMIN},
            {"github_login": MEMORY_SPLIT_LEAD},
            {"github_login": MEMORY_SPLIT_MEMBER},
            {"github_login": CURRICULUM_LEAD},
            {"github_login": CURRICULUM_MEMBER},
        ],
        "pilot_repositories": ["OLMo-core"],
        "team_bindings": {
            "teams": [
                {
                    "team_id": MEMORY_SPLIT_TEAM,
                    "github_team_slug": "memory-split",
                    "lead_logins": [MEMORY_SPLIT_LEAD],
                    "member_logins": (
                        [MEMORY_SPLIT_MEMBER]
                        if memory_split_members is None
                        else memory_split_members
                    ),
                    "s3_namespace": "sbsandbox-intern-memory-split",
                    "wandb_entity": "edu-llm-memory-split",
                },
                {
                    "team_id": CURRICULUM_TEAM,
                    "github_team_slug": "curriculum",
                    "lead_logins": [CURRICULUM_LEAD],
                    "member_logins": [CURRICULUM_MEMBER],
                    "s3_namespace": "sbsandbox-intern-curriculum",
                    "wandb_entity": "edu-llm-curriculum",
                },
            ]
        },
    }


def two_team_inventory() -> OrganizationInventory:
    return OrganizationInventory.model_validate(two_team_inventory_payload())


def inventory_with_no_team_bindings() -> OrganizationInventory:
    """The shipped roster with its team catalog removed, which it no longer ships without.

    ``config/organization.yaml`` declared no teams at all until 2026-08-01, so the shipped
    roster was itself the empty-catalog case and three tests read it that way. It now
    declares six, and the empty catalog has to be constructed to stay reachable. It is worth
    keeping reachable: ``team_scope_requires_team_bindings`` is the reason a team-scoped
    policy gives when there is nothing to scope against, and a reason no test can produce is
    one nobody would notice going wrong.
    """
    payload: dict[str, object] = load_organization_inventory().model_dump(mode="json")
    payload["team_bindings"] = {"teams": [], "repositories": []}
    return OrganizationInventory.model_validate(payload)


def inventory_where_the_admin_belongs_to_memory_split() -> OrganizationInventory:
    return OrganizationInventory.model_validate(
        two_team_inventory_payload(
            memory_split_members=[MEMORY_SPLIT_MEMBER, TEAMLESS_ADMIN],
        )
    )


def decide(
    submitter: str,
    approver: str | None,
    request: RequestFacts,
    *,
    policy: ApprovalPolicy | None = None,
    inventory: OrganizationInventory | None = None,
) -> AuthorizationDecision:
    return evaluate_authorization(
        submitter,
        approver,
        request,
        policy if policy is not None else load_approval_policy(),
        inventory if inventory is not None else load_organization_inventory(),
    )


def test_real_roster_supplies_the_actor_matrix_this_module_assumes() -> None:
    inventory = load_organization_inventory()
    assert inventory.is_admin(ADMIN_AND_LEAD) is True
    assert inventory.is_team_lead(ADMIN_AND_LEAD) is True
    assert inventory.is_admin(ADMIN_WITHOUT_LEAD) is True
    assert inventory.is_team_lead(ADMIN_WITHOUT_LEAD) is False
    assert inventory.is_admin(LEAD_WITHOUT_ADMIN) is False
    assert inventory.is_team_lead(LEAD_WITHOUT_ADMIN) is True
    for member in (PLAIN_MEMBER, OTHER_PLAIN_MEMBER):
        assert inventory.is_admin(member) is False
        assert inventory.is_team_lead(member) is False


def test_fixture_requests_classify_as_routine_and_exception() -> None:
    thresholds = load_approval_policy().thresholds
    assert classify_request(routine_facts(), thresholds) is ApprovalClass.ROUTINE
    assert classify_request(exception_facts(), thresholds) is ApprovalClass.EXCEPTION


@pytest.mark.parametrize(
    ("submitter", "approver", "granted", "reason"),
    [
        (ADMIN_AND_LEAD, None, True, AuthorizationReason.ROUTINE_SELF_AUTHORIZED),
        (ADMIN_AND_LEAD, ADMIN_AND_LEAD, True, AuthorizationReason.ROUTINE_SELF_AUTHORIZED),
        (ADMIN_WITHOUT_LEAD, None, True, AuthorizationReason.ROUTINE_SELF_AUTHORIZED),
        (LEAD_WITHOUT_ADMIN, None, True, AuthorizationReason.ROUTINE_SELF_AUTHORIZED),
        (
            LEAD_WITHOUT_ADMIN,
            LEAD_WITHOUT_ADMIN,
            True,
            AuthorizationReason.ROUTINE_SELF_AUTHORIZED,
        ),
        (
            PLAIN_MEMBER,
            None,
            False,
            AuthorizationReason.SELF_APPROVAL_NOT_PERMITTED_FOR_MEMBER,
        ),
        (
            PLAIN_MEMBER,
            PLAIN_MEMBER,
            False,
            AuthorizationReason.SELF_APPROVAL_NOT_PERMITTED_FOR_MEMBER,
        ),
        (
            PLAIN_MEMBER,
            LEAD_WITHOUT_ADMIN,
            True,
            AuthorizationReason.ROUTINE_APPROVED_BY_LEAD_OR_ADMIN,
        ),
        (
            PLAIN_MEMBER,
            OTHER_LEAD_WITHOUT_ADMIN,
            True,
            AuthorizationReason.ROUTINE_APPROVED_BY_LEAD_OR_ADMIN,
        ),
        (
            PLAIN_MEMBER,
            ADMIN_WITHOUT_LEAD,
            True,
            AuthorizationReason.ROUTINE_APPROVED_BY_LEAD_OR_ADMIN,
        ),
        (
            PLAIN_MEMBER,
            ADMIN_AND_LEAD,
            True,
            AuthorizationReason.ROUTINE_APPROVED_BY_LEAD_OR_ADMIN,
        ),
        (
            PLAIN_MEMBER,
            OTHER_PLAIN_MEMBER,
            False,
            AuthorizationReason.APPROVER_LACKS_LEAD_OR_ADMIN_ROLE,
        ),
        (
            LEAD_WITHOUT_ADMIN,
            PLAIN_MEMBER,
            False,
            AuthorizationReason.APPROVER_LACKS_LEAD_OR_ADMIN_ROLE,
        ),
        (
            LEAD_WITHOUT_ADMIN,
            ADMIN_WITHOUT_LEAD,
            True,
            AuthorizationReason.ROUTINE_APPROVED_BY_LEAD_OR_ADMIN,
        ),
        (UNKNOWN_LOGIN, LEAD_WITHOUT_ADMIN, False, AuthorizationReason.SUBMITTER_NOT_IN_ROSTER),
        (UNKNOWN_LOGIN, None, False, AuthorizationReason.SUBMITTER_NOT_IN_ROSTER),
        (UNKNOWN_LOGIN, UNKNOWN_LOGIN, False, AuthorizationReason.SUBMITTER_NOT_IN_ROSTER),
        (PLAIN_MEMBER, UNKNOWN_LOGIN, False, AuthorizationReason.APPROVER_NOT_IN_ROSTER),
        (ADMIN_AND_LEAD, UNKNOWN_LOGIN, False, AuthorizationReason.APPROVER_NOT_IN_ROSTER),
    ],
)
def test_routine_actor_matrix_under_organization_scope(
    submitter: str,
    approver: str | None,
    granted: bool,
    reason: AuthorizationReason,
) -> None:
    decision = decide(submitter, approver, routine_facts())
    assert decision.reason is reason
    assert decision.granted is granted
    assert decision.approval_class is ApprovalClass.ROUTINE
    assert decision.approval_scope is ApprovalScope.ORGANIZATION


@pytest.mark.parametrize(
    ("submitter", "approver", "granted", "reason"),
    [
        (
            ADMIN_AND_LEAD,
            None,
            True,
            AuthorizationReason.EXCEPTION_SELF_APPROVED_BY_ADMIN,
        ),
        (
            ADMIN_AND_LEAD,
            ADMIN_AND_LEAD,
            True,
            AuthorizationReason.EXCEPTION_SELF_APPROVED_BY_ADMIN,
        ),
        (
            ADMIN_WITHOUT_LEAD,
            None,
            True,
            AuthorizationReason.EXCEPTION_SELF_APPROVED_BY_ADMIN,
        ),
        (PLAIN_MEMBER, ADMIN_AND_LEAD, True, AuthorizationReason.EXCEPTION_APPROVED_BY_ADMIN),
        (PLAIN_MEMBER, ADMIN_WITHOUT_LEAD, True, AuthorizationReason.EXCEPTION_APPROVED_BY_ADMIN),
        (
            LEAD_WITHOUT_ADMIN,
            ADMIN_WITHOUT_LEAD,
            True,
            AuthorizationReason.EXCEPTION_APPROVED_BY_ADMIN,
        ),
        (
            ADMIN_WITHOUT_LEAD,
            ADMIN_AND_LEAD,
            True,
            AuthorizationReason.EXCEPTION_APPROVED_BY_ADMIN,
        ),
        (PLAIN_MEMBER, LEAD_WITHOUT_ADMIN, False, AuthorizationReason.APPROVER_LACKS_ADMIN_ROLE),
        (PLAIN_MEMBER, OTHER_PLAIN_MEMBER, False, AuthorizationReason.APPROVER_LACKS_ADMIN_ROLE),
        (LEAD_WITHOUT_ADMIN, None, False, AuthorizationReason.APPROVER_LACKS_ADMIN_ROLE),
        (
            LEAD_WITHOUT_ADMIN,
            LEAD_WITHOUT_ADMIN,
            False,
            AuthorizationReason.APPROVER_LACKS_ADMIN_ROLE,
        ),
        (
            LEAD_WITHOUT_ADMIN,
            OTHER_LEAD_WITHOUT_ADMIN,
            False,
            AuthorizationReason.APPROVER_LACKS_ADMIN_ROLE,
        ),
        (
            PLAIN_MEMBER,
            None,
            False,
            AuthorizationReason.SELF_APPROVAL_NOT_PERMITTED_FOR_MEMBER,
        ),
        (
            PLAIN_MEMBER,
            PLAIN_MEMBER,
            False,
            AuthorizationReason.SELF_APPROVAL_NOT_PERMITTED_FOR_MEMBER,
        ),
        (UNKNOWN_LOGIN, ADMIN_AND_LEAD, False, AuthorizationReason.SUBMITTER_NOT_IN_ROSTER),
        (ADMIN_AND_LEAD, UNKNOWN_LOGIN, False, AuthorizationReason.APPROVER_NOT_IN_ROSTER),
    ],
)
def test_exception_actor_matrix_under_organization_scope(
    submitter: str,
    approver: str | None,
    granted: bool,
    reason: AuthorizationReason,
) -> None:
    decision = decide(submitter, approver, exception_facts())
    assert decision.reason is reason
    assert decision.granted is granted
    assert decision.approval_class is ApprovalClass.EXCEPTION


def test_lead_self_authorizes_a_routine_run() -> None:
    decision = decide(LEAD_WITHOUT_ADMIN, None, routine_facts())
    assert decision.granted is True
    assert decision.reason is AuthorizationReason.ROUTINE_SELF_AUTHORIZED
    assert decision.submitter == LEAD_WITHOUT_ADMIN
    assert decision.approver is None


def test_plain_member_self_authorizing_a_routine_run_is_denied() -> None:
    decision = decide(PLAIN_MEMBER, None, routine_facts())
    assert decision.granted is False
    assert decision.reason is AuthorizationReason.SELF_APPROVAL_NOT_PERMITTED_FOR_MEMBER


def test_plain_member_routine_run_is_granted_by_any_lead_under_organization_scope() -> None:
    inventory = load_organization_inventory()
    for lead in inventory.team_leads:
        decision = decide(PLAIN_MEMBER, lead, routine_facts(), inventory=inventory)
        assert decision.granted is True, lead
        assert decision.reason is AuthorizationReason.ROUTINE_APPROVED_BY_LEAD_OR_ADMIN


def test_plain_member_routine_run_approved_by_another_plain_member_is_denied() -> None:
    decision = decide(PLAIN_MEMBER, OTHER_PLAIN_MEMBER, routine_facts())
    assert decision.granted is False
    assert decision.reason is AuthorizationReason.APPROVER_LACKS_LEAD_OR_ADMIN_ROLE


def test_lead_may_not_approve_an_exception() -> None:
    decision = decide(PLAIN_MEMBER, LEAD_WITHOUT_ADMIN, exception_facts())
    assert decision.granted is False
    assert decision.reason is AuthorizationReason.APPROVER_LACKS_ADMIN_ROLE


def test_admin_approves_someone_elses_exception() -> None:
    decision = decide(PLAIN_MEMBER, ADMIN_WITHOUT_LEAD, exception_facts())
    assert decision.granted is True
    assert decision.reason is AuthorizationReason.EXCEPTION_APPROVED_BY_ADMIN


@pytest.mark.parametrize("approver", [None, ADMIN_AND_LEAD])
def test_admin_may_approve_their_own_exception_as_an_accepted_risk(approver: str | None) -> None:
    decision = decide(ADMIN_AND_LEAD, approver, exception_facts())
    assert decision.granted is True
    assert decision.reason is AuthorizationReason.EXCEPTION_SELF_APPROVED_BY_ADMIN
    assert decision.approval_class is ApprovalClass.EXCEPTION
    assert AuthorizationReason.EXCEPTION_SELF_APPROVED_BY_ADMIN in GRANTING_REASONS


def test_admin_without_a_team_lead_role_may_approve_a_member_routine_run() -> None:
    inventory = load_organization_inventory()
    assert inventory.is_team_lead(ADMIN_WITHOUT_LEAD) is False
    decision = decide(PLAIN_MEMBER, ADMIN_WITHOUT_LEAD, routine_facts(), inventory=inventory)
    assert decision.granted is True
    assert decision.reason is AuthorizationReason.ROUTINE_APPROVED_BY_LEAD_OR_ADMIN


def test_unknown_submitter_and_unknown_approver_are_denied_for_distinct_reasons() -> None:
    unknown_submitter = decide(UNKNOWN_LOGIN, ADMIN_AND_LEAD, routine_facts())
    unknown_approver = decide(PLAIN_MEMBER, UNKNOWN_LOGIN, routine_facts())
    assert unknown_submitter.granted is False
    assert unknown_approver.granted is False
    assert unknown_submitter.reason is AuthorizationReason.SUBMITTER_NOT_IN_ROSTER
    assert unknown_approver.reason is AuthorizationReason.APPROVER_NOT_IN_ROSTER
    assert unknown_submitter.reason is not unknown_approver.reason


@pytest.mark.parametrize(
    "spelling",
    ["BritishAmericqn", "britishamericqn", "BRITISHAMERICQN", "bRiTiShAmEriCqn"],
)
def test_case_variants_of_an_admin_login_resolve_to_the_same_person(spelling: str) -> None:
    approved = decide(PLAIN_MEMBER, spelling, exception_facts())
    assert approved.granted is True
    assert approved.reason is AuthorizationReason.EXCEPTION_APPROVED_BY_ADMIN
    self_authorized = decide(spelling, None, routine_facts())
    assert self_authorized.granted is True
    assert self_authorized.reason is AuthorizationReason.ROUTINE_SELF_AUTHORIZED


@pytest.mark.parametrize(
    ("submitter", "approver"),
    [
        (PLAIN_MEMBER, PLAIN_MEMBER.upper()),
        (PLAIN_MEMBER.upper(), PLAIN_MEMBER),
        ("CaIiRiS", "caIIris"),
    ],
)
def test_case_variants_of_a_member_login_are_recognized_as_self_approval(
    submitter: str,
    approver: str,
) -> None:
    decision = decide(submitter, approver, routine_facts())
    assert decision.granted is False
    assert decision.reason is AuthorizationReason.SELF_APPROVAL_NOT_PERMITTED_FOR_MEMBER


@pytest.mark.parametrize(
    ("submitter", "approver"),
    [
        (ADMIN_WITHOUT_LEAD.lower(), ADMIN_WITHOUT_LEAD.upper()),
        (ADMIN_WITHOUT_LEAD.upper(), ADMIN_WITHOUT_LEAD),
    ],
)
def test_case_variants_of_an_admin_login_are_recognized_as_self_approval(
    submitter: str,
    approver: str,
) -> None:
    decision = decide(submitter, approver, exception_facts())
    assert decision.granted is True
    assert decision.reason is AuthorizationReason.EXCEPTION_SELF_APPROVED_BY_ADMIN


def test_case_variants_of_a_lead_login_are_recognized_as_self_authorization() -> None:
    decision = decide(LEAD_WITHOUT_ADMIN.upper(), LEAD_WITHOUT_ADMIN, routine_facts())
    assert decision.granted is True
    assert decision.reason is AuthorizationReason.ROUTINE_SELF_AUTHORIZED


def test_decision_preserves_authored_login_casing() -> None:
    decision = decide("CaIiRiS", "bRiTiShAmEriCqn", exception_facts())
    assert decision.granted is True
    assert decision.submitter == "CaIiRiS"
    assert decision.approver == "bRiTiShAmEriCqn"
    assert '"submitter":"CaIiRiS"' in canonical_json_bytes(decision).decode("utf-8")


def test_flipping_approval_scope_alone_turns_a_grant_into_a_denial() -> None:
    organization_payload = approval_policy_payload(ApprovalScope.ORGANIZATION)
    team_payload = approval_policy_payload(ApprovalScope.TEAM)
    assert {
        key for key in organization_payload if organization_payload[key] != team_payload[key]
    } == {"approval_scope"}

    inventory = two_team_inventory()
    request = routine_facts()
    under_organization_scope = evaluate_authorization(
        MEMORY_SPLIT_MEMBER,
        CURRICULUM_LEAD,
        request,
        ApprovalPolicy.model_validate(organization_payload),
        inventory,
    )
    under_team_scope = evaluate_authorization(
        MEMORY_SPLIT_MEMBER,
        CURRICULUM_LEAD,
        request,
        ApprovalPolicy.model_validate(team_payload),
        inventory,
    )

    assert under_organization_scope.granted is True
    assert under_organization_scope.reason is AuthorizationReason.ROUTINE_APPROVED_BY_LEAD_OR_ADMIN
    assert under_organization_scope.approval_scope is ApprovalScope.ORGANIZATION
    assert under_team_scope.granted is False
    assert under_team_scope.reason is AuthorizationReason.APPROVER_DOES_NOT_LEAD_SUBMITTER_TEAM
    assert under_team_scope.approval_scope is ApprovalScope.TEAM


def test_team_scope_grants_when_the_approver_leads_the_submitters_team() -> None:
    decision = decide(
        MEMORY_SPLIT_MEMBER,
        MEMORY_SPLIT_LEAD,
        routine_facts(),
        policy=approval_policy_with_scope(ApprovalScope.TEAM),
        inventory=two_team_inventory(),
    )
    assert decision.granted is True
    assert decision.reason is AuthorizationReason.ROUTINE_APPROVED_BY_LEAD_OR_ADMIN


def test_team_scope_leaves_lead_self_authorization_untouched() -> None:
    decision = decide(
        CURRICULUM_LEAD,
        None,
        routine_facts(claimed_team=CURRICULUM_TEAM),
        policy=approval_policy_with_scope(ApprovalScope.TEAM),
        inventory=two_team_inventory(),
    )
    assert decision.granted is True
    assert decision.reason is AuthorizationReason.ROUTINE_SELF_AUTHORIZED


def test_team_scope_bounds_lead_authority_but_not_admin_authority() -> None:
    inventory = two_team_inventory()
    assert inventory.teams_led_by(TEAMLESS_ADMIN) == ()
    team_scoped = approval_policy_with_scope(ApprovalScope.TEAM)
    by_admin = decide(
        MEMORY_SPLIT_MEMBER, TEAMLESS_ADMIN, routine_facts(), policy=team_scoped, inventory=inventory
    )
    by_other_lead = decide(
        MEMORY_SPLIT_MEMBER, CURRICULUM_LEAD, routine_facts(), policy=team_scoped, inventory=inventory
    )
    assert by_admin.granted is True
    assert by_other_lead.granted is False
    assert by_other_lead.reason is AuthorizationReason.APPROVER_DOES_NOT_LEAD_SUBMITTER_TEAM


def test_team_scope_does_not_restrict_exception_approval() -> None:
    decision = decide(
        MEMORY_SPLIT_MEMBER,
        TEAMLESS_ADMIN,
        exception_facts(),
        policy=approval_policy_with_scope(ApprovalScope.TEAM),
        inventory=two_team_inventory(),
    )
    assert decision.granted is True
    assert decision.reason is AuthorizationReason.EXCEPTION_APPROVED_BY_ADMIN


def test_team_scope_with_empty_team_bindings_denies_member_routine_runs_without_raising() -> None:
    decision = decide(
        PLAIN_MEMBER,
        LEAD_WITHOUT_ADMIN,
        routine_facts(),
        policy=approval_policy_with_scope(ApprovalScope.TEAM),
        inventory=inventory_with_no_team_bindings(),
    )
    assert decision.granted is False
    assert decision.reason is AuthorizationReason.TEAM_SCOPE_REQUIRES_TEAM_BINDINGS


def test_team_scope_with_empty_team_bindings_still_allows_lead_self_authorization() -> None:
    decision = decide(
        LEAD_WITHOUT_ADMIN,
        None,
        routine_facts(),
        policy=approval_policy_with_scope(ApprovalScope.TEAM),
    )
    assert decision.granted is True
    assert decision.reason is AuthorizationReason.ROUTINE_SELF_AUTHORIZED


def test_decision_records_the_scope_in_force() -> None:
    for scope in ApprovalScope:
        decision = decide(
            ADMIN_AND_LEAD,
            None,
            routine_facts(),
            policy=approval_policy_with_scope(scope),
        )
        assert decision.approval_scope is scope


def test_authorization_reason_values_are_stable_machine_readable_codes() -> None:
    assert {reason.value for reason in AuthorizationReason} == {
        "routine_self_authorized",
        "routine_approved_by_lead_or_admin",
        "exception_approved_by_admin",
        "exception_self_approved_by_admin",
        "submitter_not_in_roster",
        "approver_not_in_roster",
        "self_approval_not_permitted_for_member",
        "approver_lacks_lead_or_admin_role",
        "approver_lacks_admin_role",
        "approver_does_not_lead_submitter_team",
        "team_scope_requires_team_bindings",
        "submitter_not_in_claimed_team",
    }


def test_granting_reasons_partition_the_reason_vocabulary() -> None:
    assert GRANTING_REASONS == frozenset(
        {
            AuthorizationReason.ROUTINE_SELF_AUTHORIZED,
            AuthorizationReason.ROUTINE_APPROVED_BY_LEAD_OR_ADMIN,
            AuthorizationReason.EXCEPTION_APPROVED_BY_ADMIN,
            AuthorizationReason.EXCEPTION_SELF_APPROVED_BY_ADMIN,
        }
    )
    assert GRANTING_REASONS < frozenset(AuthorizationReason)


def decision_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "submitter": PLAIN_MEMBER,
        "approver": ADMIN_WITHOUT_LEAD,
        "granted": True,
        "approval_class": "exception",
        "approval_scope": "organization",
        "claimed_team": MEMORY_SPLIT_TEAM,
        "team_verified": False,
        "reason": "exception_approved_by_admin",
    }
    payload.update(overrides)
    return payload


def test_decision_accepts_enum_names_as_plain_strings_from_json() -> None:
    decision = AuthorizationDecision.model_validate(decision_payload())
    assert decision.approval_class is ApprovalClass.EXCEPTION
    assert decision.approval_scope is ApprovalScope.ORGANIZATION
    assert decision.reason is AuthorizationReason.EXCEPTION_APPROVED_BY_ADMIN


def test_decision_rejects_an_outcome_that_contradicts_its_reason() -> None:
    with pytest.raises(ValidationError) as exc_info:
        AuthorizationDecision.model_validate(decision_payload(granted=False))
    assert any(
        "authorization outcome must match the recorded reason" in item["msg"]
        for item in exc_info.value.errors()
    )


def test_decision_forbids_unknown_fields_and_is_frozen() -> None:
    decision = AuthorizationDecision.model_validate(decision_payload())
    with pytest.raises(ValidationError):
        AuthorizationDecision.model_validate(decision_payload(submitted_at="2026-07-25"))
    with pytest.raises(ValidationError):
        decision.__setattr__("granted", False)


def test_decision_rejects_a_login_that_is_not_a_github_login() -> None:
    with pytest.raises(ValidationError):
        AuthorizationDecision.model_validate(decision_payload(submitter="not a login"))


@pytest.mark.parametrize(
    ("submitter", "approver", "request_facts"),
    [
        (ADMIN_AND_LEAD, None, "routine"),
        (PLAIN_MEMBER, ADMIN_WITHOUT_LEAD, "exception"),
        (PLAIN_MEMBER, OTHER_PLAIN_MEMBER, "routine"),
    ],
)
def test_decision_round_trips_through_canonical_json_with_a_stable_digest(
    submitter: str,
    approver: str | None,
    request_facts: str,
) -> None:
    facts = routine_facts() if request_facts == "routine" else exception_facts()
    decision = decide(submitter, approver, facts)
    encoded = canonical_json_bytes(decision)
    payload = json.loads(encoded)

    assert list(payload.keys()) == sorted(payload.keys())
    restored = AuthorizationDecision.model_validate(payload)
    assert restored == decision
    assert canonical_json_bytes(restored) == encoded
    assert sha256_digest(restored) == sha256_digest(decision)
    assert sha256_digest(decision) == f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def test_canonical_json_records_a_missing_approver_as_null() -> None:
    decision = decide(ADMIN_AND_LEAD, None, routine_facts())
    assert b'"approver":null' in canonical_json_bytes(decision)


def test_decisions_differing_only_in_outcome_have_different_digests() -> None:
    granted = decide(PLAIN_MEMBER, ADMIN_WITHOUT_LEAD, exception_facts())
    denied = decide(PLAIN_MEMBER, LEAD_WITHOUT_ADMIN, exception_facts())
    assert granted.granted is True
    assert denied.granted is False
    assert sha256_digest(granted) != sha256_digest(denied)


def test_evaluate_authorization_does_not_mutate_its_inputs() -> None:
    inventory = load_organization_inventory()
    policy = load_approval_policy()
    request = routine_facts()
    before = (sha256_digest(inventory), sha256_digest(policy), sha256_digest(request))
    decide(PLAIN_MEMBER, ADMIN_AND_LEAD, request, policy=policy, inventory=inventory)
    assert (sha256_digest(inventory), sha256_digest(policy), sha256_digest(request)) == before


def test_team_scope_does_not_restrict_an_admin_to_teams_they_lead() -> None:
    inventory = two_team_inventory()
    assert inventory.is_admin(TEAMLESS_ADMIN)
    assert inventory.teams_led_by(TEAMLESS_ADMIN) == ()
    decision = decide(
        MEMORY_SPLIT_MEMBER,
        TEAMLESS_ADMIN,
        routine_facts(),
        policy=approval_policy_with_scope(ApprovalScope.TEAM),
        inventory=inventory,
    )
    assert decision.granted is True, (
        "team scope bounds lead authority, not admin authority; an admin held to team "
        "locality on a routine run while approving any team's exception is inverted privilege"
    )
    assert decision.reason is AuthorizationReason.ROUTINE_APPROVED_BY_LEAD_OR_ADMIN


def test_admin_authority_is_never_narrower_for_routine_than_for_exception() -> None:
    inventory = two_team_inventory()
    team_scoped = approval_policy_with_scope(ApprovalScope.TEAM)
    for submitter, team in (
        (MEMORY_SPLIT_MEMBER, MEMORY_SPLIT_TEAM),
        (CURRICULUM_MEMBER, CURRICULUM_TEAM),
    ):
        exception = decide(
            submitter,
            TEAMLESS_ADMIN,
            exception_facts(claimed_team=team),
            policy=team_scoped,
            inventory=inventory,
        )
        routine = decide(
            submitter,
            TEAMLESS_ADMIN,
            routine_facts(claimed_team=team),
            policy=team_scoped,
            inventory=inventory,
        )
        assert exception.granted is True
        assert routine.granted is True, (
            f"an admin may approve {submitter}'s exception but not their cheaper routine run"
        )


def test_team_scope_reports_absent_bindings_distinctly_from_a_team_mismatch() -> None:
    absent = decide(
        PLAIN_MEMBER,
        LEAD_WITHOUT_ADMIN,
        routine_facts(),
        policy=approval_policy_with_scope(ApprovalScope.TEAM),
        inventory=inventory_with_no_team_bindings(),
    )
    mismatch = decide(
        MEMORY_SPLIT_MEMBER,
        CURRICULUM_LEAD,
        routine_facts(),
        policy=approval_policy_with_scope(ApprovalScope.TEAM),
        inventory=two_team_inventory(),
    )
    assert absent.granted is False
    assert mismatch.granted is False
    assert absent.reason is AuthorizationReason.TEAM_SCOPE_REQUIRES_TEAM_BINDINGS
    assert mismatch.reason is AuthorizationReason.APPROVER_DOES_NOT_LEAD_SUBMITTER_TEAM


@pytest.mark.parametrize("claimed_team", [MEMORY_SPLIT_TEAM, CURRICULUM_TEAM, UNBOUND_TEAM])
def test_attribution_is_recorded_unverified_while_no_member_is_bound_to_a_team(
    claimed_team: str,
) -> None:
    """Mutation: read membership off the catalog existing rather than off the submitter.

    The shipped roster declares six teams and puts nobody in any of them, because which
    group a person belongs to is the one fact it has never held. A claim is therefore still
    unverifiable, including ``not-a-team``, which is not one of the six: refusing that one
    would be checking the claim against the catalog rather than against the submitter, and
    would deny a run for naming a group its author is not on record as being off.
    """
    inventory = load_organization_inventory()
    assert inventory.team_bindings.teams != (), "the shipped roster declares its groups"
    assert all(team.member_logins == () for team in inventory.team_bindings.teams)
    decision = decide(
        PLAIN_MEMBER,
        LEAD_WITHOUT_ADMIN,
        routine_facts(claimed_team=claimed_team),
        inventory=inventory,
    )
    assert decision.granted is True
    assert decision.reason is AuthorizationReason.ROUTINE_APPROVED_BY_LEAD_OR_ADMIN
    assert decision.claimed_team == claimed_team
    assert decision.team_verified is False, (
        "with no member bound to a team the claimed team cannot be checked; the audit record "
        "must say so rather than imply the attribution was confirmed"
    )


def test_a_submitter_naming_their_own_team_is_granted_and_recorded_verified() -> None:
    inventory = two_team_inventory()
    decision = decide(
        MEMORY_SPLIT_MEMBER,
        MEMORY_SPLIT_LEAD,
        routine_facts(claimed_team=MEMORY_SPLIT_TEAM),
        inventory=inventory,
    )
    assert decision.granted is True
    assert decision.reason is AuthorizationReason.ROUTINE_APPROVED_BY_LEAD_OR_ADMIN
    assert decision.claimed_team == MEMORY_SPLIT_TEAM
    assert decision.team_verified is True


def test_a_submitter_naming_another_teams_id_is_denied_despite_a_valid_lead_approval() -> None:
    inventory = two_team_inventory()
    assert inventory.is_team_lead(MEMORY_SPLIT_LEAD) is True
    decision = decide(
        MEMORY_SPLIT_MEMBER,
        MEMORY_SPLIT_LEAD,
        routine_facts(claimed_team=CURRICULUM_TEAM),
        inventory=inventory,
    )
    assert decision.approval_class is ApprovalClass.ROUTINE
    assert decision.granted is False
    assert decision.reason is AuthorizationReason.SUBMITTER_NOT_IN_CLAIMED_TEAM
    assert decision.claimed_team == CURRICULUM_TEAM
    assert decision.team_verified is False
    assert AuthorizationReason.SUBMITTER_NOT_IN_CLAIMED_TEAM not in GRANTING_REASONS


def test_a_team_id_no_roster_defines_is_denied_the_same_way_as_a_foreign_team() -> None:
    inventory = two_team_inventory()
    assert UNBOUND_TEAM not in {team.team_id for team in inventory.team_bindings.teams}
    decision = decide(
        MEMORY_SPLIT_MEMBER,
        MEMORY_SPLIT_LEAD,
        routine_facts(claimed_team=UNBOUND_TEAM),
        inventory=inventory,
    )
    assert decision.granted is False
    assert decision.reason is AuthorizationReason.SUBMITTER_NOT_IN_CLAIMED_TEAM
    assert decision.team_verified is False


def test_a_lead_self_authorizing_cannot_attribute_the_run_to_a_foreign_team() -> None:
    inventory = two_team_inventory()
    own_team = decide(
        MEMORY_SPLIT_LEAD,
        None,
        routine_facts(claimed_team=MEMORY_SPLIT_TEAM),
        inventory=inventory,
    )
    foreign_team = decide(
        MEMORY_SPLIT_LEAD,
        None,
        routine_facts(claimed_team=CURRICULUM_TEAM),
        inventory=inventory,
    )
    assert own_team.granted is True
    assert own_team.reason is AuthorizationReason.ROUTINE_SELF_AUTHORIZED
    assert own_team.team_verified is True
    assert foreign_team.granted is False, (
        "attribution is checked against the submitter's own membership, independently of who "
        "holds the authority to approve"
    )
    assert foreign_team.reason is AuthorizationReason.SUBMITTER_NOT_IN_CLAIMED_TEAM


def test_an_admin_may_not_attribute_their_run_to_another_teams_budget() -> None:
    inventory = inventory_where_the_admin_belongs_to_memory_split()
    assert inventory.is_admin(TEAMLESS_ADMIN) is True
    assert {team.team_id for team in inventory.teams_for_member(TEAMLESS_ADMIN)} == {
        MEMORY_SPLIT_TEAM
    }
    own_team = decide(
        TEAMLESS_ADMIN,
        None,
        exception_facts(claimed_team=MEMORY_SPLIT_TEAM),
        inventory=inventory,
    )
    foreign_team = decide(
        TEAMLESS_ADMIN,
        None,
        exception_facts(claimed_team=CURRICULUM_TEAM),
        inventory=inventory,
    )
    assert own_team.granted is True
    assert own_team.reason is AuthorizationReason.EXCEPTION_SELF_APPROVED_BY_ADMIN
    assert own_team.team_verified is True
    assert foreign_team.granted is False, (
        "admin privilege is approval authority; it is not a licence to charge another team's "
        "budget for the run"
    )
    assert foreign_team.reason is AuthorizationReason.SUBMITTER_NOT_IN_CLAIMED_TEAM
    assert foreign_team.team_verified is False


@pytest.mark.parametrize("filename", REPRESENTATIVE_MANIFEST_FILENAMES)
def test_attribution_changes_no_classification_outcome(filename: str) -> None:
    manifest = load_representative_manifest(filename)
    catalog = load_workload_catalog()
    thresholds = load_approval_policy().thresholds
    facts = request_facts_from_manifest(
        manifest,
        repositories=load_repository_registry(),
        catalog=catalog,
        dataset_registry=load_dataset_registry(),
        estimated_cost_usd=compute_manifest_maximum_cost(manifest, catalog),
    )
    expected = expected_manifest_classification(filename)
    assert facts.claimed_team == manifest.team
    assert classify_request(facts, thresholds) is expected
    for claimed_team in (MEMORY_SPLIT_TEAM, CURRICULUM_TEAM, UNBOUND_TEAM):
        reattributed = facts.model_copy(update={"claimed_team": claimed_team})
        assert classify_request(reattributed, thresholds) is expected, (
            f"{filename} classified differently once attributed to {claimed_team!r}; "
            "attribution is not a cost input"
        )


REQUIRED_DECISION_FIELDS = tuple(
    name for name, field in AuthorizationDecision.model_fields.items() if field.is_required()
)


def test_the_decision_payload_this_module_uses_supplies_every_required_field() -> None:
    assert set(REQUIRED_DECISION_FIELDS) <= set(decision_payload())


@pytest.mark.parametrize("field", REQUIRED_DECISION_FIELDS)
def test_decision_rejects_a_payload_that_omits_a_required_field(field: str) -> None:
    payload = decision_payload()
    del payload[field]
    with pytest.raises(ValidationError) as exc_info:
        AuthorizationDecision.model_validate(payload)
    assert any(
        item["type"] == "missing" and item["loc"] == (field,)
        for item in exc_info.value.errors()
    ), f"expected a missing-field error naming {field!r}, got {exc_info.value.errors()}"
