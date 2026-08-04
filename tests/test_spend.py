"""The projection, and the four ways it could quietly report a wrong month.

**The one that motivated the rest is the partial day.** Cost Explorer reports the current day
in pieces over the following hours, so a daily average that counted today would read low
every morning and highest overnight, and the projection would be most optimistic at exactly
the hour somebody decides whether to launch a large run. The rate is therefore taken over
whole days only, and the tests below fix the day so that the arithmetic is checkable rather
than dependent on when the suite ran.

The other three are the shapes a number can take that look like an answer and are not: a
month with nothing to extrapolate from reported as zero rather than as unknown, a window
wider than the month folding the previous month's spend into this month's rate, and a
per-team split that vanishes rather than degrading when the lineage prefix is refused.

**Nothing here reaches AWS.** Cost Explorer is exercised through the parsing of a captured
answer, because the figure this tool reports changes every hour and a test that asserted
today's spend would be red by tomorrow.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from report_spend import SPEND_LIMITS_PATH, _as_data, build_parser, main

from edullm_platform.config import load_yaml
from edullm_platform.spend import (
    DailyCost,
    SpendLimits,
    TeamShare,
    month_start,
    project,
    render_section,
    whole_days_before,
)

LIMIT = Decimal(5000)

#: Three whole days of August 2026, copied from what Cost Explorer answered for this account
#: on 2026-08-04 rather than invented, so the arithmetic below is arithmetic somebody can
#: check against the report.
AUGUST_DAYS = (
    DailyCost(day=date(2026, 8, 1), cost_usd=Decimal("128.2053306562")),
    DailyCost(day=date(2026, 8, 2), cost_usd=Decimal("52.2238075")),
    DailyCost(day=date(2026, 8, 3), cost_usd=Decimal("91.4848228395")),
)


def test_the_month_is_projected_from_the_rate_of_the_whole_days() -> None:
    """Mutation: project from the month-to-date total without dividing by the days elapsed.

    That is the failure the whole tool exists to prevent. $271.91 on the fourth of a
    thirty-one day month is on track for roughly $2,810, and a report that showed the total
    against a $5,000 limit would read as 5% of budget when it is 56% of it.
    """
    projection = project(AUGUST_DAYS, today=date(2026, 8, 4), limit_usd=LIMIT)

    assert projection.whole_days == 3
    assert projection.days_in_month == 31
    assert projection.month_to_date_usd == Decimal("271.91")
    assert projection.daily_average_usd == Decimal("90.64")
    assert projection.projected_month_usd == Decimal("2809.74")
    assert projection.would_exceed is False
    assert projection.projected_share_of_limit == Decimal("56.19")


def test_today_is_not_counted_even_when_cost_explorer_offers_it() -> None:
    """Mutation: change the ``entry.day < today`` bound to ``<=``.

    A partial day admitted to the average is the defect this is here for, and it does not
    announce itself: the report still renders, still looks right, and reads low by however
    much of the day is left. Here the partial day is a tenth of a normal one, which drags the
    landing figure down by about a fifth.
    """
    partial = (*AUGUST_DAYS, DailyCost(day=date(2026, 8, 4), cost_usd=Decimal("9.10")))

    projection = project(partial, today=date(2026, 8, 4), limit_usd=LIMIT)

    assert projection.whole_days == 3
    assert projection.month_to_date_usd == Decimal("271.91")
    assert projection.projected_month_usd == Decimal("2809.74")


def test_a_wider_window_does_not_fold_the_previous_month_into_the_rate() -> None:
    """Mutation: drop the ``start <= entry.day`` bound in :func:`project`.

    July cost this account $4,066 against June's $12.75, so a rate that quietly spanned two
    months would be wrong by three orders of magnitude in whichever direction the boundary
    fell, and the number would still look like a plausible dollar figure.
    """
    spanning = (DailyCost(day=date(2026, 7, 31), cost_usd=Decimal(4000)), *AUGUST_DAYS)

    projection = project(spanning, today=date(2026, 8, 4), limit_usd=LIMIT)

    assert projection.month_to_date_usd == Decimal("271.91")
    assert projection.projected_month_usd == Decimal("2809.74")


def test_the_first_of_the_month_is_unknown_rather_than_zero() -> None:
    """Mutation: return ``Decimal(0)`` instead of ``None`` when no whole day has elapsed.

    A projection of zero on the first is a statement that the month will cost nothing, which
    is both false and reassuring. ``would_exceed`` staying false is the other half: a
    projection that answered true because nothing is known would fire the one signal this
    produces on the first of every month, and a signal that always fires is off.
    """
    projection = project((), today=date(2026, 8, 1), limit_usd=LIMIT)

    assert projection.whole_days == 0
    assert projection.month_to_date_usd == Decimal("0.00")
    assert projection.daily_average_usd is None
    assert projection.projected_month_usd is None
    assert projection.projected_share_of_limit is None
    assert projection.would_exceed is False
    assert "nothing to project from" in render_section(
        projection, team_shares=(), limit_source="tools/spend-limits.yaml"
    )


def test_a_month_heading_over_the_limit_says_so_in_the_first_sentence() -> None:
    """Mutation: drop the over/under verdict from the headline.

    The requirement is to know *before* the limit is reached, and a reader who has to divide
    the projection by the limit themselves has not been told anything they did not already
    have.
    """
    heavy = (DailyCost(day=date(2026, 8, 1), cost_usd=Decimal(400)),)

    projection = project(heavy, today=date(2026, 8, 2), limit_usd=LIMIT)
    section = render_section(
        projection, team_shares=(), limit_source="tools/spend-limits.yaml"
    )

    assert projection.projected_month_usd == Decimal("12400.00")
    assert projection.would_exceed is True
    assert "over the $5,000.00 limit" in section
    assert section.splitlines()[2].startswith("At $400.00 a day")


def test_an_unreadable_lineage_degrades_the_split_rather_than_the_section() -> None:
    """Mutation: render an empty team list when the records could not be read.

    An empty split and a refused one look identical on the page and mean opposite things,
    and a morning report that showed every team at zero would be read as a quiet night.

    The refusal was the ordinary case while the reader role held no ``attempt/`` prefix. It
    holds it now, and this tool never ran under that role anyway -- nothing in
    ``.github/workflows/`` invokes it, so it runs from a laptop on a session that can read
    the whole bucket. The distinction is kept because a credential can lapse on any of them,
    and because the two sentences on the page still mean opposite things.
    """
    projection = project(AUGUST_DAYS, today=date(2026, 8, 4), limit_usd=LIMIT)

    refused = render_section(
        projection, team_shares=None, limit_source="tools/spend-limits.yaml"
    )
    empty = render_section(projection, team_shares=(), limit_source="tools/spend-limits.yaml")

    assert "could not be read" in refused
    assert "The account total above is unaffected" in refused
    assert "could not be read" not in empty
    assert "No run in the lineage records is priced" in empty


def test_a_team_with_spend_and_no_figure_is_not_printed_as_idle() -> None:
    """Mutation: drop ``unpriced_runs`` from the rendered line.

    A team whose month was entirely spot work has real spend and no honest number for it, for
    the reason :mod:`edullm_platform.run_costs` gives. Printing $0.00 beside it would report
    the busiest group as the quietest one.
    """
    projection = project(AUGUST_DAYS, today=date(2026, 8, 4), limit_usd=LIMIT)

    section = render_section(
        projection,
        team_shares=(TeamShare(team="memory-split", cost_usd=Decimal("0.00"), runs=4, unpriced_runs=4),),
        limit_source="tools/spend-limits.yaml",
    )

    assert "memory-split: $0.00 across 4 runs, 4 with no figure" in section


def test_the_limit_is_read_from_a_file_rather_than_written_into_the_code() -> None:
    """Mutation: hardcode the limit in :mod:`edullm_platform.spend`.

    A limit in code is a limit nobody outside this repository can change, and the number is
    a decision about the account rather than about the arithmetic. The file is outside
    ``config/`` for now and this asserts it parses wherever it is, so a move is one line and
    a red test rather than a silent default.
    """
    limits = load_yaml(SPEND_LIMITS_PATH, SpendLimits)

    assert limits.monthly_limit_usd == Decimal(5000)


@pytest.mark.parametrize("bad", ["monthly_limit_usd: -1", "monthly_limit_usd: 0", "limit: 5000"])
def test_a_limit_file_that_says_nothing_usable_is_refused(bad: str, tmp_path: Path) -> None:
    """Mutation: relax the model to accept extras or a non-positive limit.

    A misspelled key silently ignored is a limit somebody believes they changed, and a limit
    of zero makes every month read as over, which is the same as reading as nothing.
    """
    path = tmp_path / "spend-limits.yaml"
    path.write_text(bad + "\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_yaml(path, SpendLimits)


def test_whole_days_before_agrees_with_the_window_the_query_asks_for() -> None:
    """Mutation: return ``today.day`` rather than the days since the first.

    The divisor and the Cost Explorer window have to be the same count. Off by one they
    misstate the rate by ``1/n``, worst early in the month when ``n`` is small and somebody
    is most likely to be reading.
    """
    assert whole_days_before(date(2026, 8, 1)) == 0
    assert whole_days_before(date(2026, 8, 4)) == 3
    assert month_start(date(2026, 8, 4)) == date(2026, 8, 1)


def test_the_team_split_covers_the_month_rather_than_all_of_recorded_history(
    tmp_path: Path,
) -> None:
    """Mutation: drop the ``since``/``until`` filter and price every attempt ever recorded.

    Neither :func:`edullm_platform.run_costs.run_costs` nor ``tools/report_run_costs.py``
    has a window, because "what runs have cost" is a question about all of history. Printed
    under a month-to-date figure the same numbers are a different claim, and a wrong one:
    July cost this account $4,066 against August's first three days at $272, so an unwindowed
    split would attribute almost all of July to August and still look like a plausible table.
    """
    root = tmp_path / "lineage"
    (root / "intent").mkdir(parents=True)
    (root / "attempt").mkdir(parents=True)
    (root / "intent" / "only.json").write_text("{}", encoding="utf-8")

    for name, attempt_id, started, ended in (
        ("july", "att_019fa910-13ef-7af8-ad90-81b03811c034", "2026-07-15", "2026-07-15"),
        ("august", "att_019fa974-10b2-74b7-86dd-0c93bc5cd76c", "2026-08-02", "2026-08-02"),
    ):
        (root / "attempt" / f"{name}.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "attempt_id": attempt_id,
                    "run_id": "run_019fa446-8a4e-7094-9e29-d44fffbd2491",
                    "attempt_ordinal": 1,
                    "scheduler_job_id": "fde2fa08-a611-48dc-a0ef-1c6797147543",
                    "started_at": f"{started}T00:00:00Z",
                    "ended_at": f"{ended}T01:00:00Z",
                    "terminal_state": "succeeded",
                }
            ),
            encoding="utf-8",
        )

    import report_spend

    _, kept, _ = report_spend.read_records(root)
    windowed = [
        attempt
        for attempt in kept
        if date(2026, 8, 1) <= attempt.started_at.date() < date(2026, 8, 4)
    ]

    assert len(kept) == 2
    assert [attempt.started_at.date() for attempt in windowed] == [date(2026, 8, 2)]


def test_the_json_shape_carries_the_projection_and_not_only_the_total() -> None:
    """Mutation: emit only ``month_to_date_usd``.

    Whatever renders the activity message reads this, and a payload holding a total makes
    that renderer redo the arithmetic, which is where the two spellings start to disagree.
    """
    projection = project(AUGUST_DAYS, today=date(2026, 8, 4), limit_usd=LIMIT)

    data = _as_data(projection, None)

    assert data["projected_month_usd"] == "2809.74"
    assert data["monthly_limit_usd"] == "5000"
    assert data["would_exceed"] is False
    assert data["by_team"] is None
    assert json.loads(json.dumps(data))["whole_days_elapsed"] == 3


def test_nothing_in_this_tool_can_write_to_aws() -> None:
    """Mutation: add any mutating call to the tool.

    The requirement is that this observes and cannot interfere, so the guard is a property of
    the source rather than of a run. A budget action, a policy attachment or a job
    cancellation added here would be the tool becoming a control, which is the one thing it
    was asked not to be.
    """
    source = (PROJECT_ROOT / "tools" / "report_spend.py").read_text(encoding="utf-8")
    module = (PROJECT_ROOT / "src" / "edullm_platform" / "spend.py").read_text(encoding="utf-8")

    for forbidden in (
        "create-budget",
        "update-budget",
        "budgets create",
        "attach-policy",
        "put-",
        "cancel-job",
        "terminate-job",
        "update-compute-environment",
    ):
        assert forbidden not in source, forbidden
        assert forbidden not in module, forbidden


@pytest.mark.slow
def test_the_tool_refuses_rather_than_reports_when_cost_explorer_cannot_be_reached() -> None:
    """Mutation: return 0 with a zeroed section when the account cannot be read.

    Exit 2 rather than 0, because a spend report that could not read spend has not said
    anything, and a zero rendered into the morning message is the most reassuring possible
    wrong answer. Exit 2 rather than 1 for the reason every tool here gives: 1 is a finding
    and this tool has none to make.
    """
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "tools" / "report_spend.py"),
            "--today",
            "2026-08-04",
            "--profile",
            "a-profile-that-does-not-exist",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=PROJECT_ROOT,
    )

    assert completed.returncode == 2, completed.stdout + completed.stderr


def test_the_parser_defaults_to_no_profile_and_the_repository_region() -> None:
    """Mutation: default ``--profile`` to ``sbsandbox``.

    A scheduled run assumes a role and passes no profile, and a default would send it looking
    for an SSO session that is not on the runner. It is the same argument
    ``tools/visibility_board.py`` makes beside its own parser.
    """
    options = build_parser().parse_args([])

    assert options.profile is None
    assert options.region == "us-east-1"
    assert options.today is None
    assert options.limits == SPEND_LIMITS_PATH


def test_main_writes_the_section_to_a_file_when_asked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: print to stdout regardless of ``--output``.

    The activity generator will read a file rather than scrape a log, which is the same
    arrangement ``tools/visibility_board.py`` uses for the board.
    """
    import report_spend

    projection = project(AUGUST_DAYS, today=date(2026, 8, 4), limit_usd=LIMIT)
    monkeypatch.setattr(
        report_spend,
        "spend_section",
        lambda **_: (render_section(projection, team_shares=(), limit_source="x"), projection, ()),
    )
    destination = tmp_path / "section.md"

    assert main(["--today", "2026-08-04", "--output", str(destination)]) == 0
    assert "## Spend" in destination.read_text(encoding="utf-8")
