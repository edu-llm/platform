"""The tool that makes the spine's done-condition re-checkable by somebody who was not there."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

import pytest

from tests.test_run_comparison import LEFT, RIGHT, checkpoint, written
from tools.compare_two_runs import (
    CELL_BUDGET,
    EXIT_DIFFERED,
    EXIT_MATCHED,
    EXIT_UNUSABLE,
    EXIT_UNVERIFIED,
    main,
)

#: The five leaves a real July pair in the lineage store carries on neither side, in the
#: shape the records actually have them: ``exit_code`` gone from the document, and the two
#: objects present and null. Both spellings reach the same place -- there is no
#: ``result.wandb_run.entity`` leaf to compare -- and the null is the one the store holds,
#: so the fixture holds it too.
JULY_SHAPE: Final[tuple[str, ...]] = (
    "result.exit_code",
    "result.wandb_run.entity",
    "result.wandb_run.project",
    "result.checkpoint_survey.outcome",
    "result.checkpoint_survey.objects_seen",
)


def edited(root: Path, run_id: str, prefix: str, change: Callable[[dict[str, Any]], None]) -> None:
    """One record rewritten in place, the way a schema change would have written it."""
    record = root / prefix / f"{run_id}.json"
    document = json.loads(record.read_text(encoding="utf-8"))
    change(document)
    record.write_text(json.dumps(document), encoding="utf-8")


def as_the_store_held_it_in_july(document: dict[str, Any]) -> None:
    del document["exit_code"]
    document["wandb_run"] = None
    document["checkpoint_survey"] = None


def leaving_the_table_untouched(document: dict[str, Any]) -> None:
    """Drop required result fields the two fixture runs agree about, and only those.

    Every leaf this removes is one both runs carry identically, so it produced no row
    before and produces none after. That is what the guard below rests on: with these gone
    from both records there is nothing left for the two reports to differ about except
    whether the tool says the fields went unchecked. ``wandb_run`` is deliberately not
    among them -- its ``run_id`` differs by the run id, so nulling it would take a row out
    of the table and hand the guard an incidental difference to pass on.
    """
    del document["exit_code"]
    document["checkpoint_survey"] = None


def rows(report: str) -> list[str]:
    """The table, which is everything a reader compares two of these reports on."""
    return [line for line in report.splitlines() if line.startswith("| ")]


def compared(root: Path, capsys: pytest.CaptureFixture[str], **extra: str) -> tuple[int, str]:
    arguments = ["--lineage-root", str(root), "--left", LEFT, "--right", RIGHT]
    for name, value in extra.items():
        arguments += [f"--{name}", value]
    code = main(arguments)
    return code, capsys.readouterr().out


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
    """Mutation: drop the required-fields section from the report.

    A field present on one side and gone from the other is reported twice on purpose --
    once as a difference against `<absent>`, and once under a heading that says the
    comparison required it. The second is what makes the reading available to somebody
    holding the report rather than the tree, and it is what the section below it, for the
    fields neither run carries, is the other half of.
    """
    written(tmp_path, LEFT)
    written(tmp_path, RIGHT)
    edited(tmp_path, RIGHT, "intent", lambda document: document.pop("manifest_sha256"))

    code, printed = compared(tmp_path, capsys)

    assert code == EXIT_DIFFERED
    assert "one of these runs does not carry" in printed
    assert "intent.manifest_sha256" in printed


# ----------------------------------------------------------------------------------------
# A field neither run carries
# ----------------------------------------------------------------------------------------


def test_a_clean_comparison_and_one_that_checked_fewer_fields_do_not_read_alike(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """THE REGRESSION GUARD. Mutation: require only the paths the two records carry.

    That mutation is what shipped, and it needs a test of this shape because it survives
    every check written against one report at a time. Both records dropping a field
    together produces no difference row, no missing-field line and no non-zero exit, so
    there is nothing in a single report to assert about. Held against a clean pass it is
    visible and damning: the two are the SAME TABLE, the SAME COUNT and the SAME EXIT, and
    one of them checked three fewer fields than the other.

    Two answers that must not read alike, in the sense the exit codes above already use.
    One says the comparison looked and found nothing; the other says three of the fields it
    claims to check were never compared. The first assertion is what makes the second
    load-bearing: it pins the fixture to dropping only fields the runs agree about, so a
    report that differs can differ for no reason other than saying so.
    """
    clean, holed = tmp_path / "clean", tmp_path / "holed"
    for root in (clean, holed):
        written(root, LEFT)
        written(root, RIGHT)
    for run_id in (LEFT, RIGHT):
        edited(holed, run_id, "result", leaving_the_table_untouched)

    matched, agreement = compared(clean, capsys)
    unverified, silence = compared(holed, capsys)

    assert rows(silence) == rows(agreement), "the fixture drops nothing the table was showing"
    assert silence != agreement, "so the only thing left to differ about is saying so"
    assert unverified != matched, "a comparison that did not look is not one that found nothing"
    assert (matched, unverified) == (EXIT_MATCHED, EXIT_UNVERIFIED)
    for path in ("result.checkpoint_survey.objects_seen", "result.exit_code"):
        assert f"- `{path}`" in silence
        assert path not in agreement
    assert "NEITHER of these runs carries" in silence
    assert "NEITHER" not in agreement


def test_a_field_neither_run_carries_is_recorded_in_the_json_and_not_only_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: print the unverified fields and leave them out of the document.

    ``--output`` is what gets committed as evidence and read months later by somebody who
    does not have the tree. A document that lists differences alone cannot tell a
    comparison that found nothing wrong from one that did not look, which is the same
    defect one layer down from the terminal.
    """
    written(tmp_path, LEFT)
    written(tmp_path, RIGHT)
    for run_id in (LEFT, RIGHT):
        edited(tmp_path, run_id, "result", as_the_store_held_it_in_july)
    report = tmp_path / "comparison.json"

    code, _ = compared(tmp_path, capsys, output=str(report))

    assert code == EXIT_UNVERIFIED
    document = json.loads(report.read_text(encoding="utf-8"))
    assert tuple(document["unverified"]) == tuple(sorted(JULY_SHAPE))
    assert all(one["cause"] for one in document["differences"])


