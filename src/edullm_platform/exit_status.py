"""Whether the status Batch is told is the status of the program that did the work.

Everything this platform records about how a run ended comes from one number. Batch reports
the container's exit status, ``lifecycle_projection`` turns a zero into ``SUCCEEDED``, and
that word is written into the attempt record and the result record -- which are write-once,
so a wrong one is wrong for ever. Every reader downstream is built on it:
``find_runs_that_saved_nothing`` decides *whether to ask* about a prefix from it,
``run_history`` measures the median in ``what it has taken`` over the runs it calls
successful and counts the rest as failures, and ``report_run_costs`` prices from it.

**THE CONTAINER'S EXIT STATUS IS THE SHELL'S, AND THE SHELL'S IS ITS LAST COMMAND'S.**
``ContainerOverrides.Command`` is exec form, so every real submission arrives as
``bash -lc '...'`` -- the guide says so in as many words, because a command that does not
run under a shell gets ``$EDULLM_CHECKPOINT_DIR`` as twenty-two literal characters. A shell
handed a command string exits with the status of the last thing it ran, and nothing between
the form and Batch requires that to be the program the submission is about.

Measured, rather than reasoned about, because the difference between the three lines below
is the whole of this module::

    bash -lc 'python train.py 2>&1 | tail -n 200'                      -> 0
    bash -lc 'set -o pipefail; python train.py 2>&1 | tail -n 200'     -> 28
    bash -lc 'python train.py; echo done'                              -> 0

A trainer that died on ``OSError: [Errno 28] No space left on device`` staging a corpus
therefore reaches the lineage store as ``exit_code: 0, outcome: succeeded``. Nothing further
down disagrees, because nothing further down has a second opinion available: the exit status
is the only thing the container said.

**pipefail IS THE REPORTED SHAPE AND IT IS ONE OF THREE, WHICH IS WHY THIS IS NOT A ONE-LINE
FIX SOMEWHERE ELSE.** ``set -o pipefail`` repairs the first line above and does nothing at
all for the third -- 28 against 0 on the pipe, 0 against 0 on the sequence. ``|| true`` is
the same family arriving by a third road. So the rule here is about which command's status
survives rather than about any one operator, and the three refusals are one rule read at
three places.

**WHY THIS IS A REFUSAL AND NOT A WRAPPER.** The obvious repair is for
:mod:`edullm_platform.execution` to run every submitted command under a shell that has
``pipefail`` set. It is the wrong repair twice over. It cannot reach the second and third
shapes, because no shell option makes a ``;`` hand back an earlier command's status. And the
command reaching the container unaltered is a property this platform is built on:
``fanout_cell_command`` argues it at length, the manifest is hashed, and the lineage record
seals the command an approver read -- so a container running something other than that text
would make the record describe bytes that did not run. A submission is refused instead,
before the approval gate, which is where every other rule about the text of a command is
answered and for the reason recorded in :mod:`edullm_platform.launchers`: a refusal after a
lead has read and released a submission has spent a person's attention on a decision that
could not have gone the other way.

**WHAT THIS DOES NOT ESTABLISH.** A command whose last simple command is the program still
tells the truth only if the program exits non-zero when it fails, and nothing here can see
inside it -- a trainer that catches its own exception and returns is invisible to this and
to everything else on the platform. :func:`~edullm_platform.checkpoint_commands
.unverified_resume_note` is in the same position about resume, and says so for the same
reason: the honest instrument for what the platform cannot see is a sentence rather than a
refusal that pretends to cover it. What this closes is the case where the *shell* discards a
status the program did report, which is the one this side can see completely.

**THE ESCAPE IS THE SAME MECHANISM AND THE SAME SPELLING AS THE OTHER TWO.**
:data:`EXIT_STATUS_CHECK_WAIVER` travels in the command, so it is inside the hashed manifest
and the immutable lineage record, and :func:`waived_exit_status_note` puts it in front of the
lead releasing the run because the command is not on the approver page. Two guards with two
conventions would be two things to remember and one of them would be remembered wrongly.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Final

from .errors import ExitStatusIsNotTheProgramsError
from .launchers import (
    MAXIMUM_WRAPPER_DEPTH,
    SHELLS_THAT_READ_A_COMMAND_STRING,
    carries_the_token,
    shell_command_string,
)

__all__ = [
    "EXIT_STATUS_CHECK_WAIVER",
    "SwallowedStatus",
    "require_the_program_to_report_its_own_failure",
    "status_the_shell_would_report",
    "waived_exit_status_note",
]

#: What a submitter writes to say that this command's exit status is deliberately not the
#: program's. Spelled to match ``EDULLM_LAUNCH_CHECK=waived`` and
#: ``EDULLM_CHECKPOINT_CHECK=waived`` exactly, because the three escapes should be one
#: convention rather than three.
EXIT_STATUS_CHECK_WAIVER: Final = "EDULLM_EXIT_STATUS_CHECK=waived"

#: ``set -o pipefail`` and every clustering of it that works: ``-eo``, ``-euo``, and the
#: flags written separately as ``set -e -o pipefail``. Matched over the text rather than over
#: words because that is where the option is written, and anchored on ``set`` so that a path
#: or a note carrying the word is not read as the option being set.
_PIPEFAIL = re.compile(r"\bset\s+(?:-[a-zA-Z]+\s+)*-[a-zA-Z]*o[a-zA-Z]*\s+pipefail\b")

#: The right-hand sides of ``||`` that swallow a status unconditionally. Deliberately short:
#: ``|| echo failed`` is the same defect, and is caught by the trailing-command rule instead,
#: because there the reader's remedy is different.
_UNCONDITIONAL_SUCCESS: Final = frozenset({"true", ":"})

#: ``python``, ``python3``, ``python3.12``. The same expression :mod:`edullm_platform
#: .launchers` uses, restated for the reason that module restates its shell list: this is not
#: worth an import cycle over three characters.
_PYTHON = re.compile(r"^python(?:[0-9]+(?:\.[0-9]+)*)?$")

#: The launchers whose status is the run's. Read from ``launchers`` rather than relisted
#: would be better and is not available: that module's set is private and splitting it out is
#: a change to a file the four open branches all touch. The names are the ones
#: ``read_launch_plan`` recognises, and ``tests/test_exit_status.py`` holds the two together.
_LAUNCHERS: Final = frozenset(
    {"torchrun", "deepspeed", "mpirun", "mpiexec", "srun", "accelerate", "olmo-eval"}
)

#: A leading ``NAME=value``, which the shell consumes rather than passing on.
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

#: Programs that run another program and are transparent to what this is asking.
_TRANSPARENT_PREFIXES: Final = frozenset({"env", "exec", "nohup", "time"})


class SwallowedStatus:
    """One way a shell command string reports something other than its program's status.

    Three fields rather than a string, because the caller renders two different sentences
    from them -- the refusal and the approver's note -- and a message assembled twice is a
    message that comes to disagree with itself.
    """

    __slots__ = ("kind", "remedy", "text")

    def __init__(self, *, kind: str, text: str, remedy: str) -> None:
        self.kind = kind
        self.text = text
        self.remedy = remedy

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"SwallowedStatus(kind={self.kind!r})"


def require_the_program_to_report_its_own_failure(command: Sequence[str]) -> None:
    """Refuse a command whose exit status would be somebody other than the program's.

    Read over every shell command string the submission carries, at every wrapper depth,
    because a submitter who writes ``bash -lc 'exec bash -c "python t.py | tail"'`` has the
    defect one level in and :mod:`edullm_platform.launchers` already opens wrappers to that
    depth for the neighbouring rules.

    A command that runs no shell is not checked and cannot have the defect: exec form starts
    one program and the container's status is that program's, which is the property this
    whole rule exists to restore.
    """
    if carries_the_token(command, EXIT_STATUS_CHECK_WAIVER):
        return
    swallowed = status_the_shell_would_report(command)
    if swallowed is None:
        return
    raise ExitStatusIsNotTheProgramsError(_refusal(swallowed))


def waived_exit_status_note(command: Sequence[str]) -> str | None:
    """The sentence an approver is owed when the waiver is what let this command through.

    Returned only when the waiver did something, for the reason
    :func:`~edullm_platform.launchers.waived_launch_check_note` gives: a line on the approver
    page for every run that happens to carry the token is a line readers learn to skip.
    """
    if not carries_the_token(command, EXIT_STATUS_CHECK_WAIVER):
        return None
    swallowed = _read(tuple(command), depth=0)
    if swallowed is None:
        return None
    return (
        "**This run waives the exit-status check.** "
        f"{swallowed.text} so a failure of the program would reach Batch as an exit code of "
        f"zero and be recorded as a success. `{EXIT_STATUS_CHECK_WAIVER}` in the command "
        "declares that is deliberate. Nothing else on this platform has a second opinion "
        "about how a run ended."
    )


def status_the_shell_would_report(command: Sequence[str]) -> SwallowedStatus | None:
    """How this command's status is taken from something other than its program, or ``None``.

    Public and waiver-blind, so that the refusal, the approver's note and any reader that
    wants to ask the question of a recorded command all get the same answer. The waiver is
    applied by the caller above rather than here, because a waived command still has the
    defect and the note has to describe it.
    """
    return _read(tuple(command), depth=0)


def _read(words: Sequence[str], *, depth: int) -> SwallowedStatus | None:
    text = shell_command_string(words)
    if text is None or depth >= MAXIMUM_WRAPPER_DEPTH:
        return None
    found = _swallowed_in(text)
    if found is not None:
        return found
    # One shell inside another is ordinary, and the defect may be in either. The outer text
    # is read first because that is the status the container reports; the inner one is read
    # second because a clean outer wrapper around a swallowing inner one is the same run.
    for segment in _top_level_segments(text):
        inner = _shell_wrapper_words(segment)
        if inner is None:
            continue
        deeper = _read(inner, depth=depth + 1)
        if deeper is not None:
            return deeper
    return None


def _swallowed_in(text: str) -> SwallowedStatus | None:
    """The three shapes, in the order a reader meets them.

    Pipefail first because it is the one a pipeline needs and the one whose remedy is a
    single clause. Then the unconditional ``||``, which no option repairs. Then the trailing
    command, which is last because establishing it needs to know which segment is the
    program and the other two do not.
    """
    segments = _top_level_segments(text)
    operators = _top_level_operators(text)

    if "|" in operators and not _PIPEFAIL.search(text):
        return SwallowedStatus(
            kind="pipeline",
            text=(
                "this command pipes the program into something else and does not set "
                "`pipefail`, so the status the container reports is the last stage's rather "
                "than the program's,"
            ),
            remedy=(
                "write `set -o pipefail; ` at the front of the command string. A pipeline "
                "reports its last stage's status unless it is set, and `tee` and `tail` "
                "succeed whatever they were handed"
            ),
        )

    for position, operator in enumerate(operators):
        if operator != "||":
            continue
        right = segments[position + 1] if position + 1 < len(segments) else ()
        if right and _program_of(right) in _UNCONDITIONAL_SUCCESS:
            return SwallowedStatus(
                kind="unconditional",
                text=(
                    f"this command ends in `|| {_program_of(right)}`, so the status the "
                    "container reports is zero whatever the program did,"
                ),
                remedy=(
                    "remove it. There is no failure of a training program this platform "
                    "should be told about as a success, and a run that is allowed to fail "
                    "is one whose workload profile should say so"
                ),
            )

    trailing = _trailing_command_after_the_program(segments, operators)
    if trailing is not None:
        return SwallowedStatus(
            kind="trailing",
            text=(
                f"this command runs `{trailing}` after the program, separated by `;`, so the "
                "status the container reports is that command's rather than the program's,"
            ),
            remedy=(
                f"join them with `&&` instead of `;`. `&&` runs `{trailing}` only when the "
                "program succeeded and hands back the program's status when it did not, "
                "which is almost always what a step after a run is for. `pipefail` does not "
                "help here and no shell option does"
            ),
        )
    return None


def _trailing_command_after_the_program(
    segments: Sequence[tuple[str, ...]], operators: Sequence[str]
) -> str | None:
    """The command left running after the program, when a ``;`` or ``&`` put one there.

    THE PROGRAM IS FOUND RATHER THAN ASSUMED TO BE FIRST, which is what keeps
    ``set -o pipefail; python train.py`` -- the remedy this module prints -- from being
    refused by the rule below it. That line has a ``;`` and the program is after it, which is
    the ordinary shape and not the defect.

    Only ``;`` and ``&`` are the defect. ``&&`` short-circuits, so a failing program ends the
    chain and its status is what comes back; ``||`` is handled above; ``|`` is the pipeline
    rule. Recognising the program means a Python interpreter or a launcher, which is what
    every submission this platform accepts runs -- a command running neither is not refused,
    because guessing which of two unfamiliar programs was the point would refuse correct
    submissions to catch a case nobody has submitted.
    """
    last_program = None
    for position, segment in enumerate(segments):
        name = _program_of(segment)
        if name is None:
            continue
        if _PYTHON.match(name) or name in _LAUNCHERS:
            last_program = position
    if last_program is None or last_program >= len(segments) - 1:
        return None
    # Every operator between the program and the end of the line. One `;` anywhere after it
    # is enough, because whatever follows the last one is what reports.
    following = operators[last_program:]
    if not any(operator in {";", "&"} for operator in following):
        return None
    final = segments[-1]
    return " ".join(final[:3]) if final else None


def _program_of(segment: Sequence[str]) -> str | None:
    """What this simple command runs, past the assignments and wrappers in front of it."""
    words = list(segment)
    while words and (
        _ASSIGNMENT.match(words[0]) or PurePosixPath(words[0]).name in _TRANSPARENT_PREFIXES
    ):
        words.pop(0)
    return PurePosixPath(words[0]).name if words else None


#: The operators this reads, longest first so that ``||`` is never read as two ``|``.
_OPERATOR_TEXT: Final = ("&&", "||", ";", "|", "&", "\n")


def _top_level_segments(text: str) -> list[tuple[str, ...]]:
    return [words for words, _ in _split(text)]


def _top_level_operators(text: str) -> list[str]:
    return [operator for _, operator in _split(text) if operator]


def _split(text: str) -> list[tuple[tuple[str, ...], str]]:
    """The text as simple commands and the operator after each, quoting and comments honoured.

    Hand-scanned rather than handed to :func:`shlex.split`, and that is the point of it. A
    ``|`` inside single quotes is a character in an argument and a ``|`` outside them is a
    pipeline, and ``shlex`` in its default mode removes exactly the quotes that decide which
    -- so a command whose ``--filter '{a|b}'`` is an argument would read as a pipeline and be
    refused. ``shlex(punctuation_chars=...)`` gets closer and still folds ``2>&1`` into an
    operator, which reads as a background ``&``.

    A subshell's contents are not descended into. ``( python t.py | tail )`` is a case this
    returns nothing for, which is a hole and a narrow one: it is written down here rather
    than guessed at, and the parenthesis is left in the segment so nothing reads it as a
    program.
    """
    found: list[tuple[tuple[str, ...], str]] = []
    words: list[str] = []
    current = ""
    quote: str | None = None
    index = 0
    while index < len(text):
        character = text[index]
        if quote is not None:
            current += character
            if character == quote and (quote == "'" or text[index - 1] != "\\"):
                quote = None
            index += 1
            continue
        if character in "'\"":
            quote = character
            current += character
            index += 1
            continue
        if character == "\\" and index + 1 < len(text):
            current += text[index : index + 2]
            index += 2
            continue
        if character == "#" and not current:
            # A comment runs to the end of the line, and everything in it is text.
            end = text.find("\n", index)
            if end == -1:
                break
            index = end
            continue
        # A redirection is not an operator here. `2>&1` carries an `&` that means a file
        # descriptor, and reading it as a background operator is how this refuses every
        # command the guide prints.
        if character == "&" and index and text[index - 1] == ">":
            current += character
            index += 1
            continue
        matched = next(
            (operator for operator in _OPERATOR_TEXT if text.startswith(operator, index)),
            None,
        )
        if matched is not None and not (matched == "&" and text.startswith("&>", index)):
            if current:
                words.append(current)
                current = ""
            found.append((tuple(words), matched))
            words = []
            index += len(matched)
            continue
        if character.isspace():
            if current:
                words.append(current)
                current = ""
            index += 1
            continue
        current += character
        index += 1
    if current:
        words.append(current)
    if words:
        found.append((tuple(words), ""))
    return found


def _shell_wrapper_words(segment: Sequence[str]) -> list[str] | None:
    """The argv of a nested shell in this segment, with its quotes taken off.

    ``exec bash -c '...'`` is the ordinary form and the transparent prefixes in front of it
    are stripped for the reason :func:`_program_of` strips them.
    """
    words = list(segment)
    while words and (
        _ASSIGNMENT.match(words[0]) or PurePosixPath(words[0]).name in _TRANSPARENT_PREFIXES
    ):
        words.pop(0)
    if not words or PurePosixPath(words[0]).name not in SHELLS_THAT_READ_A_COMMAND_STRING:
        return None
    return [_unquoted(word) for word in words]


def _unquoted(word: str) -> str:
    if len(word) >= 2 and word[0] == word[-1] and word[0] in "'\"":
        return word[1:-1]
    return word


def _refusal(swallowed: SwallowedStatus) -> str:
    return (
        f"make the program's failure the container's failure. {swallowed.text} "
        "so a run that dies reaches the lineage store as exit code zero and is recorded as "
        "a success -- which is write-once, feeds `what it has taken`, and is what the "
        "checkpoint reconciliation decides whether to ask about. "
        f"{swallowed.remedy}. If the status here is deliberately not the program's, write "
        f"{EXIT_STATUS_CHECK_WAIVER} into the command, which records that on the run rather "
        "than leaving a zero nobody can tell from a real one."
    )
