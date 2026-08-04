"""Writing a first ``.edullm/run.yaml``, from what the catalog and the tree already say.

THREE FIELDS, AND EACH IS INFERRED OR IS A REVIEWED DEFAULT. Nothing here is invented,
which is the only property that makes writing the file without asking safe:

- ``workload_profile`` comes from ``config/workload-catalog.yaml``. A workload declares the
  repository it was written against, so the candidates are exactly the entries naming this
  one. Where there is more than one, the training entry wins over the check entry, because
  a scaffold is written by somebody about to submit work rather than about to smoke-test
  the platform, and ``check`` prints the alternatives either way.
- ``suggested_compute`` is the cheapest provisioned profile that could carry that workload:
  a CPU profile where the workload keeps no checkpoint contract, the cheapest provisioned
  GPU where it does. Cheapest rather than largest because the suggestion's job is to be
  edited, and the direction a wrong default should fail is the one that costs less.
- ``command`` is the reviewed default the submission form itself carries, unless the
  repository has an entry point under ``.edullm/`` -- in which case it is that, wrapped so
  the checkpoint directory expands and launched so that every device the profile bills for
  gets a process.

**WHERE IT CANNOT INFER, IT WRITES SOMETHING ``check`` WILL REFUSE BY NAME, RATHER THAN
SOMETHING PLAUSIBLE.** A workload with a checkpoint contract and no discoverable entry
point gets the form's default command, which
``require_a_save_folder_a_retry_can_find`` refuses in a sentence naming the variable to
write. That is a better first experience than a guessed path: a refusal costs two seconds
and names the fix, and a plausible wrong path costs a queue wait, an approval and a
container that starts and cannot find its own program.
"""

from __future__ import annotations

from pathlib import Path

from edullm_platform.cli.configuration import ReviewedConfiguration
from edullm_platform.cli.spec import SPEC_PATH, RunSpec, render_spec
from edullm_platform.contracts.workload import ComputeProfile, WorkloadProfile
from edullm_platform.execution import CONTAINER_SHAPES

__all__ = ["FIRST_RUN_COMMAND", "scaffold_spec"]

#: The command ``submit-run.yml`` pre-fills its own form with, quoted identically. Copied
#: because a scaffold that could not compile would be worse than none, and this is the one
#: command in the system that is known to: it splits under POSIX rules into three arguments
#: whose first names a program, which is what ``RunManifest.command`` requires.
FIRST_RUN_COMMAND = "python -c 'import sys; print(\"edullm ready\", sys.version)'"

#: Where a research repository keeps the things this platform reads, and therefore the only
#: directory a scaffold looks in for an entry point. Looking wider would find a training
#: script in ``src/`` that was never meant to be a container's argv.
ENTRY_POINT_GLOB = ".edullm/*.py"


def scaffold_spec(
    configuration: ReviewedConfiguration,
    *,
    repository: str,
    root: Path,
    destination: Path | None = None,
    workload_profile: str | None = None,
    compute_profile: str | None = None,
) -> Path:
    """Write the file and answer with where it went."""
    workload = _pick_workload(configuration, repository, workload_profile)
    compute = _pick_compute(configuration, workload, compute_profile)
    spec = RunSpec(
        schema_version=1,
        workload_profile=workload.name if workload is not None else (workload_profile or ""),
        suggested_compute=compute.name if compute is not None else compute_profile,
        command=_pick_command(root, workload=workload, compute=compute),
    )
    path = root / SPEC_PATH if destination is None else destination
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_spec(spec, notes=_notes(configuration, repository)), encoding="utf-8")
    return path


def _notes(configuration: ReviewedConfiguration, repository: str) -> tuple[str, ...]:
    """The header, which says what was guessed and what the alternatives were.

    Comments rather than prose printed once to a terminal, because the file outlives the
    invocation and the person editing it in a fortnight is the one who needs the list.
    """
    offered = ", ".join(
        sorted(
            entry.name
            for entry in configuration.catalog.workloads
            if entry.repository == repository
        )
    )
    return (
        (
            "# Written by edullm check, from config/workload-catalog.yaml in "
            f"{configuration.directory}."
        ),
        "# Everything here is a property of the code and travels with it in git.",
        "# What a run costs -- the machine, the corpus, the experiment -- is supplied at",
        "# submit time, because one commit run by two people belongs to two teams.",
        "#",
        f"# Workload profiles registered for {repository}: {offered or 'none'}.",
        "# edullm check prices this and lists every refusal, without dispatching anything.",
    )


