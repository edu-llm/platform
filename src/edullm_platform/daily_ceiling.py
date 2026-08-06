"""How much this account may commit in a day with nobody asked, and what happens after.

**THE BOUND THIS SITS BESIDE IS PER SUBMISSION AND THE EXPOSURE IS AGGREGATE.**
``automatic_below_cost_usd`` asks one question of one request: is this cheap enough that
nobody need look. It is the right question and it has no memory. Thirty-five researchers
each submitting one run just under it is thirty-five times the bound, every one of them
correctly classified, and nobody woken. Measured on 2026-08-06 against the deployed catalog,
the largest single run released by nobody is $482.10 -- sixteen hours of ``gpu-8xl40s`` in
one cell -- so thirty-five of those is $16,873 against a $5,000 monthly limit, reached before
anybody has been told a thing.

**SO THE COUNTERPART IS A CEILING ON THE DAY RATHER THAN ON THE RUN, AND CROSSING IT ASKS A
PERSON RATHER THAN REFUSING ANYBODY.** Under the ceiling the automatic class behaves exactly
as it does today. Over it, a submission that would have been released by nobody is released
by a team lead instead. Nothing is refused, nothing is queued behind a budget, and the
platform goes on accepting work at the same rate; what changes is that a human is in the loop
for the part of the day past the point where the account stopped being able to afford
inattention.

**IT CANNOT STOP A RUNNING JOB AND IS NOT ABLE TO.** This is consulted once, in the compile
job, before an approval gate is chosen and before anything reaches AWS. There is no path from
here to Batch, to a queue, or to a running container. Killing a training run at hour six to
protect a forecast destroys more than it saves, and the way to be sure that never happens is
for the mechanism to have no reach rather than for it to have a rule against it.

**THREE THINGS IT DELIBERATELY DOES NOT COUNT.**

*Money a lead released.* A ceiling that folded in approved spend would mean one authorised
twelve-hour training run pushes every twenty-step smoke test for the rest of the day to a
lead, which is the shape of control that fires so often people learn to click through it.
What this bounds is spending nobody looked at, so what it counts is spending nobody looked
at. A run a lead approved has already had the human this mechanism exists to summon.

*What anything actually cost.* Cost Explorer reports the current day in pieces over the
following hours, which :mod:`edullm_platform.spend` excludes from its projection for exactly
that reason. A ceiling reading a figure that arrives after the money is gone would be a
control that notices yesterday. What is counted is the worst case each submission was
authorised to commit, which is settled at mint time, is the number the approver page prints,
and is the number ``classify_request`` already routes on.

*Anybody's individual total.* A per-person daily ceiling was the obvious shape and it does not
close the hole it was reached for. Set it at the per-run bound and thirty-five people each get
one full-bound run, which is the $16,873 above unchanged. Set it low enough to matter and it
bounds the one researcher running a sweep while doing nothing about thirty-four people running
one job each. The exposure is a sum across people, so the bound has to be too. The per-person
question is a real one and it is about fairness rather than solvency, and it wants a different
instrument.

**AND IT FAILS CLOSED, WHICH IS THE HALF THAT IS EASY TO GET BACKWARDS.** Three things can go
wrong with the reading: the index is not there, it will not parse, or it holds an entry from
today that carries no figure. Every one of them produces :attr:`Verdict.UNREADABLE` and every
one of them routes to a lead. A control whose broken state is "carry on as before" is a
control that is off precisely on the days something is wrong with the machinery, and the
machinery here is a file on a branch that a force-push rewrites.

The direction of the error is what settles it. Reading the day too low costs the account the
whole of what this exists to bound. Reading it too high costs a lead one click on a run that
was going to be fine. Those are not comparable and the rule is written for the first one.

**WHERE THE COUNT COMES FROM, AND WHY IT IS THIS FILE AND NOT THE LINEAGE STORE.** The compile
job holds no AWS credential and must not: choosing the approval gate before anything can reach
the account is the property that makes the gate worth having, and ``submit-run.yml`` says so.
The lineage store is the authoritative ledger and it is behind exactly the credential that job
does not hold. What is in reach is ``machine/run-index``, which the workflow already writes on
every submission at mint time, already carries ``approval_class`` and ``minted_at``, and now
carries the worst case beside them. One authenticated read of one file.

**WHAT THAT LEDGER IS AND IS NOT.** It records what was *submitted*, which for the automatic
class is the same thing as what was *started*, because nobody stands between the two. So for
the class this bounds it is exact, and it is the right ledger rather than a proxy for one. It
is also written after the compile job that reads it, so a submission never counts itself.

**THE RACE IS REAL AND IS BOUNDED BY THE THING IT IS RACING.** Two submissions compiling in
the same few seconds both read the index before either is written to it, so both can be
released by nobody on the strength of the same reading. The overshoot is bounded by
``automatic_below_cost_usd`` times the number of submissions in flight at once, because that
is the most any one of them may be. It is not zero and pretending otherwise would be worse
than saying it: a ceiling of $2,000 crossed by two concurrent submissions lands at up to
$2,999 rather than $2,000. The alternative is a lock in the submission path, which buys a
bounded overshoot down to zero at the price of a queue in front of every run, and that is the
wrong trade for a mechanism whose whole argument is that it interferes with nothing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final, Protocol

from edullm_platform.contracts.policy import ApprovalClass

__all__ = [
    "AUTOMATIC_CLASS_NAME",
    "CeilingReading",
    "CommittedRun",
    "Verdict",
    "class_under_the_ceiling",
    "committed_today",
    "read_the_day",
]


class CommittedRun(Protocol):
    """What one entry of the ledger has to carry for the day to be summed off it.

    A structural type rather than an import of :class:`edullm_platform.run_index.MintedRun`,
    which is the only thing that produces one, and the reason is the same one
    :class:`edullm_platform.run_history.RunRecord` records. This module is reached from
    ``submission.py`` and so sits in the CLI's import tree, and
    ``tests/test_release_tag_workflow.py`` derives the release trigger from that tree by
    reading imports out of the source. Naming ``MintedRun`` here would put the run index in
    it, so every change to how a workflow writes that document would cut a CLI release for a
    module ``edullm check`` never loads.

    The three fields are the index's own, spelled the same way. ``tools/compile_submission.py``
    is where the two meet, which is the same arrangement ``tools/build_run_history.py`` has.
    """

    @property
    def approval_class(self) -> str: ...
    @property
    def minted_at(self) -> datetime: ...
    @property
    def maximum_compute_cost_usd(self) -> Decimal | None: ...

#: The class this counts, spelled as the index records it. ``MintedRun.approval_class`` is a
#: string rather than the enum, because the index is a document a person may read and a
#: reader of it should not need this package to know what a row says. Compared through
#: :data:`ApprovalClass.AUTOMATIC` rather than a literal, so that a rename of the member
#: moves both sides at once.
AUTOMATIC_CLASS_NAME: Final = ApprovalClass.AUTOMATIC.value


class Verdict(StrEnum):
    """What the day's reading says about whether the automatic class is still open."""

    #: The day's automatic commitments are under the ceiling. Nothing changes.
    UNDER = "under"
    #: They are at or over it. A submission that would be released by nobody goes to a lead.
    #: At it exactly rather than only above, for the reason ``automatic_below_cost_usd`` is
    #: strictly under: this bound decides when nobody looks, so the boundary value belongs on
    #: the side where somebody does.
    CROSSED = "crossed"
    #: The day could not be read, which routes to a lead exactly as crossing does and is a
    #: different fact. A reader of a decision record has to be able to tell "the account had
    #: spent its day" from "nothing could say whether it had", because only the second is a
    #: thing somebody has to go and fix.
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class CeilingReading:
    """The day, as much of it as could be read, and what that means for the next submission.

    Carried as a value rather than returned as a bare verdict because every one of these
    fields is printed: to the submitter on the compile log, to the lead on the approval page,
    and to whatever reports the morning. A caller that had only the verdict would compose its
    own sentence, and three renderers composing three sentences about one reading is how they
    come to disagree.
    """

    verdict: Verdict
    #: Which UTC day was counted. Named on every reading because "today" is a different day
    #: for a reader in California than for the runner that took the measurement, and a
    #: figure quoted without its day is one somebody will compare against the wrong one.
    day: date
    #: The sum over today's automatic entries that carry a figure. Zero on a day with none,
    #: and also zero on a day this could not read, which is why nothing may branch on it
    #: without reading :attr:`verdict` first.
    committed_usd: Decimal
    ceiling_usd: Decimal
    #: How many of today's automatic entries carried a figure, and how many did not. The
    #: second is what makes the reading unreadable, and it is reported rather than merely
    #: acted on: an index that goes on producing unpriced entries after the writer was
    #: changed is a workflow that is not passing the value, and the count is the only thing
    #: that would say so.
    priced_runs: int
    unpriced_runs: int
    #: Why the day could not be read, and ``None`` whenever it could. Prose, because the
    #: three ways it fails send a reader to three different places and none of them is a
    #: thing a caller branches on.
    unreadable_because: str | None = None

    @property
    def asks_a_lead(self) -> bool:
        """Whether a submission that would be released by nobody now goes to a lead.

        The two routing verdicts are collapsed into one property so that no caller has to
        remember that ``UNREADABLE`` is one of them. Writing ``verdict is CROSSED`` at a call
        site is the fail-open mistake this whole module is arranged against, and it is a
        mistake that reads correct.
        """
        return self.verdict in (Verdict.CROSSED, Verdict.UNREADABLE)

    @property
    def said(self) -> str:
        """One sentence, for the compile log and the approver page.

        Money to the cent everywhere, as the rest of the repository renders it, so that two
        reports of one figure cannot show different precision and send somebody looking for
        a bug in the arithmetic.
        """
        counted = (
            f"{self.priced_runs} run(s) released by nobody today"
            if self.unpriced_runs == 0
            else (
                f"{self.priced_runs} priced and {self.unpriced_runs} unpriced run(s) "
                "released by nobody today"
            )
        )
        if self.verdict is Verdict.UNREADABLE:
            return (
                f"The day's automatic commitments for {self.day.isoformat()} could not be "
                f"read, so this goes to a team lead rather than to nobody: "
                f"{self.unreadable_because}. The ceiling is "
                f"${self.ceiling_usd:,.2f} a day."
            )
        if self.verdict is Verdict.CROSSED:
            return (
                f"{counted} have committed ${self.committed_usd:,.2f} against a "
                f"${self.ceiling_usd:,.2f} daily ceiling on runs nobody releases, so this "
                "one goes to a team lead instead. Nothing already running is affected and "
                "nothing is refused."
            )
        return (
            f"{counted} have committed ${self.committed_usd:,.2f} of the "
            f"${self.ceiling_usd:,.2f} the day allows to be released by nobody."
        )


