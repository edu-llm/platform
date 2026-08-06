"""Writing a first ``.edullm/run.yaml``, from what the catalog and the tree already say.

THREE FIELDS, AND EACH IS INFERRED OR IS A REVIEWED DEFAULT. Nothing here is invented,
which is the only property that makes writing the file without asking safe:

- ``workload_profile`` comes from ``config/workload-catalog.yaml``. A workload declares the
  repository it was written against, so the candidates are exactly the entries naming this
  one. Where there is more than one, the training entry wins over the check entry, because
  a scaffold is written by somebody about to submit work rather than about to smoke-test
  the platform, and ``check`` prints the alternatives either way.
- ``suggested_compute`` is the cheapest provisioned profile that could carry that workload
  *and run it*: a CPU profile where the workload keeps no checkpoint contract, and where it
  does, the cheapest provisioned GPU whose card has bfloat16. Cheapest rather than largest
  because the suggestion's job is to be edited, and the direction a wrong default should
  fail is the one that costs less -- but a shape the workload cannot run is not the cheaper
  direction, which is what :func:`_can_run_what_a_trainer_defaults_to` is about.
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
from edullm_platform.precision import gpu_of

__all__ = ["FIRST_RUN_COMMAND", "scaffold_spec", "workloads_registered_for"]

#: The command ``submit-run.yml`` pre-fills its own form with, quoted identically. Copied
#: because a scaffold that could not compile would be worse than none, and this is the one
#: command in the system that is known to: it splits under POSIX rules into three arguments
#: whose first names a program, which is what ``RunManifest.command`` requires.
FIRST_RUN_COMMAND = "python -c 'import sys; print(\"edullm ready\", sys.version)'"

#: Where a research repository keeps the things this platform reads, and therefore the only
#: directory a scaffold looks in for an entry point. Looking wider would find a training
#: script in ``src/`` that was never meant to be a container's argv.
ENTRY_POINT_GLOB = ".edullm/*.py"


def workloads_registered_for(
    configuration: ReviewedConfiguration, repository: str
) -> tuple[str, ...]:
    """The catalog's entries for one repository, which is what a scaffold may choose from.

    Public because the caller has to ask this *before* scaffolding rather than after.
    ``RunSpec`` requires a workload profile of at least one character, so a repository the
    catalog does not name has no file this module could write -- and the difference between
    answering that with a refusal and discovering it inside a constructor is the difference
    between a sentence and a traceback.
    """
    return tuple(
        sorted(entry.name for entry in configuration.catalog.workloads if entry.repository == repository)
    )


def scaffold_spec(
    configuration: ReviewedConfiguration,
    *,
    repository: str,
    root: Path,
    destination: Path | None = None,
    workload_profile: str | None = None,
    compute_profile: str | None = None,
) -> Path:
    """Write the file and answer with where it went.

    The caller owns whether there is anything to write. ``workload_profile`` is required to
    resolve to a name, either because one was declared or because
    :func:`workloads_registered_for` answered non-empty, and a caller that skips that
    question gets ``RunSpec``'s own refusal rather than a readable one.
    """
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
    # NEWLINE IS NAMED, AND WITHOUT IT THIS FILE IS A DIFFERENT FILE ON WINDOWS. Text mode
    # translates every "\n" to os.linesep, so the same scaffold writes LF here and CRLF
    # there. What gets written is committed into a research repository, and a repository
    # without a .gitattributes -- which a research repository is not guaranteed to have --
    # carries the difference into the diff: this repository's own .gitattributes records the
    # incident it was written for, a 32-line change that arrived as 1,795 lines. Nothing
    # breaks, which is the problem: `check` reads with newline=None and folds CRLF back, so
    # a Windows researcher produces a file that differs from everybody else's before they
    # have typed anything and nothing tells them.
    path.write_text(
        render_spec(spec, notes=_notes(configuration, repository, compute=compute)),
        encoding="utf-8",
        newline="\n",
    )
    return path


def _notes(
    configuration: ReviewedConfiguration, repository: str, *, compute: ComputeProfile | None
) -> tuple[str, ...]:
    """The header, which says what was guessed and what the alternatives were.

    Comments rather than prose printed once to a terminal, because the file outlives the
    invocation and the person editing it in a fortnight is the one who needs the list.
    """
    offered = ", ".join(workloads_registered_for(configuration, repository))
    return (
        (
            "# Written by edullm check, from config/workload-catalog.yaml in "
            f"{configuration.directory}."
        ),
        "# Everything here is a property of the code and travels with it in git.",
        "# The machine, the corpus and the experiment are supplied at submit time,",
        "# because one commit run by two people belongs to two teams.",
        "#",
        f"# Workload profiles registered for {repository}: {offered or 'none'}.",
        "# edullm check prices this and lists every refusal, without dispatching anything.",
        *_a_card_without_bfloat16(compute),
    )


def _a_card_without_bfloat16(compute: ComputeProfile | None) -> tuple[str, ...]:
    """Said in the file only where the shape written into it has no bfloat16.

    Unreachable from the default, which is the point of it. ``_pick_compute`` no longer
    suggests a card without bfloat16, so the only way one reaches this file is
    ``edullm check --compute gpu-1xt4`` on the invocation that scaffolds -- somebody who
    chose the shape rather than accepted it, and the one reader for whom the sentence is
    news rather than clutter.

    It names the two spellings because the guard that would refuse this reads the words of
    the command, and a trainer that fixes its precision in code writes none of them. Putting
    one in the command is what moves the failure from a billed instance to a free refusal.

    **AND THIS WILL NOT PUT IT THERE UNASKED, WHICH IS THE SAME RULE THE REST OF THE MODULE
    KEEPS.** The flag a program accepts is a fact about that program: ``--param-dtype`` is
    real in OLMo-core's entry point and would be an unrecognised argument in somebody
    else's, so a scaffold that wrote it into every command would turn a default that runs
    into a default that cannot start, for every repository but one. Nothing in the catalog
    declares a workload's precision, so there is nothing here to read -- and a sentence in
    the file the researcher is about to edit is the honest version of what this knows.
    """
    if compute is None or _can_run_what_a_trainer_defaults_to(compute):
        return ()
    gpu = gpu_of(compute)
    card = "its card" if gpu is None else f"the {gpu.model} is {gpu.architecture.name} and"
    return (
        "#",
        f"# {compute.name}: {card} has no bfloat16 in the hardware. A trainer that asks",
        "# for the format dies on the first kernel needing it, once the machine is billed.",
        "# edullm check reads the words of the command and cannot see a dtype set in code,",
        "# so write the dtype in and be refused for free instead:",
        "#   train_module.dp_config.param_dtype=bfloat16   or   --param-dtype bfloat16",
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
    runnable = [entry for entry in matching if _can_run_what_a_trainer_defaults_to(entry)]
    return min(runnable or matching, key=lambda entry: (entry.hourly_rate_usd, entry.name))


def _can_run_what_a_trainer_defaults_to(profile: ComputeProfile) -> bool:
    """Whether this shape's card has bfloat16, which a default may not assume away.

    **THE CHEAPEST GPU IN THE CATALOG IS THE ONE THAT CANNOT RUN THE WORKLOAD THIS SCAFFOLD
    PAIRS IT WITH.** ``gpu-1xt4`` is a T4, ``olmo-core-train`` runs
    ``.edullm/train_on_corpus.py``, and that program builds its data-parallel config in
    bfloat16 unless told otherwise -- so the pair priced out at the cheapest rate in the
    catalog was a run that dies on the first kernel needing the format. Nothing in front of
    it says so: the dtype is set in code rather than in argv, which is the one thing
    :mod:`edullm_platform.precision` documents that it cannot see, so ``check`` returned no
    refusals and classified the run ``automatic``. The failure arrived after a machine had
    been obtained and billed for.

    So "cheapest" is read over the shapes that can run the thing, and the capability comes
    from :func:`~edullm_platform.precision.gpu_of` rather than from a list of profile names
    here. That module keys on the EC2 instance family the catalog already declares, so a
    shape promoted, renamed or demoted in ``config/workload-catalog.yaml`` is answered here
    without an edit, and the fact that Turing has no bfloat16 stays written once.

    A CPU profile has no card and passes: bfloat16 on a CPU is slow rather than absent, and
    a workload with no checkpoint contract is not the training case this is about. A GPU
    family :data:`~edullm_platform.precision.GPUS_BY_INSTANCE_FAMILY` does not carry has no
    recorded answer and does not pass, which is the opposite reading from ``gpu_of``'s own
    caller and is right for the opposite reason: that one decides whether to *refuse* a
    submission somebody wrote, where a guess is worse than nothing, and this one decides
    what to *suggest* to somebody who wrote nothing, where the unknown card is exactly what
    a default should not reach for. ``tests/test_bfloat16_guard.py`` keeps the case
    unreachable in a shipped catalog either way, and the caller falls back to the whole set
    so a catalog of nothing but unknown families still yields a suggestion.
    """
    if profile.accelerator != "gpu":
        return True
    gpu = gpu_of(profile)
    return gpu is not None and gpu.architecture.supports_bfloat16


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
