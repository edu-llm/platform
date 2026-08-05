"""Where the month lands if today repeats, which is the only spend number worth a morning.

**A total makes the reader do arithmetic and a projection does not.** "$272 so far" on the
third of the month is unreadable without knowing how many days have gone and what the limit
is; "on track for $2,810 against a $5,000 limit" is a decision. So the total is carried and
the projection is what leads, and both are held against a limit that is read from a file
rather than written into this module.

**THIS OBSERVES AND CANNOT STOP ANYTHING, AND THAT IS A REQUIREMENT RATHER THAN A
LIMITATION.** Nothing here talks to AWS Budgets, attaches a policy, or touches a queue. The
worst thing a defect in this file can do is print a wrong number into a report. That is
deliberate: the owner asked to know before a limit is reached and asked equally that nothing
interfere with a run that works, and a control that throttles training to protect a forecast
would trade a large certain loss for a small uncertain one.

**TODAY IS EXCLUDED FROM EVERY FIGURE, WHICH IS WHY THE TOTAL HERE WILL NOT MATCH THE
CONSOLE.** Cost Explorer's current day is partial and arrives over the following hours, so a
daily average that included it would be dragged toward zero every morning and the projection
would read low exactly when somebody is deciding whether to launch something. :func:`project`
therefore takes only whole days and says how many it used, and the month-to-date it reports
is the sum of those whole days rather than the console's running total.

**A month with no whole day yet is not projected.** On the first of the month there is
nothing to extrapolate from, and an average over zero days is either a crash or an invented
number. :class:`Projection` carries ``None`` for the rate and the landing figure in that
case, and the renderer says so, because "too early to say" is an answer and zero is not.

The per-team split does not come from here and does not come from Cost Explorer.
:mod:`edullm_platform.run_costs` argues that at length; the short version is that
``ce:ListCostAllocationTags`` is refused outright in a linked account, and grouping a Cost
Explorer query by ``edullm:team`` returns every dollar under the empty-value key because the
tag was never activated. So the account total is real money from Cost Explorer and the split
beneath it is measured compute from this platform's own lineage, and the two do not add up.
They are printed as two separate statements for that reason rather than reconciled into one
table that would imply they are the same quantity.

**AND THE SPLIT NAMES ONE MORE THING IT CANNOT PROMISE, WHICH IS NEW SINCE 2026-08-05.** The
group a run is charged to is the group its manifest claimed. #221 removed the only thing that
compared that claim against the roster inside AWS, on the grounds that it fired past the
approval gate and so wasted approvals without ever preventing spend. So a line here is what a
group was charged and not what a group ran, and :class:`TeamShare` carries the size of the
difference rather than leaving the reader to assume there is none.
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CENTS",
    "DailyCost",
    "Projection",
    "SpendLimits",
    "TeamShare",
    "month_start",
    "project",
    "render_section",
    "whole_days_before",
]

#: Money is rendered to the cent everywhere in this repository, and two reports of one figure
#: showing different precision sends somebody looking for a bug in the arithmetic.
CENTS = Decimal("0.01")


class SpendLimits(BaseModel):
    """The monthly number the projection is held against, as read from a file.

    A model rather than a bare float so that a limit of ``"5000"``, a negative limit or a
    missing key is a refusal at load time with the file named, rather than a report that
    renders a comparison against ``None`` and reads as though it checked something.

    ``model_config`` forbids extra keys for the reason every other config model here does:
    a misspelled key that is silently ignored is a limit somebody believes they changed.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    monthly_limit_usd: Decimal = Field(gt=0)


@dataclass(frozen=True)
class DailyCost:
    """One whole day of account spend, as Cost Explorer reported it."""

    day: date
    cost_usd: Decimal


@dataclass(frozen=True)
class TeamShare:
    """One team's measured compute for the month, from the lineage rather than from AWS.

    ``unpriced_runs`` travels with the figure because a team whose month was entirely spot
    work has a real spend and no number for it, and a share printed at zero beside a busy
    team would read as an idle one.

    ``contradicted_runs`` travels with it for the same kind of reason and a newer one. Since
    #221 nothing on the platform refuses a submitter who claims a group the roster records
    them elsewhere from, so a line here is what a group was charged rather than what it ran.
    Carrying the size of that gap is what stops the split implying a precision it stopped
    having. :mod:`edullm_platform.run_costs` computes it and argues it, and
    ``tools/report_run_costs.py`` is the one place the runs behind it are named.
    """

    team: str
    cost_usd: Decimal
    runs: int
    unpriced_runs: int
    #: How many of ``runs`` a submitter claimed whom the roster records on another group,
    #: and how much of ``cost_usd`` they carry. Counted into both rather than deducted: this
    #: says how reliable the line is, and netting it off would publish an attribution no
    #: record supports.
    contradicted_runs: int = 0
    contradicted_cost_usd: Decimal = Decimal(0)


