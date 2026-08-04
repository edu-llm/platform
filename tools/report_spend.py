"""The spend section of the daily activity, as a section rather than as a report.

**THE ACTIVITY DOES NOT EXIST YET, WHICH IS WHY THIS IS SHAPED THE WAY IT IS.**
``docs-frank/reference/system-overview.md`` describes a morning message and an
``activity/YYYY-MM-DD.md`` in this repository carrying what ran, by whom and at what cost.
Nothing in ``tools/`` or ``.github/workflows/`` produces either one today: the closest thing
running is ``tools/visibility_board.py``, which the nightly writes to a step summary and
which answers a different question. So rather than inventing a second activity generator
that would have to be merged with the real one later, this computes the spend section and
hands it back as markdown. When the activity generator exists it calls
:func:`spend_section` and pastes the result in.

Two ways to run it in the meantime. With no arguments it prints the section, which is what
the generator will eventually do with it. With ``--json`` it prints the same figures as data,
for anything that wants to render them itself.

**The account total and the per-team split are two different measurements and this does not
pretend otherwise.** The total is real money from Cost Explorer for the whole account, which
includes six other projects sharing ``sbsandbox``. The split is measured compute from this
platform's lineage records, priced at the catalog rate.
:mod:`edullm_platform.run_costs` explains why there is no third option: cost allocation tags
cannot be activated from a linked account, and a Cost Explorer query grouped by
``edullm:team`` returns the whole account's spend under the empty-value key.

Exit codes follow the repository's convention. 0 reported, 2 the inputs could not be read.
There is no 1: this tool judges nothing, and in particular a projection over the limit is
not a failure. Exiting non-zero on an over-limit forecast would make a red job the mechanism
by which spending is noticed, and a red job in a path something else depends on is one step
away from being a control. The owner asked for a number, not a brake.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

from report_run_costs import ReportInputError, read_records, sync_bucket

from edullm_platform.capture_tooling import CaptureFailedError, aws
from edullm_platform.config import load_yaml
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.run_costs import attribute_to_teams, run_costs
from edullm_platform.spend import (
    CENTS,
    DailyCost,
    Projection,
    SpendLimits,
    TeamShare,
    month_start,
    project,
    render_section,
    whole_days_before,
)

__all__ = [
    "EXIT_OK",
    "EXIT_UNUSABLE",
    "SPEND_LIMITS_PATH",
    "build_parser",
    "daily_costs",
    "main",
    "read_team_shares",
    "service_costs",
    "spend_section",
]

EXIT_OK = 0
EXIT_UNUSABLE = 2

#: Where the monthly limit is read from. Outside ``config/`` for the reason that file's own
#: header gives, and named here so that moving it there is one line rather than a search.
SPEND_LIMITS_PATH = TOOLS_DIRECTORY / "spend-limits.yaml"

DEFAULT_LINEAGE_BUCKET = "sbsandbox-intern-edullm-lineage"

#: Cost Explorer's own name for what was actually charged. Not ``AmortizedCost``, which
#: spreads a reservation over the term it covers: this account holds none, so the two agree
#: today, and the unblended figure is the one that matches what a person sees in the console
#: and in the Budgets API when they go to check this number.
METRIC = "UnblendedCost"


def _amount(value: Any) -> Decimal:
    """One Cost Explorer money field, refused rather than guessed at if it is not one.

    Cost Explorer answers with money as a string, and a malformed one becoming ``Decimal(0)``
    would be a day of spend silently dropped out of an average that exists to be believed.
    """
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as error:
        raise ReportInputError(f"Cost Explorer returned {value!r} where money was expected") from error


def _ce(
    arguments: Sequence[str], *, profile: str | None, region: str
) -> Mapping[str, Any]:
    completed = aws(["ce", *arguments], profile=profile, region=region)
    if completed.returncode != 0:
        # The message carries the calling role's ARN and so the account id, and this text
        # reaches a report in a public repository. Only the shape of the failure is repeated.
        raise ReportInputError(
            "aws ce " + str(arguments[0]) + " was refused, so there is no spend figure"
        )
    try:
        answer = json.loads(completed.stdout or "{}")
    except ValueError as error:
        raise ReportInputError("Cost Explorer answered with something that is not JSON") from error
    if not isinstance(answer, Mapping):
        raise ReportInputError("Cost Explorer answered with something that is not an object")
    return answer


def daily_costs(*, today: date, profile: str | None, region: str) -> tuple[DailyCost, ...]:
    """Every whole day of this month's account spend.

    The window ends at ``today`` rather than tomorrow, which is what makes every bucket
    returned a complete day. Cost Explorer will happily answer for the current day and the
    figure grows over the following hours, so a window that included it would produce a
    different average depending on what time the report ran.
    """
    if whole_days_before(today) == 0:
        # Cost Explorer refuses a window whose start and end are the same day, and on the
        # first of the month there is no whole day to ask about. An empty answer is the true
        # one and project() renders it as "too early to say".
        return ()
    answer = _ce(
        [
            "get-cost-and-usage",
            "--time-period",
            f"Start={month_start(today):%Y-%m-%d},End={today:%Y-%m-%d}",
            "--granularity",
            "DAILY",
            "--metrics",
            METRIC,
        ],
        profile=profile,
        region=region,
    )
    days: list[DailyCost] = []
    for bucket in answer.get("ResultsByTime") or []:
        period = bucket.get("TimePeriod") or {}
        total = (bucket.get("Total") or {}).get(METRIC) or {}
        try:
            day = date.fromisoformat(str(period.get("Start")))
        except ValueError as error:
            raise ReportInputError(f"Cost Explorer returned {period!r} as a time period") from error
        days.append(DailyCost(day=day, cost_usd=_amount(total.get("Amount"))))
    return tuple(days)


def service_costs(*, today: date, profile: str | None, region: str) -> Mapping[str, Decimal]:
    """This month's whole-day spend per service, for the few lines that carry most of it."""
    if whole_days_before(today) == 0:
        return {}
    answer = _ce(
        [
            "get-cost-and-usage",
            "--time-period",
            f"Start={month_start(today):%Y-%m-%d},End={today:%Y-%m-%d}",
            "--granularity",
            "MONTHLY",
            "--metrics",
            METRIC,
            "--group-by",
            "Type=DIMENSION,Key=SERVICE",
        ],
        profile=profile,
        region=region,
    )
    totals: dict[str, Decimal] = {}
    for bucket in answer.get("ResultsByTime") or []:
        for group in bucket.get("Groups") or []:
            keys = group.get("Keys") or []
            if not keys:
                continue
            cost = _amount(((group.get("Metrics") or {}).get(METRIC) or {}).get("Amount"))
            name = str(keys[0])
            totals[name] = totals.get(name, Decimal(0)) + cost
    return {name: total.quantize(CENTS) for name, total in totals.items() if total > 0}


