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
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from edullm_platform.phase2_evidence import (
    APPROVAL_ENVIRONMENT_NAMES,
    EnvironmentInventory,
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
def roster() -> dict[str, object]:
    loaded: dict[str, object] = yaml.safe_load(
        (PROJECT_ROOT / "config" / "organization.yaml").read_text(encoding="utf-8")
    )
    return loaded


def test_both_approval_environments_exist_and_no_third_one_does(
    environments: EnvironmentInventory,
) -> None:
    # All of them, not only the two expected. An environment is auto-created with no
    # protection rules at all by anyone who names one in a workflow file, and everyone who
    # can submit holds the write access that allows it. The trust policy enumerates two
    # subjects and would refuse a third, so such an environment could not reach AWS -- but
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


def test_self_review_is_deliberately_permitted_on_both_gates(
    environments: EnvironmentInventory,
) -> None:
    # Asserted as false on purpose, so that somebody "hardening" it has to read why. Leads
    # self-authorizing routine runs and admins approving their own exceptions are both
    # intended by the global constraints. The prohibition that does apply -- a member
    # cannot approve their own submission -- is enforced by members not being reviewers,
    # and independently by evaluate_authorization.
    for environment in environments.environments:
        assert environment.prevent_self_review is False, environment.name


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


def test_the_only_repository_variables_are_the_two_role_arns_and_the_region(
    secrets: SecretInventory,
) -> None:
    # Variables are not secrets and are recorded beside them because the criterion is
    # about what a workflow can read. An ARN carries an account id, which is why these are
    # variables rather than committed into a workflow file.
    assert secrets.repository_variable_names == (
        "AWS_ADMISSION_ROLE_ARN",
        "AWS_INFRA_DEPLOYER_ROLE_ARN",
        "AWS_REGION",
    )
