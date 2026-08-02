"""The report that answers whether a person can submit a run, and what is stopping them.

Onboarding is four systems and one file, and the file is the only one anything here can
read. So the ways this report can be wrong are ways of being confidently wrong about
somebody else's account, and they are what these tests are about.

It could report a step as done because a field exists rather than because it holds
something, which turns a run that will log under the platform's name into a green tick. It
could report an optional step as blocking, which sends a person to chase a W&B invitation
they do not need before their first run. It could report the two halves of approval
authority as one step, when a lead missing from the GitHub team and a login on the GitHub
team the roster never granted are opposite incidents with opposite fixes, and the admin gate
is the same pair again held somewhere else. It could build the list from the roster and
never see the person the organization holds and the roster does not, who is the one whose
submission gets all the way to a reviewer before being refused. And it could say a step is
missing without saying who has to do what and where, which is the manual checklist again
with extra steps.
"""

from __future__ import annotations

import copy
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from report_onboarding_readiness import (
    EXIT_FOUND_MISSING_STEPS,
    EXIT_UNUSABLE,
    STEP_ADMIN_ON_GITHUB,
    STEP_ADMIN_ON_ROSTER,
    STEP_LEAD_ON_GITHUB,
    STEP_LEAD_ON_ROSTER,
    STEP_LOGIN_RESOLVES,
    STEP_ORGANIZATION,
    STEP_ROSTER,
    STEP_TEAM,
    STEP_TEAM_ON_GITHUB,
    STEP_WANDB,
    STEP_WRITE_ACCESS,
    GitHubAccess,
    PersonReadiness,
    ReportInputError,
    main,
    parse_github_access,
    readiness,
    render,
)

from edullm_platform.config import load_yaml
from edullm_platform.contracts.authorization import evaluate_authorization
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.policy import ApprovalPolicy, RequestFacts
from tests.policy_support import ROUTINE_RATE

#: Three people rather than thirty-five, and each one carries a different case. Frank has
#: done everything and leads a group. Hiya leads a group too, so the two lead directions can
#: be varied against each other. Aryan is on the roster with no W&B account, which is the
#: shipped roster's own example of the step that costs attribution and blocks nothing.
ROSTER: dict[str, Any] = {
    "admins": ["philote-dev"],
    "team_leads": ["philote-dev", "hiyasvyas"],
    "members": [
        {
            "github_login": "philote-dev",
            "display_name": "Frank Gonzalez",
            "wandb_username": "philote",
        },
        {"github_login": "hiyasvyas", "display_name": "Hiya Vyas", "wandb_username": "hiyasvyas"},
        {"github_login": "aryanjverma", "display_name": "Aryan Verma"},
    ],
    "pilot_repositories": ["OLMo-core"],
}

EVERYBODY = ["philote-dev", "hiyasvyas", "aryanjverma"]

#: What the roster will look like once somebody declares the teams. Aryan is deliberately
#: left out of it, so the same constant serves both directions: a person the catalog places
#: and a person it does not.
TEAM_BINDINGS: dict[str, Any] = {
    "teams": [
        {
            "team_id": "memory-split",
            "github_team_slug": "memory-split",
            "lead_logins": ["hiyasvyas"],
            "member_logins": ["philote-dev"],
            "s3_namespace": "sbsandbox-intern-memory-split",
            "wandb_entity": "edu-llm-memory-split",
        }
    ]
}


def inventory(**overrides: Any) -> OrganizationInventory:
    payload = copy.deepcopy(ROSTER)
    payload.update(copy.deepcopy(overrides))
    return OrganizationInventory.model_validate(payload)


def access(**overrides: Any) -> GitHubAccess:
    """The GitHub half, built through the parser the tool reads a gathered file with.

    Through the parser rather than by constructing the dataclass, so that every test also
    exercises the normalization the report depends on: this side is written by a machine
    reading an API and the roster is written by people spelling their own names.
    """
    document: dict[str, Any] = {
        "observed_at": "2026-08-01T09:00:00+00:00",
        "organization": "edu-llm",
        "repository": "platform",
        "organization_members": list(EVERYBODY),
        "repository_writers": list(EVERYBODY),
        "team_members": {"team-leads": ["philote-dev", "hiyasvyas"]},
        "unresolvable_logins": [],
        "admin_gate_reviewers": ["philote-dev"],
        "admin_gate_review_teams": [],
    }
    document.update(overrides)
    return parse_github_access(document)


