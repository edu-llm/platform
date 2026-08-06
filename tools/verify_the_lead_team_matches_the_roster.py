"""Who GitHub lets approve a run, against who `config/organization.yaml` says may.

WHAT GOES WRONG WITHOUT THIS. Routing sends most submissions to the `run-approval-lead`
environment and that environment's reviewer is the GitHub team `team-leads`. The roster's
`team_leads` list is what admission reads and what every document describes. Nothing has ever
compared the two, so the list that decides and the list that is written down have been free to
drift apart in silence, and the drift only shows up as somebody approving a run they were not
supposed to be able to approve, or a named lead finding they cannot approve one that was
routed to them.

MEASURED ON 2026-08-06 THEY DISAGREED. The roster declared eight leads and the GitHub team
held nine. The ninth was `BritishAmericqn`, who is a declared admin, so nothing widened: an
admin already holds `run-approval-admin` and approving as a lead grants them nothing they did
not have. That is the benign shape of this failure and it is the reason the admin case is
carved out below rather than counted as drift. The dangerous shape is the same set difference
with somebody in it who is neither.

WHY THIS ONE FAILS WHERE `tools/report_roster_against_the_account.py` DOES NOT. That tool
compares the roster against `Intern-*` roles and refuses to exit 1 by construction, because
anybody can self-serve an AWS role in five minutes and a roster change is a reviewed pull
request, so the account running ahead of the roster is an ordinary Tuesday. Nothing here is
ordinary. Membership of `team-leads` is a grant of approval authority over other people's
spending, it is changed by hand in the GitHub UI, and it is reviewed by nobody. A difference
in either direction is a fault.

BOTH DIRECTIONS ARE FAULTS AND THEY ARE DIFFERENT FAULTS. Somebody on the team the roster does
not name as a lead can approve what the roster says they cannot, which is a widening. A lead
the roster names who is not on the team cannot approve what routing sends them, which is not a
security problem and is a run sitting unapproved while the person named to approve it has no
button. The report separates them because the repair is different.

    uv run --frozen python tools/verify_the_lead_team_matches_the_roster.py

Through `gh api` rather than an AWS call, because this is a GitHub surface, and it runs on the
token a scheduled workflow already holds.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from edullm_platform.config import load_yaml
from edullm_platform.contracts.inventory import OrganizationInventory

__all__ = [
    "LEAD_TEAM_SLUG",
    "Disagreement",
    "build_parser",
    "compare",
    "main",
]

PROJECT_ROOT: Final = Path(__file__).resolve().parent.parent

#: The team GitHub asks when a run routes to `run-approval-lead`. Named here rather than read
#: from the roster because the roster does not carry it, which is itself part of why the two
#: drifted: nothing in the reviewed file points at the thing that actually holds the gate.
LEAD_TEAM_SLUG: Final = "team-leads"


@dataclass(frozen=True)
class Disagreement:
    """The two set differences, and the admins that explain part of one of them."""

    #: On the team, not a declared lead, not a declared admin. A widening.
    can_approve_unnamed: tuple[str, ...]
    #: On the team, not a declared lead, and a declared admin. Allowed, and said out loud.
    admins_on_the_team: tuple[str, ...]
    #: A declared lead who is not on the team. Cannot approve what routing sends them.
    named_and_cannot_approve: tuple[str, ...]

    @property
    def agree(self) -> bool:
        return not self.can_approve_unnamed and not self.named_and_cannot_approve


def compare(
    *, on_the_team: Sequence[str], declared_leads: Sequence[str], declared_admins: Sequence[str]
) -> Disagreement:
    """The comparison, over three lists and no network.

    Case is folded because GitHub treats a login case-insensitively and the roster is typed by
    hand. A roster saying `BritishAmericqn` and a team saying `britishamericqn` are the same
    person, and reporting them as two would be this check inventing a fault.
    """
    team = {login.lower(): login for login in on_the_team}
    leads = {login.lower() for login in declared_leads}
    admins = {login.lower() for login in declared_admins}

    unnamed = sorted(
        original
        for folded, original in team.items()
        if folded not in leads and folded not in admins
    )
    admin_shaped = sorted(
        original for folded, original in team.items() if folded not in leads and folded in admins
    )
    absent = sorted(login for login in declared_leads if login.lower() not in team)
    return Disagreement(
        can_approve_unnamed=tuple(unnamed),
        admins_on_the_team=tuple(admin_shaped),
        named_and_cannot_approve=tuple(absent),
    )


def _team_members(organization: str, team: str) -> list[str]:
    completed = subprocess.run(
        ["gh", "api", "--paginate", f"orgs/{organization}/teams/{team}/members?per_page=100"],
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "gh api failed")
    text = completed.stdout.strip() or "[]"
    try:
        parsed: Any = json.loads(text)
    except ValueError:
        # --paginate concatenates one array per page, so both shapes have to work.
        parsed = [
            item for page in text.replace("][", "]\n[").splitlines() for item in json.loads(page)
        ]
    return [member["login"] for member in parsed]


def build_parser() -> argparse.ArgumentParser:
    assert __doc__ is not None
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--organization", default="edu-llm")
    parser.add_argument("--team", default=LEAD_TEAM_SLUG)
    parser.add_argument(
        "--roster", type=Path, default=PROJECT_ROOT / "config" / "organization.yaml"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    inventory = load_yaml(options.roster, OrganizationInventory)

    try:
        members = _team_members(options.organization, options.team)
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as error:
        print(f"the lead team could not be read: {error}", file=sys.stderr)
        return 2

    found = compare(
        on_the_team=members,
        declared_leads=inventory.team_leads,
        declared_admins=inventory.admins,
    )

    print(
        f"The roster names {len(inventory.team_leads)} leads and "
        f"`{options.team}` holds {len(members)}."
    )
    for login in found.admins_on_the_team:
        print(f"  {login} is on the team and is a declared admin, which grants them nothing new.")
    for login in found.can_approve_unnamed:
        print(f"  {login} can approve as a lead and the roster names them neither lead nor admin.")
    for login in found.named_and_cannot_approve:
        print(f"  {login} is a named lead and is not on the team, so routing reaches nobody.")

    if found.agree:
        # Not "the two lists match", which would be false whenever an admin sits on the team
        # and would train a reader to ignore the line. What holds is the thing worth holding.
        print("Nobody can approve who the roster does not allow, and every named lead can.")
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