def read_team_shares(
    *, lineage_root: Path, config_dir: Path, since: date, until: date
) -> tuple[TeamShare, ...]:
    """Each team's measured compute over one month, reconciled against the roster.

    Delegated whole to :mod:`edullm_platform.run_costs`, for the reason
    ``tools/visibility_board.py`` gives beside the same call: a second arithmetic here would
    disagree with ``tools/report_run_costs.py`` eventually, and two dollar figures for one
    team is worse than one figure and a caveat.

    **THE WINDOW IS APPLIED HERE BECAUSE NEITHER OF THOSE TWO HAS ONE.** ``run_costs`` prices
    every record the lineage store holds, over all time, which is the right answer for a
    report titled "what runs have cost" and the wrong one beside a month-to-date total: this
    account's whole recorded history is four months, so an unwindowed split printed under a
    August figure would attribute July's GPU work to August and be wrong by most of it.
    Attempts are filtered rather than runs, and by ``started_at``, so an attempt that began
    last month and ended in this one is counted where it started. That is a choice rather
    than a law, and it is the same one AWS makes about an instance-hour.

    A team the roster does not bind is carried under the name it claimed rather than dropped.
    Three recorded runs claim ``tokenizer`` and two claim ``evaluation``, neither of which is
    a declared group, and spend nobody can route is the finding rather than the rounding
    error.
    """
    intents, every_attempt, _ = read_records(lineage_root)
    attempts = [
        attempt
        for attempt in every_attempt
        if since <= attempt.started_at.date() < until
    ]
    catalog = load_yaml(config_dir / "workload-catalog.yaml", WorkloadCatalog)
    organization = load_yaml(config_dir / "organization.yaml", OrganizationInventory)
    costs = run_costs(
        intents=intents, attempts=attempts, compute_profiles=catalog.compute_profiles
    )
    attribution = attribute_to_teams(costs, catalog=organization.team_bindings)
    return tuple(
        TeamShare(
            team=spend.team_id,
            cost_usd=spend.cost_usd.quantize(CENTS),
            runs=spend.runs,
            unpriced_runs=spend.unpriced_runs,
        )
        for spend in attribution.bound
    ) + tuple(
        TeamShare(
            team=f"{spend.claimed_team} (no such team in the roster)",
            cost_usd=spend.cost_usd.quantize(CENTS),
            runs=spend.runs,
            unpriced_runs=spend.unpriced_runs,
        )
        for spend in attribution.unbound
    )


