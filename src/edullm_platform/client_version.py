"""Which install typed a submission, and which refusals that answer explains.

**A DISPATCH CARRIED NO CLIENT, AND THAT COST A MORNING.** The submission defect of
2026-08-06 shipped in ``edullm submit``: it rejoined the shlex-split command with a plain
space, which drops the quotes that group ``bash -lc``'s program into one word, so the
compile job refused the submission for handing ``-lc`` more than one command. Merging the
fix repaired nobody, because the defect travels with each person's install rather than with
the platform. And the refusal's own advice was a trap -- it said to quote the program, which
the submitter had already done and their install had undone on the way to the form.

Nothing on the form said which install had typed it, so nothing downstream could tell that
reader from one who really had written an unquoted command. ``submit-run.yml`` takes an
``edullm_version`` input now, ``edullm submit`` fills it in, and this module reads it back.

**ABSENT IS NOT OLD, AND CONFLATING THE TWO IS THE ONE MISTAKE THAT MATTERS HERE.** Six
accounts have dispatched that workflow eighty-two times from the Actions tab, where there is
no install to name and never will be, and every install made before the field existed sends
nothing either. So a blank field means "this cannot be known", and the wording below names
both branches rather than picking the accusing one.

**NOTHING HERE REFUSES, AND NOTHING HERE MAY LEARN TO.** Every function answers with prose
or with ``None``, and the caller prints it beside a refusal that had already happened. A
version floor on a dispatch would refuse submissions that are correct, on the strength of a
probability, at the hour when whoever is submitting can least afford it. That is
``cli/release.py``'s argument about staleness and it applies with more force here: this
field's whole purpose is to make a refusal easier to act on, so a refusal *of its own* would
be the thing it exists to prevent.

**A DEFECT EARNS AN ENTRY BY HAVING CORRUPTED THE SUBMISSION, NOT BY BEING OLD.** The
distinction is what keeps this from becoming an upgrade nag bolted onto every refusal. An
install that failed to warn a submitter about something they really did type is not the
cause of the refusal they then met: the field they have to change is the same field either
way, and a sentence about their install would be a second thing to read that changes
nothing. What belongs here is the narrow case where the install altered what reached the
form, so that the refusal's plain advice is advice to do what they already did.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

__all__ = [
    "KNOWN_CLIENT_DEFECTS",
    "ClientDefect",
    "SubmittingClient",
    "defect_note",
    "read_client_version",
    "submitted_by_said",
]

#: What the field may carry before this stops believing it is a version at all. Long enough
#: for anything ``tools/next_version.py`` can produce and for a local suffix on top of it,
#: short enough that whatever a person pastes into the box cannot become the log.
MAXIMUM_LENGTH: Final = 40

#: The characters a version may be spelled with. Anything else -- a space, a quote, a
#: newline, a backtick -- and the field is treated as unreadable rather than sanitised into
#: something it did not say. This value reaches a workflow log and a refusal a person reads,
#: so the safe answer to text nobody recognises is to decline to repeat it.
_SPELLING: Final = re.compile(r"[0-9][0-9A-Za-z.+-]*")

#: A version this can order against another. ``pyproject.toml`` carries a plain three-part
#: number and ``tools/next_version.py`` is the only thing that moves it, so anything else in
#: this field was typed by a person and is recorded rather than compared.
_COMPARABLE: Final = re.compile(r"[0-9]+(?:\.[0-9]+)*")


@dataclass(frozen=True)
class SubmittingClient:
    """What one dispatch said about the install that typed it.

    Three states and not two, because the third one is real: the field was filled in with
    something this cannot read. Reporting that as absence would be the same conflation the
    module docstring is about, one level down.
    """

    #: The version as the dispatch spelled it, or ``None`` where the field was blank or
    #: carried something unreadable.
    said: str | None = None
    #: The same thing as integers, for ordering, or ``None`` where it is not a version.
    release: tuple[int, ...] | None = None
    #: How long the unreadable field was, for a log line that says so without repeating it.
    #: ``None`` whenever the field was blank or was read.
    unreadable: int | None = None

    @property
    def reported(self) -> bool:
        """Whether an install named itself. False for the Actions form and for garbage."""
        return self.said is not None

    def older_than(self, version: str) -> bool | None:
        """``None`` where there is nothing to compare, which is not the same as ``False``.

        A caller that treated the missing answer as "not older" would read every form
        dispatch and every pre-field install as current, which is the direction that
        withholds the one sentence this module exists to print.
        """
        if self.release is None:
            return None
        return self.release < _ordered(version)


def read_client_version(text: str | None) -> SubmittingClient:
    """The ``edullm_version`` input, read into something safe to print and to order."""
    candidate = (text or "").strip()
    if not candidate:
        return SubmittingClient()
    if len(candidate) > MAXIMUM_LENGTH or not _SPELLING.fullmatch(candidate):
        return SubmittingClient(unreadable=len(candidate))
    return SubmittingClient(
        said=candidate,
        release=_ordered(candidate) if _COMPARABLE.fullmatch(candidate) else None,
    )


def submitted_by_said(client: SubmittingClient) -> str:
    """One line for the compile job's log, whatever the dispatch carried.

    Printed on every submission rather than only on a refused one, because the question
    this field was added to answer -- how many people are on a current install -- is a
    question about the submissions that worked.
    """
    if client.said is not None:
        return f"Submitted by edullm {client.said}."
    if client.unreadable is not None:
        return (
            f"The dispatch carried an edullm_version of {client.unreadable} characters that "
            "is not a version, so this submission is being read as naming no install. "
            "Nothing about it is refused for that."
        )
    return (
        "The dispatch names no edullm version. That is what the Actions form sends, and "
        "what an install older than the field sends. Nothing about it is refused for that."
    )


@dataclass(frozen=True)
class ClientDefect:
    """One defect that altered a submission on its way to the form, and what ended it."""

    #: A phrase the refusal carries, and a copy of a string that lives somewhere else.
    #:
    #: A CONSTANT IN THE RAISING MODULE WOULD BE TIDIER AND IS NOT WORTH WHAT IT COSTS.
    #: ``contracts/validation.py`` is packaged verbatim into four released Lambda zips, and
    #: ``tests/test_released_zips.py`` compares each of them against what is deployed -- so
    #: a comment added there is four function releases, for a refusal none of those four
    #: prints. ``tests/test_client_version.py`` raises the real refusal and asserts this
    #: phrase is in it, which is the same seam-test arrangement ``ADMISSION_JOB`` gets
    #: against a string in a workflow file, and it fails on the reword rather than after it.
    marker: str
    #: The first release without it.
    fixed_in: str
    #: What the install did, as the middle of a sentence beginning "edullm before 3.4.8".
    did: str


#: Every defect a refusal may name a version for. One entry, and the bar for a second is in
#: the module docstring: it has to have changed what reached the form, so that the refusal a
#: submitter meets is advice to do what their install undid.
KNOWN_CLIENT_DEFECTS: Final = (
    ClientDefect(
        marker="reads exactly one word as the command",
        fixed_in="3.4.8",
        did=(
            "rejoined the command with a plain space on the way to the form, which takes "
            "exactly these quotes off"
        ),
    ),
)


def defect_note(refusal: str, *, client: SubmittingClient, install: str) -> str | None:
    """What to add to a refusal a known defect explains, or ``None`` for every other one.

    ``None`` is the answer for most refusals and that is the point of the marker match. An
    unregistered repository, a retired corpus, a bfloat16 command on a Turing card: none of
    those is anything an install did, and a sentence about the install beside them would
    train a reader to skip the sentence in the one case it is load-bearing.

    ``None`` is also the answer when the install that submitted is new enough not to have
    the defect. The refusal then means what it says -- the command really did arrive without
    its quotes because it was written that way -- and the plain advice above is correct.

    ``install`` is passed in rather than composed here. ``cli/release.py`` is the one place
    the install line is spelled and its docstring records what the second copy cost.
    """
    for defect in KNOWN_CLIENT_DEFECTS:
        if defect.marker not in refusal:
            continue
        older = client.older_than(defect.fixed_in)
        if older is False:
            return None
        if older is True:
            return (
                f"Reinstall edullm before changing that command. This submission says it "
                f"came from edullm {client.said}, and edullm before {defect.fixed_in} "
                f"{defect.did} -- so the line above may be telling you to do what you "
                f"already did:\n\n  {install}"
            )
        return (
            "If you ran edullm submit, reinstall it before changing that command. No "
            "edullm version came with this submission, which is what the Actions form "
            "sends and what an install older than the field sends, so this cannot tell "
            f"whether the quoting was lost or was never typed. edullm before "
            f"{defect.fixed_in} {defect.did}, and the line above would then be telling you "
            f"to do what you already did:\n\n  {install}"
        )
    return None


def _ordered(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))