@dataclass(frozen=True)
class Projection:
    """Where the month lands at the rate of the days that have completed.

    ``daily_average_usd`` and ``projected_month_usd`` are ``None`` together, and only when no
    whole day has elapsed. Callers should render the absence rather than substituting a zero;
    :func:`render_section` does.
    """

    month: date
    whole_days: int
    days_in_month: int
    month_to_date_usd: Decimal
    daily_average_usd: Decimal | None
    projected_month_usd: Decimal | None
    limit_usd: Decimal

    @property
    def projected_share_of_limit(self) -> Decimal | None:
        """The projection as a percentage of the limit, or ``None`` if there is no projection."""
        if self.projected_month_usd is None:
            return None
        with localcontext() as context:
            context.prec = 28
            return (self.projected_month_usd / self.limit_usd * 100).quantize(CENTS)

    @property
    def would_exceed(self) -> bool:
        """Whether the month lands over the limit at the current rate.

        A month with no projection is not an exceedance. Answering ``True`` on the first of
        the month because nothing is known would make the one signal this produces fire
        every month regardless of spend, and a signal that always fires is off.
        """
        return self.projected_month_usd is not None and self.projected_month_usd > self.limit_usd


def month_start(today: date) -> date:
    return today.replace(day=1)


def whole_days_before(today: date) -> int:
    """How many days of this month have finished, which is never today.

    Named and exported rather than inlined, because the Cost Explorer query and the average
    have to agree about it exactly. A query whose end date and a divisor that disagreed by
    one would understate or overstate the rate by a factor of ``1/n``, worst on the days
    early in the month when ``n`` is small and somebody is most likely to be looking.
    """
    return (today - month_start(today)).days


def project(
    days: Sequence[DailyCost], *, today: date, limit_usd: Decimal
) -> Projection:
    """The month's landing figure, from the whole days handed in.

    Only the days belonging to ``today``'s month are counted. A caller that asked Cost
    Explorer for a wider window would otherwise have the previous month's spend folded into
    this month's average, and July cost this account three orders of magnitude more than
    June did, so that error would not be small.
    """
    start = month_start(today)
    this_month = [entry for entry in days if start <= entry.day < today]
    total = sum((entry.cost_usd for entry in this_month), Decimal(0)).quantize(CENTS)

    elapsed = whole_days_before(today)
    days_in_month = calendar.monthrange(today.year, today.month)[1]

    average: Decimal | None = None
    projected: Decimal | None = None
    if elapsed > 0:
        with localcontext() as context:
            context.prec = 28
            average = (total / Decimal(elapsed)).quantize(CENTS)
            projected = (total / Decimal(elapsed) * Decimal(days_in_month)).quantize(CENTS)

    return Projection(
        month=start,
        whole_days=elapsed,
        days_in_month=days_in_month,
        month_to_date_usd=total,
        daily_average_usd=average,
        projected_month_usd=projected,
        limit_usd=limit_usd,
    )


def _money(value: Decimal) -> str:
    return f"${value:,.2f}"


def _headline(projection: Projection) -> str:
    """The one sentence somebody reads before deciding whether to read the rest."""
    if projection.projected_month_usd is None:
        return (
            f"No whole day of {projection.month:%B} has completed yet, so there is nothing to "
            f"project from. The limit is {_money(projection.limit_usd)}."
        )
    verdict = "over" if projection.would_exceed else "under"
    share = projection.projected_share_of_limit
    return (
        f"At {_money(projection.daily_average_usd or Decimal(0))} a day, {projection.month:%B} "
        f"lands at {_money(projection.projected_month_usd)} — {verdict} the "
        f"{_money(projection.limit_usd)} limit, at {share}% of it."
    )


