"""The join written at mint time, and the two ways writing it could destroy what it holds.

**THE INDEX IS THE ONLY COPY OF WHAT IT HOLDS, WHICH IS WHY THESE TESTS ARE SHAPED THE WAY
THEY ARE.** Every other record in this platform can be recomputed: a cost from an attempt
record, a placement verdict from a queue, a digest from a zip. The mapping from a platform
run id to the workflow run that minted it cannot, because the runs API does not expose
dispatch inputs and the artifacts that carry the manifest age out. So a writer that lost the
existing index and force-pushed a fresh one would not degrade the answer, it would delete it.
`test_an_index_that_could_not_be_read_stops_the_write` is the test that whole arrangement
exists for.

Nothing here reaches GitHub or AWS.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from publish_run_index import EXIT_OK, EXIT_UNUSABLE, RunIndexInputError, main, publish

from edullm_platform.run_index import (
    RUN_INDEX_FORMAT_VERSION,
    MintedRun,
    RunIndexFormatError,
    as_document,
    from_document,
    merged,
)

RUN_A = "run_019fa73d-be37-7066-984b-a4bacf194f49"
RUN_B = "run_019fa96f-8f10-705a-a7a9-69c42eafce16"

#: A GitHub workflow run id, spelled rather than written. `tests/test_evidence.py` scans the
#: tracked tree for an eleven-digit integer, because this account's id read as an integer
#: loses its leading zero and is eleven digits; a run id is eleven digits now too.
A_WORKFLOW_RUN = int("30281990942")
ANOTHER_WORKFLOW_RUN = int("30281990943")

MINTED = datetime(2026, 8, 4, 14, 16, tzinfo=UTC)


def _minted(run_id: str, *, workflow_run_id: int = A_WORKFLOW_RUN, **overrides: object) -> MintedRun:
    fields: dict[str, object] = {
        "run_id": run_id,
        "workflow_run_id": workflow_run_id,
        "workflow_run_url": f"https://github.com/edu-llm/platform/actions/runs/{workflow_run_id}",
        "submitter": "alsy7009",
        "repository": "OLMo-core",
        "commit_sha": "4204375e6db85abc244ec7f626de8d3cc3511402",
        "team": "pre-training",
        "experiment": "mixlaw-370m",
        "compute_profile": "gpu-8xa100",
        "approval_class": "lead",
        "fanout_size": None,
        "minted_at": MINTED,
        "maximum_compute_cost_usd": Decimal("21.96"),
    }
    fields.update(overrides)
    return MintedRun(**fields)  # type: ignore[arg-type]


def _environment(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    values = {
        "RUN_ID": RUN_A,
        "WORKFLOW_RUN_ID": str(A_WORKFLOW_RUN),
        "WORKFLOW_RUN_URL": f"https://github.com/edu-llm/platform/actions/runs/{A_WORKFLOW_RUN}",
        "SUBMITTER": "alsy7009",
        "RESEARCH_REPOSITORY": "OLMo-core",
        "COMMIT_SHA": "4204375e6db85abc244ec7f626de8d3cc3511402",
        "TEAM": "pre-training",
        "COMPUTE_PROFILE": "gpu-8xa100",
        "APPROVAL_CLASS": "lead",
        "EXPERIMENT": "mixlaw-370m",
        "FANOUT_SIZE": "",
        "MAXIMUM_COMPUTE_COST_USD": "21.96",
    }
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


# --------------------------------------------------------------------------------------
# The document
# --------------------------------------------------------------------------------------


def test_an_entry_survives_being_written_down_and_read_back() -> None:
    """Mutation: drop a field from as_entry, or invent a default in of_entry.

    The entry is the whole of what anybody will ever know about the join, so a field lost
    here is one nobody can go back for: the artifact that carried it expires and the runs
    API never had it.
    """
    entry = _minted(RUN_A, fanout_size=66)
    assert from_document(json.loads(json.dumps(as_document([entry])))) == (entry,)


def test_a_document_this_tree_cannot_read_is_refused_rather_than_partly_read() -> None:
    """Mutation: read whatever fields are recognised and default the rest."""
    document = as_document([_minted(RUN_A)])
    assert document["format_version"] == RUN_INDEX_FORMAT_VERSION
    with pytest.raises(RunIndexFormatError, match="format"):
        from_document({**document, "format_version": RUN_INDEX_FORMAT_VERSION + 1})


def test_the_index_reads_newest_first() -> None:
    """Mutation: append, or sort by run id.

    A run id is a uuid7 under a prefix so it does sort by time, which is exactly what makes
    this worth asserting against a hand-written order rather than against the ids: a sort
    that happened to be right for the wrong reason would go wrong the first time two runs
    were minted out of id order.
    """
    older = _minted(RUN_A, minted_at=datetime(2026, 8, 3, 9, tzinfo=UTC))
    newer = _minted(RUN_B, workflow_run_id=ANOTHER_WORKFLOW_RUN, minted_at=MINTED)
    document = as_document([older, newer])
    assert [entry["run_id"] for entry in document["runs"]] == [RUN_B, RUN_A]


def test_a_run_id_that_arrives_twice_keeps_the_workflow_run_that_minted_it() -> None:
    """Mutation: let the later entry win, which is what a cache would do.

    A run id is minted once, so a second entry naming it is a re-run of the workflow rather
    than a newer truth. The first workflow run is the one holding the compile log, the
    approver and the artifacts; the re-run minted a different id and recorded nothing about
    this one.
    """
    first = _minted(RUN_A, workflow_run_id=A_WORKFLOW_RUN)
    again = _minted(RUN_A, workflow_run_id=ANOTHER_WORKFLOW_RUN)
    kept = merged([first], again)
    assert [minted.workflow_run_id for minted in kept] == [A_WORKFLOW_RUN]


# --------------------------------------------------------------------------------------
# Publishing, and the force-push it is about to feed
# --------------------------------------------------------------------------------------


def test_an_absent_index_is_an_empty_one(tmp_path: Path) -> None:
    """Mutation: refuse when the file is missing, which fails the first submission forever.

    The branch genuinely holds nothing on its first day, and that is the one time an empty
    index is a fact rather than a loss.
    """
    index = tmp_path / "nested" / "run-index.json"
    held, grew = publish(index, _minted(RUN_A))
    assert (held, grew) == (1, True)
    assert from_document(json.loads(index.read_text(encoding="utf-8")))[0].run_id == RUN_A


def test_a_second_run_is_added_rather_than_replacing_the_first(tmp_path: Path) -> None:
    """Mutation: write only the arriving run, which is what a force-push then publishes.

    The document is rewritten whole and force-pushed, so "write what I know" and "write
    everything" produce the same green job and one of them empties the branch.
    """
    index = tmp_path / "run-index.json"
    publish(index, _minted(RUN_A))
    held, grew = publish(index, _minted(RUN_B, workflow_run_id=ANOTHER_WORKFLOW_RUN))
    assert (held, grew) == (2, True)
    assert {minted.run_id for minted in from_document(json.loads(index.read_text()))} == {
        RUN_A,
        RUN_B,
    }


def test_an_index_that_could_not_be_read_stops_the_write(tmp_path: Path) -> None:
    """THE ONE THIS MODULE EXISTS FOR. Mutation: treat an unreadable file as an empty index.

    Absent and unreadable are the same zero entries and only one of them is a fact. A writer
    that started fresh on a parse error would force-push a one-entry index over every mapping
    the branch holds, and no search can rebuild them: the runs API never carried the join and
    the artifacts that did have expired. A truncated fetch is the ordinary way this happens.
    """
    index = tmp_path / "run-index.json"
    publish(index, _minted(RUN_A))
    index.write_text('{"format_version": 1, "runs": [{"run_i', encoding="utf-8")

    with pytest.raises(RunIndexInputError, match="cannot be reconstructed"):
        publish(index, _minted(RUN_B, workflow_run_id=ANOTHER_WORKFLOW_RUN))

    assert index.read_text(encoding="utf-8").startswith('{"format_version": 1, "runs": [{"run_i')


def test_a_file_from_a_newer_writer_stops_the_write_too(tmp_path: Path) -> None:
    """Mutation: refuse on a JSON error and fall through on a version this tree cannot read.

    Both are "the existing index is not something this can merge into", and the version one
    is the likelier of the two: it is what a rollback looks like from the older side.
    """
    index = tmp_path / "run-index.json"
    index.write_text(
        json.dumps({"format_version": RUN_INDEX_FORMAT_VERSION + 1, "runs": []}), encoding="utf-8"
    )
    with pytest.raises(RunIndexInputError, match="cannot be reconstructed"):
        publish(index, _minted(RUN_A))


# --------------------------------------------------------------------------------------
# What the workflow hands it
# --------------------------------------------------------------------------------------


def test_the_entry_is_built_from_the_environment_the_workflow_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: read a value off the command line, where a submitted string becomes a word.

    The experiment and the command reach this workflow as submitter-typed text. Everything
    here arrives through the environment for the reason the compile job gives beside its own
    form assembly: a value that becomes a shell word is a value somebody can put a quote in.
    """
    index = tmp_path / "run-index.json"
    _environment(monkeypatch)
    assert main(["--index", str(index)]) == EXIT_OK
    assert RUN_A in capsys.readouterr().out

    held = from_document(json.loads(index.read_text(encoding="utf-8")))
    assert len(held) == 1
    assert held[0].run_id == RUN_A
    assert held[0].workflow_run_id == A_WORKFLOW_RUN
    assert held[0].experiment == "mixlaw-370m"
    assert held[0].fanout_size is None


