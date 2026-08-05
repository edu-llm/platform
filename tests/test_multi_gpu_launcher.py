"""A multi-GPU profile whose command starts one process, refused before a lead reads it.

Six of the nine promoted GPU shapes carry more than one device, and a submitted command is
exec'd exactly as typed. ``.edullm/train_on_corpus.py`` handles being one rank of several --
it initialises the process group, shards with FSDP and writes one checkpoint shard per rank
-- and it does not start the other processes. Nothing else does either, so a command without
a launcher trains on one device, bills for all of them, and exits zero. On ``gpu-4xa10g`` at
$5.672/hour over ``olmo-core-train``'s twenty-four hours that is $136 for a quarter of the
work, and the same command on ``gpu-8xh100`` is $1,321 for an eighth of it.

**The refusal is at compile time because that is where both halves are known and where a
refusal is still cheap.** The submission names the compute profile, the compute profile fixes
the device count, and the command is on the form. Everything before Batch is reversible; the
approval is a person's attention, and this platform already refuses an off-roster submitter
here rather than after the gate for exactly that reason.

**Why not in the manifest contract, which is where the neighbouring command rules live.**
``contracts/validation.py`` already refuses a command that lost its quotes, and this looks
like a third member of that family. It is not one. A model validator on ``RunManifest`` would
change that model's structural digest, which the contract inventory records, and would
retroactively refuse ``fixtures/manifests/gpu-routine.yaml`` and ``gpu-exception.yaml``, whose
canonical digests are recorded goldens. It would also make the contract layer depend on
``CONTAINER_SHAPES``. The rule needs the device count and the contract layer has no business
knowing it.

The device count is read from ``CONTAINER_SHAPES`` rather than from the profile's name, so a
tenth shape named anything at all is covered on the day it is added. The parametrized cases
below are generated from that table for the same reason.
"""

from __future__ import annotations

import shlex

import pytest
from test_phase2_submission import compile_payload, olmo_payload, render

from edullm_platform.checkpoint_commands import CHECKPOINT_CHECK_WAIVER
from edullm_platform.errors import SubmissionRefusedError
from edullm_platform.execution import CONTAINER_SHAPES
from edullm_platform.launchers import (
    LAUNCH_CHECK_WAIVER,
    SHELLS_THAT_READ_A_COMMAND_STRING,
    corrected_command,
    read_launch_plan,
    require_a_process_for_every_device,
    waived_launch_check_note,
)

#: The profile every worked example below runs on, and the one the four-GPU workload names.
FOUR_GPUS = "gpu-4xa10g"
ONE_GPU = "gpu-1xa10g"
NO_GPUS = "cpu-32vcpu"

#: Read out of the shapes rather than listed, so a shape promoted tomorrow is covered by
#: every case here without anybody remembering to add it.
MULTI_GPU_PROFILES = tuple(
    sorted(name for name, shape in CONTAINER_SHAPES.items() if shape.gpus > 1)
)
SINGLE_GPU_PROFILES = tuple(
    sorted(name for name, shape in CONTAINER_SHAPES.items() if shape.gpus == 1)
)

#: What the guide tells a researcher to run, without the launcher. The wrapper is not
#: decoration: ``$EDULLM_RUN_ID`` is expanded by the shell and the container execs the
#: command directly, so every real training command carries it.
WRAPPED = ('bash', '-lc', 'python .edullm/train_on_corpus.py "$EDULLM_RUN_ID" --steps 4000')


def refuse(command: tuple[str, ...], *, compute_profile: str) -> str:
    with pytest.raises(SubmissionRefusedError) as exc_info:
        require_a_process_for_every_device(command=command, compute_profile=compute_profile)
    return str(exc_info.value)


def allow(command: tuple[str, ...], *, compute_profile: str) -> None:
    require_a_process_for_every_device(command=command, compute_profile=compute_profile)


def wrapped(inner: str) -> tuple[str, ...]:
    return ("bash", "-lc", inner)


