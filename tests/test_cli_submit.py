"""``edullm submit``: what reaches the form, and what stops it reaching the form at all.

THE FORM MAPPING IS THE PART WORTH TESTING HARDEST, because nothing downstream can notice
it being wrong. ``submit-run.yml``'s ``workflow_dispatch`` accepts any field name and
silently ignores one it does not declare, so a CLI sending ``dataset`` where the form says
``dataset_release`` dispatches successfully, compiles against a missing required field and
refuses -- with a message about the form rather than about the typist.

Nothing here reaches GitHub. ``FakeRunner`` answers ``gh`` and raises on anything it was
not told about, which is also what holds the second property this file cares about: a
submission refused locally must not be dispatched, and the way to assert that is that no
``gh`` command was run at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edullm_platform.cli.actions import SUBMIT_WORKFLOW
from edullm_platform.cli.main import EXIT_OK, EXIT_REFUSED
from edullm_platform.submission import SubmissionInputs
from tests.cli_support import (
    PROJECT_ROOT,
    FakeRunner,
    git_answers,
    invoke,
    ok,
    write_spec,
)

WORKFLOW_RUN = {
    "id": 19407766,
    "status": "waiting",
    "conclusion": None,
    "created_at": "2099-01-01T00:00:00Z",
    "html_url": "https://github.com/edu-llm/platform/actions/runs/19407766",
}


def submitting(
    tmp_path: Path,
    *,
    compiled: dict[str, object] | None = None,
    **spec: object,
) -> tuple[Path, FakeRunner]:
    """A checkout that can submit, with GitHub answering as it does after a dispatch."""
    write_spec(tmp_path, **spec)  # type: ignore[arg-type]
    answers = dict(git_answers(tmp_path))
    answers[("gh", "workflow", "run")] = ok("")
    answers[("gh", "api")] = ok(json.dumps({"workflow_runs": [WORKFLOW_RUN]}))

    def download(argv: tuple[str, ...]) -> object:
        if compiled is None:
            from tests.cli_support import failed

            return failed("no artifact matches")
        directory = Path(argv[argv.index("--dir") + 1])
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "compiled-submission.json").write_text(
            json.dumps(compiled), encoding="utf-8"
        )
        return ok("")

    answers[("gh", "run", "download")] = download  # type: ignore[assignment]
    return tmp_path, FakeRunner(answers)


def dispatched_fields(runner: FakeRunner) -> dict[str, str]:
    """The ``-f name=value`` pairs the CLI handed ``gh workflow run``."""
    argv = runner.ran("gh", "workflow", "run")[0]
    fields: dict[str, str] = {}
    for index, word in enumerate(argv):
        if word == "-f":
            name, _, value = argv[index + 1].partition("=")
            fields[name] = value
    return fields


def test_every_field_the_form_declares_is_sent_under_the_name_the_form_declares(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mapping, checked against ``SubmissionInputs`` rather than against a list here.

    Mutation: rename one field. The model is what the compile job validates the form
    against, so comparing to its own field names is the only check that stays true when
    somebody adds a sixteenth input -- and a list written out here would be a second roster
    of the form that agrees with it until the next change.
    """
    root, runner = submitting(tmp_path, workload="olmo-core-check", compute="gpu-1xt4")

    code, out, err = invoke(
        [
            "submit",
            "--dataset",
            "none",
            "--experiment",
            "an-experiment",
            "--no-wait",
        ],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    fields = dispatched_fields(runner)
    assert set(fields) <= set(SubmissionInputs.model_fields)
    required = {
        name
        for name, field in SubmissionInputs.model_fields.items()
        if field.is_required() and name != "command"
    }
    assert required <= set(fields)
    assert fields["dataset_release"] == "none"
    assert fields["workload_profile"] == "olmo-core-check"
    assert fields["compute_profile"] == "gpu-1xt4"


def test_the_command_reaches_the_form_as_text_a_shell_split_would_recover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: ``shlex.join`` it.

    The compile job POSIX-splits the text box, and joining with ``shlex`` single-quotes
    every word -- so ``"$EDULLM_CHECKPOINT_DIR"`` arrives as ``'$EDULLM_CHECKPOINT_DIR'``,
    which no shell expands. OLMo-core is then handed twenty-two literal characters as a save
    folder and cheerfully creates a directory with that name.
    """
    root, runner = submitting(tmp_path)

    invoke(
        [
            "submit",
            "--dataset",
            "regmix-10b-v1",
            "--experiment",
            "an-experiment",
            "--no-wait",
        ],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    command = dispatched_fields(runner)["command"]
    assert '"$EDULLM_CHECKPOINT_DIR"' in command
    assert "'$EDULLM_CHECKPOINT_DIR'" not in command


def test_no_image_digest_is_sent_so_the_workflow_derives_it_from_the_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: send the placeholder the local checks built a manifest with.

    That digest names nothing. The form's image field is an override for a deliberate
    rebuild-and-pin and is checked against what the declared commit published, so a value
    invented here is an override aimed at an image that does not exist -- and the refusal
    would be about a pin nobody asked for.
    """
    root, runner = submitting(tmp_path)

    invoke(
        [
            "submit",
            "--dataset",
            "regmix-10b-v1",
            "--experiment",
            "an-experiment",
            "--no-wait",
        ],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert "image_digest" not in dispatched_fields(runner)


def test_a_submission_the_local_checks_refuse_is_not_dispatched_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of the whole binary, asserted as the absence of a call.

    Mutation: dispatch and let admission refuse it. That is a queue wait, a lead's attention
    where the class is routine, and the same answer.
    """
    root, runner = submitting(tmp_path)

    code, _, err = invoke(
        ["submit", "--dataset", "math-frontload-100m", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert "refused  unregistered_dataset" in err
    assert runner.ran("gh") == []


def test_force_dispatches_over_a_local_refusal_and_says_it_is_doing_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The escape hatch, and why it has to be loud.

    Mutation: make it silent, or remove it. A guard with no way out is one people get around
    by editing the check, and a guard that lets somebody past without saying so turns a
    deliberate override into a submission nobody remembers making.
    """
    root, runner = submitting(tmp_path)

    code, _, err = invoke(
        [
            "submit",
            "--dataset",
            "math-frontload-100m",
            "--experiment",
            "an-experiment",
            "--force",
            "--no-wait",
        ],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK
    assert "--force: dispatching over 1 local refusal(s)." in err
    assert len(runner.ran("gh", "workflow", "run")) == 1


def test_the_run_id_the_compile_job_issued_is_read_back_and_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print the workflow run url and stop.

    The run id is what ``status``, ``logs`` and ``cancel`` take, what names the Batch job
    and the S3 prefix, and what somebody quotes when they ask for help. A submitter who is
    given only an Actions url has to go and find it on a page.
    """
    root, runner = submitting(
        tmp_path,
        compiled={
            "run_id": "run_019fd2a1-4e07-7a3c-9d55-1b2f8c0e6a41",
            "approval_class": "routine",
            "approving_environment": "run-approval-lead",
        },
    )

    code, out, err = invoke(
        ["submit", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert "run_019fd2a1-4e07-7a3c-9d55-1b2f8c0e6a41" in out
    assert "waiting at run-approval-lead" in out


def test_an_automatic_submission_is_told_nobody_is_waiting_on_a_person(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print the same "waiting at" line for every class.

    The automatic gate carries a branch policy and no reviewers, so there is nobody to wait
    for -- and a submitter told to wait for one goes and messages a lead about a run that
    already started.
    """
    root, runner = submitting(
        tmp_path,
        compiled={
            "run_id": "run_019ff5e1-0a2c-7f83-b615-88d40e97a4c2",
            "approval_class": "automatic",
            "approving_environment": "run-approval-automatic",
        },
    )

    code, out, _ = invoke(
        ["submit", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK
    assert "released automatically. Nothing is waiting on a person." in out


def test_the_workflow_it_dispatches_is_the_one_the_trust_policy_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: dispatch anything else.

    The admission role's trust policy pins ``job_workflow_ref`` to this exact path with
    ``StringEquals``, so the same attempt made from another workflow is refused for being
    the wrong file -- a refusal that says nothing about the submission.
    """
    root, runner = submitting(tmp_path)

    invoke(
        [
            "submit",
            "--dataset",
            "regmix-10b-v1",
            "--experiment",
            "an-experiment",
            "--no-wait",
        ],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    argv = runner.ran("gh", "workflow", "run")[0]
    assert argv[3] == SUBMIT_WORKFLOW
    # And that name is a file in this repository rather than a string agreeing with itself.
    assert (PROJECT_ROOT / ".github" / "workflows" / SUBMIT_WORKFLOW).is_file()
