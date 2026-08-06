"""The tool that writes the corpus measurement, driven over a reading a case builds.

**IT REACHES NO NETWORK AND THAT IS WHAT MAKES IT TESTABLE AT ALL.** The obvious shape for
this tool is a boto3 call, and a boto3 call is a thing a test can only pretend to have made.
Splitting the fetch out -- one ``aws s3 cp --recursive`` by somebody who has already assumed
the researcher role, then this over the directory it left -- means the reduction runs here
against real ``dataset.json`` documents laid out as the bucket lays them out, and means two
people reducing the same reading commit the same bytes.

The documents below are the three seal shapes the sealed bucket actually holds, cut down to
the fields this reduces. Written out rather than fetched, because what is under test is the
reduction and not the bucket, and a fixture that mirrored one dataset exactly would pass on
the one shape it was copied from.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.corpora import CorporaSnapshot, from_document
from tools.build_corpora_snapshot import (
    EXIT_OK,
    EXIT_UNUSABLE,
    ReadingUnusableError,
    main,
    measurement_from,
    read_a_reading,
    report,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: A token corpus as the bucket seals one: a payload group named ``tokens`` carrying the
#: dtype and the byte count, partitions carrying the counts, and a licence block.
A_TOKEN_CORPUS: dict[str, Any] = {
    "dataset_id": "pretrain/regmix-10b",
    "version": {"id": "v1", "relation": "supersedes", "of": None},
    "purpose": "A ten billion token dolma2 pretraining mixture.",
    "license": {"id": "ODC-By-1.0", "basis": "declared"},
    "groups": [
        {
            "name": "tokens",
            "profile": "pretrain-tokens/v1",
            "dtype": "uint32",
            "bytes": 4_995_000_000,
            "partitions": [
                {"name": "train", "count": 9_989_799_834},
                {"name": "val", "count": 5_000_000},
            ],
        }
    ],
}

#: The shape that carries no licence, which is fourteen of the thirty-two, and a share-alike
#: fact that lives in prose rather than in the licence field.
A_CORPUS_WITH_NO_LICENCE_ID: dict[str, Any] = {
    "dataset_id": "pretrain/reservoir-dolma2",
    "version": "v1",
    "license": {"id": None, "basis": "unknown"},
    "notes": "stackexchange and finewiki are CC-BY-SA-4.0, finewiki additionally GFDL.",
    "groups": [
        {
            "name": "tokens",
            "profile": "pretrain-tokens/v1",
            "dtype": "uint32",
            "bytes": 5_004_971_698_176,
            "partitions": [{"name": "train", "count": 1_251_242_924_544}],
        }
    ],
}

#: A conversations corpus, which declares no dtype and holds rows rather than tokens.
A_CONVERSATIONS_CORPUS: dict[str, Any] = {
    "dataset_id": "sft/pedagogy70-normal30",
    "version": "v1",
    "license": {"id": None, "basis": "unknown"},
    "groups": [
        {
            "name": "conversations",
            "profile": "sft-conversations/v1",
            "bytes": 21_630_120,
            "partitions": [{"name": "train", "count": 25_329}],
        }
    ],
}


def a_reading(root: Path, *documents: dict[str, Any]) -> Path:
    """The documents laid out the way ``aws s3 cp --recursive`` leaves them."""
    for document in documents:
        version = document["version"]
        version_id = version["id"] if isinstance(version, dict) else version
        directory = root / str(document["dataset_id"]) / str(version_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "dataset.json").write_text(json.dumps(document), encoding="utf-8")
    return root


def test_the_reading_is_keyed_on_the_pair_the_registry_resolves_a_corpus_by(
    tmp_path: Path,
) -> None:
    """Mutation: key on the path.

    The registry resolves a corpus by ``{dataset_id, version}`` and the tool joins on that.
    Keying on the path would make the answer depend on how somebody happened to invoke
    ``aws s3 cp`` -- with or without a trailing prefix, into one directory or a nested one.

    Both spellings of ``version`` are read, because upstream writes it as an object carrying
    a relation and the registry stores the id, and the bucket's seals already come in three
    shapes. A reader that understood one would silently drop the other.
    """
    root = a_reading(tmp_path, A_TOKEN_CORPUS, A_CORPUS_WITH_NO_LICENCE_ID)
    found = read_a_reading(root)

    assert set(found) == {
        ("pretrain/regmix-10b", "v1"),
        ("pretrain/reservoir-dolma2", "v1"),
    }


def test_a_reading_with_nothing_in_it_is_refused_rather_than_written_as_empty(
    tmp_path: Path,
) -> None:
    """**THE FAILURE THAT WOULD BE SILENT.** Mutation: reduce an empty directory to an empty file.

    A pointed-at-the-wrong-directory run would commit a measurement covering no corpus, and
    the verb would print a table of dashes that reads as a platform holding no data. The
    refusal names the command that produces a reading, because the likeliest reason to be
    here is not having made one.
    """
    with pytest.raises(ReadingUnusableError, match="no dataset.json"):
        read_a_reading(tmp_path)


def test_the_reduction_takes_the_seven_facts_and_invents_none_of_them() -> None:
    measured = measurement_from("regmix-10b-v1", A_TOKEN_CORPUS)

    assert measured.train_tokens == 9_989_799_834
    assert measured.train_tokens_exact, "a figure read off a seal is exact by construction"
    assert measured.shard_dtype == "uint32"
    assert measured.size_bytes == 4_995_000_000
    assert measured.licence == "ODC-By-1.0"
    assert not measured.share_alike
    assert measured.purpose


def test_an_absent_field_is_absent_rather_than_filled_with_a_plausible_value() -> None:
    """**THE ONE KIND OF FACT THIS PLATFORM REFUSES TO INVENT.** Mutation: default the dtype.

    Fourteen of the thirty-two sealed datasets declare ``{basis: unknown, id: null}``, and a
    conversations corpus declares no dtype because it holds rows. ``pretrain-tokens/v1`` is
    the value twenty entries carry, so it is exactly the guess somebody would reach for --
    and it is precisely the guess that makes the hazard invisible again, because a corpus
    read at the wrong width produces a loss curve rather than an exception.
    """
    measured = measurement_from("pedagogy70-normal30-v1", A_CONVERSATIONS_CORPUS)

    assert measured.shard_dtype is None
    assert measured.licence is None
    assert measured.purpose is None
    assert measured.size_bytes == 21_630_120


def test_share_alike_is_read_out_of_the_prose_because_that_is_where_it_lives() -> None:
    """**WHY THIS IS A SUBSTRING TEST AND NOT A PARSE.** Mutation: read the licence field only.

    ``pretrain/reservoir-dolma2`` declares ``{basis: unknown, id: null}`` and records in its
    own notes that stackexchange and finewiki are CC-BY-SA-4.0 and finewiki additionally
    GFDL. There is nothing structured to parse: the fact was written by hand by whoever
    published, and the licence field says the opposite of it.

    It over-reports rather than under-reports, which is the right direction. A corpus flagged
    here is one somebody goes and reads; a corpus missed here is a model published under a
    condition nobody saw.
    """
    measured = measurement_from("reservoir-dolma2-v1", A_CORPUS_WITH_NO_LICENCE_ID)

    assert measured.licence is None
    assert measured.share_alike


def test_the_tool_writes_only_rows_the_registry_carries_and_reports_the_rest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both halves of the join, in the two directions they can go wrong.

    The sealed bucket holds more datasets than the registry carries, so a reading is
    routinely wider than the file and a row nothing can name is a row no verb prints. The
    other direction is the one worth being loud about: a registered corpus the reading missed
    prints dashes for ever, so it is named rather than counted.
    """
    root = a_reading(tmp_path / "reading", A_TOKEN_CORPUS, {
        **A_TOKEN_CORPUS,
        "dataset_id": "pretrain/nothing-registers-this",
    })
    written = tmp_path / "corpora.json"

    code = main(
        [
            "--reading",
            str(root),
            "--config-dir",
            str(PROJECT_ROOT / "config"),
            "--write",
            str(written),
        ]
    )
    said = capsys.readouterr().out
    snapshot = from_document(json.loads(written.read_text(encoding="utf-8")))

    assert code == EXIT_OK
    assert {entry.reference_id for entry in snapshot.measurements} == {"regmix-10b-v1"}
    assert "the reading covered none of these" in said
    assert "reservoir-dolma2-v1" in said, "name the registered corpora the reading missed"