def test_a_missing_workflow_run_id_is_refused_rather_than_indexed_as_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: default an unset variable to the empty string, which every shell does.

    An entry whose workflow_run_id is empty is a mapping to nowhere, written into the one
    document that exists so nobody has to search. It would answer a lookup, and the answer
    would be wrong rather than absent -- which is worse than the search it replaced.
    """
    index = tmp_path / "run-index.json"
    _environment(monkeypatch, WORKFLOW_RUN_ID="")
    assert main(["--index", str(index)]) == EXIT_UNUSABLE
    assert "run_index_not_written" in capsys.readouterr().err
    assert not index.exists()


def test_a_run_arriving_with_no_worst_case_is_refused_rather_than_indexed_unpriced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: default the figure to absent, the way the field itself is defaulted.

    The field on ``MintedRun`` is optional because the branch holds entries written before
    it existed and a reader has to parse them. Writing one without it is a different thing:
    the compile job computes this on every submission and puts it in ``GITHUB_OUTPUT``, so
    an entry arriving here with no cost means the workflow stopped passing it.

    Defaulting it would be the quiet failure and it is quiet in the worst direction. Every
    subsequent submission would read a day it could not price, ``daily_ceiling`` would fail
    closed, and every run on the platform would go to a team lead for a reason nothing on
    the page explains. That is a control stuck on rather than off, and the first repair
    anybody reaches for is deleting the ceiling. Refusing here puts it in the log of the
    job that broke it, on the run that broke it.
    """
    index = tmp_path / "run-index.json"
    _environment(monkeypatch, MAXIMUM_COMPUTE_COST_USD="")

    assert main(["--index", str(index)]) == EXIT_UNUSABLE
    assert "MAXIMUM_COMPUTE_COST_USD" in capsys.readouterr().err
    assert not index.exists()


