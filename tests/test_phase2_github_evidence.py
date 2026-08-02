"""What the captured GitHub configuration has to say, read from the committed record.

Three Phase 2 criteria are about settings rather than about code, and until these captures
existed all three cited nothing at all. The configuration was set deliberately and then
believed, which is the state this repository does not accept anywhere else.

What a capture proves and what it does not is worth being exact about. It proves that the
repository looked like this at ``observed_at``. It does not prove it looks like this now,
which is why the records are ``FreshEvidenceModel`` and refuse to load past the freshness
window -- a GitHub setting can be changed in a browser in ten seconds by anybody with
admin, leaving no artifact in any repository. When these expire the criteria resting on
them go red, and the two honest responses are to re-capture or to delete the records and
the citations together.

The reviewer comparison is against ``config/organization.yaml`` rather than against a list
written here. Drift between GitHub's reviewers and the platform's roster is otherwise
silent, and the whole authorization model assumes the two agree.

**On the lead gate that comparison has to reach one level further, and until now it did
not.** That gate's reviewer list names no people at all: its single reviewer is the
``team-leads`` team, because eight leads exceed the six reviewer slots and a team counts
as one. Nothing recorded who was in that team, so a member added to it on GitHub became a
reviewer on the lead gate and every test in this module went on passing. The two
membership tests below read ``lead-team.sanitized.json`` and compare it against
``team_leads`` in both directions, as two tests rather than one, because the directions
are different incidents with different fixes: a login on GitHub and not in the roster can
open a gate that admission will then refuse, and a login in the roster and not on GitHub
is a lead the lead gate will never release, even for his own group's run. Both were live
at once during the two-day window that ended on 2026-07-30, which
``config/organization.yaml`` records in its own words.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from edullm_platform.phase2_evidence import (
    APPROVAL_ENVIRONMENT_NAMES,
    EnvironmentInventory,
    LeadTeamMembership,
    SecretInventory,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CAPTURE_DIR = PROJECT_ROOT / "fixtures" / "evidence" / "phase-2" / "github"


def _load(name: str) -> dict[str, object]:
    payload: dict[str, object] = json.loads(
        (CAPTURE_DIR / f"{name}.sanitized.json").read_text(encoding="utf-8")
    )
    return payload


@pytest.fixture(scope="module")
def environments() -> EnvironmentInventory:
    return EnvironmentInventory.model_validate(_load("environments"))


@pytest.fixture(scope="module")
def secrets() -> SecretInventory:
    return SecretInventory.model_validate(_load("secrets"))


@pytest.fixture(scope="module")
def lead_team() -> LeadTeamMembership:
    return LeadTeamMembership.model_validate(_load("lead-team"))


@pytest.fixture(scope="module")
def roster() -> dict[str, object]:
    loaded: dict[str, object] = yaml.safe_load(
        (PROJECT_ROOT / "config" / "organization.yaml").read_text(encoding="utf-8")
    )
    return loaded


def test_all_three_approval_environments_exist_and_no_fourth_one_does(
    environments: EnvironmentInventory,
) -> None:
    # All of them, not only the three expected. An environment is auto-created with no
    # protection rules at all by anyone who names one in a workflow file, and everyone who
    # can submit holds the write access that allows it. The trust policy enumerates three
    # subjects and would refuse a fourth, so such an environment could not reach AWS -- but
    # a capture that only looked for the expected names could not tell anybody it existed.
    assert set(environments.names) == set(APPROVAL_ENVIRONMENT_NAMES)


def test_every_environment_restricts_deployments_to_main_by_name(
    environments: EnvironmentInventory,
) -> None:
    # The custom form specifically, and this is the assertion the criterion exists for.
    # protected_branches follows whatever branch protection happens to cover, so it widens
    # silently the moment a second branch is protected -- a change nobody would connect to
    # this control. custom_branch_policies matches names that were written down.
    for environment in environments.environments:
        assert environment.custom_branch_policies is True, environment.name
        assert environment.protected_branches is False, environment.name
        assert environment.branch_policy_names == ("main",), environment.name


def test_no_environment_lets_an_admin_release_without_a_reviewer(
    environments: EnvironmentInventory,
) -> None:
    # Admin bypass produces no approval record at all. The master plan's compensating
    # control for admin self-approval is that the decision is recorded and attributable,
    # not that it is prevented, so a bypass removes the thing the design leans on rather
    # than merely widening who may approve.
    for environment in environments.environments:
        assert environment.can_admins_bypass is False, environment.name


def test_self_review_is_deliberately_permitted_on_the_two_reviewed_gates(
    environments: EnvironmentInventory,
) -> None:
    # Asserted as false on purpose, so that somebody "hardening" it has to read why. Leads
    # self-authorizing routine runs and admins approving their own exceptions are both
    # intended by the global constraints. The prohibition that does apply -- a member
    # cannot approve their own submission -- is enforced by members not being reviewers,
    # and independently by evaluate_authorization.
    #
    # False on run-approval-automatic for an unrelated reason, and the name says two gates
    # because that environment is not one of them. GitHub answers 422 to setting this flag
    # on an environment with no reviewers, so the capture derives false from the absent
    # required_reviewers rule. Same value, different fact: there is nobody to prevent.
    for environment in environments.environments:
        assert environment.prevent_self_review is False, environment.name


def test_the_automatic_gate_carries_every_protection_the_other_two_do_except_a_reviewer(
    environments: EnvironmentInventory,
) -> None:
    """The claim the third trust policy subject rests on, read off the live capture.

    Enumerating a reviewer-less environment in the trust policy is defensible only because
    what it drops is the reviewer and nothing else. Three sibling tests above already assert
    the branch policy, the admin bypass and the wait timer across every environment; this
    one states the negative half those cannot, which is that the reviewer list is empty on
    purpose rather than by an edit somebody made in a browser.

    Mutation: add a reviewer to this environment. Nothing else in the suite objects -- the
    class would still route here, the subject would still match, and runs the policy says
    need no human would quietly start waiting for one again. This is the only place that
    would say so.
    """
    automatic = next(
        e for e in environments.environments if e.name == "run-approval-automatic"
    )

    assert automatic.reviewers == ()
    assert automatic.branch_policy_names == ("main",)
    assert automatic.can_admins_bypass is False
    assert automatic.wait_timer_minutes == 0


def test_the_lead_gate_is_reviewed_by_the_leads_team_rather_than_by_named_people(
    environments: EnvironmentInventory,
) -> None:
    # Eight leads and six reviewer slots, and a team counts as one slot, so the team is
    # the only way to list them all. Asserting the type and not just the name matters: a
    # capture that flattened the team into its members would agree with the roster for the
    # wrong reason, and would keep agreeing after somebody replaced it with six names.
    lead_gate = next(e for e in environments.environments if e.name == "run-approval-lead")

    assert [(r.kind, r.name) for r in lead_gate.reviewers] == [("Team", "team-leads")]


def test_the_admin_gate_is_reviewed_by_the_roster_admins_and_nobody_else(
    environments: EnvironmentInventory,
    roster: dict[str, object],
) -> None:
    # The roster's admins, not GitHub's org owners. The third owner is the sandbox owner,
    # who appears nowhere in this platform's role model, and an exception released by
    # somebody outside the model would be attributable to a person the policy cannot
    # reason about.
    admin_gate = next(e for e in environments.environments if e.name == "run-approval-admin")
    reviewers = {r.name.lower() for r in admin_gate.reviewers}

    assert all(r.kind == "User" for r in admin_gate.reviewers)
    assert reviewers == {str(login).lower() for login in roster["admins"]}


def test_no_member_who_is_not_a_lead_or_admin_reviews_either_gate(
    environments: EnvironmentInventory,
    roster: dict[str, object],
) -> None:
    # The captured half of "a member cannot approve their own submission". The other half
    # is evaluate_authorization returning self_approval_not_permitted_for_member, which
    # holds whatever GitHub is configured to do.
    privileged = {str(login).lower() for login in roster["team_leads"]}
    privileged |= {str(login).lower() for login in roster["admins"]}
    named = {
        reviewer.name.lower()
        for environment in environments.environments
        for reviewer in environment.reviewers
        if reviewer.kind == "User"
    }

    assert named <= privileged, sorted(named - privileged)


def test_the_membership_captured_is_of_the_team_the_lead_gate_actually_names(
    environments: EnvironmentInventory,
    lead_team: LeadTeamMembership,
) -> None:
    # What the slug is recorded for. The two tests below compare a list of logins against
    # the roster, and would go on passing if the capture were of some other team, or if
    # the gate's reviewer had been swapped for one -- either of which turns them into
    # assertions about a team that releases nothing. Read out of the same environment
    # capture the reviewer tests use, so the gate this is tied to cannot drift from the
    # gate they describe.
    lead_gate = next(e for e in environments.environments if e.name == "run-approval-lead")

    # Unpacked with a message rather than as a tuple assignment. Two sibling tests fail
    # loudly if the gate gains a second reviewer, so nothing is missed either way, but a
    # bare ValueError: too many values to unpack is the one failure in this module that
    # says nothing about what it is complaining about.
    assert len(lead_gate.reviewers) == 1, (
        "the lead gate names more than one reviewer, so there is no longer a single team "
        "for this capture to be the membership of, and the two comparisons below are "
        f"about one entry in a longer list: {[(r.kind, r.name) for r in lead_gate.reviewers]}"
    )
    (reviewer,) = lead_gate.reviewers

    assert (reviewer.kind, reviewer.name) == ("Team", lead_team.team_slug)
    # A team belongs to an organization rather than to a repository, and the capture
    # records both because the claim is about this repository's gate: the same team can
    # review an environment on a repository this platform does not own.
    assert (lead_team.organization, lead_team.repository) == (
        lead_gate.organization,
        lead_gate.repository,
    )


def test_only_a_lead_the_roster_declares_can_release_a_run_at_the_lead_gate(
    lead_team: LeadTeamMembership,
    roster: dict[str, object],
) -> None:
    # The direction that widens authority, and the hole this capture was added to close.
    # GitHub's team is what releases the deployment; config/organization.yaml is what
    # admission reads afterwards. A login here and not there can open the lead gate on any
    # team's routine run and is then refused at admission with
    # approver_lacks_lead_or_admin_role -- the run is stopped, but somebody held approval
    # authority this repository never granted and nothing here could say who. That was
    # live for syz2026 through the two-day window ending 2026-07-30.
    #
    # Case-insensitive, the way the admin gate's reviewers are compared just above. GitHub
    # treats a login as case-insensitive, and an exact comparison would report drift that
    # is not there while saying nothing about the drift that is.
    declared = {str(login).lower() for login in roster["team_leads"]}
    undeclared = sorted(
        login for login in lead_team.member_logins if login.lower() not in declared
    )

    assert not undeclared, (
        f"on GitHub's {lead_team.team_slug} team and not in config/organization.yaml's "
        "team_leads, so each of these can release any team's routine run at the lead gate "
        f"while admission refuses the submission it released: {undeclared}"
    )


def test_a_lead_the_roster_declares_is_never_locked_out_of_the_lead_gate(
    lead_team: LeadTeamMembership,
    roster: dict[str, object],
) -> None:
    # The direction that withdraws authority the roster granted, which is a different
    # incident with a different fix and therefore a different test. A login here and not
    # on the team is a lead admission would accept and the gate will never release, so
    # even his own group's routine run waits on somebody else -- which is what
    # VS-code-cloud met through the same two-day window, from the other side of it.
    on_github = {login.lower() for login in lead_team.member_logins}
    absent = sorted(
        str(login) for login in roster["team_leads"] if str(login).lower() not in on_github
    )

    assert not absent, (
        "in config/organization.yaml's team_leads and not on GitHub's "
        f"{lead_team.team_slug} team, so each of these is a lead this platform authorizes "
        f"and the lead gate will never let through, their own group's run included: {absent}"
    )


def test_the_repository_holds_no_secret_a_branch_could_read(
    secrets: SecretInventory,
) -> None:
    # The one that must stay empty. A repository secret is readable by a workflow on any
    # branch, so a credential here is reachable from a branch nobody reviewed -- which is
    # the whole reason the rule exists rather than a preference for tidiness.
    assert secrets.repository_secret_names == ()
    assert secrets.organization_secret_names == ()
    assert secrets.dependabot_secret_names == ()


def test_phase_two_introduced_no_credential_at_all(secrets: SecretInventory) -> None:
    # Recorded because it was a live question rather than a foregone conclusion. The
    # fallback if the approvals endpoint had needed a fine-grained token was to store one
    # as an environment secret; the endpoint answered a GITHUB_TOKEN holding actions read,
    # so nothing was stored. This check starts satisfied and exists to keep it that way.
    assert all(names == () for names in secrets.environment_secret_names.values())
    assert set(secrets.environment_secret_names) == set(APPROVAL_ENVIRONMENT_NAMES)


def test_the_only_repository_variables_are_role_arns_and_the_region(
    secrets: SecretInventory,
) -> None:
    # Variables are not secrets and are recorded beside them because the criterion is
    # about what a workflow can read. An ARN carries an account id, which is why these are
    # variables rather than committed into a workflow file.
    #
    # Three more than this test knew about, found by re-capturing for the automatic gate
    # rather than by anybody looking. The capture behind it dated from 2026-07-27 and the
    # account had moved: the resolver, canceller and nightly reader roles all landed after
    # it, and infra/README.md records the last two as stacks 4 and 5 with their variables
    # set by hand. So this list grew for reasons that have nothing to do with the approval
    # gate, and the test is renamed off the count it used to carry, because a count is the
    # part of an assertion like this that ages.
    assert secrets.repository_variable_names == (
        "AWS_ADMISSION_ROLE_ARN",
        "AWS_IMAGE_RESOLVER_ROLE_ARN",
        "AWS_INFRA_DEPLOYER_ROLE_ARN",
        "AWS_NIGHTLY_READER_ROLE_ARN",
        "AWS_REGION",
        "AWS_RUN_CANCELLER_ROLE_ARN",
    )
