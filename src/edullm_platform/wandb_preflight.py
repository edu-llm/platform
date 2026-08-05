"""Whether a submission may proceed, decided from the last W&B credential check the audit made.

WHY THIS READS A VERDICT INSTEAD OF READING THE KEY, WHICH IS THE WHOLE DESIGN.

Nine of the last sixty-seven job failures were a W&B key W&B refused, and eight of them
were filed as torch distributed bugs because ``ProcessGroup is not registered`` is what the
tail of the log says. A submit-time preflight is worth having for that reason. What it must
not cost is the property the submit path is built on.

Exactly three principals in this account hold ``secretsmanager:GetSecretValue`` on
``sbsandbox-intern-edullm-wandb-api-key-*``, confirmed against the live account on
2026-08-02: the two Batch execution roles, which are trusted to ``ecs-tasks.amazonaws.com``
and inject the value into a container at task start, and
``sbsandbox-intern-edullm-audit-reader``, which is trusted to
``audit.yml@refs/heads/main`` and exists to answer exactly this question. No identity
``submit-run.yml`` can obtain holds it, and that is deliberate rather than an oversight:
``infra/iam/admission-role.yaml`` argues it beside the grant it is an argument about.

So the check is already made, once a night, under the one GitHub-facing role built to make
it. What was missing is not a check. It is that the answer never reached the path that
spends money. A red audit is a red scheduled run and nothing else, and the audit's own
header says there is no alerting infrastructure, so a key W&B refuses can go on costing a GPU
allocation per submission until somebody happens to look. This module is what connects the
two, and it needs no credential at all: the submit job already holds ``actions: read`` for
the approvals endpoint, and that is enough to read what the audit published.

WHAT THIS BUYS AND WHAT IT DOES NOT, STATED RATHER THAN LEFT TO BE DISCOVERED.

It buys a definite refusal on a key already known to be refused, which today stops nothing
at all. It does not buy detection of a key that broke after the last check. The stored
value changes only when a person writes it -- rotation is not enabled on the secret,
confirmed 2026-08-02 -- so that window is one bad paste away from mattering, and the remedy
is procedural and cheap: dispatch ``audit.yml`` after writing the key. ``infra/README.md``
carries that as a step rather than as advice.

THE ASYMMETRY BETWEEN A STALE REFUSAL AND A STALE ACCEPTANCE IS DELIBERATE.

A refusal is honoured however old it is. An acceptance older than :data:`FRESHNESS` stops
being treated as one. Those look inconsistent and are not: evidence that the key is bad
does not expire merely because nobody re-checked, while the absence of fresh evidence that
it is good is not evidence that it is bad. Everything that is not a definite refusal
therefore lets the submission through and says so loudly, which is the same shape every
other check in ``submit-run.yml`` takes -- a tool that could not find out must never read
as a verdict.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Final

__all__ = [
    "AUDIT_BRANCH",
    "AUDIT_VERDICT_ARTIFACT",
    "AUDIT_VERDICT_FILENAME",
    "AUDIT_WORKFLOW",
    "CHECKED_AT_FIELD",
    "FAULTS_FIELD",
    "FRESHNESS",
    "VERDICT_FIELD",
    "Outcome",
    "Preflight",
    "Verdict",
    "decide",
    "read_checked_at",
    "read_verdict",
]

#: The workflow that publishes the verdict, the branch it must have run on, and the
#: artifact it publishes. The branch matters and is not belt and braces: the audit reader
#: role pins its subject to ``ref:refs/heads/main``, so a dispatch of ``audit.yml`` from a
#: branch cannot assume it and cannot read the secret. Accepting a verdict from a branch
#: would therefore mean accepting one produced by a tool somebody edited on that branch,
#: against a secret it was never able to read.
AUDIT_WORKFLOW: Final = "audit.yml"
AUDIT_BRANCH: Final = "main"
AUDIT_VERDICT_ARTIFACT: Final = "wandb-credential"
AUDIT_VERDICT_FILENAME: Final = "wandb-credential.json"

#: The three fields of the published report this module reads. The rest of the report -- a
#: length, a four character prefix, a truncated digest, the entity W&B named -- is for a
#: person reading a refusal, and the key itself is never in it by construction.
VERDICT_FIELD: Final = "verdict"
CHECKED_AT_FIELD: Final = "checked_at"
FAULTS_FIELD: Final = "looks_wrong"

#: How old an acceptance may be before it stops being treated as one. The audit runs at
#: 05:00 UTC, so a healthy verdict is at most a day old and a bound of twenty-four hours
#: would report every submission made shortly before 05:00 as stale. Thirty-six hours fires
#: on one missed night and not on ordinary schedule jitter, which GitHub documents as
#: possible and occasionally as a dropped run under load.
#:
#: Crossing it refuses nothing. It changes what the step says, so an audit that has quietly
#: stopped running becomes visible on the path people use rather than only in a scheduled
#: run the header of audit.yml admits nobody watches.
FRESHNESS: Final = timedelta(hours=36)


class Verdict(StrEnum):
    """What the audit's check concluded, written by the tool that asked W&B.

    Derived where the answer is known rather than inferred later from prose. ``looks_wrong``
    is a list of sentences for a person, and a second reader matching strings against it
    would be a second definition of "refused" that drifts from the first one silently.
    """

    #: W&B resolved the stored key to the entity this platform logs its runs into.
    ACCEPTED = "accepted"
    #: W&B was asked and would not accept the key, or resolved it to another entity.
    REFUSED = "refused"
    #: W&B could not be reached, so nothing was established about the key either way.
    UNREACHABLE = "unreachable"


class Outcome(StrEnum):
    """What the submit path should do about it."""

    PROCEED = "proceed"
    REFUSE = "refuse"
    #: Nothing was established. Never a refusal, and never reported as a pass either.
    NOT_ESTABLISHED = "not_established"


@dataclass(frozen=True)
class Preflight:
    """The decision, with the machine token and the sentence that belong to it.

    Both are here rather than composed by the caller, so the log line and the step summary
    cannot end up describing two different outcomes.
    """

    outcome: Outcome
    reason: str
    sentence: str
    verdict: Verdict | None = None
    checked_at: datetime | None = None
    age: timedelta | None = None

    @property
    def refuses(self) -> bool:
        return self.outcome is Outcome.REFUSE


def read_verdict(report: Mapping[str, Any]) -> Verdict | None:
    """The verdict a published report carries, or ``None`` if it carries none it names.

    ``None`` rather than a guess, and the case is real rather than defensive: a report
    written before this field existed, or by a tool run with ``--offline``, has no answer
    from W&B in it at all. Reading such a report as an acceptance would be the preflight
    reporting a pass it has no evidence for.
    """
    named = report.get(VERDICT_FIELD)
    if not isinstance(named, str):
        return None
    try:
        return Verdict(named)
    except ValueError:
        return None


def read_checked_at(report: Mapping[str, Any]) -> datetime | None:
    """When the check was made, as the report states it, or ``None`` if it does not.

    The report's own timestamp rather than the artifact's upload time. They differ by
    seconds in the ordinary case and by everything in the case worth guarding: a report
    copied out of one run and published from another would carry a fresh upload time and a
    stale statement about when W&B was actually asked.

    A timestamp carrying no offset is read as no timestamp. An age computed across two
    machines' idea of local noon is not an age, and answering ``None`` puts the report in
    the same place as one that states nothing, which lets the submission through.
    """
    stated = report.get(CHECKED_AT_FIELD)
    if not isinstance(stated, str):
        return None
    try:
        moment = datetime.fromisoformat(stated)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def decide(
    *,
    report: Mapping[str, Any] | None,
    now: datetime,
    freshness: timedelta = FRESHNESS,
) -> Preflight:
    """What to do about the newest verdict the audit published, or the absence of one.

    ``report`` is ``None`` when no verdict could be found at all, which is the state this
    lands in and stays in until the first audit after it merges. Failing then would take
    every submission down in order to add a check to them, which is the hazard *Why IAM is
    laptop-only* in ``infra/README.md`` is about, in a different costume.
    """
    if report is None:
        return Preflight(
            outcome=Outcome.NOT_ESTABLISHED,
            reason="wandb_verdict_not_published",
            sentence=(
                "No W&B credential verdict was found, so nothing was checked and this "
                f"submission continues. The verdict comes from the {AUDIT_WORKFLOW} run "
                f"on {AUDIT_BRANCH}; dispatch it to produce one."
            ),
        )

    verdict = read_verdict(report)
    checked_at = read_checked_at(report)
    if verdict is None or checked_at is None:
        return Preflight(
            outcome=Outcome.NOT_ESTABLISHED,
            reason="wandb_verdict_unreadable",
            sentence=(
                "The published W&B credential report names no verdict this can read, so "
                "nothing was checked and this submission continues. A report written "
                f"before the {VERDICT_FIELD} field existed reads this way, and the next "
                f"{AUDIT_WORKFLOW} run replaces it."
            ),
            verdict=verdict,
            checked_at=checked_at,
        )

    age = now - checked_at
    if verdict is Verdict.REFUSED:
        # Honoured at any age. See the asymmetry paragraph in this module's docstring: a
        # measured refusal does not stop being one because the schedule slipped, and the
        # remedy for a repair nothing has confirmed is one dispatch of the audit.
        return Preflight(
            outcome=Outcome.REFUSE,
            reason="wandb_credential_would_be_refused",
            sentence=(
                "W&B would refuse the key this platform injects into every container, so "
                "no job was submitted and nothing was allocated."
            ),
            verdict=verdict,
            checked_at=checked_at,
            age=age,
        )
    if verdict is Verdict.UNREACHABLE:
        return Preflight(
            outcome=Outcome.NOT_ESTABLISHED,
            reason="wandb_verdict_inconclusive",
            sentence=(
                "The last check could not reach W&B, so it established nothing about the "
                "key and this submission continues. This is an outage rather than a "
                "verdict."
            ),
            verdict=verdict,
            checked_at=checked_at,
            age=age,
        )
    if age > freshness:
        return Preflight(
            outcome=Outcome.NOT_ESTABLISHED,
            reason="wandb_verdict_stale",
            sentence=(
                f"The last W&B credential check accepted the key {_hours(age)} ago, which "
                f"is older than the {_hours(freshness)} this treats as current, so this "
                "submission continues on a verdict nothing has renewed. The "
                f"{AUDIT_WORKFLOW} schedule has slipped or stopped."
            ),
            verdict=verdict,
            checked_at=checked_at,
            age=age,
        )
    return Preflight(
        outcome=Outcome.PROCEED,
        reason="wandb_credential_accepted",
        sentence=(
            f"W&B accepted the stored key {_hours(age)} ago. Nothing here can tell whether "
            "it has been rewritten from a laptop since, which is the one way it changes."
        ),
        verdict=verdict,
        checked_at=checked_at,
        age=age,
    )


def _hours(span: timedelta) -> str:
    """A span in whole hours and minutes, because a reader is comparing it to a schedule."""
    minutes = max(int(span.total_seconds()) // 60, 0)
    hours, remainder = divmod(minutes, 60)
    return f"{hours}h{remainder:02d}m"
