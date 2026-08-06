"""The day's runs, the one the message opens with, and the source that usually is not there.

**THE LARGEST RUN IS THE FIRST LINE OF THE MESSAGE, SO PICKING IT WRONG IS THE WHOLE PRODUCT.**
`system-overview.md` § "Where everything is seen" says the first line names a person, an
experiment and a figure, because a message opening with a total opens with something nobody is
accountable for. Two ways to pick it wrong are tested below: comparing money against a run that
has none, and picking across the whole substrate rather than across the day.

**THIS IS A VIEW OVER THE SUBSTRATE AND THE TESTS BUILD ONE DIRECTLY.** The substrate has its own
tests for how it is read; these build ``RunFacts`` by hand so that a failure here is a failure of
the aggregation and never of the ingestion.

**AN UNREAD SOURCE IS NOT AN ABSENT ONE**, three times over: the experiment tag needs a grant,
the durations are under a lineage prefix, and the launch feed is a CloudTrail read this account
is large enough to refuse. The last is the one that is unread on an ordinary day, so the pair of
activities that both produce no mismatch list -- one because nothing launched, one because
nobody could look -- is asserted here to render differently.

Nothing here reaches AWS.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from edullm_platform.activity import (
    day_activity,
    render_launch_feed_unread,
    render_launch_window,
    render_section,
)
from edullm_platform.cells import outcome_of_cells
from edullm_platform.substrate import (
    SOURCE_EMPTY,
    SOURCE_NOT_READ,
    SOURCE_READ,
    AttemptFacts,
    LaunchEvent,
    RunFacts,
    SourceGap,
    Substrate,
)

DAY = date(2026, 8, 4)
COLLECTED = datetime(2026, 8, 5, 5, 0, tzinfo=UTC)

RUN_A = "run_019fa73d-be37-7066-984b-a4bacf194f49"
RUN_B = "run_019fa96f-8f10-705a-a7a9-69c42eafce16"
RUN_C = "run_019fa984-085c-7088-9c94-799e4b5d9126"

#: The reason this account actually gave on 2026-08-05, shortened. A real one rather than an
#: invented one, so that the test asserting the reason reaches the page is asserting against
#: the shape of sentence ``tools/read_launch_events.py`` produces.
CEILING = SourceGap(
    source="cloudtrail:LookupEvents",
    reason="RunInstances has more than 6000 events between 2026-08-05 and 2026-08-06",
    unanswered="no mismatch list exists for this window, which is not the same as an empty one",
)

#: A gap that is not the launch feed's, so that a reader keyed on position rather than on the
#: source name prints this reason under the mismatch heading and is caught doing it.
BATCH = SourceGap(
    source="batch:DescribeJobs",
    reason="the reading role holds no batch action",
    unanswered="no run can be reported as running rather than finished",
)


def _facts(
    run_id: str,
    submitter: str,
    usd: str | None,
    *,
    hour: int = 9,
    day: date = DAY,
    experiment: str | None = None,
    seconds: str = "3600",
    state: str = "succeeded",
    state_source: str = "attempt",
) -> RunFacts:
    started = datetime(day.year, day.month, day.day, hour, tzinfo=UTC)
    attempts = (
        ()
        if state_source == "intent"
        else (
            AttemptFacts(
                attempt_id=f"att_019fa974-10b2-74b7-86dd-0c93bc5cd7{hour:02x}",
                ordinal=1,
                scheduler_job_id=f"00000000-0000-0000-0000-0000000000{hour:02x}",
                started_at=started,
                ended_at=datetime(day.year, day.month, day.day, hour + 1, tzinfo=UTC),
                terminal_state=state,
            ),
        )
    )
    cells = outcome_of_cells(
        (one.scheduler_job_id, one.ordinal, one.terminal_state) for one in attempts
    )
    return RunFacts(
        run_id=run_id,
        submitter=submitter,
        team="pre-training",
        experiment=experiment,
        repository="OLMo-core",
        commit_sha="4204375e6db85abc244ec7f626de8d3cc3511402",
        image_digest="sha256:" + "4e" * 32,
        dataset_release="regmix-10b-v1",
        workload_profile="olmo-core-train",
        compute_profile="gpu-8xa100",
        wandb_project="olmo-core-pre-training",
        fanout_size=None,
        submitted_at=started,
        approving_environment="run-approval-lead",
        workflow_run_id=19407766,
        workflow_run_url="https://github.com/edu-llm/platform/actions/runs/19407766/attempts/1",
        attempts=attempts,
        state=state,
        state_source=state_source,
        cells_total=None if cells is None else cells.total,
        cells_succeeded=None if cells is None else cells.succeeded,
        cells_failed=None if cells is None else cells.failed,
        cells_said=None if cells is None else cells.said,
        seconds=Decimal(seconds),
        cost_usd=None if usd is None else Decimal(usd),
        unpriced_reason=None if usd is not None else "a spot profile is not priced",
    )


def _a_launch() -> LaunchEvent:
    """One launch by a role nothing here binds, which is all these tests need of a feed."""
    return LaunchEvent(
        event_id="b1e2c3d4-0000-4000-8000-a1b2c3d4e5f6",
        event_name="RunInstances",
        occurred_at=datetime(DAY.year, DAY.month, DAY.day, 4, 30, tzinfo=UTC),
        role_name="Intern-cathy.du-sbsandbox",
        run_id=None,
    )


def _substrate(
    *facts: RunFacts,
    experiments_read: bool = True,
    attempts_read: bool = True,
    launches: tuple[LaunchEvent, ...] | None = (),
    gaps: tuple[SourceGap, ...] = (),
    collected_at: datetime = COLLECTED,
) -> Substrate:
    """A substrate built by hand, with every outcome written out rather than derived.

    The outcomes are literals here on purpose. Deriving them from the arguments would make
    every assertion below a statement about this helper, which is the check-that-cannot-fail
    shape this repository keeps finding.
    """
    return Substrate(
        collected_at=collected_at,
        runs={one.run_id: one for one in facts},
        launches=launches,
        source_outcomes={
            "attempt": SOURCE_READ if attempts_read else SOURCE_NOT_READ,
            "experiment": SOURCE_READ if experiments_read else SOURCE_NOT_READ,
            "launch": SOURCE_NOT_READ
            if launches is None
            else (SOURCE_EMPTY if len(launches) == 0 else SOURCE_READ),
            "live": SOURCE_NOT_READ,
        },
        gaps=gaps,
    )


def test_the_largest_run_is_the_one_that_cost_the_most() -> None:
    """Mutation: sort by duration, or take the first row.

    The cheap run is the long one here, and it has to be. Two runs of equal length make
    "order by money" and "order by duration" pick the same winner, so the test would pass
    under both and prove nothing -- which is what it did until the mutation was applied.
    """
    activity = day_activity(
        day=DAY,
        substrate=_substrate(
            _facts(RUN_A, "alsy7009", "12.00", hour=9, seconds="21600"),
            _facts(RUN_B, "meric233", "87.83", hour=10, experiment="mixlaw-370m", seconds="3600"),
        ),
    )
    assert [row.run_id for row in activity.rows] == [RUN_A, RUN_B]
    assert activity.largest is not None
    assert activity.largest.run_id == RUN_B
    assert activity.largest.submitter == "meric233"
    assert activity.largest.experiment == "mixlaw-370m"


def test_a_run_with_no_figure_never_wins_on_a_missing_number() -> None:
    """Mutation: rank the unpriced runs too, reading None as zero or as larger than any figure.

    A spot run is priced at nothing on purpose -- `run_costs` refuses to report an on-demand
    ceiling as a measurement -- and either reading of None puts a run nobody can cost into
    the first line of a pushed message.

    The day whose only run is unpriced is the case that does the work. Where something else
    is priced, reading None as zero still picks the priced run and the assertion cannot tell
    the two implementations apart; where nothing is priced, the honest answer is that there
    is no largest run and every mutation has to name one.
    """
    mixed = day_activity(
        day=DAY,
        substrate=_substrate(
            _facts(RUN_A, "alsy7009", None, hour=9, seconds="28800"),
            _facts(RUN_B, "meric233", "3.00", hour=10),
        ),
    )
    assert mixed.largest is not None
    assert mixed.largest.run_id == RUN_B
    assert mixed.unpriced == 1

    nothing_priced = day_activity(
        day=DAY, substrate=_substrate(_facts(RUN_A, "alsy7009", None, hour=9))
    )
    assert nothing_priced.rows != ()
    assert nothing_priced.largest is None
    assert nothing_priced.total_usd == Decimal(0)


def test_only_runs_that_belong_to_the_day_are_counted() -> None:
    """Mutation: aggregate the whole substrate, or apply the window in the collector.

    The substrate holds every run it could see over all time, because a snapshot refreshed on
    state change has no day at all. An unfiltered aggregation would put July's GPU work in
    this morning's message; this account's July was three orders of magnitude bigger than its
    June.
    """
    activity = day_activity(
        day=DAY,
        substrate=_substrate(
            _facts(RUN_A, "alsy7009", "12.00", hour=9),
            _facts(RUN_C, "philote-dev", "9999.00", hour=9, day=date(2026, 7, 20)),
        ),
    )
    assert [row.run_id for row in activity.rows] == [RUN_A]
    assert activity.total_usd == Decimal("12.00")


def test_a_refused_experiment_read_is_not_reported_as_no_experiment() -> None:
    """Mutation: collapse experiments_read into every row's experiment being None.

    Thirty rows reading "no experiment" is a statement about thirty runs. One sentence saying
    the tag read was refused is a statement about the reader, and only one of them is true.
    """
    refused = day_activity(
        day=DAY, substrate=_substrate(_facts(RUN_A, "alsy7009", "12.00"), experiments_read=False)
    )
    named_none = day_activity(day=DAY, substrate=_substrate(_facts(RUN_A, "alsy7009", "12.00")))
    assert refused.experiments_read is False
    assert named_none.experiments_read is True
    assert render_section(refused) != render_section(named_none)
    assert "could not be read" in render_section(refused)


def test_an_unread_attempt_prefix_is_named_rather_than_rendered_as_zeroes() -> None:
    """Mutation: render an unread duration as 0h 00m with no caveat.

    The reading role has held `intent/` and `result/` and not `attempt/` before, and every
    row on the page then has no figure. A page of zeroes reads as a day of instant free runs.
    """
    activity = day_activity(
        day=DAY,
        substrate=_substrate(
            _facts(RUN_A, "alsy7009", None, seconds="0", state="unknown", state_source="unread"),
            attempts_read=False,
        ),
    )
    rendered = render_section(activity)
    assert activity.attempts_read is False
    assert "`attempt/` prefix" in rendered
    assert "missing rather than zero" in rendered
    assert "unknown (not read)" in rendered


def test_a_day_with_nothing_on_it_is_a_day_with_nothing_on_it() -> None:
    """Mutation: raise, or return None, when no run ran.

    A quiet day still has a budget line and still has a mismatch denominator, so the message
    still goes out and this still has to render.
    """
    activity = day_activity(day=DAY, substrate=_substrate())
    assert activity.rows == ()
    assert activity.largest is None
    assert activity.people == 0
    assert "Nothing ran" in render_section(activity)


def test_the_number_of_people_counts_people_and_not_runs() -> None:
    """Mutation: count rows instead of distinct submitters."""
    activity = day_activity(
        day=DAY,
        substrate=_substrate(
            _facts(RUN_A, "alsy7009", "1.00", hour=9),
            _facts(RUN_B, "alsy7009", "2.00", hour=10),
            _facts(RUN_C, "meric233", "3.00", hour=11),
        ),
    )
    assert activity.people == 2
    assert len(activity.rows) == 3


def test_a_queued_run_appears_on_the_day_it_was_submitted() -> None:
    """Mutation: keep only the runs that have an attempt.

    `run_costs` drops an intent with no attempt, so an activity built on it could never show
    a queued, running or refused run -- three of the four states the overview names. This is
    the test that fails if the substrate is bypassed.
    """
    activity = day_activity(
        day=DAY,
        substrate=_substrate(
            _facts(RUN_A, "alsy7009", None, seconds="0", state="submitted", state_source="intent")
        ),
    )
    assert [row.run_id for row in activity.rows] == [RUN_A]
    assert activity.by_state == {"submitted": 1}
    assert activity.largest is None


def test_an_unread_launch_feed_does_not_read_like_one_that_found_nothing() -> None:
    """Mutation: render a launch feed of None the way an empty one renders.

    THIS IS THE ORDINARY STATE OF THIS ACCOUNT AND NOT A HYPOTHETICAL. `RunInstances` alone
    returned more than six thousand events for 2026-08-05 and the reader refused the feed
    rather than truncating it, so a whole day usually has no feed at all. Both of these
    produce a mismatch list of length zero; one is a quiet account and the other is a page
    nobody should read as one, and the difference has to survive into the rendering because
    the rendering is the only part a person sees.
    """
    unread = day_activity(
        day=DAY,
        substrate=_substrate(_facts(RUN_A, "alsy7009", "12.00"), launches=None, gaps=(CEILING,)),
    )
    nothing_launched = day_activity(
        day=DAY, substrate=_substrate(_facts(RUN_A, "alsy7009", "12.00"), launches=())
    )

    assert unread.launch_outcome == SOURCE_NOT_READ
    assert nothing_launched.launch_outcome == SOURCE_EMPTY
    assert render_launch_feed_unread(nothing_launched) is None
    section = render_launch_feed_unread(unread)
    assert section is not None
    assert "Not computed, and that is not the same as none found." in section
    assert render_section(unread) != render_section(nothing_launched)


def test_the_reason_the_launch_feed_was_refused_reaches_the_page() -> None:
    """Mutation: print that the feed was not read without printing why.

    A lapsed grant and a feed too big to read are both "not read" and are fixed by different
    people doing different things. The reader's own sentence is the only thing on the page
    that tells them apart.
    """
    activity = day_activity(
        day=DAY, substrate=_substrate(_facts(RUN_A, "alsy7009", "12.00"), launches=None,
                                      gaps=(CEILING,))
    )
    section = render_launch_feed_unread(activity)
    assert section is not None
    assert "more than 6000 events" in section


def test_the_launch_gap_is_found_by_name_and_not_by_position() -> None:
    """Mutation: take the first gap, or the last one, instead of matching the source.

    The collector appends gaps in whatever order it tried the sources, and Batch is appended
    unconditionally. A reader keyed on position prints "the reading role holds no batch
    action" under the mismatch heading, which is a true sentence about the wrong source.
    """
    activity = day_activity(
        day=DAY,
        substrate=_substrate(
            _facts(RUN_A, "alsy7009", "12.00"), launches=None, gaps=(BATCH, CEILING)
        ),
    )
    assert activity.launch_gap == CEILING
    section = render_launch_feed_unread(activity)
    assert section is not None
    assert "batch action" not in section


def test_a_feed_that_was_read_and_empty_is_reported_as_a_finding() -> None:
    """Mutation: say nothing when the feed was read and held nothing.

    An empty feed is a claim about the account -- nothing launched all day, on an account
    whose own autoscaler launches capacity most days -- and a claim that surprising belongs
    on the page rather than only in the mismatch section's denominator.
    """
    activity = day_activity(
        day=DAY, substrate=_substrate(_facts(RUN_A, "alsy7009", "12.00"), launches=())
    )
    assert "reported no launch at all" in render_section(activity)


def test_a_denominator_counted_before_the_day_ended_says_which_hours_it_covers() -> None:
    """Mutation: print the day and stop, or print nothing at all.

    THIS IS THE STATE OF EVERY PAGE THE AUDIT WILL WRITE. The collector asks CloudTrail for
    one day and the account can only answer for the part of it that has happened, so a
    reading taken at 05:00 carries five hours of launches under a heading naming a date. Six
    thousand is a large enough figure that a reader assumes it is the day, which makes it a
    wrong denominator that reads as a thorough one.
    """
    at_breakfast = day_activity(
        day=DAY,
        substrate=_substrate(
            _facts(RUN_A, "alsy7009", "12.00"),
            launches=(_a_launch(),),
            collected_at=datetime(DAY.year, DAY.month, DAY.day, 5, 0, tzinfo=UTC),
        ),
    )
    window = render_launch_window(at_breakfast)
    assert window is not None
    assert "05:00" in window
    assert "not the day" in window


def test_a_denominator_counted_after_the_day_ended_says_it_is_the_whole_day() -> None:
    """Mutation: print the partial-day sentence whatever the reading's own date.

    A standing caveat that is sometimes false is worse than none, because the morning it is
    false is the morning somebody stops reading it. Reporting a past day by hand does get the
    whole feed, and saying so is what keeps the sentence worth the line it takes.
    """
    afterwards = day_activity(
        day=DAY,
        substrate=_substrate(
            _facts(RUN_A, "alsy7009", "12.00"),
            launches=(_a_launch(),),
            collected_at=datetime(2026, 8, 6, 5, 0, tzinfo=UTC),
        ),
    )
    window = render_launch_window(afterwards)
    assert window is not None
    assert "the whole of what CloudTrail reports" in window
    assert "not the day" not in window


def test_a_feed_nobody_read_has_no_window_to_state() -> None:
    """Mutation: return the window sentence whether or not there is a feed under it.

    A window printed over a list that does not exist describes the hours in which nothing was
    examined, which is a caveat dressed as a measurement.
    """
    unread = day_activity(
        day=DAY,
        substrate=_substrate(_facts(RUN_A, "alsy7009", "12.00"), launches=None, gaps=(CEILING,)),
    )
    assert render_launch_window(unread) is None


def test_a_feed_that_was_read_leaves_the_list_to_the_mismatch_module() -> None:
    """Mutation: return a section whenever the mismatch list would be short.

    This function's whole job is to say when there is no list. A feed that was read has one,
    however short, and rendering a substitute here would put two mismatch sections on one
    page or replace a real list with a caveat.
    """
    activity = day_activity(
        day=DAY, substrate=_substrate(_facts(RUN_A, "alsy7009", "12.00"), launches=(_a_launch(),))
    )
    assert activity.launch_outcome == SOURCE_READ
    assert render_launch_feed_unread(activity) is None
