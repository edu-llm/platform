"""The tool that assembles the activity page, exercised without touching the account.

Every AWS read is a local directory or a committed reading, because the point of these tests is
the assembly and the exit codes. The three computations have their own tests.

**THE EXIT CODES ARE THE CONTRACT.** 0 a document was written, 2 nothing could be, and there is
no 1. A red job in a path something else depends on is one step away from being a control, and
`src/edullm_platform/spend.py` records that nothing here may stop a run. A mismatch is not a
failure of this tool and neither is a month projected over the limit.

**THE HAZARD THIS FILE EXISTS FOR IS THE WRONG WINDOW.** The lineage store is read whole, so any
reading answers for any day's runs, and it is tempting to treat a reading the same way about its
launch feed. It is not the same: the collector reads one day of CloudTrail, so joining the
sixth's launches against the fifth's runs produces a denominator from a window nobody asked
about. That is worse than no list, because it is a list.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from report_activity import (
    EXIT_OK,
    EXIT_UNUSABLE,
    build_parser,
    main,
    mismatch_report,
    render_document,
    restrict_to_the_day,
)

from edullm_platform.config import load_yaml
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.substrate import (
    SOURCE_EMPTY,
    SOURCE_NOT_READ,
    SOURCE_READ,
    AttemptFacts,
    LaunchEvent,
    RunFacts,
    SourceGap,
    Substrate,
    as_document,
)

#: A committed lineage fixture holding both `intent/` and `attempt/`. Its one attempt started on
#: 2026-07-28, which is why the days below are that and not today's.
RECORDS = (
    PROJECT_ROOT / "fixtures/evidence/phase-3/runs/run_019fa96f-8f10-705a-a7a9-69c42eafce16/records"
)
FIXTURE_DAY = "2026-07-28"
FIXTURE_RUN = "run_019fa96f-8f10-705a-a7a9-69c42eafce16"

DAY = date(2026, 8, 5)
RUN_A = "run_019fa73d-be37-7066-984b-a4bacf194f49"

#: A role the committed identity table binds to nobody, so its launches land in the unresolved
#: bucket and are counted rather than dropped.
UNBOUND_ROLE = "AWSServiceRoleForAutoScaling"


@pytest.fixture(scope="module")
def inventory() -> OrganizationInventory:
    return load_yaml(PROJECT_ROOT / "config/organization.yaml", OrganizationInventory)


def _facts(run_id: str, submitter: str, usd: str | None, *, hour: int = 9) -> RunFacts:
    started = datetime(DAY.year, DAY.month, DAY.day, hour, tzinfo=UTC)
    return RunFacts(
        run_id=run_id,
        submitter=submitter,
        team="pre-training",
        experiment="mixlaw-370m",
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
        workflow_run_url="https://github.com/edu-llm/platform/actions/runs/19407766",
        attempts=(
            AttemptFacts(
                attempt_id="att_019fa974-10b2-74b7-86dd-0c93bc5cd76c",
                ordinal=1,
                started_at=started,
                ended_at=datetime(DAY.year, DAY.month, DAY.day, hour + 1, tzinfo=UTC),
                terminal_state="succeeded",
            ),
        ),
        state="succeeded",
        state_source="attempt",
        seconds=Decimal(3600),
        cost_usd=None if usd is None else Decimal(usd),
        unpriced_reason=None if usd is not None else "a spot profile is not priced",
    )


def _launch(hour: int, *, day: date = DAY, run_id: str | None = None) -> LaunchEvent:
    return LaunchEvent(
        event_id=f"b1e2c3d4-0000-4000-8000-a1b2c3d4e5{hour:02d}",
        event_name="RunInstances",
        occurred_at=datetime(day.year, day.month, day.day, hour, tzinfo=UTC),
        role_name=UNBOUND_ROLE,
        run_id=run_id,
    )


def _substrate(
    *facts: RunFacts,
    collected_on: date = DAY,
    launches: tuple[LaunchEvent, ...] | None = (),
    gaps: tuple[SourceGap, ...] = (),
) -> Substrate:
    return Substrate(
        collected_at=datetime(collected_on.year, collected_on.month, collected_on.day, 5, 0,
                              tzinfo=UTC),
        runs={one.run_id: one for one in facts},
        launches=launches,
        source_outcomes={
            "attempt": SOURCE_READ,
            "experiment": SOURCE_READ,
            "launch": SOURCE_NOT_READ
            if launches is None
            else (SOURCE_EMPTY if len(launches) == 0 else SOURCE_READ),
            "live": SOURCE_NOT_READ,
        },
        gaps=gaps,
    )


def test_the_day_defaults_to_today_in_utc() -> None:
    """Mutation: default to the local date.

    The runner is UTC and a laptop is not, so a local default makes the same command produce
    two different days and one of them silently empty.
    """
    assert build_parser().parse_args([]).day is None


def test_a_named_day_is_parsed_as_a_date() -> None:
    """Mutation: keep --day as a string, which compares false against every record."""
    assert build_parser().parse_args(["--day", "2026-08-04"]).day == date(2026, 8, 4)


def test_there_is_no_exit_code_one() -> None:
    """Mutation: return 1 when a mismatch is found, or when the month is over the limit.

    `tools/report_spend.py` carries this as a rule and the reason is that a red job in a path
    something else depends on is one step away from being a control.
    """
    source = (PROJECT_ROOT / "tools/report_activity.py").read_text(encoding="utf-8")
    assert "EXIT_UNUSABLE = 2" in source
    assert "return 1" not in source
    assert "EXIT_DISAGREES" not in source


def test_a_reading_from_another_day_may_not_supply_the_mismatch_arm(
    inventory: OrganizationInventory,
) -> None:
    """Mutation: use every reading's launch feed whatever day it was taken on.

    THIS IS THE ONE THAT PRODUCES A WRONG ANSWER RATHER THAN A MISSING ONE, and it is easy to
    write: the lineage records are the whole store, so a reading from any day answers for any
    day's runs, and the launch feed looks like it should behave the same. It does not. The
    collector reads one day of CloudTrail, so the sixth's feed joined against the fifth's runs
    would report the sixth's launches as unaccounted for by the fifth.
    """
    on_the_sixth = _substrate(
        _facts(RUN_A, "alsy7009", "12.00"),
        collected_on=date(2026, 8, 6),
        launches=(_launch(1, day=date(2026, 8, 6)), _launch(2, day=date(2026, 8, 6))),
    )
    assert mismatch_report(substrate=on_the_sixth, inventory=inventory) is not None

    restricted = restrict_to_the_day(on_the_sixth, DAY)
    assert restricted.launches is None
    assert restricted.outcome("launch") == SOURCE_NOT_READ
    assert mismatch_report(substrate=restricted, inventory=inventory) is None
    assert [gap.source for gap in restricted.gaps] == ["cloudtrail:LookupEvents"]
    assert "2026-08-06" in restricted.gaps[0].reason
    assert "2026-08-05" in restricted.gaps[0].reason


def test_a_reading_taken_on_the_day_keeps_its_launch_feed() -> None:
    """Mutation: refuse every launch feed, which is safe and reports nothing forever.

    The guard above is only worth having if it lets the right reading through. A tool that
    never trusts a feed produces the same page every morning and the mismatch list is never
    built at all.
    """
    on_the_day = _substrate(_facts(RUN_A, "alsy7009", "12.00"), launches=(_launch(1),))
    assert restrict_to_the_day(on_the_day, DAY) is on_the_day


def test_an_unread_launch_feed_is_a_section_and_not_an_empty_list(
    inventory: OrganizationInventory,
) -> None:
    """Mutation: render the mismatch section from a report computed over no launches.

    `compute_mismatches(())` returns a perfectly well-formed report that says zero mismatches
    out of zero events examined, which is a true sentence about a feed that was read and a
    false one about a feed nobody read. The document has to branch before it computes.
    """
    unread = _substrate(_facts(RUN_A, "alsy7009", "12.00"), launches=None)
    page = render_document(day=DAY, substrate=unread, inventory=inventory, spend_markdown="")
    assert "Not computed, and that is not the same as none found." in page
    assert "0 launch events examined" not in page


def test_a_feed_that_was_read_produces_the_list_and_its_denominator(
    inventory: OrganizationInventory,
) -> None:
    """Mutation: treat an empty feed as an unread one, which loses a finding.

    A feed that answered and held nothing is a claim about the account. The denominator is
    what carries it, and it has to appear on the page for the section above to mean anything.
    """
    empty = _substrate(_facts(RUN_A, "alsy7009", "12.00"), launches=())
    page = render_document(day=DAY, substrate=empty, inventory=inventory, spend_markdown="")
    assert "0 launch events examined" in page
    assert "Not computed, and that is not the same as none found." not in page


def test_a_launch_by_an_unbound_role_is_counted_rather_than_dropped(
    inventory: OrganizationInventory,
) -> None:
    """Mutation: filter the launches down to the bound roles before counting them.

    A launch by a role the identity table does not carry produces no mismatch, and a report
    that also does not count it is one whose denominator shrinks with the thing it is meant to
    measure. The autoscaler is bound to nobody and launches most of this account's capacity.
    """
    substrate = _substrate(
        _facts(RUN_A, "alsy7009", "12.00"), launches=(_launch(1), _launch(2), _launch(3))
    )
    report = mismatch_report(substrate=substrate, inventory=inventory)
    assert report is not None
    assert report.events_examined == 3
    assert report.unresolved_launches == 3
    assert report.adds_up is True
    assert report.is_clean is False


def test_every_source_that_did_not_answer_is_on_the_page(
    inventory: OrganizationInventory,
) -> None:
    """Mutation: log the gaps to stderr instead of writing them onto the page.

    The runner's log is not where the reader is. A page carrying a table of runs and no note
    that the launch feed was never read is a page that reads as complete, which is the same
    failure as a mismatch count printed without its denominator.
    """
    substrate = _substrate(
        _facts(RUN_A, "alsy7009", "12.00"),
        launches=None,
        gaps=(
            SourceGap(
                source="batch:DescribeJobs",
                reason="the reading role holds no batch action",
                unanswered="no run can be reported as running rather than finished",
            ),
        ),
    )
    page = render_document(day=DAY, substrate=substrate, inventory=inventory, spend_markdown="")
    assert "## What was not read" in page
    assert "batch:DescribeJobs" in page


def test_a_committed_reading_is_read_rather_than_the_account(
    tmp_path: Path, inventory: OrganizationInventory
) -> None:
    """Mutation: collect a second time even when a reading was handed over.

    The point of the substrate being one pipeline is that the page and the reading beside it
    cannot disagree about a run. A tool that re-reads the account produces a second ingestion
    and eventually two dollar figures for one thing.
    """
    reading = tmp_path / "2026-08-05.json"
    reading.write_text(
        json.dumps(as_document(_substrate(_facts(RUN_A, "alsy7009", "12.00"), launches=()))),
        encoding="utf-8",
    )
    out = tmp_path / "activity"
    code = main(
        ["--day", "2026-08-05", "--reading", str(reading), "--output-dir", str(out), "--offline"]
    )
    page = (out / "2026-08-05.md").read_text(encoding="utf-8")
    assert code == EXIT_OK
    assert RUN_A in page
    assert "$12.00" in page


def test_an_unreadable_lineage_root_exits_unusable_rather_than_writing_a_quiet_page(
    tmp_path: Path,
) -> None:
    """Mutation: catch the error and report an empty day.

    An empty activity and a lineage store nobody could read are the same document and only
    one of them is true. This is the mismatch denominator's distinction, one level up.
    """
    out = tmp_path / "activity"
    code = main(
        [
            "--day", "2026-08-04",
            "--lineage-root", str(tmp_path / "nothing-here"),
            "--output-dir", str(out),
            "--offline",
        ]
    )
    assert code == EXIT_UNUSABLE
    assert not (out / "2026-08-04.md").exists()


def test_the_document_is_written_under_the_day_it_describes(tmp_path: Path) -> None:
    """Mutation: write one file and overwrite it, losing the history the overview asks for."""
    out = tmp_path / "activity"
    main(["--day", FIXTURE_DAY, "--lineage-root", str(RECORDS), "--output-dir", str(out),
          "--offline"])
    main(["--day", "2026-08-04", "--lineage-root", str(RECORDS), "--output-dir", str(out),
          "--offline"])
    on_the_day = (out / f"{FIXTURE_DAY}.md").read_text(encoding="utf-8")
    on_another = (out / "2026-08-04.md").read_text(encoding="utf-8")
    assert FIXTURE_RUN in on_the_day
    assert "Nothing ran" in on_another


@pytest.mark.slow
def test_the_tool_runs_from_the_command_line(tmp_path: Path) -> None:
    """Mutation: drop the __main__ guard, or leave an import that only resolves under pytest.

    Every other caller of this file is a workflow step rather than a test, and a module that
    imports cleanly and cannot be executed exits 0 having written nothing -- which is a green
    audit job and no page. So the file is asserted rather than the exit code alone.
    """
    completed = subprocess.run(
        [
            sys.executable, str(PROJECT_ROOT / "tools/report_activity.py"),
            "--day", FIXTURE_DAY,
            "--lineage-root", str(RECORDS),
            "--output-dir", str(tmp_path),
            "--offline",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == EXIT_OK, completed.stderr
    assert (tmp_path / f"{FIXTURE_DAY}.md").exists()
