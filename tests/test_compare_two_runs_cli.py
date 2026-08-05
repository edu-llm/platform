"""The tool that makes the spine's done-condition re-checkable by somebody who was not there."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_run_comparison import LEFT, RIGHT, written
from tools.compare_two_runs import EXIT_DIFFERED, EXIT_MATCHED, EXIT_UNUSABLE, main


def test_two_runs_of_one_submission_exit_zero_and_name_every_difference(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    written(tmp_path, LEFT)
    written(tmp_path, RIGHT)
    report = tmp_path / "comparison.json"

    code = main(
        [
            "--lineage-root",
            str(tmp_path),
            "--left",
            LEFT,
            "--right",
            RIGHT,
            "--output",
            str(report),
        ]
    )

    assert code == EXIT_MATCHED
    document = json.loads(report.read_text(encoding="utf-8"))
    assert document["left"] == LEFT
    assert {one["path"] for one in document["differences"]} >= {
        "intent.run_id",
        "intent.recorded_at",
        "result.attempt_id",
        "result.output_prefixes[0]",
    }
    assert all(one["cause"] for one in document["differences"])
    assert "the run id" in capsys.readouterr().out


def test_a_difference_nothing_explains_exits_one_and_says_which_field(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: exit zero whatever was found.

    A tool that reports and does not refuse is a tool nobody wires to anything. The exit
    code is what lets the done-condition be re-checked later without a person reading a
    table.
    """
    written(tmp_path, LEFT)
    written(tmp_path, RIGHT, wandb_project="somewhere-else")

    code = main(["--lineage-root", str(tmp_path), "--left", LEFT, "--right", RIGHT])

    assert code == EXIT_DIFFERED
    assert "intent.manifest.wandb_project" in capsys.readouterr().out


def test_a_missing_record_exits_two_rather_than_reporting_a_difference(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: report a run whose records are not all there as differing.

    Two exit codes that must not read alike. One means the runs are not the same
    submission; the other means nobody could tell, and treating the second as the first
    would turn an unsynced tree into a finding about the platform.
    """
    written(tmp_path, LEFT)
    written(tmp_path, RIGHT)
    (tmp_path / "decision" / f"{RIGHT}.json").unlink()

    code = main(["--lineage-root", str(tmp_path), "--left", LEFT, "--right", RIGHT])

    assert code == EXIT_UNUSABLE
    assert "decision" in capsys.readouterr().err


def test_the_two_run_ids_must_differ(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Comparing a run with itself reports no differences and establishes nothing."""
    written(tmp_path, LEFT)

    code = main(["--lineage-root", str(tmp_path), "--left", LEFT, "--right", LEFT])

    assert code == EXIT_UNUSABLE
    assert "same run" in capsys.readouterr().err


def test_a_required_field_one_run_stopped_carrying_is_named_in_its_own_section(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: drop identical_fields_missing from the report.

    A field present on one side and gone from the other is reported twice on purpose --
    once as a difference against `<absent>`, and once under a heading that says the
    comparison required it. Only the second survives the case the first cannot see, which
    is both records dropping the field together; keeping the section is what makes that
    reading available to somebody holding the report rather than the tree.
    """
    written(tmp_path, LEFT)
    written(tmp_path, RIGHT)
    record = tmp_path / "intent" / f"{RIGHT}.json"
    document = json.loads(record.read_text(encoding="utf-8"))
    del document["manifest_sha256"]
    record.write_text(json.dumps(document), encoding="utf-8")

    code = main(["--lineage-root", str(tmp_path), "--left", LEFT, "--right", RIGHT])

    printed = capsys.readouterr().out
    assert code == EXIT_DIFFERED
    assert "Fields the comparison requires" in printed
    assert "intent.manifest_sha256" in printed
