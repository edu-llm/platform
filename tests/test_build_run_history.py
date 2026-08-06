"""The wiring between the one account reader and the digest an install carries."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from edullm_platform.run_history import HISTORY_FILENAME, load_run_history
from edullm_platform.substrate import Substrate, as_document
from tests.test_run_history import Shape, a_run

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COLLECTED_AT = datetime(2026, 8, 5, 6, 0, tzinfo=UTC)


def build_run_history():  # type: ignore[no-untyped-def]
    """The tool, imported by path because ``tools/`` is not a package.

    The same route ``tests/test_read_substrate.py`` takes, for the same reason: these are
    scripts with a ``main``, and adding an ``__init__.py`` to make them importable would put
    them on the release trigger's import tree.
    """
    spec = importlib.util.spec_from_file_location(
        "build_run_history", PROJECT_ROOT / "tools" / "build_run_history.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_run_history"] = module
    spec.loader.exec_module(module)
    return module


def a_reading(path: Path, *, runs: tuple[object, ...]) -> Path:
    substrate = Substrate(
        collected_at=COLLECTED_AT,
        runs={run.run_id: run for run in runs},  # type: ignore[attr-defined]
        launches=None,
        source_outcomes={
            "attempt": "read",
            "experiment": "not read",
            "launch": "not read",
            "live": "not read",
        },
        gaps=(),
    )
    path.write_text(json.dumps(as_document(substrate), indent=2), encoding="utf-8")
    return path


def test_a_reading_becomes_a_digest_the_installed_cli_can_load(tmp_path: Path) -> None:
    """The whole path, end to end, with no account in it.

    Mutation: write the substrate document straight into ``config/`` rather than summarising
    it. The loader refuses it, because the two formats are different documents with
    different version numbers, and the failure would arrive on a researcher's laptop rather
    than here.
    """
    tool = build_run_history()
    reading = a_reading(
        tmp_path / "reading.json",
        runs=tuple(
            a_run(seconds=seconds, run_id=f"run_{index}")
            for index, seconds in enumerate((600, 1800, 5400))
        ),
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    exit_code = tool.main(["--reading", str(reading), "--config-dir", str(config_dir)])

    assert exit_code == tool.EXIT_OK
    history = load_run_history(config_dir)
    assert history is not None
    assert history.runs_read == 3
    assert history.runs_with_a_duration == 3
    answer = history.answer(
        {
            "repository": Shape().repository,
            "workload_profile": Shape().workload_profile,
            "compute_profile": Shape().compute_profile,
            "dataset_release": Shape().dataset_release,
        }
    )
    assert answer.cohort is not None
    assert answer.cohort.median_seconds == Decimal(1800)


def test_a_dry_run_measures_and_writes_nothing(tmp_path: Path) -> None:
    """The half somebody runs before deciding whether to open a pull request.

    Mutation: write anyway and only skip the print. A maintainer checking what a reading
    would say would silently change the file the CLI reads.
    """
    tool = build_run_history()
    reading = a_reading(tmp_path / "reading.json", runs=(a_run(seconds=600),))
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    exit_code = tool.main(
        ["--reading", str(reading), "--config-dir", str(config_dir), "--dry-run"]
    )

    assert exit_code == tool.EXIT_OK
    assert not (config_dir / HISTORY_FILENAME).exists()


def test_being_given_no_source_is_a_refusal_rather_than_an_empty_digest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty digest is the one output this must never produce by accident.

    A file saying no cohort has enough runs is a claim about the platform, and a tool that
    wrote one when it had been handed nothing to read would be making that claim on no
    evidence.

    Mutation: default ``--reading`` to an empty substrate. This exits zero, writes a digest,
    and every install starts telling researchers nothing of their shape has ever run.
    """
    tool = build_run_history()
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    exit_code = tool.main(["--config-dir", str(config_dir)])

    assert exit_code == tool.EXIT_UNUSABLE
    assert "run_history_not_built" in capsys.readouterr().err
    assert not (config_dir / HISTORY_FILENAME).exists()


