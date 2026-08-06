"""A launch by a roster principal that lineage knows nothing about, and its denominator.

**THE LIST IS ONLY AS LONG AS THE TABLE IT JOINS THROUGH, AND A SHORT LIST LOOKS EXACTLY LIKE
A CLEAN DAY.** The join runs from a launch event's session issuer role name, through
``config/organization.yaml``'s ``aws_identities`` table, to a roster login. A role the table
does not carry is not a roster principal as far as this can tell, so its launches are filtered
out -- and a filtered launch produces no mismatch, no error and no trace. The account holds 43
``Intern-*`` roles and twenty of them are the roster's, so a missing row is the ordinary
state of this table rather than an exotic failure.

**SO THIS REPORTS ITS DENOMINATOR AND :func:`render_section` PRINTS IT.** Every event lands in
exactly one of four buckets -- accounted for, a mismatch, launched by a role nobody has bound,
or launched by a role somebody excluded by name -- and all four are reported.
:attr:`MismatchReport.adds_up` holds the arithmetic to that, and :attr:`MismatchReport.is_clean`
is false when anything is unresolved even where no mismatch was found. Zero-because-clean and
zero-because-broken are different sentences here.

**THE EXCLUSION IS A LIST OF LITERAL NAMES AND WILL NEVER BE A PATTERN.** A prefix match would
swallow the next role that happens to match it, with no line anywhere recording that it stopped
being looked at, which is the same failure as a missing binding and strictly harder to see.
Excluded launches are counted and reported rather than dropped, for the reason the denominator
exists at all.

**WHAT THIS IS BLIND TO IS RECORDED RATHER THAN DISCOVERED.** Four things: a roster member
acting through an identity the table does not list, the administrators outside the roster, the
roster members holding no AWS role at all, and spend that is not a launch.
:func:`render_section` says the last three out loud rather than leaving a reader to find them.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from edullm_platform.substrate import LaunchEvent

__all__ = [
    "INTERN_ROLE_PREFIX",
    "LaunchEvent",
    "Mismatch",
    "MismatchReport",
    "RoleTally",
    "compute_mismatches",
    "render_line",
    "render_section",
]

#: What every human role in this account is called. Used to say "this unresolved role looks
#: like a person" in a report and NEVER to decide whether an event is examined: a highlight
#: that is wrong reports one row oddly, and a filter that is wrong loses a person. Two roles
#: in the account carry the prefix and are task roles rather than people, which is the same
#: point from the other side.
INTERN_ROLE_PREFIX: Final = "Intern-"


@dataclass(frozen=True)
class Mismatch:
    event_id: str
    event_name: str
    occurred_at: datetime
    role_name: str
    github_login: str


@dataclass(frozen=True)
class RoleTally:
    role_name: str
    launches: int

    @property
    def looks_like_a_person(self) -> bool:
        return self.role_name.startswith(INTERN_ROLE_PREFIX)


@dataclass(frozen=True)
class MismatchReport:
    """What the join found, and everything it did not join."""

    events_examined: int
    mismatches: tuple[Mismatch, ...]
    accounted: int
    resolved: tuple[RoleTally, ...]
    unresolved: tuple[RoleTally, ...]
    excluded: tuple[RoleTally, ...]

    @property
    def unresolved_launches(self) -> int:
        return sum(tally.launches for tally in self.unresolved)

    @property
    def excluded_launches(self) -> int:
        return sum(tally.launches for tally in self.excluded)

    @property
    def unresolved_people(self) -> tuple[RoleTally, ...]:
        return tuple(tally for tally in self.unresolved if tally.looks_like_a_person)

    @property
    def adds_up(self) -> bool:
        """Whether every event examined reached exactly one bucket.

        False is a defect in this module rather than a finding about the account, and the
        renderer says so, because a denominator that does not add up cannot be read as one.
        """
        return self.events_examined == (
            self.accounted
            + len(self.mismatches)
            + self.unresolved_launches
            + self.excluded_launches
        )

    @property
    def is_clean(self) -> bool:
        """Whether this morning is genuinely quiet, which needs both halves.

        An empty mismatch list is half of it. The other half is that every role that launched
        anything was one somebody had already written down, because otherwise the list is
        short rather than empty and the two look identical.
        """
        return not self.mismatches and not self.unresolved and self.adds_up


def _tallies(counts: Mapping[str, int]) -> tuple[RoleTally, ...]:
    """Roles ordered by how much they launched, then by name, so the report is stable."""
    return tuple(
        RoleTally(role_name=name, launches=count)
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    )


def compute_mismatches(
    events: Iterable[LaunchEvent],
    *,
    role_logins: Mapping[str, str],
    excluded_roles: Collection[str],
    known_run_ids: Collection[str],
) -> MismatchReport:
    """Sort every launch into one of four buckets and report all four.

    ``excluded_roles`` is checked first and ``role_logins`` second. The order is deliberate
    and the contract in ``config/organization.yaml`` refuses a role in both lists, so the two
    can never disagree about one role; checking the exclusion first means that if that
    contract is ever loosened, an excluded role is excluded rather than half-reported.
    """
    mismatches: list[Mismatch] = []
    accounted = 0
    resolved_counts: dict[str, int] = {}
    unresolved_counts: dict[str, int] = {}
    # Every excluded name starts at zero so that the report says the exclusion was applied
    # even on a day the excluded role launched nothing. An exclusion nobody can see is one
    # nobody re-judges when the role it names is retired.
    excluded_counts: dict[str, int] = {name: 0 for name in excluded_roles}

    examined = 0
    for event in events:
        examined += 1
        if event.role_name in excluded_counts:
            excluded_counts[event.role_name] += 1
            continue
        login = role_logins.get(event.role_name)
        if login is None:
            unresolved_counts[event.role_name] = unresolved_counts.get(event.role_name, 0) + 1
            continue
        resolved_counts[event.role_name] = resolved_counts.get(event.role_name, 0) + 1
        if event.run_id is not None and event.run_id in known_run_ids:
            accounted += 1
            continue
        mismatches.append(
            Mismatch(
                event_id=event.event_id,
                event_name=event.event_name,
                occurred_at=event.occurred_at,
                role_name=event.role_name,
                github_login=login,
            )
        )

    return MismatchReport(
        events_examined=examined,
        mismatches=tuple(
            sorted(mismatches, key=lambda found: (found.occurred_at, found.event_id))
        ),
        accounted=accounted,
        resolved=_tallies(resolved_counts),
        unresolved=_tallies(unresolved_counts),
        excluded=_tallies(excluded_counts),
    )


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    """``2 roles``, and ``2 mismatches`` rather than ``2 mismatchs``.

    The irregular form is a parameter rather than a rule, because only two words on this
    surface need one and both are sibilant: the word the surface is named after, and the
    word for what it counts. A wrong plural in the first figure of a report is the kind of
    thing that makes a reader distrust the arithmetic under it.
    """
    return f"{count} {singular}" if count == 1 else f"{count} {plural or singular + 's'}"


def render_line(report: MismatchReport) -> str:
    """The mismatch sentence the morning message carries, denominator included.

    THE MISMATCHES ARE A COUNT AND THE UNRESOLVED ROLES ARE NAMED, WHICH LOOKS INCONSISTENT
    AND IS NOT. A mismatch is adjudicated by opening the activity document and looking at a
    run, so a name in the message buys nothing and costs the space the burn rate takes. An
    unresolved role named like a person is adjudicated by adding one line to
    ``config/organization.yaml``, and that line needs the name -- and there are at most a
    handful, usually none, because every AWS service role is unresolved too and none of them
    carries the prefix.

    The denominator is on the same line rather than in a footnote: a reader who sees
    "0 mismatches" and stops reading has to have seen the second half of the sentence to have
    read anything at all.
    """
    parts = [
        _plural(len(report.mismatches), "mismatch", "mismatches"),
        f"out of {_plural(report.events_examined, 'launch event')} examined",
        f"{_plural(len(report.resolved), 'role')} resolved",
        f"{_plural(len(report.unresolved), 'role')} not",
    ]
    if report.excluded:
        set_aside = ", ".join(
            f"{tally.role_name} ({tally.launches})" for tally in report.excluded
        )
        parts.append(f"excluded by name: {set_aside}")
    if report.unresolved_people:
        people = ", ".join(tally.role_name for tally in report.unresolved_people)
        parts.append(f"unresolved and named like a person: {people}")
    if not report.adds_up:
        parts.append("and the buckets do not add up, so read none of this")
    return "; ".join(parts) + "."


def render_section(report: MismatchReport) -> str:
    """The mismatch section of the activity document, as markdown."""
    lines = ["## Mismatches", "", render_line(report), ""]

    if report.mismatches:
        lines += ["| When | Who | Role | Event |", "| --- | --- | --- | --- |"]
        for found in report.mismatches:
            lines.append(
                f"| {found.occurred_at:%H:%M} | {found.github_login} | `{found.role_name}` "
                f"| {found.event_name} |"
            )
        lines.append("")

    if report.unresolved:
        lines += [
            (
                "These roles launched something and are bound to nobody in "
                "`config/organization.yaml`. A row added there is what shortens this list; "
                "until then their launches are not examined and are counted here instead."
            ),
            "",
        ]
        lines += [
            f"- `{tally.role_name}`: {_plural(tally.launches, 'launch', 'launches')}"
            + ("  **— this is named like a person**" if tally.looks_like_a_person else "")
            for tally in report.unresolved
        ]
        lines.append("")

    lines += [
        (
            "What this cannot see, stated here rather than discovered later: spend that is "
            "not a launch, since a capacity purchase is an API call with a price and no "
            "instance behind it; the administrators in this account the roster does not "
            "name; and roster members holding no AWS role at all, who can produce no "
            "mismatch."
        ),
        "",
    ]
    return "\n".join(lines)