# ---------------------------------------------------------------------------------------
# The defect: more devices than processes
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("compute_profile", MULTI_GPU_PROFILES)
def test_a_multi_gpu_profile_whose_command_starts_no_launcher_is_refused(
    compute_profile: str,
) -> None:
    """Mutation: check only the profile the four-GPU workload happens to name.

    Every shape with more than one device has the same defect and the same price attached
    to it, so the case list is the table rather than the one profile a researcher reached
    first.
    """
    message = refuse(WRAPPED, compute_profile=compute_profile)

    assert compute_profile in message
    assert str(CONTAINER_SHAPES[compute_profile].gpus) in message


def test_the_refusal_names_the_profile_the_device_count_and_a_corrected_command() -> None:
    """Mutation: refuse with `multi_gpu_command_has_no_launcher` and nothing else.

    A reason code sends a submitter to whoever wrote it. The three things that make this
    self-service are which profile they picked, how many devices it bills for, and the line
    to paste in place of the one they typed -- and the last is the one that cannot be
    guessed, because the launcher goes between the interpreter and the script rather than
    in front of the whole command.
    """
    message = refuse(WRAPPED, compute_profile=FOUR_GPUS)

    assert FOUR_GPUS in message
    assert "4" in message
    assert "--nproc-per-node=4" in message
    assert ".edullm/train_on_corpus.py" in message
    # The variable has to survive the rewrite still quoted for the shell, because a
    # corrected command that hands OLMo-core the fourteen literal characters of
    # $EDULLM_RUN_ID is worse than no correction at all.
    assert '"$EDULLM_RUN_ID"' in message


def test_the_corrected_command_the_refusal_prints_is_one_this_guard_accepts() -> None:
    """THE ONE THAT MATTERS. Mutation: print a launcher line that is subtly wrong.

    A refusal that hands somebody a command which is itself refused costs two rounds and
    teaches them the guard is broken. So the correction is not asserted against a literal
    string; it is split the way the submission form splits it and put back through the same
    function, on every multi-GPU shape.
    """
    for compute_profile in MULTI_GPU_PROFILES:
        corrected = corrected_command(WRAPPED, devices=CONTAINER_SHAPES[compute_profile].gpus)

        assert corrected is not None
        allow(tuple(shlex.split(corrected)), compute_profile=compute_profile)


def test_the_correction_for_a_module_command_runs_the_module_rather_than_two_of_them() -> None:
    """Mutation: prefix the launcher and leave the interpreter's own ``-m`` alone.

    ``python -m olmo_core.train`` is a real submission shape, and the naive correction is
    ``python -m torch.distributed.run ... -m olmo_core.train``, which reads like two module
    arguments to one interpreter. It is not: the second ``-m`` is torchrun's own store_true
    flag saying the training script is a module name. Asserted through the guard rather than
    against the string, so the reasoning is checked rather than the spelling.
    """
    corrected = corrected_command(("python", "-m", "olmo_core.train"), devices=4)

    assert corrected is not None
    allow(tuple(shlex.split(corrected)), compute_profile=FOUR_GPUS)


def test_a_command_that_cannot_be_rewritten_is_still_refused_with_the_launcher_named() -> None:
    """Mutation: return no message when the correction cannot be built.

    The rewrite splices the launcher after the interpreter, which needs the command to
    start with one. A shell script or a wrapper binary does not, and refusing to refuse
    would leave the expensive case uncovered for the commands least likely to be reviewed.
    """
    message = refuse(wrapped("./scripts/train.sh --steps 4000"), compute_profile=FOUR_GPUS)

    assert "torch.distributed.run" in message
    assert "--nproc-per-node=4" in message


# ---------------------------------------------------------------------------------------
# What counts as a launcher
# ---------------------------------------------------------------------------------------

