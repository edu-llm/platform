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
    """Every question this repository has surfaced and has not answered.

    Decision 1, on whether a registry scan may block, was answered during Phase 3 and is
    gone from here rather than edited to agree with what was built. The answer lives in
    ``contracts/image_scan.py``, in ``config/policy.yaml``'s ``image_scan`` block and
    ``image_scan_findings_unreviewed`` condition, in ``config/image-exceptions.yaml``, and
    in ``tests/test_phase3_image_scan.py``. It went one way the register did not list as
    obvious: block unless an exception is recorded, enforced at admission rather than at
    publish, because ECR scans after the push and a publish-time refusal would leave that
    commit permanently unpublishable.
    """
    decisions = (
        OpenDecision(
            number="2",
            question=(
                "Should the workload role's write access be scoped per run, per team, or "
                "per bucket?"
            ),
            why_it_matters=(
                (
                    "Phase 3 writes the first workload role, so whichever scope it uses "
                    "becomes the shape every later team inherits. Nothing forces the "
                    "choice at the moment it is made, which is exactly the condition under "
                    "which it gets made by whoever is typing."
                ),
                (
                    "Phase 4 asserts that S3 receives outputs only under the authorized run "
                    "prefix, and Phase 5 asserts that cross-team data access fails closed. "
                    "Both are claims about this scope. A role scoped per bucket satisfies "
                    "neither and would pass every test written before those phases."
                ),
                (
                    "The scopes are not equally reachable. A static role cannot name a run "
                    "id, so per-run needs either a session tag the submitting principal "
                    "sets or a role assumed per run; per-team needs a prefix convention and "
                    "a role per team or a tag. Deciding late means discovering the "
                    "mechanism late, after the prefix layout is already in lineage records "
                    "that cannot be rewritten."
                ),
            ),
            what_is_known=(
                (
                    "The lineage bucket is not a candidate. It is write-once by bucket "
                    "policy and only the admission state machine writes to it; a workload "
                    "role holding s3:PutObject there would undo the property that store "
                    "exists to have."
                ),
                (
                    "Batch supports neither tags on the job role session nor a per-job role "
                    "override at submit time in a way this platform currently uses, so "
                    "per-run scoping is not free: it needs the run id to reach the policy "
                    "somehow, and the two ways to do that are a session tag and a role per "
                    "run."
                ),
                (
                    "config/organization.yaml carries no team bindings yet, so per-team "
                    "scoping has nothing to enumerate today. TeamBinding already has fields "
                    "for an S3 namespace, which is where the answer would land."
                ),
            ),
            options=(
                (
                    "Per bucket. One outputs bucket, the workload role may write anywhere "
                    "in it. Simplest, and it makes the Phase 4 and Phase 5 isolation checks "
                    "unprovable rather than failing."
                ),
                (
                    "Per team, through a prefix and the S3 namespace TeamBinding already "
                    "declares. Reachable with a role per team, and it matches where Phase 5 "
                    "is going, but it isolates teams rather than runs."
                ),
                (
                    "Per run, through a session tag the submitting principal sets and an "
                    "aws:PrincipalTag condition on the prefix. The narrowest, and the only "
                    "one that makes 'outputs only under the authorized run prefix' literally "
                    "true; it needs the tag to travel from admission into the Batch job."
                ),
            ),
            lands_in=(
                "Before a second team submits, or before Phase 4 writes its check that "
                "outputs land only under the authorized run prefix -- whichever comes "
                "first. Phase 3 may ship the widest scope provided the record says so; it "
                "may not ship a narrower claim than it enforces."
            ),
            raised_by=(
                "Phase 3 writing sbsandbox-intern-edullm-batch-workload and finding that "
                "nothing in the repository said what it should be allowed to write to."
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
