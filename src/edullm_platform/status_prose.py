"""What prose may claim about a phase's criteria, and how a gate states it.

Two kinds of document in this repository talk about criteria in sentences: a proof bundle,
which exists so a reviewer can decide whether a phase is done without reading the suite,
and a gate report, which carries a note beside the verdict it computed. Both are only
worth reading if a sentence in them cannot say something the computation does not, so the
reader that decides what a sentence claims lives here rather than in either of them. A
copy in each would be a copy that could be relaxed on its own.

Two kinds of claim are read.

A **numbered claim** ascribes a status to one check: ``Check 9 is deferred``. This is the
older of the two and the reason the bundle guard exists.

An **aggregate claim** ascribes a status to a count of them — ``Four criteria are gaps`` —
or states how many criteria a phase has — ``the thirteen Phase 0 acceptance criteria``.
This is the one the Phase 1 gate note got wrong: it was true when written, the build path
then ran and closed the four, and the note went on being emitted in the same JSON document
as the ``passed: true`` it contradicted. A count nobody counts is a count that goes stale
silently, so :func:`status_summary_sentence` derives the sentence from the computed
criteria and :func:`contradicting_status_claims` reads it back.

Both readers are blunt by design and in the same direction. They do not understand
negation, so ``Check 9 is not covered`` reads as a claim of ``covered`` and ``no criteria
are gaps`` is read as the claim that zero are. A claim a machine cannot check does not
belong in a document whose whole purpose is being trustworthy without verification.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Final, Protocol

from edullm_platform.criteria import CriterionStatus

__all__ = [
    "NUMBER_WORDS",
    "PHASE_CRITERIA_NOTE_PREAMBLE",
    "NumberedStatusRecord",
    "StatusRecord",
    "checked_phase_criteria_note",
    "contradicting_status_claims",
    "phase_criteria_note",
    "spell",
    "status_claims",
    "status_count_claims",
    "status_summary_sentence",
]


class StatusRecord(Protocol):
    """Anything carrying a computed or recorded criterion status."""

    @property
    def status(self) -> CriterionStatus: ...


class NumberedStatusRecord(StatusRecord, Protocol):
    """A status record a sentence can name by number."""

    @property
    def number(self) -> str: ...


#: Counts are spelled in prose, so the reader has to know the words. The range stops where
#: a phase's criteria plausibly stop; past it :func:`spell` uses digits, which the reader
#: also accepts.
NUMBER_WORDS: Final = {
    "no": 0,
    "none": 0,
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}

#: What :func:`spell` writes for each count. ``no`` rather than ``zero``, because "no
#: criteria are gaps" is the sentence a reader wants; the reader accepts either.
_SPELLED: Final = {
    count: word for word, count in NUMBER_WORDS.items() if word not in {"none", "zero"}
}

_COUNT: Final = r"\d+|" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))

# Prose that names a check and gives it a status. A sentence is the unit, because a
# status word further away than that is talking about something else.
CHECK_REFERENCE: Final = re.compile(
    r"\b(?:check|criterion|criteria)\s+(?P<number>D?[0-9]+)\b", re.IGNORECASE
)

# Prose that counts criteria and gives that many of them a status: "Four criteria are
# gaps". The count has to sit immediately before the noun, so "five refusals proved the
# criterion" is not read as a claim about five criteria.
GROUP_REFERENCE: Final = re.compile(
    rf"\b(?:all\s+)?(?P<count>{_COUNT})\s+(?:of\s+(?:the\s+)?)?criteri(?:a|on)\b",
    re.IGNORECASE,
)

# Prose that states how many criteria a phase has. "acceptance criteria" is the idiom
# every such sentence uses, and requiring it is what keeps "one criterion" out. The
# lookbehind keeps the 1 of "Phase 1 acceptance criteria" from being read as the count.
TOTAL_REFERENCE: Final = re.compile(
    rf"(?<![Pp]hase )\b(?P<count>{_COUNT})\s+(?:[A-Za-z0-9][A-Za-z0-9-]*\s+){{0,2}}"
    r"acceptance\s+criteri(?:a|on)\b",
    re.IGNORECASE,
)

STATUS_CLAIM: Final = re.compile(r"\b(?P<word>covered|deferred|gaps?)\b", re.IGNORECASE)
CLAUSE_BREAK: Final = re.compile(r"[.;|\n]")
STATUS_BY_WORD: Final = {
    "covered": CriterionStatus.COVERED,
    "deferred": CriterionStatus.DEFERRED,
    "gap": CriterionStatus.GAP,
    "gaps": CriterionStatus.GAP,
}

#: The part of a gate's note that is a statement about the three-status model rather than
#: a fact about the run in front of it. Nothing here counts anything, so nothing here can
#: go stale; everything that counts is derived by :func:`status_summary_sentence`.
PHASE_CRITERIA_NOTE_PREAMBLE: Final = (
    "Every pytest node id cited for a criterion was executed by this run. A criterion whose "
    "cited tests do not all exist and pass is a gap and fails the gate, whatever status the "
    "definition records. Only three statuses exist: covered passes, deferred passes and "
    "requires a written reason and a written trigger, gap fails."
)


def spell(count: int) -> str:
    return _SPELLED.get(count, str(count))


def _clause_around(text: str, reference: re.Match[str]) -> tuple[str, str]:
    return (
        CLAUSE_BREAK.split(text[: reference.start()])[-1],
        CLAUSE_BREAK.split(text[reference.end() :], maxsplit=1)[0],
    )


def _count_of(reference: re.Match[str]) -> int:
    raw = reference.group("count").lower()
    return int(raw) if raw.isdigit() else NUMBER_WORDS[raw]


def status_claims(text: str) -> tuple[tuple[str, CriterionStatus], ...]:
    """Every ``(check number, status)`` pair this prose asserts.

    A status word after the reference wins over one before it, because that is how a
    status is ascribed: ``Check 9 is deferred``. Only the first word in the clause counts,
    so ``deferred rather than covered`` claims one status and not two.
    """
    claims: list[tuple[str, CriterionStatus]] = []
    for reference in CHECK_REFERENCE.finditer(text):
        before, after = _clause_around(text, reference)
        claimed = STATUS_CLAIM.search(after) or STATUS_CLAIM.search(before)
        if claimed is None:
            continue
        claims.append((reference.group("number"), STATUS_BY_WORD[claimed.group().lower()]))
    return tuple(claims)


def status_count_claims(text: str) -> tuple[tuple[int, CriterionStatus | None], ...]:
    """Every ``(count, status)`` pair this prose asserts about criteria in the aggregate.

    A ``None`` status is a claim about how many criteria the phase has at all. A count
    with no status word in the clause after it — ``two criteria rest on the run capture``
    — is not a claim about a status and is not returned.
    """
    claims: list[tuple[int, CriterionStatus | None]] = []
    for reference in TOTAL_REFERENCE.finditer(text):
        claims.append((_count_of(reference), None))
    for reference in GROUP_REFERENCE.finditer(text):
        _before, after = _clause_around(text, reference)
        claimed = STATUS_CLAIM.search(after)
        if claimed is None:
            continue
        claims.append((_count_of(reference), STATUS_BY_WORD[claimed.group().lower()]))
    return tuple(claims)


def _numbered_problems(
    filename: str,
    text: str,
    checks: Sequence[NumberedStatusRecord],
) -> list[str]:
    recorded = {check.number: check.status for check in checks}
    problems: list[str] = []
    for number, claimed in status_claims(text):
        actual = recorded.get(number)
        if actual is None:
            problems.append(
                f"{filename} calls check {number} {claimed.value}, and no criterion of "
                "this phase carries that number"
            )
        elif actual is not claimed:
            problems.append(
                f"{filename} calls check {number} {claimed.value}; the criteria "
                f"definition records it {actual.value}"
            )
    return problems


def _aggregate_problems(
    filename: str,
    text: str,
    checks: Sequence[NumberedStatusRecord],
) -> list[str]:
    # Only the numbered acceptance criteria are counted. A bundle also hands the guard the
    # phase's related recorded deferrals, which carry D-prefixed numbers and are not
    # acceptance criteria; counting them would make every correct total read as wrong.
    criteria = [check for check in checks if check.number.isdigit()]
    by_status = Counter(check.status for check in criteria)
    problems: list[str] = []
    for count, claimed in status_count_claims(text):
        if claimed is None:
            if count != len(criteria):
                problems.append(
                    f"{filename} says this phase has {spell(count)} acceptance criteria; "
                    f"the criteria definition records {spell(len(criteria))}"
                )
            continue
        actual = by_status[claimed]
        if count != actual:
            problems.append(
                f"{filename} says {_clause(count, claimed)}; the computed criteria say "
                f"{_clause(actual, claimed)}"
            )
    return problems


def contradicting_status_claims(
    documents: Mapping[str, str],
    checks: Sequence[NumberedStatusRecord],
) -> tuple[str, ...]:
    """Prose that gives a check, or a count of them, a status the computation does not.

    A bundle exists so a reviewer can trust it without reading the suite and a gate note
    is emitted beside the verdict it describes, so a sentence that disagrees with the
    computation is the one defect neither can survive. Tables and derived sentences are
    rendered from the computed status and cannot disagree; a hand-written sentence can,
    and twice did.
    """
    problems: list[str] = []
    for filename, text in sorted(documents.items()):
        problems.extend(_numbered_problems(filename, text, checks))
        problems.extend(_aggregate_problems(filename, text, checks))
    return tuple(problems)


def _clause(count: int, status: CriterionStatus) -> str:
    if status is CriterionStatus.GAP:
        subject = "criterion is a gap" if count == 1 else "criteria are gaps"
    else:
        subject = f"criterion is {status.value}" if count == 1 else f"criteria are {status.value}"
    return f"{spell(count)} {subject}"


def status_summary_sentence(records: Sequence[StatusRecord]) -> str:
    """How many criteria this run computed, and how many of each status.

    Every status is named even when nothing holds it, because a status left out of the
    sentence reads as unknown, and because a stated zero is a claim the guard can check.
    """
    by_status = Counter(record.status for record in records)
    total = len(records)
    noun = "acceptance criterion" if total == 1 else "acceptance criteria"
    clauses = ", ".join(
        _clause(by_status[status], status)
        for status in (CriterionStatus.COVERED, CriterionStatus.DEFERRED)
    )
    return (
        f"This run evaluated {spell(total)} {noun}: {clauses}, and "
        f"{_clause(by_status[CriterionStatus.GAP], CriterionStatus.GAP)}."
    )


def phase_criteria_note(records: Sequence[StatusRecord], *, phase: str) -> str:
    """The note a gate emits beside its verdict: the durable rule, then the facts."""
    return (
        f"phase_criteria are the {phase} acceptance criteria. "
        f"{PHASE_CRITERIA_NOTE_PREAMBLE} {status_summary_sentence(records)}"
    )


def checked_phase_criteria_note(records: Sequence[NumberedStatusRecord], *, phase: str) -> str:
    """The note, read back by the same guard that refuses a contradicting proof bundle.

    Deriving the facts is what stops the note going stale; reading it back is what stops
    a durable-looking sentence being added to :data:`PHASE_CRITERIA_NOTE_PREAMBLE` later
    and asserting a count nobody computed. A gate that cannot state its verdict without
    contradicting itself does not get to print one.
    """
    note = phase_criteria_note(records, phase=phase)
    problems = contradicting_status_claims({"phase_criteria_note": note}, records)
    if problems:
        raise ValueError(
            "the gate's own note states something this run did not compute:\n  "
            + "\n  ".join(problems)
        )
    return note