def committed_today(
    runs: Iterable[CommittedRun], *, day: date
) -> tuple[Decimal, int, int]:
    """Today's automatic commitments, and how much of today this could actually price.

    Returns the sum, the number of entries behind it, and the number of today's automatic
    entries carrying no figure. The third is separate from the first two rather than folded
    into either, because an unpriced entry is neither a run that committed nothing nor a run
    whose figure is known: it is a hole in the reading, and the caller has to be able to see
    it to fail closed on it.

    The day is compared in UTC, which is what ``minted_at`` carries and what every other
    instant in this tree is written in.
    """
    total = Decimal(0)
    priced = 0
    unpriced = 0
    for minted in runs:
        if minted.approval_class != AUTOMATIC_CLASS_NAME:
            continue
        if minted.minted_at.astimezone(UTC).date() != day:
            continue
        if minted.maximum_compute_cost_usd is None:
            unpriced += 1
            continue
        total += minted.maximum_compute_cost_usd
        priced += 1
    return total, priced, unpriced


def read_the_day(
    runs: Iterable[CommittedRun] | None,
    *,
    ceiling_usd: Decimal,
    now: datetime,
    unreadable_because: str | None = None,
) -> CeilingReading:
    """What the ceiling says about the next submission, from an index or from the absence of one.

    ``runs`` is ``None`` when the index could not be fetched or could not be parsed, and
    ``unreadable_because`` is the caller's sentence for which of those it was. The caller
    supplies the sentence rather than this function inventing one, because the ways a fetch
    fails are the caller's business and the ways this reading is used are not.

    A ``None`` with no reason given is still unreadable, and the sentence falls back rather
    than raising. A control that crashed on the path where its input was already missing
    would take the submission down with it, and the submission is the thing this is supposed
    to leave alone.
    """
    day = now.astimezone(UTC).date()
    if runs is None:
        return CeilingReading(
            verdict=Verdict.UNREADABLE,
            day=day,
            committed_usd=Decimal(0),
            ceiling_usd=ceiling_usd,
            priced_runs=0,
            unpriced_runs=0,
            unreadable_because=(
                unreadable_because or "the run index was not available to this job"
            ),
        )

    total, priced, unpriced = committed_today(runs, day=day)
    if unpriced:
        return CeilingReading(
            verdict=Verdict.UNREADABLE,
            day=day,
            committed_usd=total,
            ceiling_usd=ceiling_usd,
            priced_runs=priced,
            unpriced_runs=unpriced,
            unreadable_because=(
                f"{unpriced} of today's runs released by nobody carry no recorded worst "
                "case, so the day's total is a floor rather than a figure. An entry minted "
                "before the index recorded one reads this way, and so does one written by a "
                "workflow that is not passing it"
            ),
        )

    return CeilingReading(
        verdict=Verdict.CROSSED if total >= ceiling_usd else Verdict.UNDER,
        day=day,
        committed_usd=total,
        ceiling_usd=ceiling_usd,
        priced_runs=priced,
        unpriced_runs=0,
    )


