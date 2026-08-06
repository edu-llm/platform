"""Whether the GitHub team that opens the lead gate still matches the roster, and how stale
the answer is.

**WHAT THIS IS FOR IS THE STALENESS, NOT THE COMPARISON.** The comparison already exists.
``tests/test_phase2_github_evidence.py`` reads
``fixtures/evidence/phase-2/github/lead-team.sanitized.json`` and holds it against
``holds_routine_approver_role`` in both directions on every push. What did not exist is anything
that asks the question on a clock. The capture is taken by hand, ``FreshEvidenceModel`` refuses
it after thirty days, and until that moment a suite full of green ticks is reporting on whatever
GitHub looked like the day somebody last ran the capture tool. Thirty days is also exactly how
long the ninth member of that team went unnoticed.

**SO THE FINDING HERE IS AN AGE AND THE COMPARISON IS CARRIED ALONGSIDE IT.** A capture older
than ``config/reports/lead-gate.yaml`` says is red, because past that point this job is
reporting on the past and saying nothing about today. A capture inside the window that disagrees
with the roster is also red, for the reason the two directions are different incidents: a login
on the team the roster does not authorize opens the gate and is then refused by admission, which
spends an approval on a no, and an authorized login absent from the team is somebody the lead
gate will never ask, their own group's run included. Both were live at once for two days ending
2026-07-30.

**THIS CANNOT READ GITHUB AND IS NOT PRETENDING TO.** ``orgs/edu-llm/teams/team-leads/members``
needs the Members organization permission, the Actions ``GITHUB_TOKEN`` has none, and a stored
PAT would be a repository secret ``test_the_repository_holds_no_secret_a_branch_could_read``
forbids by name. ``config/organization.yaml`` records that at length and concluded a scheduled
check could not be had. That conclusion was right about reading the team live and wrong about
there being nothing to schedule: what a schedule can do without any credential at all is refuse
to let a hand capture stand quietly, which is the half of the problem that let the ninth member
sit unnoticed for a month.

**IT NEEDS NO CREDENTIAL, NO ROLE AND NO REPOSITORY VARIABLE.** Two files in the checkout. That
makes it the second job on the audit, beside ``tools/report_asks.py``, that cannot fail for a
reason outside the question it was asked.

Exit 0 when the capture stands and the two agree. Exit 1 when it does not stand or they do not.
Exit 2 when the comparison could not be made at all, which is a capture that is absent or is not
what its contract accepts, and which says nothing about the roster in either direction.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

from pydantic import Field, ValidationError

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent
if str(PROJECT_ROOT / "src") not in sys.path:  # pragma: no cover - import wiring
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from edullm_platform.config import load_yaml
from edullm_platform.contracts.authorization import holds_routine_approver_role
from edullm_platform.contracts.base import ContractModel
from edullm_platform.contracts.inventory import OrganizationInventory, normalize_github_login

__all__ = [
    "CAPTURE_PATH",
    "CAPTURE_UNREADABLE",
    "INVENTORY_PATH",
    "LEAD_GATE_CONFIG_PATH",
    "LeadGateSettings",
    "build_parser",
    "capture_stands",
    "days_old",
    "main",
    "who_the_gate_and_the_roster_disagree_about",
]

#: The committed capture, and the one this reads rather than any other. A capture of some other
#: team validates against the same contract and would compare cleanly against nothing.
CAPTURE_PATH: Final = "fixtures/evidence/phase-2/github/lead-team.sanitized.json"
INVENTORY_PATH: Final = "config/organization.yaml"
LEAD_GATE_CONFIG_PATH: Final = "config/reports/lead-gate.yaml"

#: Printed on stderr when there is no comparison to report, so a reader can tell "the two
#: disagree" from "nobody has said what the team holds". The two look identical on a board that
#: only carries a red cross, and they need opposite repairs.
CAPTURE_UNREADABLE: Final = "lead_gate_capture_unreadable"


class LeadGateSettings(ContractModel):
    schema_version: Literal[1]
    capture_stands_for_days: int = Field(gt=0)


def days_old(observed_at: datetime, *, now: datetime) -> float:
    """How long ago the capture was taken, in days.

    A float rather than a whole number of days, because the report says the age out loud and a
    capture taken thirty-six hours ago reading as "1 day old" is the kind of rounding somebody
    argues with. Compared against the threshold at full precision for the same reason.
    """
    return (now - observed_at).total_seconds() / 86400.0


def capture_stands(age: float, *, stands_for_days: int) -> bool:
    """Whether a capture of this age is still worth believing.

    Inclusive, and a function rather than an inline comparison so the edge can be tested at the
    edge. Through the report the age is a live subtraction from ``datetime.now``, so a test
    aiming at exactly the threshold lands a few microseconds past it and a strict comparison
    survives every mutation the report can be driven through.

    config/reports/lead-gate.yaml says a capture of exactly the stated age still stands. A
    strict comparison moves the window by a day without anybody deciding to, which is the
    argument config/reports/asks.yaml makes about its own threshold.
    """
    return age <= stands_for_days


def who_the_gate_and_the_roster_disagree_about(
    *, member_logins: Sequence[str], approvers: frozenset[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The two directions, as two sets, because they are two different incidents.

    First: logins on the GitHub team that the roster does not make routine approvers. Each one
    can open the lead gate and is then refused by admission with
    ``approver_lacks_lead_or_admin_role``, which spends somebody's approval on a no.

    Second: routine approvers absent from the GitHub team. Each one is somebody the lead gate
    will never ask, including for their own group's runs, and ``can_admins_bypass`` is false on
    that environment so being an admin does not help.

    Compared on normalized logins, because GitHub is case-insensitive about them and the roster
    is written the way people spell their own names.
    """
    on_the_team = {normalize_github_login(login) for login in member_logins}
    return (
        tuple(sorted(on_the_team - approvers)),
        tuple(sorted(approvers - on_the_team)),
    )


