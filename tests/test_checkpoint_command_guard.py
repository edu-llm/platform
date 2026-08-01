"""A run that promised a checkpoint and whose command writes nowhere a retry will look.

A workload profile's checkpoint contract is what buys it a second attempt: Batch re-runs a
retry with the same ``EDULLM_RUN_ID`` and therefore the same ``EDULLM_CHECKPOINT_DIR``, and
OLMo-core's ``Trainer.fit()`` auto-resumes from its save folder. Nothing checked that the
submitted command ever pointed a trainer at that variable. The command is exec'd exactly as
typed, so a trainer that was not told writes to its own default -- ``/tmp/{run_name}`` for the
OLMo-core example, which is local disk on a machine that stops existing. The run exits zero,
the prefix stays empty, and the retry the platform promised starts from nothing.

**The asymmetry is what makes this worth the module.** ``compile_submission`` already refuses
the opposite mistake -- more than one attempt on a workload with no checkpoint contract -- so
the codebase already holds that a retry bound and a checkpoint contract must agree. The half
it never checked is the one that costs twelve hours of GPU time.

**What counts as a reference is the whole difficulty, and getting it wrong in the permissive
direction reintroduces the defect.** ``--save-folder "$EDULLM_CHECKPOINT_DIR"`` counts and so
does ``${EDULLM_CHECKPOINT_DIR}``. A mention inside single quotes does not, because the shell
hands the program the twenty-two literal characters; nor does one in a comment; nor does one
in a command the container execs directly, which is the trap
``execution.py`` records against the variable itself. Every one of those reads as a reference
to anything searching for a substring, and a guard satisfied by them would pass exactly the
submission it exists to refuse while looking covered.
"""

from __future__ import annotations

import shlex

import pytest
from test_phase2_submission import compile_payload, olmo_payload, render, workload_profile

from edullm_platform.checkpoint_commands import (
    CHECKPOINT_CHECK_WAIVER,
    CHECKPOINT_DIRECTORY_VARIABLE,
    expands_the_checkpoint_directory,
    require_a_save_folder_a_retry_can_find,
    waived_checkpoint_check_note,
)
from edullm_platform.contracts.workload import CheckpointContract
from edullm_platform.errors import SubmissionRefusedError

#: The two workloads that carry a contract today, read as contracts rather than as names so
#: that a third profile promising checkpoints needs nothing added here.
CONTRACTED = "olmo-core-train-1gpu"
UNCONTRACTED = "olmo-core-check-gpu"


def contract(name: str = CONTRACTED) -> CheckpointContract | None:
    return workload_profile(name).checkpoint


def wrapped(inner: str) -> tuple[str, ...]:
    return ("bash", "-lc", inner)


def refuse(command: tuple[str, ...], *, workload: str = CONTRACTED) -> str:
    with pytest.raises(SubmissionRefusedError) as exc_info:
        require_a_save_folder_a_retry_can_find(
            command=command,
            workload_profile=workload,
            checkpoint=contract(workload),
        )
    return str(exc_info.value)


def allow(command: tuple[str, ...], *, workload: str = CONTRACTED) -> None:
    require_a_save_folder_a_retry_can_find(
        command=command,
        workload_profile=workload,
        checkpoint=contract(workload),
    )


#: The line the guide prints, which is the one most submissions start from.
SAVES = wrapped(
    'python .edullm/train_on_corpus.py "$EDULLM_RUN_ID" '
    '--save-folder "$EDULLM_CHECKPOINT_DIR" --steps 4000'
)

#: The same line with the save folder left off. This is not a hypothesis: it is the shape of
#: run_019fbce3-ce4b-7067-b8c7-c2cf25e6b667, which declared a checkpoint contract, was
#: recorded as a success, and left its prefix empty.
SAVES_NOWHERE = wrapped('python .edullm/train_on_corpus.py "$EDULLM_RUN_ID" --steps 4000')


# ---------------------------------------------------------------------------------------
# The defect: a contract the command does not back
# ---------------------------------------------------------------------------------------


def test_a_contracted_command_that_never_names_the_directory_is_refused() -> None:
    """THE ONE THAT MATTERS. Mutation: check the contract and not the command.

    The workload promises the platform saves state and that a retry resumes from it. Nothing
    but the command decides whether that is true, and the command is exec'd as typed.
    """
    message = refuse(SAVES_NOWHERE)

    assert CONTRACTED in message
    assert CHECKPOINT_DIRECTORY_VARIABLE in message


