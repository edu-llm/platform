"""The contract every phase's acceptance criteria are recorded against.

A phase records its criteria as :class:`CriterionSpec` values in its own
``phase*_criteria.py`` module. This module holds only the shape of that record and the
rules it must satisfy; it names no phase and states no criterion, so a new phase adds a
definition rather than a second copy of the machinery.

Three statuses exist and no more:

``COVERED``
    One or more cited tests prove the criterion as stated against the shipped
    configuration. The gate passes the criterion when every cited test runs and passes.

``DEFERRED``
    An explicit recorded decision not to satisfy the criterion yet. Both a written
    reason and a written trigger condition are required; a deferral without either is
    rejected when the spec is constructed. The gate passes the criterion.

``GAP``
    Anything else. The gate fails the criterion.

There is deliberately no fourth status. A partly satisfied criterion is either a
recorded decision with a trigger, in which case it is ``DEFERRED``, or it is unfinished
work, in which case it is a ``GAP``. A status that sits between the two is what lets a
gate be green and wrong at the same time, so no such status exists here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

__all__ = [
    "REENTRANT_TEST_MODULES",
    "CriteriaDefinitionError",
    "CriterionSpec",
    "CriterionStatus",
    "cited_node_ids",
    "validate_criterion_specs",
]

#: Test modules that themselves invoke a gate or a proof generator. A criterion may
#: never cite one of these, because executing the citation would re-enter the runner
#: that selected it. This is enforced by :class:`CriterionSpec`, and backed at runtime by
#: the nested-execution guard in ``edullm_platform.criteria_runner``. An entry is allowed
#: to name a module that does not exist yet: listing one early costs nothing, because the
#: only effect is to refuse citations nobody wanted, and it means the guard is already in
#: place on the day the module lands rather than one review later.
REENTRANT_TEST_MODULES: Final = (
    "tests/test_phase0_criteria.py",
    "tests/test_phase0_proof.py",
    "tests/test_phase1_criteria.py",
    "tests/test_phase1_proof.py",
)


class CriteriaDefinitionError(ValueError):
    """The criteria definition is not internally consistent."""


class CriterionStatus(StrEnum):
    COVERED = "covered"
    DEFERRED = "deferred"
    GAP = "gap"


def _is_written(text: str | None) -> bool:
    return text is not None and bool(text.strip())


@dataclass(frozen=True)
class CriterionSpec:
    """One recorded phase criterion and the node ids cited for it.

    ``proving_node_ids`` are tests that prove the criterion as stated against the
    shipped configuration. Citing one is what ``COVERED`` means, so only a ``COVERED``
    entry may carry them.

    ``supporting_node_ids`` are tests that are cited as evidence but do not amount to
    proof of the criterion as stated — either because they exercise the code path under
    a synthetic configuration that is not what ships, or because they prove only part of
    the claim. They are executed by the gate exactly like proving tests, so a supporting
    citation that is renamed or deleted still fails the criterion.
    """

    number: str
    statement: str
    status: CriterionStatus
    proving_node_ids: tuple[str, ...] = ()
    supporting_node_ids: tuple[str, ...] = ()
    scope_limits: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    deferral_reason: str | None = None
    deferral_trigger: str | None = None

    def __post_init__(self) -> None:
        self._require_statement()
        self._require_well_formed_node_ids()
        if self.status is CriterionStatus.COVERED:
            self._validate_covered()
        elif self.status is CriterionStatus.DEFERRED:
            self._validate_deferred()
        else:
            self._validate_gap()

    @property
    def cited_node_ids(self) -> tuple[str, ...]:
        return self.proving_node_ids + self.supporting_node_ids

    def _fail(self, problem: str) -> CriteriaDefinitionError:
        return CriteriaDefinitionError(f"criterion {self.number}: {problem}")

    def _require_statement(self) -> None:
        if not self.statement.strip():
            raise self._fail("has no statement")

    def _require_well_formed_node_ids(self) -> None:
        cited = self.cited_node_ids
        for node_id in cited:
            if not node_id.startswith("tests/") or "::" not in node_id:
                raise self._fail(f"cites {node_id!r}, which is not a tests/ pytest node id")
            module = node_id.split("::", 1)[0]
            if module in REENTRANT_TEST_MODULES:
                raise self._fail(
                    f"cites {node_id!r} from {module}, which invokes the gate or the proof "
                    "generator; executing that citation would re-enter the runner"
                )
        duplicates = sorted({node_id for node_id in cited if cited.count(node_id) > 1})
        if duplicates:
            raise self._fail(f"cites the same node id more than once: {duplicates!r}")

    def _reject_deferral_fields(self) -> None:
        if _is_written(self.deferral_reason) or _is_written(self.deferral_trigger):
            raise self._fail(
                f"is recorded as {self.status.value} but carries deferral text; only a "
                "deferred criterion may record a deferral reason or trigger"
            )

    def _validate_covered(self) -> None:
        if not self.proving_node_ids:
            raise self._fail("claims coverage without citing a proving test")
        if self.gaps:
            raise self._fail("is covered but records a gap; a criterion with an open gap is a GAP")
        self._reject_deferral_fields()

    def _validate_deferred(self) -> None:
        if self.proving_node_ids:
            raise self._fail(
                "is deferred but cites proving tests; a deferred criterion is not proved, "
                "so its citations belong in supporting_node_ids"
            )
        if not _is_written(self.deferral_reason):
            raise self._fail("is deferred without a written reason")
        if not _is_written(self.deferral_trigger):
            raise self._fail(
                "is deferred without a written trigger condition; a deferral that never "
                "becomes live again is a gap wearing a deferral's label"
            )
        if self.gaps:
            raise self._fail("is deferred but also records a gap; it must be one or the other")

    def _validate_gap(self) -> None:
        if self.proving_node_ids:
            raise self._fail("is a gap but cites proving tests; if a test proves it, it is covered")
        if not self.gaps:
            raise self._fail("is a gap with no written explanation")
        self._reject_deferral_fields()


def validate_criterion_specs(specs: Sequence[CriterionSpec]) -> None:
    """Check the cross-entry rules. Per-entry rules are enforced at construction."""
    numbers = [spec.number for spec in specs]
    duplicates = sorted({number for number in numbers if numbers.count(number) > 1})
    if duplicates:
        raise CriteriaDefinitionError(f"criterion numbers must be unique; repeated: {duplicates!r}")


def cited_node_ids(specs: Sequence[CriterionSpec]) -> frozenset[str]:
    return frozenset(node_id for spec in specs for node_id in spec.cited_node_ids)