def class_under_the_ceiling(
    approval_class: ApprovalClass, *, reading: CeilingReading | None
) -> ApprovalClass:
    """The class a submission takes once the day is taken into account.

    **THIS RAISES AND NEVER LOWERS, WHICH IS THE WHOLE OF ITS SAFETY ARGUMENT.** The only
    transition it can make is automatic to routine: from a run nobody sees to a run a team
    lead sees. There is no input to this function that makes a submission cheaper to
    approve, so a defect here costs a lead a click and cannot cost the account a dollar.

    Applied after ``classify_request`` rather than inside it, and that is deliberate rather
    than convenient. ``classify_request`` runs in two places: here in the compile job, and
    again inside AWS from the admission validator's deployed zip, where it re-derives the
    class from the manifest to check that the gate a run passed is the gate it needed. A rule
    that read a mutable ledger could not be re-derived: admission cannot reach the index, and
    if it could it would read a different day-total than the compile job did a few minutes
    earlier and refuse runs on the difference. So the ledger-reading half lives here, on the
    one side that has the ledger, and admission is taught instead to accept a gate stronger
    than the one it derives -- which it can check without reading anything at all.

    ``reading`` is ``None`` when no ceiling is configured, which is a platform that has not
    turned this on rather than a reading that failed. Those are different and only one of
    them routes anywhere: an unset ceiling changes nothing, and a set ceiling that could not
    be read asks a lead.
    """
    if reading is None:
        return approval_class
    if approval_class is not ApprovalClass.AUTOMATIC:
        return approval_class
    return ApprovalClass.ROUTINE if reading.asks_a_lead else approval_class
