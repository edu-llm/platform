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

import itertools
import json
import shlex
import time
from pathlib import Path

import pytest

from edullm_platform.cli.actions import PLATFORM_REPOSITORY, SUBMIT_WORKFLOW
from edullm_platform.cli.configuration import load_reviewed_configuration
from edullm_platform.cli.main import EXIT_OK, EXIT_REFUSED
from edullm_platform.cli.presentation import who_may_release
from edullm_platform.cli.release import install_command, installed_version
from edullm_platform.contracts.admission import ApprovalEnvironment
from edullm_platform.submission import SubmissionInputs
from tests.cli_support import (
    CONFIG_DIR,
    PROJECT_ROOT,
    TRAINING_COMMAND,
    FakeRunner,
    failed,
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

#: The endpoint the version probe reads, spelled as the prefix ``FakeRunner`` matches on.
RELEASES = ("gh", "api", f"repos/{PLATFORM_REPOSITORY}/releases/latest")


def submitting(
    tmp_path: Path,
    *,
    compiled: dict[str, object] | None = None,
    release: object | None = None,
    compiled_after: int = 0,
    run_status: str | None = None,
    **spec: object,
) -> tuple[Path, FakeRunner]:
    """A checkout that can submit, with GitHub answering as it does after a dispatch.

    ``compiled_after`` is how many times the artifact download fails before it succeeds,
    which is the only way to express the thing that actually happens: the compile job takes
    about two minutes and the artifact does not exist until it has finished. The default of
    zero -- available on the first ask -- is a state no real submission is ever in.
    """
    write_spec(tmp_path, **spec)  # type: ignore[arg-type]
    answers = dict(git_answers(tmp_path))
    answers[("gh", "workflow", "run")] = ok("")
    answers[("gh", "api")] = ok(json.dumps({"workflow_runs": [WORKFLOW_RUN]}))
    if run_status is not None:
        answers[("gh", "api", f"repos/{PLATFORM_REPOSITORY}/actions/runs/{WORKFLOW_RUN['id']}")] = (
            ok(json.dumps({"status": run_status, "conclusion": "failure"}))
        )
    # The suite runs from a checkout, where ``installed_version`` finds no distribution and
    # nothing can be stale. Answering the probe with the current release by default keeps
    # every other test in this file about the thing it is about.
    answers[RELEASES] = release if release is not None else ok(_current_release())
    asked = itertools.count()

    def download(argv: tuple[str, ...]) -> object:
        from tests.cli_support import failed

        if compiled is None or next(asked) < compiled_after:
            return failed("no artifact matches")
        directory = Path(argv[argv.index("--dir") + 1])
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "compiled-submission.json").write_text(
            json.dumps(compiled), encoding="utf-8"
        )
        return ok("")

    answers[("gh", "run", "download")] = download  # type: ignore[assignment]
    return tmp_path, FakeRunner(answers)


