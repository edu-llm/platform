"""That the gate which stops a run is still there, read from GitHub rather than remembered.

Everything this platform refuses, it refuses from a file somebody reviewed. The one control
that decides whether a run waits for a person is not a file: it is an environment protection
rule in GitHub's settings, editable in a browser in ten seconds by anybody holding admin, and
leaving no commit. The committed capture under ``fixtures/evidence/phase-2/github/`` is a
photograph with a thirty-day expiry, so between capture and expiry it says nothing about now,
and thirty days is exactly how long the ninth member of the reviewer team went unnoticed.

**The reading that matters most is an absence.** Required reviewers on an environment are
offered for public repositories on every plan and for private ones only above Free. Convert
this repository to private and the gate is not weakened, it is deleted: the protection rule
goes, every waiting job proceeds, and nothing turns red anywhere, because a job whose
environment has no protection rule is a job that runs. That is the failure this tool exists
to make loud, and it is why the visibility and the protection rule are checked as two
independent questions rather than one.

**What it does not reach.** The lead gate's one reviewer is a team, and listing a team's
members needs the Members organization permission. An Actions ``GITHUB_TOKEN`` holds no
organization permission, and a stored PAT would be a repository secret that
``test_the_repository_holds_no_secret_a_branch_could_read`` forbids by name. So the scheduled
run reports the membership as unread, in those words, rather than passing over it; a person
whose ``gh`` session can list the team passes ``--check-team-membership`` and gets the
comparison. What that flag costs is one API call and what it buys is the one drift a schedule
cannot see.

``tools/report_who_can_open_the_lead_gate.py`` is the scheduled half of that same question and
this tool does not repeat it. It compares the committed capture against the roster and puts a
clock over how old the capture is, which is what a schedule can do with no credential at all.
Between the two: that one asks who stands behind the reviewer slot, this one asks whether the
slot is still there.

Three exit codes, and the two non-zero ones are not interchangeable. Exit 1 says the account
disagrees with this repository and sends a reader to a settings page. Exit 2 says the account
was never read and sends them to a session or a rate limit. A check that could not look is
never reported as a check that found nothing.

    uv run python tools/verify_the_gate.py
    uv run python tools/verify_the_gate.py --check-team-membership
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from edullm_platform.approval_gate import (
    DECLARED_GATES,
    GateFinding,
    compare_gate,
    compare_lead_team_membership,
    compare_the_branch_policy,
    compare_the_environment_list,
    compare_visibility,
    declared_gate,
    read_branch_policy_names,
    read_environment,
)
from edullm_platform.config import load_yaml
from edullm_platform.contracts.authorization import holds_routine_approver_role
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.phase2_evidence import LEAD_APPROVAL_TEAM_SLUG

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
ROSTER_PATH: Final = PROJECT_ROOT / "config" / "organization.yaml"

DEFAULT_REPOSITORY: Final = "edu-llm/platform"

EXIT_OK: Final = 0

#: The account and this repository disagree about the gate. A definite answer about settings.
EXIT_DRIFTED: Final = 1

#: Nothing was read, so nothing is claimed.
EXIT_UNUSABLE: Final = 2

#: What the plan reads as when this tool could not establish it. Not in
#: ``PLANS_CARRYING_THE_GATE_ON_A_PRIVATE_REPOSITORY``, so a private repository whose plan is
#: unknown is a finding rather than a pass. ``GET /orgs/{org}`` returns ``plan`` only to a
#: token an owner minted, and the scheduled run holds no such token — which is fine, because
#: the plan is only consulted once the repository has stopped being public.
UNREADABLE_PLAN: Final = "unknown"

GH_TIMEOUT_SECONDS: Final = 60

__all__ = [
    "EXIT_DRIFTED",
    "EXIT_OK",
    "EXIT_UNUSABLE",
    "GitHubUnreachable",
    "build_parser",
    "main",
]


class GitHubUnreachable(Exception):
    """``gh`` did not answer, so the question was never put to GitHub."""


def _github(path: str) -> Any:
    """One ``gh api`` call, parsed. A 404 is an answer and is returned as ``None``."""
    try:
        completed = subprocess.run(
            ["gh", "api", "-H", "Accept: application/vnd.github+json", path],
            capture_output=True,
            text=True,
            check=False,
            timeout=GH_TIMEOUT_SECONDS,
            shell=False,
        )
    except subprocess.TimeoutExpired as error:
        raise GitHubUnreachable(f"gh api {path} timed out") from error
    except OSError as error:
        raise GitHubUnreachable("the gh CLI is not installed or not on PATH") from error
    if completed.returncode == 0:
        return json.loads(completed.stdout or "null")
    if "HTTP 404" in completed.stderr:
        return None
    # The stderr rather than a reason token, because what a refused call prints is GitHub's
    # own sentence and that is the whole value of it to the reader. Truncated because a rate
    # limit answer is long.
    raise GitHubUnreachable(f"gh api {path}: {completed.stderr.strip()[:400]}")


def read_plan(organization: str) -> str:
    """The organization's plan, or :data:`UNREADABLE_PLAN`.

    Never raises. A token that cannot see the plan is the normal case rather than a failure,
    and the only question the plan settles is one that is asked after the repository has
    stopped being public.
    """
    try:
        organization_payload = _github(f"orgs/{organization}")
    except GitHubUnreachable:
        return UNREADABLE_PLAN
    if not isinstance(organization_payload, dict):
        return UNREADABLE_PLAN
    plan = organization_payload.get("plan")
    if isinstance(plan, dict) and isinstance(plan.get("name"), str):
        return str(plan["name"])
    return UNREADABLE_PLAN


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Check that the approval gate GitHub enforces is the one this repository "
            "declares."
        )
    )
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY, help="owner/repository")
    parser.add_argument("--roster", type=Path, default=ROSTER_PATH)
    parser.add_argument(
        "--check-team-membership",
        action="store_true",
        help=(
            "also compare the reviewer team's members against everybody admission accepts. "
            "Needs a session holding the Members organization permission, which an Actions "
            "GITHUB_TOKEN does not have."
        ),
    )
    return parser


def _report(findings: Sequence[GateFinding]) -> None:
    for finding in findings:
        print(finding.reason, file=sys.stderr)
        print(finding.message, file=sys.stderr, flush=True)
        print(file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    repository = str(options.repository)
    organization = repository.split("/", 1)[0]

    try:
        inventory = load_yaml(Path(options.roster), OrganizationInventory)
    except Exception as error:  # noqa: BLE001 - any unreadable roster is the same answer
        print(f"roster_unreadable: {error}", file=sys.stderr)
        return EXIT_UNUSABLE

    try:
        repository_payload = _github(f"repos/{repository}")
        if not isinstance(repository_payload, dict):
            print(f"repository_unreadable: {repository} answered nothing", file=sys.stderr)
            return EXIT_UNUSABLE
        listed = _github(f"repos/{repository}/environments")
    except GitHubUnreachable as error:
        print("github_unreadable", file=sys.stderr)
        print(str(error), file=sys.stderr)
        return EXIT_UNUSABLE

    visibility = str(repository_payload.get("visibility") or "")
    findings: list[GateFinding] = []
    # The plan is asked for only once the repository has stopped being public, which is the
    # only case where it decides anything and the only case worth spending a call on.
    plan = UNREADABLE_PLAN if visibility.lower() == "public" else read_plan(organization)
    findings.extend(compare_visibility(visibility, plan))

    environments = listed.get("environments") if isinstance(listed, dict) else None
    if not isinstance(environments, list):
        print("environment_list_unreadable", file=sys.stderr)
        return EXIT_UNUSABLE
    live_names = tuple(
        str(entry["name"])
        for entry in environments
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    )
    findings.extend(compare_the_environment_list(live_names))

    for name in live_names:
        declared = declared_gate(name)
        if declared is None:
            # Already reported as undeclared by the list comparison, and there is nothing to
            # hold an undeclared environment to.
            continue
        try:
            payload = _github(f"repos/{repository}/environments/{name}")
            # A second call per environment, and the endpoint answers an anonymous request
            # on a public repository exactly as the first does — verified against this
            # repository on 2026-08-06, HTTP 200 with no session. Which branches may deploy
            # to a gate is not in the environment body: that carries the two boolean forms
            # and not the patterns, so reading only the first would report a gate as pinned
            # while its one named pattern was `*`.
            branch_policy = _github(
                f"repos/{repository}/environments/{name}/deployment-branch-policies"
            )
        except GitHubUnreachable as error:
            print("environment_unreadable", file=sys.stderr)
            print(str(error), file=sys.stderr)
            return EXIT_UNUSABLE
        if not isinstance(payload, dict):
            print(f"environment_unreadable: {name} answered nothing", file=sys.stderr)
            return EXIT_UNUSABLE
        if not isinstance(branch_policy, dict):
            print(f"branch_policy_unreadable: {name} answered nothing", file=sys.stderr)
            return EXIT_UNUSABLE
        findings.extend(compare_gate(declared, read_environment(payload), inventory.admins))
        findings.extend(
            compare_the_branch_policy(declared, read_branch_policy_names(branch_policy))
        )

    routine_approvers = tuple(
        member.github_login
        for member in inventory.members
        if holds_routine_approver_role(inventory, member.github_login)
    )
    if options.check_team_membership:
        try:
            members = _github(f"orgs/{organization}/teams/{LEAD_APPROVAL_TEAM_SLUG}/members")
        except GitHubUnreachable as error:
            print("team_membership_unreadable", file=sys.stderr)
            print(str(error), file=sys.stderr)
            return EXIT_UNUSABLE
        if not isinstance(members, list):
            print("team_membership_unreadable", file=sys.stderr)
            return EXIT_UNUSABLE
        member_logins = tuple(
            str(entry["login"])
            for entry in members
            if isinstance(entry, dict) and isinstance(entry.get("login"), str)
        )
        findings.extend(compare_lead_team_membership(member_logins, routine_approvers))

    if findings:
        _report(findings)
        return EXIT_DRIFTED

    for gate in DECLARED_GATES:
        reviewers = (
            f"reviewed by {list(gate.reviewer_team_slugs)}"
            if gate.reviewer_team_slugs
            else "reviewed by the roster's admins"
            if gate.reviewer_logins_are_the_roster_admins
            else "reviewer-less by design"
        )
        branches = ", ".join(gate.branch_policy_names)
        print(f"{gate.name} is {reviewers} and deployable from {branches}, and GitHub agrees.")
    print(f"The repository is {visibility}, so the gate is a control GitHub still offers.")
    if options.check_team_membership:
        print(
            f"The {LEAD_APPROVAL_TEAM_SLUG} team and the "
            f"{len(routine_approvers)} approvers admission accepts are the same set."
        )
    else:
        # Never silence. A check that skipped something has to say which something, or the
        # green it prints is read as covering it.
        print(
            f"The {LEAD_APPROVAL_TEAM_SLUG} team's membership was NOT read, so who stands "
            "behind the lead gate's one reviewer slot is unchecked here. Listing a team needs "
            "the Members organization permission and an Actions GITHUB_TOKEN has none. What "
            "covers it is fixtures/evidence/phase-2/github/lead-team.sanitized.json, which "
            "expires after thirty days, and this tool with --check-team-membership from a "
            "laptop."
        )
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