def test_a_contracted_command_that_expands_the_directory_compiles() -> None:
    """A guard that refused everything would satisfy every case above it."""
    allow(SAVES)


def test_the_refusal_names_the_profile_the_variable_and_the_way_through() -> None:
    """Mutation: refuse with `checkpoint_contract_not_backed_by_command` and nothing else.

    A reason code sends a submitter to whoever wrote it. The four things that make this
    self-service are which profile carries the contract, which variable the platform sets,
    what happens to a run that ignores it, and the escape for a program that genuinely
    places its own checkpoints.
    """
    message = refuse(SAVES_NOWHERE)

    assert CONTRACTED in message
    assert f"${CHECKPOINT_DIRECTORY_VARIABLE}" in message
    assert "--save-folder" in message, (
        "the refusal names no flag at all, and OLMo-core's is the one nearly every "
        "submission here needs"
    )
    assert CHECKPOINT_CHECK_WAIVER in message


# ---------------------------------------------------------------------------------------
# What counts as a reference
# ---------------------------------------------------------------------------------------

#: Every spelling a shell expands, all of which point a program at the run's own prefix.
EXPANDED = (
    'python train.py --save-folder "$EDULLM_CHECKPOINT_DIR"',
    "python train.py --save-folder $EDULLM_CHECKPOINT_DIR",
    "python train.py --save-folder ${EDULLM_CHECKPOINT_DIR}",
    'python train.py --save-folder "${EDULLM_CHECKPOINT_DIR}"',
    'python train.py --save-folder "$EDULLM_CHECKPOINT_DIR/shards"',
    'python train.py --save-folder "${EDULLM_CHECKPOINT_DIR%/}"',
    'python train.py trainer.save_folder="$EDULLM_CHECKPOINT_DIR"',
    'SAVE_TO="$EDULLM_CHECKPOINT_DIR" python train.py --save-folder "$SAVE_TO"',
)


@pytest.mark.parametrize("inner", EXPANDED, ids=range(len(EXPANDED)))
def test_every_spelling_a_shell_expands_is_a_reference(inner: str) -> None:
    """Mutation: recognise ``"$EDULLM_CHECKPOINT_DIR"`` and nothing else.

    The braced form is what somebody writes when the path is followed by a word character,
    and the two trimming forms are ordinary parameter expansion. Refusing any of them would
    refuse a working command for a reason its author cannot see, which is the false refusal
    that teaches people the guard is broken.
    """
    allow(wrapped(inner))


#: Every spelling that reads as a reference and is not one. The shell hands the program the
#: twenty-two literal characters, or hands it nothing at all.
INERT = (
    "python train.py --save-folder '$EDULLM_CHECKPOINT_DIR'",
    "python train.py  # --save-folder $EDULLM_CHECKPOINT_DIR",
    'python train.py --save-folder "\\$EDULLM_CHECKPOINT_DIR"',
    "python train.py --note 'remember $EDULLM_CHECKPOINT_DIR next time'",
)


@pytest.mark.parametrize("inner", INERT, ids=range(len(INERT)))
def test_a_mention_the_shell_will_never_expand_is_not_a_reference(inner: str) -> None:
    """THE PERMISSIVE FAILURE, WHICH IS THE ONE THAT REINTRODUCES THE DEFECT.

    Single quotes suppress expansion outright, a backslash escapes the ``$`` inside double
    quotes, and everything after an unquoted ``#`` is text. A guard that searched the command
    for the substring would accept all four, and the run each of them describes writes to a
    directory literally named ``$EDULLM_CHECKPOINT_DIR`` on a disk that stops existing.
    """
    refuse(wrapped(inner))


def test_a_variable_whose_name_merely_starts_with_it_is_not_a_reference() -> None:
    """Mutation: match on the prefix.

    ``$EDULLM_CHECKPOINT_DIRECTORY`` is a variable nothing sets, so a command using it
    expands to the empty string and the trainer falls back to its own default -- which is the
    defect, arrived at by a name that looks more correct than the correct one.
    """
    refuse(wrapped("python train.py --save-folder $EDULLM_CHECKPOINT_DIRECTORY"))
    allow(wrapped("python train.py --save-folder $EDULLM_CHECKPOINT_DIR"))