def _routine_approvers(inventory: OrganizationInventory) -> frozenset[str]:
    """Everybody admission accepts as the approver of a routine run.

    Asked of ``holds_routine_approver_role`` rather than assembled from ``team_leads``, which is
    the mistake ``tests/test_phase2_github_evidence.py`` records making. The set the gate has to
    match is ``admins | team_leads``, and an admin on the team read as drift is a finding whose
    only repair the roster's own tests refuse.
    """
    return frozenset(
        member.normalized_github_login
        for member in inventory.members
        if holds_routine_approver_role(inventory, member.github_login)
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help="the checkout to read the capture and the roster from",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    root = Path(arguments.project_root)

    settings = load_yaml(root / LEAD_GATE_CONFIG_PATH, LeadGateSettings)
    inventory = load_yaml(root / INVENTORY_PATH, OrganizationInventory)
    approvers = _routine_approvers(inventory)

    capture_path = root / CAPTURE_PATH
    try:
        captured = json.loads(capture_path.read_text(encoding="utf-8"))
        observed_at = datetime.fromisoformat(str(captured["observed_at"])).astimezone(UTC)
        member_logins = [str(login) for login in captured["member_logins"]]
    except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
        print(CAPTURE_UNREADABLE, file=sys.stderr)
        print(
            f"{CAPTURE_PATH} could not be read as a capture of the team: {exc}. This run says "
            "nothing about the lead gate in either direction. Re-capture with "
            "tools/capture_phase2_evidence.py --target lead-team.",
            file=sys.stderr,
        )
        return 2

    # READ OUT OF THE RAW JSON AND NOT THROUGH LeadTeamMembership, WHICH IS THE ONE PLACE THIS
    # TOOL DELIBERATELY BYPASSES A CONTRACT. FreshEvidenceModel refuses to load a capture past
    # thirty days, so a stale record raises rather than parsing -- and this job's whole finding
    # is the age. Validating first would turn the interesting answer into the unreadable one.
    age = days_old(observed_at, now=datetime.now(tz=UTC))
    stands = capture_stands(age, stands_for_days=settings.capture_stands_for_days)
    unauthorized, unasked = who_the_gate_and_the_roster_disagree_about(
        member_logins=member_logins, approvers=approvers
    )

    print("## Who can open the lead gate\n")
    print(
        f"The committed capture of `team-leads` was taken {age:.1f} days ago, on "
        f"{observed_at.strftime('%Y-%m-%d')}, and {LEAD_GATE_CONFIG_PATH} lets one stand for "
        f"{settings.capture_stands_for_days}. It lists {len(member_logins)} logins against "
        f"{len(approvers)} the roster makes routine approvers.\n"
    )
    print(
        "This compares a committed file against a committed file. Nothing here reads GitHub, "
        "because the token a scheduled job holds cannot, so what a clean run means is that "
        "the last hand capture agreed with the roster and is recent enough to be worth "
        "believing.\n"
    )

    if not stands:
        print(
            f"**The capture no longer stands.** It is older than "
            f"{settings.capture_stands_for_days} days, so the agreement below is a statement "
            "about the day it was taken. Re-take it, which needs no AWS session:\n"
        )
        print("```bash")
        print("uv run python tools/capture_phase2_evidence.py --target lead-team \\")
        print("  --output-dir docs-frank/working/phase-2-evidence")
        print("```\n")

    if unauthorized:
        print(
            "### On the team, and not a routine approver\n\n"
            "Each opens `run-approval-lead` and is then refused by admission with "
            "`approver_lacks_lead_or_admin_role`, which spends an approval on a no. Either "
            f"remove them from the GitHub team or give them a group to lead in {INVENTORY_PATH}.\n"
        )
        print(", ".join(f"`{login}`" for login in unauthorized) + "\n")

    if unasked:
        print(
            "### A routine approver the lead gate will never ask\n\n"
            "Admission accepts each of these as an approver and the GitHub team does not hold "
            "them, so the gate cannot route to them, their own group's runs included. "
            "`can_admins_bypass` is false on that environment, so being an admin does not "
            "help. Adding them is an owner action in the organization settings.\n"
        )
        print(", ".join(f"`{login}`" for login in unasked) + "\n")

    if stands and not unauthorized and not unasked:
        print("The team and the roster agree, and the capture is recent enough to say so.")
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