#: Every spelling that actually starts one process per device, at the count `gpu-4xa10g`
#: bills for. The torch ones declare the count and the rest do not, which is the difference
#: the plan below records rather than a gap in the list.
LAUNCHERS = (
    "python -m torch.distributed.run --nproc-per-node=4 --standalone train.py",
    "python -m torch.distributed.launch --nproc-per-node=4 train.py",
    "python3 -m torch.distributed.run --nproc-per-node=4 train.py",
    "torchrun --nproc-per-node=4 --standalone train.py",
    "/opt/conda/bin/torchrun --nproc-per-node=4 train.py",
    "accelerate launch --num_processes=4 train.py",
    "deepspeed --num_gpus=4 train.py",
    "mpirun -np 4 python train.py",
    "srun --ntasks=4 python train.py",
)


@pytest.mark.parametrize("inner", LAUNCHERS, ids=[line.split()[0] for line in LAUNCHERS])
def test_every_launcher_this_platform_recognises_is_accepted(inner: str) -> None:
    """Mutation: recognise `torch.distributed.run` and nothing else.

    Six of these are what a researcher arriving from somewhere else will type first.
    Recognising one spelling would refuse five working commands, and a guard that refuses
    working commands is one people route around by picking a smaller profile -- which is
    the same waste, chosen deliberately and recorded nowhere.
    """
    allow(wrapped(inner), compute_profile=FOUR_GPUS)


@pytest.mark.parametrize("inner", LAUNCHERS, ids=[line.split()[0] for line in LAUNCHERS])
def test_a_launcher_is_recognised_without_the_shell_wrapper_too(inner: str) -> None:
    """The container execs the command directly, so the wrapper is a choice rather than a
    requirement, and a command with no variable to expand does not need one."""
    allow(tuple(shlex.split(inner)), compute_profile=FOUR_GPUS)


def test_a_launcher_named_only_inside_a_quoted_argument_is_not_an_invocation() -> None:
    """Mutation: search the command for the substring `torchrun`.

    The submission form is shlex-split, so a quoted argument arrives as one word. A note, a
    W&B tag or a config value mentioning a launcher is not a launcher, and treating it as
    one would let exactly the defect this guards through while looking covered.
    """
    message = refuse(
        wrapped("python train.py --note 'run this under torchrun next time'"),
        compute_profile=FOUR_GPUS,
    )

    assert FOUR_GPUS in message


def test_a_launcher_named_only_in_a_comment_is_not_an_invocation() -> None:
    """Mutation: treat every word as a candidate program.

    A word beginning with `#` starts a comment for the shell that runs it, so everything
    after it is text. It reads exactly like an invocation to anything scanning tokens.
    """
    refuse(wrapped("python train.py  # torchrun --nproc-per-node=4"), compute_profile=FOUR_GPUS)


def test_a_launcher_named_as_a_bare_argument_is_not_an_invocation() -> None:
    """Not every mention is quoted. `--launcher torchrun` names one in argument position."""
    refuse(wrapped("python train.py --launcher torchrun"), compute_profile=FOUR_GPUS)


def test_a_launcher_after_a_shell_operator_is_an_invocation() -> None:
    """The mirror of the three above, and the reason position is read rather than order.

    A command that changes directory or exports something first puts the launcher second,
    and refusing it would be a false refusal on an ordinary command.
    """
    allow(
        wrapped("export NCCL_DEBUG=INFO && torchrun --nproc-per-node=4 train.py"),
        compute_profile=FOUR_GPUS,
    )


def test_an_environment_assignment_in_front_of_a_launcher_does_not_hide_it() -> None:
    allow(
        wrapped("NCCL_DEBUG=INFO torchrun --nproc-per-node=4 train.py"),
        compute_profile=FOUR_GPUS,
    )


def test_the_shells_this_unwraps_include_every_one_the_manifest_contract_knows() -> None:
    """A seam, because the same wrapper is parsed in two places for different reasons.

    ``contracts/validation.py`` refuses a ``bash -lc`` whose quoting was lost, and knows
    which programs read one argument as a whole command line. This module has to see inside
    the same wrappers. The set is restated rather than imported because that module is
    packaged into both Lambda zips and this check is not, and a word added there and not
    here would be a wrapper this guard cannot see into -- which fails open.

    The private name is read deliberately: a second spelling of the same list is exactly
    what this asserts cannot drift.
    """
    from edullm_platform.contracts.validation import _SHELLS_THAT_TAKE_A_COMMAND_STRING

    unseen = {
        shell.rsplit("/", maxsplit=1)[-1]
        for shell in _SHELLS_THAT_TAKE_A_COMMAND_STRING
    } - SHELLS_THAT_READ_A_COMMAND_STRING

    assert unseen == set(), (
        f"the manifest contract knows {sorted(unseen)} reads a command string and this "
        "module does not look inside it, so a launcher written there is invisible here"
    )


