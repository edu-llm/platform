"""Whether a submitted command starts one process per device it is billed for.

A researcher's command is exec'd exactly as typed. ``.edullm/train_on_corpus.py`` in the
OLMo-core image handles being one rank of several -- it initialises the process group, shards
the model with FSDP and writes one checkpoint shard per rank -- and it does not start the
other processes. Nothing on this side does either. So a multi-GPU submission whose command
names no launcher trains on one device, bills for all of them, and exits zero: on
``gpu-4xa10g`` at $5.672/hour over the twelve hours its workload allows that is $68 for a
quarter of the work, and ``gpu-8xh100`` is four times worse again. There is no error anywhere,
and until this module existed the only statement of the rule was a comment in
``config/workload-catalog.yaml`` and a sentence in a form field's description.

**The device count is read from ``CONTAINER_SHAPES`` and never from the profile's name.**
``gpu-4xa10g`` reads like four devices and ``gpu-8xh100`` like eight, which makes deriving the
count from the string tempting and wrong: the name is a convention nothing enforces, and a
tenth shape named for its instance family rather than its device count would silently stop
being checked. The shapes are the same table the registered job definition asks Batch for, so
the number here is the number that is billed.

**This is asked at compile time, in ``compile_submission``, and the ordering is the point.**
Both halves are known there -- the workload fixes the compute profile and the command is on
the form -- and a refusal costs nothing before the approval gate. The same argument already
placed :func:`~edullm_platform.submission.require_submitter_on_the_roster` there: a refusal
that arrives after a lead has read the submission and released it has spent a person's
attention on a decision that could not have gone the other way.

**Why the rule is not a validator on ``RunManifest``, where the neighbouring command rules
are.** ``contracts/validation.py`` already refuses a command whose quoting was lost and one
whose first element cannot name a program, and this reads like a third member of that family.
Three things separate it. It needs the device count, which is a fact about deployed compute
that the contract layer has no business importing. It would move ``RunManifest``'s structural
digest, which four committed proof bundles record. And it would retroactively refuse
``fixtures/manifests/gpu-routine.yaml`` and ``gpu-exception.yaml``, whose canonical digests
are recorded goldens -- a rule that invalidates records written before it existed is a rule
that cannot be added to a hashed contract.

**The escape is a token in the command rather than a field on the form.** A guard with no way
out is one people get around by selecting a profile that fits, which wastes the same money
and records no reason at all. :data:`LAUNCH_CHECK_WAIVER` travels in the command, so it is
inside the hashed manifest and the immutable lineage record, it reaches the container's own
environment, and it is per-submission -- where a checkbox is ticked once and copied forward by
everybody who reuses the form. What it is not is silent:
:func:`waived_launch_check_note` puts a sentence in front of the lead who releases the run,
because the command itself is not on the approver page.

**FOUR NAMES HERE ARE PUBLIC BECAUSE A SECOND GUARD READS THE SAME COMMAND.**
:mod:`edullm_platform.checkpoint_commands` asks whether a command under a checkpoint contract
writes where a retry will look, which needs the same three facts this module needs: which
programs read one argument as a whole command line, where one simple command ends and the
next begins, and whether an exact token was written anywhere in the text. Restating them
there would be a second reading of one wrapper, and the drift fails open in both directions
-- a wrapper one guard can see into and the other cannot is a rule that quietly stops
applying. The argument against sharing with ``contracts/validation.py`` does not apply here:
neither module is packaged into either Lambda zip, so nothing about this costs a release.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final

from .errors import SubmissionRefusedError
from .execution import CONTAINER_SHAPES

__all__ = [
    "LAUNCH_CHECK_WAIVER",
    "MAXIMUM_WRAPPER_DEPTH",
    "SHELLS_THAT_READ_A_COMMAND_STRING",
    "LaunchPlan",
    "carries_the_token",
    "corrected_command",
    "read_launch_plan",
    "require_a_process_for_every_device",
    "shell_command_string",
    "simple_command_segments",
    "waived_launch_check_note",
]

#: What a submitter writes to say that the process count on this command is deliberate.
#:
#: An environment assignment rather than a bare word, because the two places a token is inert
#: are an assignment and a comment and both are consumed by the shell rather than handed to
#: the program. Spelled out in full so that it cannot be arrived at by nearly typing
#: something else: a waiver reachable by accident is not a decision.
LAUNCH_CHECK_WAIVER: Final = "EDULLM_LAUNCH_CHECK=waived"

#: Programs that read one argument as an entire command line, by base name so that
#: ``/bin/bash`` and ``bash`` are the same thing.
#:
#: RESTATED RATHER THAN SHARED WITH ``contracts/validation.py``, WHICH KNOWS THE SAME LIST FOR
#: THE NEIGHBOURING CHECK. That module is packaged into both Lambda zips, so widening a
#: frozenset there is a rebuild, an upload and two release records for a line that changes no
#: deployed behaviour. The cost of two spellings is that they drift, and the drift fails open
#: -- a wrapper this module cannot see into is a launcher it cannot find -- so
#: ``tests/test_multi_gpu_launcher.py`` holds this set to that one.
SHELLS_THAT_READ_A_COMMAND_STRING: Final = frozenset({"sh", "bash", "dash", "zsh", "ksh"})

#: Programs that run another program and are transparent to what this is asking. Deliberately
#: short: each entry is a way for a launcher to hide one word further along, and a list that
#: tried to be complete would be guessing.
_TRANSPARENT_PREFIXES: Final = frozenset({"env", "exec", "nohup", "time"})

#: The launchers that start one process per device. ``torch.distributed.launch`` is deprecated
#: and still works, which is exactly why it is here: a command that runs is a command this
#: must recognise, whatever upstream thinks of it.
_TORCH_LAUNCHER_PROGRAMS: Final = frozenset({"torchrun"})
_TORCH_LAUNCHER_MODULES: Final = frozenset({"torch.distributed.run", "torch.distributed.launch"})
_OTHER_LAUNCHER_PROGRAMS: Final = frozenset({"deepspeed", "mpirun", "mpiexec", "srun"})

#: ``python``, ``python3``, ``python3.12``. Matched on the base name, so an absolute
#: interpreter path is the same program.
_PYTHON = re.compile(r"^python(?:[0-9]+(?:\.[0-9]+)*)?$")

#: A leading ``NAME=value``, which the shell consumes rather than passing on.
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

#: Words that separate one simple command from the next. A launcher is recognised in command
#: position only, so a mention of one in an argument or a note is not an invocation -- and
#: these are what make "command position" mean more than "the first word".
_OPERATORS: Final = frozenset({";", "&&", "||", "|", "&", "(", ")", "\n"})

#: torchrun's own flag, in both spellings argparse accepts for it.
_RANK_FLAGS: Final = ("--nproc-per-node", "--nproc_per_node")

#: Values of that flag that resolve to a device count at runtime rather than to a number here.
_RANKS_DECIDED_AT_RUNTIME: Final = frozenset({"auto", "gpu", "cpu"})

#: One shell inside another is ordinary -- ``bash -lc 'exec bash -c ...'`` -- and a chain of
#: them is somebody trying to get past this. Bounded rather than followed indefinitely.
MAXIMUM_WRAPPER_DEPTH: Final = 3

#: What the corrected command is built with. ``--standalone`` is what makes a single-host run
#: need no rendezvous endpoint, which is the whole of the multi-GPU case this platform runs.
_LAUNCHER_TEMPLATE: Final = "-m torch.distributed.run --nproc-per-node={devices} --standalone"


@dataclass(frozen=True)
class LaunchPlan:
    """How many processes a command starts, and what starts them.

    ``processes`` is ``None`` when a launcher was found and its count could not be read --
    ``mpirun`` and ``srun`` take it from a scheduler, ``deepspeed`` and ``accelerate`` each
    spell it differently, and ``--nproc-per-node=auto`` resolves at runtime. That is recorded
    as an absence rather than as a guess, because a guessed count refuses a correct command.
    """

    #: The launcher as it was written, for a message to quote back, or ``None`` when the
    #: command starts one process by simply being a program.
    launcher: str | None
    processes: int | None


def require_a_process_for_every_device(
    *,
    command: Sequence[str],
    compute_profile: str,
) -> None:
    """Refuse a GPU submission that would leave devices billed and unused, or oversubscribed.

    One rule in both directions: on a profile with GPUs, the number of processes the command
    starts is the number of devices the profile bills for. It reads as two defects and is one
    -- ``--nproc-per-node=2`` on a four-GPU shape idles two cards at $2.84/hour, and
    ``--nproc-per-node=4`` on a one-GPU shape puts four ranks on one card, which is an
    ``invalid device ordinal`` at best and four processes contending for one device at worst.

    CPU profiles are not checked. ``torchrun --nproc-per-node=8`` on a CPU container is an
    ordinary gloo data-parallel job, there is no device to idle, and the count this rule
    compares against is zero.

    A profile with no container shape is not checked either, and that is deliberate rather
    than a hole. It cannot run: an unregistered profile has no rate and is refused for that a
    few lines earlier, and a registered one with no shape raises
    :class:`~edullm_platform.execution.UnshapedComputeProfileError` when its job definition is
    registered. Inventing a third refusal here would name the launcher for a submission whose
    actual problem is that the profile does not exist.
    """
    shape = CONTAINER_SHAPES.get(compute_profile)
    if shape is None or shape.gpus == 0:
        return
    if carries_the_token(command, LAUNCH_CHECK_WAIVER):
        return

    plan = read_launch_plan(command)
    if plan.processes is None or plan.processes == shape.gpus:
        return

    if plan.launcher is None:
        raise SubmissionRefusedError(
            _no_launcher_refusal(
                command,
                devices=shape.gpus,
                compute_profile=compute_profile,
            )
        )
    raise SubmissionRefusedError(
        _rank_count_refusal(
            compute_profile=compute_profile,
            devices=shape.gpus,
            plan=plan,
        )
    )


def waived_launch_check_note(
    *,
    command: Sequence[str],
    compute_profile: str,
) -> str | None:
    """The sentence an approver is owed when a waiver is what let this command through.

    Returned only when the waiver did something. A command that names a launcher at the right
    count and carries the token as well is not waiving anything, and a line on the approver
    page for every such run would be a line readers learn to skip -- which is the failure mode
    of every warning that is not selective.
    """
    if not carries_the_token(command, LAUNCH_CHECK_WAIVER):
        return None
    shape = CONTAINER_SHAPES.get(compute_profile)
    if shape is None or shape.gpus == 0:
        return None
    plan = read_launch_plan(command)
    if plan.processes is None or plan.processes == shape.gpus:
        return None
    return (
        f"**This run waives the device-count check.** `{compute_profile}` bills for "
        f"{_devices_said(shape.gpus)} and the command starts "
        f"{_processes_said(plan.processes)}, which `{LAUNCH_CHECK_WAIVER}` in the command "
        "declares is deliberate. Nothing verifies that the other devices are used."
    )


def read_launch_plan(command: Sequence[str]) -> LaunchPlan:
    """What this command would start, read the way a shell would read it.

    The first recognised launcher in command position wins. Command position rather than
    anywhere in the words, because the submission form is shlex-split and a launcher named in
    a note, a tag or a config value arrives as an ordinary argument -- and a guard that
    accepted those would pass exactly the submission it exists to refuse while looking
    covered.
    """
    for segment in _simple_commands(tuple(command)):
        launcher = _launcher_in(segment)
        if launcher is not None:
            return launcher
    # No launcher is one process, which is what the container execs. This is the reading the
    # defect turns on, so it is stated rather than left as a fall-through.
    return LaunchPlan(launcher=None, processes=1)


def corrected_command(command: Sequence[str], *, devices: int) -> str | None:
    """The command a submitter should have typed, or ``None`` when it cannot be built.

    REBUILT FROM THE ORIGINAL TEXT RATHER THAN FROM ITS WORDS, AND THAT IS THE WHOLE
    DIFFICULTY. Rejoining shlex-split words with :func:`shlex.join` single-quotes every one
    of them, so ``"$EDULLM_RUN_ID"`` comes back as ``'$EDULLM_RUN_ID'`` -- which the shell
    does not expand, so the corrected command would hand OLMo-core the fourteen literal
    characters of a variable name as a run id. A refusal that prints a broken command is
    worse than one that prints none.

    So the launcher is spliced into the text the shell was given, immediately after the
    interpreter, leaving every other character of the submitter's line alone. That needs the
    command to start with a Python interpreter; a shell script or a wrapper binary gets
    ``None`` and the caller falls back to naming the launcher without rewriting anything.

    Splicing there rather than around the whole line is also what makes the correction right
    for ``python -m some.module``, which would otherwise need a second rule. ``-m`` in the
    spliced result is torchrun's own flag -- it is a store_true that says the training script
    is a module name -- so ``python -m torch.distributed.run ... -m some.module`` runs the
    module under four ranks rather than passing two module names to one interpreter.
    """
    words = tuple(command)
    if not words:
        return None
    launcher = _LAUNCHER_TEMPLATE.format(devices=devices)

    position = _shell_command_position(words)
    if position is None:
        if not _PYTHON.match(PurePosixPath(words[0]).name):
            return None
        # No shell is involved, so nothing here is expanded and quoting is presentation only.
        return shlex.join((words[0], *launcher.split(), *words[1:]))

    spliced = _splice_after_the_interpreter(words[position], launcher)
    if spliced is None:
        return None
    # Everything in front of the command string is the submitter's own wrapper, reproduced
    # word for word so the correction is their line with one insertion rather than a line of
    # ours.
    return " ".join((*words[:position], shlex.quote(spliced)))


# ---------------------------------------------------------------------------------------
# Reading the command
# ---------------------------------------------------------------------------------------


def _shell_command_position(words: Sequence[str]) -> int | None:
    """Where the one word a shell wrapper hands to ``-c`` is, or ``None`` if this is not one.

    ``-c`` has to end its cluster to take the next word as the command, so ``-lc`` is the
    form the guide prints and ``-cl`` would be a different thing. Flags before it are skipped
    and a word that is not a flag ends the search, because ``bash script.sh`` runs a file
    rather than a string.

    The position rather than the word, because :func:`corrected_command` has to reproduce
    everything in front of it verbatim. Searching for the word again with ``list.index`` would
    find the first equal one, which is the same word in every realistic command and not in
    every possible one.
    """
    if not words or PurePosixPath(words[0]).name not in SHELLS_THAT_READ_A_COMMAND_STRING:
        return None
    for position, word in enumerate(words[1:], start=1):
        if not word.startswith("-") or word == "--":
            return None
        if word.endswith("c") and len(word) > 1:
            return position + 1 if position + 1 < len(words) else None
    return None


def shell_command_string(words: Sequence[str]) -> str | None:
    """The one word this argv hands to a shell's ``-c``, or ``None`` if it hands none."""
    position = _shell_command_position(words)
    return None if position is None else words[position]