def person(people: list[PersonReadiness], github_login: str) -> PersonReadiness:
    return next(entry for entry in people if entry.github_login == github_login)


def missing_names(entry: PersonReadiness) -> set[str]:
    return {step.name for step in entry.missing}


def test_a_person_who_has_done_every_step_is_reported_as_missing_nothing() -> None:
    """The baseline, without which every other assertion here could be a tool that says no.

    Frank is on the roster with a W&B account recorded, in the organization, holds write, is
    on a team both lists agree about, and is a lead on both lists. There is nothing left for
    anybody to do about him, and a report that still found something would be one nobody
    could act on.
    """
    people = readiness(
        inventory(team_bindings=TEAM_BINDINGS),
        access(
            team_members={
                "team-leads": ["philote-dev", "hiyasvyas"],
                "memory-split": ["philote-dev", "hiyasvyas"],
            }
        ),
    )

    frank = person(people, "philote-dev")
    assert frank.missing == ()
    assert frank.blocked is False


def test_a_person_absent_from_the_roster_is_reported_as_needing_a_pull_request() -> None:
    """Mutation: build the report from the roster, so the organization's extra people vanish.

    Somebody the GitHub organization holds and `config/organization.yaml` does not can see
    the Run button and fill in the whole form, and every submission they dispatch is
    refused. A report built from the roster alone cannot see that person at all, which is
    the one reader who most needs to.
    """
    people = readiness(inventory(), access(organization_members=[*EVERYBODY, "mccorkel"]))

    stranger = person(people, "mccorkel")
    assert stranger.on_the_roster is False
    assert STEP_ROSTER in missing_names(stranger)
    assert stranger.blocked is True

    step = next(item for item in stranger.missing if item.name == STEP_ROSTER)
    assert "pull request" in step.action
    assert "before a reviewer is asked" in step.action


def test_a_person_on_the_roster_and_not_in_the_organization_is_sent_to_an_owner() -> None:
    """Mutation: infer organization membership from the roster, which is the wrong direction.

    The roster is a file in this repository and organization membership is a list an owner
    edits in a browser. Nothing keeps the two in step, and the roster is the easier of the
    two to change, so a name can arrive here before the invitation does.
    """
    people = readiness(inventory(), access(organization_members=["philote-dev", "hiyasvyas"]))

    aryan = person(people, "aryanjverma")
    assert STEP_ORGANIZATION in missing_names(aryan)
    assert aryan.blocked is True

    step = next(item for item in aryan.missing if item.name == STEP_ORGANIZATION)
    assert "organization owner" in step.action
    assert "GitHub organization settings" in step.action


def test_a_person_in_the_organization_without_write_access_cannot_see_the_run_button() -> None:
    """Mutation: treat organization membership as write access.

    They are separate grants and the difference is the whole first gate. The submission form
    is a manual workflow and GitHub shows one only to people who can write, so somebody with
    membership and no write meets a page that does not exist rather than a refusal. Reporting
    them as ready would send them to look for a button nobody can show them.
    """
    people = readiness(inventory(), access(repository_writers=["philote-dev", "hiyasvyas"]))

    aryan = person(people, "aryanjverma")
    assert STEP_WRITE_ACCESS in missing_names(aryan)
    assert STEP_ORGANIZATION not in missing_names(aryan)
    assert aryan.blocked is True

    step = next(item for item in aryan.missing if item.name == STEP_WRITE_ACCESS)
    assert "write" in step.action
    assert "Collaborators and teams" in step.action


def test_a_blank_wandb_username_is_missing_attribution_and_does_not_block_a_run() -> None:
    """Mutation: report a missing W&B account as done because the field exists, or as blocking.

    Both directions are wrong and they are wrong in opposite ways. Reported as done, the
    person is told they are ready and their runs log under the platform's service account,
    which nothing warns them about and only they are placed to notice. Reported as blocking,
    they are sent to chase an invitation from a W&B owner before a first run that would have
    worked, and the step that genuinely stops runs is buried beside it.
    """
    people = readiness(inventory(), access())

    aryan = person(people, "aryanjverma")
    assert STEP_WANDB in missing_names(aryan)
    assert aryan.blocked is False

    step = next(item for item in aryan.missing if item.name == STEP_WANDB)
    assert step.blocking is False
    assert "Weights and Biases owner" in step.action
    assert "service account" in step.action