# ---------------------------------------------------------------------------------------
# One process per device, in both directions
# ---------------------------------------------------------------------------------------


def test_fewer_ranks_than_devices_is_refused_and_the_message_names_both() -> None:
    """Two idle A10Gs billed for twenty-four hours is $68 of the same waste, so it is the
    same refusal rather than a warning.

    The submitter has already thought about ranks, which is what makes this different from
    the no-launcher case and why the message says what to change rather than what to add.
    """
    message = refuse(
        wrapped("torchrun --nproc-per-node=2 --standalone train.py"),
        compute_profile=FOUR_GPUS,
    )

    assert FOUR_GPUS in message
    assert "2" in message
    assert "4" in message


def test_torchrun_with_no_rank_count_is_refused_because_its_own_default_is_one() -> None:
    """Mutation: treat the presence of a launcher as the whole answer.

    ``--nproc-per-node`` defaults to 1. So ``torchrun train.py`` on a four-GPU shape is the
    original defect with a launcher in front of it: one rank, four devices billed, exit
    zero. A guard satisfied by the word `torchrun` would pass it.
    """
    message = refuse(wrapped("torchrun --standalone train.py"), compute_profile=FOUR_GPUS)

    assert "--nproc-per-node is 1 when it is not given" in message
    assert "--nproc-per-node=4" in message
    assert "1 process," in message


def test_more_ranks_than_devices_is_refused_on_a_single_gpu_profile() -> None:
    """The reverse case, guarded rather than left alone, and the reason is that it is free.

    Four ranks on one device is either an immediate `invalid device ordinal` -- which is at
    least loud -- or four processes contending for one card, which is slower than one and
    reports nothing. Both numbers are known here and the check costs nothing, so the
    asymmetry that justifies guarding the expensive direction justifies this one too.
    """
    message = refuse(
        wrapped("torchrun --nproc-per-node=4 --standalone train.py"),
        compute_profile=ONE_GPU,
    )

    assert ONE_GPU in message
    assert "4 processes" in message
    # `1 GPUs` is the kind of seam that makes a refusal read as machine output, which is
    # exactly when people stop reading the sentence after it.
    assert "1 GPU " in message
    assert "1 GPUs" not in message


@pytest.mark.parametrize(
    "inner",
    [
        "torchrun --nproc-per-node 4 train.py",
        "torchrun --nproc_per_node=4 train.py",
        "torchrun --nproc_per_node 4 train.py",
    ],
)
def test_both_spellings_of_the_rank_flag_and_both_ways_of_giving_it_a_value(
    inner: str,
) -> None:
    """torchrun is argparse, so the underscore and the hyphen are the same flag to it.

    Reading only one spelling would refuse a working command for a reason the submitter
    cannot see, which is the worst kind of false refusal: the flag is right there.
    """
    allow(wrapped(inner), compute_profile=FOUR_GPUS)


@pytest.mark.parametrize("value", ["auto", "gpu", "$RANKS"])
def test_a_rank_count_this_cannot_read_is_allowed_rather_than_guessed(value: str) -> None:
    """`auto` and `gpu` resolve to the device count at runtime and a variable resolves to
    whatever the shell has.

    None of the three is a number this can compare, and the direction to fail in is the one
    that does not refuse a correct command: the submitter has named a launcher and named a
    count, which is the whole of what this guard is for.
    """
    allow(
        wrapped(f"torchrun --nproc-per-node={value} --standalone train.py"),
        compute_profile=FOUR_GPUS,
    )


