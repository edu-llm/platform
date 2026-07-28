"""Questions this repository has surfaced and deliberately has not answered.

A phase criterion records something that must be true and whether it is. This records
something nobody has decided, which is a different thing and has nowhere else to live. A
gap says "unfinished work"; a deferral says "postponed, and here is what brings it back".
Neither fits a question whose answer is a policy choice rather than an implementation, and
a question like that has exactly two fates if it is not written down: it is settled by
accident by whoever first trips over it, or it is settled silently by whoever happens to
be implementing near it.

**No entry may carry an answer.** :class:`OpenDecision` has fields for the question, for
what is already known, and for the options nobody has chosen between. It has no field for
a recommendation, and :func:`validate_open_decisions` requires at least two options, so an
entry cannot become a decision by having its alternatives quietly removed. Answering one
means deleting it from here and putting the answer where it is enforced — a criterion, a
policy file, a gate — not editing this module until it agrees with what was built.

**Every entry names where it lands.** A question with no phase attached is a question
nobody will read at the moment it matters. ``lands_in`` is the point at which somebody has
to have answered, and it is deliberately a sentence rather than a phase number, because
the trigger is a circumstance and phase numbers move.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

__all__ = [
    "OPEN_DECISION_COUNT",
    "OpenDecision",
    "OpenDecisionsDefinitionError",
    "open_decisions",
    "validate_open_decisions",
]

OPEN_DECISION_COUNT: Final = 0


class OpenDecisionsDefinitionError(ValueError):
    """The open-decisions definition is not internally consistent."""


@dataclass(frozen=True)
class OpenDecision:
    """One question that has been surfaced, is not answered, and must not be forgotten."""

    number: str
    question: str
    #: Why leaving it unanswered has a cost, stated so a reader can weigh it rather than
    #: take somebody's word that it matters.
    why_it_matters: tuple[str, ...]
    #: What is already established, with the evidence behind each fact. Separated from
    #: the options so that a reader can tell what has been observed from what is a view.
    what_is_known: tuple[str, ...]
    #: The choices, at least two, none of them marked as preferred.
    options: tuple[str, ...]
    #: The circumstance by which somebody has to have answered.
    lands_in: str
    #: What surfaced the question, so a reader can go and look at the same thing.
    raised_by: str

    def __post_init__(self) -> None:
        if not self.question.strip().endswith("?"):
            raise self._fail("is not written as a question")
        for name in ("why_it_matters", "what_is_known"):
            if not getattr(self, name):
                raise self._fail(f"records no {name.replace('_', ' ')}")
        if len(self.options) < 2:
            raise self._fail(
                "records fewer than two options; a question with one option is a decision "
                "that has been taken and belongs where it is enforced"
            )
        if not self.lands_in.strip():
            raise self._fail("does not say by when it has to be answered")
        if not self.raised_by.strip():
            raise self._fail("does not say what surfaced it")

    def _fail(self, problem: str) -> OpenDecisionsDefinitionError:
        return OpenDecisionsDefinitionError(f"open decision {self.number}: {problem}")


def validate_open_decisions(decisions: Sequence[OpenDecision]) -> None:
    numbers = [decision.number for decision in decisions]
    duplicates = sorted({number for number in numbers if numbers.count(number) > 1})
    if duplicates:
        raise OpenDecisionsDefinitionError(
            f"open decision numbers must be unique; repeated: {duplicates!r}"
        )


def open_decisions() -> tuple[OpenDecision, ...]:
    """Every question this repository has surfaced and has not answered.

    Decision 1, on whether a registry scan may block, was answered during Phase 3 and is
    gone from here rather than edited to agree with what was built. The answer lives in
    ``contracts/image_scan.py``, in ``config/policy.yaml``'s ``image_scan`` block and
    ``image_scan_findings_unreviewed`` condition, in ``config/image-exceptions.yaml``, and
    in ``tests/test_phase3_image_scan.py``. It went one way the register did not list as
    obvious: block unless an exception is recorded, enforced at admission rather than at
    publish, because ECR scans after the push and a publish-time refusal would leave that
    commit permanently unpublishable.

    Decision 2, on what the workload role may write to, was answered on 2026-07-28 when its
    own trigger fired: a second team was about to submit and Phase 4 was about to write its
    check that outputs land only under the authorized run prefix. It is gone from here for
    the same reason decision 1 is. The answer lives in ``contracts/results.py``'s
    ``output_prefix``, which is now the single author of the string; in
    ``infra/iam/batch-roles.yaml``, which enforces it; and in the Phase 4 isolation
    criterion, which is a **gap** rather than a pass because of how it was answered.

    It took the widest of the three options -- ``teams/*/runs/*`` -- and that is worth
    stating plainly rather than leaving to be discovered. A workload may write under any
    team's prefix and under any run id, including one belonging to somebody else, and
    nothing detects it. The register permitted exactly this and attached one condition:
    the widest scope may ship provided the record says so, and a narrower claim than is
    enforced may not. Both halves are honoured.

    Two things the register got slightly wrong, recorded because the next reader will
    otherwise repeat them. It called per-team scoping "reachable today"; Batch has no
    per-job role override at submit time, so a role per team also needs a job definition
    per team. And it treated per-run as merely narrower, when it needs a session tag to
    travel from admission into the Batch job -- a mechanism that does not exist. Both are
    Phase 5 work. The prefix layout is already the narrow one, so tightening is later an
    IAM change rather than a migration of keys already written.

    **This register is empty, and empty is a state it is allowed to be in.** It does not
    mean nothing is undecided; it means nothing is undecided *and unrecorded*. A question
    that arrives gets an entry, and the count below moves with it.
    """
    decisions: tuple[OpenDecision, ...] = ()
    if len(decisions) != OPEN_DECISION_COUNT:
        raise OpenDecisionsDefinitionError(
            f"the definition lists {len(decisions)} open decisions; {OPEN_DECISION_COUNT} "
            "are recorded"
        )
    validate_open_decisions(decisions)
    return decisions
