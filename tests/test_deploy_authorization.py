"""Who may start a deployment or a release by hand, held to the roster that decides it.

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

**``release-tag.yml`` was added to this module on 2026-08-06 and it is not a deploy.** It
assumes no role and reconciles no stack. It was the last Run button here in front of a write
nobody can take back -- ``git tag`` and ``gh release create`` under ``contents: write``, on a
repository with no rulesets and no tag protection -- and the tag it publishes is what every
installed CLI compares its own version against. It is held to the same roster and the same
three mutations, and deliberately not held to the deploy workflows' wording: theirs names
deploying, and telling somebody refused a release to open a pull request against a template
would be advice about the wrong thing.
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

#: The fourth workflow behind a Run button that writes something nobody can take back, and
#: the one this module did not cover until 2026-08-06. It is not a deploy: it assumes no AWS
#: role and reconciles no stack, so it is not in ``DEPLOY_WORKFLOWS`` and does not have to
#: match those three word for word. What it does is `git tag` and `gh release create` under
#: ``contents: write``, on a repository with no rulesets and no tag protection -- and the tag
#: it publishes is what every installed CLI compares its own version against. It is held to
#: the same roster by the tests below.
RELEASE_WORKFLOW = "release-tag.yml"

RELEASE_GUARD_STEP_NAME = "Refuse a hand-started release from somebody who may not make one"


def workflow(name: str) -> dict[str, Any]:
    loaded = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def only_job(name: str) -> dict[str, Any]:
    jobs = workflow(name)["jobs"]
    assert len(jobs) == 1, f"{name} grew a second job and this module still reads one"
    return next(iter(jobs.values()))


def guard_step(name: str, step_name: str = GUARD_STEP_NAME) -> dict[str, Any]:
    steps = only_job(name)["steps"]
    matching = [step for step in steps if step.get("name") == step_name]
    assert matching, f"{name} has no step named {step_name!r}"
    return matching[0]


def accepted_actors(body: str) -> frozenset[str]:
    """The logins a ``case`` arm lets through, read out of the guard's own script."""
    arms = frozenset(
        line.strip().removesuffix(" ;;").strip()
        for line in body.splitlines()
        if line.strip().endswith(") ;;")
    )
    return frozenset(part for entry in arms for part in entry.rstrip(")").split("|"))


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
    assert accepted_actors(guard_step(name)["run"]) == frozenset(admins())


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


def test_cutting_a_release_by_hand_meets_the_same_roster_a_deploy_does() -> None:
    """THE LAST UNGUARDED RUN BUTTON IN THIS REPOSITORY, AND THE ONE THAT PUBLISHES.

    ``release-tag.yml`` carried ``workflow_dispatch`` and ``contents: write`` and no guard at
    all. Thirty-six people hold write here, there are no rulesets and no tag protection, so a
    dispatch was one person's click away from a published tag and a published release. Every
    installed ``edullm`` compares its own version against that tag, so a release nobody meant
    to cut tells the field it is current or that it is behind.

    Held to the roster rather than to a list here, for the reason the deploy tests are: a
    name dropped from ``config/organization.yaml`` otherwise keeps its button.
    """
    guard = guard_step(RELEASE_WORKFLOW, RELEASE_GUARD_STEP_NAME)

    assert accepted_actors(guard["run"]) == frozenset(admins())


def test_the_release_guard_refuses_before_the_checkout_and_fails_rather_than_skipping() -> None:
    """The same two mutations the deploy guard is held against, in the file that tags.

    Below the checkout it is a refusal that has already done work; as a job-level ``if`` it
    is a skip, which renders beside a tick and tells the person who pressed the button that
    they succeeded.
    """
    job = only_job(RELEASE_WORKFLOW)

    assert job["steps"][0]["name"] == RELEASE_GUARD_STEP_NAME
    assert "if" not in job
    assert (
        guard_step(RELEASE_WORKFLOW, RELEASE_GUARD_STEP_NAME)["if"]
        == "github.event_name == 'workflow_dispatch'"
    )


def test_a_merge_still_releases_without_meeting_the_release_guard() -> None:
    """Mutation: guard every event rather than the dispatch.

    A push to these paths has already been through the check that refuses a change declaring
    a version no release has, and the person who clicks merge need not be an admin. Guarding
    the push would strand every merge that earns a release, and the whole reason this
    workflow exists is that releases cut by somebody remembering to are releases that stop.
    """
    condition = guard_step(RELEASE_WORKFLOW, RELEASE_GUARD_STEP_NAME)["if"]

    assert "workflow_dispatch" in condition
    assert condition.startswith("github.event_name ==")


def test_a_refused_release_says_who_was_refused_and_what_to_do_instead() -> None:
    """It also must not tell somebody to deploy, which a copied deploy refusal would."""
    body = guard_step(RELEASE_WORKFLOW, RELEASE_GUARD_STEP_NAME)["run"]

    assert "release_dispatch_not_authorized:${ACTOR}" in body
    assert "pull request" in body
    assert "Deploying infrastructure" not in body


def test_nothing_else_publishes_a_release_from_a_button_this_module_has_not_read() -> None:
    """Mutation: a second workflow that cuts a release, dispatchable and unguarded.

    ``RELEASE_WORKFLOW`` is one hand-written name and on its own cannot notice a second file.
    This is the half that can, and it reads the property that matters rather than a filename:
    a workflow that creates a GitHub release and offers a Run button is one somebody can
    publish from, and it either carries a guard or it is this test going red.

    ``refuse-a-tag-above-the-ceiling.yml`` deliberately does not match. It fires on a tag
    push, has no dispatch and creates nothing -- it is the control for a tag pushed from a
    laptop, which is the case a dispatch guard cannot see.
    """
    publishes = set()
    for path in WORKFLOWS.glob("*.yml"):
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        triggers = loaded.get("on", loaded.get(True)) or {}
        if not isinstance(triggers, dict) or "workflow_dispatch" not in triggers:
            continue
        # `.get("steps", [])` because a job may be a call to a reusable workflow instead,
        # which has no steps of its own here.
        scripts = "".join(
            str(step.get("run", ""))
            for job in loaded["jobs"].values()
            for step in job.get("steps", [])
        )
        if "gh release create" in scripts:
            publishes.add(path.name)

    assert publishes == {RELEASE_WORKFLOW}


def test_no_deploy_workflow_exists_that_this_module_does_not_know_about() -> None:
    """Mutation: add deploy-phase5-whatever.yml and guard it nowhere.

    ``DEPLOY_WORKFLOWS`` is a hand-written list, so on its own it cannot notice a fourth
    file. This is the half that can: anything named ``deploy-*.yml`` is a deploy workflow,
    and one missing from the list above is one nothing here checks.
    """
    on_disk = sorted(path.name for path in WORKFLOWS.glob("deploy-*.yml"))

    assert on_disk == sorted(DEPLOY_WORKFLOWS)