def test_the_report_says_what_fraction_would_be_answered_for(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Coverage is printed, because it is the figure the key has to be argued on.

    Three runs of one shape and one of another. Three would be answered for and one would
    not, and the line says so rather than reporting two cohorts.

    Mutation: print the cohort count as the coverage. The number stops being about
    submissions, and a store with one big cohort and nine tiny ones reads as poor coverage
    while answering for nearly everybody.
    """
    tool = build_run_history()
    reading = a_reading(
        tmp_path / "reading.json",
        runs=(
            *(
                a_run(seconds=seconds, run_id=f"run_{index}")
                for index, seconds in enumerate((600, 1800, 5400))
            ),
            a_run(
                seconds=99,
                shape=Shape(workload_profile="olmo-eval-check"),
                run_id="run_alone",
            ),
        ),
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    tool.main(["--reading", str(reading), "--config-dir", str(config_dir), "--dry-run"])

    out = capsys.readouterr().out
    assert "4 run(s) read, 4 with a duration" in out
    assert "3 of 4 would be answered for if submitted again" in out


def test_a_local_lineage_tree_goes_through_the_one_collector(tmp_path: Path) -> None:
    """The offline path, which is what a maintainer with a synced prefix uses.

    Read through ``read_substrate.collect`` rather than parsed here, so that a digest built
    from records on disk and one built from the account are the same code path with the same
    refusals.

    Mutation: parse the intent records in this tool. The digest would stop agreeing with the
    substrate about which runs exist, which is the second ingestion ``substrate.py``'s header
    exists to prevent.
    """
    tool = build_run_history()
    lineage = tmp_path / "lineage"
    (lineage / "intent").mkdir(parents=True)
    (lineage / "attempt").mkdir(parents=True)
    fixtures = PROJECT_ROOT / "fixtures" / "evidence" / "phase-3" / "runs"
    for records in sorted(fixtures.glob("*/records")):
        for prefix in ("intent", "attempt"):
            source = records / prefix
            if not source.is_dir():
                continue
            for document in source.rglob("*.json"):
                (lineage / prefix / document.name).write_bytes(document.read_bytes())

    # The repository's own config, because the collector prices runs from the catalog and a
    # temporary directory holds none. Nothing is written: this is the dry run.
    exit_code = tool.main(
        [
            "--lineage-root",
            str(lineage),
            "--config-dir",
            str(PROJECT_ROOT / "config"),
            "--dry-run",
        ]
    )

    assert exit_code == tool.EXIT_OK


def test_the_committed_fixtures_do_not_reach_the_bar_and_that_is_the_honest_answer(
    tmp_path: Path,
) -> None:
    """What this repository can measure without an account, and what the committed file is not.

    This tree holds four runs of fixture evidence, one of which has an attempt. So a digest
    built from what is committed here answers for nothing, and a digest built from these
    fixtures put in front of researchers would describe a store four runs deep.

    THE ASSERTION AT THE BOTTOM USED TO BE THAT THE FILE WAS ABSENT, AND THAT WAS A
    STATEMENT ABOUT A DAY RATHER THAN A PROPERTY. #267 shipped the lookup with no reading
    because the agent that built it held no credentials, so "there is no digest" was the
    honest thing to record then. There is one now, read off the lineage store, and the
    property worth holding is the one the absence was standing in for: whatever is committed
    describes the real store and not this fixture tree.

    Mutation: commit a digest built from these fixtures, or from any four runs. The bar is
    what catches it. A fixture-derived digest answers for nothing by construction -- which
    the first half of this case proves -- so a committed digest that reaches the bar cannot
    be one, and one that stopped reaching it is a reading nobody should be shipping either.
    """
    tool = build_run_history()
    reading = a_reading(
        tmp_path / "reading.json",
        runs=tuple(
            a_run(
                seconds=600,
                shape=Shape(workload_profile=f"workload-{index}"),
                run_id=f"run_{index}",
            )
            for index in range(4)
        ),
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    tool.main(["--reading", str(reading), "--config-dir", str(config_dir)])

    history = load_run_history(config_dir)
    assert history is not None
    assert not any(cohort.answerable for cohort in history.cohorts)

    committed = load_run_history(PROJECT_ROOT / "config")
    assert committed is not None, (
        f"config/{HISTORY_FILENAME} is what every install quotes durations from. It is "
        "produced by tools/build_run_history.py against the lineage store and lands in its "
        "own pull request"
    )
    assert committed.runs_read > history.runs_read
    quotable = [cohort for cohort in committed.cohorts if cohort.answerable]
    assert quotable, (
        "the committed digest answers for nothing, so either it was built from fixtures or "
        "the store it was built from has stopped holding enough successes to quote"
    )


def test_a_run_with_no_attempt_contributes_nothing_but_is_still_read(tmp_path: Path) -> None:
    """The denominator and the cohorts are different populations, and both are printed.

    Mutation: drop runs with no attempt before summarising. ``runs_read`` would stop being
    the number of runs the reading held, and a reader of the file could no longer tell how
    much of the store had produced no duration at all.
    """
    tool = build_run_history()
    never_started = a_run(seconds=0, run_id="run_queued")
    object.__setattr__(never_started, "attempts", ())
    object.__setattr__(never_started, "state", "submitted")
    object.__setattr__(never_started, "seconds", Decimal(0))
    reading = a_reading(
        tmp_path / "reading.json",
        runs=(a_run(seconds=600, run_id="run_done"), never_started),
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    tool.main(["--reading", str(reading), "--config-dir", str(config_dir)])

    history = load_run_history(config_dir)
    assert history is not None
    assert history.runs_read == 2
    assert history.runs_with_a_duration == 1


def test_the_reading_and_the_digest_agree_about_when_a_run_ended(tmp_path: Path) -> None:
    """The duration is the substrate's own, not recomputed from the timestamps here.

    Mutation: recompute the wall clock in ``run_history`` from ``started_at`` and
    ``ended_at``. Two arithmetics for one number is how a report and a page come to disagree,
    and ``run_costs`` already owns this one: it sums across attempts, which a single
    subtraction would get wrong for a retry.
    """
    tool = build_run_history()
    two_attempts = a_run(seconds=3600, attempts=2, run_id="run_retried")
    assert two_attempts.attempts[-1].ended_at - two_attempts.attempts[0].started_at == (
        timedelta(seconds=3600)
    )
    reading = a_reading(
        tmp_path / "reading.json",
        runs=(
            two_attempts,
            a_run(seconds=3600, run_id="run_b"),
            a_run(seconds=3600, run_id="run_c"),
        ),
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    tool.main(["--reading", str(reading), "--config-dir", str(config_dir)])

    history = load_run_history(config_dir)
    assert history is not None
    cohort = history.cohorts[0]
    assert cohort.median_seconds == two_attempts.seconds
