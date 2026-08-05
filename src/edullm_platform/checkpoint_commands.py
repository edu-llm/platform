"""Whether a command under a checkpoint contract writes where a retry would look for it.

A workload profile's ``checkpoint`` contract is the platform's promise that the run saves
state and that a retry resumes from it, and it is what a second attempt is granted on. Batch
re-runs a retry with the same ``EDULLM_RUN_ID`` and therefore the same
``EDULLM_CHECKPOINT_DIR``, and OLMo-core's ``Trainer.fit()`` auto-resumes from its save
folder. Nothing checked that the submitted command ever pointed a trainer at that variable.

**The command is exec'd exactly as typed and nothing on this side rewrites it.** A trainer
that was not told where to save uses its own default, which for the OLMo-core example is
``/tmp/{run_name}`` -- local disk on a machine that stops existing when the job ends. The run
trains for its full twelve hours, writes checkpoints nobody can reach, exits zero, and is
recorded as an unqualified success. ``run_019fbce3-ce4b-7067-b8c7-c2cf25e6b667`` is that
shape in the account, and the audit's reconciliation is the only thing that ever said so --
hours after the money was spent, and with nothing left to repair.

**The rule this closes was already half-held.** ``compile_submission`` refuses more than one
attempt on a workload carrying no checkpoint contract, so the codebase already takes the
position that a retry bound and a checkpoint contract must agree. The half it never checked
is the one that costs twelve hours of GPU time.

**PROGRAM-AGNOSTIC, BECAUSE THE FLAG IS NOT THE CONTRACT SURFACE AND THE VARIABLE IS.**
``--save-folder`` is OLMo-core's spelling; a second registered repository's trainer will
spell it differently, and a guard that matched on the flag would stop applying on the day it
was most needed. What this platform actually promises is
:data:`CHECKPOINT_DIRECTORY_VARIABLE`, which :mod:`edullm_platform.execution` puts in the
container, so the requirement is that the command reference it and not that it reference it
in any particular way.

**What counts as a reference is the whole difficulty.** Every real command arrives inside
``bash -lc '...'``, because ``ContainerOverrides.Command`` is exec form and a variable in it
is not expanded at all -- so the reference lives in the wrapper's text rather than in the
argv, and it has to be read there. A mention the shell will never expand is not a reference:
single quotes suppress expansion, a backslash escapes the ``$``, an unquoted ``#`` starts a
comment, and a command with no shell in front of it expands nothing anywhere. All four read
as a reference to anything searching for a substring, and accepting them would pass exactly
the submission this exists to refuse while looking covered. The scan is therefore over the
characters of the text a shell is handed rather than over words, because
:func:`shlex.split` removes the quotes that decide the answer.

**Asked at compile time, for the reason recorded in
:mod:`edullm_platform.launchers`.** Both halves are known there -- the workload fixes the
contract and the command is on the form -- and a refusal before the approval gate costs
nobody anything, where one after it has spent a lead's attention on a decision that could not
have gone the other way.

**The escape is the same mechanism and the same spelling as the device-count waiver.**
:data:`CHECKPOINT_CHECK_WAIVER` travels in the command, so it is inside the hashed manifest
and the immutable lineage record, and :func:`waived_checkpoint_check_note` puts it in front
of the lead releasing the run, because the command is not on the approver page. Two guards
with two conventions would be two things to remember and one of them would be remembered
wrongly. The cases it exists for are real: a program that derives its own checkpoint path,
and a deliberate throwaway -- the historical run above was the second, a ``--dry-run`` on a
training profile, which resolves a config and trains nothing.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from typing import Final

from .contracts.workload import CheckpointContract
from .errors import CheckpointPathNotInCommandError
from .launchers import (
    MAXIMUM_WRAPPER_DEPTH,
    carries_the_token,
    shell_command_string,
    simple_command_segments,
)

__all__ = [
    "CHECKPOINT_CHECK_WAIVER",
    "CHECKPOINT_DIRECTORY_VARIABLE",
    "expands_the_checkpoint_directory",
    "require_a_save_folder_a_retry_can_find",
    "waived_checkpoint_check_note",
]

#: The variable the container is given, and the whole of what this platform promises about
#: where a run's checkpoints go. Named once here rather than written into the pattern and the
#: three sentences below, so that renaming it in ``execution.py`` cannot leave this agreeing
#: with a variable nothing sets.
CHECKPOINT_DIRECTORY_VARIABLE: Final = "EDULLM_CHECKPOINT_DIR"

#: What a submitter writes to say that this run's checkpoints are deliberately not the
#: platform's business. Spelled to match ``EDULLM_LAUNCH_CHECK=waived`` exactly, because the
#: two escapes should be one convention rather than two.
CHECKPOINT_CHECK_WAIVER: Final = "EDULLM_CHECKPOINT_CHECK=waived"

#: ``$NAME`` and ``${NAME``, with the trailing lookahead doing the load-bearing work.
#: ``$EDULLM_CHECKPOINT_DIRECTORY`` is a variable nothing sets, so a command using it expands
#: to the empty string and the trainer falls back to the default this guard is about -- and
#: the name looks more correct than the correct one, which is exactly how it gets typed.
#: ``${NAME%/}`` and ``${NAME:-...}`` are ordinary parameter expansion and are references, so
#: the lookahead admits any character that cannot continue an identifier.
_REFERENCE: Final = re.compile(rf"\$\{{?{CHECKPOINT_DIRECTORY_VARIABLE}(?![A-Za-z0-9_])")

#: Inside double quotes a backslash escapes only these; before anything else it is literal.
#: Getting this wrong in either direction misreads ``"\$EDULLM_CHECKPOINT_DIR"``, which is a
#: submitter having deliberately written the name and not the value.
_DOUBLE_QUOTED_ESCAPES: Final = frozenset('$`"\\\n')

#: What ends a word, and therefore what a ``#`` has to follow to start a comment. ``foo#bar``
#: is one word to a shell rather than ``foo`` and a comment.
_WORD_ENDS: Final = frozenset(";&|()<>")


def require_a_save_folder_a_retry_can_find(
    *,
    command: Sequence[str],
    workload_profile: str,
    checkpoint: CheckpointContract | None,
) -> None:
    """Refuse a run whose checkpoint contract nothing in its command would keep.

    A workload with no contract is not checked, and that is the rule rather than an omission:
    it promises nothing, so a command that writes no checkpoint is the correct outcome for it.
    The refusal is about a promise, and there is no promise to keep.

    Raises :class:`~edullm_platform.errors.SubmissionRefusedError`, as the neighbouring
    command rule does, so the caller needs no second branch.
    """
    if checkpoint is None:
        return
    if carries_the_token(command, CHECKPOINT_CHECK_WAIVER):
        return
    if expands_the_checkpoint_directory(command):
        return
    raise CheckpointPathNotInCommandError(
        _refusal(command, workload_profile=workload_profile, checkpoint=checkpoint)
    )


def waived_checkpoint_check_note(
    *,
    command: Sequence[str],
    workload_profile: str,
    checkpoint: CheckpointContract | None,
) -> str | None:
    """The sentence an approver is owed when a waiver is what let this command through.

    Returned only when the waiver did something. A command that already saves where a retry
    looks and carries the token as well is waiving nothing, and a line on the approver page
    for every such run is the line readers learn to skip.
    """
    if checkpoint is None or not carries_the_token(command, CHECKPOINT_CHECK_WAIVER):
        return None
    if expands_the_checkpoint_directory(command):
        return None
    return (
        f"**This run waives the checkpoint-directory check.** `{workload_profile}` declares "
        f"{_contract_said(checkpoint)} and nothing in the command expands "
        f"`${CHECKPOINT_DIRECTORY_VARIABLE}`, which `{CHECKPOINT_CHECK_WAIVER}` in the "
        "command declares is deliberate. Nothing verifies that a retry of this run would "
        "find anything to resume from."
    )


def expands_the_checkpoint_directory(command: Sequence[str]) -> bool:
    """Whether a shell running this command would expand the checkpoint directory anywhere.

    Read over the raw text of every command string a shell is handed, outermost first and
    then inside any wrapper those texts start themselves. The argv's own words are not
    scanned, and that is the point rather than an oversight: the container execs the command,
    so a ``$`` in an argv word is twenty-two literal characters and a directory OLMo-core
    will cheerfully create.
    """
    return any(_expands_in(text) for text in _texts_a_shell_would_expand(tuple(command)))


def _names_the_directory(command: Sequence[str]) -> bool:
    """Whether the variable's name appears at all, expanded or not.

    Only ever asked once :func:`expands_the_checkpoint_directory` has said no, so a true
    answer means the submitter wrote the name somewhere inert. That is the one case where
    they have done the work and still have nothing, and it is worth a different sentence.
    """
    return any(CHECKPOINT_DIRECTORY_VARIABLE in word for word in command)


# ---------------------------------------------------------------------------------------
# Reading the command
# ---------------------------------------------------------------------------------------


def _texts_a_shell_would_expand(words: Sequence[str], depth: int = 0) -> list[str]:
    """Every command string a shell in this argv is handed, wrappers within wrappers included.

    In command position only, which is what ``simple_command_segments`` is for. A submission
    is shlex-split, so ``--launcher bash -c '...'`` puts the word ``bash`` in argument
    position, and treating that as a shell would find a reference in a string nothing runs.
    """
    if depth >= MAXIMUM_WRAPPER_DEPTH:
        return []
    found: list[str] = []
    for segment in simple_command_segments(words):
        text = shell_command_string(segment)
        if text is None:
            continue
        found.append(text)
        try:
            inner = shlex.split(text)
        except ValueError:
            # Unbalanced quoting inside the wrapper. contracts/validation.py already refuses
            # that on the manifest and says the right thing about it; a second refusal here
            # would report a missing save folder for a command nobody can read.
            continue
        found.extend(_texts_a_shell_would_expand(inner, depth + 1))
    return found


def _expands_in(text: str) -> bool:
    """Whether a shell handed this text would substitute the checkpoint directory into it.

    A character scan rather than a word scan, and the reason is that
    :func:`shlex.split` deletes the evidence: it strips both kinds of quote, so
    ``"$EDULLM_CHECKPOINT_DIR"`` and ``'$EDULLM_CHECKPOINT_DIR'`` arrive as the same word and
    only one of them is a reference. The four states below are the four a shell is in when it
    decides whether a ``$`` introduces an expansion.
    """
    quote: str | None = None
    after_a_word_end = True
    index = 0
    while index < len(text):
        character = text[index]
        if quote is None and character == "\\":
            index += 2
            after_a_word_end = False
            continue
        if (
            quote == '"'
            and character == "\\"
            and index + 1 < len(text)
            and text[index + 1] in _DOUBLE_QUOTED_ESCAPES
        ):
            index += 2
            continue
        if quote is None and character in "'\"":
            quote = character
            index += 1
            after_a_word_end = False
            continue
        if character == quote:
            quote = None
            index += 1
            after_a_word_end = False
            continue
        if quote is None and character == "#" and after_a_word_end:
            newline = text.find("\n", index)
            if newline < 0:
                return False
            index = newline + 1
            after_a_word_end = True
            continue
        if quote != "'" and character == "$" and _REFERENCE.match(text, index):
            return True
        after_a_word_end = character.isspace() or character in _WORD_ENDS
        index += 1
    return False


# ---------------------------------------------------------------------------------------
# What a submitter reads
# ---------------------------------------------------------------------------------------


def _contract_said(checkpoint: CheckpointContract) -> str:
    """The contract in the terms it is written in, so the refusal quotes rather than asserts.

    ``resume_required`` is stated only when it is true. It is a declaration no code branches
    on, and saying "which no retry has to resume from" on a profile that allows two attempts
    would read as permission to ignore the rest of this.
    """
    interval = f"a checkpoint contract of one checkpoint every {checkpoint.interval_minutes} minutes"
    return f"{interval}, which a retry resumes from" if checkpoint.resume_required else interval


def _refusal(
    command: Sequence[str],
    *,
    workload_profile: str,
    checkpoint: CheckpointContract,
) -> str:
    # Said only to the submitter who wrote the name somewhere the shell will not read it.
    # They have already done the thinking this refusal would otherwise be explaining, and a
    # paragraph about quoting on every refusal is a paragraph people stop reading.
    inert = (
        f" The name is in this command in a position nothing expands -- inside single "
        f"quotes, behind a backslash, in a comment, or in a command the container execs "
        f"directly rather than through a shell -- so what would reach the program is the "
        f"literal text ${CHECKPOINT_DIRECTORY_VARIABLE}, and it will create a directory by "
        f"that name on the instance."
        if _names_the_directory(command)
        else ""
    )
    return (
        f"workload profile {workload_profile!r} declares "
        f"{_contract_said(checkpoint)}, and nothing in this command expands "
        f"${CHECKPOINT_DIRECTORY_VARIABLE}.{inert} The container is given that variable and "
        "the command is exec'd exactly as typed, so a program that is not pointed at it "
        "saves where its own default says -- /tmp/{run_name} for the OLMo-core example, "
        "which is local disk on a machine that stops existing. The run exits zero, the "
        "checkpoint prefix stays empty, and the retry this contract is what pays for starts "
        "from nothing. Pass it to whatever your program calls its save folder, under a shell "
        "so that it expands: bash -lc 'python train.py --save-folder "
        f'"${CHECKPOINT_DIRECTORY_VARIABLE}"\'. If this run places its own checkpoints, or '
        f"is a throwaway nobody will resume, write {CHECKPOINT_CHECK_WAIVER} into the "
        "command, which records that on the run rather than leaving it to be read off an "
        "empty prefix weeks later."
    )
