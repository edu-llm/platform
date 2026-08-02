"""The rule this package is built around, which is that telemetry cannot fail a run.

NINE OF THE LAST SIXTY-SEVEN JOB FAILURES IN THE RETAINED WINDOW WERE A W&B CREDENTIAL
PROBLEM. Eight of those nine were filed as distributed training bugs, because the log tail
showed ``ProcessGroup is not registered`` from torch distributed about twenty lines after
the real cause, which was ``CommError: user is not logged in``. The first exception tore
down a process group the ranks were still using, and everyone read the second message.

Every test below is a way that could happen again. They are written against a fake W&B
module rather than the real one for two reasons. ``wandb`` is an optional dependency of
``edullm-client`` and is deliberately not installed in this virtualenv, which is what makes
the absent-library case here the real thing. And the failures worth covering are a bad
credential and an unreachable service, neither of which a test may go and produce.

**None of these assert on the return value alone.** A function that returned ``None``
correctly and re-raised on the way out would pass a weaker test, so what is asserted is that
the caller keeps running.
"""

from __future__ import annotations

import logging
import sys
from types import ModuleType
from typing import Any

import pytest
from edullm_client import (
    RunEnvironment,
    finish_wandb,
    start_wandb,
    start_wandb_for,
    wandb_log,
)

#: The exact message a run with a bad key dies on, before torch distributed buries it.
LOGIN_FAILURE = "CommError: user is not logged in"


class FakeRun:
    """A W&B handle that records what it was told, or refuses everything.

    One class for both, because the interesting assertion is that the caller cannot tell
    the difference, and two classes would let a helper accidentally be written against only
    the cooperative one.
    """

    def __init__(self, *, failing: bool = False) -> None:
        self.failing = failing
        self.logged: list[tuple[dict[str, Any], int | None]] = []
        self.finished_with: list[int | None] = []

    def log(self, data: dict[str, Any], step: int | None = None) -> None:
        if self.failing:
            raise RuntimeError("Network error (ConnectionError), entering retry loop")
        self.logged.append((data, step))

    def finish(self, exit_code: int | None = None) -> None:
        if self.failing:
            raise RuntimeError("Error uploading run history")
        self.finished_with.append(exit_code)


def install_wandb(monkeypatch: pytest.MonkeyPatch, init: Any) -> ModuleType:
    """Put a module named ``wandb`` where the import inside ``start_wandb`` will find it."""
    module = ModuleType("wandb")
    module.init = init  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "wandb", module)
    return module


def no_wandb(monkeypatch: pytest.MonkeyPatch) -> None:
    """A container with no ``wandb`` installed, forced rather than assumed.

    ``None`` in ``sys.modules`` makes ``import wandb`` raise ``ImportError`` whatever is on
    disk. Written this way rather than relying on the library being absent from this
    virtualenv, because somebody running ``uv sync --all-extras`` would otherwise turn this
    test green for the wrong reason.
    """
    monkeypatch.setitem(sys.modules, "wandb", None)


def environment(**changes: str | None) -> RunEnvironment:
    fields: dict[str, Any] = {
        "run_id": "run_019fbddb-5125-7045-95aa-1951e5ca3f31",
        "team": "memory-split",
        "commit_sha": "298afac6e1e4a5b6c7d8e9f0a1b2c3d4e5f60718",
        "dataset_release": "regmix-10b-v1",
        "output_bucket": "sbsandbox-intern-edullm-outputs",
        "output_prefix": "s3://sbsandbox-intern-edullm-outputs/teams/memory-split/runs/r/",
        "checkpoint_dir": "s3://sbsandbox-intern-edullm-outputs/teams/memory-split/runs/r/c/",
        "wandb_project": "olmo-core-memory-split",
        "wandb_entity": "eduLLM",
        "experiment": "an-ablation",
    }
    fields.update(changes)
    return RunEnvironment(**fields)


