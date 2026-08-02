"""Weights and Biases, wired so that it cannot fail the run it is watching.

NINE OF THE LAST SIXTY-SEVEN JOB FAILURES IN THE RETAINED WINDOW WERE A W&B CREDENTIAL
PROBLEM, AND EIGHT OF THOSE NINE WERE FILED AS DISTRIBUTED TRAINING BUGS. The real cause
was ``CommError: user is not logged in``. What the log tail showed, about twenty lines
later, was ``ProcessGroup is not registered`` from torch distributed, because the first
exception tore down a process group the ranks were still using. Everybody who read the end
of the log diagnosed the second message. They were logins.

So every function in this module swallows every ``Exception`` it can raise and returns.
A run that cannot reach W&B loses its dashboard and keeps its GPU hours, and the loss of a
dashboard is recoverable in a way that the loss of eleven hours of training is not.

**What is deliberately not caught, and what that means.** ``BaseException`` passes through,
so ``KeyboardInterrupt`` and ``SystemExit`` still end the process. Catching those would make
a job that Batch is trying to stop into one that ignores the first signal, and an
uncancellable job on a GPU queue is a worse failure than a missing chart. The consequence is
honest rather than hidden. If a future version of the W&B client calls ``sys.exit`` from
inside ``init``, this module will not stop it, and the symptom would be a clean-looking exit
in the first seconds of a run.

**The complement to this is a preflight, not a stricter runtime.** The platform's
``verify_wandb_credential`` tool asks W&B who the stored key resolves to, and submission
time is where a bad key should cost two seconds and a clear refusal. Making failure loud
here instead would be paying for the same information with a GPU allocation.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from .environment import RunEnvironment

__all__ = [
    "WANDB_ENTITY",
    "finish_wandb",
    "start_wandb",
    "start_wandb_for",
    "wandb_log",
]

#: The W&B entity every run on this platform logs into, which is the parent team of the
#: service account whose key the job definition injects. Restated here rather than imported,
#: because ``edullm_platform`` is not installed in a research container. The platform's suite
#: asserts the two are equal, since a team-scoped service account can only write to its own
#: team and a client naming a different entity would authenticate and then have nowhere to
#: put anything.
WANDB_ENTITY: Final = "eduLLM"

#: The package's logger rather than this module's, so that everything the client says
#: arrives under one name a training script can raise, lower or route in one line.
#:
#: Warnings from it are visible without any configuration at all, which is the property that
#: matters here. A script that never calls ``logging.basicConfig`` still gets these on
#: stderr through the standard library's handler of last resort, so the cause of a missing
#: dashboard reaches the log tail of a job whose author never set logging up.
_log = logging.getLogger("edullm_client")


def start_wandb(
    *,
    run_id: str,
    project: str,
    entity: str = WANDB_ENTITY,
    group: str | None = None,
) -> Any | None:
    """Open a W&B run named for this job, or warn and return ``None``.

    The run's name is the run id and its group is the experiment, so the three places a run
    is findable all say the same string. Those places are W&B, the S3 prefix and the Batch
    job. Nothing is derived from the W&B side; the id is the platform's and W&B is told it,
    which is why the id goes in ``name`` and not in ``id``, since W&B mints one of its own
    for the URL and it is not this one.

    The import is inside the try alongside the call, which is the part most easily lost in a
    later tidy-up. A container without ``wandb`` installed is the ordinary state of a CPU
    tokenization job, and an ``ImportError`` at the top of this module would make that job
    fail on a line it never intended to reach.

    Returns whatever ``wandb.init`` returned, typed as ``Any`` because this package does not
    depend on ``wandb`` and therefore cannot name its ``Run``. Returns ``None`` when W&B is
    absent, unauthenticated, unreachable, or refusing for any other reason, and those cases
    are not distinguished here on purpose. Every one of them means the same thing to the
    caller, which is that there is no handle, and :func:`wandb_log` and :func:`finish_wandb`
    both take ``None`` so that no caller has to branch.
    """
    try:
        import wandb  # type: ignore[import-not-found]  # an extra, not a dependency

        return wandb.init(project=project, entity=entity, name=run_id, group=group)
    except Exception as error:  # noqa: BLE001 - telemetry must not be able to fail a run
        _log.warning("edullm: W&B unavailable, continuing without it: %s", error)
        return None


def start_wandb_for(environment: RunEnvironment) -> Any | None:
    """:func:`start_wandb` with every argument taken from the run the platform described.

    The one-line form, and the one a training script should use. Passing the four values by
    hand is how a run ends up named after its command, grouped under nothing, or logged to
    an entity the key cannot write to, which are three separate ways to produce a run that
    exists and that nobody looking for it will find.
    """
    return start_wandb(
        run_id=environment.run_id,
        project=environment.wandb_project,
        entity=environment.wandb_entity,
        group=environment.experiment,
    )


def wandb_log(run: Any | None, data: Mapping[str, Any], *, step: int | None = None) -> None:
    """Log to a run that may be ``None``, and never raise whatever happens.

    THIS EXISTS BECAUSE ``None`` IS OTHERWISE A TRAP RATHER THAN A RESULT. A caller handed
    an optional handle writes ``if run is not None`` once, at the top, and then calls
    ``run.log`` inside the training loop where the guard is not. That reintroduces exactly
    the failure :func:`start_wandb` was written to remove, at step 400 rather than at step
    zero, in a job that has already spent most of its budget.

    The second guard is the same argument applied to the network. W&B's client buffers and
    flushes on its own thread, so a call here can raise long after the credential that broke
    was accepted, and a run that trains fine for six hours and dies logging a scalar is the
    same defect wearing a different clock.
    """
    if run is None:
        return
    try:
        run.log(dict(data), step=step)
    except Exception as error:  # noqa: BLE001 - telemetry must not be able to fail a run
        _log.warning("edullm: W&B log failed, continuing without it: %s", error)


def finish_wandb(run: Any | None, *, exit_code: int | None = None) -> None:
    """Close a run that may be ``None``, and never raise whatever happens.

    Worth calling and worth being safe, for opposite reasons. Without it a run that ends
    cleanly can sit marked as running until W&B's own timeout decides otherwise, which reads
    to anybody looking as a job that hung. With it, and without this guard, the last line of
    a twelve-hour job that did everything right becomes the line that makes Batch record a
    non-zero exit.
    """
    if run is None:
        return
    try:
        run.finish(exit_code=exit_code)
    except Exception as error:  # noqa: BLE001 - telemetry must not be able to fail a run
        _log.warning("edullm: W&B finish failed, continuing without it: %s", error)