def test_a_launcher_whose_count_this_cannot_read_is_allowed() -> None:
    """`deepspeed`, `mpirun`, `srun` and `accelerate launch` each spell the count
    differently and two of them take it from the scheduler.

    Reading four more flag vocabularies would buy a narrower check and a wider set of false
    refusals. The presence of the launcher is what is asserted, and the plan says so rather
    than claiming a number it did not read.
    """
    plan = read_launch_plan(("mpirun", "-np", "4", "python", "train.py"))

    assert plan.launcher == "mpirun"
    assert plan.processes is None


# ---------------------------------------------------------------------------------------
# What is deliberately not checked
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("compute_profile", SINGLE_GPU_PROFILES)
def test_a_single_gpu_profile_needs_no_launcher(compute_profile: str) -> None:
    """One process for one device is the default shape of every command on the form, and
    the guide's whole worked example is one of them."""
    allow(WRAPPED, compute_profile=compute_profile)


def test_a_cpu_profile_is_not_checked_at_all() -> None:
    """Mutation: apply the rule wherever a rank count can be read.

    ``torchrun --nproc-per-node=8`` on a 4-vCPU CPU container is a legitimate gloo
    data-parallel job and the device count this rule is about is zero. There is nothing here
    to be idle and nothing to oversubscribe.
    """
    allow(wrapped("torchrun --nproc-per-node=8 tokenize.py"), compute_profile=NO_GPUS)
    allow(WRAPPED, compute_profile=NO_GPUS)


def test_a_profile_with_no_container_shape_is_left_to_the_refusal_that_owns_it() -> None:
    """Mutation: refuse an unknown profile here as well.

    A profile with no shape cannot run: ``batch_register_job_definition_request`` raises
    ``UnshapedComputeProfileError`` for it, and an unregistered one is refused for having no
    rate before this is reached. Inventing a third refusal would name the launcher for a
    submission whose problem is that the profile does not exist.
    """
    allow(WRAPPED, compute_profile="gpu-64xz9000")


# ---------------------------------------------------------------------------------------
# The way through
# ---------------------------------------------------------------------------------------


def test_the_waiver_lets_a_deliberate_single_process_run_through() -> None:
    """A benchmark, a memory profile or an inference sweep that places its own devices is a
    real reason to hold a multi-GPU box with one process, and a guard with no way out is one
    people escape by picking a profile that fits -- which wastes the same money and records
    no reason at all.

    It is an assignment rather than a form field because it travels in the command, into the
    hashed manifest, and into the container's own environment. A checkbox on the form would
    be ticked once and inherited by every later submission that copied it.
    """
    allow(
        wrapped(f"{LAUNCH_CHECK_WAIVER} python benchmarks/memory.py --batch 64"),
        compute_profile=FOUR_GPUS,
    )


def test_the_waiver_works_wherever_it_is_written_in_the_command() -> None:
    """A shell comment and an assignment are the two places a token is inert, and which one
    is available depends on whether the command runs under a shell at all."""
    allow(wrapped(f"python bench.py  # {LAUNCH_CHECK_WAIVER}"), compute_profile=FOUR_GPUS)
    allow(("python", "bench.py", LAUNCH_CHECK_WAIVER), compute_profile=FOUR_GPUS)


def test_the_waiver_has_to_be_the_exact_token() -> None:
    """Mutation: match on `EDULLM_LAUNCH_CHECK`, or case-insensitively.

    A waiver that can be reached by nearly typing it is not a decision. Prose that happens
    to quote it -- a note, this message being pasted into a config value -- arrives as one
    word after splitting and does not match.
    """
    refuse(wrapped("python bench.py EDULLM_LAUNCH_CHECK=off"), compute_profile=FOUR_GPUS)
    refuse(wrapped("python bench.py edullm_launch_check=waived"), compute_profile=FOUR_GPUS)
    refuse(
        wrapped(f"python bench.py --note 'see {LAUNCH_CHECK_WAIVER}'"),
        compute_profile=FOUR_GPUS,
    )