def simple_command_segments(words: Sequence[str]) -> list[tuple[str, ...]]:
    """The words split into simple commands, with anything after a comment dropped.

    A word beginning with ``#`` starts a comment for the shell, so everything from there on is
    text rather than a program -- which is what keeps a launcher written in a comment from
    reading as an invocation.
    """
    found: list[tuple[str, ...]] = []
    current: list[str] = []
    for word in words:
        if word.startswith("#"):
            break
        if word in _OPERATORS:
            if current:
                found.append(tuple(current))
            current = []
            continue
        current.append(word)
    if current:
        found.append(tuple(current))
    return found


def _simple_commands(words: Sequence[str], depth: int = 0) -> list[tuple[str, ...]]:
    """Every simple command this argv would run, looking inside shell wrappers as it goes."""
    found: list[tuple[str, ...]] = []
    for segment in simple_command_segments(words):
        text = shell_command_string(segment)
        if text is None or depth >= MAXIMUM_WRAPPER_DEPTH:
            found.append(segment)
            continue
        try:
            inner = shlex.split(text)
        except ValueError:
            # Unbalanced quoting inside the wrapper. Not this module's refusal to make, and
            # the segment is kept so that whatever is readable in it is still read.
            found.append(segment)
            continue
        found.extend(_simple_commands(inner, depth + 1))
    return found


