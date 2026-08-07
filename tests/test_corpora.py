"""What ``edullm data`` answers, and what keeps the measurement behind it true.

**THE COLUMN THIS VERB EXISTS FOR IS A JOIN AND NOT A RECORD, AND THAT IS WHAT MOST OF THIS
FILE HOLDS.** Five registered corpora are current, in a trainable family, at a corpus payload
profile, and refused by nothing this platform checks. A submission naming one compiles clean,
classifies routine, spends an approval, allocates the machine, and reaches a container that
cannot construct a tokenizer for the tokens it just resolved. Until this verb the only way to
find that out was to submit one and pay for it.

That verdict is computed on every printing out of ``config/datasets.yaml``,
:data:`~edullm_platform.tokenizers.TOKENIZERS` and ``config/image-contents.yaml``. All
three are on the release trigger, so a change to any of them cuts a release; nothing about it
is stored, so there is no stale copy to go wrong. The cases below hold it against the very
functions the submission path uses, in both directions, so the verb cannot say a corpus runs
while ``edullm check`` refuses it, and cannot say one is refused while nothing refuses it.

**THE THIRD OF THOSE THREE IS NEW AND ITS ABSENCE IS WHY THIS FILE'S CLAIM WAS FALSE.** The
tokenizer map is what this platform can express and the image record is what an image can
build, and the verdict was reading the first as though it were the second. Two entries were
added to the map ahead of the matching lines in OLMo-core -- the ordering ``tokenizers.py``
sets out in capitals and nothing enforced -- so ``fineweb-edu-750m-v2``, ``fineweb-edu-1b-v6``
and ``formal-proof-premises-500m-v3`` were verdicted ``runs``, and ``run_019fdd88-3ac4``
named the first, was admitted, allocated a GPU and exited 69. Every case below that names a
count or a set now counts three more, and that is the defect being visible rather than the
suite weakening.

**THE PART THAT CAN GO STALE IS THE MEASUREMENT, AND WHAT FAILS WHEN IT DRIFTS IS HERE.**
``config/reports/corpora.json`` carries the facts that live in somebody else's bucket -- train
tokens, shard dtype, licence -- because ``edullm`` holds no credential and fifteen of the
thirty-five people on the roster hold no AWS role. A committed table goes stale silently,
which is the exact defect the guide's hand-typed table had, so three things hold this one:
every registered corpus has a row or the suite is red, every row names a corpus the registry
carries or the suite is red, and every measured field agrees with the registry where the two
describe the same fact. What it cannot hold is whether a token count is still the bucket's
answer, which is why the document carries ``measured_at`` and every printing of it prints
that date.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from edullm_platform.config import load_yaml
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.image_contents import ImageContentsRecord, VocabularyName
from edullm_platform.corpora import (
    CORPORA_FILENAME,
    NO_SNAPSHOT_PACKAGED,
    CorporaSnapshot,
    CorporaSnapshotFormatError,
    CorpusMeasurement,
    CorpusUnknownError,
    as_document,
    corpora,
    from_document,
    load_corpora_snapshot,
    one_corpus,
    tokens_said,
)
from edullm_platform.tokenizers import THE_CONTAINERS_REFUSAL, TOKENIZERS
from tests.test_submission_form_options import refusals_for

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT_ROOT / "config"


def registry() -> DatasetRegistry:
    return load_yaml(CONFIG / "datasets.yaml", DatasetRegistry)


def images() -> ImageContentsRecord:
    """The reviewed reading of what the published images hold, as the verb loads it.

    The committed file rather than a fixture, in every case below that asks what the verb
    answers. A fixture here would make this module a test of the join and no longer a test of
    what a researcher is told, and what a researcher is told is the thing that was wrong.
    """
    return load_yaml(CONFIG / "image-contents.yaml", ImageContentsRecord)


def snapshot() -> CorporaSnapshot:
    packaged = load_corpora_snapshot(CONFIG)
    assert packaged is not None, (
        f"config/{CORPORA_FILENAME} is not committed, so every case below would be asserting "
        "over a verb printing dashes"
    )
    return packaged


# --------------------------------------------------------------------------------------
# The join: what will actually run
# --------------------------------------------------------------------------------------


def test_the_verb_and_the_submission_path_agree_about_what_is_refused() -> None:
    """**THE CASE THE WHOLE VERB RESTS ON.** Mutation: report a verdict of its own.

    A verb that says a corpus runs while ``edullm check`` refuses it is worse than no verb:
    the reader has been told the opposite of the truth by the tool they consulted to avoid
    finding out the expensive way. So the verdict is asked against ``refusals_for``, which
    drives the same ``build_request_facts``, ``denied_outright_conditions`` and
    ``require_a_dataset_release_that_is_current`` the compile job and admission drive.

    Both directions, and each fails differently. A corpus the verb calls refused and nothing
    refuses is the verb hiding a real option. A corpus the verb says will run and something
    refuses is the verb sending somebody to a refusal.
    """
    for row in corpora(registry(), images=images()):
        refusals = refusals_for(row.reference_id)
        if row.runnability.verdict == "refused":
            assert refusals, (
                f"{row.reference_id} is reported as refused before it costs anything and "
                "nothing on the submission path refuses it, so this verb is hiding a corpus "
                "somebody could have named"
            )
        else:
            assert not refusals, (
                f"{row.reference_id} is reported as nameable and the submission path refuses "
                f"it with {refusals}, so this verb sends a reader to a refusal"
            )


def test_the_corpora_that_exit_69_are_the_ones_nothing_refuses_and_no_tokenizer_builds() -> None:
    """**THE FIVE. Mutation: fold ``exits_69`` into ``refused``, since neither one runs.**

    They are not the same state and the difference is a GPU allocation. A refused corpus
    costs a submitter a second attempt; one of these costs an approval, a machine and the
    time to find out, and what it reaches is a container printing
    ``THIS_IMAGE_HAS_NO_CONFIG_FOR_THAT_TOKENIZER``. A boolean column would have collapsed
    the expensive case into the harmless one.

    Derived rather than listed, so it shrinks on its own: record an image carrying the
    missing tokenizer and the corpus moves into the runnable table with nobody editing this.
    The names are asserted anyway, beside the derivation, because a set that quietly went
    empty would read as the gap having closed rather than as the derivation having broken.

    **IT HELD FIVE AND HOLDS EIGHT, AND THE THREE THAT JOINED WERE ALWAYS IN THIS STATE.**
    They were not caught because the derivation asked the wrong map. ``fineweb-edu-750m-v2``,
    ``fineweb-edu-1b-v6`` and ``formal-proof-premises-500m-v3`` depend on
    ``tokenizer/smollm2-bpe`` and ``tokenizer/qwen25-vendored``, both of which this platform
    can express and neither of which any published image carries -- so all three compiled
    clean, reached the form, and cost exactly what this set exists to enumerate.
    ``run_019fdd88-3ac4`` proved it on the first of them.
    """
    caught = {
        row.reference_id
        for row in corpora(registry(), images=images())
        if row.runnability.costs_a_machine
    }

    assert caught == {
        "fineweb-edu-1b-v6",
        "fineweb-edu-750m-v2",
        "formal-proof-premises-500m-v3",
        "frontload-cl-chat-sft-v1",
        "lean4-mathlib-bytes-v3",
        "math-memory-full-v1",
        "math-sft-60m-v1",
        "pedagogy70-normal30-v1",
    }, (
        "the set of registered corpora that nothing refuses and no image can build a "
        "tokenizer for has moved. If it shrank, either an image was recorded carrying the "
        "tokenizer or a refusal was built, and both are progress. If it grew, a corpus was "
        "registered that this platform admits and cannot run."
    )
    for reference_id in caught:
        assert refusals_for(reference_id) == (), (
            f"{reference_id} is refused now, so it is not in this state any more"
        )


@pytest.mark.parametrize(
    ("reference_id", "why"),
    [
        ("lean4-mathlib-bytes-v3", "tokenizer/bytes-utf8"),
        ("math-memory-full-v1", "tokenizer/bytes-utf8"),
        ("pedagogy70-normal30-v1", "declares no tokenizer"),
        ("fineweb-edu-750m-v2", "no published image carries one"),
        ("fineweb-edu-1b-v6", "no published image carries one"),
        ("formal-proof-premises-500m-v3", "no published image carries one"),
    ],
)
def test_the_verb_says_which_of_the_three_reasons_a_corpus_will_not_run(
    reference_id: str, why: str
) -> None:
    """Mutation: one sentence for all eight, since all eight meet the same exit code.

    The remedies are opposite, which is the whole reason ``said`` is prose beside a closed
    verdict. A corpus on ``tokenizer/bytes-utf8`` is waiting on an upstream feature and
    resolves itself the day OLMo-core grows one, so the thing to do is ask upstream. A corpus
    declaring no tokenizer is not waiting on anything at all: its payload is pre-tokenization
    conversation text and the run's tokenizer comes from the model, so what it needs is a
    workload that reads it that way. Telling somebody who picked the tutor corpus to go and
    ask for a byte tokenizer spends their week on the wrong question.

    **THE THIRD REASON IS THE ONE THIS CHANGE ADDS AND IT IS THE CHEAPEST TO FIX.** The
    tokenizer is one this platform already knows how to build a config for; what is missing
    is a line in a research repository's own map and a re-reading of that image. Reported as
    "no OLMo-core TokenizerConfig builds it" -- the sentence the other five get -- it would
    send somebody to write a config that already exists.
    """
    row = one_corpus(reference_id, registry(), images=images())

    assert row.runnability.costs_a_machine
    assert why in row.runnability.said
    assert THE_CONTAINERS_REFUSAL in row.runnability.said, (
        "the sentence names no exit condition, so a reader cannot search for what they will "
        "meet in the container log"
    )


def test_a_tokenizer_this_platform_can_build_and_no_image_carries_names_the_images_asked() -> (
    None
):
    """**THE DEFECT, HELD AT THE SENTENCE A RESEARCHER READS.** Mutation: report these three
    as runnable, which is what shipped, or refuse them without saying which images were asked.

    ``run_019fdd88-3ac4`` named ``fineweb-edu-750m-v2``, was admitted, allocated a GPU and
    exited 69. The verb had said it would run, because the verdict read this platform's
    tokenizer map -- which is a statement about what can be expressed -- and presented it as
    a statement about what an image can build.

    Naming the images is the second half and is not decoration. "No image carries it" sends a
    reader to look at every image there is; naming them and what each holds sends them to the
    one map that has to gain a line, which is the same argument
    ``unreviewed_blocking_findings`` makes for listing findings rather than counting them.
    """
    row = one_corpus("fineweb-edu-750m-v2", registry(), images=images())
    recorded = images()

    assert row.runnability.verdict == "exits_69"
    assert not row.runnability.will_run
    assert "tokenizer/smollm2-bpe" in row.runnability.said
    for reading in recorded.images:
        assert reading.repository in row.runnability.said, (
            f"{reading.repository} was asked and is not named, so a reader cannot tell which "
            "image has to gain the line"
        )
    # And the distinction from the other reason survives: this one must not send somebody to
    # write a TokenizerConfig that this platform already has.
    assert "no OLMo-core TokenizerConfig builds" not in row.runnability.said


def test_the_runnable_set_is_the_one_the_submission_form_offers() -> None:
    """Mutation: let the verb and the dropdown answer differently.

    Two surfaces promising "everything in this list works" have to promise it about the same
    list, or the verb is a second menu and the day they part company nobody finds out from
    either of them.

    **BOTH CONDITIONS, BECAUSE ONE OF THEM ALONE IS THE DEFECT.** A corpus is runnable when
    this platform can express its tokenizer *and* a published image carries it. Asking only
    the first is what put three corpora on both surfaces that no image can train; asking only
    the second would offer a corpus an image can build and this platform cannot describe to
    it. The join is restated here rather than imported, deliberately -- an equality against
    the function under test is not an equality -- and that restatement is what makes this
    fail when either condition is dropped from ``_runnability``.
    """
    runnable = {
        row.reference_id
        for row in corpora(registry(), images=images())
        if row.runnability.will_run
    }
    carried = images().names_some_image_carries(VocabularyName.TOKENIZERS)
    offered = {
        entry["reference_id"]
        for entry in json.loads(json.dumps(_published()))
        if entry["dataset_id"].split("/", maxsplit=1)[0] in {"pretrain", "sft"}
        and entry["payload_profile"] in {"pretrain-tokens/v1", "sft-conversations/v1"}
        and not entry.get("retired", False)
        and entry["tokenizer"] in TOKENIZERS
        and entry["tokenizer"] in carried
    }

    assert runnable == offered
    assert runnable, "no corpus runs at all, so both sides of this are empty and prove nothing"


def _published() -> list[dict[str, object]]:
    document = load_yaml(CONFIG / "datasets.yaml", DatasetRegistry)
    return [entry.model_dump(mode="json") for entry in document.published]


def test_no_runnability_verdict_is_written_into_the_committed_measurement() -> None:
    """**WHAT KEEPS THE COLUMN THAT MATTERS FROM GOING STALE.** Mutation: cache it.

    Caching the verdict is the obvious optimisation and it is the one thing this design must
    not do. A stored verdict is a claim made on the day somebody ran a tool, and the day
    OLMo-core grows a byte tokenizer it becomes a wrong claim with nothing to prompt a
    re-measurement -- which is exactly the defect the guide's hand-typed table had, moved
    into a file that looks machine-generated and is therefore trusted more.

    Asserted on the document rather than on the dataclass, because the file is what an
    install carries and the file is what somebody would edit.
    """
    document = json.loads((CONFIG / CORPORA_FILENAME).read_text(encoding="utf-8"))
    for entry in document["corpora"]:
        for banned in ("runs", "verdict", "runnable", "will_run", "exits_69"):
            assert banned not in entry, (
                f"{entry['reference_id']} carries a stored {banned!r}. Runnability is a join "
                "over config/datasets.yaml and edullm_platform.tokenizers, both of which the "
                "wheel carries and both of which are on the release trigger, so it is "
                "computed on every printing and cannot be older than the install"
            )


# --------------------------------------------------------------------------------------
# The measurement, and what fails when it drifts
# --------------------------------------------------------------------------------------


def test_every_registered_corpus_is_measured_and_every_measurement_is_registered() -> None:
    """**THE STALENESS GUARD. Mutation: register a corpus and re-measure nothing.**

    Both directions, because each one is a different silence. A registered corpus with no row
    prints dashes for its size, tokenizer dtype and licence on every terminal, for ever, and
    nothing anywhere says the reading missed it -- which is how the reading rots one corpus at
    a time as the registry grows. A row for a corpus nothing registers is a measurement no
    verb will ever print, left behind by a de-registration.

    This is the check that makes ``tools/build_corpora_snapshot.py`` load bearing rather than
    optional: registering a corpus is now a pull request that has to carry a re-measurement,
    and the failure names the command.
    """
    registered = {entry.reference_id for entry in registry().published}
    measured = {entry.reference_id for entry in snapshot().measurements}

    assert registered - measured == set(), (
        f"{sorted(registered - measured)} are registered and unmeasured, so edullm data "
        "prints dashes for them. Re-run tools/build_corpora_snapshot.py against a reading of "
        "s3://edullm-data and commit config/reports/corpora.json"
    )
    assert measured - registered == set(), (
        f"{sorted(measured - registered)} are measured and nothing registers them, so no verb "
        "will ever print those rows"
    )


def test_the_measurement_and_the_registry_never_describe_two_different_corpora() -> None:
    """Mutation: measure a corpus and key the row on the dataset id rather than the reference.

    The two files are joined on ``reference_id`` and nothing else, so a typo there is a row
    that silently attaches to no corpus -- or, worse, to the wrong version of one, since
    ``fineweb-edu-1b`` is registered at v2 and v6. The case above catches an absent row; this
    catches a row that is present and describes something else, by holding the one fact both
    files carry about the same corpus.
    """
    for row in corpora(registry(), images=images(), snapshot=snapshot()):
        assert row.measurement is not None
        assert row.measurement.reference_id == row.reference_id
        if row.reference.tokenizer is None:
            assert row.measurement.train_tokens is None, (
                f"{row.reference_id} declares no tokenizer and the measurement gives it a "
                "token count, so the two files disagree about whether it holds tokens"
            )


def test_a_rounded_token_count_is_marked_as_one_rather_than_printed_as_a_fact() -> None:
    """**WHAT STOPS A TRANSCRIPTION READING AS A READING.** Mutation: drop the flag.

    The first committed measurement is partly a transcription: seven figures come out of
    ``config/datasets.yaml``, where whoever registered the corpus wrote the count off its
    sealed ``dataset.json`` to the token, and the rest come out of a table that states one
    decimal place in billions. Both sort the same, and only one of them may be printed as a
    fact. Without the flag the file would be indistinguishable from a bucket reading and
    every figure in it would be quoted as exact.

    The flag also measures how much of the file is still a transcription, and it goes away on
    its own: every row a real reading produces is exact.
    """
    rounded = [
        entry for entry in snapshot().measurements if not entry.train_tokens_exact
    ]

    assert rounded, (
        "every token count is now exact, which means the file is a reading rather than a "
        "transcription. Delete this case and the ~ in presentation.py's size column"
    )
    for entry in rounded:
        assert entry.train_tokens is not None, (
            f"{entry.reference_id} is marked as rounded and carries no figure, so the flag "
            "is describing nothing"
        )
    said = {
        row.reference_id: row.train_tokens_said
        for row in corpora(registry(), images=images(), snapshot=snapshot())
    }
    for entry in rounded:
        assert said[entry.reference_id].startswith("~"), (
            f"{entry.reference_id} carries a rounded count and prints as a plain figure, so "
            "a reader takes a number nobody read to the token as one that was"
        )


def test_the_licence_column_says_share_alike_where_the_licence_field_does_not() -> None:
    """**WHY LICENCE EARNS A COLUMN.** Mutation: print the licence id and stop.

    ``reservoir-dolma2-v1`` declares ``{basis: unknown, id: null}`` and its own notes record
    that stackexchange and finewiki are CC-BY-SA-4.0, finewiki additionally GFDL, and that
    the two together are 7.13 per cent of its train tokens. A reader of the licence field
    alone sees the same blank they see beside a dozen honestly-unknown corpora, and
    share-alike is not that kind of unknown: it is a condition on redistributing a model,
    which somebody sorting by size has no other way to learn.
    """
    row = one_corpus("reservoir-dolma2-v1", registry(), images=images(), snapshot=snapshot())

    assert row.measurement is not None
    assert row.measurement.licence is None, (
        "the corpus now declares a licence id, so this case is asserting about the wrong "
        "corpus and the share-alike column has lost its worked example"
    )
    assert row.measurement.share_alike
    assert "share-alike" in row.licence_said
    assert "CC-BY-SA-4.0" in (row.measurement.note or ""), (
        "the detail view no longer says which sources carry it, so a reader is told there is "
        "a condition and not what it is"
    )


def test_a_measurement_this_tree_cannot_read_is_a_broken_install_and_not_an_absent_one(
    tmp_path: Path,
) -> None:
    """Mutation: swallow a parse failure and report no measurement.

    An absent reading and an unreadable one send a reader to different places. The first is
    an ordinary install, an editable checkout or a directory a test built, and the verb says
    so. The second is a broken install, and degrading it to "no measurement" would print a
    table of dashes that looks like a platform with no data in it.
    """
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "corpora.json").write_text("{ not json", encoding="utf-8")

    with pytest.raises(CorporaSnapshotFormatError):
        load_corpora_snapshot(tmp_path)


def test_an_install_carrying_no_measurement_says_so_rather_than_printing_nothing(
    tmp_path: Path,
) -> None:
    """Mutation: return an empty snapshot when the file is absent.

    An empty reading would answer every corpus with a dash and no explanation, which reads as
    a platform holding no data rather than as an install carrying no reading. That is
    ``run_history``'s distinction and it is the same one.
    """
    assert load_corpora_snapshot(tmp_path) is None
    assert "tools/build_corpora_snapshot.py" in NO_SNAPSHOT_PACKAGED, (
        "the sentence names no way out, so a reader who meets it can only conclude the tool "
        "is broken"
    )


def test_a_document_from_a_newer_format_is_refused_rather_than_read_thinly() -> None:
    """Mutation: read what it recognises and ignore the rest.

    A newer document would carry fields this reader drops, and a dropped measurement is
    indistinguishable from a measurement nobody took. Absent is the one thing a reading may
    not invent.
    """
    document = as_document(
        CorporaSnapshot(
            measured_at=datetime(2026, 8, 6, tzinfo=UTC),
            measured_from="a reading",
            measurements=(CorpusMeasurement("regmix-10b-v1", train_tokens=1),),
        )
    )
    assert from_document(document).measurements[0].train_tokens == 1

    with pytest.raises(CorporaSnapshotFormatError, match="format_version"):
        from_document({**document, "format_version": document["format_version"] + 1})


def test_the_committed_file_is_the_bytes_the_writer_produces() -> None:
    """Mutation: hand-edit a figure into the file.

    The file is written by one tool, sorted and indented, and nothing else may put bytes in
    it. Reading it back and re-serialising has to reproduce it exactly, so an edit that a
    re-measurement would not produce -- a hand-typed number, a key nobody writes, a row out
    of order -- fails here rather than surviving until somebody re-runs the tool and gets a
    diff they did not expect.
    """
    path = CONFIG / CORPORA_FILENAME
    written = json.dumps(as_document(snapshot()), indent=2, sort_keys=True) + "\n"

    assert path.read_text(encoding="utf-8") == written, (
        "config/reports/corpora.json is not what as_document writes for the reading it "
        "holds. It is written by tools/build_corpora_snapshot.py and by nothing else"
    )


# --------------------------------------------------------------------------------------
# The shape of the answer
# --------------------------------------------------------------------------------------


def test_the_list_is_sorted_by_size_and_the_unmeasured_sort_last() -> None:
    """Mutation: sort by reference id.

    Somebody choosing a corpus is choosing a size first, and alphabetical order puts a 21B
    multilingual corpus above a 100M maths one for no reason a reader can use. An unmeasured
    corpus sorts last rather than first, because a row with no figure is not a small one.
    """
    rows = corpora(registry(), images=images(), snapshot=snapshot())
    measured = [row for row in rows if row.measurement and row.measurement.train_tokens]
    counted = [row.measurement.train_tokens for row in measured if row.measurement]

    assert counted == sorted(counted)
    assert rows[len(measured) :], "nothing is unmeasured, so the tail of this is untested"
    for row in rows[len(measured) :]:
        assert row.measurement is None or row.measurement.train_tokens is None


def test_a_name_the_registry_does_not_carry_is_a_refusal_rather_than_an_empty_table() -> None:
    for missing in ("regmix-10b", "no-such-corpus-v9"):
        with pytest.raises(CorpusUnknownError):
            one_corpus(missing, registry(), images=images())


@pytest.mark.parametrize(
    ("count", "said"),
    [(9_989_799_834, "10.0B"), (99_793_454, "99.8M"), (2_078, "2078")],
)
def test_a_token_count_is_said_the_way_a_person_says_one(count: int, said: str) -> None:
    """Nobody reads 250,242,924,544 as a quarter of a trillion, and the table is for reading.

    The exact integer is on the detail view and in ``--json``, which is where somebody
    computing a step count already is.
    """
    assert tokens_said(count) == said
