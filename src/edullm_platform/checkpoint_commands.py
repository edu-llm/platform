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
:func:`require_a_demonstrated_resume_for_retries` **IS IN THE SAME MODULE.** Two checks used
to stand between a submission and a second attempt: :func:`~edullm_platform.contracts
.validation.require_checkpoint_for_retries` asks whether the workload profile carries a
checkpoint contract, and the guard above asks whether the command expands the variable.
Neither reads the codebase, and the thing that decides whether a retry resumes is in the
codebase.

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

**THIS MODULE ANSWERED THAT WITH A SENTENCE AND THE SENTENCE WAS NOT ENOUGH, WHICH IS THE
ONE DECISION HERE THAT HAS BEEN REVERSED.** What stood above was: "the honest instrument is
a sentence rather than a refusal. The platform can see a registry entry, a command and a
reviewed catalog; it cannot see a trainer's load path, and a per-repository field asserting
that one resumes would be the same easy question standing in for the same hard one, one
level further up -- and would refuse a run in the other direction on the day a repository
changed its trainer." Every clause of that is true about *inference* and none of it reaches
*observation*. The platform does not have to read a load path to know whether one works; it
has to have watched one work once, and a run that resumed is a thing this platform records
whether or not anybody reads it. :mod:`edullm_platform.contracts.resume_evidence` is that
record, and the field it carries is a citation of a run rather than a claim about a
codebase -- so the reviewer of the pull request adding one opens the run and sees the same
two step numbers, which is not available to a reviewer of an assertion.

The staleness objection survives and is answered by not refusing on it. An entry names the
commit whose image ran, :func:`resume_note` prints that commit and that date, and a
repository whose trainer moved since has evidence a submitter can discount. What refuses is
the absence of any evidence at all, which is where every repository stood on 2026-08-07 and
where ``olmo-core-train`` and ``edullm-alt-cl-train`` were both selling second attempts.

**AND IT IS WAIVABLE, IN THE SPELLING THE OTHER TWO ESCAPES ALREADY USE.** A refusal that
cannot be got past would make a lost host cost the whole run for anybody whose repository
has no demonstration yet, which is worse than the thing being refused.
:data:`RESUME_CHECK_WAIVER` travels in the command, so it is inside the hashed manifest and
the immutable lineage record, and :func:`resume_note` puts it in front of the lead releasing
the run.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from typing import Final

from .contracts.resume_evidence import ResumeDemonstration, ResumeDemonstrations
from .contracts.workload import CheckpointContract
from .errors import CheckpointPathNotInCommandError, ResumeNotDemonstratedError
from .launchers import (
    MAXIMUM_WRAPPER_DEPTH,
    carries_the_token,
    shell_command_string,
    simple_command_segments,
)
from .reviewed_configuration import ConfigFile