def _program_and_arguments(segment: Sequence[str]) -> tuple[str, tuple[str, ...]] | None:
    """What this simple command runs, past the assignments and wrappers in front of it."""
    words = list(segment)
    while words:
        if _ASSIGNMENT.match(words[0]) or PurePosixPath(words[0]).name in _TRANSPARENT_PREFIXES:
            words.pop(0)
            continue
        break
    if not words:
        return None
    return words[0], tuple(words[1:])


def _module_argument(arguments: Sequence[str]) -> str | None:
    """The module a Python interpreter was told to run, if it was told to run one.

    Stops at the first word that is not a flag, because that word is the script path and
    everything after it belongs to the script. Without that, ``python train.py -m resnet``
    would read ``resnet`` as an interpreter module -- and, worse, a script whose own ``-m``
    took ``torch.distributed.run`` as a value would read as a launcher.
    """
    index = 0
    while index < len(arguments):
        word = arguments[index]
        if not word.startswith("-"):
            return None
        if word == "-m":
            return arguments[index + 1] if index + 1 < len(arguments) else None
        if word.startswith("-m") and not word.startswith("--"):
            return word[2:]
        if word == "-c":
            # A program passed as a string is not a module, and a launcher named inside one is
            # a launcher named inside a string.
            return None
        if word in {"-X", "-W"}:
            index += 2
            continue
        index += 1
    return None


