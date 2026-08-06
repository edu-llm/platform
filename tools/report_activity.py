"""The day's activity as one document: what ran, what launched that nothing accounts for, and
what it cost.

**THIS IS THE ONLY PLACE THE COMPUTATIONS MEET, AND IT DELIBERATELY OWNS NO ARITHMETIC.**
``edullm_platform.activity``, ``edullm_platform.mismatch`` and ``edullm_platform.spend`` each
answer one question and each is a function of its arguments. This hands them what they need and
pastes what they return. A second arithmetic here would disagree with
``tools/report_run_costs.py`` eventually, and two dollar figures for one run is worse than one
figure and a caveat.

**IT PREFERS A READING SOMEBODY ALREADY TOOK.** ``--reading`` points at a document
``tools/read_substrate.py --write`` produced, which is what the audit commits to the
``machine/substrate`` branch every morning. Reading that file rather than the account is the
whole point of the substrate being one pipeline: the page is then provably a view over the
reading beside it, and a page and a reading that disagree about one run cannot happen. Without
``--reading`` it collects one itself, which is how a laptop asks about a day nobody captured.

**A READING FROM ANOTHER DAY MAY NOT SUPPLY THE MISMATCH ARM.** The lineage records are the
whole store, so any reading answers for any day's runs. The launch feed is not: the collector
reads one day of CloudTrail, so a reading taken on the sixth carries the sixth's launches and
knows nothing about the fifth. Joining those against the fifth's runs would produce a mismatch
list with a denominator from the wrong window, which is the failure the denominator exists to
prevent, so the arm is refused and the page says which two days disagreed.

**EXIT CODES: 0 A DOCUMENT WAS WRITTEN, 2 NOTHING COULD BE, AND THERE IS NO 1.** A mismatch is
not a failure of this tool and neither is a month projected over the limit.
``tools/report_spend.py`` carries the same rule and the reason is the same: a red job in a path
something else depends on is one step away from being a control, and
``src/edullm_platform/spend.py`` records that nothing here may interfere with a run that works.
What a source that did not answer earns is a section on the page, not an exit code.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

from read_substrate import STAGED_LINEAGE, collect
from report_run_costs import ReportInputError
from report_spend import DEFAULT_LINEAGE_BUCKET, SPEND_LIMITS_PATH, spend_section

from edullm_platform import activity as activity_module
from edullm_platform import mismatch as mismatch_module
from edullm_platform.activity import LAUNCH_SOURCE
from edullm_platform.capture_tooling import CaptureFailedError
from edullm_platform.config import load_yaml
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.mismatch import MismatchReport
from edullm_platform.substrate import SOURCE_NOT_READ, SourceGap, Substrate, from_document

__all__ = [
    "EXIT_OK",
    "EXIT_UNUSABLE",
    "build_parser",
    "load_reading",
    "main",
    "mismatch_report",
    "render_document",
    "restrict_to_the_day",
]

EXIT_OK = 0
EXIT_UNUSABLE = 2


def restrict_to_the_day(substrate: Substrate, day: date) -> Substrate:
    """The same substrate, with a launch feed that does not answer for a day it never saw.

    The runs need no restriction: the lineage store is read whole, so every reading holds
    every run and :func:`~edullm_platform.activity.day_activity` applies the window. The
    launch feed does, because the collector reads one day of CloudTrail and nothing in the
    document records which. A feed from the sixth joined against the fifth's runs would
    report every launch of the sixth as unaccounted for by the fifth, which is a mismatch
    list that is wrong rather than short.
    """
    if substrate.collected_at.date() == day:
        return substrate
    return replace(
        substrate,
        launches=None,
        source_outcomes={**substrate.source_outcomes, LAUNCH_SOURCE: SOURCE_NOT_READ},
        gaps=(
            *(gap for gap in substrate.gaps if gap.source != "cloudtrail:LookupEvents"),
            SourceGap(
                source="cloudtrail:LookupEvents",
                reason=(
                    f"this reading was taken on {substrate.collected_at:%Y-%m-%d} and its "
                    f"launch feed covers that day, not {day:%Y-%m-%d}"
                ),
                unanswered=(
                    "no mismatch list exists for this window. Report the day the reading "
                    "covers, or take a reading of this one"
                ),
            ),
        ),
    )


def load_reading(path: Path) -> Substrate:
    """One reading off disk, refusing a document this tree cannot read.

    Errors are left to the caller rather than swallowed. A reading that would not parse and a
    reading that held nothing are the same absent page from here, and only one of them is a
    reason to go looking at the capture.
    """
    return from_document(json.loads(path.read_text(encoding="utf-8")))


def mismatch_report(
    *, substrate: Substrate, inventory: OrganizationInventory
) -> MismatchReport | None:
    """The union arm, computed over the substrate's launch feed rather than over CloudTrail.

    ``None`` exactly when the feed was not read. The mismatch list is the one part of the
    activity that has no run id at all -- it is made of the launches that joined to nothing --
    which is why it is a union with the run-keyed rows and never a column on them.
    """
    if substrate.launches is None:
        return None
    return mismatch_module.compute_mismatches(
        substrate.launches,
        role_logins=inventory.aws_identities.role_logins(),
        excluded_roles=inventory.aws_identities.excluded_role_names(),
        known_run_ids=substrate.known_run_ids,
    )


def _not_read_section(substrate: Substrate) -> str:
    """Every source that did not answer, with the question it left open.

    Printed on the page rather than logged, for the reason the mismatch denominator is
    printed on the same page: a figure that is missing and a figure that is zero look alike,
    and the reader who needs the difference is not reading the runner's log.
    """
    if not substrate.gaps:
        return ""
    lines = ["## What was not read", ""]
    lines += [f"- `{gap.source}` — {gap.reason}. {gap.unanswered}." for gap in substrate.gaps]
    lines.append("")
    return "\n".join(lines)


def render_document(
    *,
    day: date,
    substrate: Substrate,
    inventory: OrganizationInventory,
    spend_markdown: str,
) -> str:
    """The whole page, from values. Reaches nothing and is the half the tests exercise."""
    today = activity_module.day_activity(day=day, substrate=substrate)
    report = mismatch_report(substrate=substrate, inventory=inventory)

    # Every block below already ends in a blank line, so the join adds one between them. The
    # header is assembled as one block for that reason: a list of lines joined with the
    # sections would have its own blank lines dropped by the filter at the end.
    header = "\n".join(
        [
            f"# The activity, {day:%A %-d %B %Y}",
            "",
            (
                f"Read at {substrate.collected_at:%Y-%m-%d %H:%M} UTC and written by "
                "`tools/report_activity.py`. Nothing here can stop a run: "
                '`docs-frank/reference/system-overview.md` § "How money gets spent, and what '
                'stops a mistake" records the unwiring as the decision rather than an omission.'
            ),
            "",
        ]
    )
    parts = [header, activity_module.render_section(today)]

    unread = activity_module.render_launch_feed_unread(today)
    if unread is not None:
        parts.append(unread)
    elif report is not None:
        parts.append(
            mismatch_module.render_section(
                report, window=activity_module.render_launch_window(today)
            )
        )

    parts += [spend_markdown, _not_read_section(substrate)]
    return "\n".join(part for part in parts if part)


def _spend_markdown(
    *,
    day: date,
    substrate: Substrate,
    options: argparse.Namespace,
    lineage_root: Path | None,
) -> str:
    """The budget section, or the reason there is not one.

    Degraded rather than fatal. The lineage records are what this tool cannot do without;
    Cost Explorer refusing costs the budget line and nothing else, and a page carrying the
    runs and no budget is still the page somebody asked for.
    """
    if options.offline:
        return (
            "## Spend\n\nNot computed: Cost Explorer was not read. The account total is the "
            "one figure on this page that cannot come from the lineage records. "
            "`tools/report_spend.py` is what produces it, and it needs a credential holding "
            "`ce:GetCostAndUsage`.\n"
        )
    try:
        markdown, _, shares = spend_section(
            today=day,
            limits_path=options.limits,
            lineage_root=lineage_root,
            lineage_bucket=options.lineage_bucket,
            config_dir=options.config_dir,
            profile=options.profile,
            region=options.region,
        )
    except (ReportInputError, CaptureFailedError, OSError, ValueError) as error:
        return f"## Spend\n\nNot computed: {error}\n"
    # A SPLIT COMPUTED FROM RECORDS NOBODY READ IS NOT A SPLIT. `read_team_shares` attributes
    # spend through the attempt records, so with that prefix refused it returns every team at
    # nothing rather than raising, and the section would report a real month-to-date divided
    # among teams that all look idle. Recomputing without the split is cheap: the account
    # total is already in hand and the second call re-renders it.
    if not substrate.attempts_read and shares is not None:
        markdown, _, _ = spend_section(
            today=day,
            limits_path=options.limits,
            lineage_root=None,
            lineage_bucket=options.lineage_bucket,
            config_dir=options.config_dir,
            profile=options.profile,
            region=options.region,
        )
    return markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--day",
        type=date.fromisoformat,
        default=None,
        help="the day to report, in UTC. Defaults to today",
    )
    parser.add_argument(
        "--reading",
        type=Path,
        default=None,
        help=(
            "a substrate document tools/read_substrate.py --write produced. Preferred over "
            "collecting a second one, so that this page and that reading cannot disagree"
        ),
    )
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    parser.add_argument("--limits", type=Path, default=SPEND_LIMITS_PATH)
    parser.add_argument(
        "--lineage-root",
        type=Path,
        default=None,
        help="a directory already holding intent/ and attempt/ records, rather than syncing",
    )
    parser.add_argument("--lineage-bucket", default=DEFAULT_LINEAGE_BUCKET)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "activity")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="skip every AWS read, for exercising the assembly against local records",
    )
    # No default profile, for the reason tools/visibility_board.py gives beside its own: a
    # scheduled run assumes a role and passes none, and a default would send it looking for
    # an SSO session that is not there.
    parser.add_argument("--profile", default=None)
    parser.add_argument("--region", default="us-east-1")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    day = options.day or datetime.now(UTC).date()
    try:
        inventory = load_yaml(options.config_dir / "organization.yaml", OrganizationInventory)
        catalog = load_yaml(options.config_dir / "workload-catalog.yaml", WorkloadCatalog)
    except (OSError, ValueError) as error:
        print(f"activity_config_not_read: {error}", file=sys.stderr)
        return EXIT_UNUSABLE

    # ONE COLLECTION, HELD OPEN ACROSS EVERY READER. Where this collects rather than reading a
    # reading, the collector stages the lineage records under a known directory and the spend
    # section is handed the same one, because the store is fifteen thousand objects and paying
    # for the bytes twice at 05:00 buys nothing.
    with tempfile.TemporaryDirectory() as scratch:
        staged: Path | None = Path(scratch) / STAGED_LINEAGE
        try:
            if options.reading is not None:
                substrate = load_reading(options.reading)
                staged = options.lineage_root
            else:
                substrate = collect(
                    scratch=Path(scratch),
                    compute_profiles=catalog.compute_profiles,
                    lineage_root=options.lineage_root,
                    lineage_bucket=options.lineage_bucket,
                    until=day,
                    profile=options.profile,
                    region=options.region,
                    offline=options.offline,
                )
        except (ReportInputError, CaptureFailedError, OSError, ValueError) as error:
            print(f"activity_substrate_not_read: {error}", file=sys.stderr)
            print(
                "No document was written. An activity with no runs and a lineage store "
                "nobody could read are the same page and only one of them is true.",
                file=sys.stderr,
            )
            return EXIT_UNUSABLE

        substrate = restrict_to_the_day(substrate, day)
        spend_markdown = _spend_markdown(
            day=day, substrate=substrate, options=options, lineage_root=staged
        )
        document = render_document(
            day=day, substrate=substrate, inventory=inventory, spend_markdown=spend_markdown
        )

    options.output_dir.mkdir(parents=True, exist_ok=True)
    written = options.output_dir / f"{day:%Y-%m-%d}.md"
    written.write_text(document, encoding="utf-8")
    print(f"{written}")
    for gap in substrate.gaps:
        print(f"  not read: {gap.source} — {gap.unanswered}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