def test_a_run_whose_key_is_not_logged_in_gets_no_handle_and_keeps_its_machine(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """THE ONE THAT MATTERS. Mutation: let the exception out of ``start_wandb``.

    This is the failure the whole package is shaped around. With the exception propagating,
    a twelve-hour training job on eight H100s ends in its first seconds, and it ends with a
    message about a process group rather than about a login. Nine jobs in the retained
    window died this way and eight were investigated as the wrong bug.

    The warning is asserted as well as the return, and the assertion is on the cause rather
    than on the wording. A warning that says W&B is unavailable and does not say why leaves
    the same log tail that started this, with one fewer exception in it.
    """

    def refuse(**_: Any) -> Any:
        raise RuntimeError(LOGIN_FAILURE)

    install_wandb(monkeypatch, refuse)

    with caplog.at_level(logging.WARNING, logger="edullm_client"):
        handle = start_wandb(run_id="run_1", project="a-project")

    assert handle is None
    assert LOGIN_FAILURE in caplog.text, (
        "the warning does not carry the reason W&B refused, so the log tail says only that "
        "telemetry is off and the next reader diagnoses whatever failed twenty lines later"
    )


def test_a_container_with_no_wandb_installed_is_an_ordinary_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: move the import to module scope.

    A CPU tokenization job installs the path helpers and neither optional library, which is
    the case the empty dependency list exists for. An import at the top of ``telemetry`` is
    an ImportError raised by ``import edullm_client`` itself, so the job fails on a line
    nothing in it wrote, before reaching any code of its own.
    """
    no_wandb(monkeypatch)

    assert start_wandb(run_id="run_1", project="a-project") is None


def test_a_run_that_starts_is_named_and_grouped_the_way_everything_else_names_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: pass the run id as ``id`` rather than ``name``, or drop the group.

    The run id is the one string that joins the Batch job, the S3 prefix and the W&B run, so
    a W&B run under any other name is one that cannot be matched to its outputs afterwards.
    W&B mints an id of its own for the URL and it is not this one, which is why the id goes
    in the name and not in the id.
    """
    calls: list[dict[str, Any]] = []

    def record(**keywords: Any) -> FakeRun:
        calls.append(keywords)
        return FakeRun()

    install_wandb(monkeypatch, record)

    handle = start_wandb(run_id="run_1", project="a-project", group="an-ablation")

    assert isinstance(handle, FakeRun)
    assert calls == [
        {
            "project": "a-project",
            "entity": "eduLLM",
            "name": "run_1",
            "group": "an-ablation",
        }
    ]


def test_the_one_line_form_takes_all_four_values_off_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: hard-code the entity, or read the project from the wrong field.

    Passing the four by hand is how a run ends up named after its command, grouped under
    nothing, or logged to an entity the injected key cannot write to. Each of those produces
    a run that exists and that nobody looking for it will find, which is worse than no run.
    """
    calls: list[dict[str, Any]] = []
    install_wandb(monkeypatch, lambda **keywords: calls.append(keywords))

    start_wandb_for(environment())

    assert calls == [
        {
            "project": "olmo-core-memory-split",
            "entity": "eduLLM",
            "name": "run_019fbddb-5125-7045-95aa-1951e5ca3f31",
            "group": "an-ablation",
        }
    ]


def test_a_run_with_no_experiment_is_started_ungrouped_rather_than_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A manifest compiled before the experiment field existed carries no group.

    ``wandb.init`` takes ``group=None`` and means it, so the absent case needs no branch
    here. Asserted because the obvious alternative, omitting the keyword, is a change nobody
    would notice until a run appeared in the wrong grouping.
    """
    calls: list[dict[str, Any]] = []
    install_wandb(monkeypatch, lambda **keywords: calls.append(keywords))

    start_wandb_for(environment(experiment=None))

    assert calls[0]["group"] is None


def test_logging_to_a_run_that_was_never_opened_does_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Mutation: drop the ``None`` guard and let the AttributeError out.

    ``start_wandb`` returning ``None`` puts the burden on the caller, and the caller writes
    the guard at the top where the handle is obtained rather than in the training loop where
    it is used. This is what makes that safe, and it is silent on purpose. One warning per
    logged step is a run whose log is the warning.
    """
    with caplog.at_level(logging.WARNING, logger="edullm_client"):
        wandb_log(None, {"loss": 2.4}, step=100)
        finish_wandb(None)

    assert caplog.text == ""


def test_a_run_that_breaks_partway_through_does_not_take_the_training_with_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Mutation: guard ``None`` and let a live handle's exceptions propagate.

    W&B buffers and flushes on a thread of its own, so a call can raise long after the
    credential that broke was accepted. A job that trains correctly for six hours and dies
    logging a scalar at step 40,000 is the same defect as the login one wearing a different
    clock, and it is the more expensive of the two.
    """
    failing = FakeRun(failing=True)

    with caplog.at_level(logging.WARNING, logger="edullm_client"):
        wandb_log(failing, {"loss": 2.4}, step=100)
        finish_wandb(failing, exit_code=0)

    assert "W&B log failed" in caplog.text
    assert "W&B finish failed" in caplog.text


def test_a_whole_training_loop_completes_against_a_wandb_that_refuses_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The proof, rather than three assertions about three functions.

    Each of the calls below is a place a run has died in this account. Written as one
    sequence because the property being asserted is about the program and not about any of
    them individually. What must be true is that a job whose telemetry is broken end to end
    still trains, still checkpoints, and still returns its result, and no test of a single
    function can say that.
    """

    def refuse(**_: Any) -> Any:
        raise RuntimeError(LOGIN_FAILURE)

    install_wandb(monkeypatch, refuse)
    steps_completed = 0

    run = environment()
    handle = start_wandb_for(run)
    for step in range(4):
        steps_completed += 1
        wandb_log(handle, {"loss": 1.0 / (step + 1)}, step=step)
    finish_wandb(handle, exit_code=0)

    assert handle is None
    assert steps_completed == 4


def test_a_stop_signal_is_not_swallowed_by_the_thing_that_swallows_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deliberate limit of the guarantee, asserted so that it stays deliberate.

    ``except Exception`` was chosen over ``except BaseException``, and the difference is
    reachable rather than theoretical. Batch stops a cancelled job by signalling it, and a
    module that caught ``KeyboardInterrupt`` would make the first stop request a no-op. An
    uncancellable job on a GPU queue costs more than a missing chart, which is the same
    trade this file makes everywhere else, pointing the other way.

    Widening the catch would turn this red, which is the intent. If it ever should be
    widened, this test is where the argument for it goes.
    """
    def interrupt(**_: Any) -> Any:
        raise KeyboardInterrupt

    install_wandb(monkeypatch, interrupt)

    with pytest.raises(KeyboardInterrupt):
        start_wandb(run_id="run_1", project="a-project")