def test_the_reference_is_read_inside_the_wrapper_most_commands_arrive_in() -> None:
    """``bash -lc`` is how the variable reaches the program at all, so it is the ordinary case.

    ``ContainerOverrides.Command`` is exec form. Without a shell nothing expands, which is
    why the guide's every worked command carries the wrapper and why a guard that only read
    the outer argv would find no reference in any real submission.
    """
    assert expands_the_checkpoint_directory(SAVES)
    assert not expands_the_checkpoint_directory(SAVES_NOWHERE)


def test_a_reference_inside_a_nested_wrapper_still_counts() -> None:
    """One shell inside another is ordinary, and the inner one does the expanding.

    The outer shell sees single quotes and expands nothing; the text it hands the inner shell
    is a command line in its own right. Reading only the outer level would refuse this.
    """
    allow(wrapped("""bash -c 'python train.py --save-folder $EDULLM_CHECKPOINT_DIR'"""))
    allow(wrapped("""cd /work && bash -c 'python t.py -s $EDULLM_CHECKPOINT_DIR'"""))


def test_a_command_exec_d_without_a_shell_expands_nothing_and_is_told_so() -> None:
    """Mutation: accept the bare word, since the name is right there.

    ``ContainerOverrides.Command`` is exec form, so ``$EDULLM_CHECKPOINT_DIR`` in it reaches
    the program as twenty-two literal characters and OLMo-core creates a directory by that
    name beside the code. This is the trap ``execution.py`` records against the variable, and
    it is the one case where the submitter has done the work and still has nothing, so the
    refusal says why rather than repeating that a reference is missing.
    """
    message = refuse(("python", "train.py", "--save-folder", "$EDULLM_CHECKPOINT_DIR"))

    assert "nothing expands" in message
    assert "bash -lc" in message


def test_a_command_that_names_the_variable_nowhere_is_not_told_about_quoting() -> None:
    """The mirror of the case above, so the extra sentence stays worth reading.

    A submitter who never wrote the variable is not confused about quoting, and a paragraph
    about single quotes on every refusal is a paragraph readers learn to skip.
    """
    assert "nothing expands" not in refuse(SAVES_NOWHERE)


# ---------------------------------------------------------------------------------------
# What is deliberately not checked
# ---------------------------------------------------------------------------------------


def test_a_workload_with_no_checkpoint_contract_is_left_alone() -> None:
    """Mutation: apply the rule to every submission.

    ``olmo-core-check-gpu`` promises nothing, allows one attempt and has no interval to
    checkpoint on, so a command that writes no checkpoint is the correct outcome for it. The
    rule is about a promise, and there is no promise here to keep.
    """
    allow(SAVES_NOWHERE, workload=UNCONTRACTED)
    assert contract(UNCONTRACTED) is None


def test_a_command_whose_quoting_cannot_be_read_is_left_to_the_refusal_that_owns_it() -> None:
    """Mutation: refuse an unbalanced wrapper here as well.

    ``contracts/validation.py`` already refuses a command whose quoting was lost, and it
    refuses it on the manifest, which is earlier and says the right thing. Inventing a second
    refusal here would report a missing save folder for a submission whose actual problem is
    that nobody can tell what it runs.
    """
    assert not expands_the_checkpoint_directory(("bash", "-lc", "python 'train.py"))


# ---------------------------------------------------------------------------------------
# The way through
# ---------------------------------------------------------------------------------------


def test_the_waiver_lets_a_program_that_places_its_own_checkpoints_through() -> None:
    """Two real cases: a program that derives the path itself, and a deliberate throwaway.

    The historical run this guard exists for was the second of those -- a ``--dry-run`` on a
    training profile, which resolves the config and trains nothing, so it never had a
    checkpoint to write. A guard with no way out is one people get past by picking a profile
    that promises nothing, which loses the retry as well and records no reason at all.

    It is an assignment in the command rather than a field on the form, and it is the same
    mechanism and the same spelling as the device-count waiver, so a researcher meets one
    convention rather than two.
    """
    allow(wrapped(f"{CHECKPOINT_CHECK_WAIVER} python .edullm/train_on_corpus.py --dry-run"))


def test_the_waiver_works_wherever_it_is_written_in_the_command() -> None:
    """Which position is inert depends on whether the command runs under a shell at all."""
    allow(wrapped(f"python train.py  # {CHECKPOINT_CHECK_WAIVER}"))
    allow(("python", "train.py", CHECKPOINT_CHECK_WAIVER))