def _launcher_in(segment: Sequence[str]) -> LaunchPlan | None:
    """The launcher this simple command runs, or ``None`` if it runs an ordinary program."""
    resolved = _program_and_arguments(segment)
    if resolved is None:
        return None
    program, arguments = resolved
    name = PurePosixPath(program).name

    if name in _TORCH_LAUNCHER_PROGRAMS:
        return LaunchPlan(launcher=name, processes=_declared_ranks(arguments))
    if name in _OTHER_LAUNCHER_PROGRAMS:
        return LaunchPlan(launcher=name, processes=None)
    # `accelerate` on its own configures, tests and estimates; only `launch` starts anything.
    if name == "accelerate" and arguments and arguments[0] == "launch":
        return LaunchPlan(launcher="accelerate launch", processes=None)
    if _PYTHON.match(name):
        module = _module_argument(arguments)
        if module in _TORCH_LAUNCHER_MODULES:
            return LaunchPlan(launcher=f"{name} -m {module}", processes=_declared_ranks(arguments))
    return None


def _declared_ranks(arguments: Sequence[str]) -> int | None:
    """How many processes a torch launcher was told to start.

    AN ABSENT FLAG IS ONE PROCESS, WHICH IS THE CASE A GUARD SATISFIED BY THE WORD `torchrun`
    WOULD PASS. ``--nproc-per-node`` defaults to 1, so ``torchrun --standalone train.py`` on a
    four-GPU shape is the original defect with a launcher in front of it.

    ``auto``, ``gpu`` and ``cpu`` resolve to a count at runtime and a shell variable resolves
    to whatever the environment holds. None of the four is a number to compare, and the
    direction to fail in is the one that does not refuse a correct command: the submitter has
    named a launcher and named a count.
    """
    for position, word in enumerate(arguments):
        for flag in _RANK_FLAGS:
            if word == flag:
                value = arguments[position + 1] if position + 1 < len(arguments) else None
                break
            if word.startswith(f"{flag}="):
                value = word[len(flag) + 1 :]
                break
        else:
            continue
        if value is None or value in _RANKS_DECIDED_AT_RUNTIME:
            return None
        try:
            return int(value)
        except ValueError:
            return None
    return 1