def test_a_lead_the_roster_names_and_github_does_not_is_reported_against_the_lead_gate() -> None:
    """The direction that withdraws authority the roster granted.

    `team_leads` is what admission reads and GitHub's `team-leads` team is the only reviewer
    on the `run-approval-lead` environment. A lead on the first and not the second is
    authorized by this platform and locked out by the gate, their own group's run included,
    and the fix is an owner adding them to a team in the organization settings.
    """
    people = readiness(inventory(), access(team_members={"team-leads": ["philote-dev"]}))

    hiya = person(people, "hiyasvyas")
    assert STEP_LEAD_ON_GITHUB in missing_names(hiya)
    assert STEP_LEAD_ON_ROSTER not in missing_names(hiya)

    step = next(item for item in hiya.missing if item.name == STEP_LEAD_ON_GITHUB)
    assert "run-approval-lead" in step.action
    assert "organization owner" in step.action


def test_a_lead_github_names_and_the_roster_does_not_is_a_different_fix() -> None:
    """Mutation: fold the two directions into one step about the lists disagreeing.

    This is the direction that widens authority, and it has a different remedy from the one
    above: somebody can release any team's routine run at the gate and admission then refuses
    the submission with `approver_lacks_lead_or_admin_role`, which reads as a permissions bug
    rather than as a list being out of date. A single step saying the lists disagree would
    tell whoever reads it neither which incident this is nor which of the two lists to edit.
    """
    people = readiness(
        inventory(team_leads=["philote-dev"]),
        access(team_members={"team-leads": ["philote-dev", "hiyasvyas"]}),
    )

    hiya = person(people, "hiyasvyas")
    assert STEP_LEAD_ON_ROSTER in missing_names(hiya)
    assert STEP_LEAD_ON_GITHUB not in missing_names(hiya)

    step = next(item for item in hiya.missing if item.name == STEP_LEAD_ON_ROSTER)
    assert "approver_lacks_lead_or_admin_role" in step.action


def test_an_admin_the_roster_names_and_the_admin_gate_does_not_is_reported_separately() -> None:
    """Mutation: check the admin's authority against the lead team, or not at all.

    An exception run stops at `run-approval-admin`, whose reviewers are two named users
    rather than a team, and admission accepts an exception approval only from `admins` in
    the roster. An admin the roster names and the environment does not list is authorized
    by the platform and never offered the button, so an exception request waits on somebody
    who cannot be asked. It is held on a different screen from the lead team and it is not
    the same finding.
    """
    people = readiness(inventory(admins=["philote-dev", "hiyasvyas"]), access())

    hiya = person(people, "hiyasvyas")
    assert STEP_ADMIN_ON_GITHUB in missing_names(hiya)
    assert STEP_ADMIN_ON_ROSTER not in missing_names(hiya)

    step = next(item for item in hiya.missing if item.name == STEP_ADMIN_ON_GITHUB)
    assert step.blocking is False
    assert "run-approval-admin" in step.action
    assert "Environments in the GitHub repository settings" in step.action


def test_an_admin_gate_reviewer_the_roster_does_not_name_is_the_opposite_finding() -> None:
    """Mutation: fold the admin directions together, or into the lead ones.

    A reviewer the environment lists and `admins` does not name can release an exception
    run, and admission then refuses it with `approver_lacks_admin_role` after the reviewer
    has already been asked. `approver_lacks_lead_or_admin_role` is what the lead gate
    produces, so naming the wrong one would send somebody to the wrong list.
    """
    people = readiness(
        inventory(), access(admin_gate_reviewers=["philote-dev", "hiyasvyas"])
    )

    hiya = person(people, "hiyasvyas")
    assert STEP_ADMIN_ON_ROSTER in missing_names(hiya)
    assert STEP_ADMIN_ON_GITHUB not in missing_names(hiya)

    step = next(item for item in hiya.missing if item.name == STEP_ADMIN_ON_ROSTER)
    assert "approver_lacks_admin_role" in step.action
    assert "approver_lacks_lead_or_admin_role" not in step.action