def spend_section(
    *,
    today: date,
    limits_path: Path = SPEND_LIMITS_PATH,
    lineage_root: Path | None = None,
    lineage_bucket: str = DEFAULT_LINEAGE_BUCKET,
    config_dir: Path = PROJECT_ROOT / "config",
    profile: str | None = None,
    region: str = "us-east-1",
) -> tuple[str, Projection, tuple[TeamShare, ...] | None]:
    """The section the activity report pastes in, and the figures behind it.

    The account total is required and the per-team split is not, which is the asymmetry the
    whole function is arranged around. Cost Explorer answering is what makes this a spend
    report at all, so a refusal there is a :class:`ReportInputError` the caller reports. The
    lineage records live in a prefix the nightly reader role does not hold, so a refusal
    there is ordinary and degrades to a section with no split rather than to no section.
    """
    limits = load_yaml(limits_path, SpendLimits)
    days = daily_costs(today=today, profile=profile, region=region)
    projection = project(days, today=today, limit_usd=limits.monthly_limit_usd)
    services = service_costs(today=today, profile=profile, region=region)

    shares: tuple[TeamShare, ...] | None
    with tempfile.TemporaryDirectory() as scratch:
        try:
            root = lineage_root
            if root is None:
                root = Path(scratch)
                sync_bucket(lineage_bucket, root, profile=profile, region=region)
            shares = read_team_shares(
                lineage_root=root,
                config_dir=config_dir,
                since=month_start(today),
                until=today,
            )
        except (ReportInputError, CaptureFailedError, OSError, ValueError):
            shares = None

    section = render_section(
        projection,
        team_shares=shares,
        by_service=services,
        limit_source=limits_path.relative_to(PROJECT_ROOT).as_posix(),
    )
    return section, projection, shares


def _as_data(
    projection: Projection, shares: Sequence[TeamShare] | None
) -> Mapping[str, Any]:
    return {
        "month": f"{projection.month:%Y-%m}",
        "whole_days_elapsed": projection.whole_days,
        "days_in_month": projection.days_in_month,
        "month_to_date_usd": str(projection.month_to_date_usd),
        "daily_average_usd": (
            None if projection.daily_average_usd is None else str(projection.daily_average_usd)
        ),
        "projected_month_usd": (
            None if projection.projected_month_usd is None else str(projection.projected_month_usd)
        ),
        "monthly_limit_usd": str(projection.limit_usd),
        "would_exceed": projection.would_exceed,
        "by_team": (
            None
            if shares is None
            else [
                {
                    "team": share.team,
                    "cost_usd": str(share.cost_usd),
                    "runs": share.runs,
                    "unpriced_runs": share.unpriced_runs,
                }
                for share in shares
            ]
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    parser.add_argument("--limits", type=Path, default=SPEND_LIMITS_PATH)
    parser.add_argument(
        "--lineage-root",
        type=Path,
        default=None,
        help="a directory already holding intent/ and attempt/ records, rather than syncing",
    )
    parser.add_argument("--lineage-bucket", default=DEFAULT_LINEAGE_BUCKET)
    parser.add_argument("--output", type=Path, help="write the section here rather than to stdout")
    parser.add_argument("--json", action="store_true", help="print the figures rather than prose")
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=None,
        help="the day to report as of, in UTC. Defaults to today, and exists for tests",
    )
    # No default profile, for the reason tools/visibility_board.py gives beside its own: a
    # scheduled run assumes a role and passes none, and a default would send it looking for
    # an SSO session that is not there.
    parser.add_argument("--profile", default=None)
    parser.add_argument("--region", default="us-east-1")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    today = options.today or datetime.now(UTC).date()

    try:
        section, projection, shares = spend_section(
            today=today,
            limits_path=options.limits,
            lineage_root=options.lineage_root,
            lineage_bucket=options.lineage_bucket,
            config_dir=options.config_dir,
            profile=options.profile,
            region=options.region,
        )
    except (ReportInputError, CaptureFailedError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return EXIT_UNUSABLE

    rendered = (
        json.dumps(_as_data(projection, shares), indent=2) + "\n" if options.json else section
    )
    if options.output:
        options.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