def _current_release() -> str:
    """Whatever this install would call itself, so the default probe answer is "current"."""
    installed = installed_version()
    version = installed.version or "0.0.0"
    return f"v{version}\t2026-08-04T00:00:00Z\n"


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
    """Mutation: ``" ".join`` it, which is what this did until it was run against AWS.

    The compile job POSIX-splits the text box, so the only thing that matters is which
    words it recovers. ``" ".join`` drops the quoting that groups ``bash -lc``'s program
    into a single word, so the three words this spec carries arrive as five and the compile
    job refuses the submission for giving ``-lc`` more than one command -- after
    ``edullm check`` exited 0 saying there were no refusals. Every ``bash -lc`` line in the
    guides is that shape, and so is the spec ``edullm check`` writes for OLMo-core, so the
    CLI could not submit its own scaffold.

    What this used to assert, and why it was not enough. It read the wire text for
    ``'$EDULLM_CHECKPOINT_DIR'`` on the theory that ``shlex.join`` single-quotes every word
    and kills the expansion. It does not reach that far here: the variable sits inside
    ``bash -lc``'s one word, so quoting wraps the whole program and the split on the far
    side hands it back with its double quotes intact. Both of those assertions are kept
    below and both pass, which is the point -- they passed while the round trip was broken.
    The word count is what fails.
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

    recovered = shlex.split(command)
    assert recovered == shlex.split(TRAINING_COMMAND), (
        "the words the compile job recovers are not the words the spec declared, so the "
        "submission it judges is not the one edullm check priced"
    )
    assert recovered[:2] == ["bash", "-lc"] and len(recovered) == 3, (
        f"bash -lc reads one word as its program and this gives it {len(recovered) - 2}, "
        "which is the refusal the compile job raises after check has already exited 0"
    )


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


def test_submit_waits_for_the_run_id_the_way_its_help_says_it_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: ask the compile job once and print "compiling" on ``None``, which shipped.

    ``submit --help`` says it waits for the run id the compile job mints unless ``--no-wait``
    says not to. It asked once, immediately after finding the workflow run, and the compile
    job takes about two minutes -- so three real submissions returned in 3.8s, 7.5s and 8.4s
    with no run id, and ``--no-wait`` was a flag that changed nothing.

    What is lost is not only the id. The approval sentence below it -- which gate holds the
    run and how many people can release it -- is printed on the same branch, so on every real
    submission the submitter was told neither what their run was called nor whether anybody
    had to release it.

    ``compiled_after`` is the point of the case. The shipped test for this line had the
    artifact available on the first ask, which is a state no submission is ever in, so it
    passed and could not have failed.
    """
    monkeypatch.setattr(time, "sleep", lambda _: None)
    root, runner = submitting(
        tmp_path,
        compiled_after=3,
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
    assert "compiling." not in out
    # Asked more than once, which is the whole of the fix.
    assert len(runner.ran("gh", "run", "download")) > 1


def test_no_wait_still_returns_without_asking_the_compile_job_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: wait whatever ``--no-wait`` says.

    The flag was documented and inert, and making the default wait is what gives it a
    meaning. It has to keep the one it was given: dispatch, say where to look, return. An
    agent in a loop and a submitter dispatching forty sweep arms both depend on it, and a
    two minute wait forty times over is an hour and twenty minutes of nothing.
    """
    root, runner = submitting(tmp_path, compiled_after=3, compiled={"run_id": "run_0198"})

    code, out, err = invoke(
        ["submit", "--no-wait", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert "not waiting" in out
    assert runner.ran("gh", "run", "download") == []


def test_a_compile_job_that_published_nothing_is_not_waited_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: poll the whole window whenever the artifact is absent.

    ``compiled_submission`` answers ``None`` while the compile job is running and ``None``
    where it refused, so a poll that reads only the artifact spends its entire ceiling on a
    submission that was turned away in twenty seconds -- and then reports it as still
    compiling, which is the opposite of what happened.

    The run's own status settles it. The artifact is published by a job inside the run, so a
    run that has reached ``completed`` with no artifact is one that is never going to have
    one. Said as what is known -- nothing was published -- rather than as a reason, because
    a cancelled run reaches the same place and was not refused.
    """
    monkeypatch.setattr(time, "sleep", lambda _: None)
    root, runner = submitting(tmp_path, compiled=None, run_status="completed")

    code, out, err = invoke(
        ["submit", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert "published no" in out + err
    # Two asks at most: one before the status was known, one to learn it.
    assert len(runner.ran("gh", "run", "download")) <= 2


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


@pytest.mark.parametrize(
    ("approval_class", "environment"),
    [("routine", "run-approval-lead"), ("exception", "run-approval-admin")],
)
def test_how_many_can_release_is_counted_off_the_roster_for_the_gate_that_holds_it(
    approval_class: str,
    environment: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**One sentence for two gates, and it was wrong by seven at the expensive one.**

    This line said "Any of the nine approvers can release it" whatever the class. Nine is the
    size of ``admins`` unioned with ``team_leads``, so it happened to describe
    ``run-approval-lead`` on the day it was typed; ``run-approval-admin`` asks only the
    admins. Exception runs are disproportionately the owner's, because he is the one
    submitting on hardware that costs more than a lead may release, so the gate the number
    was wrong about is the gate he reads.

    Mutation: write either count down, or count one of the two lists. Both counts come from
    ``holds_routine_approver_role`` and ``holds_exception_approver_role`` applied to the
    roster, which is what admission applies inside AWS -- and the routine one is a union that
    no field in ``config/policy.yaml`` states, so a reader counting ``team_leads`` alone lands
    two short.
    """
    inventory = load_reviewed_configuration(CONFIG_DIR).inventory
    expected = len(who_may_release(inventory, ApprovalEnvironment(environment)))
    root, runner = submitting(
        tmp_path,
        compiled={
            "run_id": "run_019fd2a1-4e07-7a3c-9d55-1b2f8c0e6a41",
            "approval_class": approval_class,
            "approving_environment": environment,
        },
    )

    code, out, err = invoke(
        ["submit", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )
    said = " ".join(out.split())

    assert code == EXIT_OK, out + err
    assert f"waiting at {environment}. {expected} people hold the role {environment}" in said
    # The gate's own reviewer list is a GitHub setting in the organization and is in no file
    # here, so the count is the roster's answer and has to be readable as the roster's.
    assert "is a GitHub setting rather than reviewed configuration" in said


def test_the_two_gates_are_not_told_the_same_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tripwire's own tripwire: a derivation that ignored the gate would pass above.

    Mutation: derive the count from the roster and not from the environment. Both cases above
    would still hold, because both would be told whichever single number the derivation
    produced, and the defect being fixed is precisely one number told to two gates.
    """
    inventory = load_reviewed_configuration(CONFIG_DIR).inventory
    lead = who_may_release(inventory, ApprovalEnvironment.LEAD)
    admin = who_may_release(inventory, ApprovalEnvironment.ADMIN)

    assert len(admin) < len(lead)
    assert set(admin) < set(lead)
    assert who_may_release(inventory, ApprovalEnvironment.AUTOMATIC) == ()


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


# ---------------------------------------------------------------------------------------
# the version probe
# ---------------------------------------------------------------------------------------
#
# ONE gh CALL, HERE AND NOWHERE ELSE. The reviewed configuration travels inside the wheel,
# `config/` took 55 commits in the last thirty days, and the direction that costs money is
# real: #188 withdrew two H100 shapes, so a CLI from before it prices an H100 run, calls it
# valid, and spends a lead's approval on a submission admission then refuses. This is the
# one moment in the CLI's life where that is worth an API call, because it is the one
# moment where being wrong costs somebody else's attention.


def test_a_stale_install_is_warned_and_dispatched_anyway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**WARN, NOT REFUSE, AND THE DISPATCH IS THE ASSERTION.**

    Mutation: refuse on staleness. Releases are cut per merge touching the CLI or the
    configuration, so being behind is the ordinary state of every install within a day --
    and a refusal that fires on the ordinary state is one everybody routes around, at which
    point it protects nobody and has cost somebody a submission at a bad hour. The probe
    also fails open by requirement, so a gate here would advertise an enforcement that
    being offline defeats, over a check admission makes again inside AWS regardless.

    The warning carries the ``--force`` install line and must not carry the other one:
    ``uv tool upgrade`` answers ``Nothing to upgrade`` for a git-installed tool, so a
    warning suggesting it would send the reader away believing they had fixed this.
    """
    root, runner = submitting(
        tmp_path, release=ok("v99.0.0\t2026-07-01T00:00:00Z\n")
    )

    code, _, err = invoke(
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

    assert code == EXIT_OK
    assert len(runner.ran("gh", "workflow", "run")) == 1
    assert "v99.0.0" in err
    assert install_command(repository=PLATFORM_REPOSITORY, tag="v99.0.0") in err
    assert "uv tool upgrade" not in err


def test_the_probe_runs_before_the_dispatch_and_not_after_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: ask afterwards.

    The warning is about a submission that is about to be made. Printed after the dispatch
    it is a fact about one that already was, arriving under the run url, where the reader
    has what they came for and has stopped reading.
    """
    root, runner = submitting(tmp_path, release=ok("v99.0.0\t2026-07-01T00:00:00Z\n"))

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

    order = [argv[:3] for argv in runner.calls if argv[0] == "gh"]
    assert order.index(RELEASES) < order.index(("gh", "workflow", "run"))


def test_a_probe_that_cannot_be_answered_still_dispatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: raise, or return non-zero, when the probe fails.

    A validator that stops working on a train is worse than one that is occasionally
    stale, and a repository with no releases yet answers 404 -- which is the state this
    one is in until the first tag is cut, so the day-one behaviour is the failure path.
    """
    root, runner = submitting(tmp_path, release=failed("gh: Not Found (HTTP 404)"))

    code, _, err = invoke(
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

    assert code == EXIT_OK
    assert len(runner.ran("gh", "workflow", "run")) == 1
    assert "404" in err


def test_a_submission_refused_locally_is_not_worth_a_probe_either(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The existing "no gh at all" property, restated now that there is a second gh call.

    Mutation: probe before the local checks. Nothing is being dispatched, so there is no
    approval to protect, and the researcher is about to be handed a wall of refusals with
    a note about a newer version on top of it.
    """
    root, runner = submitting(tmp_path)

    code, _, _ = invoke(
        ["submit", "--dataset", "math-frontload-100m", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert runner.ran("gh") == []
