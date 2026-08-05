"""Every account read the instruments need, in one place, degrading one source at a time.

**THIS IS THE ONLY MODULE THAT TOUCHES THE ACCOUNT ON THE INSTRUMENTS' BEHALF.** The daily file
and the per-run snapshot are two publications of one pipeline -- ``docs-frank`` records the split
under "The activity and the run index are one pipeline and two publications" -- and this is the
pipeline's mouth. Building either publication as its own reader would mean two ingestions of one
account, which eventually disagree about one run, and the disagreement would surface as two
dollar figures for the same thing.

**EVERY SOURCE DEGRADES ALONE, AND A SOURCE THAT WAS NOT READ IS NEVER REPORTED AS ONE THAT HELD
NOTHING.** A prefix nobody could list and a prefix holding nothing produce the same empty
directory, so existence is never allowed to carry meaning: each destination is created before its
sync runs, and a refusal is taken from the sync's exit status. That is the same rule
``tools/visibility_board.py`` states as "a category is not reported unless both sources it
compares were read", applied one layer down. Every refusal produces a
:class:`~edullm_platform.substrate.SourceGap` naming the source and the question it leaves
unanswered, and :meth:`~edullm_platform.substrate.Substrate.outcome` carries the same fact where
a renderer that never looks at the gaps still cannot miss it.

**THE ONLY REQUIRED SOURCE IS ``intent/``.** An activity with no runs and a lineage store nobody
could read are the same page and only one of them is true, so that read raises. Everything else
costs a column.

**A RECORD THE CONTRACTS REFUSE IS THE SAME DISTINCTION ONE OBJECT DOWN.** ``read_records``
counts what would not parse, and that count becomes a gap rather than being discarded: a run
whose record this tree cannot read belongs in neither the table nor the list of unread sources,
so without the gap it is in no count anywhere. The lineage store held one such record on
2026-08-04.

**BATCH IS NOT READ HERE, ON PURPOSE.** A live state is what a status query wants and a daily
file cannot use: Batch forgets a job after about a week, so it can never be the day's source of
truth, and the grant belongs to the slice that publishes the snapshot. :data:`BATCH_GAP` carries
the absence so that nothing downstream has to infer it.

**THE LAUNCH FEED HAS NO READER YET.** ``tools/read_launch_events.py`` is a task of its own and
does not exist, and the nightly reader role holds no ``cloudtrail:LookupEvents``. Both are
reported as the launch feed not having been read, in the reader's own words, because a
substrate that reported an empty mismatch list would be describing an account nobody looked at.
Nothing here needs editing when that module lands: it is found by name.

Exit codes follow the repository's convention: 0 reported, 2 the inputs could not be read. There
is no 1, because this tool judges nothing and so has nothing to refuse.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final, Protocol, cast

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

from report_run_costs import LINEAGE_PREFIXES, ReportInputError, read_records
from report_spend import DEFAULT_LINEAGE_BUCKET
from visibility_board import read_tagged_resources

from edullm_platform.capture_tooling import CaptureFailedError, aws
from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import IntentRecord
from edullm_platform.contracts.lifecycle import SchedulerAttempt
from edullm_platform.contracts.workload import ComputeProfile, WorkloadCatalog
from edullm_platform.evidence import ACCOUNT_ID_IN_FREE_TEXT, AWS_ACCOUNT_ID_PLACEHOLDER
from edullm_platform.substrate import (
    SOURCES,
    LaunchEvent,
    SourceGap,
    Substrate,
    normalise,
)

__all__ = [
    "BATCH_GAP",
    "EXIT_OK",
    "EXIT_UNUSABLE",
    "LAUNCH_READER",
    "STAGED_LINEAGE",
    "build_parser",
    "collect",
    "main",
    "read_experiments",
    "read_launches",
    "read_lineage",
    "stage_prefixes",
]

EXIT_OK = 0
EXIT_UNUSABLE = 2

#: Where the collector presents the lineage records to every reader. Named rather than
#: recomputed, because ``tools/report_spend.py`` is handed the same directory and paying twice
#: for fifteen thousand objects at 05:00 is the cost of getting it wrong.
STAGED_LINEAGE = "lineage"

#: The module that knows CloudTrail's event shape, found by name rather than imported. It is a
#: separate task and does not exist yet; see :func:`_launch_reader`.
LAUNCH_READER = "read_launch_events"

#: The one source this slice deliberately does not read, carried as a value so that the absence
#: is printed rather than inferred from a missing column.
BATCH_GAP = SourceGap(
    source="batch:DescribeJobs",
    reason=(
        "the reading role holds no batch action, and the grant belongs to the slice that "
        "publishes the per-run snapshot rather than to this one"
    ),
    unanswered=(
        "no run can be reported as running or queued rather than finished. Batch also forgets "
        "a job after about a week, so it can never be the daily file's source of truth"
    ),
)

#: What each optional source leaves unanswered when it is not read. Held beside the sources
#: rather than written at each refusal, so that "offline" and "refused" and "not built" differ
#: in their reason and agree about the consequence.
UNANSWERED: Final[Mapping[str, str]] = {
    "tag:GetResources": "no run can be attributed to an experiment",
    "cloudtrail:LookupEvents": (
        "no mismatch list exists for this window, which is not the same as an empty one"
    ),
}


class LaunchReader(Protocol):
    """What ``tools/read_launch_events.py`` will expose, stated here so this module can type it."""

    def __call__(
        self, *, since: date, until: date, profile: str | None, region: str
    ) -> tuple[LaunchEvent, ...]: ...


def _masked(text: str) -> str:
    """Mask any account id, leaving content digests alone.

    ``edullm_platform.evidence.redact_aws_account_ids`` is the sanctioned mask and is not used
    here, for the reason ``tools/visibility_board.py`` gives beside its own copy of this
    function: that one raises on text also carrying another credential shape, which is right
    for a capture somebody is about to commit and wrong for a nightly report, where a traceback
    in place of a reading would report nothing at all on the one morning the account held
    something unexpected. The same expression is reused so the mask cannot be stepped around
    differently here than anywhere else.
    """
    return ACCOUNT_ID_IN_FREE_TEXT.sub(
        lambda found: AWS_ACCOUNT_ID_PLACEHOLDER if found.group("account") else found.group(0),
        text,
    )


def _launch_reader() -> LaunchReader | None:
    """The CloudTrail reader, if it has been built.

    Found by name rather than imported at module scope, and this is not indirection for its own
    sake. ``tools/read_launch_events.py`` is a task of its own and does not exist yet; an import
    of it would make this whole collector unimportable, which would take the runs, the costs and
    the join down with the one column that is genuinely missing. Absent, the launch feed reports
    itself as not read, in words that say the reader is missing rather than that the account
    refused -- two states that a deploy fixes only one of.
    """
    try:
        module = importlib.import_module(LAUNCH_READER)
    except ModuleNotFoundError:
        return None
    return cast("LaunchReader | None", getattr(module, LAUNCH_READER, None))


def stage_prefixes(destination: Path, prefixes: Iterable[str]) -> Path:
    """Create every prefix directory before anything writes to one.

    ``read_records`` raises when a prefix directory is absent, and an absent directory is what
    both a refused listing and an empty tree leave behind. Creating them all up front means the
    refusal is recorded from the sync's exit status, where it is unambiguous, instead of being
    guessed from the file system afterwards.
    """
    destination.mkdir(parents=True, exist_ok=True)
    for prefix in prefixes:
        (destination / prefix).mkdir(parents=True, exist_ok=True)
    return destination


def _sync_prefix(
    bucket: str, destination: Path, prefix: str, *, profile: str | None, region: str | None
) -> str | None:
    """Sync one prefix down, returning the reason it could not be read, or ``None``.

    One prefix per call rather than through ``report_run_costs.sync_bucket``, which raises on
    the first refusal by design and is right to for a report that needs both. This collector
    survives losing one, so it needs the exit status of each rather than of the pair.
    """
    completed = aws(
        ["s3", "sync", f"s3://{bucket}/{prefix}/", str(destination / prefix), "--quiet"],
        profile=profile,
        region=region,
    )
    if completed.returncode != 0:
        return _masked((completed.stderr or completed.stdout or "").strip()) or "the sync failed"
    return None


def _present(staged: Path, prefix: str, source: Path) -> None:
    """Point one staged prefix at a directory that already holds the records.

    The staged tree is what ``read_records`` is handed, so a local root has to appear under it.
    A symlink rather than a copy, because the phase-3 fixtures are the ordinary input and
    copying them per test is a cost paid for nothing. A staged directory that is not empty is
    left alone to fail loudly: two sources under one prefix is worse than either.
    """
    target = staged / prefix
    if target.is_symlink():
        target.unlink()
    else:
        target.rmdir()
    target.symlink_to(source, target_is_directory=True)


def read_lineage(
    *,
    root: Path | None,
    scratch: Path,
    bucket: str,
    profile: str | None,
    region: str | None,
) -> tuple[tuple[IntentRecord, ...], tuple[SchedulerAttempt, ...], bool, tuple[SourceGap, ...]]:
    """The intent and attempt records, plus whether the attempts were readable at all.

    ``intent/`` is required: an activity with no runs and a lineage store nobody could read are
    the same page and only one of them is true. ``attempt/`` is not, because refusing to report
    anything until a grant lands would leave the whole surface dark over one prefix -- and the
    grant has been missing before. ``infra/iam/nightly-reader-role.yaml`` is where it lives.
    """
    staged = stage_prefixes(scratch / STAGED_LINEAGE, LINEAGE_PREFIXES)
    refused: dict[str, str] = {}
    for prefix in LINEAGE_PREFIXES:
        if root is None:
            reason = _sync_prefix(bucket, staged, prefix, profile=profile, region=region)
            if reason is not None:
                refused[prefix] = reason
        elif (root / prefix).is_dir():
            _present(staged, prefix, root / prefix)
        else:
            refused[prefix] = f"no {prefix}/ directory under {root}"

    if "intent" in refused:
        raise ReportInputError(f"the intent records could not be read: {refused['intent']}")

    parsed_intents, parsed_attempts, unparsed = read_records(staged)
    attempts_read = "attempt" not in refused
    gaps: list[SourceGap] = []
    if unparsed:
        # A RECORD THAT WOULD NOT PARSE IS A RUN THAT IS IN NO COUNT AT ALL, which is the
        # third outcome one object down: read, and not understood. `read_records` returns
        # the tally and every earlier caller of it prints one; dropping it here would put a
        # run in neither the table nor the holes, and the store held exactly one on
        # 2026-08-04. The record is not wrong -- it was valid when it was sealed, and it is
        # immutable -- so what this reports is that a contract was tightened after it.
        gaps.append(
            SourceGap(
                source="lineage records the contracts refuse",
                reason=(
                    f"{unparsed} record(s) under intent/ or attempt/ did not parse against the "
                    "contracts in this tree"
                ),
                unanswered=(
                    "those runs appear in no count on this substrate, neither as runs nor as "
                    "anything unread; a record refused now was valid when it was written, so "
                    "what needs deciding is whether the rule that refuses it should tolerate "
                    "what came before it"
                ),
            )
        )
    if not attempts_read:
        gaps.append(
            SourceGap(
                source="s3://.../attempt/",
                reason=refused["attempt"],
                unanswered=(
                    "no run has a duration or a cost, and no run can be reported as having "
                    "failed to start. infra/iam/nightly-reader-role.yaml is where the grant goes"
                ),
            )
        )
    return tuple(parsed_intents), tuple(parsed_attempts), attempts_read, tuple(gaps)


def read_experiments(
    *, profile: str | None, region: str
) -> tuple[Mapping[str, str] | None, SourceGap | None]:
    """Run id to experiment, out of the Batch tags, with the reason if nobody could look.

    The experiment is not in the lineage records -- ``src/edullm_platform/submission.py`` keeps
    a grouping key out of a hashed manifest deliberately -- so the tag is the only place it
    survives. ``tag:GetResources`` is the grant.

    An empty mapping is returned where the read succeeded and nothing carried an experiment tag,
    and that is a different answer from ``None``: one says no run was labelled, the other says
    nobody could ask.
    """
    try:
        resources = read_tagged_resources(profile=profile, region=region)
    except CaptureFailedError as error:
        return None, _not_read("tag:GetResources", _masked(str(error)))
    return {
        resource.run_id: resource.experiment
        for resource in resources
        if resource.run_id and resource.experiment
    }, None


def read_launches(
    *, since: date, until: date, profile: str | None, region: str
) -> tuple[tuple[LaunchEvent, ...] | None, SourceGap | None]:
    """Every launch CloudTrail will report over the window, or why there is no feed.

    ``None`` rather than an empty tuple, because an empty launch feed is a finding -- nothing
    started in the account -- and an unread one is not. The two ways it goes unread are told
    apart in the reason: the reader is not built, or the call was refused.
    """
    reader = _launch_reader()
    if reader is None:
        return None, _not_read(
            "cloudtrail:LookupEvents",
            f"tools/{LAUNCH_READER}.py is not built, so nothing here can read the launch feed",
        )
    try:
        return reader(since=since, until=until, profile=profile, region=region), None
    except (CaptureFailedError, ValueError) as error:
        return None, _not_read("cloudtrail:LookupEvents", _masked(str(error)))


def _not_read(source: str, reason: str) -> SourceGap:
    """One refusal, with the question it leaves unanswered attached from :data:`UNANSWERED`."""
    return SourceGap(source=source, reason=reason, unanswered=UNANSWERED[source])


def collect(
    *,
    scratch: Path,
    compute_profiles: Iterable[ComputeProfile],
    lineage_root: Path | None = None,
    lineage_bucket: str = DEFAULT_LINEAGE_BUCKET,
    since: date | None = None,
    until: date | None = None,
    profile: str | None = None,
    region: str = "us-east-1",
    offline: bool = False,
    collected_at: datetime | None = None,
) -> Substrate:
    """One record per run, from whichever sources answered.

    ``offline`` skips every network read, which is how the assembly is exercised against local
    records, and it therefore requires ``lineage_root``: there is nothing to read otherwise and
    silently syncing would make a flag that promises no network calls make several. It is not a
    mode the nightly runs in -- it produces a substrate whose launch feed is unread, which
    downstream renders as a mismatch list that does not exist rather than an empty one.
    """
    if offline and lineage_root is None:
        raise ReportInputError("offline needs --lineage-root; there is nothing else to read")

    intents, attempts, attempts_read, gaps = read_lineage(
        root=lineage_root,
        scratch=scratch,
        bucket=lineage_bucket,
        profile=profile,
        region=region,
    )

    experiments: Mapping[str, str] | None = None
    launches: tuple[LaunchEvent, ...] | None = None
    every_gap = [*gaps, BATCH_GAP]

    if offline:
        every_gap.append(_not_read("tag:GetResources", "offline: the account was not read"))
        every_gap.append(_not_read("cloudtrail:LookupEvents", "offline: the account was not read"))
    else:
        experiments, tag_gap = read_experiments(profile=profile, region=region)
        window_end = until or datetime.now(UTC).date()
        launches, launch_gap = read_launches(
            since=since or window_end,
            until=date.fromordinal(window_end.toordinal() + 1),
            profile=profile,
            region=region,
        )
        every_gap += [gap for gap in (tag_gap, launch_gap) if gap is not None]

    return normalise(
        collected_at=collected_at or datetime.now(UTC),
        intents=intents,
        attempts=attempts,
        compute_profiles=compute_profiles,
        experiments=experiments,
        live_states=None,
        launches=launches,
        attempts_read=attempts_read,
        gaps=tuple(every_gap),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    parser.add_argument("--lineage-root", type=Path, default=None)
    parser.add_argument("--lineage-bucket", default=DEFAULT_LINEAGE_BUCKET)
    parser.add_argument("--day", type=date.fromisoformat, default=None)
    parser.add_argument("--offline", action="store_true")
    # No default profile, for the reason tools/visibility_board.py gives beside its own: a
    # scheduled run assumes a role and passes none, and a default would send it looking for an
    # SSO session that is not there.
    parser.add_argument("--profile", default=None)
    parser.add_argument("--region", default="us-east-1")
    return parser


def report(substrate: Substrate) -> list[str]:
    """What the pipeline read, in the order somebody checking a deploy wants it.

    Every source is named whether or not it answered. A report listing only what went wrong
    leaves a source that was read indistinguishable from one nobody thought to read, which is
    the same collapse this module refuses everywhere else.
    """
    priced = sum(1 for facts in substrate.runs.values() if facts.cost_usd is not None)
    joined = sum(1 for facts in substrate.runs.values() if facts.workflow_run_id is not None)
    launches = "not read" if substrate.launches is None else str(len(substrate.launches))
    lines = [
        f"{len(substrate.runs)} run(s), {priced} priced, {launches} launch event(s)",
        f"{joined} of {len(substrate.runs)} run(s) resolve the workflow-run join",
        "sources:",
    ]
    lines += [f"  {source}: {substrate.outcome(source)}" for source in SOURCES]
    if substrate.gaps:
        lines.append("what could not be read:")
        lines += [
            f"  {gap.source}: {gap.reason}\n    unanswered: {gap.unanswered}"
            for gap in substrate.gaps
        ]
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    """Print what the pipeline read, so that a refusal is visible before anything publishes it."""
    options = build_parser().parse_args(argv)
    with tempfile.TemporaryDirectory() as scratch:
        try:
            catalog = load_yaml(options.config_dir / "workload-catalog.yaml", WorkloadCatalog)
            substrate = collect(
                scratch=Path(scratch),
                compute_profiles=catalog.compute_profiles,
                lineage_root=options.lineage_root,
                lineage_bucket=options.lineage_bucket,
                until=options.day,
                profile=options.profile,
                region=options.region,
                offline=options.offline,
            )
        except (ReportInputError, CaptureFailedError, OSError, ValueError) as error:
            print(f"substrate_not_read: {_masked(str(error))}", file=sys.stderr)
            return EXIT_UNUSABLE
        print("\n".join(report(substrate)))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
