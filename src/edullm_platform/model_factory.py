"""Whether the image a run will land on has the model factory the command names.

``.edullm/train_on_corpus.py`` resolves ``--model-factory`` with
``getattr(TransformerConfig, name, None)`` and, on a miss, exits 70 with ``unknown model
factory``. There is no earlier signal: the name is a string until the container looks it up,
and by then the submission has been compiled, classified, released by a lead, admitted and
given an instance. ``guides/olmo-core.md`` tells a researcher to write
``--model-factory olmo2_1B`` and, until this module, nothing anywhere checked that any image
has such a factory. One does, which is luck rather than a check.

**THIS IS THE SAME DEFECT AS THE TOKENIZER ONE AND IT IS CLOSED THE SAME WAY.** A fact about
what an image contains cannot be answered from a file describing what this platform can
express, so it is answered from a recorded reading of the image:
``config/image-contents.yaml``, whose ``model_factories`` vocabulary
``tools/probe_image_contents.py`` writes and ``tests/test_image_contents.py`` holds to the
guide in both directions. :mod:`edullm_platform.contracts.image_contents` carries the argument
in full.

**AN UNREAD REPOSITORY IS NOT REFUSED, WHICH IS THE OPPOSITE CHOICE TO THE CORPUS VERDICT AND
IS DELIBERATE.** ``edullm data`` reads "no reading" as "will not run" because the cost of being
wrong there is a researcher told to pick a different corpus, and nothing is blocked. The cost
of being wrong *here* is a submission refused, so absence buys silence: only a repository whose
factories somebody has actually read is checked, and the other five registered repositories
pass through this untouched. Refusing on a file nobody has written yet would be this platform
asserting something about an image it has not read, which is the failure the record exists to
end, arriving from the other direction.

**THE READING CAN BE BEHIND THE SUBMISSION, WHICH IS WHY THERE IS A WAIVER AND WHY IT IS THIS
ONE.** The record names a commit, and a researcher on a branch that adds a factory is naming a
commit the reading does not cover -- their run works and this would refuse it. That is exactly
the case :data:`~edullm_platform.launchers.LAUNCH_CHECK_WAIVER` and its checkpoint sibling
exist for: the waived run still works and the platform is only declining to assert something
about it. :mod:`edullm_platform.precision` has no waiver and says why -- a bfloat16 run on
Turing does not work however sure the submitter is -- and the difference between those two
cases is the whole of why this one has an escape and that one must not. It travels in the
command for :mod:`~edullm_platform.launchers`'s reasons: inside the hashed manifest, inside the
lineage record, per submission rather than a checkbox somebody ticks once and copies forward.

**WHAT THIS READS IS THE TEXT OF A COMMAND, WHICH BOUNDS THE CLAIM.**
:func:`model_factory_request_in` finds the name where it is written into argv and cannot find
one that was not. A command that runs the trainer without ``--model-factory`` gets that
program's own default, which this does not check and does not refuse -- reading a default out
of somebody else's argparse would be a second copy of a fact that moves. The refusal says so,
for the reason ``precision.py`` says the equivalent: a guard that lets a submitter believe it
covers more than it does is worse than one nobody relies on.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from .contracts.image_contents import (
    ImageContentsReading,
    ImageContentsRecord,
    VocabularyName,
)
from .errors import ModelFactoryNotInTheImageError
from .launchers import carries_the_token, simple_commands

__all__ = [
    "MODEL_FACTORY_CHECK_WAIVER",
    "MODEL_FACTORY_FLAG",
    "model_factory_request_in",
    "require_a_model_factory_the_image_has",
    "waived_model_factory_note",
]

#: What a submitter writes to say they know their commit adds a factory the reading predates.
#:
#: An environment assignment rather than a bare word, and spelled out in full, for
#: :data:`~edullm_platform.launchers.LAUNCH_CHECK_WAIVER`'s reasons: the two places a token is
#: inert are an assignment and a comment, and a waiver reachable by nearly typing something else
#: is not a decision.
MODEL_FACTORY_CHECK_WAIVER: Final = "EDULLM_MODEL_FACTORY_CHECK=waived"

#: The flag the trainer declares, normalised the way :func:`_setting_name` normalises. One
#: spelling rather than a family of them, because unlike a dtype -- which every trainer spells
#: differently, as ``precision.py`` records -- this is one argparse option in one program, and a
#: substring rule would read ``--model-factory-preset`` as the thing itself.
MODEL_FACTORY_FLAG: Final = "model_factory"

#: Shell operators, which arrive stuck to the end of a word rather than as one. The same
#: characters ``precision.py`` trims and for the same reason: ``shlex`` splits on whitespace and
#: quoting rather than on control operators.
_TRAILING_OPERATORS: Final = ";&|"


def model_factory_request_in(command: Sequence[str]) -> str | None:
    """The factory name this command asks for, or ``None`` if it names none.

    Read over the words of every simple command, wrappers opened, comments dropped, which is
    what :func:`~edullm_platform.launchers.simple_commands` returns. The last spelling wins
    where a command writes two, because that is what argparse does with a repeated option and
    the point of this is to predict what the container will resolve.
    """
    found: str | None = None
    for segment in simple_commands(tuple(command)):
        for position, word in enumerate(segment):
            read = word.rstrip(_TRAILING_OPERATORS)
            key, separator, value = read.partition("=")
            if separator and _setting_name(key) == MODEL_FACTORY_FLAG:
                # `--model-factory=x` only. A bare `MODEL_FACTORY=x` is a shell assignment the
                # trainer never reads, and reading it as the request would refuse a command that
                # sets an unrelated variable of a similar name.
                if key.startswith("-") and value:
                    found = value
                continue
            if not read.startswith("-") or _setting_name(read) != MODEL_FACTORY_FLAG:
                continue
            following = segment[position + 1] if position + 1 < len(segment) else None
            if following is not None:
                stripped = following.rstrip(_TRAILING_OPERATORS)
                if stripped and not stripped.startswith("-"):
                    found = stripped
    return found


def _setting_name(word: str) -> str:
    """``--model-factory`` and ``--model_factory`` are one thing.

    Narrower than ``precision._setting_name``, which also collapses a dotted config path: this
    flag is argparse's and never arrives as a dotted override, and collapsing on dots here would
    read ``--foo.model_factory`` as the trainer's own option.
    """
    return word.lstrip("-").replace("-", "_").casefold()


def require_a_model_factory_the_image_has(
    *,
    command: Sequence[str],
    repository: str,
    images: ImageContentsRecord,
) -> None:
    """Refuse a command naming a factory the reading of this repository's image does not hold.

    Silent in three cases, and each is a case where refusing would assert something nobody has
    measured: a command naming no factory, a repository with no reading at all, and a repository
    whose reading never established this vocabulary. The waiver is the fourth.

    Raises :class:`~edullm_platform.errors.ModelFactoryNotInTheImageError`, a
    ``SubmissionRefusedError`` as every other command rule raises, so the caller needs no second
    branch.
    """
    requested = model_factory_request_in(command)
    if requested is None:
        return
    reading = images.reading_for(repository)
    if reading is None:
        return
    known = reading.names(VocabularyName.MODEL_FACTORIES)
    if known is None or requested in known:
        return
    if carries_the_token(command, MODEL_FACTORY_CHECK_WAIVER):
        return
    raise ModelFactoryNotInTheImageError(_refusal(requested=requested, reading=reading))


def waived_model_factory_note(
    *,
    command: Sequence[str],
    repository: str,
    images: ImageContentsRecord,
) -> str | None:
    """The sentence an approver is owed when the waiver is what let this command through.

    Returned only when the waiver did something, which is
    :func:`~edullm_platform.launchers.waived_launch_check_note`'s rule and for its reason: a line
    on the approver page for every run that merely carries the token is a line readers learn to
    skip.
    """
    if not carries_the_token(command, MODEL_FACTORY_CHECK_WAIVER):
        return None
    requested = model_factory_request_in(command)
    if requested is None:
        return None
    reading = images.reading_for(repository)
    if reading is None:
        return None
    known = reading.names(VocabularyName.MODEL_FACTORIES)
    if known is None or requested in known:
        return None
    return (
        f"This run waives the model factory check with {MODEL_FACTORY_CHECK_WAIVER}. It asks for "
        f"--model-factory {requested}, which the reading of {reading.repository} at "
        f"{reading.commit_sha[:12]} does not hold, and the submitter is asserting that the commit "
        "this run is built from adds it. If they are wrong the container exits 70 in the first "
        "seconds with the machine already allocated."
    )


#: What this guard did and did not look at, said on the refusal rather than left in a guide.
#: ``precision.py`` makes this argument at length: a submitter who has met a refusal once will
#: reasonably believe the platform knows which runs name a bad factory, and it does not -- it
#: knows which *commands say so*, against a reading that has a date on it.
_WHAT_WAS_CHECKED: Final = (
    "This read the words of your command against a recorded reading of the image, and neither "
    "half is the running container. A factory selected inside the program, or left to the "
    "trainer's own default because the command names none, is invisible here and is not refused."
)


def _refusal(*, requested: str, reading: ImageContentsReading) -> str:
    known = reading.names(VocabularyName.MODEL_FACTORIES) or ()
    near = sorted(name for name in known if _looks_like(name, requested))
    suggestion = (
        f" The closest names it does have are {', '.join(near[:5])}."
        if near
        else f" It holds {len(known)} others."
    )
    return (
        f"name a model factory the image has, or waive this with {MODEL_FACTORY_CHECK_WAIVER} in "
        f"the command if the commit you are submitting adds one. {reading.repository} was read at "
        f"commit {reading.commit_sha[:12]} from {_read_from(reading)}, and "
        f"{requested!r} is not among the {len(known)} factories it holds.{suggestion} The "
        "container resolves this name with getattr(TransformerConfig, ...) and exits 70 on a "
        "miss, which happens after the run has been priced, released by a lead, admitted and "
        f"given a machine. {_WHAT_WAS_CHECKED}"
    )


def _read_from(reading: ImageContentsReading) -> str:
    for entry in reading.vocabularies:
        if entry.kind is VocabularyName.MODEL_FACTORIES:
            return entry.read_from
    return "the recorded reading"


def _looks_like(name: str, requested: str) -> bool:
    """Whether a recorded name is close enough to the request to be worth printing.

    A prefix comparison on the family rather than an edit distance, because the mistakes this
    has to help with are a size that does not exist and a generation that does not --
    ``olmo2_2B`` and ``olmo4_1B`` -- and for both of those the useful answer is the rest of the
    family. An edit distance would rank ``olmo3_1B`` and ``olmo2_1M`` equally against
    ``olmo2_1B`` and teach nobody anything.
    """
    head = requested.split("_", maxsplit=1)[0].casefold()
    return bool(head) and name.casefold().startswith(head[: max(4, len(head) - 1)])