def _team_lines(shares: Sequence[TeamShare] | None) -> list[str]:
    if shares is None:
        return [
            (
                "The per-team split is not available, because the lineage records could not "
                "be read. The account total above is unaffected; it comes from Cost Explorer."
            ),
            "",
        ]
    lines = [
        (
            "By team, over the same whole days, this is measured compute from this "
            "platform's own lineage and not a share of the figure above. Cost allocation "
            "tags are refused in a linked "
            "account, so grouping Cost Explorer by `edullm:team` puts every dollar under the "
            "empty key. These numbers exclude instance start-up, idle time, storage and "
            "transfer, so they are smaller than the bill by construction. A line is the "
            "group each run's manifest claimed, which nothing on the platform checks against "
            "the roster any more."
        ),
        "",
    ]
    if not shares:
        lines += ["- No run in the lineage records is priced for this period.", ""]
        return lines
    for share in shares:
        note = f", {share.unpriced_runs} with no figure" if share.unpriced_runs else ""
        lines.append(
            f"- {share.team}: {_money(share.cost_usd)} across {share.runs} "
            f"run{'' if share.runs == 1 else 's'}{note}{_contradicted_note(share)}"
        )
    lines.append("")
    lines += _precision_lines(shares)
    return lines


def _contradicted_note(share: TeamShare) -> str:
    """What part of one line was booked by somebody the roster puts on another group."""
    if not share.contradicted_runs:
        return ""
    return (
        f", of which {_money(share.contradicted_cost_usd)} across "
        f"{share.contradicted_runs} run{'' if share.contradicted_runs == 1 else 's'} was "
        "claimed by somebody the roster records elsewhere"
    )


def _precision_lines(shares: Sequence[TeamShare]) -> list[str]:
    """How far the split can be trusted, said once and only where it is not trivially true.

    Printed only when something is actually contradicted, unlike the caveat above it, and
    that difference is deliberate. The paragraph above describes how the figures are
    produced and is true every morning. This one describes what happened this month, and a
    standing sentence about a thing that did not happen is how a reader learns to skip the
    line that matters.

    It states the size and stops. The runs are named by ``tools/report_run_costs.py`` and by
    nothing else, because one fact enumerated in two reports is two reports to keep in step.
    """
    runs = sum(share.contradicted_runs for share in shares)
    if not runs:
        return []
    money = sum((share.contradicted_cost_usd for share in shares), Decimal(0))
    return [
        (
            f"{runs} run{'s' if runs != 1 else ''} above, carrying {_money(money)}, "
            f"{'were' if runs != 1 else 'was'} claimed against a group the roster records "
            "the submitter on a different one from. Nothing refuses that any more, so each "
            "one is counted where it was claimed and the split is what each group was "
            "charged rather than what each group ran. `tools/report_run_costs.py` names the "
            "runs. It is a floor: a submitter the roster records on no group at all "
            "contradicts nothing and is not counted here."
        ),
        "",
    ]


def render_section(
    projection: Projection,
    *,
    team_shares: Sequence[TeamShare] | None,
    by_service: Mapping[str, Decimal] | None = None,
    limit_source: str,
) -> str:
    """The spend section, for the activity report to paste in whole.

    Returned as a string rather than written anywhere, so that the caller decides whether it
    lands in a file, a step summary or a chat message. That is the same arrangement
    ``tools/visibility_board.py`` uses and it is what lets one computation serve all three
    without any of them importing the others.
    """
    lines = [
        "## Spend",
        "",
        _headline(projection),
        "",
        (
            f"{_money(projection.month_to_date_usd)} spent over the "
            f"{projection.whole_days} whole day{'' if projection.whole_days == 1 else 's'} of "
            f"{projection.month:%B} so far, out of {projection.days_in_month}. Today is not "
            "counted: Cost Explorer reports the current day in pieces over the following "
            "hours, so including it would drag the average down every morning."
        ),
        "",
    ]
    lines += _team_lines(team_shares)

    if by_service:
        ranked = sorted(by_service.items(), key=lambda item: -item[1])[:5]
        lines += ["The five services carrying the most of it:", ""]
        lines += [f"- {name}: {_money(cost)}" for name, cost in ranked]
        lines.append("")

    lines += [
        (
            f"The limit is read from `{limit_source}`. Nothing here can stop a run — it is a "
            "number to read, and the account has no budget action attached that could act on "
            "it."
        ),
        "",
    ]
    return "\n".join(lines)