def carries_the_token(command: Sequence[str], token: str) -> bool:
    """Whether an exact token appears as a word, wherever in the command it is written.

    Read over every word including comments and including the inside of wrappers, because
    which position is inert depends on the command: an assignment is consumed by a shell and a
    comment by anything, while a command exec'd directly has neither and has to carry the
    token as an argument its own program ignores.

    Exact, and case-sensitive. Prose quoting the token -- this refusal pasted into a note,
    say -- arrives as one word after splitting and does not match.

    Takes the token rather than closing over :data:`LAUNCH_CHECK_WAIVER`, because the
    checkpoint guard offers its own waiver and the two have to be written in the same places
    for the same reasons. One function is what makes "wherever it is written" mean the same
    thing to a researcher whichever guard they are getting past.
    """
    return token in _every_word(tuple(command))


def _every_word(words: Sequence[str], depth: int = 0) -> set[str]:
    found = set(words)
    if depth >= MAXIMUM_WRAPPER_DEPTH:
        return found
    text = shell_command_string(words)
    if text is None:
        return found
    try:
        return found | _every_word(shlex.split(text), depth + 1)
    except ValueError:
        return found


# ---------------------------------------------------------------------------------------
# What a submitter reads
# ---------------------------------------------------------------------------------------


def _splice_after_the_interpreter(text: str, launcher: str) -> str | None:
    """``python X`` becomes ``python -m torch.distributed.run ... X``, in the original text."""
    match = re.match(r"(\s*)(python(?:[0-9]+(?:\.[0-9]+)*)?)(\s)", text)
    if match is None:
        return None
    return f"{match.group(1)}{match.group(2)} {launcher}{text[match.end(2) :]}"