def test_a_run_that_differs_outranks_a_field_that_was_never_checked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: return the unverified code whenever anything went unchecked.

    Both are printed and only one can be a return value. A difference nothing explains is
    actionable now; a field nobody could check is a caveat on how much of the search was
    possible. Reporting the caveat and swallowing the finding would let an unexplained
    difference through on any pair old enough to be missing a field.
    """
    written(tmp_path, LEFT)
    written(tmp_path, RIGHT, wandb_project="somewhere-else")
    for run_id in (LEFT, RIGHT):
        edited(tmp_path, run_id, "result", as_the_store_held_it_in_july)

    code, printed = compared(tmp_path, capsys)

    assert code == EXIT_DIFFERED
    assert "intent.manifest.wandb_project" in printed
    assert "NEITHER of these runs carries" in printed


# ----------------------------------------------------------------------------------------
# A checkpoint comparison that could not run
# ----------------------------------------------------------------------------------------


def wrote_where_nobody_looks(document: dict[str, Any]) -> None:
    """The record the two spine runs actually carry: a survey naming what it skipped.

    ``objects_seen`` and ``bytes_seen`` are the ones the fixture already has, and they are
    the half that was never in doubt. The run wrote. What it wrote is under a directory
    called ``step-20``, which matches neither ``^step(\\d+)$`` nor ``^checkpoint-(\\d+)$``,
    so nothing described it and ``checkpoints`` stayed empty.
    """
    document["checkpoint_survey"]["unparsed_directories"] = ["step-20"]


def test_a_checkpoint_comparison_that_could_not_run_says_so_and_does_not_exit_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """THE REGRESSION GUARD. Mutation: make the layout matcher miss again.

    This is the shape of ``test_a_clean_comparison_and_one_that_checked_fewer_fields_do_not
    _read_alike`` one level down, and it is here because the defect it guards is worse.
    That one was a required field neither record carried. This is the checkpoint, which is
    the thing the spine exists to verify, and it passed clean on 2026-08-05 against
    ``run_019fd2c9`` and ``run_019fd2ca``: ten differences, every one a run id, a clock or
    an id Batch or GitHub minted, exit 0, and not one checkpoint field among them. Both
    runs had written 762 MB.

    Held against a clean pass, because a single report of the broken case has nothing in it
    to assert on. No checkpoint row appears either way, the difference count is the same,
    and the exit code was the same. The three assertions are that the table is unchanged --
    so the fixture is not smuggling in an incidental difference -- that the two reports are
    nonetheless different, and that the exit codes are not both zero.

    TO SEE THIS GO RED, put the hyphen back. Revert
    ``tools/build_gpu_training_submission.py`` to ``checkpoints/step-{steps}/`` and a run of
    it records exactly the fixture below; delete the ``checkpoints.is_blocked`` term from
    ``main`` and this asserts ``EXIT_DIFFERED == EXIT_MATCHED``.
    """
    clean, blind = tmp_path / "clean", tmp_path / "blind"
    for root in (clean, blind):
        written(root, LEFT)
        written(root, RIGHT)
    for run_id in (LEFT, RIGHT):
        edited(blind, run_id, "result", wrote_where_nobody_looks)

    matched, agreement = compared(clean, capsys)
    blocked, alarm = compared(blind, capsys)

    assert rows(alarm) == rows(agreement), "the fixture adds nothing the table was showing"
    assert alarm != agreement, "so the only thing left to differ about is saying so"
    assert (matched, blocked) == (EXIT_MATCHED, EXIT_DIFFERED)
    assert "THE CHECKPOINT COMPARISON DID NOT RUN" in alarm
    assert "step-20" in alarm
    assert LEFT in alarm and RIGHT in alarm
    assert "DID NOT RUN" not in agreement


def test_the_directory_nobody_could_read_is_in_the_json_and_not_only_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: print the blocked checkpoint and leave it out of the document.

    ``--output`` is what gets committed beside a done-condition and read months later by
    somebody without the tree. A document recording ten named differences and nothing else
    is a document that says the spine was checked, and the checkpoint half of it was not.
    """
    written(tmp_path, LEFT)
    written(tmp_path, RIGHT)
    for run_id in (LEFT, RIGHT):
        edited(tmp_path, run_id, "result", wrote_where_nobody_looks)
    report = tmp_path / "comparison.json"

    code, _ = compared(tmp_path, capsys, output=str(report))

    assert code == EXIT_DIFFERED
    document = json.loads(report.read_text(encoding="utf-8"))
    assert [
        (one["run_id"], one["directory"]) for one in document["unreadable_checkpoints"]
    ] == [(LEFT, "step-20"), (RIGHT, "step-20")]


