"""What the gate that stops a run must be, and reading whether it still is.

The gate is not in this repository. ``run-approval-lead`` and ``run-approval-admin`` are
GitHub environment settings: a browser, an admin, ten seconds, and no commit anywhere. Every
other control this platform relies on is a file somebody reviewed, and this one is a
recollection. ``fixtures/evidence/phase-2/github/environments.sanitized.json`` is the
existing answer and it is a photograph — it says what the settings were at ``observed_at``,
it expires after thirty days so a stale reading cannot pass as current, and between the
capture and its expiry it says nothing at all about now.

**The failure this exists for is silent and is one click.** GitHub offers required reviewers
on an environment for public repositories on every plan, and for private repositories only
on Pro, Team or Enterprise. This organization is Free and this repository is public, so the
gate holds by the narrowest margin GitHub sells. Converting ``platform`` to private does not
weaken the gate, it deletes it: the protection rule is removed, every job that was waiting
proceeds, and there is no warning, no failure and no red run, because a job whose environment
carries no protection rule is a job that runs. Five repositories in this organization are
already private, which is what makes this a habit somebody could act on rather than a
hypothetical.

**So the check is a live reading and its two halves are independent on purpose.** The
repository must still be public *or* the plan must have moved to one that carries the control
on a private repository, and separately the protection rule must still be there. Either
answer going wrong is a finding. Asserting only the protection rule would trust GitHub to
remove it, which is the documented behaviour and not something this repository has watched
happen; asserting only the visibility would miss an admin who deleted the rule by hand.

**What it cannot reach, said here rather than left as a silence.** The lead gate's single
reviewer is the ``team-leads`` team, and listing that team's members needs the Members
organization permission. An Actions ``GITHUB_TOKEN`` holds no organization permission at all,
and a stored PAT would be a repository secret that
``test_the_repository_holds_no_secret_a_branch_could_read`` forbids by name. Verified against
this organization on 2026-08-06: the environment and its reviewer *team* are readable
anonymously because the repository is public, and that team's *members* answer 404 to the
same anonymous call and 401 by slug. :func:`compare_lead_team_membership` is therefore
written and is reached only by a caller holding a credential that can list the team, which
today means a person at a laptop. The scheduled half reports the membership as unread rather
than as agreeing.

**What covers that half on a schedule is a different job and it is not this one.**
``tools/report_who_can_open_the_lead_gate.py`` compares the committed capture against
``holds_routine_approver_role`` and puts a clock over the capture's age, which is the most a
schedule can do without a credential that does not exist here. The two are complementary and
neither substitutes for the other: that job asks who stands behind the reviewer slot and can
only ask it of a capture, this one asks whether the slot is there at all and can only ask it
live. The failure each is blind to is the other's subject.

Everything here is pure. The ``gh`` calls, the exit codes and the printing are in
``tools/verify_the_gate.py``, so the comparisons can be driven from a mutated payload in a
test without a network or a session.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from edullm_platform.contracts.bindings import normalize_github_login

__all__ = [
    "DECLARED_GATES",
    "LEAD_APPROVAL_GATE",
    "PLANS_CARRYING_THE_GATE_ON_A_PRIVATE_REPOSITORY",
    "DeclaredGate",
    "GateFinding",
    "LiveGate",
    "compare_gate",
    "compare_lead_team_membership",
    "compare_the_environment_list",
    "compare_visibility",
    "declared_gate",
    "read_environment",
]

#: The environment a routine submission is routed to. Named because three of the comparisons
#: below are only interesting about this one, and spelling it at each of them is how the
#: three drift apart.
LEAD_APPROVAL_GATE: Final = "run-approval-lead"

#: The GitHub plans that carry required reviewers on a *private* repository. Spelled the same
#: way ``operational_inventory.PHASE1_PRIVATE_REPO_GITHUB_PLANS`` spells it, and deliberately
#: not imported from there: that constant is about branch rulesets and CODEOWNERS review
#: assignment, this one is about environment protection rules, and the two happening to list
#: the same plans today is a coincidence of GitHub's pricing rather than one fact.
PLANS_CARRYING_THE_GATE_ON_A_PRIVATE_REPOSITORY: Final = frozenset({"team", "enterprise"})


@dataclass(frozen=True)
class DeclaredGate:
    """What one approval environment must look like, written down where a diff shows it.

    This is the half of the design that has never been in the repository. A reader who wants
    to know who reviews the lead gate has to open organization settings, and a person who
    changes it leaves nothing behind. Declaring it here means the answer is in a file, the
    check below holds GitHub to it, and moving the setting means moving this line in the same
    ten minutes — which is the whole point, because that line is the commit the setting change
    has never had.
    """

    name: str

    #: Reviewer teams, by slug. Empty means no team reviews this gate.
    reviewer_team_slugs: tuple[str, ...]

    #: Whether the named-user reviewers must be exactly the roster's ``admins``. A boolean
    #: rather than a list of logins, because a list here would be a second spelling of
    #: ``config/organization.yaml`` and the two would drift the way every other pair of lists
    #: in this system already has.
    reviewer_logins_are_the_roster_admins: bool

    #: What ``prevent_self_review`` must read. **False today, on purpose, and it is the one
    #: field here somebody is expected to change.** Turning the GitHub setting on without
    #: moving this line turns the daily audit red the next morning with a message naming this
    #: line; that is the intended cost and it is ten seconds. The reason it is false today is
    #: recorded on ``ProtectedEnvironment.prevent_self_review`` and argued in
    #: ``tests/test_self_approval.py``, and the measured consequence — fourteen of the last
    #: thirty-four approvals released by the person who submitted them — is a decision for
    #: the owner rather than a fact about the mechanism.
    #:
    #: ``None`` where the gate carries no reviewer rule for the flag to live on. GitHub
    #: answers 422 to setting it on an environment with no reviewers, so there is nothing to
    #: compare and reading the absence as ``False`` would claim self-review was considered.
    prevent_self_review: bool | None

    @property
    def reviewers_required(self) -> bool:
        """Whether a job entering this environment must wait for a person.

        Derived rather than declared, so a gate cannot say it needs no reviewer and then list
        one.
        """
        return bool(self.reviewer_team_slugs) or self.reviewer_logins_are_the_roster_admins


#: Every environment this repository deploys to, and no others. **All four, including the two
#: with no reviewer**, because the finding that matters most is an environment nobody
#: declared: GitHub creates one with no protection rules whatsoever for anybody who names it
#: in a workflow file, and everybody who can submit holds the write access that allows it.
#: A check that looked only at the two reviewed gates would report a healthy gate beside an
#: open door.
#:
#: ``run-approval-preview`` is the fourth and it is younger than the committed capture, which
#: is the drift this list would already have caught. It reviews nothing by design: a dispatch
#: from a branch demotes to it, and what it reaches is bounded by
#: ``infra/iam/run-preview-role.yaml`` rather than by a person.
DECLARED_GATES: Final[tuple[DeclaredGate, ...]] = (
    DeclaredGate(
        name="run-approval-automatic",
        reviewer_team_slugs=(),
        reviewer_logins_are_the_roster_admins=False,
        prevent_self_review=None,
    ),
    DeclaredGate(
        name=LEAD_APPROVAL_GATE,
        reviewer_team_slugs=("team-leads",),
        reviewer_logins_are_the_roster_admins=False,
        prevent_self_review=False,
    ),
    DeclaredGate(
        name="run-approval-admin",
        reviewer_team_slugs=(),
        reviewer_logins_are_the_roster_admins=True,
        prevent_self_review=False,
    ),
    DeclaredGate(
        name="run-approval-preview",
        reviewer_team_slugs=(),
        reviewer_logins_are_the_roster_admins=False,
        prevent_self_review=None,
    ),
)


def declared_gate(name: str) -> DeclaredGate | None:
    return next((gate for gate in DECLARED_GATES if gate.name == name), None)


@dataclass(frozen=True)
class GateFinding:
    """One thing the live gate does not match, and what a reader does about it.

    ``reason`` is the machine-readable first line every tool in ``tools/`` prints, and
    ``message`` is the paragraph under it. Both are here rather than formatted at the print
    site so a test can assert on the reason without matching prose.
    """

    reason: str
    message: str


@dataclass(frozen=True)
class LiveGate:
    """One environment as GitHub answered, reduced to what the comparison reads."""

    name: str
    has_required_reviewer_rule: bool
    reviewer_team_slugs: tuple[str, ...]
    reviewer_logins: tuple[str, ...]
    prevent_self_review: bool | None
    can_admins_bypass: bool


def read_environment(payload: Mapping[str, Any]) -> LiveGate:
    """One ``GET /repos/{owner}/{repo}/environments/{name}`` body, reduced.

    The reviewer shape is GitHub's rather than this repository's: each entry is a ``type`` of
    ``User`` or ``Team`` beside a ``reviewer`` object, and a user carries ``login`` where a
    team carries ``slug``. Reading both out of one loop rather than two keeps a reviewer that
    is neither from being dropped silently — an unrecognized kind lands in neither tuple and
    the count check below reports it.
    """
    rules = payload.get("protection_rules")
    reviewer_rule: Mapping[str, Any] | None = None
    if isinstance(rules, Sequence) and not isinstance(rules, (str, bytes)):
        for rule in rules:
            if isinstance(rule, Mapping) and rule.get("type") == "required_reviewers":
                reviewer_rule = rule
                break

    teams: list[str] = []
    logins: list[str] = []
    if reviewer_rule is not None:
        listed = reviewer_rule.get("reviewers")
        entries = listed if isinstance(listed, Sequence) and not isinstance(listed, str) else ()
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            reviewer = entry.get("reviewer")
            if not isinstance(reviewer, Mapping):
                continue
            if entry.get("type") == "Team" and isinstance(reviewer.get("slug"), str):
                teams.append(str(reviewer["slug"]))
            elif entry.get("type") == "User" and isinstance(reviewer.get("login"), str):
                logins.append(str(reviewer["login"]))

    prevent_self_review: bool | None = None
    if reviewer_rule is not None and isinstance(reviewer_rule.get("prevent_self_review"), bool):
        prevent_self_review = bool(reviewer_rule["prevent_self_review"])

    return LiveGate(
        name=str(payload.get("name") or ""),
        has_required_reviewer_rule=reviewer_rule is not None,
        reviewer_team_slugs=tuple(teams),
        reviewer_logins=tuple(logins),
        prevent_self_review=prevent_self_review,
        # Absent means unrestricted, which is the reading that reports rather than the one
        # that reassures. A response missing the field is a response this code does not
        # understand, and defaulting it to False would turn that into a pass.
        can_admins_bypass=bool(payload.get("can_admins_bypass", True)),
    )


def compare_visibility(visibility: str, plan_name: str) -> tuple[GateFinding, ...]:
    """Whether the plan and the visibility still permit the gate to exist at all.

    The single most consequential reading in this module, and the one whose failure mode is
    an absence rather than an error. See the module docstring.
    """
    if visibility.lower() == "public":
        return ()
    if plan_name.lower() in PLANS_CARRYING_THE_GATE_ON_A_PRIVATE_REPOSITORY:
        return ()
    return (
        GateFinding(
            "the_gate_depends_on_public_visibility",
            f"The repository is {visibility!r} on the {plan_name!r} plan. GitHub offers "
            "required reviewers on an environment for public repositories on every plan and "
            "for private repositories only on Pro, Team or Enterprise, so on this plan a "
            "private repository has no approval gate. This does not weaken the gate, it "
            "deletes it: the protection rules are removed, every job that was waiting "
            "proceeds, and nothing goes red, because a job whose environment carries no "
            "protection rule is a job that runs. Either make the repository public again or "
            "move the organization to a plan above, and do not submit anything until one of "
            "those is true.",
        ),
    )


def compare_the_environment_list(live_names: Iterable[str]) -> tuple[GateFinding, ...]:
    """Every environment on the repository against the four this repository declares.

    An undeclared environment is the finding, not a tidiness complaint. GitHub creates one
    with no protection rules for anybody who names it in a workflow file, and everybody who
    can submit holds the write access that allows it. The admission role's trust policy
    enumerates its subjects and would refuse a job deploying to an environment it does not
    name, so such a job cannot reach AWS — but nothing else would say the door exists.
    """
    declared = {gate.name for gate in DECLARED_GATES}
    seen = set(live_names)
    findings: list[GateFinding] = []
    for name in sorted(seen - declared):
        findings.append(
            GateFinding(
                "undeclared_environment",
                f"{name!r} is an environment on this repository and "
                "edullm_platform.approval_gate.DECLARED_GATES does not name it. An "
                "environment is created with no protection rules at all by anybody who names "
                "one in a workflow file. Either it is a gate, in which case declare it there "
                "and give it reviewers, or it is a leftover, in which case delete it.",
            )
        )
    for name in sorted(declared - seen):
        findings.append(
            GateFinding(
                "declared_environment_is_gone",
                f"{name!r} is declared in edullm_platform.approval_gate.DECLARED_GATES and "
                "this repository has no environment of that name. A workflow routing a job "
                "to it deploys to an environment GitHub creates on the spot, with no "
                "protection rules, so the run this gate was supposed to hold would not wait "
                "for anybody.",
            )
        )
    return tuple(findings)


def compare_gate(
    declared: DeclaredGate,
    live: LiveGate,
    roster_admins: Iterable[str],
) -> tuple[GateFinding, ...]:
    """One declared gate against what GitHub answered.

    Every disagreement is reported rather than the first one, because these are four
    independent settings and a reader who fixes the reviewer list and comes back tomorrow for
    the bypass flag has been sent round twice by the check rather than by the account.
    """
    findings: list[GateFinding] = []
    expected_admins = tuple(sorted({normalize_github_login(login) for login in roster_admins}))

    if declared.reviewers_required and not live.has_required_reviewer_rule:
        findings.append(
            GateFinding(
                "the_gate_no_longer_asks_anybody",
                f"{declared.name!r} carries no required-reviewer rule, and this repository "
                "declares that it must. A job routed here does not wait: it deploys, assumes "
                "the admission role and submits, with nobody asked and no approval record. "
                "The two ways this happens are somebody deleting the rule in environment "
                "settings and the repository being converted to private on a plan that does "
                "not carry the control. Check the repository's visibility first.",
            )
        )
    if not declared.reviewers_required and live.has_required_reviewer_rule:
        findings.append(
            GateFinding(
                "an_unreviewed_gate_acquired_a_reviewer",
                f"{declared.name!r} has a required-reviewer rule and this repository "
                "declares it as reviewer-less. Runs that policy says need no human are now "
                "waiting for one, which nothing else in this system would report: the class "
                "still routes here and the trust policy still matches, so the only symptom "
                "is a queue.",
            )
        )

    if live.has_required_reviewer_rule:
        if tuple(sorted(live.reviewer_team_slugs)) != tuple(sorted(declared.reviewer_team_slugs)):
            findings.append(
                GateFinding(
                    "the_reviewer_team_moved",
                    f"{declared.name!r} is reviewed by teams "
                    f"{sorted(live.reviewer_team_slugs)} and this repository declares "
                    f"{sorted(declared.reviewer_team_slugs)}. Who stands behind a team slot "
                    "is organization state that appears in no commit, so a swapped team is a "
                    "wholly different set of approvers with no diff anywhere.",
                )
            )
        actual_logins = tuple(
            sorted({normalize_github_login(login) for login in live.reviewer_logins})
        )
        wanted_logins = expected_admins if declared.reviewer_logins_are_the_roster_admins else ()
        if actual_logins != wanted_logins:
            findings.append(
                GateFinding(
                    "the_named_reviewers_are_not_the_roster",
                    f"{declared.name!r} names reviewers {list(actual_logins)} and "
                    f"config/organization.yaml makes the set {list(wanted_logins)}. A "
                    "reviewer GitHub asks who the roster does not authorize opens the gate "
                    "and is then refused by admission with approver_lacks_lead_or_admin_role, "
                    "which spends an approval on a no; an authorized login GitHub never asks "
                    "is somebody who cannot release even their own group's work.",
                )
            )
        if live.prevent_self_review != declared.prevent_self_review:
            findings.append(
                GateFinding(
                    "prevent_self_review_moved",
                    f"{declared.name!r} reads prevent_self_review "
                    f"{live.prevent_self_review!r} and this repository declares "
                    f"{declared.prevent_self_review!r}. If the setting was changed on "
                    "purpose, move the declaration in "
                    "edullm_platform.approval_gate.DECLARED_GATES to match, in a commit, so "
                    "that the change to who may release a run exists somewhere a reviewer "
                    "can read it. If it was not, this is a change to who may release a run "
                    "that nobody recorded.",
                )
            )

    if live.can_admins_bypass:
        findings.append(
            GateFinding(
                "an_admin_may_release_without_a_reviewer",
                f"{declared.name!r} allows admins to bypass, so \"Start all waiting jobs\" "
                "releases a run with no reviewer and no approval record. That is worse than "
                "widening who may approve: admission reads the approver out of the approvals "
                "endpoint, a bypassed job leaves nothing there, and the attribution the whole "
                "design leans on is gone rather than wrong.",
            )
        )
    return tuple(findings)


def compare_lead_team_membership(
    member_logins: Iterable[str],
    routine_approvers: Iterable[str],
) -> tuple[GateFinding, ...]:
    """The lead gate's reviewer team against everybody admission accepts, in both directions.

    ``routine_approvers`` is asked of ``holds_routine_approver_role`` by the caller rather
    than assembled here, because that function is what admission consults. It is
    ``admins | team_leads`` and not ``team_leads``, and reading it as the latter is the
    mistake that makes an admin on the team look like drift: admission accepts him, and the
    only edit that silences the false reading is one ``tests/test_inventory.py`` refuses.

    The two directions are separate findings because they are separate incidents with
    separate fixes. Somebody GitHub asks who admission will refuse spends an approval on a
    no, and the refusal lands *after* the click, which is the worst ordering available.
    Somebody admission accepts who GitHub never asks is an approver the gate cannot use.

    This is the comparison the scheduled check cannot make. See the module docstring.
    """
    on_github = {normalize_github_login(login) for login in member_logins}
    accepted = {normalize_github_login(login) for login in routine_approvers}
    findings: list[GateFinding] = []
    asked_and_refused = sorted(on_github - accepted)
    if asked_and_refused:
        findings.append(
            GateFinding(
                "the_gate_asks_somebody_admission_will_refuse",
                f"{asked_and_refused} are in the team-leads team and are neither an admin nor "
                "a team lead in config/organization.yaml. GitHub will request a review from "
                "each of them on every routine run, they can release one, and admission then "
                "refuses it with approver_lacks_lead_or_admin_role — after the approval, so "
                "the attention is already spent and the run is dead. Either give them a group "
                "to lead, add them to admins, or take them off the team.",
            )
        )
    accepted_and_unasked = sorted(accepted - on_github)
    if accepted_and_unasked:
        findings.append(
            GateFinding(
                "an_approver_the_gate_will_never_ask",
                f"{accepted_and_unasked} are admins or team leads in "
                "config/organization.yaml and are not in the team-leads team. Admission would "
                "accept a release from them and the lead gate will never request one, so they "
                "cannot release any run, their own group's included.",
            )
        )
    return tuple(findings)