def test_a_team_reviewing_the_admin_gate_counts_as_its_members() -> None:
    """Mutation: read the admin gate's reviewers as user logins only.

    GitHub lets an environment name either a user or a team, and the capture this reads
    keeps the distinction because a team reviewer is a different control. Whether a
    particular admin can open the gate is still a question about people, so a team named
    there has to be resolved to its members. Read as logins, a team reviewer would report
    every roster admin as having lost their authority.
    """
    people = readiness(
        inventory(admins=["philote-dev", "hiyasvyas"]),
        access(
            admin_gate_reviewers=[],
            admin_gate_review_teams=["platform-admins"],
            team_members={
                "team-leads": ["philote-dev", "hiyasvyas"],
                "platform-admins": ["philote-dev", "hiyasvyas"],
            },
        ),
    )

    assert STEP_ADMIN_ON_GITHUB not in missing_names(person(people, "philote-dev"))
    assert STEP_ADMIN_ON_GITHUB not in missing_names(person(people, "hiyasvyas"))


def test_a_member_who_is_not_a_lead_is_not_reported_as_missing_approval_authority() -> None:
    """Mutation: give everybody the approval steps, so being an ordinary member reads as a gap.

    Approval authority is granted deliberately and organization-wide here, so most of the
    roster not having it is the intended state rather than an outstanding task. Listing it
    against every name would bury the handful of people it is genuinely about.
    """
    people = readiness(inventory(), access())

    aryan = person(people, "aryanjverma")
    assert STEP_LEAD_ON_GITHUB not in {step.name for step in aryan.steps}
    assert STEP_LEAD_ON_ROSTER not in {step.name for step in aryan.steps}
    assert STEP_ADMIN_ON_GITHUB not in {step.name for step in aryan.steps}
    assert STEP_ADMIN_ON_ROSTER not in {step.name for step in aryan.steps}


def test_a_roster_login_github_cannot_resolve_is_itself_a_finding() -> None:
    """Mutation: treat a login GitHub has never heard of as somebody who was not invited yet.

    A renamed account leaves its old login answering 404, and that login is the key every
    roster lookup joins on, so the person behind it is silently excluded from everything
    derived from the file rather than being visibly absent from one thing. `zsophiaaa` was
    `mathishard17` until she was not, and the old spelling still answers 404 today.
    """
    people = readiness(
        inventory(),
        access(
            organization_members=["philote-dev", "hiyasvyas"],
            repository_writers=["philote-dev", "hiyasvyas"],
            unresolvable_logins=["aryanjverma"],
        ),
    )

    aryan = person(people, "aryanjverma")
    assert STEP_LOGIN_RESOLVES in missing_names(aryan)
    assert aryan.blocked is True
    assert "404" in aryan.missing[0].action
    assert "pull request" in aryan.missing[0].action


def test_nothing_else_is_claimed_about_a_login_that_does_not_resolve() -> None:
    """Mutation: go on reporting the other steps for a login that belongs to nobody.

    Every one of them would report as missing, because an account that does not exist is in
    no organization and on no team, and each line would send somebody to grant access to
    nobody. Correcting the login is the whole of what there is to do, and the rest is
    answerable once it is right.
    """
    people = readiness(inventory(), access(unresolvable_logins=["aryanjverma"]))

    aryan = person(people, "aryanjverma")
    assert [step.name for step in aryan.steps] == [STEP_LOGIN_RESOLVES]


