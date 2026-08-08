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

**AND THE GUARD ABOVE ESTABLISHES LESS THAN ITS NAME PROMISES, WHICH IS WHY**
:func:`unverified_resume_note` **IS IN THE SAME MODULE.** Two checks stand between a
submission and a second attempt: :func:`~edullm_platform.contracts.validation
.require_checkpoint_for_retries` asks whether the workload profile carries a checkpoint
contract, and the guard above asks whether the command expands the variable. Neither reads
the codebase, and the thing that decides whether a retry resumes is in the codebase.

``edullm-p1`` is the case that proved it. The contract exists, the command expands the
variable, the trainer honours it on save, and a second attempt starts from step 0 for three
independent reasons in that repository: ``RECOVERY_MODE=fail`` is written into every child
environment, so no submitted command can change it; every attempt gets a fresh container, so
the fail-closed check for leftovers finds an empty scratch directory and the trainer reports
starting from nothing; and ``resolve_load_path`` raises by name on any ``s3://`` load path
with "S3 checkpoint resume is disabled", which is the only kind of path this platform hands
out. ``open-instruct-scored-rewards`` fails the same way for a fourth reason:
``grpo_fast.py`` gates its load on ``os.path.exists(checkpoint_state_dir)``, a local-filesystem
test against an ``s3://`` URI, so it logs that it is skipping the load and sets
``optimization_steps_done`` to zero. Both would pass both checks, which is what makes them a
pair of checks that cannot fail rather than a pair that has not fired yet.

So the honest instrument is a sentence rather than a refusal. The platform can see a
registry entry, a command and a reviewed catalog; it cannot see a trainer's load path, and a
per-repository field asserting that one resumes would be the same easy question standing in
for the same hard one, one level further up -- and would refuse a run in the other direction
on the day a repository changed its trainer.
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
    "unverified_resume_note",
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


