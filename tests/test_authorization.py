import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from edullm_platform.canonical import canonical_json_bytes, sha256_digest
from edullm_platform.config import load_yaml
from edullm_platform.contracts import authorization as authorization_module
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
from edullm_platform.manifest_helpers import compute_manifest_cost_inputs
from edullm_platform.operational_inventory import (
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

#: THE TEAM THESE TESTS CLAIM WHEN THEY ARE NOT TESTING ATTRIBUTION, AND WHY IT CHANGED.
#:
#: These facts used to claim `memory-split` by default, which was free while every team in
#: config/organization.yaml recorded no members: evaluate_authorization treated an unrecorded
#: submitter as unverifiable and the claim cost nothing. Recording the roster made the claim
#: load-bearing, and the default silently turned every test in this file into an attribution
#: test -- `ericrcwu001` leads data-prep, not Memory, so a routine self-authorization began
#: failing on a team the test was never about.
#:
#: `scratch` is the honest default because every member of the roster is in it. It is the bin
#: the guide tells a new person to pick, so a submitter is never refused it, which leaves
#: these tests measuring the thing they name. A test that is about attribution says so by
#: passing claimed_team explicitly.
SCRATCH_TEAM = "scratch"


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
        "claimed_team": SCRATCH_TEAM,
        "repository_registered": True,
        "dataset_registered": True,
        "dataset_is_a_corpus": True,
        "compute_profile_registered": True,
        "capacity_block_backed": False,
        "immutable_revision": True,
        "immutable_image": True,
        "image_scan_reviewed": True,
        # SIX HUNDRED, WHICH WAS A HUNDRED UNTIL POLICY v5. A hundred dollars in one cell is
        # released by nobody now, so the base fixture of a module about approvers had to
        # move above the bound or every row about who releases what would have been a row
        # about nobody releasing anything, passing or failing for reasons unrelated to its
        # subject.
        "estimated_cost_usd": "600",
        "maximum_runtime_hours": "6",
        "maximum_attempts": 2,
    }
    payload.update(overrides)
    return payload


def routine_facts(**overrides: object) -> RequestFacts:
    return RequestFacts.model_validate(request_facts_payload(**overrides))


def exception_facts(**overrides: object) -> RequestFacts:
    """What used to classify as an exception, which is now a large routine run.

    Kept, and kept at five thousand dollars, because the rows it feeds are about an admin
    releasing something and the facts are what those rows were reviewed against. Nothing
    about these facts reaches the exception class any more, so they are passed to
    :func:`decide_an_exception`, which injects it and says why.
    """
    return routine_facts(**{"estimated_cost_usd": "5000", **overrides})


def automatic_facts(**overrides: object) -> RequestFacts:
    """Under the bound and a single cell, so classify_request answers automatic.

    Built from the same base as :func:`routine_facts` and moving only the cost, so a test
    using this differs from its routine sibling in nothing else. It moved the runtime too
    until v5 retired ``automatic_below_runtime_hours``.
    """
    return routine_facts(**{"estimated_cost_usd": "0.75", **overrides})


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


