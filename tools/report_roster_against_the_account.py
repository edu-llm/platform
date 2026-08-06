"""Which ``Intern-*`` roles the roster does not name, and which roster members hold no role.

**THE GAP THIS REPORTS OPENED SILENTLY AND NOBODY NOTICED FOR TWO WEEKS.** Anyone in the
organization can self-serve AWS access in about five minutes -- install ``sb-aws-creds``, log
in with a company Google account, and the first AWS call provisions an
``Intern-<first>.<last>-sbsandbox`` role with ``AdministratorAccess`` under
``InternSandboxBoundary``. Admission reads ``config/organization.yaml`` and nothing tells that
file a role appeared. So a person can hold cloud credentials for a week and be refused by this
platform the first time they submit, and the only symptom before that moment is nothing at all.
Measured on 2026-08-05 the account held 43 ``Intern-*`` roles against a 35-person roster.

**IT IS INFORMATION AND NOT A FAILURE, AND THAT IS THE WHOLE DESIGN.** A person appearing in
the account before the roster is the normal order of events -- self-serving takes five minutes
and a roster change is a reviewed pull request -- so a red cross for it would be a red cross on
an ordinary Tuesday. ``tools/report_asks.py`` gives the general form of the argument and this
follows it exactly: **there is no exit code 1 here by construction.** 0 means the comparison was
made, whatever it found. 2 means it could not be made, which is a real failure and is red.

**THE JOIN IS EXACT AND THE SUGGESTION IS NOT, AND THEY ARE PRINTED IN DIFFERENT COLUMNS.**
This is the part an earlier attempt got wrong in both directions. The roster is keyed on GitHub
logins and the roles are named from email addresses, so there is no reliable string relation
between the two -- ``gorpyshortlegs`` is Arhant Choudhary and nothing about either says so. What
this tool joins on is neither: it is ``aws_identities.roles`` in ``config/organization.yaml``, a
reviewed one-to-one table of role name to login. Both findings below are exact set differences
over role names and over logins, with no name comparison anywhere in them.

The fuzzy part is confined to one column and is never a finding. Given an unnamed role, the
report offers the roster member whose ``display_name`` equals the role's ``<first>.<last>``
read as a name, and labels it a suggestion. :func:`person_the_role_is_named_after` says what
that cannot resolve; the short version is that ``Intern-langming.xing-sbsandbox`` is the roster's
Meric Xing and no string comparison will ever find it.

**WHAT STOPS THIS BEING A CHECK THAT CANNOT FAIL.** A roster comparison whose account read came
back empty reports no unnamed roles, which is indistinguishable from a clean account. This
account has held between twenty and fifty ``Intern-*`` roles every day of its existence and a
reading of zero is a broken read rather than a tidy one, so it is exit 2 rather than a clean
report. The report also prints how many unnamed roles it could parse a name out of at all, so a
broken parser shows up as a zero somebody can see rather than as an empty suggestion column.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent
if str(PROJECT_ROOT / "src") not in sys.path:  # pragma: no cover - import wiring
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from edullm_platform.capture_tooling import CaptureFailedError, aws_json
from edullm_platform.config import load_yaml
from edullm_platform.contracts.inventory import OrganizationInventory

__all__ = [
    "ACCOUNT_HOLDS_NO_INTERN_ROLES",
    "INVENTORY_PATH",
    "MISSING_LIST_ROLES_GRANT",
    "ROLE_PREFIX",
    "ROLE_SUFFIX",
    "AccountRole",
    "UnnamedRole",
    "build_parser",
    "main",
    "members_holding_no_role",
    "person_the_role_is_named_after",
    "read_account_roles",
    "roles_the_roster_does_not_name",
    "roster_members_named",
]

INVENTORY_PATH: Final = "config/organization.yaml"

#: What the self-serve broker calls the roles it makes. A role is examined because it carries
#: this prefix and is then reported whatever else is true of it, which is why the two
#: ``Intern-p3math-*`` task roles land here rather than being filtered out: a prefix used as a
#: filter would quietly drop the next role somebody names unexpectedly.
ROLE_PREFIX: Final = "Intern-"

#: The account this platform runs in. Part of the role name rather than of the person's, so it
#: comes off before a name is read out of what is left.
ROLE_SUFFIX: Final = "-sbsandbox"

#: The reason code for an account read that came back empty. Named rather than written inline
#: because it is the guard that stops this whole report being vacuously clean, and a guard
#: worth having is a guard worth being able to grep for.
ACCOUNT_HOLDS_NO_INTERN_ROLES: Final = "account_holds_no_intern_roles"

#: Quoted character-for-character as ``infra/iam/audit-reader-role.yaml`` grants it, for the
#: reason ``tools/visibility_board.py`` gives about its own two: this string is printed into
#: the 05:00 report as the thing to paste, so a second spelling here would mean whoever pastes
#: it changes the role into something no test covers.
MISSING_LIST_ROLES_GRANT: Final = """\
- Sid: ListTheInternRolesTheRosterIsComparedAgainst
  Effect: Allow
  Action: iam:ListRoles
  Resource: "*"\
