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

OPEN_DECISION_COUNT: Final = 1


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
    """Every question this repository has surfaced and has not answered."""
    decisions = (
        OpenDecision(
            number="1",
            question=(
                "Should the result of the registry image scan be able to block a publish, "
                "and if so on what?"
            ),
            why_it_matters=(
                (
                    "Nothing runs a Phase 1 image, so the findings are inert today and the "
                    "question has no urgency. It acquires all of its urgency at once, on the "
                    "day the first workload runs one, and that is the worst moment to be "
                    "deciding it: whoever is standing there will settle it by whichever way "
                    "makes their job work."
                ),
                (
                    "It is a policy question rather than an implementation one. Blocking on "
                    "criticals would have refused the image this phase published, whose four "
                    "critical findings are all inherited from a base image the platform "
                    "chose and pins. Not blocking at all means a scan runs on every push, "
                    "costs nothing to ignore, and is decoration."
                ),
                (
                    "Whatever the answer, the enforcement point is the publish workflow, "
                    "which is Phase 1's file. A rule added there later is a change to the "
                    "path this phase's criteria are written about."
                ),
            ),
            what_is_known=(
                (
                    "The repository is created with ScanOnPush, so a scan exists as soon as "
                    "an image does. The scan of the published image is committed under "
                    "fixtures/evidence/phase-1/run/image-scan.sanitized.json: status "
                    "COMPLETE, four critical and eight high findings."
                ),
                (
                    "Those findings are the base image's. The Dockerfile installs nothing — "
                    "it sets three environment variables, creates a working directory and "
                    "copies the source — so every package a scanner can see came from the "
                    "registered base, which is pinned by digest in config/repositories.yaml."
                ),
                (
                    "A gate would need no new permission. The publisher role already holds "
                    "ecr:DescribeImageScanFindings, because reading the scan back was "
                    "anticipated even though nothing reads it yet."
                ),
                (
                    "Whatever the rule, it cannot be enforced at push time in the obvious "
                    "way: ECR scans an image after it is pushed, so a scan result can refuse "
                    "the next step but cannot prevent the image existing. A tag that has been "
                    "written cannot be withdrawn, only left unused."
                ),
            ),
            options=(
                (
                    "Record and never block. The scan is evidence, a run manifest names a "
                    "digest, and whether a digest with findings may run is decided by "
                    "whoever authorizes the run rather than by the build."
                ),
                (
                    "Block on a severity threshold. Simple to state and simple to check, and "
                    "it would have refused this phase's first image on findings nobody in "
                    "this project introduced or can fix without changing the base."
                ),
                (
                    "Block only on findings the build introduced — those present in the image "
                    "and absent from the registered base, which is scannable on its own. "
                    "Narrow and meaningful, and it needs a second scan and a comparison that "
                    "does not exist."
                ),
                (
                    "Block unless an exception is recorded against the digest, in the way "
                    "Phase 0 already records exceptions for fan-out over the routine ceiling."
                ),
            ),
            lands_in=(
                "Before the first workload runs a published image. The phase that introduces "
                "the workload role is where that happens, and this must be answered before "
                "its acceptance criteria are written rather than after."
            ),
            raised_by=(
                "Phase 1's first live publish, whose scan returned four critical and eight "
                "high findings and blocked nothing, because nothing was ever wired to it."
            ),
        ),
    )
    if len(decisions) != OPEN_DECISION_COUNT:
        raise OpenDecisionsDefinitionError(
            f"the definition lists {len(decisions)} open decisions; {OPEN_DECISION_COUNT} "
            "are recorded"
        )
    validate_open_decisions(decisions)
    return decisions
