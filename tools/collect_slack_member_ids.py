"""Match this organization's Slack accounts to the roster, and refuse rather than guess.

WHAT THIS IS FOR AND WHAT IT DELIBERATELY DOES NOT DO. A Slack member id is what lets a
message address a person rather than print their name, and there is an open design item about
using one in the approval notification. This collects the ids and stops. It writes nothing to
``config/organization.yaml``, posts nothing to Slack, and changes no notification. Read the
report, decide whether the mapping is worth having, and only then write any of it down.

**THE OBVIOUS IMPLEMENTATION IS WRONG AND THE ROSTER'S OWN DATA PROVES IT.** Corporate email
here looks like ``frank.gonzalez@alphaaiengineering.com``, so the tempting rule is to lowercase
``display_name``, join the two words with a dot, and match that against the email Slack
reports. That rule can be checked, because ``aws_identities`` in the roster carries
``Intern-<first>.<last>-sbsandbox`` role names for twenty people and those come out of the same
corporate directory the mail domain does. Checked on 2026-08-06:

- Nineteen of twenty agree.
- **One disagrees.** ``ninLi0`` is on the roster as ``Eric Ni`` and holds
  ``Intern-linjian.ni-sbsandbox``. The derived address is a different person's, or nobody's.
- One is ambiguous rather than wrong. ``meric233`` holds both ``Intern-meric.xing-sbsandbox``
  and ``Intern-langming.xing-sbsandbox``, and the roster records the owner confirming that the
  second spelling is the same human. Derivation picks one of two with nothing to break the tie.
- One cannot be derived at all. ``Yuen Kai Chow`` is three words and the rule needs two.
- **Fifteen roster members hold no role, so for them the rule is untested rather than
  confirmed.** A rule measured wrong once in twenty, on the only twenty it can be measured
  against, is not a rule to run over the other fifteen unsupervised.

So the derived address is not used as evidence anywhere below. It is computed only to be
reported as a hint beside a person nothing else resolved, clearly labelled, for the owner to
confirm or reject. That is the same treatment ``config/organization.yaml`` already gives a
near-matched W&B account, in that file's own words: an exact equality is accepted, a prefix or
a near-match is left blank until the owner confirms it, because a person filed wrongly is
indistinguishable from one filed correctly and the only reader placed to notice is the person
whose message went somewhere else.

WHAT COUNTS AS EVIDENCE HERE, STRONGEST FIRST.

1. **The email Slack reports has a local part equal to one of that person's AWS role names.**
   Both sides come from the corporate directory rather than from anybody's typing, so this is
   two independent systems agreeing. It is the rule that gets ``ninLi0`` right.
2. **The email local part equals the derived ``first.last``.** Only used for somebody the
   first rule cannot reach, and only when nothing else claims that account. Measured wrong
   once in twenty, so it is reported as a weaker match rather than mixed in with the rest.
3. **Slack's ``real_name`` equals ``display_name`` exactly, ignoring case and spacing.** An
   equality and never a prefix. ``Sophia Z`` is not ``Sophia Zhang`` and this must not say it
   is.

Anything else is unresolved and is printed as unresolved. There is no fuzzy matching in this
file and none should be added.

TWO-WAY AMBIGUITY IS A REFUSAL AND NOT A TIE TO BREAK. Two roster members resolving to one
Slack account, or one roster member resolving to two, exits 1 and emits neither. That is the
same ruling ``config/organization.yaml`` makes for a W&B account claimed twice and the same
one ``edullm``'s lane verbs make for an ambiguous AWS profile.

RUNNING IT.

    export SLACK_BOT_TOKEN='xoxb-...'
    uv run --frozen python tools/collect_slack_member_ids.py
    uv run --frozen python tools/collect_slack_member_ids.py --json > mapping.json

The token belongs to the ``eduLLM Notifications`` app and needs ``users:read`` and
``users:read.email``. **It is not the credential the platform posts with and must not become
one.** ``src/edullm_platform/notifications/delivery.py`` posts through an incoming webhook
whose URL lives in Secrets Manager as ``sbsandbox-intern-edullm-runs-webhook``, a webhook
cannot call ``users.list``, and that is why this needs a token at all. Put the token in an
environment variable for the one run and nowhere else. If a bot token ever has to be read by
something running unattended, it goes in Secrets Manager beside the webhook and never in a
repository secret, which ``tests/test_secrets.py`` forbids by name because any branch can read
one.

Exit codes follow the table in ``AGENTS.md``. 0 it ran, 1 something is ambiguous and no
mapping is emitted, 2 the tool could not be driven, 3 Slack could not be asked.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from edullm_platform.config import load_yaml
from edullm_platform.contracts.inventory import OrganizationInventory

#: Where the roster lives, relative to a checkout root.
ROSTER_PATH: Final = "config/organization.yaml"

SLACK_USERS_LIST: Final = "https://slack.com/api/users.list"

#: Slack caps a page at 1000 and recommends far less. 200 is its own documented suggestion,
#: and this roster fits in one page at that size with room to spare.
PAGE_SIZE: Final = 200

#: A guard against a paginator that never terminates. This organization has tens of accounts,
#: so anything past a handful of pages means the cursor is not advancing.
MAX_PAGES: Final = 25

REQUEST_TIMEOUT_SECONDS: Final = 30.0

#: Why a match was made, strongest first. The report groups by this and the reader is meant to
#: treat the last one differently from the first two, so it is a label rather than a boolean.
BY_AWS_ROLE: Final = "email matches an AWS role name"
BY_DISPLAY_NAME: Final = "Slack real name equals the roster display name"
BY_DERIVED_EMAIL: Final = "email matches first.last derived from the display name"

WEAK_EVIDENCE: Final = frozenset({BY_DERIVED_EMAIL})


@dataclass(frozen=True)
class SlackAccount:
    """One human Slack account, reduced to the three fields anything here reads."""

    member_id: str
    real_name: str
    email: str | None

    @property
    def email_local_part(self) -> str | None:
        if self.email is None or "@" not in self.email:
            return None
        return self.email.split("@", 1)[0].strip().lower()


@dataclass(frozen=True)
class Match:
    github_login: str
    account: SlackAccount
    evidence: str


@dataclass
class Report:
    matched: list[Match] = field(default_factory=list)
    #: Roster members nothing resolved, with a derived address beside each as a hint for the
    #: owner rather than as an answer.
    unresolved: list[tuple[str, str | None]] = field(default_factory=list)
    #: Roster members more than one Slack account claims, or Slack accounts more than one
    #: roster member claims. Either way nothing is emitted for anybody involved.
    ambiguous: list[str] = field(default_factory=list)
    #: Slack accounts this roster does not contain. Expected and not a problem: the workspace
    #: is not only this project.
    unclaimed_accounts: int = 0


def normalize_name(value: str) -> str:
    """Case and spacing removed, and nothing else.

    Deliberately not a fuzzy key. Stripping punctuation or dropping middle names would make
    ``Eric Wu`` and ``Eric Ruocheng Wu`` equal, and this file's whole argument is that a
    near-match must reach a person for confirmation rather than be resolved quietly.
    """
    return " ".join(value.split()).casefold()


def derive_email_local_part(display_name: str | None) -> str | None:
    """``first.last``, or ``None`` when the name is not exactly two words.

    **A hint and never evidence.** See the header for the measurement: one of the twenty
    people this could be checked against has a corporate address this rule gets wrong.
    """
    if display_name is None:
        return None
    words = display_name.split()
    if len(words) != 2:
        return None
    return f"{words[0].casefold()}.{words[1].casefold()}"


def aws_role_local_parts(inventory: OrganizationInventory) -> dict[str, set[str]]:
    """The ``<first>.<last>`` inside each person's ``Intern-*`` role names.

    A person may hold more than one, and ``meric233`` does. Both are kept: they are two
    spellings the directory has used for one human, so either matching is that human.
    """
    parts: dict[str, set[str]] = defaultdict(set)
    for binding in inventory.aws_identities.roles:
        name = binding.role_name
        if not name.startswith("Intern-"):
            continue
        stem = name[len("Intern-") :]
        stem = stem.removesuffix("-sbsandbox")
        if stem:
            parts[binding.github_login].add(stem.casefold())
    return dict(parts)


def slack_accounts(payload: Iterable[Mapping[str, Any]]) -> Iterator[SlackAccount]:
    """The human accounts out of a ``users.list`` member array.

    Bots, apps, deactivated accounts and Slackbot are dropped here rather than being matched
    and then filtered, so nothing downstream has to know they exist.
    """
    for member in payload:
        if member.get("deleted") or member.get("is_bot") or member.get("id") == "USLACKBOT":
            continue
        profile = member.get("profile") or {}
        real_name = (
            profile.get("real_name_normalized") or profile.get("real_name") or member.get("real_name") or ""
        )
        email = profile.get("email")
        member_id = member.get("id")
        if not isinstance(member_id, str) or not member_id:
            continue
        yield SlackAccount(
            member_id=member_id,
            real_name=str(real_name),
            email=str(email) if isinstance(email, str) and email else None,
        )


def match_roster(
    inventory: OrganizationInventory, accounts: Sequence[SlackAccount]
) -> Report:
    """Resolve each roster member to at most one Slack account, refusing every tie.

    Pure, so that ``tests/test_slack_member_ids.py`` can put every interesting shape through
    it without a token and without a network.
    """
    report = Report()
    roles = aws_role_local_parts(inventory)

    by_local_part: dict[str, list[SlackAccount]] = defaultdict(list)
    by_real_name: dict[str, list[SlackAccount]] = defaultdict(list)
    for account in accounts:
        local = account.email_local_part
        if local is not None:
            by_local_part[local].append(account)
        if account.real_name:
            by_real_name[normalize_name(account.real_name)].append(account)

    claimed: dict[str, list[str]] = defaultdict(list)
    for member in inventory.members:
        login = member.github_login
        found: list[tuple[SlackAccount, str]] = []

        for local in sorted(roles.get(login, set())):
            found.extend((account, BY_AWS_ROLE) for account in by_local_part.get(local, []))

        if not found and member.display_name:
            found.extend(
                (account, BY_DISPLAY_NAME)
                for account in by_real_name.get(normalize_name(member.display_name), [])
            )

        if not found:
            derived = derive_email_local_part(member.display_name)
            if derived is not None:
                found.extend(
                    (account, BY_DERIVED_EMAIL) for account in by_local_part.get(derived, [])
                )

        distinct = {account.member_id: (account, evidence) for account, evidence in found}
        if len(distinct) > 1:
            report.ambiguous.append(
                f"{login} is claimed by {len(distinct)} Slack accounts: "
                + ", ".join(sorted(account.member_id for account, _ in distinct.values()))
            )
            continue
        if not distinct:
            report.unresolved.append((login, derive_email_local_part(member.display_name)))
            continue
        account, evidence = next(iter(distinct.values()))
        report.matched.append(Match(github_login=login, account=account, evidence=evidence))
        claimed[account.member_id].append(login)

    contested = {member_id for member_id, logins in claimed.items() if len(logins) > 1}
    if contested:
        for member_id in sorted(contested):
            report.ambiguous.append(
                f"Slack account {member_id} is claimed by {len(claimed[member_id])} roster "
                f"members: {', '.join(sorted(claimed[member_id]))}"
            )
        report.matched = [
            match for match in report.matched if match.account.member_id not in contested
        ]

    resolved = {match.account.member_id for match in report.matched}
    report.unclaimed_accounts = sum(1 for a in accounts if a.member_id not in resolved)
    return report


def fetch_slack_users(token: str) -> list[SlackAccount]:
    """Every account in the workspace, following the cursor Slack hands back.

    ``urllib`` rather than a Slack library, which is the same choice
    ``notifications/delivery.py`` made and for the same reason: this repository ships a CLI
    people install, and a dependency added for one maintainer tool is a dependency in
    everybody's install.
    """
    accounts: list[SlackAccount] = []
    cursor = ""
    for _ in range(MAX_PAGES):
        query = {"limit": str(PAGE_SIZE)}
        if cursor:
            query["cursor"] = cursor
        request = urllib.request.Request(
            f"{SLACK_USERS_LIST}?{urllib.parse.urlencode(query)}",
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8"))
        if not body.get("ok"):
            error = body.get("error", "unknown")
            hint = ""
            if error == "missing_scope":
                hint = (
                    ". The token is missing a scope. This needs users:read and "
                    "users:read.email, and a scope added in the app configuration only takes "
                    "effect after the app is reinstalled to the workspace"
                )
            elif error in {"invalid_auth", "not_authed", "account_inactive"}:
                hint = (
                    ". Check SLACK_BOT_TOKEN holds the bot token from the eduLLM "
                    "Notifications app, which starts xoxb-, rather than a webhook URL"
                )
            raise RuntimeError(f"Slack refused users.list: {error}{hint}")
        accounts.extend(slack_accounts(body.get("members") or []))
        cursor = ((body.get("response_metadata") or {}).get("next_cursor") or "").strip()
        if not cursor:
            return accounts
    raise RuntimeError(f"users.list did not finish inside {MAX_PAGES} pages")


def render(report: Report, accounts: Sequence[SlackAccount]) -> str:
    lines: list[str] = []
    strong = [m for m in report.matched if m.evidence not in WEAK_EVIDENCE]
    weak = [m for m in report.matched if m.evidence in WEAK_EVIDENCE]

    lines.append(
        f"{len(accounts)} human Slack accounts, {len(strong) + len(weak)} matched to the "
        f"roster, {len(report.unresolved)} roster members unresolved."
    )
    if not any(account.email for account in accounts):
        lines.append("")
        lines.append(
            "No account carried an email address. That is what a token without "
            "users:read.email looks like, and it is not what an empty directory looks like. "
            "Add the scope, reinstall the app to the workspace, and run this again."
        )

    if strong:
        lines.append("")
        lines.append("Matched on evidence. These are safe to write down.")
        for match in sorted(strong, key=lambda m: m.github_login.casefold()):
            lines.append(
                f"  {match.github_login:24} {match.account.member_id:12} {match.evidence}"
            )
    if weak:
        lines.append("")
        lines.append(
            "Matched only on an address derived from the display name. That rule is measured "
            "wrong for one of the twenty people it could be checked against, so confirm each "
            "of these with the person before writing it down."
        )
        for match in sorted(weak, key=lambda m: m.github_login.casefold()):
            lines.append(
                f"  {match.github_login:24} {match.account.member_id:12} "
                f"{match.account.real_name}"
            )
    if report.unresolved:
        lines.append("")
        lines.append(
            "Unresolved. Nothing in Slack matched these on evidence. The second column is "
            "what the display name would derive to, printed as a lead to follow rather than "
            "as an answer."
        )
        for login, derived in sorted(report.unresolved, key=lambda pair: pair[0].casefold()):
            lines.append(f"  {login:24} {derived or '(display name is not two words)'}")
    if report.ambiguous:
        lines.append("")
        lines.append("Refused as ambiguous. Nothing is emitted for anybody named here.")
        for note in report.ambiguous:
            lines.append(f"  {note}")

    lines.append("")
    lines.append(
        f"{report.unclaimed_accounts} Slack accounts belong to nobody on this roster, which "
        "is expected: the workspace is not only this project."
    )
    lines.append(
        "Nothing was written. This tool does not edit config/organization.yaml and does not "
        "post to Slack."
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--roster", default=ROSTER_PATH, help=f"the roster to match against, default {ROSTER_PATH}"
    )
    parser.add_argument(
        "--json", action="store_true", help="print the mapping as one JSON document"
    )
    arguments = parser.parse_args(argv)

    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not token:
        print(
            "SLACK_BOT_TOKEN is not set. This needs the bot token from the eduLLM "
            "Notifications app, which starts xoxb- and holds users:read and "
            "users:read.email. The webhook the platform posts with cannot call users.list, "
            "so it will not do here.",
            file=sys.stderr,
        )
        return 2

    inventory = load_yaml(arguments.roster, OrganizationInventory)
    try:
        accounts = fetch_slack_users(token)
    except (urllib.error.URLError, TimeoutError, RuntimeError) as failure:
        print(f"Could not ask Slack: {failure}", file=sys.stderr)
        return 3

    report = match_roster(inventory, accounts)

    if arguments.json:
        print(
            json.dumps(
                {
                    "format_version": 1,
                    "roster_members": len(inventory.members),
                    "slack_accounts": len(accounts),
                    "matched": [
                        {
                            "github_login": match.github_login,
                            "slack_member_id": match.account.member_id,
                            "evidence": match.evidence,
                            "confirm_before_use": match.evidence in WEAK_EVIDENCE,
                        }
                        for match in sorted(report.matched, key=lambda m: m.github_login)
                    ],
                    "unresolved": [login for login, _ in sorted(report.unresolved)],
                    "ambiguous": report.ambiguous,
                },
                indent=2,
            )
        )
    else:
        print(render(report, accounts))

    return 1 if report.ambiguous else 0


if __name__ == "__main__":
    raise SystemExit(main())
