"""Which onboarding steps a person has done, and who has to do each one they have not.

**Nobody can answer "is this person ready to submit a run" without opening three systems.**
Getting somebody to the point where a run they submit succeeds end to end takes membership
of the GitHub organization, write access to this repository, a line in
``config/organization.yaml``, an account inside the Weights and Biases eduLLM team, a place
on a research team, and, for a lead or an admin, two list entries in two systems that have
to agree. Only one of those is granted by a pull request. Most of them are settings an owner
changes in a browser leaving no artifact in any repository, and each fails differently: a
Run button that is not on the page, a refusal naming a reason code after the form has been
filled in, or a run that works and carries the wrong name.

So this reports the whole checklist per person and, for every step that is not done, says
what a human must do and which system they do it in. That second half is the point. A
report that says a step is missing and stops is the manual checklist with extra steps.

**Why the GitHub side arrives as a file.** Organization membership, repository permission
and team membership are not in this repository and cannot be. The logic that decides done
or missing is therefore a pure function over data, so it can be tested against people
nobody currently is, and ``--gather`` is a separate mode that asks GitHub and writes the
file the report reads. The two lists that already had a capture path, the members of the
team reviewing ``run-approval-lead`` and the reviewers on the approval environments, are
gathered by calling that path rather than by queries written here that could quietly
disagree with it.

**What it cannot tell you.** Whether somebody has a Weights and Biases account at all.
Nothing in this repository can read the eduLLM team's member list: the platform logs with a
service account whose key is in Secrets Manager, and no reviewed configuration here names
the entity. A blank ``wandb_username`` means nobody has recorded one, which is not the same
as nobody having an account, and both are reported the same way because from here they are
indistinguishable. Nor can it tell you that a step done when the file was gathered is still
done. Every GitHub fact in it is a statement about the moment it was taken, and an owner can
change any of them in ten seconds leaving no artifact anywhere.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

from edullm_platform.capture_tooling import observed_now
from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import ApprovalEnvironment
from edullm_platform.contracts.bindings import normalize_github_login
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.phase2_evidence import LEAD_APPROVAL_TEAM_SLUG

EXIT_FOUND_MISSING_STEPS = 1
EXIT_UNUSABLE = 2

#: Where the Run button lives and whose organization holds it. Defaults rather than
#: constants, because the gather mode has to be pointable at a fork while somebody is
#: working out what this reports.
DEFAULT_ORGANIZATION = "edu-llm"
DEFAULT_REPOSITORY = "platform"

STEP_LOGIN_RESOLVES = "a GitHub login that resolves"
STEP_ORGANIZATION = "membership of the GitHub organization"
STEP_WRITE_ACCESS = "write access to the platform repository"
STEP_ROSTER = "a place on the roster"
STEP_TEAM = "a research team on the roster"
STEP_TEAM_ON_GITHUB = "the matching GitHub team"
STEP_WANDB = "a W&B account recorded for attribution"
STEP_LEAD_ON_GITHUB = "approval authority on GitHub"
STEP_LEAD_ON_ROSTER = "approval authority on the roster"
STEP_ADMIN_ON_GITHUB = "admin approval authority on GitHub"
STEP_ADMIN_ON_ROSTER = "the admin role on the roster"

#: The gate an exception run stops at, and the one whose reviewers are named users rather
#: than a team. The lead gate's reviewer is written down in
#: :mod:`edullm_platform.phase2_evidence` and read from there for the reason that module
#: gives, so this is the other half of the same pair of names.
ADMIN_APPROVAL_ENVIRONMENT = ApprovalEnvironment.ADMIN.value


class ReportInputError(Exception):
    """The inputs could not be read, which is never the same as nothing being missing."""


@dataclass(frozen=True)
class Step:
    """One step for one person, and the sentence somebody acts on if it is not done.

    ``blocking`` means a run this person submits cannot succeed while the step is
    outstanding. It is false for the steps that cost something other than a run: the W&B
    account costs attribution, and a lead missing from the GitHub team costs that lead the
    ability to release other people's work. Collapsing the two would put a run that works
    and a run that is refused in the same list.
    """

    name: str
    done: bool
    blocking: bool
    action: str


@dataclass(frozen=True)
class PersonReadiness:
    """Every step for one person, in the order that person meets them."""

    github_login: str
    display_name: str | None
    on_the_roster: bool
    steps: tuple[Step, ...]

    @property
    def missing(self) -> tuple[Step, ...]:
        return tuple(step for step in self.steps if not step.done)

    @property
    def blocked(self) -> bool:
        return any(not step.done and step.blocking for step in self.steps)

    @property
    def described(self) -> str:
        if self.display_name is None:
            return f"`{self.github_login}`"
        return f"{self.display_name} (`{self.github_login}`)"


@dataclass(frozen=True)
class GitHubAccess:
    """What GitHub knows about these people that no file in this repository does.

    Logins are normalized on the way in, the way every roster lookup normalizes, because
    GitHub treats a login case-insensitively and this file is written by a machine reading
    an API while ``config/organization.yaml`` is written by people spelling their own names.

    ``unresolvable_logins`` holds logins GitHub answered 404 for. A login absent from it was
    either found or never asked about, and the gather mode asks about exactly the roster
    logins the organization does not hold, which is the only case where the question is
    open.

    The admin gate's reviewers are kept as two fields because GitHub lets an environment
    name either a user or a team, and today it names two users. Flattening a team reviewer
    into its members would report the same answer for a control that is not the same one,
    so the teams are listed by slug and their members arrive in ``team_members`` beside
    every other team.
    """

    observed_at: str
    organization: str
    repository: str
    organization_members: frozenset[str]
    repository_writers: frozenset[str]
    team_members: Mapping[str, frozenset[str]]
    unresolvable_logins: frozenset[str]
    admin_gate_reviewers: frozenset[str]
    admin_gate_review_teams: tuple[str, ...]

    def in_organization(self, github_login: str) -> bool:
        return normalize_github_login(github_login) in self.organization_members

    def may_write(self, github_login: str) -> bool:
        return normalize_github_login(github_login) in self.repository_writers

    def in_team(self, team_slug: str, github_login: str) -> bool:
        members = self.team_members.get(team_slug, frozenset())
        return normalize_github_login(github_login) in members

    def resolves(self, github_login: str) -> bool:
        return normalize_github_login(github_login) not in self.unresolvable_logins

    def reviews_admin_gate(self, github_login: str) -> bool:
        if normalize_github_login(github_login) in self.admin_gate_reviewers:
            return True
        return any(self.in_team(slug, github_login) for slug in self.admin_gate_review_teams)


def _slug(team_slug: object) -> str:
    """A GitHub team slug, folded on the way in so the lookups do not have to.

    ``GitHubTeamSlug`` is lowercase by contract, so the roster side of every team comparison
    is already folded and nothing else in this file needs to fold again. The gathered
    document is the side with no contract on it, so it is folded here.
    """
    return str(team_slug).casefold()


def _normalized(values: object, *, field: str) -> frozenset[str]:
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise ReportInputError(f"{field} must be a list of GitHub logins")
    return frozenset(normalize_github_login(str(item)) for item in values)


def _slugs(values: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise ReportInputError(f"{field} must be a list of GitHub team slugs")
    return tuple(sorted({_slug(item) for item in values}))


def parse_github_access(document: object) -> GitHubAccess:
    """Read the gathered document, refusing anything it cannot read rather than guessing.

    A missing key is refused instead of defaulting to an empty list. An empty list is a real
    answer here, and it is the loudest one this report can be given: it says nobody is in the
    organization, which would put every person in the blocked section. A typo in a key name
    would say the same thing while meaning nothing at all.
    """
    if not isinstance(document, dict):
        raise ReportInputError("the GitHub facts must be a JSON object")

    required = (
        "observed_at",
        "organization",
        "repository",
        "organization_members",
        "repository_writers",
        "team_members",
        "unresolvable_logins",
        "admin_gate_reviewers",
        "admin_gate_review_teams",
    )
    absent = [key for key in required if key not in document]
    if absent:
        raise ReportInputError(
            f"the GitHub facts are missing {', '.join(absent)}. Re-gather with --gather "
            "rather than filling the keys in by hand, so what the report reads is what "
            "GitHub said."
        )

    teams = document["team_members"]
    if not isinstance(teams, dict):
        raise ReportInputError("team_members must map a GitHub team slug to its logins")

    return GitHubAccess(
        observed_at=str(document["observed_at"]),
        organization=str(document["organization"]),
        repository=str(document["repository"]),
        organization_members=_normalized(
            document["organization_members"], field="organization_members"
        ),
        repository_writers=_normalized(
            document["repository_writers"], field="repository_writers"
        ),
        team_members={
            _slug(slug): _normalized(logins, field=f"team_members.{slug}")
            for slug, logins in teams.items()
        },
        unresolvable_logins=_normalized(
            document["unresolvable_logins"], field="unresolvable_logins"
        ),
        admin_gate_reviewers=_normalized(
            document["admin_gate_reviewers"], field="admin_gate_reviewers"
        ),
        admin_gate_review_teams=_slugs(
            document["admin_gate_review_teams"], field="admin_gate_review_teams"
        ),
    )


def read_github_access(path: Path) -> GitHubAccess:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ReportInputError(f"{path} could not be read: {error}") from error
    except ValueError as error:
        raise ReportInputError(f"{path} is not readable JSON: {error}") from error
    try:
        return parse_github_access(document)
    except ReportInputError as error:
        raise ReportInputError(f"{path}: {error}") from error


def _login_step(github_login: str, access: GitHubAccess) -> Step:
    return Step(
        name=STEP_LOGIN_RESOLVES,
        done=access.resolves(github_login),
        blocking=True,
        action=(
            f"GitHub answers 404 for `{github_login}`, so this login belongs to nobody. It "
            "is the key every other roster lookup joins on, which means the person behind "
            "it is silently absent from everything derived from "
            "`config/organization.yaml`, including the rest of this report. Ask them how "
            "GitHub spells their account today and change `github_login` in a pull request "
            "against this repository."
        ),
    )


def _organization_step(github_login: str, access: GitHubAccess) -> Step:
    return Step(
        name=STEP_ORGANIZATION,
        done=access.in_organization(github_login),
        blocking=True,
        action=(
            f"An organization owner invites `{github_login}` to the "
            f"`{access.organization}` organization, under People in the GitHub "
            "organization settings, and the invitation has to be accepted. No command in "
            "this repository can do either half."
        ),
    )


def _write_access_step(github_login: str, access: GitHubAccess) -> Step:
    return Step(
        name=STEP_WRITE_ACCESS,
        done=access.may_write(github_login),
        blocking=True,
        action=(
            f"An organization owner or a repository admin gives `{github_login}` write on "
            f"`{access.organization}/{access.repository}`, under Collaborators and teams "
            "in the repository settings. The submission form is a manual workflow and "
            "GitHub shows one only to people who can write, so the symptom of this being "
            "the missing step is that the page other people are describing does not exist."
        ),
    )


def _roster_step(github_login: str, *, on_the_roster: bool) -> Step:
    return Step(
        name=STEP_ROSTER,
        done=on_the_roster,
        blocking=True,
        action=(
            f"Add `{github_login}` to `members` in `config/organization.yaml` and open a "
            "pull request against this repository. It is the only step here that is "
            "entirely a pull request; every other one needs somebody holding an owner's "
            "access first. Until it merges every submission they dispatch is refused at "
            "the compile step, naming the roster, before anything is compiled and before "
            "a reviewer is asked."
        ),
    )


def _wandb_step(github_login: str, *, wandb_username: str | None) -> Step:
    return Step(
        name=STEP_WANDB,
        done=wandb_username is not None,
        blocking=False,
        action=(
            f"A Weights and Biases owner adds `{github_login}` to the eduLLM team, in the "
            "team's member settings in W&B. No command in this repository can do it, and "
            "nothing here can see whether they have an account at all. Then read their "
            "exact login out of that member list and record it as `wandb_username` in a "
            "pull request, rather than guessing it: W&B does not refuse a name it does not "
            "recognise, it logs the run under the service account, which is what a blank "
            "does. Their runs work meanwhile and carry the platform's name instead of "
            "theirs."
        ),
    )


def _team_steps(
    github_login: str, inventory: OrganizationInventory, access: GitHubAccess
) -> list[Step]:
    """The research team, from the roster first and from GitHub second.

    WHETHER THIS STEP BLOCKS IS ASKED OF THE PERSON, NOT OF THE CATALOG. This read the
    catalog: empty meant nothing was enforced, non-empty meant everybody without a team was
    refused. That was true of ``evaluate_authorization`` when this was written and stopped
    being true in the same change that declared the six teams. It now reads
    ``membership_is_knowable`` per submitter, so declaring a team switches enforcement on
    for the people recorded in one and changes nothing for anybody else. Reading the
    catalog here inverted the answer for all thirty-five the moment the teams landed: every
    one of them was reported blocked, from a run that would in fact have been admitted.

    So a person with no recorded membership is not blocked, whether that is because no team
    exists or because none of them lists this person. What it costs is attribution: the
    decision records ``team_verified: false``, meaning the claim on the form was taken at
    face value. What makes it a gate is the person's own membership being recorded, which
    is why the two branches below differ in what somebody should do and agree that no run
    is waiting on it.
    """
    if not inventory.team_bindings.teams:
        return [
            Step(
                name=STEP_TEAM,
                done=False,
                blocking=False,
                action=(
                    "No team is declared at all. `team_bindings` in "
                    "`config/organization.yaml` holds no team, so every membership lookup "
                    "answers nothing and no claimed team can be checked against anything. "
                    "Declaring the teams is a pull request against this repository; "
                    "creating the matching GitHub teams is an owner action in the "
                    "organization settings. No submission is refused over this, and every "
                    "decision records `team_verified: false`."
                ),
            )
        ]

    teams = inventory.teams_for_member(github_login)
    if not teams:
        return [
            Step(
                name=STEP_TEAM,
                done=False,
                blocking=False,
                action=(
                    f"No declared team lists `{github_login}`. Add them to a team's "
                    "`member_logins` in `config/organization.yaml` in a pull request "
                    "against this repository; the team's lead is who knows which one. No "
                    "submission is refused over this, because admission checks a claimed "
                    "team only against a submitter whose own membership is recorded. What "
                    "it costs is that their runs record `team_verified: false` and are "
                    "attributed to whatever the form said, and their claim starts being "
                    "checked the moment this line exists."
                ),
            )
        ]

    absent_from = [
        team for team in teams if not access.in_team(team.github_team_slug, github_login)
    ]
    slugs = ", ".join(f"`{team.github_team_slug}`" for team in absent_from)
    return [
        Step(name=STEP_TEAM, done=True, blocking=True, action=""),
        Step(
            name=STEP_TEAM_ON_GITHUB,
            done=not absent_from,
            blocking=False,
            action=(
                f"`config/organization.yaml` puts `{github_login}` on a team whose GitHub "
                f"team does not hold them. An organization owner adds them to {slugs}, in "
                "the GitHub organization settings. No submission is refused over this, "
                "because the submission path reads the roster and never the GitHub team, "
                "so what it costs is that the two lists disagree and nothing says so."
            ),
        ),
    ]


def _lead_steps(
    github_login: str, inventory: OrganizationInventory, access: GitHubAccess
) -> list[Step]:
    """Approval authority, as two steps because it is two lists that fail two ways.

    ``team_leads`` in ``config/organization.yaml`` is what admission reads. The GitHub
    ``team-leads`` team is the only reviewer on the ``run-approval-lead`` environment and is
    what actually releases a job. A person on one and not the other is a different incident
    in each direction with a different fix, so neither is reported as one step being half
    done.

    Somebody who is on neither list is not a lead and gets neither step. Approval authority
    is granted deliberately here, so an ordinary member missing it is not an outstanding
    task, and a report that listed it against thirty-three people would bury the two it
    applies to.
    """
    on_the_roster = inventory.is_team_lead(github_login)
    on_github = access.in_team(LEAD_APPROVAL_TEAM_SLUG, github_login)
    steps: list[Step] = []
    if on_the_roster:
        steps.append(
            Step(
                name=STEP_LEAD_ON_GITHUB,
                done=on_github,
                blocking=False,
                action=(
                    f"`config/organization.yaml` names `{github_login}` in `team_leads` and "
                    f"GitHub's `{LEAD_APPROVAL_TEAM_SLUG}` team does not hold them. That "
                    "team is the only reviewer on the `run-approval-lead` environment, so "
                    "the gate will never let them release a run, their own group's "
                    "included. An organization owner adds them to "
                    f"`{access.organization}/{LEAD_APPROVAL_TEAM_SLUG}`, in the GitHub "
                    "organization settings."
                ),
            )
        )
    if on_github:
        steps.append(
            Step(
                name=STEP_LEAD_ON_ROSTER,
                done=on_the_roster,
                blocking=False,
                action=(
                    f"GitHub's `{LEAD_APPROVAL_TEAM_SLUG}` team holds `{github_login}` and "
                    "`config/organization.yaml` does not name them in `team_leads`. They "
                    "can release any team's routine run at the lead gate, and admission "
                    "then refuses the submission with `approver_lacks_lead_or_admin_role`, "
                    "which reads as a permissions bug rather than as a list being out of "
                    "date. Either add them to `team_leads` in a pull request, or an "
                    "organization owner removes them from the team in the GitHub "
                    "organization settings. Which of those is right is a question about who "
                    "leads a group, so it is not one this report can answer."
                ),
            )
        )
    return steps


def _admin_steps(
    github_login: str, inventory: OrganizationInventory, access: GitHubAccess
) -> list[Step]:
    """The same pair again for the gate an exception run stops at.

    ``admins`` in ``config/organization.yaml`` is what ``holds_exception_approver_role``
    reads. The reviewers on the ``run-approval-admin`` environment are what release the job.
    The lead gate names a team and this one names two users, which changes where an owner
    clicks and changes nothing about the failure: the two lists are maintained separately
    and nothing on this platform compares them.

    Not folded into :func:`_lead_steps` even though the shape is identical. A lead who
    cannot open the lead gate and an admin who cannot open the admin gate are held in
    different places, fixed on different screens, and admission refuses them with different
    reason codes, so a shared sentence would have to stop naming any of that.
    """
    on_the_roster = inventory.is_admin(github_login)
    on_github = access.reviews_admin_gate(github_login)
    steps: list[Step] = []
    if on_the_roster:
        steps.append(
            Step(
                name=STEP_ADMIN_ON_GITHUB,
                done=on_github,
                blocking=False,
                action=(
                    f"`config/organization.yaml` names `{github_login}` in `admins` and the "
                    f"`{ADMIN_APPROVAL_ENVIRONMENT}` environment does not list them as a "
                    "reviewer. Admission would accept their approval of an exception run "
                    "and the gate will never offer them one, so an exception request waits "
                    "for somebody who cannot be asked. A repository admin adds them under "
                    "Environments in the GitHub repository settings, on "
                    f"`{ADMIN_APPROVAL_ENVIRONMENT}`."
                ),
            )
        )
    if on_github:
        steps.append(
            Step(
                name=STEP_ADMIN_ON_ROSTER,
                done=on_the_roster,
                blocking=False,
                action=(
                    f"The `{ADMIN_APPROVAL_ENVIRONMENT}` environment lists `{github_login}` "
                    "as a reviewer and `config/organization.yaml` does not name them in "
                    "`admins`. They can release an exception run at the gate, and admission "
                    "then refuses it with `approver_lacks_admin_role` after the reviewer has "
                    "already been asked. Either add them to `admins` in a pull request "
                    "against this repository, or a repository admin removes them under "
                    "Environments in the GitHub repository settings. Which of those is right "
                    "is a question about who administers this platform, so it is not one "
                    "this report can answer."
                ),
            )
        )
    return steps


def readiness(
    inventory: OrganizationInventory, access: GitHubAccess
) -> list[PersonReadiness]:
    """One entry per person either list knows about, ordered by login.

    The union rather than the roster, because somebody the GitHub organization holds and
    the roster does not is the case with the quietest failure: they can see the Run button,
    fill in the form, spend a lead's attention on releasing it, and be refused inside AWS.
    A report built from the roster alone could not see them at all.
    """
    subjects: dict[str, tuple[str, str | None, str | None]] = {}
    for member in inventory.members:
        subjects[member.normalized_github_login] = (
            member.github_login,
            member.display_name,
            member.wandb_username,
        )
    on_the_roster = set(subjects)
    for normalized in access.organization_members:
        subjects.setdefault(normalized, (normalized, None, None))

    people: list[PersonReadiness] = []
    for normalized in sorted(subjects):
        github_login, display_name, wandb_username = subjects[normalized]
        rostered = normalized in on_the_roster
        login_step = _login_step(github_login, access)
        if login_step.done:
            steps = [
                login_step,
                _organization_step(github_login, access),
                _write_access_step(github_login, access),
                _roster_step(github_login, on_the_roster=rostered),
                *_team_steps(github_login, inventory, access),
                _wandb_step(github_login, wandb_username=wandb_username),
                *_lead_steps(github_login, inventory, access),
                *_admin_steps(github_login, inventory, access),
            ]
        else:
            # Nothing else is worth saying about a login GitHub does not have. Every other
            # step would report as missing, because a login that belongs to nobody is in no
            # organization and on no team, and each of those lines would send somebody to
            # grant access to an account that does not exist. Fixing the login is the whole
            # of what there is to do, and the rest is answerable once it is right.
            steps = [login_step]
        people.append(
            PersonReadiness(
                github_login=github_login,
                display_name=display_name,
                on_the_roster=rostered,
                steps=tuple(steps),
            )
        )
    return people


def _count(number: int, singular: str, plural: str) -> str:
    return f"{number} {singular if number == 1 else plural}"


def shared_steps(people: Sequence[PersonReadiness]) -> tuple[Step, ...]:
    """Steps everybody who has them is missing, in the same words.

    A condition that holds for the whole roster is a fact about the platform rather than
    about any of the people in it, and repeating it under thirty-five headings is how a
    report stops being read. The test is that the sentence is identical, which is what makes
    hoisting safe: every action here that is about one person interpolates their login, so a
    personal step can never collapse into this no matter how many people share the problem.
    """
    grouped: dict[str, list[Step]] = {}
    for person in people:
        for step in person.steps:
            grouped.setdefault(step.name, []).append(step)
    return tuple(
        steps[0]
        for steps in grouped.values()
        if not steps[0].done and len(set(steps)) == 1
    )


def _entry(person: PersonReadiness, outstanding: Sequence[Step]) -> list[str]:
    """One person's outstanding steps, the ones that stop a run first.

    All of them and not only the blocking ones, because whoever acts on this entry is about
    to open the same settings pages either way, and a second pass a week later costs more
    than a line does.
    """
    ordered = sorted(outstanding, key=lambda step: not step.blocking)
    lines = [f"### {person.described}", ""]
    lines += [f"- **{step.name}.** {step.action}" for step in ordered]
    lines.append("")
    return lines


def render(people: Sequence[PersonReadiness], access: GitHubAccess) -> str:
    if not people:
        return (
            "Neither the roster nor the GitHub organization names anybody, so there is "
            "nobody here to be ready or not.\n"
        )

    shared = shared_steps(people)
    hoisted = {step.name for step in shared}
    outstanding = [
        (person, tuple(step for step in person.missing if step.name not in hoisted))
        for person in people
    ]
    blocked = [(person, steps) for person, steps in outstanding if any(s.blocking for s in steps)]
    partial = [
        (person, steps)
        for person, steps in outstanding
        if steps and not any(step.blocking for step in steps)
    ]
    ready = [person for person, steps in outstanding if not steps]

    lines = [
        "# Onboarding readiness",
        "",
        (
            f"{_count(len(people), 'person is', 'people are')} known to this platform. "
            f"{_count(len(blocked), 'is', 'are')} missing something that stops a run "
            f"outright, {_count(len(partial), 'is', 'are')} missing something that costs "
            "attribution or the authority to release somebody else's work, and "
            f"{_count(len(ready), 'is', 'are')} missing nothing of their own."
        ),
        "",
        (
            f"The GitHub half of this was read at {access.observed_at} and is a statement "
            "about that moment. Organization membership, repository permission and team "
            "membership are changed in a browser and leave no artifact in this repository, "
            "so re-gather before acting on a report that has been sitting around."
        ),
        "",
    ]

    if shared:
        lines += [
            "## Missing for everybody",
            "",
            (
                "True of every person below rather than of any of them, so it is said once "
                "here instead of under each name. Nobody is counted as missing it "
                "individually."
            ),
            "",
        ]
        lines += [f"- **{step.name}.** {step.action}" for step in shared]
        lines.append("")

    if blocked:
        lines += [
            "## Cannot have a run succeed yet",
            "",
            (
                "Each of these is missing at least one step that stops a run outright, and "
                "that step is listed first. None of them fails by saying so: what the "
                "person meets is a page that is not there, or a refusal naming a reason "
                "code after a reviewer has already been asked."
            ),
            "",
        ]
        for person, steps in blocked:
            lines += _entry(person, steps)

    if partial:
        lines += [
            "## Can submit, and something is still missing",
            "",
            (
                "A run these people submit works. What is missing costs attribution, or "
                "costs a lead the ability to release somebody else's work, and nothing on "
                "this platform reports either of those on its own."
            ),
            "",
        ]
        for person, steps in partial:
            lines += _entry(person, steps)

    if ready:
        lines += ["## Missing nothing of their own", ""]
        lines += [f"- {person.described}" for person in ready]
        lines.append("")

    return "\n".join(lines)


def _github(*arguments: str) -> Any:
    """One ``gh api`` call, parsed, with the service's own words on a failure.

    Not in :mod:`edullm_platform.capture_tooling`, and that module says why it holds no
    GitHub wrapper: what a refused ``gh api`` call prints is the service's stderr, which is
    the whole value of the message to whoever has to fix the session, and which a
    machine-readable reason token throws away.
    """
    completed = subprocess.run(
        ["gh", "api", *arguments], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise ReportInputError(
            f"gh api {' '.join(arguments)} failed with {completed.returncode}: "
            f"{completed.stderr.strip()[:400]}"
        )
    return json.loads(completed.stdout or "null")


def _github_listing(path: str) -> list[Any]:
    """Every page of a listing, flattened.

    ``--paginate`` alone emits one JSON array per page, which is not a document anything
    can parse; ``--slurp`` wraps the pages into one array. An older ``gh`` fails on the
    unknown flag and the gather stops, which is the right way to lose: a listing truncated
    at the first page reports the people past the cut as missing everything.
    """
    pages = _github("--paginate", "--slurp", path)
    return [entry for page in pages or [] for entry in page or []]


def _resolves(github_login: str) -> bool:
    """Whether GitHub has a user by this name at all.

    Asked only about logins the organization does not hold, because a member resolves by
    construction. A 404 is the answer rather than a failure; anything else is a failure,
    since reporting an outage as a dead login would send somebody to rename an account that
    is fine.
    """
    completed = subprocess.run(
        ["gh", "api", f"users/{github_login}"], capture_output=True, text=True, check=False
    )
    if completed.returncode == 0:
        return True
    if "HTTP 404" in completed.stderr or "Not Found" in completed.stderr:
        return False
    raise ReportInputError(
        f"gh api users/{github_login} failed with {completed.returncode}: "
        f"{completed.stderr.strip()[:400]}"
    )


def gather(
    inventory: OrganizationInventory, *, organization: str, repository: str
) -> dict[str, Any]:
    """Ask GitHub for the facts the report cannot hold, and return the document it reads.

    The ``team-leads`` membership and the approval environments come from
    ``tools/capture_phase2_evidence.py`` rather than from queries written here. Those
    captures already argue out what they do with a login the roster does not declare, page
    the endpoints properly, keep the members of child teams because a child team's member
    can release the gate, and record a reviewer as a type and a name rather than flattening
    a team into its members. A second query for either list would be a second set of those
    decisions, and the two would disagree the first time only one of them was corrected.

    Imported inside the function so that the reporting path, which is the path with tests
    and no network, does not pull in a module whose whole job is to shell out.
    """
    from capture_phase2_evidence import CaptureError, capture_environments, capture_lead_team

    try:
        lead_team = capture_lead_team(organization, repository)
        environments = capture_environments(organization, repository)
    except CaptureError as error:
        raise ReportInputError(str(error)) from error

    admin_gate = next(
        (
            environment
            for environment in environments.environments
            if environment.name == ADMIN_APPROVAL_ENVIRONMENT
        ),
        None,
    )
    if admin_gate is None:
        # Refused rather than recorded as a gate with no reviewers. Those two are not the
        # same statement and this report would print the second as every admin having lost
        # their authority, which is a page of findings about nobody.
        raise ReportInputError(
            f"{organization}/{repository} has no `{ADMIN_APPROVAL_ENVIRONMENT}` environment, "
            "so there is nothing to compare `admins` against. Whether that is a fork without "
            "the gate or the gate having been deleted is not a question this report can "
            "answer."
        )
    admin_reviewers = [
        reviewer.name for reviewer in admin_gate.reviewers if reviewer.kind == "User"
    ]
    admin_review_teams = [
        reviewer.name for reviewer in admin_gate.reviewers if reviewer.kind == "Team"
    ]

    members = [str(entry["login"]) for entry in _github_listing(f"orgs/{organization}/members")]
    # Collaborators rather than members, and filtered on the permission itself. Write can
    # arrive through a team, through the organization's base permission or through a direct
    # grant, and this endpoint is the one place that reports the answer rather than one of
    # the three ways of arriving at it.
    writers = [
        str(entry["login"])
        for entry in _github_listing(f"repos/{organization}/{repository}/collaborators")
        if bool(entry.get("permissions", {}).get("push"))
    ]

    team_members: dict[str, list[str]] = {
        _slug(lead_team.team_slug): sorted(lead_team.member_logins)
    }
    # The research teams the roster declares, which is none of them today, and any team the
    # admin gate names as a reviewer, of which there are none today either. Read off the
    # bindings and off the gate rather than off GitHub's team list: a team GitHub holds that
    # neither declares grants nothing on this platform, and asking for it would put a list
    # in the document that nothing reads.
    wanted = {_slug(team.github_team_slug) for team in inventory.team_bindings.teams}
    wanted |= {_slug(name) for name in admin_review_teams}
    for slug in sorted(wanted - set(team_members)):
        team_members[slug] = sorted(
            str(entry["login"])
            for entry in _github_listing(f"orgs/{organization}/teams/{slug}/members")
        )

    known = {normalize_github_login(login) for login in members}
    unresolvable = [
        member.github_login
        for member in inventory.members
        if member.normalized_github_login not in known
        and not _resolves(member.github_login)
    ]

    return {
        "observed_at": observed_now().isoformat(),
        "organization": organization,
        "repository": repository,
        "organization_members": sorted(members),
        "repository_writers": sorted(writers),
        "team_members": {slug: sorted(logins) for slug, logins in sorted(team_members.items())},
        "unresolvable_logins": sorted(unresolvable),
        "admin_gate_reviewers": sorted(admin_reviewers),
        "admin_gate_review_teams": sorted(admin_review_teams),
    }


def _checked_destination(path: Path) -> Path:
    """Refuse to gather straight into ``fixtures/``.

    This asks a live organization who its members are and what each of them may do here,
    and that answer is somebody's to read before it is committed. Writing it into
    ``fixtures/`` would skip the only moment anybody looks, which is the same rule every
    capture tool in this repository follows for the same reason.
    """
    if "fixtures" in path.resolve().parts:
        raise ReportInputError(
            f"{path} is under fixtures/. A gathered file names every member of the "
            "organization and what each of them may do in this repository, so it is local "
            "until somebody has read it and decided what belongs in a committed record."
        )
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--github-access",
        type=Path,
        required=True,
        help="the gathered GitHub facts this report reads",
    )
    parser.add_argument(
        "--gather",
        action="store_true",
        help="ask GitHub through the gh CLI and write --github-access first",
    )
    parser.add_argument("--organization", default=DEFAULT_ORGANIZATION)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    parser.add_argument("--output", type=Path, help="write the report here rather than to stdout")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    try:
        inventory = load_yaml(options.config_dir / "organization.yaml", OrganizationInventory)
        if options.gather:
            # Checked before anything is asked of GitHub, so a destination that would be
            # refused costs no calls and leaves nothing half written.
            destination = _checked_destination(options.github_access)
            document = gather(
                inventory,
                organization=options.organization,
                repository=options.repository,
            )
            destination.write_text(
                json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        access = read_github_access(options.github_access)
        people = readiness(inventory, access)
    except (ReportInputError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_UNUSABLE

    report = render(people, access)
    if options.output:
        options.output.write_text(report, encoding="utf-8")
    else:
        print(report, end="")

    # A non-zero exit so this can gate an onboarding checklist later without being
    # rewritten. It is not an error in the tool; it is the tool having found what it looks
    # for.
    return EXIT_FOUND_MISSING_STEPS if any(person.missing for person in people) else 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
