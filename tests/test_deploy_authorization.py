"""Who may start a deployment by hand, held to the roster that decides it.

``workflow_dispatch`` is offered to everybody holding write, and write stopped meaning
"an admin" the day the pilot opened. The three deploy workflows assume a role that
reconciles CloudFormation stacks in the sandbox account, so between the Run button and
that role there needs to be something, and what is there is a guard step enumerating the
admins.

**Three copies of that step, deduplicated by this module rather than by an abstraction.**
A composite action would give one copy and would move a security control out of the file
it protects, into a path CODEOWNERS does not currently cover. The precedent for going the
other way is already in this repository: the Batch job queue name appears in three places
no CloudFormation reference connects, and ``tests/test_phase3_infrastructure.py`` compares
them against each other rather than introducing an indirection. This does the same for the
same reason.

The mutation each test here catches is the one that produces no error anywhere: a fourth
deploy workflow added without the step, an admin removed from the roster and left in the
workflows, or the guard demoted to a job-level ``if`` that reports a refusal as a skip.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from edullm_platform.config import load_yaml
from edullm_platform.contracts.inventory import OrganizationInventory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = PROJECT_ROOT / ".github" / "workflows"
ORGANIZATION = PROJECT_ROOT / "config" / "organization.yaml"

#: Every workflow that assumes the infrastructure deployer role. Listed rather than
#: discovered, because a discovery rule that found nothing would pass silently -- and the
#: failure this module exists to prevent is a deploy workflow nobody guarded.
DEPLOY_WORKFLOWS = (
    "deploy-phase1-ecr.yml",
    "deploy-phase2-admission.yml",
    "deploy-phase3-batch.yml",
)

GUARD_STEP_NAME = "Refuse a hand-started deploy from somebody who may not make one"


def workflow(name: str) -> dict[str, Any]:
    loaded = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def only_job(name: str) -> dict[str, Any]:
    jobs = workflow(name)["jobs"]
    assert len(jobs) == 1, f"{name} grew a second job and this module still reads one"
    return next(iter(jobs.values()))


def guard_step(name: str) -> dict[str, Any]:
    steps = only_job(name)["steps"]
    matching = [step for step in steps if step.get("name") == GUARD_STEP_NAME]
    assert matching, f"{name} has no step named {GUARD_STEP_NAME!r}"
    return matching[0]


def admins() -> tuple[str, ...]:
    return load_yaml(ORGANIZATION, OrganizationInventory).admins


@pytest.mark.parametrize("name", DEPLOY_WORKFLOWS)
def test_every_deploy_workflow_refuses_a_dispatch_before_it_does_anything(name: str) -> None:
    """Mutation: move the guard below the checkout, or below the credentials step.

    A refusal that arrives after the job has assumed a role has already done the thing it
    was refusing. First in the job is the only position where the guard is worth having.
    """
    steps = only_job(name)["steps"]

    assert steps[0]["name"] == GUARD_STEP_NAME


@pytest.mark.parametrize("name", DEPLOY_WORKFLOWS)
def test_a_refused_dispatch_fails_rather_than_reporting_itself_as_skipped(name: str) -> None:
    """Mutation: hoist the actor test into a job-level ``if``.

    That version works, in the sense that nothing deploys. It reports the job as skipped,
    which renders beside a tick in the checks list, so the person who pressed the button is
    told they succeeded and the log says nothing about why. The guard runs on every
    dispatch and decides inside the step, which is what makes the refusal visible.
    """
    job = only_job(name)

    assert "if" not in job
    assert guard_step(name)["if"] == "github.event_name == 'workflow_dispatch'"


@pytest.mark.parametrize("name", DEPLOY_WORKFLOWS)
def test_a_push_to_main_deploys_without_meeting_the_guard(name: str) -> None:
    """Mutation: guard every event rather than the dispatch.

    A push to main has already been through the required checks on the template and on the
    workflow applying it, and the person who clicks merge need not be an admin. Guarding
    that path would strand an infrastructure change with no way to land it, and the failure
    would name the merger rather than the rule.

    The argument was written when a code-owner review stood in front of that merge, and the
    review came off main on 2026-08-05. What is left in front of it is the two status
    checks, which is a weaker thing to rest a deploy on and is still the reason not to guard
    the push. The three workflow files say the same in a comment and are not edited here,
    because a change to any of them is what their own push trigger fires a deploy on.
    """
    condition = guard_step(name)["if"]

    assert "workflow_dispatch" in condition
    assert condition.startswith("github.event_name ==")


@pytest.mark.parametrize("name", DEPLOY_WORKFLOWS)
def test_the_actors_a_deploy_workflow_accepts_are_the_roster_admins(name: str) -> None:
    """Reads both sides. Mutation: remove an admin from the roster and not the workflows.

    The roster is where admin is decided and the workflow is where it is enforced, and
    nothing connects them -- a name dropped from ``config/organization.yaml`` keeps its
    deploy button until somebody remembers these three files exist.
    """
    body = guard_step(name)["run"]
    accepted = frozenset(
        line.strip().removesuffix(" ;;").strip()
        for line in body.splitlines()
        if line.strip().endswith(") ;;")
    )
    accepted = frozenset(part for entry in accepted for part in entry.rstrip(")").split("|"))

    assert accepted == frozenset(admins())


@pytest.mark.parametrize("name", DEPLOY_WORKFLOWS)
def test_a_refusal_says_who_was_refused_and_what_to_do_instead(name: str) -> None:
    """A refusal naming no next step gets read as a broken workflow and reported as one."""
    body = guard_step(name)["run"]

    assert "deploy_dispatch_not_authorized:${ACTOR}" in body
    assert "pull request" in body


def test_the_three_deploy_workflows_carry_the_same_guard_word_for_word() -> None:
    """Mutation: fix a message in one file and not the other two.

    Three copies are acceptable only while they are provably one thing. This is the test
    that makes them provably one thing, and it is why no composite action is needed.
    """
    bodies = {name: guard_step(name)["run"] for name in DEPLOY_WORKFLOWS}

    assert len(set(bodies.values())) == 1, f"the guards have drifted apart: {sorted(bodies)}"


def test_no_deploy_workflow_exists_that_this_module_does_not_know_about() -> None:
    """Mutation: add deploy-phase5-whatever.yml and guard it nowhere.

    ``DEPLOY_WORKFLOWS`` is a hand-written list, so on its own it cannot notice a fourth
    file. This is the half that can: anything named ``deploy-*.yml`` is a deploy workflow,
    and one missing from the list above is one nothing here checks.
    """
    on_disk = sorted(path.name for path in WORKFLOWS.glob("deploy-*.yml"))

    assert on_disk == sorted(DEPLOY_WORKFLOWS)