def _no_launcher_refusal(
    command: Sequence[str],
    *,
    devices: int,
    compute_profile: str,
) -> str:
    corrected = corrected_command(command, devices=devices)
    correction = (
        f"Run it under a launcher:\n\n    {corrected}\n\n"
        if corrected is not None
        else (
            "Put a launcher in front of the program that trains -- "
            f"python -m torch.distributed.run --nproc-per-node={devices} --standalone, "
            "or torchrun with the same flag.\n\n"
        )
    )
    return (
        f"compute profile {compute_profile!r} has {devices} GPUs and this command starts one "
        f"process, so {devices - 1} of them would be billed and left idle. A training program "
        "can handle being one rank of several and still not start the others, and the command "
        f"is exec'd exactly as typed, so nothing else will. {correction}"
        "torchrun, torch.distributed.launch, accelerate launch, deepspeed, mpirun and srun "
        "are recognised as well. If one process on this machine is deliberate -- a benchmark, "
        "a memory profile, an inference sweep that places its own devices -- write "
        f"{LAUNCH_CHECK_WAIVER} into the command, which records the decision on the run "
        "rather than leaving it to be read off a profile nobody can explain."
    )


def _rank_count_refusal(*, compute_profile: str, devices: int, plan: LaunchPlan) -> str:
    processes = plan.processes
    assert processes is not None  # the caller returns on an unreadable count
    if processes < devices:
        consequence = (
            f"so {devices - processes} of them would be billed and left idle, which is the "
            "same waste as running no launcher at all and costs the same per hour"
        )
        # The flag's own default is the reason a command can name a launcher and still start
        # one process, and it is the single thing a submitter in that position does not know.
        # Said only when the count is one, because it explains nothing about any other number.
        default = (
            " --nproc-per-node is 1 when it is not given."
            if processes == 1
            else ""
        )
        remedy = f"{default} Set --nproc-per-node={devices}."
    else:
        consequence = (
            f"so {processes - devices} of them have no device to take: those ranks either "
            "stop with an invalid device ordinal or contend for a card another rank is "
            "already using, which is slower than one process and reports nothing"
        )
        # Naming the other profile only in this direction, because it exists in this
        # direction. Going the other way there is frequently no shape with the smaller count
        # -- nothing has two devices -- and pointing at one that is not there reads as a
        # dropdown that lost an option.
        remedy = (
            f" Set --nproc-per-node={devices}, or pick a compute profile with "
            f"{processes} devices."
        )
    return (
        f"compute profile {compute_profile!r} has {_devices_said(devices)} and "
        f"{plan.launcher} on this command starts {_processes_said(processes)}, "
        f"{consequence}.{remedy} A count this platform cannot read -- auto, gpu, or a shell "
        f"variable -- is not refused. If the mismatch is deliberate, write "
        f"{LAUNCH_CHECK_WAIVER} into the command."
    )


def _devices_said(count: int) -> str:
    return f"{count} GPU" if count == 1 else f"{count} GPUs"


def _processes_said(count: int) -> str:
    return "1 process" if count == 1 else f"{count} processes"