def decide_an_exception(
    submitter: str,
    approver: str | None,
    request: RequestFacts,
    *,
    policy: ApprovalPolicy | None = None,
    inventory: OrganizationInventory | None = None,
) -> AuthorizationDecision:
    """The same funnel with the class forced, because no request can reach it any more.

    **THE CLASS IS INJECTED AND THAT IS THE ONLY HONEST WAY TO TEST THIS BRANCH.**
    ``classify_request`` answers automatic or routine under policy v5 and nothing else, so
    every row below that is about an admin releasing an exception would otherwise be a row
    about a lead releasing a routine run, silently, and would go green for the wrong reason.
    That is the shape of bug this repository has met seven times.

    The branch is not dead and is not deleted. It is what a capacity block will route
    through, ``exception_approver_roles`` in ``config/policy.yaml`` names the role it asks
    for, and four of the reasons it returns are parsed back out of nineteen decision records
    written under v2 to v4. What these rows assert is the property that has to survive until
    something classifies as an exception again: only an admin may release one.
    """
    with patch.object(
        authorization_module, "classify_request", return_value=ApprovalClass.EXCEPTION
    ):
        return decide(
            submitter, approver, request, policy=policy, inventory=inventory
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


def test_no_fixture_in_this_module_can_reach_the_exception_class() -> None:
    """Says out loud what :func:`decide_an_exception` is working around.

    ``exception_facts`` is five thousand dollars, which was an exception under v4 and is a
    team lead's to release under v5. Every row in this module that reads as an admin
    approval is reaching that branch by injection, and a reader who did not know that would
    take those rows as evidence that a five-thousand-dollar run still needs an admin.

    Mutation: return ``ApprovalClass.EXCEPTION`` from any branch of ``classify_request``.
    The second assertion fails, and the injection in ``decide_an_exception`` should then be
    replaced by the facts that reach it.
    """
    thresholds = load_approval_policy().thresholds

    assert classify_request(routine_facts(), thresholds) is ApprovalClass.ROUTINE
    assert classify_request(exception_facts(), thresholds) is ApprovalClass.ROUTINE


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
    decision = decide_an_exception(submitter, approver, exception_facts())
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
    decision = decide_an_exception(PLAIN_MEMBER, LEAD_WITHOUT_ADMIN, exception_facts())
    assert decision.granted is False
    assert decision.reason is AuthorizationReason.APPROVER_LACKS_ADMIN_ROLE


def test_admin_approves_someone_elses_exception() -> None:
    decision = decide_an_exception(PLAIN_MEMBER, ADMIN_WITHOUT_LEAD, exception_facts())
    assert decision.granted is True
    assert decision.reason is AuthorizationReason.EXCEPTION_APPROVED_BY_ADMIN


@pytest.mark.parametrize("approver", [None, ADMIN_AND_LEAD])
def test_admin_may_approve_their_own_exception_as_an_accepted_risk(approver: str | None) -> None:
    decision = decide_an_exception(ADMIN_AND_LEAD, approver, exception_facts())
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
    approved = decide_an_exception(PLAIN_MEMBER, spelling, exception_facts())
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
    decision = decide_an_exception(submitter, approver, exception_facts())
    assert decision.granted is True
    assert decision.reason is AuthorizationReason.EXCEPTION_SELF_APPROVED_BY_ADMIN


def test_case_variants_of_a_lead_login_are_recognized_as_self_authorization() -> None:
    decision = decide(LEAD_WITHOUT_ADMIN.upper(), LEAD_WITHOUT_ADMIN, routine_facts())
    assert decision.granted is True
    assert decision.reason is AuthorizationReason.ROUTINE_SELF_AUTHORIZED


def test_decision_preserves_authored_login_casing() -> None:
    decision = decide_an_exception("CaIiRiS", "bRiTiShAmEriCqn", exception_facts())
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
    request = routine_facts(claimed_team=MEMORY_SPLIT_TEAM)
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
        routine_facts(claimed_team=MEMORY_SPLIT_TEAM),
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
    own_team = routine_facts(claimed_team=MEMORY_SPLIT_TEAM)
    by_admin = decide(
        MEMORY_SPLIT_MEMBER, TEAMLESS_ADMIN, own_team, policy=team_scoped, inventory=inventory
    )
    by_other_lead = decide(
        MEMORY_SPLIT_MEMBER, CURRICULUM_LEAD, own_team, policy=team_scoped, inventory=inventory
    )
    assert by_admin.granted is True
    assert by_other_lead.granted is False
    assert by_other_lead.reason is AuthorizationReason.APPROVER_DOES_NOT_LEAD_SUBMITTER_TEAM


def test_team_scope_does_not_restrict_exception_approval() -> None:
    decision = decide_an_exception(
        MEMORY_SPLIT_MEMBER,
        TEAMLESS_ADMIN,
        exception_facts(claimed_team=MEMORY_SPLIT_TEAM),
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
        "automatic_below_approval_thresholds",
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


def test_the_retired_claimed_team_reason_still_reads_back_off_a_stored_record() -> None:
    """Mutation: delete the enum member now that nothing returns it.

    Four decision records were written with this reason before the refusal was removed, one
    of them committed under ``fixtures/evidence/``, and ``AuthorizationReasonValue`` parses
    the stored string back through this enum. Deleting the member makes those four records
    unreadable by the code that wrote them, which is the one thing an audit trail may not
    do. The member is also where ``cli.preflight`` reads the word it refuses with, so the
    spelling a submitter meets locally cannot drift from the spelling in the history.
    """
    stored = AuthorizationDecision.model_validate(
        decision_payload(granted=False, reason="submitter_not_in_claimed_team")
    )

    assert stored.reason is AuthorizationReason.SUBMITTER_NOT_IN_CLAIMED_TEAM
    assert stored.granted is False


def test_no_evaluation_against_the_shipped_roster_reaches_the_claimed_team_reason() -> None:
    """Mutation: put the refusal back for one class, or for one kind of approver.

    Every recorded member, against a team they are not in, on all three approval classes and
    with each of the three kinds of approver. None of it may refuse. Asserted as a sweep
    rather than as one case because the branch used to sit above the automatic return and
    below the roster checks, and a partial reinstatement would leave most of this file green.
    """
    inventory = load_organization_inventory()
    policy = load_approval_policy()
    recorded = sorted(
        {
            login
            for team in inventory.team_bindings.teams
            for login in team.member_logins + team.lead_logins
        }
    )
    assert recorded, "the shipped roster records nobody, so this sweep proves nothing"

    for submitter in recorded:
        for request in (
            automatic_facts(claimed_team=UNBOUND_TEAM),
            routine_facts(claimed_team=UNBOUND_TEAM),
            exception_facts(claimed_team=UNBOUND_TEAM),
        ):
            for approver in (None, LEAD_WITHOUT_ADMIN, ADMIN_WITHOUT_LEAD):
                decision = evaluate_authorization(
                    submitter,
                    approver,
                    request,
                    policy,
                    inventory,
                )
                assert (
                    decision.reason is not AuthorizationReason.SUBMITTER_NOT_IN_CLAIMED_TEAM
                ), f"{submitter} claiming {UNBOUND_TEAM} was refused for the claim"
                assert decision.team_verified is False


def test_granting_reasons_partition_the_reason_vocabulary() -> None:
    assert GRANTING_REASONS == frozenset(
        {
            AuthorizationReason.AUTOMATIC_BELOW_APPROVAL_THRESHOLDS,
            AuthorizationReason.ROUTINE_SELF_AUTHORIZED,
            AuthorizationReason.ROUTINE_APPROVED_BY_LEAD_OR_ADMIN,
            AuthorizationReason.EXCEPTION_APPROVED_BY_ADMIN,
            AuthorizationReason.EXCEPTION_SELF_APPROVED_BY_ADMIN,
        }
    )
    assert GRANTING_REASONS < frozenset(AuthorizationReason)


# --------------------------------------------------------------------------------------
# The automatic class, and the roster checks it does not skip
# --------------------------------------------------------------------------------------


def test_an_ordinary_member_may_run_an_automatic_submission_with_no_approver() -> None:
    """The behaviour the whole class exists for, and the one a member could not have before.

    A plain member submitting with no approver is self-approval on every other path, and
    self_approval_not_permitted_for_member refuses it. An automatic run is not an approval
    by the submitter; it is a decision by policy that no approval is required, so it returns
    before the approver question is reached.

    Mutation: move the automatic branch below the self-approval test in
    ``evaluate_authorization``. This is refused, and the cheapest runs on the platform go
    from unattended to impossible.
    """
    decision = evaluate_authorization(
        PLAIN_MEMBER,
        None,
        automatic_facts(),
        load_approval_policy(),
        load_organization_inventory(),
    )

    assert decision.approval_class is ApprovalClass.AUTOMATIC
    assert decision.granted is True
    assert decision.reason is AuthorizationReason.AUTOMATIC_BELOW_APPROVAL_THRESHOLDS
    assert decision.approver is None


def test_an_off_roster_submitter_is_refused_an_automatic_submission() -> None:
    """The control the original incident bought, which auto-approval does not spend.

    This is the sentence config/policy.yaml makes and the only one that matters when
    somebody asks in six months whether the auto-approve rule weakened anything. Routing
    changed; who may submit did not.

    Mutation: move the automatic branch above the roster test. Anybody on the internet who
    can dispatch the workflow gets a run, and the decision record says it was authorized.
    """
    decision = evaluate_authorization(
        UNKNOWN_LOGIN,
        None,
        automatic_facts(),
        load_approval_policy(),
        load_organization_inventory(),
    )

    assert decision.approval_class is ApprovalClass.AUTOMATIC
    assert decision.granted is False
    assert decision.reason is AuthorizationReason.SUBMITTER_NOT_IN_ROSTER


def test_an_automatic_submission_claiming_a_foreign_team_runs_and_records_the_claim() -> None:
    """The two roster checks stay, and the third one is gone rather than reordered.

    A member whose group is recorded and who names a different one is authorized, and the
    decision record carries ``team_verified`` false. That flag is the whole of what a
    mis-claimed team now produces.

    Mutation: refuse it again. This branch used to sit between the two roster checks above,
    it is the only reason in the enum that ever denied anybody, and every one of the four
    denials was a real researcher whose approval a lead or an admin had already spent.
    """
    decision = evaluate_authorization(
        MEMORY_SPLIT_MEMBER,
        None,
        automatic_facts(claimed_team=CURRICULUM_TEAM),
        load_approval_policy(),
        load_organization_inventory(),
    )

    assert decision.granted is True
    assert decision.reason is AuthorizationReason.AUTOMATIC_BELOW_APPROVAL_THRESHOLDS
    assert decision.claimed_team == CURRICULUM_TEAM
    assert decision.team_verified is False


def test_a_run_over_the_automatic_bounds_still_needs_the_approver_it_always_did() -> None:
    """The unchanged half, asserted beside the changed one so the boundary is visible.

    Same submitter, same absent approver, one difference: the request is no longer small
    enough. It is refused exactly as it was before this class existed.
    """
    decision = evaluate_authorization(
        PLAIN_MEMBER,
        None,
        routine_facts(),
        load_approval_policy(),
        load_organization_inventory(),
    )

    assert decision.approval_class is ApprovalClass.ROUTINE
    assert decision.granted is False
    assert decision.reason is AuthorizationReason.SELF_APPROVAL_NOT_PERMITTED_FOR_MEMBER


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
    granted = decide_an_exception(PLAIN_MEMBER, ADMIN_WITHOUT_LEAD, exception_facts())
    denied = decide_an_exception(PLAIN_MEMBER, LEAD_WITHOUT_ADMIN, exception_facts())
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
        routine_facts(claimed_team=MEMORY_SPLIT_TEAM),
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
        exception = decide_an_exception(
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
        routine_facts(claimed_team=MEMORY_SPLIT_TEAM),
        policy=approval_policy_with_scope(ApprovalScope.TEAM),
        inventory=two_team_inventory(),
    )
    assert absent.granted is False
    assert mismatch.granted is False
    assert absent.reason is AuthorizationReason.TEAM_SCOPE_REQUIRES_TEAM_BINDINGS
    assert mismatch.reason is AuthorizationReason.APPROVER_DOES_NOT_LEAD_SUBMITTER_TEAM


@pytest.mark.parametrize("claimed_team", [CURRICULUM_TEAM, UNBOUND_TEAM])
def test_attribution_is_recorded_against_the_shipped_roster_and_not_enforced(
    claimed_team: str,
) -> None:
    """Mutation: refuse a claim the roster contradicts, as this used to.

    Both parameters are teams ``caiiris`` is not in. ``curriculum`` is a retired group name
    the catalog no longer declares and ``not-a-team`` never existed. Neither is refused, and
    both are recorded: the claim goes on the decision as it was made and ``team_verified``
    says nothing established it.

    The refusal that used to be here fired four times against real researchers and every
    one of them already had an approval spent on it, because this function runs inside AWS
    on the far side of the gate. The comparison itself did not go away. It happens on the
    form, which offers eight declared ids, and in ``cli.preflight._check_team``, which asks
    the same question through the same helper before anything is dispatched.
    """
    inventory = load_organization_inventory()
    assert inventory.team_bindings.teams != (), "the shipped roster declares its groups"
    assert any(team.member_logins != () for team in inventory.team_bindings.teams)
    decision = decide(
        PLAIN_MEMBER,
        LEAD_WITHOUT_ADMIN,
        routine_facts(claimed_team=claimed_team),
        inventory=inventory,
    )
    assert decision.granted is True
    assert decision.reason is AuthorizationReason.ROUTINE_APPROVED_BY_LEAD_OR_ADMIN
    assert decision.claimed_team == claimed_team
    assert decision.team_verified is False


def test_a_recorded_member_claiming_their_own_group_is_verified_on_the_shipped_roster() -> None:
    """The other half of enforcement, against the real roster rather than a synthetic one.

    ``caiiris`` is recorded in Memory. Naming it is granted and the decision records the
    attribution as verified, which is the state no shipped decision could reach before the
    assignments landed. Without this, a change that made every claim unverifiable again
    would leave the whole suite green, since a false ``team_verified`` was the old normal.
    """
    inventory = load_organization_inventory()
    decision = decide(
        PLAIN_MEMBER,
        LEAD_WITHOUT_ADMIN,
        routine_facts(claimed_team=MEMORY_SPLIT_TEAM),
        inventory=inventory,
    )
    assert decision.granted is True
    assert decision.reason is AuthorizationReason.ROUTINE_APPROVED_BY_LEAD_OR_ADMIN
    assert decision.team_verified is True


def test_every_recorded_member_may_claim_scratch() -> None:
    """The property that keeps a first run from being refused the team the guide names.

    ``guides/the-platform.md`` tells a new person to use ``scratch`` for their first run, in
    those words. Enforcement is per submitter, so the moment somebody's assignment is
    recorded, every team they claim is checked. A member recorded in their research group
    and left out of ``scratch`` would be denied the one team they were told to pick, with a
    refusal naming a team the documentation handed them.

    Pinned as a property over the whole roster rather than a case, because the failure
    arrives one person at a time as assignments are edited.
    """
    inventory = load_organization_inventory()
    scratch = next(
        team for team in inventory.team_bindings.teams if team.team_id == SCRATCH_TEAM
    )
    recorded = {
        login
        for team in inventory.team_bindings.teams
        for login in team.lead_logins + team.member_logins
    }
    missing = sorted(login for login in recorded if not scratch.includes(login))
    assert missing == [], (
        f"recorded members who cannot claim scratch: {missing}. Every login in any team "
        "binding must also be in scratch, or their first run is refused."
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


def test_a_submitter_naming_another_teams_id_keeps_the_lead_approval_they_were_given() -> None:
    """The refusal that spent four approvals, asserted as gone rather than merely absent.

    A lead released this run. Nothing downstream of that signature may take it back over a
    claim the same roster could have been read for before the gate opened.
    """
    inventory = two_team_inventory()
    assert inventory.is_team_lead(MEMORY_SPLIT_LEAD) is True
    decision = decide(
        MEMORY_SPLIT_MEMBER,
        MEMORY_SPLIT_LEAD,
        routine_facts(claimed_team=CURRICULUM_TEAM),
        inventory=inventory,
    )
    assert decision.approval_class is ApprovalClass.ROUTINE
    assert decision.granted is True
    assert decision.reason is AuthorizationReason.ROUTINE_APPROVED_BY_LEAD_OR_ADMIN
    assert decision.claimed_team == CURRICULUM_TEAM
    assert decision.team_verified is False
    assert AuthorizationReason.SUBMITTER_NOT_IN_CLAIMED_TEAM not in GRANTING_REASONS


def test_a_team_id_no_roster_defines_is_recorded_the_same_way_as_a_foreign_team() -> None:
    inventory = two_team_inventory()
    assert UNBOUND_TEAM not in {team.team_id for team in inventory.team_bindings.teams}
    decision = decide(
        MEMORY_SPLIT_MEMBER,
        MEMORY_SPLIT_LEAD,
        routine_facts(claimed_team=UNBOUND_TEAM),
        inventory=inventory,
    )
    assert decision.granted is True
    assert decision.team_verified is False


def test_the_verified_flag_is_the_only_thing_a_foreign_team_changes_for_a_lead() -> None:
    """Two decisions differing in one field, which is what makes the flag load-bearing.

    Mutation: stop computing ``team_verified`` and hard-code it. Nothing else in either
    decision distinguishes a run whose attribution the roster confirms from one whose
    attribution nothing established, so this field is the whole of the record.
    """
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
    assert foreign_team.granted is True
    assert foreign_team.reason is AuthorizationReason.ROUTINE_SELF_AUTHORIZED
    assert foreign_team.team_verified is False, (
        "attribution is read off the submitter's own membership and recorded; the flag is "
        "what a later reader has instead of a refusal"
    )


def test_an_admin_attributing_a_run_elsewhere_is_recorded_rather_than_refused() -> None:
    inventory = inventory_where_the_admin_belongs_to_memory_split()
    assert inventory.is_admin(TEAMLESS_ADMIN) is True
    assert {team.team_id for team in inventory.teams_for_member(TEAMLESS_ADMIN)} == {
        MEMORY_SPLIT_TEAM
    }
    own_team = decide_an_exception(
        TEAMLESS_ADMIN,
        None,
        exception_facts(claimed_team=MEMORY_SPLIT_TEAM),
        inventory=inventory,
    )
    foreign_team = decide_an_exception(
        TEAMLESS_ADMIN,
        None,
        exception_facts(claimed_team=CURRICULUM_TEAM),
        inventory=inventory,
    )
    assert own_team.granted is True
    assert own_team.reason is AuthorizationReason.EXCEPTION_SELF_APPROVED_BY_ADMIN
    assert own_team.team_verified is True
    assert foreign_team.granted is True
    assert foreign_team.reason is AuthorizationReason.EXCEPTION_SELF_APPROVED_BY_ADMIN
    assert foreign_team.team_verified is False


@pytest.mark.parametrize("filename", REPRESENTATIVE_MANIFEST_FILENAMES)
def test_attribution_changes_no_classification_outcome(filename: str) -> None:
    manifest = load_representative_manifest(filename)
    catalog = load_workload_catalog()
    thresholds = load_approval_policy().thresholds
    cost = compute_manifest_cost_inputs(manifest, catalog)
    facts = request_facts_from_manifest(
        manifest,
        repositories=load_repository_registry(),
        catalog=catalog,
        dataset_registry=load_dataset_registry(),
        estimated_cost_usd=cost.maximum_compute_cost_usd,
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
