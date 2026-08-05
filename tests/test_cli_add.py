"""``edullm add``: what teaching the platform a thing does, and what it refuses to pretend.

WHAT THIS VERB IS. docs-frank/reference/decisions.md, under "`add` and `ask`, not one
`request`", settles it: teaching the system about a thing, such as a repository, a dataset,
a shape, a model or a person, produces a config change that is permanent, shared and
self-service, with an agent writing the pull request. That is one act and `ask` is the other.

WHY FOUR OF THE FIVE KINDS REFUSE RATHER THAN BEING ABSENT. The buildout spec's non-goals
defer "the `add` and `ask` intake surface beyond `add repository`", so four of the five have
nothing behind them. Leaving them off the parser answers `edullm add dataset` with argparse's
"invalid choice", which tells an agent the word is wrong when the word is right and the
route is elsewhere. A refusal carrying a code says the true thing and says it in the one
vocabulary a skill can match on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edullm_platform.cli.intake import ADD_KINDS, SELF_SERVICE_KINDS
from edullm_platform.cli.main import EXIT_REFUSED, NOT_BUILT_YET
from tests.cli_support import FakeRunner, invoke


def test_add_is_no_longer_declared_unbuilt() -> None:
    """Mutation: build the verb and leave the row in NOT_BUILT_YET.

    That table is what makes `edullm add` answer "not built yet" rather than "invalid
    choice", and main.py refuses any verb in it before the subparser is ever reached. A row
    left behind would make the built verb unreachable while every help page said it existed.
    """
    assert "add" not in NOT_BUILT_YET


def test_every_kind_the_overview_names_is_a_kind_this_verb_takes() -> None:
    """Mutation: implement repository and drop the other four from the vocabulary.

    system-overview.md, under "What you click", names five: a repository, a dataset, a
    shape, a model or a person. The vocabulary is the design's and not this module's, so a
    kind missing here is a kind an agent is told does not exist.
    """
    assert set(ADD_KINDS) == {"repository", "dataset", "shape", "model", "person"}
    assert SELF_SERVICE_KINDS <= set(ADD_KINDS)


@pytest.mark.parametrize("kind", ["dataset", "shape", "model", "person"])
def test_a_kind_that_is_not_self_service_is_refused_under_a_code(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: exit 2 with a sentence, the way an unbuilt verb does.

    Exit 2 means the tool could not be driven, by input or by installation, and retrying it
    unchanged reaches the same place. That is false here. The input is correct, the platform
    understands it, and the answer is that this act goes through a person. Exit 1 is a
    verdict a caller can act on, and the code is what it acts on.
    """
    runner = FakeRunner({})

    code, out, err = invoke(
        ["add", kind],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED, out + err
    assert "add_kind_is_not_self_service" in err
    assert runner.calls == []


def test_the_refusal_names_ask_and_names_no_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: name `.github/ISSUE_TEMPLATE/dataset-request.yml` in the remedy.

    The templates are collapsing into one under the population plan's Task 10, so a refusal
    naming a file by name goes stale on somebody else's merge and points an agent at a path
    that 404s. `edullm ask` is the stable address, and it prints its own kinds.
    """
    code, out, err = invoke(
        ["add", "shape"],
        runner=FakeRunner({}),
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED, out + err
    assert "edullm ask" in err
    assert ".github/ISSUE_TEMPLATE" not in err


def test_a_routed_refusal_is_a_document_when_json_is_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: put the document on stderr with the paragraphs.

    An agent that asked for a document and got prose on stderr has to parse prose, which is
    the whole thing --json removes. Stdout, one document, exit unchanged.
    """
    code, out, err = invoke(
        ["add", "person", "--json"],
        runner=FakeRunner({}),
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED, out + err
    document = json.loads(out)
    assert document["verb"] == "add"
    assert [refusal["code"] for refusal in document["refusals"]] == [
        "add_kind_is_not_self_service"
    ]


def test_a_kind_nobody_declared_is_answered_with_the_kinds_there_are(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: accept any word and route it to ask.

    A misspelled kind and a kind that goes through a person are different facts, and routing
    the first to `ask` files an issue asking a human for `edullm add repositry`. argparse's
    `choices` answers this one for free and answers it with the list, which is what somebody
    who mistyped needs.

    Read through SystemExit and capsys rather than through invoke's two streams, because
    argparse owns this refusal and exits on it. tests/test_cli_exit_codes.py makes the same
    accommodation for `--attempts nope` and gives the reason: both are one fact to a shell.
    """
    with pytest.raises(SystemExit) as raised:
        invoke(
            ["add", "repositry"],
            runner=FakeRunner({}),
            cwd=tmp_path,
            monkeypatch=monkeypatch,
        )

    assert raised.value.code != 0
    assert "repository" in capsys.readouterr().err