def test_a_reading_that_cannot_be_read_is_exit_two_rather_than_a_traceback(
    tmp_path: Path,
) -> None:
    """The repository's convention: 0 reported, 2 the inputs could not be read, and no 1.

    There is no 1 because this tool judges nothing, so it has nothing to refuse on the
    merits.
    """
    assert main(["--reading", str(tmp_path / "nowhere")]) == EXIT_UNUSABLE


def test_the_report_names_the_rounded_figures_so_a_transcription_cannot_hide(
    tmp_path: Path,
) -> None:
    """Mutation: report a count and stop.

    The first committed measurement is partly a transcription, and the flag on each row is
    how much of it still is. Somebody deciding whether to commit a new reading wants to know
    that number went to zero, and a bare count of rows does not say.
    """
    from edullm_platform.corpora import CorpusMeasurement

    snapshot = CorporaSnapshot(
        measured_at=from_document(
            {
                "format_version": 1,
                "measured_at": "2026-08-06T00:00:00+00:00",
                "measured_from": "a reading",
                "corpora": [],
            }
        ).measured_at,
        measured_from="a reading",
        measurements=(
            CorpusMeasurement("a-v1", train_tokens=1, train_tokens_exact=False),
            CorpusMeasurement("b-v1", train_tokens=2),
        ),
    )

    said = "\n".join(report(snapshot, registered=["a-v1", "b-v1", "c-v1"]))

    assert "carrying a rounded token count" in said
    assert "a-v1" in said
    assert "c-v1" in said, "the uncovered corpus has to be named as well"
