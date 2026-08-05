"""The collector, exercised against local records rather than against the account.

**THE POINT OF THESE TESTS IS THAT ONE REFUSED SOURCE COSTS ONE COLUMN.** A pipeline that treats
every read as required goes dark over a grant; a pipeline that treats every read as optional
publishes an empty page and calls it a quiet day. The intent records are required and everything
else degrades with its reason named, and each of those is asserted below.

**AND THAT A SOURCE NOBODY COULD READ IS NEVER A SOURCE THAT HELD NOTHING.** The collector is
where the three outcomes are decided, because it is the only thing that knows whether a call was
made and what it answered. Three tests hold the launch feed to all three, and one holds the
collector to naming every refusal so that no reader has to infer one from a missing column.

Nothing here reaches AWS. Where an account call is unavoidable to the shape of the test, ``aws``
is replaced and the replacement records what it was asked for.
"""

from __future__ import annotations

import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import read_substrate
import visibility_board
from read_substrate import BATCH_GAP, collect, stage_prefixes
from report_run_costs import ReportInputError

from edullm_platform.config import load_yaml
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.evidence import AWS_ACCOUNT_ID_PLACEHOLDER
from edullm_platform.substrate import (
    ATTEMPTS_NOT_READ,
    SOURCE_EMPTY,
    SOURCE_NOT_READ,
    SOURCE_READ,
    SOURCES,
    LaunchEvent,
    SourceGap,
)

#: A committed lineage fixture holding both `intent/` and `attempt/`. Its one attempt started on
#: 2026-07-28, which is where the priced run below comes from.
RECORDS = (
    PROJECT_ROOT / "fixtures/evidence/phase-3/runs/run_019fa96f-8f10-705a-a7a9-69c42eafce16/records"
)
THE_RUN = "run_019fa96f-8f10-705a-a7a9-69c42eafce16"

#: Which gap explains which source, written out by hand rather than read off the collector.
#: A source added to the substrate with no entry here fails the first assertion of
#: `test_every_source_that_could_not_be_read_is_named_in_the_gaps`, which is the point: a
#: source that can be refused and cannot say so is the defect this module is arranged around.
GAP_FOR_SOURCE = {
    "attempt": "s3://.../attempt/",
    "experiment": "tag:GetResources",
    "launch": "cloudtrail:LookupEvents",
    "live": "batch:DescribeJobs",
}

#: The only AWS calls this collector is allowed to make, as (service, operation). Everything
#: here reads; nothing here writes. Asserted against rather than described, because "the
#: instruments never launch anything and never write to a bucket" is a property somebody has
#: to be able to check without reading the code.
READ_ONLY_CALLS = {("s3", "sync"), ("resourcegroupstaggingapi", "get-resources")}


@pytest.fixture(scope="module")
def profiles() -> tuple[object, ...]:
    catalog = load_yaml(PROJECT_ROOT / "config/workload-catalog.yaml", WorkloadCatalog)
    return tuple(catalog.compute_profiles)


