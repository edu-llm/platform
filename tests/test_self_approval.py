"""Who may release their own run, read at both of the places that decide it.

The rule is one sentence in two halves: a researcher may not approve their own submission,
and a team lead or a platform admin may. Neither half is enforced in one place, and the two
places answer different questions, so a test that read only one of them would go on
reporting the rule while the other half was being removed.

The GitHub environment decides who may *release* the job. ``run-approval-lead`` names the
``team-leads`` team as its only reviewer and ``run-approval-admin`` names the two roster
admins, so a researcher is a reviewer nowhere and GitHub offers them no approval to give.
Who stands behind a team slot is organization state rather than repository state, so what is
read here is the committed capture of it, which expires.

``evaluate_authorization`` decides whether a released submission runs. It is handed the
submitter and whoever the workflow read back off the approvals endpoint, and it refuses a
self-approval by anybody holding neither role. That half holds whatever GitHub is configured
to do, and it is the half that stops a run rather than an approval.

``prevent_self_review`` is off on both gates and belongs to neither half. It is indifferent
to who the submitter is, so turning it on would take the second half of the rule away --
leads and admins would lose an approval the rule grants them -- while changing nothing for a
researcher who was never a reviewer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edullm_platform.contracts.admission import ApprovalEnvironment
from edullm_platform.contracts.authorization import (
    AuthorizationReason,
    evaluate_authorization,
)
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.policy import ApprovalClass, ApprovalPolicy, RequestFacts
from edullm_platform.phase2_evidence import (
    EnvironmentInventory,
    LeadTeamMembership,
    ProtectedEnvironment,
)
from tests.test_authorization import (
    load_approval_policy,
    load_organization_inventory,
    request_facts_payload,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_DIR = PROJECT_ROOT / "fixtures" / "evidence" / "phase-2" / "github"

#: A floor on the roster rather than its size. The loops below are per-person assertions
#: and would pass over an empty list, and the roster grows by a pull request that has no
#: reason to come here.
FEWEST_RESEARCHERS_WORTH_CHECKING = 20


def _capture(name: str) -> dict[str, object]:
    payload: dict[str, object] = json.loads(
        (CAPTURE_DIR / f"{name}.sanitized.json").read_text(encoding="utf-8")
    )
    return payload


@pytest.fixture(scope="module")
def inventory() -> OrganizationInventory:
    return load_organization_inventory()


@pytest.fixture(scope="module")
def policy() -> ApprovalPolicy:
    return load_approval_policy()


@pytest.fixture(scope="module")
def environments() -> EnvironmentInventory:
    return EnvironmentInventory.model_validate(_capture("environments"))


@pytest.fixture(scope="module")
def lead_team() -> LeadTeamMembership:
    return LeadTeamMembership.model_validate(_capture("lead-team"))


def researchers(inventory: OrganizationInventory) -> tuple[str, ...]:
    """Everybody on the roster who holds neither role, which is who the rule is about."""
    return tuple(
        member.github_login
        for member in inventory.members
        if not inventory.is_admin(member.github_login)
        and not inventory.is_team_lead(member.github_login)
    )


def gate_for(
    environments: EnvironmentInventory, approval_class: ApprovalClass
) -> ProtectedEnvironment:
    """The captured gate a submission of this class is routed to.

    Resolved through :meth:`ApprovalEnvironment.for_approval_class` rather than by name, so
    a routing change reaches these tests instead of leaving them describing the gate the
    class used to use.
    """
    wanted = ApprovalEnvironment.for_approval_class(approval_class)
    return next(e for e in environments.environments if e.name == wanted.value)


def may_release(
    environments: EnvironmentInventory, lead_team: LeadTeamMembership
) -> frozenset[str]:
    """Every person who can release either gate, with the team slot expanded.

    A team names nobody in the environment record, so the capture of its membership is the
    only way to answer this at all. A slot that cannot be expanded fails rather than being
    skipped: an unexpanded team is precisely the shape a wrong answer takes here.
    """
    logins: set[str] = set()
    for environment in environments.environments:
        for reviewer in environment.reviewers:
            if reviewer.kind == "User":
                logins.add(reviewer.name)
                continue
            assert reviewer.name == lead_team.team_slug, (
                f"the {environment.name} gate is reviewed by the {reviewer.name} team and "
                "no capture of that team's membership is committed, so who may release "
                "that gate cannot be read here"
            )
            logins.update(lead_team.member_logins)
    return frozenset(login.lower() for login in logins)


def facts_for(
    inventory: OrganizationInventory, login: str, **overrides: object
) -> RequestFacts:
    """A request this person could really file, claiming a team the roster lets them claim.

    Attribution is checked before self-approval is reached, and a submission naming a team
    its submitter is not recorded in is refused for that instead. No group records its
    members today so every claim passes, and recording one would otherwise quietly turn
    these into tests about attribution.
    """
    recorded = inventory.teams_for_member(login)
    if recorded:
        overrides["claimed_team"] = recorded[0].team_id
    return RequestFacts.model_validate(request_facts_payload(**overrides))


def test_a_researcher_cannot_release_their_own_run(
    inventory: OrganizationInventory,
    policy: ApprovalPolicy,
    environments: EnvironmentInventory,
    lead_team: LeadTeamMembership,
) -> None:
    people = researchers(inventory)
    assert len(people) >= FEWEST_RESEARCHERS_WORTH_CHECKING, people

    reviewers = may_release(environments, lead_team)
    for login in people:
        assert login.lower() not in reviewers, (
            f"{login} holds neither role and reviews an approval gate, so GitHub will let "
            "them release their own submission"
        )
        # Both classes, because the cost of a run is what the classes separate and neither
        # of them is a run somebody may wave through for themselves.
        for facts in (
            facts_for(inventory, login),
            facts_for(inventory, login, estimated_cost_usd="5000"),
        ):
            decision = evaluate_authorization(
                login, login, facts, policy, inventory
            )

            assert decision.granted is False, login
            assert (
                decision.reason is AuthorizationReason.SELF_APPROVAL_NOT_PERMITTED_FOR_MEMBER
            ), login


def test_a_lead_can_release_their_own_run(
    inventory: OrganizationInventory,
    policy: ApprovalPolicy,
    environments: EnvironmentInventory,
    lead_team: LeadTeamMembership,
) -> None:
    gate = gate_for(environments, ApprovalClass.ROUTINE)

    assert [(r.kind, r.name) for r in gate.reviewers] == [("Team", lead_team.team_slug)]
    # The setting that would take this away, and it cannot tell a lead from a researcher:
    # it refuses whoever started the run, so it would stop the people the rule permits and
    # would change nothing for the people it does not.
    assert gate.prevent_self_review is False

    on_the_team = {login.lower() for login in lead_team.member_logins}
    for lead in inventory.team_leads:
        assert lead.lower() in on_the_team, lead
        decision = evaluate_authorization(
            lead,
            lead,
            facts_for(inventory, lead),
            policy,
            inventory
        )

        assert decision.granted is True, lead
        assert decision.reason is AuthorizationReason.ROUTINE_SELF_AUTHORIZED, lead


def test_the_admin_gate_still_lets_an_administrator_release_their_own_run(
    inventory: OrganizationInventory,
    environments: EnvironmentInventory,
) -> None:
    """The gate is unchanged and no run reaches it, which are two facts and not one.

    This asked ``evaluate_authorization`` for a five-thousand-dollar submission and got
    ``exception_self_approved_by_admin``. Policy v5 classifies that as routine, so the
    question can no longer be put through a request, and a version of this test that kept
    asking would be asserting the lead path under an admin's name.

    What is left to check is the environment, which is a real GitHub setting that a person
    can change and that nothing else in this module reads for the admin gate. It carries the
    two admins as reviewers and does not prevent self-review, so the day a capacity block
    classifies as an exception, an admin can release their own.

    Mutation: turn ``prevent_self_review`` on for ``run-approval-admin``. This fails, and
    the failure it prevents is a capacity block that nobody can release, discovered on the
    first one.
    """
    gate = gate_for(environments, ApprovalClass.EXCEPTION)
    named = {reviewer.name.lower() for reviewer in gate.reviewers if reviewer.kind == "User"}

    assert gate.prevent_self_review is False
    for admin in inventory.admins:
        assert admin.lower() in named, admin