def test_the_refusal_says_how_to_get_through_it() -> None:
    """A guard whose escape is documented only in the pull request that added it is a guard
    people work around by changing the profile."""
    assert LAUNCH_CHECK_WAIVER in refuse(WRAPPED, compute_profile=FOUR_GPUS)


def test_a_waived_run_puts_a_sentence_in_front_of_the_lead_who_releases_it() -> None:
    """WHAT MAKES THE ESCAPE ACCOUNTABLE RATHER THAN SILENT.

    The command is not on the approver page, so a waiver written into it would otherwise be
    invisible to the one person who could ask about it. The note is returned only when the
    waiver is what let the command through -- a waiver on a command that needed none says
    nothing and would train a reader to skip the line.
    """
    waived = waived_launch_check_note(
        command=wrapped(f"{LAUNCH_CHECK_WAIVER} python bench.py"),
        compute_profile=FOUR_GPUS,
    )

    assert waived is not None
    assert FOUR_GPUS in waived
    assert "4" in waived

    assert (
        waived_launch_check_note(
            command=wrapped(f"{LAUNCH_CHECK_WAIVER} torchrun --nproc-per-node=4 train.py"),
            compute_profile=FOUR_GPUS,
        )
        is None
    )
    assert (
        waived_launch_check_note(
            command=wrapped(f"{LAUNCH_CHECK_WAIVER} python bench.py"),
            compute_profile=NO_GPUS,
        )
        is None
    )


# ---------------------------------------------------------------------------------------
# Through the compile step a submission actually takes
# ---------------------------------------------------------------------------------------


def test_compiling_a_four_gpu_submission_without_a_launcher_is_refused() -> None:
    """The rule reached through the function the workflow calls, rather than in isolation.

    ``olmo-core-train`` on ``gpu-4xa10g`` is the submission the defect was found on:
    twenty-four hours, two attempts, $272 of ceiling, and a quarter of the work.
    """
    with pytest.raises(SubmissionRefusedError) as exc_info:
        compile_payload(olmo_payload(command=["python", "-m", "olmo_core.train"]))

    assert "gpu-4xa10g" in str(exc_info.value)


def test_the_submitted_compute_profile_is_the_one_the_command_is_checked_against() -> None:
    """Mutation: read a device count from anywhere but the profile the submission names.

    The machine is a field on the form and the only statement of it anywhere, since the
    workload profile stopped declaring one. This is the case that used to say an override
    beat the catalog; there is no catalog answer to beat now, and what it still says is that
    a four-rank command on an eight-GPU shape is refused for the shape it names.
    """
    with pytest.raises(SubmissionRefusedError) as exc_info:
        compile_payload(
            olmo_payload(
                compute_profile="gpu-8xh100",
                command=shlex.split(
                    "torchrun --nproc-per-node=4 --standalone .edullm/train_on_corpus.py"
                ),
            )
        )

    assert "gpu-8xh100" in str(exc_info.value)


def test_a_compiled_four_gpu_submission_with_a_launcher_still_compiles() -> None:
    """A check that refused everything would pass every test above."""
    compiled = compile_payload(olmo_payload())

    assert compiled.manifest.compute_profile == FOUR_GPUS


def test_the_approver_context_carries_the_waiver_when_a_run_uses_one() -> None:
    """A benchmark on the four-GPU training workload, which is two waivers rather than one.

    ``olmo-core-train`` carries a checkpoint contract, this submission names four devices,
    and a benchmark writes no checkpoint, so the second check refuses this command too. Both are
    waived here because both are genuinely being waived; the point of the case is that the
    device-count one reaches the approver page, and it still does beside another.
    """
    compiled = compile_payload(
        olmo_payload(
            command=[
                "bash",
                "-lc",
                f"{LAUNCH_CHECK_WAIVER} {CHECKPOINT_CHECK_WAIVER} python bench.py",
            ]
        )
    )

    assert LAUNCH_CHECK_WAIVER in render(compiled)


def test_the_approver_context_says_nothing_about_a_run_that_needed_no_waiver() -> None:
    assert LAUNCH_CHECK_WAIVER not in render(compile_payload(olmo_payload()))