def test_the_waiver_has_to_be_the_exact_token() -> None:
    """Mutation: match on `EDULLM_CHECKPOINT_CHECK`, or case-insensitively.

    A waiver reachable by nearly typing it is not a decision. Prose quoting it -- this
    refusal pasted into a note -- arrives as one word after splitting and does not match.
    """
    refuse(wrapped("python train.py EDULLM_CHECKPOINT_CHECK=off"))
    refuse(wrapped("python train.py edullm_checkpoint_check=waived"))
    refuse(wrapped(f"python train.py --note 'see {CHECKPOINT_CHECK_WAIVER}'"))


def test_the_two_waivers_are_spelled_the_same_way_and_are_not_the_same_token() -> None:
    """Mutation: let one waiver stand for the other.

    They answer different questions -- one says a process count is deliberate, the other says
    a checkpoint path is -- and a run that waived the first has said nothing about the second.
    What they share is the shape, so that the escape is one thing to remember.
    """
    from edullm_platform.launchers import LAUNCH_CHECK_WAIVER

    assert LAUNCH_CHECK_WAIVER.endswith("=waived")
    assert CHECKPOINT_CHECK_WAIVER.endswith("=waived")
    assert LAUNCH_CHECK_WAIVER != CHECKPOINT_CHECK_WAIVER

    refuse(wrapped(f"{LAUNCH_CHECK_WAIVER} python train.py"))


def test_a_waived_run_puts_a_sentence_in_front_of_the_lead_who_releases_it() -> None:
    """WHAT MAKES THE ESCAPE ACCOUNTABLE RATHER THAN SILENT.

    The command is not on the approver page, so a waiver written into it would otherwise be
    invisible to the one person who could ask about it. Returned only when the waiver is what
    let the command through: a waiver on a command that already saves where a retry looks
    says nothing, and a line on every run is a line readers learn to skip.
    """
    waived = waived_checkpoint_check_note(
        command=wrapped(f"{CHECKPOINT_CHECK_WAIVER} python train.py --dry-run"),
        workload_profile=CONTRACTED,
        checkpoint=contract(),
    )

    assert waived is not None
    assert CONTRACTED in waived
    assert CHECKPOINT_CHECK_WAIVER in waived

    assert (
        waived_checkpoint_check_note(
            command=wrapped(f'{CHECKPOINT_CHECK_WAIVER} python t.py -s "$EDULLM_CHECKPOINT_DIR"'),
            workload_profile=CONTRACTED,
            checkpoint=contract(),
        )
        is None
    )
    assert (
        waived_checkpoint_check_note(
            command=wrapped(f"{CHECKPOINT_CHECK_WAIVER} python train.py"),
            workload_profile=UNCONTRACTED,
            checkpoint=contract(UNCONTRACTED),
        )
        is None
    )


# ---------------------------------------------------------------------------------------
# Through the compile step a submission actually takes
# ---------------------------------------------------------------------------------------


def test_compiling_a_contracted_submission_with_no_save_folder_is_refused() -> None:
    """The rule reached through the function the workflow calls, rather than in isolation.

    Refused while the submission compiles, which is before a lead spends an approval on it
    and is where both halves are known -- the workload fixes the contract and the command is
    on the form. The same argument already put the roster check and the device-count check
    here.
    """
    with pytest.raises(SubmissionRefusedError) as exc_info:
        compile_payload(
            olmo_payload(
                command=shlex.split(
                    "bash -lc 'python -m torch.distributed.run --nproc-per-node=4 "
                    "--standalone .edullm/train_on_corpus.py'"
                )
            )
        )

    assert "olmo-core-train-4gpu" in str(exc_info.value)


def test_a_compiled_contracted_submission_that_saves_where_a_retry_looks_still_compiles() -> None:
    compiled = compile_payload(olmo_payload())

    assert compiled.manifest.checkpoint is not None
    assert expands_the_checkpoint_directory(compiled.manifest.command)


def test_the_approver_context_carries_the_checkpoint_waiver_when_a_run_uses_one() -> None:
    compiled = compile_payload(
        olmo_payload(
            command=[
                "bash",
                "-lc",
                f"{CHECKPOINT_CHECK_WAIVER} torchrun --nproc-per-node=4 bench.py",
            ]
        )
    )

    assert CHECKPOINT_CHECK_WAIVER in render(compiled)


def test_the_approver_context_says_nothing_about_a_run_that_needed_no_waiver() -> None:
    assert CHECKPOINT_CHECK_WAIVER not in render(compile_payload(olmo_payload()))
