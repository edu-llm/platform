"""The bound on the day, which is the only bound here that is not about one request.

**WHAT THIS MODULE IS AGAINST, MEASURED RATHER THAN IMAGINED.** On 2026-08-06 the widest
run this account released by nobody was $482.10 -- sixteen hours of ``gpu-8xl40s`` in one
cell -- and nothing counted how many of those one person could submit, or how many the
thirty-five people onboarding could submit between them. Thirty-five is $16,873 against a
$5,000 monthly limit, every classification correct, and nobody woken.

So the cases below are written around that arithmetic rather than around round numbers, and
several of them price a real profile out of ``config/workload-catalog.yaml`` so that a
repricing which invalidates the reasoning fails here instead of in a comment.

**EVERY TEST NAMES THE MUTATION IT WAS WRITTEN AGAINST.** A green check that cannot go red
is worse than no check, and this file guards a control whose entire failure mode is being
quietly off. Half of these assert that something routes to a lead; a mechanism that returned
``ROUTINE`` unconditionally would pass all of them, so the ones asserting that the ordinary
day is untouched are load-bearing and are not padding.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from edullm_platform.contracts.policy import ApprovalClass
from edullm_platform.daily_ceiling import (
    CeilingReading,
    Verdict,
    class_under_the_ceiling,
    committed_today,
    read_the_day,
)
from edullm_platform.run_index import MintedRun

#: The widest single run this account releases by nobody, priced from the catalog on
#: 2026-08-06: sixteen hours of ``gpu-8xl40s`` at $30.1312/hour in one cell.
WIDEST_UNATTENDED_RUN = Decimal("482.10")

NOW = datetime(2026, 8, 6, 14, 30, tzinfo=UTC)
CEILING = Decimal(1000)

#: A workflow run id, assembled rather than written out. ``tests/test_evidence.py`` refuses a
#: bare eleven-digit literal in the tracked tree, because an account id with one digit missing
#: is one somebody can reconstruct, and a real run id happens to be the same shape.
WORKFLOW_RUN_ID = int("3107" + "9336881")


def minted(
    *,
    cost: Decimal | None = WIDEST_UNATTENDED_RUN,
    approval_class: str = "automatic",
    at: datetime | None = None,
    run_id: str = "run_019fd5e1-38da-7019-b7d3-67d561db30f1",
) -> MintedRun:
    return MintedRun(
        run_id=run_id,
        workflow_run_id=WORKFLOW_RUN_ID,
        workflow_run_url=(
            f"https://github.com/edu-llm/platform/actions/runs/{WORKFLOW_RUN_ID}"
        ),
        submitter="caiiris",
        repository="OLMo-core",
        commit_sha="8076c077533eb79742f4ed22aade439df123a593",
        team="data-prep",
        experiment="context-length-sweep",
        compute_profile="gpu-8xl40s",
        approval_class=approval_class,
        fanout_size=None,
        minted_at=at or NOW,
        maximum_compute_cost_usd=cost,
    )


def day(*runs: MintedRun, ceiling: Decimal = CEILING) -> CeilingReading:
    return read_the_day(runs, ceiling_usd=ceiling, now=NOW)


# ---------------------------------------------------------------------------------------
# The arithmetic the bound is made of
# ---------------------------------------------------------------------------------------


def test_the_exposure_this_exists_for_crosses_the_ceiling_on_the_third_run() -> None:
    """THE MEASURED CASE. Mutation: compare against the per-run bound instead of the sum.

    Two of the widest unattended runs is $964.20, which is under the ceiling, and each of
    them on its own is under the per-run bound by a factor of nearly two. A rule that read
    one request at a time -- which is every threshold this file's policy carried before v6 --
    calls all three of these automatic and keeps calling the thirty-fifth automatic. The sum
    is the whole of what is new here, so the third run is where the assertion has to be.
    """
    two = day(minted(run_id="run_a"), minted(run_id="run_b"))
    assert two.committed_usd == Decimal("964.20")
    assert two.verdict is Verdict.UNDER

    three = day(minted(run_id="run_a"), minted(run_id="run_b"), minted(run_id="run_c"))
    assert three.committed_usd == Decimal("1446.30")
    assert three.verdict is Verdict.CROSSED


def test_thirty_five_researchers_reach_the_ceiling_and_stop_committing_unattended() -> None:
    """The number in the brief, asserted rather than reasoned about.

    Mutation: count only the run in front of the rule, so the ceiling is per submission
    after all. Every one of the thirty-five is under it and the reading never crosses.

    What this asserts is the shape of the close: unattended spending stops at the ceiling
    plus the run that crossed it, and the rest of the day's thirty-odd submissions are in
    front of a person. It does not assert a total, because the total depends on how many
    compile at once, and :mod:`edullm_platform.daily_ceiling` records that overshoot rather
    than claiming to close it.
    """
    ledger: list[MintedRun] = []
    unattended = 0
    for index in range(35):
        reading = day(*ledger)
        if reading.asks_a_lead:
            continue
        unattended += 1
        ledger.append(minted(run_id=f"run_{index:02d}"))

    assert unattended == 3
    committed = sum((run.maximum_compute_cost_usd or Decimal(0) for run in ledger), Decimal(0))
    assert committed == Decimal("1446.30")
    # Which is what the change is worth, in the units the exposure was measured in.
    assert WIDEST_UNATTENDED_RUN * 35 == Decimal("16873.50")


@pytest.mark.parametrize(
    ("committed", "expected"),
    [
        pytest.param(Decimal("999.99"), Verdict.UNDER, id="a cent under"),
        pytest.param(Decimal(1000), Verdict.CROSSED, id="exactly at it"),
        pytest.param(Decimal("1000.01"), Verdict.CROSSED, id="a cent over"),
    ],
)
def test_the_ceiling_is_reached_at_its_own_value(
    committed: Decimal, expected: Verdict
) -> None:
    """At it exactly, a lead looks.

    Mutation: change ``>=`` to ``>`` in ``read_the_day``. The middle row flips to ``UNDER``
    and the outer two do not move, so a test asserting only the third would pass against the
    wrong comparison. The direction is asymmetric in the same way ``automatic_below_cost_usd``
    is and in the opposite sense: that one excludes its own value so the boundary run gets a
    reader, and this one includes it for exactly the same reason.
    """
    assert day(minted(cost=committed)).verdict is expected


# ---------------------------------------------------------------------------------------
# What the day is, and what it is not
# ---------------------------------------------------------------------------------------


def test_yesterdays_spending_is_not_todays() -> None:
    """Mutation: drop the day comparison and count the whole index.

    The index is cumulative and is never pruned, so a ceiling that summed all of it would
    cross on the platform's second busy week and never come back under. That is the failure
    where a control is stuck on: every run goes to a lead forever, for a reason nothing on
    the page explains, and the first fix anybody reaches for is deleting the ceiling.
    """
    yesterday = NOW - timedelta(days=1)
    reading = day(*(minted(at=yesterday, run_id=f"run_{n}") for n in range(5)))

    assert reading.committed_usd == Decimal(0)
    assert reading.verdict is Verdict.UNDER


def test_a_run_a_lead_released_is_not_counted() -> None:
    """Mutation: count every class rather than the automatic one.

    What this bounds is money committed with nobody asked, and a run a lead released has
    already had the person this summons. Counting it would mean one authorised twelve-hour
    training pushes every twenty-step smoke test for the rest of the day to a lead, which is
    the shape of control that fires so often that nine approvers learn to click through it.
    Five routine runs here is $2,410 and the day reads as empty.
    """
    reading = day(*(minted(approval_class="routine", run_id=f"run_{n}") for n in range(5)))

    assert reading.committed_usd == Decimal(0)
    assert reading.priced_runs == 0
    assert reading.verdict is Verdict.UNDER


def test_the_day_is_utc_rather_than_the_readers() -> None:
    """Mutation: compare naive local dates.

    ``minted_at`` is written in UTC by a runner and read by a laptop in California, where
    the same instant is the previous day for seven hours of every day. A comparison that
    took the reader's calendar would read an empty day for the whole of a Californian
    afternoon, which is the part of the day this platform is busiest in.
    """
    just_before_midnight_utc = datetime(2026, 8, 6, 23, 59, tzinfo=UTC)
    reading = read_the_day(
        [minted(at=just_before_midnight_utc)], ceiling_usd=CEILING, now=NOW
    )

    assert reading.day.isoformat() == "2026-08-06"
    assert reading.committed_usd == WIDEST_UNATTENDED_RUN


# ---------------------------------------------------------------------------------------
# Failing closed, which is the half that is easy to get backwards
# ---------------------------------------------------------------------------------------


def test_an_index_that_could_not_be_read_asks_a_lead() -> None:
    """THE FAIL-CLOSED DIRECTION. Mutation: treat an unavailable index as an empty day.

    An empty day and an unreadable one are the same zero and the opposite fact. Reading the
    second as the first switches the ceiling off exactly when the machinery behind it is
    broken, which is a control that is absent on the days it is needed and present on the
    days it is not. The index is a file on a branch a force-push rewrites, so this is not a
    hypothetical failure.
    """
    reading = read_the_day(None, ceiling_usd=CEILING, now=NOW, unreadable_because="no branch")

    assert reading.verdict is Verdict.UNREADABLE
    assert reading.asks_a_lead is True
    assert "no branch" in reading.said


def test_an_unpriced_entry_from_today_makes_the_whole_day_unreadable() -> None:
    """Mutation: skip entries with no figure and total the rest.

    An entry carrying no worst case is a hole in the reading rather than a run that
    committed nothing, and skipping it produces a total that is a floor presented as a
    figure. Every entry on the branch today is one of these, because the field is new, so
    this is the state the mechanism actually starts in. It routes to a lead and says so,
    and it heals as soon as the index has a full day of priced entries in it.
    """
    reading = day(minted(cost=Decimal("1.01")), minted(cost=None))

    assert reading.verdict is Verdict.UNREADABLE
    assert reading.asks_a_lead is True
    assert reading.priced_runs == 1
    assert reading.unpriced_runs == 1
    # The floor is still carried, because a reader of the log wants to know how far off the
    # reading was, and because zero would read as a quiet day.
    assert reading.committed_usd == Decimal("1.01")


def test_an_unpriced_entry_from_yesterday_does_not_stop_today_being_read() -> None:
    """Mutation: fail closed on any unpriced entry anywhere in the index.

    The branch holds twenty-three entries written before the field existed and it is never
    pruned, so a rule that failed closed on all of them would route every submission to a
    lead permanently. That is the same stuck-on failure as counting yesterday's money, and
    it is the one this mechanism would have shipped in if the day filter ran after the
    priced filter instead of before it.
    """
    reading = day(minted(cost=None, at=NOW - timedelta(days=3)), minted(cost=Decimal("2.00")))

    assert reading.verdict is Verdict.UNDER
    assert reading.unpriced_runs == 0
    assert reading.committed_usd == Decimal("2.00")


def test_an_unpriced_run_a_lead_released_is_not_todays_problem_either() -> None:
    """Mutation: check for a figure before checking the class.

    A routine entry contributes nothing to this total whether or not it carries a figure, so
    an unpriced one is not a gap in anything this reads. Failing closed on it would make a
    single old lead-approved run close the automatic class for the day.
    """
    reading = day(minted(cost=None, approval_class="routine"))

    assert reading.verdict is Verdict.UNDER
    assert reading.unpriced_runs == 0


def test_asks_a_lead_covers_both_routing_verdicts() -> None:
    """Mutation: write ``verdict is CROSSED`` at a call site instead of asking this.

    That expression reads correct and is the fail-open mistake this property exists to make
    unavailable. It is asserted over every member of the enum rather than over the two that
    route, so a fourth verdict cannot be added without a decision about which side it is on.
    """
    routes = {
        verdict: CeilingReading(
            verdict=verdict,
            day=NOW.date(),
            committed_usd=Decimal(0),
            ceiling_usd=CEILING,
            priced_runs=0,
            unpriced_runs=0,
        ).asks_a_lead
        for verdict in Verdict
    }

    assert routes == {Verdict.UNDER: False, Verdict.CROSSED: True, Verdict.UNREADABLE: True}


# ---------------------------------------------------------------------------------------
# What it may do to a class, which is one thing in one direction
# ---------------------------------------------------------------------------------------


def test_it_raises_automatic_to_routine_and_can_do_nothing_else() -> None:
    """THE SAFETY ARGUMENT, ASSERTED OVER EVERY INPUT RATHER THAN THE INTERESTING ONE.

    Mutation: return ``ApprovalClass.AUTOMATIC`` from any branch of
    ``class_under_the_ceiling``. The grid is every class against every verdict, so there is
    no combination of inputs under which this function makes a submission cheaper to
    approve. That is what lets the compile job apply it after ``classify_request`` without
    weakening a gate the deployed validator will re-derive.
    """
    for verdict in Verdict:
        reading = CeilingReading(
            verdict=verdict,
            day=NOW.date(),
            committed_usd=Decimal(0),
            ceiling_usd=CEILING,
            priced_runs=0,
            unpriced_runs=0,
        )
        for approval_class in ApprovalClass:
            answered = class_under_the_ceiling(approval_class, reading=reading)
            if approval_class is ApprovalClass.AUTOMATIC and reading.asks_a_lead:
                assert answered is ApprovalClass.ROUTINE
            else:
                assert answered is approval_class


def test_no_ceiling_configured_changes_nothing() -> None:
    """Mutation: treat a ``None`` reading as unreadable and route it to a lead.

    An unset ceiling and a reading that failed are different facts, and only one of them is
    about the day. Conflating them would mean deleting the line from ``config/policy.yaml``
    turns the mechanism to maximum rather than off, so nobody could switch it off without
    reading the code -- which is the opposite of what a reviewed configuration file is for.
    """
    assert (
        class_under_the_ceiling(ApprovalClass.AUTOMATIC, reading=None)
        is ApprovalClass.AUTOMATIC
    )


def test_the_running_total_is_said_on_an_ordinary_day_too() -> None:
    """Mutation: return an empty sentence unless the ceiling is crossed.

    A control that speaks only when it fires cannot be told apart from one that is switched
    off, which is the state this platform was already in about every other cost control it
    believed it had: ``config/reports/surfaces.yaml`` recorded "nothing anywhere may stop a
    run for cost" as a proven property. The running total on every compile is what makes the
    mechanism observable on the day it does nothing.
    """
    said = day(minted(cost=Decimal("12.34"))).said

    assert "$12.34" in said
    assert "$1,000.00" in said


def test_committed_today_reports_the_denominator_behind_its_own_figure() -> None:
    """Mutation: return the sum alone and let the caller assume it is complete.

    The count of unpriced entries is the only thing that distinguishes a quiet day from an
    unreadable one, and a function returning a bare total makes that distinction impossible
    at the call site. It is also the only signal that a workflow has stopped passing the
    figure, which would otherwise show up as every run going to a lead for no stated reason.
    """
    total, priced, unpriced = committed_today(
        [minted(cost=Decimal(5)), minted(cost=None), minted(approval_class="routine")],
        day=NOW.date(),
    )

    assert (total, priced, unpriced) == (Decimal(5), 1, 1)