def test_a_comparison_that_walked_a_checkpoint_says_its_payload_was_not_compared(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: let a table with no `checksum` row stand for two identical checkpoints.

    With the layout fixed, both spine runs record one checkpoint whose ``checksum`` is a
    SHA-256 over the two object names and their sizes. Those are identical, so the field is
    identical, so no row appears. The payloads are not identical -- ``1a3f1588...`` against
    ``606e9ee2...`` in the markers -- and nothing in the record can show it. Silence about
    a digest that was never read is the same defect as silence about a directory that was
    never parsed, one level further in.
    """
    written(tmp_path, LEFT)
    written(tmp_path, RIGHT)
    for run_id in (LEFT, RIGHT):
        edited(
            tmp_path,
            run_id,
            "result",
            lambda document, run_id=run_id: document.update(  # type: ignore[misc]
                checkpoints=[checkpoint(run_id)]
            ),
        )

    code, printed = compared(tmp_path, capsys)

    assert code == EXIT_MATCHED
    assert not [line for line in rows(printed) if ".checksum" in line]
    assert "The checkpoint payloads were not compared" in printed
    assert "1 checkpoint(s) were compared" in printed


def test_a_pair_that_saved_nothing_is_not_told_its_payloads_went_uncompared(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: print the caveat unconditionally.

    The fixture runs are check-shaped and wrote no checkpoints. Telling their reader that
    no checkpoint payload was compared is true and is noise, and a caveat printed on every
    comparison is one nobody reads by the third time.
    """
    written(tmp_path, LEFT)
    written(tmp_path, RIGHT)

    code, printed = compared(tmp_path, capsys)

    assert code == EXIT_MATCHED
    assert "were not compared" not in printed
    assert "DID NOT RUN" not in printed


def test_the_four_exit_codes_are_four_answers() -> None:
    """Four answers, and no two of them may read alike.

    The tool is wired to a done-condition, so the codes are the interface. Collapsing any
    pair of them is how a caller stops being able to tell one of these situations apart
    from another.
    """
    assert len({EXIT_MATCHED, EXIT_DIFFERED, EXIT_UNUSABLE, EXIT_UNVERIFIED}) == 4


# ----------------------------------------------------------------------------------------
# A value too long for a table
# ----------------------------------------------------------------------------------------


def test_a_command_too_long_for_a_cell_is_cut_in_the_table_and_printed_whole_below(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: put the value in the cell.

    ``intent.manifest.command[N]`` is a real leaf and the longest one in the lineage store
    on 2026-08-04 is seven thousand characters. Inline that is not a table: it is one row
    wrapped over eighty screen lines with every other row pushed off the top. The pipe
    matters as much as the length -- an unescaped one inside a cell invents two columns and
    the rest of the table stops parsing anywhere it is rendered as markdown.
    """
    written(tmp_path, LEFT)
    written(tmp_path, RIGHT)
    scripts = {
        LEFT: "python -m train | tee " + "a" * 450,
        RIGHT: "python -m train | tee " + "b" * 450,
    }
    for run_id, script in scripts.items():
        edited(
            tmp_path,
            run_id,
            "intent",
            lambda document, script=script: document["manifest"].update(  # type: ignore[misc]
                command=["bash", "-lc", script]
            ),
        )

    _, printed = compared(tmp_path, capsys)

    row = next(line for line in rows(printed) if "intent.manifest.command[2]" in line)
    assert scripts[LEFT] not in row
    assert "..." in row
    # Bounded by the budget at all is the property. Before this a row was bounded by
    # whatever the submitter typed, which in this store reaches seven thousand characters.
    assert max(len(line) for line in rows(printed)) < 2 * CELL_BUDGET + 120
    # Four columns is five delimiters. A pipe the value carried is escaped and does not add
    # one, which is the whole of what escaping buys.
    assert row.count("|") - row.count("\\|") == 5
    table, _, below = printed.partition("### The values the table above had to cut short")
    assert scripts[LEFT] in below and scripts[RIGHT] in below
    assert scripts[LEFT] not in table


def test_a_value_the_table_can_hold_is_left_whole_in_its_cell(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: shorten every value, or repeat every value below the table.

    A run id is forty-two characters and an output prefix is a hundred, and both are rows a
    reader came for: the whole point of the prefix row is watching the run id substituted
    into it. Cutting those would send somebody below the table on every comparison, which
    would cost the section its meaning by the second time they read one.
    """
    written(tmp_path, LEFT)
    written(tmp_path, RIGHT)

    _, printed = compared(tmp_path, capsys)

    assert "had to cut short" not in printed
    assert "..." not in printed
    for path in ("intent.run_id", "result.output_prefixes[0]"):
        row = next(line for line in printed.splitlines() if line.startswith(f"| `{path}`"))
        assert LEFT in row and RIGHT in row