def test_the_empty_team_catalog_blocks_nobody_and_is_reported_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: report the absent team catalog as a gate, or repeat it under every name.

    A roster declaring no team at all, which the shipped one no longer is: it declares six.
    The case is kept because it is the degenerate end of the same rule and the one where the
    right thing to say differs, being about the file rather than about any person.

    Nobody is refused for a team nothing can verify, so it is missing rather than blocking.
    It is also true of everybody equally, which makes it a fact about the platform, and
    repeated under thirty-five headings it is how a report stops being read.
    """
    people = readiness(inventory(), access())

    for entry in people:
        assert STEP_TEAM in missing_names(entry)
        assert entry.blocked is False

    report = render(people, access())
    assert "Missing for everybody" in report
    assert report.count("No team is declared at all") == 1
    assert capsys.readouterr().err == ""


def test_a_declared_team_does_not_block_somebody_no_team_lists() -> None:
    """Mutation: read whether the team blocks off the catalog instead of off the person.

    This asserted the opposite until the six teams were declared, and it was right about the
    `evaluate_authorization` of the time: any catalog at all switched the membership check
    on for everybody, so one populated team refused all thirty-five people. That is the
    reason the check became per submitter, and this report has to follow it there. Reading
    the catalog would have reported every person on the roster as blocked, out of a run that
    is in fact admitted, on the day the teams landed.

    So the catalog is populated here and `aryanjverma` is in none of it. The step is still
    missing, because a team is still not recorded for them, and it is not a gate.
    """
    people = readiness(inventory(team_bindings=TEAM_BINDINGS), access())

    aryan = person(people, "aryanjverma")
    assert STEP_TEAM in missing_names(aryan)
    assert aryan.blocked is False

    step = next(item for item in aryan.missing if item.name == STEP_TEAM)
    assert "No submission is refused over this" in step.action
    assert "team_verified: false" in step.action
    assert "pull request" in step.action


def test_the_step_is_not_a_gate_because_admission_admits_the_person_it_describes() -> None:
    """The claim in the action text, put to the function that would have to refuse them.

    The test above pins what the report says. This pins that it is true, by asking
    `evaluate_authorization` directly rather than restating the rule: a submitter no team
    lists, under a populated catalog, claiming a team they are not recorded in. If that ever
    starts being refused, the report is telling thirty-five people a run will go through
    that will not, and this fails rather than the wording drifting quietly out of date.
    """
    roster = inventory(team_bindings=TEAM_BINDINGS)
    decision = evaluate_authorization(
        "aryanjverma",
        "philote-dev",
        RequestFacts(
            claimed_team="memory-split",
            repository_registered=True,
            dataset_registered=True,
            compute_profile_registered=True,
            immutable_revision=True,
            immutable_image=True,
            image_scan_reviewed=True,
            estimated_cost_usd=Decimal(10),
            maximum_runtime_hours=Decimal(1),
            maximum_attempts=1,
        ),
        load_yaml(PROJECT_ROOT / "config" / "policy.yaml", ApprovalPolicy),
        roster,
        hourly_rate_usd=ROUTINE_RATE,
    )

    assert roster.teams_for_member("aryanjverma") == ()
    assert decision.granted is True
    assert decision.team_verified is False


def test_a_team_the_roster_declares_and_github_does_not_hold_is_reported_without_blocking() -> None:
    """Mutation: report the GitHub team as a gate, or not at all.

    The submission path reads the roster and never the GitHub team, so a person the roster
    puts on a team and GitHub does not can submit and be admitted. What it costs is that the
    two lists disagree with nothing saying so, which is the same shape as the lead gate and
    the reason that one went unnoticed for two days.
    """
    people = readiness(inventory(team_bindings=TEAM_BINDINGS), access())

    frank = person(people, "philote-dev")
    assert STEP_TEAM not in missing_names(frank)
    assert STEP_TEAM_ON_GITHUB in missing_names(frank)

    step = next(item for item in frank.missing if item.name == STEP_TEAM_ON_GITHUB)
    assert step.blocking is False
    assert "memory-split" in step.action
    assert "GitHub organization settings" in step.action


def test_a_login_is_matched_however_either_side_spelled_its_case() -> None:
    """Mutation: compare the two sides as written.

    GitHub treats a login case-insensitively, this file is written by people spelling their
    own names, and the gathered side is written by a machine reading an API. A comparison
    that was exact would report somebody as missing every GitHub step because of a capital
    letter, which is a finding about nothing that reads exactly like a finding about access.

    The team slugs are spelled loudly here for the same reason on the other axis. A slug is
    lowercase by contract on the roster side and has no contract on it at all in a gathered
    file, so the fold has to happen as that file is read rather than at each comparison.
    """
    people = readiness(
        inventory(team_bindings=TEAM_BINDINGS),
        access(
            organization_members=["PHILOTE-DEV", "HiyasVyas", "AryanJVerma"],
            repository_writers=["PHILOTE-DEV", "HiyasVyas", "AryanJVerma"],
            team_members={
                "Team-Leads": ["Philote-Dev", "HIYASVYAS"],
                "Memory-Split": ["Philote-Dev", "HIYASVYAS"],
            },
            admin_gate_review_teams=["Team-Leads"],
            admin_gate_reviewers=[],
        ),
    )

    assert len(people) == 3
    assert person(people, "philote-dev").missing == ()


def test_every_missing_step_names_somebody_to_act_and_the_system_they_act_in() -> None:
    """THE POINT OF THE TOOL. Mutation: report a step as missing and leave the action empty.

    A report that says a step is missing and stops is the manual checklist with extra steps:
    the reader still has to know that a W&B invitation is an owner action inside W&B, that
    write access is a repository setting, and that only two of these are a pull request. Each
    action therefore has to name who does it and where, and that is asserted over every step
    the tool can produce rather than over the ones that were convenient to write.
    """
    people = readiness(
        inventory(team_bindings=TEAM_BINDINGS),
        access(
            organization_members=["philote-dev", "mccorkel"],
            repository_writers=["philote-dev"],
            team_members={"team-leads": ["mccorkel"], "memory-split": []},
            unresolvable_logins=["aryanjverma"],
            admin_gate_reviewers=["mccorkel"],
        ),
    )
    produced = {step.name for entry in people for step in entry.missing}
    assert produced == {
        STEP_LOGIN_RESOLVES,
        STEP_ORGANIZATION,
        STEP_WRITE_ACCESS,
        STEP_ROSTER,
        STEP_TEAM,
        STEP_TEAM_ON_GITHUB,
        STEP_WANDB,
        STEP_LEAD_ON_GITHUB,
        STEP_LEAD_ON_ROSTER,
        STEP_ADMIN_ON_GITHUB,
        STEP_ADMIN_ON_ROSTER,
    }, "a step this tool can report is not covered by the assertions below"

    for entry in people:
        for step in entry.missing:
            assert any(actor in step.action for actor in ("owner", "admin", "pull request")), (
                f"{step.name} does not say who has to act: {step.action}"
            )
            assert any(
                system in step.action
                for system in ("GitHub", "Weights and Biases", "this repository")
            ), f"{step.name} does not name the system it happens in: {step.action}"


def test_the_report_names_the_person_beside_the_action_rather_than_only_the_login() -> None:
    """A report read by a human setting somebody up, who knows them by name.

    The login is what every list joins on and is kept for that reason, and it is not what
    the person asking "has Aryan been set up" is holding in their head.
    """
    report = render(readiness(inventory(), access()), access())

    assert "Aryan Verma (`aryanjverma`)" in report
    assert "Weights and Biases owner adds `aryanjverma`" in report


def test_the_report_says_nobody_is_here_rather_than_printing_an_empty_document() -> None:
    """A report with no people should read as an answer, not as a broken tool."""
    assert "nobody here to be ready" in render([], access())


def test_the_shipped_roster_reports_the_people_nothing_can_attribute() -> None:
    """Six people have no W&B account, and the report is where that is said out loud.

    The roster leaves them blank deliberately, because a guessed login produces a run that
    logs as the service account and looks exactly like a correctly attributed one. What was
    missing is anywhere that names whose runs are affected, and this is it. The GitHub half is
    synthesized as everything being right, so the only thing this can find is the gap the
    roster itself records.

    `aryanjverma` used to be the example here and is now the counter-example. He submitted
    both of the runs Phase 5 rests on, the roster called him unattributable, and the `eduLLM`
    entity had held `aryan-jaden-verma` under an exact display-name match all along. Asserted
    from the roster rather than against a name written twice, so that recording one of the six
    moves this test without editing it.
    """
    shipped = load_yaml(PROJECT_ROOT / "config" / "organization.yaml", OrganizationInventory)
    logins = [member.github_login for member in shipped.members]
    # The GitHub half is synthesized as everything being right, which is what leaves the W&B
    # gap as the only thing this test can find. Synthesizing only `team-leads` was enough
    # while no group recorded anyone, because a person on no group is asked for no GitHub
    # team. Recording the assignments gave every person a group and therefore a GitHub team
    # to be missing from, so the research teams are synthesized here too. Without this the
    # test reports a gap in GitHub rather than the gap in the roster it exists to name.
    synthesized_teams = {"team-leads": list(shipped.team_leads)}
    for team in shipped.team_bindings.teams:
        synthesized_teams[team.github_team_slug] = list(team.lead_logins + team.member_logins)
    people = readiness(
        shipped,
        access(
            organization_members=logins,
            repository_writers=logins,
            team_members=synthesized_teams,
        ),
    )
    unattributable = [
        member.github_login for member in shipped.members if member.wandb_username is None
    ]

    assert len(unattributable) == 7
    for login in unattributable:
        entry = person(people, login)
        # A superset rather than equality: BritishAmericqn is an admin, and the synthesized
        # access above grants nobody admin approval authority on GitHub, so he carries a
        # third step that has nothing to do with attribution.
        # W&B alone now. STEP_TEAM used to appear beside it because no group recorded
        # anybody, so every person was missing a research team as well as an account.
        # Recording the assignments closed that half for all thirty-four, which leaves this
        # test measuring only the gap it was named for.
        assert STEP_WANDB in missing_names(entry)
        assert STEP_TEAM not in missing_names(entry)
        # Unattributed is a whole run that works, so none of this blocks anybody.
        assert entry.blocked is False
    # He was the counter-example when the roster called him unattributable and W&B had held
    # his account all along. He is now the fully onboarded case, which is the point: nothing
    # is missing, and this line moves on its own if either half regresses.
    assert missing_names(person(people, "aryanjverma")) == set()


def test_gathered_facts_missing_a_key_are_refused_rather_than_read_as_an_empty_list() -> None:
    """Mutation: default an absent key to no logins.

    An empty list is a real answer and the loudest one this report can be given: it says
    nobody is in the organization, which puts every person in the blocked section. A key
    somebody misspelled would say exactly that while meaning nothing at all, and the report
    it produced would read as an emergency.
    """
    with pytest.raises(ReportInputError, match="organization_members"):
        parse_github_access(
            {
                "observed_at": "2026-08-01T09:00:00+00:00",
                "organization": "edu-llm",
                "repository": "platform",
                "repository_writers": [],
                "team_members": {},
                "unresolvable_logins": [],
            }
        )


def test_the_tool_exits_one_when_it_finds_a_missing_step(tmp_path: Path) -> None:
    """The repository's exit-code convention: 1 is having found what the tool looks for.

    Not an error in the tool, so that this can gate an onboarding checklist later without
    being rewritten. 2 is reserved for inputs it could not read, which is the answer that
    must never be confused with everybody being ready.
    """
    facts = tmp_path / "github-access.json"
    facts.write_text(
        json.dumps(
            {
                "observed_at": "2026-08-01T09:00:00+00:00",
                "organization": "edu-llm",
                "repository": "platform",
                "organization_members": [],
                "repository_writers": [],
                "team_members": {},
                "unresolvable_logins": [],
                "admin_gate_reviewers": [],
                "admin_gate_review_teams": [],
            }
        ),
        encoding="utf-8",
    )
    report = tmp_path / "report.md"

    assert main(["--github-access", str(facts), "--output", str(report)]) == (
        EXIT_FOUND_MISSING_STEPS
    )
    assert "Cannot have a run succeed yet" in report.read_text(encoding="utf-8")


def test_unreadable_inputs_exit_two_rather_than_reporting_that_nothing_is_missing(
    tmp_path: Path,
) -> None:
    """Mutation: report an unreadable file as an empty organization.

    The two answers must not look alike. "Nobody is missing anything" and "the file did not
    parse" would both come back as a clean exit, and the second is the one where nobody has
    been checked at all.
    """
    facts = tmp_path / "github-access.json"
    facts.write_text("{not json", encoding="utf-8")

    assert main(["--github-access", str(facts)]) == EXIT_UNUSABLE


def test_gathering_into_fixtures_is_refused_before_github_is_asked_anything() -> None:
    """Mutation: write the gathered file wherever it was pointed.

    A gathered file names every member of the organization and what each of them may do in
    this repository. Every capture tool here writes to a working directory and leaves the
    copy into `fixtures/` to a person, because that copy is the only moment anybody reads
    what a live account actually answered. Refused before the calls rather than after, so a
    destination nobody can write to costs nothing and leaves nothing half written.
    """
    destination = PROJECT_ROOT / "fixtures" / "evidence" / "github-access.json"

    assert main(["--github-access", str(destination), "--gather"]) == EXIT_UNUSABLE
    assert not destination.exists()