__all__ = [
    "CHECKPOINT_CHECK_WAIVER",
    "CHECKPOINT_DIRECTORY_VARIABLE",
    "RESUME_CHECK_WAIVER",
    "expands_the_checkpoint_directory",
    "require_a_demonstrated_resume_for_retries",
    "require_a_save_folder_a_retry_can_find",
    "resume_note",
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

#: What a submitter writes to buy a second attempt for a repository nobody has watched
#: resuming. Spelled to match the two escapes above exactly, because three escapes with
#: three conventions would be three things to remember and one of them would be remembered
#: wrongly.
RESUME_CHECK_WAIVER: Final = "EDULLM_RESUME_CHECK=waived"

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

#: Named in the refusal so that a submitter can open the file the refusal is about, off the
#: member rather than typed. A literal here reads as a path and this module resolves no
#: directory -- it is handed the loaded file -- so the two would drift the day the file moved
#: and the refusal would send somebody to a name nothing carries.
_DEMONSTRATIONS_FILE: Final = ConfigFile.RESUME_DEMONSTRATIONS.value


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


def require_a_demonstrated_resume_for_retries(
    *,
    command: Sequence[str],
    maximum_attempts: int,
    repository: str,
    workload_profile: str,
    demonstrations: ResumeDemonstrations,
) -> None:
    """Refuse a second attempt for a repository nobody has watched resume.

    A single-attempt run is not checked, because it has no second attempt to justify. That
    is the rule rather than an omission: this refusal is entirely about what the attempt
    factor in the price buys, and a run buying nothing is priced at one.

    The question is asked of the repository and not of the workload profile, because what
    resumes is a trainer. Two profiles over one codebase are the same load path with
    different bounds.

    Raises :class:`~edullm_platform.errors.SubmissionRefusedError`, as the neighbouring
    command rules do, so the caller needs no second branch.
    """
    if maximum_attempts <= 1:
        return
    if carries_the_token(command, RESUME_CHECK_WAIVER):
        return
    if demonstrations.for_repository(repository) is not None:
        return
    raise ResumeNotDemonstratedError(
        f"ask for one attempt, or demonstrate that {repository} resumes. This run is "
        f"priced for {maximum_attempts} attempts of {workload_profile!r} and each one "
        f"costs what the first did, so the second is worth paying for only if the program "
        f"picks up where the first stopped. Nothing has ever watched {repository} do that: "
        f"config/{_DEMONSTRATIONS_FILE} records no run of it in which a second process "
        "resumed a first one's checkpoint and went on from a step above zero. Two of the "
        "six registered repositories were measured starting again from step 0 while "
        "passing every other check on this path, so the absence is a real question rather "
        "than a formality. A demonstration is one submission: kill a training run partway, "
        "let it start again against the same $"
        f"{CHECKPOINT_DIRECTORY_VARIABLE}, and record the two step numbers. If this run "
        f"needs its second attempt before anybody has time for that, write "
        f"{RESUME_CHECK_WAIVER} into the command, which says so on the record and on the "
        "page the lead releasing it reads."
    )


def resume_note(
    *,
    command: Sequence[str],
    maximum_attempts: int,
    repository: str,
    workload_profile: str,
    checkpoint: CheckpointContract | None,
    demonstrations: ResumeDemonstrations,
) -> str | None:
    """What a second attempt buys, said out of a measurement rather than out of a hope.

    Returned for every submission asking for more than one attempt, and ``None`` for the
    rest, because a single-attempt run has no second attempt to say anything about.

    **THE ARITHMETIC IT SITS UNDER IS RIGHT AND IS NOT WHAT THIS QUALIFIES.** Two attempts
    price at twice one attempt, which is what a lead approves and what the run may spend.
    What the figure cannot say is whether the second attempt does anything the first did
    not, and the ceiling is identical either way.

    **PLAIN PROSE AND NO MARKDOWN, BECAUSE THE SAME STRING IS READ IN THREE PLACES.** The
    approver page renders markdown, ``edullm check --json`` hands this to a caller verbatim,
    and a terminal shows it as typed. ``placement_said`` settled this already and for the
    same reason; emphasis that reads as bold in one place is asterisks in the other two.

    **THE TIMEOUT SENTENCE WAS THE LOAD-BEARING ONE AND WAS MEASURED FALSE.** It said that a
    reader who knows :data:`~edullm_platform.execution.RETRY_ONLY_WHAT_A_RETRY_FIXES` will
    conclude a second attempt is nearly unreachable -- the only ``RETRY`` arm matches ``Host
    EC2*`` and every compute environment in ``infra/`` is ``Type: EC2`` rather than SPOT, so
    nothing reclaims a host on purpose -- and that the reading misses the arm matching no rule
    at all, because Batch retries a failure none of the ``EvaluateOnExit`` entries match and
    an attempt stopped for outrunning ``attemptDurationSeconds`` reports no container exit
    code for the ``*`` rule to match. The premise is right and the conclusion is wrong.
    ``run_019fdd90-99d1-70e8-a005-e341452d9458`` was submitted on 2026-08-07 with two attempts
    and a bound it could not finish inside. Batch stopped it with the status reason ``Job
    attempt duration exceeded timeout`` and reported ``FAILED`` at ``Attempts 1 of 2``. A
    timeout terminates the job rather than ending an attempt the retry rules are consulted
    about, so no second attempt was made and none is ever made that way.

    **SO WHAT THE SENTENCE TELLS A SUBMITTER IS WHAT THE SECOND ATTEMPT IS ACTUALLY FOR.** It
    is insurance against a host dying underneath a running attempt, which is the one arm that
    retries, and against nothing else anybody here has observed. The derivation above is for a
    reader of this module: it answers an objection only somebody who already knows
    ``EvaluateOnExit`` can raise, and it is written out here and again in
    ``config/policy.yaml`` for them.
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
    # The demonstration first. A command carrying the waiver token for a repository that has
    # been watched resuming is waiving nothing, and printing the waiver over the measurement
    # would report the weaker of two true things.
    demonstration = demonstrations.for_repository(repository)
    if demonstration is not None:
        return _demonstrated_resume_said(
            maximum_attempts=maximum_attempts,
            checkpoint=checkpoint,
            demonstration=demonstration,
        )
    # WHETHER THE WAIVER IS ACTUALLY THERE IS ASKED RATHER THAN ASSUMED, AND THE FIRST
    # VERSION ASSUMED IT. Past the refusal a waiver is the only way to be here, so the
    # sentence was written as though one had been used -- and `edullm check` composes this
    # note beside the refusal rather than instead of it, so every refused submission was
    # told it carried a token it had not written. A submitter reading that goes looking for
    # a waiver in their own command.
    return _no_demonstration_said(
        maximum_attempts=maximum_attempts,
        repository=repository,
        workload_profile=workload_profile,
        waived=carries_the_token(command, RESUME_CHECK_WAIVER),
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


def _demonstrated_resume_said(
    *,
    maximum_attempts: int,
    checkpoint: CheckpointContract,
    demonstration: ResumeDemonstration,
) -> str:
    """The second attempt priced against the run that showed the first one could be resumed.

    The commit and the date are both in it, and neither is decoration. They are what lets a
    reader decide whether the evidence still describes the code they are about to run, which
    is the judgement this platform declines to make on their behalf -- see the module
    docstring on why a stale entry does not refuse.
    """
    return (
        f"This run is priced for {maximum_attempts} attempts and each one costs what the "
        f"first did. The second is worth paying for here: {demonstration.repository} has "
        f"been watched resuming. On {demonstration.recorded_at.date().isoformat()}, run "
        f"{demonstration.run_id} on {demonstration.compute_profile} at commit "
        f"{demonstration.commit_sha[:12]} was stopped partway and started again against the "
        f"same checkpoint prefix; the second process reported resuming from step "
        f"{demonstration.resumed_from_step} and went on to step "
        f"{demonstration.reached_step}. The profile declares a checkpoint every "
        f"{checkpoint.interval_minutes} minutes, so the work a second attempt repeats is "
        "bounded by that interval rather than by the whole run. Read the commit above "
        "against the one you are submitting: a trainer that has changed since has evidence "
        "that is older than it is."
    )


def _no_demonstration_said(
    *,
    maximum_attempts: int,
    repository: str,
    workload_profile: str,
    waived: bool,
) -> str:
    """The sentence for a multi-attempt run standing over the gap rather than past it.

    Three ways to be here and the ``waived`` clause is what tells the reader which. A
    submitter used the token; a submission is being refused and this note is composed beside
    the refusal; or the install carries no demonstrations file at all, which
    ``edullm check`` reports separately by naming the configuration directory it read.
    """
    how = (
        f"and this submission carries {RESUME_CHECK_WAIVER}, which is what let it ask for "
        f"more than one attempt of {workload_profile!r}"
        if waived
        else f"which is why {workload_profile!r} is refused more than one attempt here"
    )
    return (
        f"This run is priced for {maximum_attempts} attempts and each one costs what the "
        f"first did, and nothing establishes that the later ones reach further than the "
        f"first. No run of {repository} has been recorded resuming a checkpoint, {how}. "
        f"What is checked besides that is that a checkpoint contract exists and that the "
        f"command expands ${CHECKPOINT_DIRECTORY_VARIABLE}; neither reads the codebase, and "
        "a trainer that writes to that prefix and never loads back from it passes both -- "
        "which two of the six registered repositories were measured doing on 2026-08-06. "
        "The second attempt is also narrower than it looks: a run stopped for outrunning its "
        "time bound is not retried at all, measured on 2026-08-07, so what the attempt factor "
        "buys is a retry on a host dying and nothing else -- starting wherever the program "
        "resumes from, which is the beginning if it resumes from nowhere."
    )


def _contract_said(checkpoint: CheckpointContract) -> str:
    """The contract in the terms it is written in, so the refusal quotes rather than asserts.

    ``resume_required`` is stated only when it is true. It is a declaration no code branches
    on, and saying "which no retry has to resume from" on a profile that allows two attempts
    would read as permission to ignore the rest of this.

    **AND IT IS ATTRIBUTED TO THE PROFILE RATHER THAN STATED AS A FACT, WHICH IS THE
    DIFFERENCE THIS FUNCTION'S FIRST LINE ALREADY CLAIMED AND DID NOT MAKE.** This read
    "which a retry resumes from", inside a sentence beginning "declares", so the interval was
    the profile's word and resuming was the platform's -- and the platform has never checked
    it. Both clauses hang off ``declares`` now, and :func:`resume_note` is where a
    reader is told what stands behind the second one.
    """
    interval = f"a checkpoint contract of one checkpoint every {checkpoint.interval_minutes} minutes"
    return f"{interval}, and that a retry resumes from it" if checkpoint.resume_required else interval


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
        f" The name is in this command where nothing expands it, inside single quotes, "
        f"behind a backslash, in a comment, or in a command the container execs without a "
        f"shell, so the program receives the literal text "
        f"${CHECKPOINT_DIRECTORY_VARIABLE} and creates a directory by that name."
        if _names_the_directory(command)
        else ""
    )
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
