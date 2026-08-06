"""What the platform tells a container, as an object rather than as scattered lookups.

**Where this list comes from.** ``batch_submit_request`` in
``src/edullm_platform/execution.py`` builds the environment block every submission sends,
and ``CONTAINER_SHAPES`` in the same module carries the two variables the registered job
definition declares and no submission overrides. Batch merges the two, so what a container
holds is the union, and the union is what is read here. The environment table in
``guides/the-platform.md`` documents a subset of it and is a promise to researchers rather
than the source of truth, which the platform's own ``tests/test_guides.py`` states directly
by asserting that the guide promises no variable the container is not given, and not the
reverse.

**One variable is deliberately not read.** ``WANDB_API_KEY`` reaches the container from
Secrets Manager, resolved by ECS under the execution role while the task starts. Putting it
on this object would put a live credential inside the repr of a dataclass that programs
print while debugging, and into any traceback that renders locals. The W&B client reads it
from the environment itself, so nothing here needs to hold it.

**Two more are excluded because they travel the other way.** ``EDULLM_LAUNCH_CHECK`` and
``EDULLM_CHECKPOINT_CHECK`` are waiver tokens a submitter writes into their own command to
record a decision. They are spelled like injected variables and are not ones, and a client
that surfaced them would invite a program to branch on whether its own submitter waived a
check the platform already accepted.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from .storage import DatasetLocation, resolve_dataset

__all__ = [
    "INTERPRETER_VARIABLES",
    "OPTIONAL_VARIABLES",
    "REQUIRED_VARIABLES",
    "MissingRunEnvironmentError",
    "RunEnvironment",
    "run_environment",
]

#: Set on every container, by the submit request or by the job definition it is submitted
#: against. In the order the fields below are declared, so the two read down the same way.
#:
#: ``EDULLM_OUTPUT_BUCKET`` is the odd one and is required anyway. It is the only name here
#: that no submission sends. It comes from the registered job definition's declared
#: environment, which Batch merges with the override, so a container holds it for a
#: different reason than it holds the other nine. Required rather than optional because
#: every deployed container shape declares it and a container without it is a job definition
#: that changed underneath the platform, which is worth being told about in the first second
#: rather than discovering from a write that lands somewhere unexpected.
REQUIRED_VARIABLES: Final[tuple[str, ...]] = (
    "EDULLM_RUN_ID",
    "EDULLM_TEAM",
    "EDULLM_COMMIT_SHA",
    "EDULLM_DATASET_RELEASE",
    "EDULLM_OUTPUT_BUCKET",
    "EDULLM_OUTPUT_PREFIX",
    "EDULLM_CHECKPOINT_DIR",
    "EDULLM_WANDB_PROJECT",
    "WANDB_PROJECT",
    "WANDB_ENTITY",
)

#: Sent only when there is something to say, and absent rather than empty when there is not.
#: The platform appends each of these conditionally, because an empty value reads as an
#: attribution or a resolution that was attempted and failed rather than one never made.
#:
#: ``WANDB_RUN_ID`` is here for a different reason than the rest, and it is the reason
#: ``ResultManifest.exit_code`` is optional: every submission compiled today carries one
#: unconditionally, but a container captured before the variable existed does not, and
#: those captures are records rather than fixtures -- adding the name to one would be
#: writing down something the container never held. Required would make the whole
#: captured history unreadable by the client that describes it.
OPTIONAL_VARIABLES: Final[tuple[str, ...]] = (
    "WANDB_RUN_ID",
    "WANDB_RUN_GROUP",
    "WANDB_USERNAME",
    "EDULLM_DATASET_ID",
    "EDULLM_DATASET_VERSION",
    "EDULLM_DATASET_TOKENIZER",
)

#: Set on every container and deliberately not presented as run facts, because they are not
#: facts about the run. CPython reads both of these itself, before any workload gets a say,
#: and no script has a reason to ask what they are: nothing branches on whether stdout is
#: buffered, and a program that did would be reading its own plumbing.
#:
#: LISTED RATHER THAN IGNORED, because the layout test subtracts these three tuples from
#: what the platform actually sends and reports the remainder. A name absent from all three
#: is a variable the platform added and the client does not present, which is the state this
#: package exists to end. Dropping these two into a silent exception would make that check
#: weaker for every future variable, so they are declared here and the check stays exact.
#:
#: Why the platform sets them at all is in ``edullm_platform.execution``: 10 of the 73 failed
#: runs read on 2026-08-06 are a log stream that stops mid-sentence or never starts, and both
#: are a buffer the interpreter discarded rather than a program with nothing to say.
INTERPRETER_VARIABLES: Final[tuple[str, ...]] = (
    "PYTHONUNBUFFERED",
    "PYTHONFAULTHANDLER",
)


class MissingRunEnvironmentError(RuntimeError):
    """The variables the platform sets are not all here.

    EVERY MISSING NAME AT ONCE, WHICH IS THE ONLY PART OF THIS CLASS THAT IS A DECISION. The
    obvious implementation raises on the first absent variable, and the cost of that is paid
    by the person it is least useful to. Somebody reproducing a run on a laptop exports one
    variable, reruns, is told about the next one, and pays a round trip per name; inside a
    real job the same loop costs a queue wait and an approval each time. Reporting the whole
    set is one edit to a shell profile.

    Raised rather than defaulted for the same reason ``output_prefix`` refuses to default a
    team. A client that invented a run id would produce a job writing to a location no
    lineage record names, and the platform's record of where that run wrote would be wrong
    in a store nothing rewrites.
    """


@dataclass(frozen=True)
class RunEnvironment:
    """One run's identity and locations, as the platform decided them.

    Frozen because none of it is the container's to change. The platform sends the output
    prefix rather than the parts to build it from, precisely so that the container is not
    the thing deciding where its own output goes, and a mutable copy of that decision is an
    invitation to adjust it locally and be denied by the workload role twelve hours later.

    **The four identifiers a run carries are not four fields here, and the mismatch is
    worth knowing before you go looking.** ``team`` and ``run_id`` are their own variables.
    ``experiment`` arrives as ``WANDB_RUN_GROUP``, under W&B's own name rather than a
    prefixed one, because the W&B client reads that name without being asked and a prefixed
    copy would need every workload to forward it. The submitter does not arrive at all. It
    is recorded on the Batch job as the ``edullm:submitter`` tag, which the cancel path
    reads and a container cannot see. ``wandb_username`` below is the nearest thing the
    container is given and is not the same fact, since it is a W&B account name and most of
    the roster has none.

    ``wandb_run_id`` is a fifth and is the one identifier that is usually a copy of
    another. It equals ``run_id`` on a single run, which is the whole point -- it is what
    joins a platform run to its W&B run -- and it is deliberately still its own field,
    because on a fan-out cell it carries a ``-cell-<index>`` suffix that ``run_id`` does
    not. Reading ``run_id`` where a W&B run is meant is therefore correct on every single
    run and wrong on every cell.
    """

    run_id: str
    team: str
    commit_sha: str
    #: The identifier the submitter picked on the form, which is what the platform's
    #: immutable record of this run was written about. Not the same as ``dataset_id``, which
    #: is what the registry resolved that identifier to, and ``none`` for a run reading no
    #: published corpus.
    dataset_release: str
    output_bucket: str
    output_prefix: str
    checkpoint_dir: str
    #: Read from ``EDULLM_WANDB_PROJECT``. The container also carries ``WANDB_PROJECT`` with
    #: the same value, because the W&B client reads that name unasked, and both are required
    #: below. Only one field holds it. Two fields for one fact is an invitation to read the
    #: wrong one, and the prefixed spelling is the platform's own assertion about where the
    #: run reports while the other exists for a library's convenience.
    wandb_project: str
    wandb_entity: str
    #: The W&B run this container reports as, which is the platform's own ``run_id`` on a
    #: single run and ``<run_id>-cell-<index>`` on a fan-out cell. Read from
    #: ``WANDB_RUN_ID`` rather than derived from :attr:`run_id`, because deriving it would
    #: make this field agree with the platform on a single run and disagree with the
    #: container on every cell -- the fan-out suffix is appended by a shell at container
    #: start, since the cell index does not exist when the submission is compiled.
    #:
    #: None only on a container captured before the platform set the variable. Every
    #: submission compiled today carries one.
    wandb_run_id: str | None = None
    #: The grouping key the submission form calls ``experiment``. Absent on a run admitted
    #: before the field existed, which is why this is optional rather than required. Every
    #: submission compiled today carries one.
    experiment: str | None = None
    #: Whose W&B account the run is attributed to, absent for most of the roster. Not the
    #: submitter. See the class docstring.
    wandb_username: str | None = None
    #: The three facts the registry resolved the form's ``dataset_release`` to, all absent
    #: together on a run that named ``none``. Read them through :meth:`dataset` rather than
    #: individually, so that the partial case is refused in one place.
    dataset_id: str | None = None
    dataset_version: str | None = None
    dataset_tokenizer: str | None = None

    def dataset(self, *, paths: Sequence[str] | None = None) -> DatasetLocation:
        """Where this run reads from, published or explicit.

        A method rather than a field, because explicit mode takes an argument and because a
        run that reads nothing at all is ordinary. A field would have to hold either a
        location that does not exist or an exception nobody asked for.
        """
        return resolve_dataset(self.as_environment(), paths=paths)

    def as_environment(self) -> dict[str, str]:
        """This object back as the variables it was read from, absent ones omitted.

        Here so that a program launching a subprocess can hand it the same run rather than
        rebuilding the names by hand, and so that :meth:`dataset` has one mapping to read
        without ``storage`` importing this module.
        """
        values = {
            "EDULLM_RUN_ID": self.run_id,
            "EDULLM_TEAM": self.team,
            "EDULLM_COMMIT_SHA": self.commit_sha,
            "EDULLM_DATASET_RELEASE": self.dataset_release,
            "EDULLM_OUTPUT_BUCKET": self.output_bucket,
            "EDULLM_OUTPUT_PREFIX": self.output_prefix,
            "EDULLM_CHECKPOINT_DIR": self.checkpoint_dir,
            "EDULLM_WANDB_PROJECT": self.wandb_project,
            "WANDB_PROJECT": self.wandb_project,
            "WANDB_ENTITY": self.wandb_entity,
            "WANDB_RUN_ID": self.wandb_run_id,
            "WANDB_RUN_GROUP": self.experiment,
            "WANDB_USERNAME": self.wandb_username,
            "EDULLM_DATASET_ID": self.dataset_id,
            "EDULLM_DATASET_VERSION": self.dataset_version,
            "EDULLM_DATASET_TOKENIZER": self.dataset_tokenizer,
        }
        return {name: value for name, value in values.items() if value is not None}


def run_environment(environ: Mapping[str, str] | None = None) -> RunEnvironment:
    """Read the run this process is executing, or say precisely what is missing.

    ``environ`` defaults to ``os.environ`` and is an argument so that a test, a launcher
    holding a child's environment, or a tool replaying a recorded job can all use the same
    reader. Reading it once at the top of a program and passing the object down is the
    intended shape, because ``os.environ`` is process-wide mutable state and a program that
    looks twice can get two answers.

    An empty value is treated as an absent one. Batch will carry an empty string through to
    the container unchanged, and every variable here names something, so ``""`` is a
    resolution that failed rather than a location called nothing.
    """
    source = os.environ if environ is None else environ
    missing = [name for name in REQUIRED_VARIABLES if not source.get(name)]
    if missing:
        raise MissingRunEnvironmentError(
            "this process is not running inside a job the eduLLM platform started, or the "
            f"job it is running inside is missing {', '.join(missing)}. The platform sets "
            "all of these on every container; export them to reproduce a run outside one"
        )

    def optional(name: str) -> str | None:
        return source.get(name) or None

    return RunEnvironment(
        run_id=source["EDULLM_RUN_ID"],
        team=source["EDULLM_TEAM"],
        commit_sha=source["EDULLM_COMMIT_SHA"],
        dataset_release=source["EDULLM_DATASET_RELEASE"],
        output_bucket=source["EDULLM_OUTPUT_BUCKET"],
        output_prefix=source["EDULLM_OUTPUT_PREFIX"],
        checkpoint_dir=source["EDULLM_CHECKPOINT_DIR"],
        # The prefixed spelling, not W&B's. Both are required above, so both are present,
        # and this one is the platform's assertion about where the run reports while the
        # other exists because the client library reads that name unasked.
        wandb_project=source["EDULLM_WANDB_PROJECT"],
        wandb_entity=source["WANDB_ENTITY"],
        wandb_run_id=optional("WANDB_RUN_ID"),
        experiment=optional("WANDB_RUN_GROUP"),
        wandb_username=optional("WANDB_USERNAME"),
        dataset_id=optional("EDULLM_DATASET_ID"),
        dataset_version=optional("EDULLM_DATASET_VERSION"),
        dataset_tokenizer=optional("EDULLM_DATASET_TOKENIZER"),
    )