def unverified_resume_note(
    *,
    maximum_attempts: int,
    workload_profile: str,
    checkpoint: CheckpointContract | None,
) -> str | None:
    """What a second attempt costs, and the part of "is it worth it" nothing here answered.

    Returned for every submission asking for more than one attempt, and ``None`` for the
    rest, because a single-attempt run has no second attempt to say anything about. That is
    the only condition. The two waiver notes in this tree appear when a submitter did
    something unusual; this one appears whenever the arithmetic multiplies, because the gap
    it describes is in the platform rather than in the submission and every multi-attempt
    run is standing over it.

    **THE ARITHMETIC IT SITS UNDER IS RIGHT AND IS NOT WHAT THIS CORRECTS.** Two attempts
    price at twice one attempt, which is what a lead approves and what the run may spend.
    What the figure cannot say is whether the second attempt does anything the first did
    not, and the ceiling is identical either way.

    **PLAIN PROSE AND NO MARKDOWN, BECAUSE THE SAME STRING IS READ IN THREE PLACES.** The
    approver page renders markdown, ``edullm check --json`` hands this to a caller verbatim,
    and a terminal shows it as typed. ``placement_said`` settled this already and for the
    same reason; emphasis that reads as bold in one place is asterisks in the other two.

    **THE TIMEOUT SENTENCE IS THE LOAD-BEARING ONE AND IS THE LEAST OBVIOUS.** A reader who
    knows :data:`~edullm_platform.execution.RETRY_ONLY_WHAT_A_RETRY_FIXES` will conclude that
    a second attempt is nearly unreachable, since the only ``RETRY`` arm matches ``Host
    EC2*`` and every compute environment in ``infra/`` is provisioned ``Type: EC2`` rather
    than SPOT, so nothing reclaims a host on purpose. That reading misses the arm that
    matches no rule at all. Batch retries a failure none of the ``EvaluateOnExit`` entries
    match -- documented on ``AWS::Batch::JobDefinition EvaluateOnExit`` and on the job
    definition parameters page -- and an attempt stopped for outrunning
    ``attemptDurationSeconds`` carries the status reason ``Job attempt duration exceeded
    timeout`` and no container exit code, so the exit-code rule globbing ``*`` has nothing to
    match and neither of the other two applies. The one second attempt this platform reliably
    spends is therefore the one on the run that could not finish in its bound, which is
    exactly the run for which starting again from nothing cannot help.

    **AND THAT DERIVATION IS FOR A READER OF THIS MODULE RATHER THAN FOR A SUBMITTER.** The
    sentence used to carry it: "because Batch retries a failure matching none of its rules
    and an attempt stopped at its runtime bound reports no container exit code for the rules
    to match". It is true, it is written out here and again in ``config/policy.yaml``, and
    it answers an objection only somebody who already knows ``EvaluateOnExit`` can raise. A
    submitter deciding ``--attempts`` needs the finding, which is that the retry they are
    paying for lands on the run that ran out of time and gets the same bound again; the
    mechanism behind it changes nothing they can do. The finding stayed and the derivation
    went, which is a fifth of the paragraph a first-time reader was spending on Batch's
    retry semantics.
    """
    if maximum_attempts <= 1:
        return None
    if checkpoint is None:
        # Unreachable through compile_submission, which refuses this pairing on the manifest.
        # Said rather than skipped anyway: silence here would be silence in the one case that
        # is worst, which is the shape of defect this whole note exists to report.
        return (
            f"This run is priced for {maximum_attempts} attempts and {workload_profile!r} "
            "declares no checkpoint contract, so every attempt after the first repeats the "
            "whole of the first at the same price and can reach no further."
        )
    declared = (
        "declares a checkpoint every "
        f"{checkpoint.interval_minutes} minutes that a retry resumes from"
        if checkpoint.resume_required
        else f"declares a checkpoint every {checkpoint.interval_minutes} minutes"
    )
    return (
        f"This run is priced for {maximum_attempts} attempts and each one costs what the "
        f"first did. Whether the later ones buy anything depends on whether the program "
        f"resumes, which nothing on this platform establishes: {workload_profile!r} "
        f"{declared}, and what is checked is that the declaration exists and that the "
        f"command expands ${CHECKPOINT_DIRECTORY_VARIABLE}. Neither check reads the "
        "codebase, and a trainer that writes to that prefix and never loads back from it "
        "passes both -- which two of the six registered repositories were measured doing on "
        "2026-08-06. The attempt a retry is actually spent on is the one that ran out of "
        "time, and it gets the same bound again, starting wherever the program resumes from "
        "-- which is the beginning if it resumes from nowhere."
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

    **AND IT IS ATTRIBUTED TO THE PROFILE RATHER THAN STATED AS A FACT, WHICH IS THE
    DIFFERENCE THIS FUNCTION'S FIRST LINE ALREADY CLAIMED AND DID NOT MAKE.** This read
    "which a retry resumes from", inside a sentence beginning "declares", so the interval was
    the profile's word and resuming was the platform's -- and the platform has never checked
    it. Both clauses hang off ``declares`` now, and :func:`unverified_resume_note` is where a
    reader is told what stands behind the second one.
    """
    interval = f"a checkpoint contract of one checkpoint every {checkpoint.interval_minutes} minutes"
    return f"{interval}, and that a retry resumes from it" if checkpoint.resume_required else interval


def _why_it_is_inert(command: Sequence[str]) -> str:
    """Why this particular command names the directory and does not expand it.

    Two answers, because there are two causes and they have different remedies. A command
    that hands text to no shell needs a wrapper; a command that hands text to one has the
    wrapper already and needs the quoting inside it changed. Telling somebody with a shell
    to add a shell is the misdiagnosis this exists to stop.
    """
    if not _texts_a_shell_would_expand(tuple(command)):
        return (
            f" Nothing in this command runs a shell -- the container execs it exactly as "
            f"typed -- so the program receives the literal text "
            f"${CHECKPOINT_DIRECTORY_VARIABLE} and creates a directory by that name."
        )
    return (
        f" This command does run a shell, and the name is somewhere in it the shell will "
        f"not read: inside single quotes, behind a backslash, or after a #. The program "
        f"receives the literal text ${CHECKPOINT_DIRECTORY_VARIABLE} and creates a "
        f"directory by that name."
    )


def _refusal(
    command: Sequence[str],
    *,
    workload_profile: str,
    checkpoint: CheckpointContract,
) -> str:
    # Said only to the submitter who wrote the name somewhere the shell will not read it.
    # They have already done the thinking this refusal would otherwise be explaining, and a
    # paragraph about quoting on every refusal is a paragraph people stop reading.
    #
    # NAMING THE CAUSE THIS COMMAND HAS RATHER THAN THE FOUR IT MIGHT. This listed all of
    # them at once, so a submission with a shell in it was told it might have no shell and
    # one with no shell was told to check its quoting. Both readers go looking in the wrong
    # place, and the reader who has a shell has the worse time of it: nothing about their
    # quoting is wrong, so the sentence sends them round a loop with no exit. Which half
    # applies is known here -- a command handing text to a shell has quoting to get wrong,
    # and one handing text to nothing does not.
    inert = "" if not _names_the_directory(command) else _why_it_is_inert(command)
    example = (
        f"bash -lc 'python train.py --save-folder \"${CHECKPOINT_DIRECTORY_VARIABLE}\"'"
    )
    return (
        f"pass ${CHECKPOINT_DIRECTORY_VARIABLE} to whatever your program calls its save "
        f"folder, under a shell so that it expands. Workload profile {workload_profile!r} "
        f"declares {_contract_said(checkpoint)}, and nothing in this command expands "
        f"${CHECKPOINT_DIRECTORY_VARIABLE}.{inert} The container is given that variable and "
        "the command is exec'd exactly as typed, so a program that is not pointed at it "
        "saves where its own default says, which for the OLMo-core example is "
        "/tmp/{run_name} on local disk that stops existing with the machine. The run exits "
        "zero, the checkpoint prefix stays empty, and the retry this contract pays for "
        "starts from nothing. If this run places its own checkpoints, or is a throwaway "
        f"nobody will resume, write {CHECKPOINT_CHECK_WAIVER} into the command instead. A "
        f"command that saves where a retry looks is {example}"
    )
