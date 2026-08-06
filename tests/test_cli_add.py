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
import yaml

from edullm_platform.cli.actions import REGISTER_WORKFLOW
from edullm_platform.cli.intake import ADD_KINDS, SELF_SERVICE_KINDS, register_repository_form
from edullm_platform.cli.main import EXIT_OK, EXIT_REFUSED, EXIT_UNREACHABLE, NOT_BUILT_YET
from tests.cli_support import FakeRunner, failed, git_answers, invoke, ok

REGISTER_RUNS = """{"workflow_runs": [
  {"id": 77, "status": "queued", "conclusion": null,
   "created_at": "2099-01-01T00:00:00Z",
   "html_url": "https://github.com/edu-llm/platform/actions/runs/77"}
]}"""


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


def register_runner(tmp_path: Path, *, dispatch_ok: bool = True) -> FakeRunner:
    """A checkout of an unregistered codebase, with GitHub answering both calls it makes.

    The `("gh", "api")` entry is one callable rather than a key per endpoint for the reason
    `status_runner` in tests/test_cli_machine_output.py gives at length. `workflow_runs`
    appends a query string, so an exact key naming the bare path matches nothing.
    """

    def api(argv: tuple[str, ...]) -> object:
        path = argv[-1]
        if f"/workflows/{REGISTER_WORKFLOW}/runs" in path:
            return ok(REGISTER_RUNS)
        if path.endswith("/a-new-codebase"):
            return ok('{"id": 90210}')
        return ok("{}")

    answers: dict[tuple[str, ...], object] = dict(
        git_answers(tmp_path, repository="a-new-codebase")
    )
    answers[("gh", "workflow", "run", REGISTER_WORKFLOW)] = (
        ok("") if dispatch_ok else failed("HTTP 403: Resource not accessible by integration")
    )
    answers[("gh", "api")] = api
    return FakeRunner(answers)  # type: ignore[arg-type]


def test_the_workflow_name_is_the_one_that_exists() -> None:
    """Mutation: spell it register_repository.yml, or point it at submit-run.yml.

    The constant is a copy of a filename in .github/workflows/, which is the same seam
    ADMISSION_JOB and PLATFORM_REPOSITORY sit on. Read off the directory rather than
    asserted equal to itself, so a rename of the file is a red test rather than a dispatch
    that 404s in front of a researcher.
    """
    workflows = Path(__file__).resolve().parents[1] / ".github" / "workflows"

    assert (workflows / REGISTER_WORKFLOW).is_file()


def test_the_form_carries_every_required_input_the_workflow_declares() -> None:
    """Mutation: leave `reason` out, because it has no obvious value to fill in.

    A workflow_dispatch with a required input missing is refused by GitHub with a message
    about inputs, which reaches a researcher as a failed dispatch and tells them nothing.
    The three required inputs are read out of the workflow rather than listed here, so an
    input added there fails this rather than failing a person.
    """
    workflow = yaml.safe_load(
        (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / REGISTER_WORKFLOW
        ).read_text(encoding="utf-8")
    )
    triggers = workflow.get("on", workflow.get(True))
    declared = triggers["workflow_dispatch"]["inputs"]
    required = {name for name, spec in declared.items() if spec.get("required")}

    form = register_repository_form(
        repository="a-new-codebase",
        github_repository_id="90210",
        reason="it is a codebase of its own",
        dockerfile_path=".edullm/Dockerfile",
        default_branch="main",
    )

    assert required <= set(form)
    assert set(form) <= set(declared)


def test_add_repository_dispatches_and_names_the_run_it_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: dispatch and return without finding the run.

    `gh workflow run` returns nothing that identifies what it started, so a caller that
    stopped there could only say "queued". The whole product of this verb is a pull request
    somebody has to review, and the run page is the only address it has until the pull
    request exists.
    """
    runner = register_runner(tmp_path)

    code, out, err = invoke(
        [
            "add",
            "repository",
            "--reason",
            "it is a codebase of its own",
        ],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert runner.ran("gh", "workflow", "run", REGISTER_WORKFLOW)
    assert "https://github.com/edu-llm/platform/actions/runs/77" in out


def test_add_repository_says_where_the_pull_request_gets_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last step is a person's click, and this is where that person is standing.

    The workflow writes the registration and pushes a branch, and stops there: the
    organization forbids Actions from opening a pull request, and the single setting that
    would allow it also allows approving one, which is what protects the reviewed
    configuration files a registration edits. Saying so only in the workflow log would send
    somebody out of a terminal and into the Actions UI to hunt for a link.

    It costs no extra call to GitHub, which is the reason it can be said here at all: the
    branch is derived from the repository name rather than read back off the run. What it
    cannot carry is the body, which the run composes and prints in its own summary, so the
    sentence around the link has to say that rather than imply a filled-in form.
    """
    runner = register_runner(tmp_path)

    code, out, err = invoke(
        ["add", "repository", "--reason", "it is a codebase of its own"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert "/compare/register/" in out
    assert "?expand=1" in out
    # The prose is wrapped to the terminal, so the sentences are read off one line.
    said = " ".join(out.split())
    assert "It does not open the pull request" in said
    assert "paste the body the run's summary prints" in said


def test_a_dispatch_nobody_may_make_is_exit_three_and_not_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: read a 403 as a refusal of the registration.

    GitHub refusing to run a workflow says nothing about whether the repository should be
    registered, and a caller told "refused" goes and edits something. Exit 3 is the class
    for a platform that could not be asked, and it is the one of the five worth retrying.
    It is also the wrong class for "you do not have write access", which is a known and
    accepted mismatch: correcting it would be a changed exit code and therefore a major.
    """
    code, out, err = invoke(
        ["add", "repository", "--reason", "it is a codebase of its own"],
        runner=register_runner(tmp_path, dispatch_ok=False),
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_UNREACHABLE, out + err


def test_add_repository_outside_a_checkout_is_refused_before_anything_is_dispatched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: fall back to the directory name when there is no origin remote.

    config/repositories.yaml is keyed on the GitHub name and a clone can be named anything,
    so a directory name is a guess. A registration opened against a guessed name creates an
    ECR repository, widens a publisher role and puts an entry on the submission form, all
    under a name nothing will ever match.
    """
    runner = FakeRunner(
        {("git", "rev-parse", "--show-toplevel"): failed("not a git repository")}
    )

    code, out, err = invoke(
        ["add", "repository", "--reason", "it is a codebase of its own"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED, out + err
    assert "no_origin_remote" in err or "not_a_repository" in err
    assert runner.ran("gh") == []


def test_a_registration_with_no_reason_is_refused_and_says_why_there_is_no_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: default --reason to something, or make argparse require it.

    A required flag is answered with a usage line, and a usage line does not say why the
    field exists. The reason is written into a comment above the entry and it is the only
    part of the pull request a reviewer cannot derive from the rest of it, so a refusal that
    says that is worth one exit over argparse's.
    """
    runner = register_runner(tmp_path)

    code, out, err = invoke(
        ["add", "repository"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED, out + err
    assert "no_registration_reason" in err
    assert runner.ran("gh") == []