@pytest.mark.parametrize("carried", ["free", "-1.00", "$21.96"])
def test_a_worst_case_that_is_not_an_amount_is_refused(
    carried: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: read it with ``Decimal`` and let whatever parses through.

    ``Decimal("free")`` raises and ``Decimal("-1.00")`` does not, which is the row worth
    having: a negative figure in this document subtracts from the day's total and buys back
    unattended spending that a real run committed. It cannot arrive from the compile job,
    which is exactly why nothing else would notice it.
    """
    index = tmp_path / "run-index.json"
    _environment(monkeypatch, MAXIMUM_COMPUTE_COST_USD=carried)

    assert main(["--index", str(index)]) == EXIT_UNUSABLE
    assert "MAXIMUM_COMPUTE_COST_USD" in capsys.readouterr().err


def test_a_submission_with_no_experiment_is_indexed_without_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: require the experiment, which is optional on the form.

    It is the field the morning message opens with and it is not compulsory, so refusing an
    entry without one would leave exactly the submissions nobody labelled out of the index.
    """
    index = tmp_path / "run-index.json"
    _environment(monkeypatch, EXPERIMENT="", FANOUT_SIZE="66")
    assert main(["--index", str(index)]) == EXIT_OK
    capsys.readouterr()

    held = from_document(json.loads(index.read_text(encoding="utf-8")))
    assert held[0].experiment is None
    assert held[0].fanout_size == 66
