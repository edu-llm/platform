"""``edullm data`` at the terminal: what it prints, what it refuses, and what it never does.

**THE VERB IS A LOOKUP AND EVERY PROPERTY BELOW FOLLOWS FROM THAT.** It drives no process,
reaches no network, holds no credential and judges no submission, so it exits 0 unless a name
somebody typed is not one the registry carries. That is what lets it answer on a cluster login
node with no egress and, more to the point, for the fifteen of the thirty-five people on the
roster who hold no AWS role and can therefore see nothing in any bucket by any route. A verb
that read ``s3://edullm-data`` would be the only verb in the set that works for some people
and refuses others.

**WHAT IT REPLACES IS A DOCUMENTED INSTRUCTION TO PROVOKE AN ERROR.** The skill said, against
``unregistered_dataset``, that the detail lists what is registered -- so the way to find out
what corpora exist was to name one that does not. That list is names and nothing else, and
five of the names in it reach a container that exits 69 after the machine has been paid for.
The cases here hold the replacement in both directions: the verb answers, and the refusal
points at the verb.

``edullm_platform.corpora`` and ``tests/test_corpora.py`` hold the join and the measurement.
This file is about the surface.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edullm_platform.cli.main import EXIT_OK, EXIT_REFUSED, EXIT_UNUSABLE
from tests.cli_support import FakeRunner, invoke

#: What a reader is choosing between, and the one name in it a case may safely pin. Every
#: other reference id in this file is derived, because the registry grows.
A_CORPUS_THAT_RUNS = "regmix-10b-v1"

#: Registered, current, refused by nothing, and unrunnable. Pinned deliberately: this is the
#: state the verb exists for, and a case that derived it would pass on an empty set.
A_CORPUS_THAT_EXITS_69 = "lean4-mathlib-bytes-v3"


def data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *arguments: str
) -> tuple[int, str, str, FakeRunner]:
    runner = FakeRunner({})
    code, out, err = invoke(
        ["data", *arguments], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )
    return code, out, err, runner


def test_the_list_costs_no_process_and_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE PROPERTY THE VERB IS FOR.** Mutation: read the bucket, or shell out to anything.

    ``FakeRunner({})`` refuses to invent an answer for a command it was not given one for, so
    a verb that reached for ``aws``, ``git`` or ``gh`` fails here rather than working on a
    laptop with a session and failing on the login node this exists to serve.
    """
    code, out, err, runner = data(tmp_path, monkeypatch)

    assert code == EXIT_OK, out + err
    assert runner.calls == []
    assert out


def test_the_default_view_names_every_corpus_that_will_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE DECISION ABOUT UNRUNNABLE ENTRIES, HELD.** Mutation: put them behind ``--all``.

    Nothing on this platform refuses one of these, so the person who never types a flag is
    exactly the person who picks one and loses a machine to it. Hiding them behind a flag
    would make the verb's most valuable output opt-in, and the reader who most needs it is
    the one who does not know it exists.

    The exit code is checked too. A corpus that will not run is a fact about the registry and
    not a refusal of anything the reader asked for, so this is exit 0 with a warning rather
    than exit 1 with a verdict.
    """
    code, out, _, _ = data(tmp_path, monkeypatch)

    assert code == EXIT_OK
    assert A_CORPUS_THAT_EXITS_69 in out
    assert "exits 69" in out
    assert "THIS_IMAGE_HAS_NO_CONFIG_FOR_THAT_TOKENIZER" in out, (
        "the block names no exit condition, so a reader cannot search for what they would "
        "meet in the container log"
    )


def test_the_default_view_names_the_superseded_ones_and_what_replaced_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE DECISION ABOUT RETIRED ENTRIES, HELD.** Mutation: drop them from the page.

    ``formal-proof-premises-500m-v2`` was the version to name until 2026-08-06. Somebody
    reading a colleague's notebook from last week types that name, and a page that does not
    carry it tells them the platform never had it -- so they file an ask, or they name a
    different version and record that their run read something it did not. One line saying it
    is superseded and naming v3 answers the question they actually have.

    The replacement is read off the registry rather than written into a sentence, so a corpus
    superseded tomorrow gets a correct line with nobody editing anything.
    """
    code, out, _, _ = data(tmp_path, monkeypatch)

    assert code == EXIT_OK
    assert "formal-proof-premises-500m-v2" in out
    assert "name formal-proof-premises-500m-v3" in out