def _pick_workload(
    configuration: ReviewedConfiguration, repository: str, declared: str | None
) -> WorkloadProfile | None:
    workloads = configuration.catalog.workloads
    if declared is not None:
        return next((entry for entry in workloads if entry.name == declared), None)
    candidates = [entry for entry in workloads if entry.repository == repository]
    if not candidates:
        return None
    # A checkpoint contract is what separates a training entry from a check entry, and it
    # is a declared fact rather than a reading of the name -- ``olmo-core-check`` and
    # ``edullm-alt-cl-check`` happen to say so in their names and nothing enforces that.
    checkpointing = [entry for entry in candidates if entry.checkpoint is not None]
    return min(checkpointing or candidates, key=lambda entry: entry.name)


def _pick_compute(
    configuration: ReviewedConfiguration,
    workload: WorkloadProfile | None,
    declared: str | None,
) -> ComputeProfile | None:
    profiles = configuration.catalog.compute_profiles
    if declared is not None:
        return next((entry for entry in profiles if entry.name == declared), None)
    provisioned = [entry for entry in profiles if entry.provisioned]
    if not provisioned:
        return None
    wanted = "gpu" if workload is not None and workload.checkpoint is not None else "cpu"
    matching = [entry for entry in provisioned if entry.accelerator == wanted] or provisioned
    return min(matching, key=lambda entry: (entry.hourly_rate_usd, entry.name))


def _pick_command(
    root: Path, *, workload: WorkloadProfile | None, compute: ComputeProfile | None
) -> str:
    """The argv, built to satisfy both command rules where an entry point can be found."""
    entry_point = _find_entry_point(root, workload)
    if entry_point is None:
        return FIRST_RUN_COMMAND
    devices = _devices(compute)
    launcher = (
        f"python -m torch.distributed.run --nproc-per-node={devices} --standalone"
        if devices > 1
        else "python"
    )
    saving = (
        ' --save-folder "$EDULLM_CHECKPOINT_DIR"'
        if workload is not None and workload.checkpoint is not None
        else ""
    )
    # A shell wrapper rather than a bare argv, and the double quotes inside it are the
    # point. The container execs what it is given, so a ``$`` in an exec-form word is
    # twenty-two literal characters and a directory OLMo-core would cheerfully create; a
    # shell is what expands it, and single quotes around the whole thing would stop it.
    inner = f'{launcher} {entry_point} "$EDULLM_RUN_ID"{saving}'
    return f"bash -lc '{inner}'"


def _find_entry_point(root: Path, workload: WorkloadProfile | None) -> str | None:
    """A Python file under ``.edullm/``, preferring one that reads like the workload's.

    Sorted before it is filtered so the answer does not depend on the order a filesystem
    happens to list a directory in, which is what would make a scaffold produce different
    files on two machines from the same commit.
    """
    found = sorted(path for path in root.glob(ENTRY_POINT_GLOB) if path.is_file())
    if not found:
        return None
    if workload is not None and workload.checkpoint is not None:
        training = [path for path in found if "train" in path.name]
        if training:
            return training[0].relative_to(root).as_posix()
    return found[0].relative_to(root).as_posix()


def _devices(compute: ComputeProfile | None) -> int:
    """How many processes the command has to start, read where the launcher guard reads it.

    ``CONTAINER_SHAPES`` and never the profile's name: the name is a convention nothing
    enforces, and a scaffold that counted devices out of ``gpu-4xa10g`` would write a
    command the guard refuses the moment somebody adds a shape named for its family.
    """
    if compute is None:
        return 1
    shape = CONTAINER_SHAPES.get(compute.name)
    return shape.gpus if shape is not None and shape.gpus else 1
