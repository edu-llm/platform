"""``edullm ask``: one ask, filed where asks are counted, from wherever somebody is standing.

WHAT THIS VERB IS. docs-frank/reference/decisions.md, under "`add` and `ask`, not one
`request`": asking for something for yourself, such as a credential the platform does not
hold, an unusual resource or an escalation for work the platform cannot express, produces a
time-boxed grant to one person and genuinely needs a human. system-overview.md adds the half
that decides this module's shape: `edullm ask` files the same form the templates offer, and
one place is what makes asks countable, which is what turns the third identical one into a
config change.

**THE KIND VOCABULARY IS A SEAM AND NOT A LIST, AND THAT IS THE WHOLE OF WHY THIS PLAN DOES
NOT WAIT ON ANOTHER ONE.** The four intake templates exist today and the population plan's
Task 10 collapses them into one. A CLI holding a hardcoded copy of the labels would keep
working after that collapse while filing asks under labels nothing counts. So the tuple lives
in the CLI, because an installed wheel carries no `.github/`, and the test below reads the
templates and asserts set equality. The collapse turns that test red, one tuple is edited,
and no ask is ever filed under a label the counter does not read. It is the arrangement
ADMISSION_JOB and PLATFORM_REPOSITORY already sit on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from edullm_platform.cli.intake import ASK_KINDS, issue_body
from edullm_platform.cli.main import EXIT_OK, EXIT_REFUSED, NOT_BUILT_YET
from tests.cli_support import CONFIG_DIR, FakeRunner, failed, invoke, ok

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / ".github" / "ISSUE_TEMPLATE"
ISSUE_URL = "https://github.com/edu-llm/platform/issues/301"


def declared_kinds() -> set[str]:
    """Every kind the intake forms offer, read out of the forms.

    READ FROM THE DROPDOWN RATHER THAN FROM ``labels:``, WHICH IS WHERE IT USED TO BE. Four
    forms meant one kind per form, so a form's unconditional label was its kind. One triage
    form cannot work that way: ``labels:`` is unconditional, so a single form declaring a kind
    would file every ask under it. The kind is now what the requester picks and the label is
    what whoever triages puts on, and this is the seam that keeps the two vocabularies equal.

    Still read across every form in the directory rather than out of ask.yml by name, so a
    second form reintroduced beside the triage one and offering a kind of its own widens this
    set and fails the comparison. A second form offering no kind at all is invisible here and
    is caught by test_triage_form.py, which is where the one-form property lives.
    """
    found: set[str] = set()
    for path in sorted(TEMPLATE_DIR.glob("*.yml")):
        if path.name == "config.yml":
            continue
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for entry in document.get("body", ()):
            if entry.get("type") != "dropdown" or entry.get("id") != "kind":
                continue
            found.update(str(option) for option in entry["attributes"]["options"])
    return found


def ask_runner(*, create_ok: bool = True, labelled_ok: bool = True) -> FakeRunner:
    def create(argv: tuple[str, ...]) -> object:
        if not create_ok:
            return failed("HTTP 410: Issues are disabled for this repo")
        if "--label" in argv and not labelled_ok:
            return failed("could not add label: 'access-request' not found")
        return ok(f"{ISSUE_URL}\n")

    return FakeRunner({("gh", "issue", "create"): create})  # type: ignore[arg-type]


def test_ask_is_no_longer_declared_unbuilt() -> None:
    """Mutation: build the verb and leave the row in NOT_BUILT_YET.

    main.py refuses any verb in that table before the subparser is reached, so the row would
    make the built verb unreachable while every help page said it existed.
    """
    assert "ask" not in NOT_BUILT_YET


def test_the_kinds_this_verb_offers_are_the_kinds_the_forms_offer() -> None:
    """Mutation: add a kind here that the form does not offer, or drop one that it does.

    THIS IS THE TEST THAT LET TWO PLANS PROCEED WITHOUT WAITING ON EACH OTHER, AND IT HAS NOW
    DONE IT. An installed wheel carries no .github/, so the vocabulary has to live in the
    package. What must never happen is that it drifts from the forms, because an ask filed
    under a label the counter does not read is an ask nobody counts, and countability is the
    whole reason one place exists.

    The collapse to a single triage form turned this red rather than silent, exactly as the
    comment above ASK_KINDS said it would, and what moved is where the kind is written: from a
    label per form to a dropdown on the one form. The vocabulary itself did not move, so every
    ask already filed still counts under the label it carries.
    """
    assert set(ASK_KINDS) == declared_kinds()


def test_an_ask_is_filed_with_its_kind_as_the_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: file every ask unlabelled and let a human triage it.

    Counting groups on the label. An unlabelled ask is uncounted, and an uncounted ask is
    exactly the one that gets asked a third time without anybody noticing it should have
    become a config change.
    """
    runner = ask_runner()

    code, out, err = invoke(
        [
            "ask",
            "--kind",
            "access-request",
            "--title",
            "I cannot see the submission form",
            "--detail",
            "The Actions page other people describe does not exist for me.",
        ],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert ISSUE_URL in out
    created = runner.ran("gh", "issue", "create")[0]
    assert "--label" in created
    assert created[created.index("--label") + 1] == "access-request"


def test_a_label_the_repository_does_not_carry_still_files_the_ask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: let the failed create stand, so the ask is never filed.

    "Add information, remove gates." A label that does not exist yet is a property of the
    repository's settings and says nothing about whether the ask is worth making. Refusing to
    file it would be a gate that prevents nothing and costs the one thing this verb exists to
    produce. Filed unlabelled, with the label named on stderr so somebody can add it.
    """
    runner = ask_runner(labelled_ok=False)

    code, out, err = invoke(
        [
            "ask",
            "--kind",
            "access-request",
            "--title",
            "I cannot see the submission form",
            "--detail",
            "The Actions page other people describe does not exist for me.",
        ],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert ISSUE_URL in out
    assert "access-request" in err
    assert len(runner.ran("gh", "issue", "create")) == 2


def test_the_body_carries_the_environment_the_ask_was_made_from() -> None:
    """Mutation: send the detail and nothing else.

    Half the asks this form receives are about a refusal, and the first three questions
    anybody answering one has to ask are which edullm, which reviewed configuration, and who.
    A stale install checking against a frozen config copy is the commonest cause of a refusal
    that looks wrong, and it is invisible from the sentence somebody types.
    """
    body = issue_body(
        detail="The Actions page other people describe does not exist for me.",
        submitter="caiiris",
        version="1.4.0",
        config_directory=str(CONFIG_DIR),
        run_id=None,
    )

    assert "The Actions page other people describe does not exist for me." in body
    assert "caiiris" in body
    assert "1.4.0" in body
    assert str(CONFIG_DIR) in body


def test_an_ask_with_no_detail_is_refused_before_anything_is_filed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: file an empty ask and let the human chase it.

    An ask with a title and nothing under it costs the person answering a round trip, and
    the person answering is the owner. This is the one gate in this verb worth having, and it
    costs the caller one flag rather than a day.
    """
    runner = ask_runner()

    code, out, err = invoke(
        ["ask", "--kind", "run-problem", "--title", "it broke"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED, out + err
    assert "no_ask_detail" in err
    assert runner.calls == []


def test_a_refused_ask_is_a_document_when_json_is_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: put the refusal on stderr under --json, as prose."""
    code, out, err = invoke(
        ["ask", "--kind", "run-problem", "--title", "it broke", "--json"],
        runner=ask_runner(),
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED, out + err
    assert json.loads(out)["refusals"][0]["code"] == "no_ask_detail"


def test_an_issues_endpoint_that_will_not_answer_is_exit_three(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: read a failed create as a refusal of the ask.

    GitHub not accepting an issue says nothing about whether the ask is a good one, and a
    caller told "refused" edits something. Exit 3 is the class for a platform that could not
    be asked, and it is the one worth retrying.
    """
    code, out, err = invoke(
        [
            "ask",
            "--kind",
            "feedback",
            "--title",
            "the refusal was confusing",
            "--detail",
            "It named a file I do not have.",
        ],
        runner=ask_runner(create_ok=False),
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == 3, out + err