"""


@dataclass(frozen=True, slots=True)
class AccountRole:
    """One ``Intern-*`` role as ``iam:ListRoles`` describes it.

    **THERE IS NO LAST-USED HERE AND ITS ABSENCE IS A DECISION.** ``ListRoles`` does not return
    ``RoleLastUsed`` at all -- only ``GetRole`` does, one call per role -- so a version of this
    that asked for it would be 43 further calls behind a second grant, and the first draft of
    this tool queried the field off ``ListRoles`` and printed "never" against every role in the
    account, including ones assumed that morning. A confidently wrong column is worse than an
    absent one. Whoever triages a row runs ``aws iam get-role --role-name <name>`` and gets it
    in a second; the daily report answers who is here that the roster does not know, and the
    creation date is what dates that.
    """

    role_name: str
    created_at: str | None = None


@dataclass(frozen=True, slots=True)
class UnnamedRole:
    """One role in the account that ``config/organization.yaml`` neither binds nor excludes.

    ``suggested_login`` is a hint and never a resolution. It is populated only by an equality
    between the name read out of the role and a roster member's ``display_name``, and the
    report prints it under a heading that says so.
    """

    role: AccountRole
    reads_as_a_name: str | None
    suggested_logins: tuple[str, ...]


def person_the_role_is_named_after(role_name: str) -> str | None:
    """``Intern-first.last-sbsandbox`` read as ``First Last``, or ``None`` where it is not one.

    **THIS IS THE ONLY FUZZY THING IN THE FILE AND IT DECIDES NOTHING.** What it produces is
    offered as a suggestion beside an exactly-computed finding, so a wrong answer costs a
    misleading hint and never a wrong count.

    Four things it cannot resolve, each of them live in this account rather than hypothetical:

    * **A person whose work name is not their roster name.**
      ``Intern-langming.xing-sbsandbox`` is the roster's ``meric233``, display name Meric
      Xing, confirmed by the owner on 2026-08-04 when the same person's W&B account was
      resolved. No comparison of strings finds that, and none should be made to.
    * **A role that is not a person.** ``Intern-p3math-olmo370-eval-20260731`` has no
      ``<first>.<last>`` shape, so this answers ``None`` and the role is still reported.
    * **A roster member with no display name recorded.** ``display_name`` is optional, so
      there is nothing on their side to compare against and they can never be suggested.
    * **A second role for somebody already bound.** This says who a role reads as; it does not
      say whether that person already holds another role. The report says that separately,
      because a second role for a bound person and a first role for a new one look identical
      here and are different changes to make.
    """
    if not role_name.startswith(ROLE_PREFIX) or not role_name.endswith(ROLE_SUFFIX):
        return None
    local = role_name[len(ROLE_PREFIX) : -len(ROLE_SUFFIX)]
    parts = local.split(".")
    if len(parts) != 2 or not all(part.isalpha() for part in parts):
        return None
    return " ".join(part.capitalize() for part in parts)


def roster_members_named(inventory: OrganizationInventory, reads_as: str | None) -> tuple[str, ...]:
    """Every roster login whose ``display_name`` is exactly this name, case-insensitively.

    A tuple rather than one answer, because two members could share a display name and picking
    the first would be the fuzzy match pretending to be a lookup. Empty is the common case and
    is the honest one.
    """
    if reads_as is None:
        return ()
    wanted = reads_as.casefold()
    return tuple(
        member.github_login
        for member in inventory.members
        if member.display_name is not None and member.display_name.casefold() == wanted
    )


def roles_the_roster_does_not_name(
    account_roles: Sequence[AccountRole], inventory: OrganizationInventory
) -> tuple[UnnamedRole, ...]:
    """Every ``Intern-*`` role in the account that is neither bound to a login nor excluded.

    An exact set difference over role names. Both sides are literal strings that IAM and the
    reviewed table agree on character for character, so nothing here can be off by a near-match
    in either direction.
    """
    accounted = set(inventory.aws_identities.role_logins()) | set(
        inventory.aws_identities.excluded_role_names()
    )
    unnamed: list[UnnamedRole] = []
    for role in sorted(account_roles, key=lambda entry: entry.role_name):
        if not role.role_name.startswith(ROLE_PREFIX) or role.role_name in accounted:
            continue
        reads_as = person_the_role_is_named_after(role.role_name)
        unnamed.append(
            UnnamedRole(
                role=role,
                reads_as_a_name=reads_as,
                suggested_logins=roster_members_named(inventory, reads_as),
            )
        )
    return tuple(unnamed)


def members_holding_no_role(inventory: OrganizationInventory) -> tuple[str, ...]:
    """Every roster login with no row in ``aws_identities.roles``.

    Exact, and in the other direction from the case above. This half is not a defect either:
    somebody can be on the roster and simply not have logged into AWS yet, which is the
    ordinary state of a new person between their invitation and their first run.
    """
    bound = {
        binding.github_login.casefold() for binding in inventory.aws_identities.roles
    }
    return tuple(
        member.github_login
        for member in sorted(inventory.members, key=lambda entry: entry.github_login.casefold())
        if member.github_login.casefold() not in bound
    )


def read_account_roles(*, profile: str | None, region: str) -> tuple[AccountRole, ...]:
    """Every ``Intern-*`` role IAM will name, and when each was made.

    ``--query`` rather than filtering here, because this is a shared account holding several
    hundred roles and the two fields below are all that is read. The prefix filter is applied
    afterwards rather than in the query, so that the count of what IAM answered with is a real
    reading and an empty answer is distinguishable from an answer with no ``Intern-*`` in it.
    """
    answer = aws_json(
        ["iam", "list-roles", "--query", "Roles[].[RoleName,CreateDate]"],
        profile=profile,
        region=region,
    )
    if not isinstance(answer, list):
        raise CaptureFailedError("list_roles_answered_with_something_that_is_not_a_list")
    found: list[AccountRole] = []
    for row in answer:
        if not isinstance(row, list) or not row or not isinstance(row[0], str):
            continue
        if not row[0].startswith(ROLE_PREFIX):
            continue
        created = row[1] if len(row) > 1 else None
        found.append(
            AccountRole(
                role_name=row[0], created_at=created if isinstance(created, str) else None
            )
        )
    return tuple(found)


def _markdown(
    *,
    account_roles: Sequence[AccountRole],
    unnamed: Sequence[UnnamedRole],
    unheld: Sequence[str],
    inventory: OrganizationInventory,
) -> str:
    bound = len(inventory.aws_identities.roles)
    excluded = len(inventory.aws_identities.excluded_role_names())
    parsed = sum(1 for entry in unnamed if entry.reads_as_a_name is not None)
    lines = [
        "## The roster against the account",
        "",
        (
            f"The account holds {len(account_roles)} `{ROLE_PREFIX}*` roles. "
            f"`{INVENTORY_PATH}` binds {bound} of them to a roster login and excludes "
            f"{excluded} as nobody's, and carries {len(inventory.members)} members."
        ),
        "",
        (
            "Neither list below is a defect. Somebody self-serves AWS access in about five "
            "minutes and a roster change is a reviewed pull request, so a person reaching "
            "the account first is the ordinary order of events. What this reports is that "
            "the two have drifted and by how much, which is the thing nothing else says "
            "out loud."
        ),
        "",
    ]

    lines += [
        f"### {len(unnamed)} roles the roster does not name",
        "",
    ]
    if unnamed:
        lines += [
            (
                "Joined by role name against `aws_identities`, which is exact. The last "
                "column is a **suggestion only**, from the role's name read as `First Last` "
                f"and compared to a member's `display_name`; {parsed} of these "
                f"{len(unnamed)} roles have a name that can be read that way at all. A "
                "suggestion is not a resolution, and adding somebody on the strength of one "
                "is the mistake this column exists to make visible rather than to make for "
                "you."
            ),
            "",
            "| Role | Created | Reads as | Roster member of that name |",
            "| --- | --- | --- | --- |",
        ]
        for entry in unnamed:
            suggestion = ", ".join(f"`{login}`" for login in entry.suggested_logins) or ""
            lines.append(
                f"| `{entry.role.role_name}` | {entry.role.created_at or 'unknown'} "
                f"| {entry.reads_as_a_name or ''} | {suggestion} |"
            )
        lines += [
            "",
            (
                "A role here is somebody who can reach AWS and would be refused by `edullm "
                "submit`. It is not necessarily somebody who should be on the roster: this "
                "account is shared with about a dozen unrelated projects, and adding a "
                "stranger to a research roster is worse than leaving them off."
            ),
        ]
    else:
        lines.append("Every `Intern-*` role in the account is bound to a login or excluded.")
    lines.append("")

    lines += [
        f"### {len(unheld)} roster members holding no role",
        "",
    ]
    if unheld:
        lines += [
            (
                "These people are authorized to submit and have not appeared in AWS under a "
                "name this file binds. Most of them have simply not logged in yet, which "
                "costs nothing; what it does cost is that anything they launch outside this "
                "platform is reported by role name and attributed to nobody."
            ),
            "",
            ", ".join(f"`{login}`" for login in unheld),
        ]
    else:
        lines.append("Every roster member has a bound role.")
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    assert __doc__ is not None
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", default=None)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--json", action="store_true", help="machine-readable instead of markdown")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    inventory = load_yaml(PROJECT_ROOT / INVENTORY_PATH, OrganizationInventory)

    try:
        account_roles = read_account_roles(profile=arguments.profile, region=arguments.region)
    except CaptureFailedError as error:
        print(f"roster_comparison_unmade: {error.reason}", file=sys.stderr)
        print(
            "The account was not read, so this run says nothing about the roster either way. "
            "A denial here is usually iam:ListRoles, which "
            "infra/iam/audit-reader-role.yaml grants as:\n"
            f"{MISSING_LIST_ROLES_GRANT}\n"
            "That stack is applied by hand from a laptop; infra/README.md has the name.",
            file=sys.stderr,
        )
        return 2

    # THE GUARD THAT STOPS THIS BEING A CHECK THAT CANNOT FAIL. An empty account read produces
    # an empty unnamed list, which prints as a tidy account and is indistinguishable from one.
    # This account has never held zero of these roles, so zero is a broken read.
    if not account_roles:
        print(f"roster_comparison_unmade: {ACCOUNT_HOLDS_NO_INTERN_ROLES}", file=sys.stderr)
        print(
            f"IAM named no `{ROLE_PREFIX}*` role at all. That is not a tidy account, it is a "
            "read that did not work: every person with AWS access in this account has one. "
            "Reporting a clean comparison off it would be a check passing because it could "
            "not look.",
            file=sys.stderr,
        )
        return 2

    unnamed = roles_the_roster_does_not_name(account_roles, inventory)
    unheld = members_holding_no_role(inventory)

    if arguments.json:
        payload: dict[str, Any] = {
            "intern_roles_in_the_account": len(account_roles),
            "roles_the_roster_does_not_name": [
                {
                    "role_name": entry.role.role_name,
                    "created_at": entry.role.created_at,
                    "reads_as_a_name": entry.reads_as_a_name,
                    "suggested_logins": list(entry.suggested_logins),
                }
                for entry in unnamed
            ],
            "members_holding_no_role": list(unheld),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(
        _markdown(
            account_roles=account_roles, unnamed=unnamed, unheld=unheld, inventory=inventory
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