class _Completed:
    """What `aws` hands back, reduced to the three fields this collector reads."""

    def __init__(self, returncode: int = 0, stdout: str = "{}", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _recorder(calls: list[list[str]], *, fails: str | None = None, stderr: str = "") -> Any:
    """A stand-in for `aws` that records every call and can refuse one named prefix."""

    def call(arguments: Any, *, profile: Any = None, region: Any = None) -> Any:
        recorded = [str(argument) for argument in arguments]
        calls.append(recorded)
        if fails is not None and any(fails in argument for argument in recorded):
            return _Completed(returncode=255, stderr=stderr)
        return _Completed()

    return call


def _no_experiments(**_: Any) -> tuple[None, SourceGap]:
    """The tag read, refused. Its own gap, so the collector's invariant still holds."""
    return None, SourceGap(
        source="tag:GetResources",
        reason="stubbed out by a test",
        unanswered="no run can be attributed to an experiment",
    )


def test_a_local_root_is_read_without_reaching_the_account(
    tmp_path: Path, profiles: tuple[object, ...]
) -> None:
    """Mutation: always sync, which turns every test into a network call."""
    substrate = collect(
        scratch=tmp_path,
        compute_profiles=profiles,
        lineage_root=RECORDS,
        offline=True,
    )
    assert THE_RUN in substrate.runs
    assert substrate.attempts_read is True
    assert substrate.runs[THE_RUN].cost_usd is not None


def test_a_refused_attempt_prefix_costs_the_figures_and_nothing_else(
    tmp_path: Path, profiles: tuple[object, ...]
) -> None:
    """Mutation: raise when attempt/ is absent, or report the runs as never having started.

    Raising would leave the whole surface dark over one grant; reporting the runs as never
    started would be a false claim about every run in the store. The run is reported, with no
    figure, and the reason names the prefix.
    """
    partial = tmp_path / "partial"
    (partial / "intent").mkdir(parents=True)
    for record in (RECORDS / "intent").glob("*.json"):
        shutil.copy(record, partial / "intent" / record.name)

    substrate = collect(
        scratch=tmp_path / "scratch",
        compute_profiles=profiles,
        lineage_root=partial,
        offline=True,
    )
    assert substrate.attempts_read is False
    assert substrate.outcome("attempt") == SOURCE_NOT_READ
    assert THE_RUN in substrate.runs
    assert substrate.runs[THE_RUN].unpriced_reason == ATTEMPTS_NOT_READ
    assert substrate.runs[THE_RUN].state_source == "unread"
    named = [gap.source for gap in substrate.gaps]
    assert "s3://.../attempt/" in named


def test_a_refused_intent_prefix_is_fatal_rather_than_an_empty_page(
    tmp_path: Path, profiles: tuple[object, ...]
) -> None:
    """Mutation: degrade the intent read the way the attempt read degrades.

    An activity with no runs and a lineage store nobody could read are the same page and only
    one of them is true. There is nothing to publish without the intents, so this is the one
    source that stops the pipeline.
    """
    with pytest.raises(ReportInputError):
        collect(
            scratch=tmp_path / "scratch",
            compute_profiles=profiles,
            lineage_root=tmp_path / "nothing-here",
            offline=True,
        )


def test_batch_is_named_as_unread_rather_than_left_to_be_inferred(
    tmp_path: Path, profiles: tuple[object, ...]
) -> None:
    """Mutation: skip the Batch read silently because this slice does not need it.

    The slice that publishes the per-run snapshot does need it, and a reader of this substrate
    has no other way to learn that no run here can be reported as running. The gap says which
    grant and which question, so the next slice does not rediscover both.
    """
    substrate = collect(
        scratch=tmp_path,
        compute_profiles=profiles,
        lineage_root=RECORDS,
        offline=True,
    )
    assert BATCH_GAP in substrate.gaps
    assert "running" in BATCH_GAP.unanswered
    assert substrate.outcome("live") == SOURCE_NOT_READ
    assert all(facts.state_source != "live" for facts in substrate.runs.values())


def test_offline_leaves_the_launch_feed_unread_rather_than_empty(
    tmp_path: Path, profiles: tuple[object, ...]
) -> None:
    """Mutation: return an empty launch tuple when the read is skipped.

    An empty feed says nothing launched in the account, which is a finding. Offline is the
    absence of a reading, and the mismatch list downstream turns exactly this distinction into
    the difference between a clean day and a broken one.
    """
    substrate = collect(
        scratch=tmp_path,
        compute_profiles=profiles,
        lineage_root=RECORDS,
        offline=True,
    )
    assert substrate.launches is None
    assert substrate.outcome("launch") == SOURCE_NOT_READ
    assert "cloudtrail:LookupEvents" in {gap.source for gap in substrate.gaps}


def test_every_prefix_exists_before_anything_writes_to_one(tmp_path: Path) -> None:
    """Mutation: create a prefix directory only when its sync succeeds.

    Then an absent directory would mean either "refused" or "synced and empty", and the two
    are the difference between a finding and a fact. Existence is not allowed to carry the
    meaning; the sync's exit status is.
    """
    staged = stage_prefixes(tmp_path / "lineage", ("intent", "attempt"))
    assert (staged / "intent").is_dir()
    assert (staged / "attempt").is_dir()


def test_every_source_that_could_not_be_read_is_named_in_the_gaps(
    tmp_path: Path, profiles: tuple[object, ...]
) -> None:
    """Mutation: report one source as unread without adding its gap.

    THE GAP IS THE ONLY THING A READER SEES. A source whose outcome says "not read" and whose
    absence is nowhere in the printed list is a hole the reader is left to notice from a blank
    column, which is the failure this collector exists to end.
    """
    assert set(GAP_FOR_SOURCE) == set(SOURCES)
    substrate = collect(
        scratch=tmp_path,
        compute_profiles=profiles,
        lineage_root=RECORDS,
        offline=True,
    )
    named = {gap.source for gap in substrate.gaps}
    unread = [source for source in SOURCES if substrate.outcome(source) == SOURCE_NOT_READ]
    assert unread == ["experiment", "launch", "live"]
    for source in unread:
        assert GAP_FOR_SOURCE[source] in named, source


def _lineage_holding(tmp_path: Path, *, extra: str | None = None) -> Path:
    """The committed fixture's records, optionally with one more intent document beside them."""
    root = tmp_path / "records"
    for prefix in ("intent", "attempt"):
        (root / prefix).mkdir(parents=True)
        for record in (RECORDS / prefix).rglob("*.json"):
            shutil.copy(record, root / prefix / record.name)
    if extra is not None:
        (root / "intent" / "refused.json").write_text(extra, encoding="utf-8")
    return root


def test_a_record_the_contracts_refuse_is_counted_rather_than_dropped(
    tmp_path: Path, profiles: tuple[object, ...]
) -> None:
    """Mutation: discard read_records' unparsed tally, which is what `_` did.

    A record this tree cannot parse is a run in neither the table nor the unread sources, so
    dropping the count puts it in no count at all. The lineage store holds one: an intent whose
    manifest command was written as a shell line rather than as arguments, refused by a
    validator added after it was sealed.
    """
    root = _lineage_holding(tmp_path, extra='{"schema_version": 1, "run_id": "nonsense"}')
    substrate = collect(
        scratch=tmp_path / "scratch",
        compute_profiles=profiles,
        lineage_root=root,
        offline=True,
    )
    gap = next(
        gap for gap in substrate.gaps if gap.source == "lineage records the contracts refuse"
    )
    assert "1 record(s)" in gap.reason
    assert THE_RUN in substrate.runs


def test_records_that_all_parse_leave_no_refusal_behind(
    tmp_path: Path, profiles: tuple[object, ...]
) -> None:
    """Mutation: file the refusal gap unconditionally.

    A gap that is always there is a warning nobody reads, and it would say a store was partly
    unreadable on every morning it was completely fine. This is the half that stops the test
    above passing for the wrong reason.
    """
    substrate = collect(
        scratch=tmp_path / "scratch",
        compute_profiles=profiles,
        lineage_root=_lineage_holding(tmp_path),
        offline=True,
    )
    assert substrate.runs[THE_RUN].cost_usd is not None, "the copied attempt was not read"
    assert "lineage records the contracts refuse" not in {gap.source for gap in substrate.gaps}


def test_a_launch_feed_that_answered_and_found_nothing_carries_no_gap(
    tmp_path: Path, profiles: tuple[object, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: add the launch gap whenever the feed is empty.

    CloudTrail answering that nothing launched is a finding about the account and the cleanest
    morning there is. Filing a gap for it would tell the reader to distrust a figure that is
    correct, and after a few of those nobody reads the gap list at all.
    """
    monkeypatch.setattr(read_substrate, "_launch_reader", lambda: lambda **_: ())
    monkeypatch.setattr(read_substrate, "read_experiments", _no_experiments)
    substrate = collect(scratch=tmp_path, compute_profiles=profiles, lineage_root=RECORDS)
    assert substrate.launches == ()
    assert substrate.outcome("launch") == SOURCE_EMPTY
    assert "cloudtrail:LookupEvents" not in {gap.source for gap in substrate.gaps}


def test_a_launch_reader_that_is_not_built_is_refused_in_its_own_words(
    tmp_path: Path, profiles: tuple[object, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: report the missing reader with the same reason as a refused call.

    Both are "not read" and only one of them is fixed by a deploy. A reader told the launch
    feed was refused goes looking at the role; the truth today is that nothing has been built
    that could call it, and the reason has to say which.
    """
    monkeypatch.setattr(read_substrate, "_launch_reader", lambda: None)
    monkeypatch.setattr(read_substrate, "read_experiments", _no_experiments)
    substrate = collect(scratch=tmp_path, compute_profiles=profiles, lineage_root=RECORDS)
    gap = next(gap for gap in substrate.gaps if gap.source == "cloudtrail:LookupEvents")
    assert substrate.launches is None
    assert "read_launch_events" in gap.reason


def test_a_launch_feed_that_was_read_is_carried_whole(
    tmp_path: Path, profiles: tuple[object, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: keep only the launches with no run id, or drop the feed on the way through.

    The mismatch denominator downstream counts every launch examined, so a feed narrowed here
    would make the denominator equal the numerator.
    """
    launch = LaunchEvent(
        event_id="44444444-4444-4444-4444-44444444444d",
        event_name="SubmitJob",
        occurred_at=datetime(2026, 8, 4, 9, tzinfo=UTC),
        role_name="sbsandbox-intern-edullm-run",
        run_id=THE_RUN,
    )
    monkeypatch.setattr(read_substrate, "_launch_reader", lambda: lambda **_: (launch,))
    monkeypatch.setattr(read_substrate, "read_experiments", _no_experiments)
    substrate = collect(scratch=tmp_path, compute_profiles=profiles, lineage_root=RECORDS)
    assert substrate.launches == (launch,)
    assert substrate.outcome("launch") == SOURCE_READ


def test_offline_reaches_the_account_for_nothing_at_all(
    tmp_path: Path, profiles: tuple[object, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: let offline skip the tag and launch reads but sync the bucket anyway.

    The flag says every network read and has to mean it, because what it protects is a test
    suite that would otherwise silently start costing money and needing credentials.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(read_substrate, "aws", _recorder(calls))
    monkeypatch.setattr(visibility_board, "aws", _recorder(calls))
    collect(
        scratch=tmp_path,
        compute_profiles=profiles,
        lineage_root=RECORDS,
        offline=True,
    )
    assert calls == []


def test_offline_with_no_local_records_is_refused_rather_than_synced(
    tmp_path: Path, profiles: tuple[object, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: fall through to the sync when offline is set and no root is given.

    Offline with nothing to read is a caller mistake, and reaching the account is the one
    thing it must not resolve into.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(read_substrate, "aws", _recorder(calls))
    with pytest.raises(ReportInputError, match="offline"):
        collect(scratch=tmp_path, compute_profiles=profiles, offline=True)
    assert calls == []


def test_the_collector_only_ever_reads(
    tmp_path: Path, profiles: tuple[object, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: add a write, or sync the scratch directory back up to the bucket.

    THE INSTRUMENTS OBSERVE AND CHANGE NOTHING. `aws s3 sync` is directional and the direction
    is an argument, so the same call that fills the scratch directory would, with its two
    arguments swapped, overwrite the lineage store from it. The source is asserted to be the
    bucket and the destination a path.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(read_substrate, "aws", _recorder(calls))
    monkeypatch.setattr(visibility_board, "aws", _recorder(calls))
    monkeypatch.setattr(read_substrate, "_launch_reader", lambda: None)
    collect(scratch=tmp_path, compute_profiles=profiles)

    assert calls, "the collector made no call at all, so this asserts nothing"
    assert ["s3", "sync"] in [command[:2] for command in calls]
    assert ["resourcegroupstaggingapi", "get-resources"] in [command[:2] for command in calls]
    for command in calls:
        assert (command[0], command[1]) in READ_ONLY_CALLS, command
        if command[:2] == ["s3", "sync"]:
            assert command[2].startswith("s3://"), command
            assert not command[3].startswith("s3://"), command


def test_nothing_printed_carries_an_account_id(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print the sync's stderr through unmasked.

    A denial names the caller, and the caller is an assumed-role ARN carrying the account id.
    This report is read in a step summary that anybody with the repository can open.

    **The id below is fabricated, and spelled rather than written.** A test that names the
    account id it is looking for puts the account id in a tracked file, which is the
    disclosure it exists to prevent. `tests/test_pilot_limitations.py` made that mistake on
    2026-07-29 and says so in its own docstring. The masking under test keys off the shape of
    an ARN and not off any particular value, so a fabricated id exercises it exactly.
    """
    fabricated = "9" * 12
    denial = (
        "An error occurred (AccessDenied) when calling the ListObjectsV2 operation: User: "
        f"arn:aws:sts::{fabricated}:assumed-role/sbsandbox-intern-edullm-audit-reader/x "
        "is not authorized to perform: s3:ListBucket"
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(read_substrate, "aws", _recorder(calls, fails="attempt", stderr=denial))
    monkeypatch.setattr(read_substrate, "read_experiments", _no_experiments)
    monkeypatch.setattr(read_substrate, "_launch_reader", lambda: None)

    assert read_substrate.main(["--region", "us-east-1"]) == 0

    printed = capsys.readouterr()
    assert fabricated not in printed.out + printed.err
    assert AWS_ACCOUNT_ID_PLACEHOLDER in printed.out


def test_the_report_names_every_source_and_what_each_one_did(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: print only the gaps, leaving a read source indistinguishable from an absent one.

    A list of what went wrong is not a list of what was read. An operator checking whether a
    deploy landed needs to see the sources that answered, or the absence of a line is the only
    evidence -- and absence is the one thing this module refuses to let carry meaning.
    """
    assert read_substrate.main(["--lineage-root", str(RECORDS), "--offline"]) == 0
    printed = capsys.readouterr().out
    for source in SOURCES:
        assert f"{source}: " in printed, source
    assert SOURCE_READ in printed
    assert SOURCE_NOT_READ in printed