def test_the_default_view_withholds_the_inputs_and_says_how_many_it_withheld(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE ONE THING THAT IS BEHIND A FLAG, AND WHY THAT IS NOT HIDING.**

    Mutation: show the tokenizers beside the corpora, since nothing should be hidden.

    A tokenizer, a vendor mirror and a text corpus at a payload profile no run may read are
    registered so that dependents can pin them by digest. Naming one is refused before it
    costs anything and the refusal names the file, so there is no expensive mistake to
    prevent -- and a chooser reading past four tokenizers to find a corpus is reading a
    registry rather than a menu. What makes it not hiding is that the footer counts them and
    names the flag, so the number on the page and the number in the registry agree.
    """
    code, out, _, _ = data(tmp_path, monkeypatch)
    _, everything, _, _ = data(tmp_path, monkeypatch, "--all")

    assert code == EXIT_OK
    assert "smollm2-bpe-v1" not in out
    assert "edullm data --all" in out
    assert "smollm2-bpe-v1" in everything
    assert A_CORPUS_THAT_EXITS_69 in everything, "--all has to be a superset, not a filter"


def test_the_default_view_fits_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print every registered name in one table.

    The whole value of this over the registry file is that a person can read it in one go. A
    page that scrolls is one where the block naming the corpora that will not run is the part
    that goes off the top, because it is at the bottom.

    The bound is generous rather than tight -- it is not asserting a layout, it is asserting
    that the grouping decisions above are actually buying something.
    """
    _, out, _, _ = data(tmp_path, monkeypatch)

    assert len(out.splitlines()) <= 45, (
        "the default view has grown past a screen, so whichever group is last is the one "
        "nobody reads. Move something behind --all"
    )


def test_naming_one_corpus_prints_the_detail_a_chooser_has_stopped_needing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One verb with a detail form rather than two verbs, and this is what the form adds.

    The exact token count rather than the table's rounded one, the address, the pinned digest
    and the payload profile. Somebody on this page has chosen and is computing a step count
    or writing a registration, and neither is served by ``250.2B``.
    """
    code, out, err, _ = data(tmp_path, monkeypatch, "reservoir-dolma2-v1")

    assert code == EXIT_OK, out + err
    assert "s3://edullm-data/pretrain/reservoir-dolma2/v1/" in out
    assert "250,242,924,544" in out
    assert "pretrain-tokens/v1" in out


def test_the_detail_view_names_the_seal_gap_and_the_table_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**WHERE AN HONEST CAVEAT GOES.** Mutation: print it on every row, or nowhere.

    What a seal attests is that some build of the validator, at some time, agreed the digests
    matched, and not one of the sealed datasets records which build or when. That is a real
    guarantee about the bytes and not a guarantee about which checks ran. On every row it is
    noise a reader learns to skip; on the page of somebody who has narrowed to one corpus it
    is the sentence that stops "sealed and frozen" being read as more than it is.
    """
    _, listed, _, _ = data(tmp_path, monkeypatch)
    _, detail, _, _ = data(tmp_path, monkeypatch, A_CORPUS_THAT_RUNS)

    assert "edullm-data#23" in detail
    assert "edullm-data#23" not in listed


def test_every_printing_says_when_the_measurement_was_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**WHAT A COMMITTED MEASUREMENT OWES ITS READER.** Mutation: print the figures alone.

    The reading travels with the install, so an old install quotes an old reading and has no
    way to know it is old. A date is the whole of what lets a reader discount one, and it has
    to be on every printing rather than on a verbose form, because the reader who would think
    to ask is not the reader who needs telling.

    A date rather than an age, for ``run_history``'s reason: an age is computed against the
    reader's clock, so one reading printed in a test, in a pull request and on a terminal
    would be three different strings.
    """
    for arguments in ((), ("--all",), (A_CORPUS_THAT_RUNS,)):
        _, out, _, _ = data(tmp_path, monkeypatch, *arguments)
        assert "Measured on 2026-" in out, arguments


def test_a_name_nothing_registers_is_exit_one_and_suggests_the_nearest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print an empty table, or list all of them.

    An empty table reads as a platform with no corpora. Listing all of them answers a
    question the reader did not ask, and is a question one command with no argument already
    answers -- which is the difference between this refusal and the submission path's, where
    the reader has already filled in a form and needs the alternatives in hand.
    """
    code, out, err, _ = data(tmp_path, monkeypatch, "regmix-10b")

    assert code == EXIT_REFUSED, out + err
    assert "unregistered_dataset" in err
    # The refusal wraps, so the suggestion is read out of the words rather than out of a line.
    assert "Did you mean" in err
    assert "regmix-10b-v1?" in err


def test_the_document_carries_the_verdict_so_a_script_never_parses_the_prose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: publish the sentence and let a caller grep it.

    Every sentence this verb prints is prose this repository rewords, and a script matching
    on "will not run" is a script that breaks on a reword rather than on a change of fact.
    ``verdict`` is a closed set, so a caller filtering for the corpora that cost a machine
    can do it without knowing how the paragraph is phrased this week.
    """
    code, out, err, _ = data(tmp_path, monkeypatch, "--json")
    document = json.loads(out)

    assert code == EXIT_OK, err
    assert document["verb"] == "data"
    assert {"format_version", "edullm_version", "verb"} <= set(document)

    verdicts = {entry["reference_id"]: entry["verdict"] for entry in document["corpora"]}
    assert verdicts[A_CORPUS_THAT_EXITS_69] == "exits_69"
    assert verdicts[A_CORPUS_THAT_RUNS] == "runs"
    assert set(verdicts.values()) <= {"runs", "refused", "exits_69"}


def test_the_document_is_the_whole_registry_whatever_the_page_is_showing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: make ``--all`` change the document too.

    The flag decides what a person is shown, and grouping is a reading aid. A caller filtering
    on ``verdict`` should not have to know a flag exists before its filter covers the whole
    registry, and a caller that did not know would silently miss exactly the entries the flag
    withholds.
    """
    _, plain, _, _ = data(tmp_path, monkeypatch, "--json")
    _, everything, _, _ = data(tmp_path, monkeypatch, "--all", "--json")

    assert json.loads(plain)["corpora"] == json.loads(everything)["corpora"]


def test_the_measurement_is_reported_as_absent_rather_than_as_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: default an unmeasured field to 0 so the key always holds a number.

    A token count of zero is a claim about a corpus. ``None`` is the absence of one, and the
    two send a reader to different places: the first to ask why the corpus is empty, the
    second to re-run the measurement.
    """
    _, out, _, _ = data(tmp_path, monkeypatch, "--json")
    measured = {
        entry["reference_id"]: entry["measured"] for entry in json.loads(out)["corpora"]
    }
    without_tokens = measured[A_CORPUS_THAT_EXITS_69]

    assert without_tokens is not None
    assert without_tokens["train_tokens"] is None


def test_the_json_flag_is_the_flag_and_the_stream_is_not_sniffed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule ``cli/machine.py`` sets for the other two verbs, applied to the third.

    Emitting JSON whenever stdout is not a terminal would make ``edullm data > note.txt`` and
    ``edullm data`` disagree about what they printed, on the one artifact somebody pastes into
    a message.
    """
    _, redirected, _, _ = data(tmp_path, monkeypatch)

    assert not redirected.lstrip().startswith("{")


def test_a_flag_the_verb_does_not_take_is_exit_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, _, err, _ = data(tmp_path, monkeypatch, "--everything")

    assert code == EXIT_UNUSABLE
    assert "--everything is not a flag" in err
