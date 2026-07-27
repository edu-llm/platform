"""The contract every phase's acceptance criteria are recorded against.

A phase records its criteria as :class:`CriterionSpec` values in its own
``phase*_criteria.py`` module. This module holds the shape of that record, the rules it
must satisfy, and the verdict a gate reaches for one criterion once its cited tests have
run. It names no phase and states no criterion, so a new phase adds a definition rather
than a second copy of the machinery.

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

:func:`criterion_result` is here rather than in a phase's gate for the same reason the
statuses are. It is where execution is allowed to overrule the recorded table, and a
phase that carried its own copy could quietly overrule it in the other direction.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final

from pydantic import BeforeValidator, Field

from edullm_platform.contracts.base import (
    ContractModel,
    parse_str_enum,
    require_ordered_sequence,
)
from edullm_platform.criteria_runner import SelectionOutcome, run_node_ids

__all__ = [
    "REENTRANT_TEST_MODULES",
    "CriteriaDefinitionError",
    "CriterionResult",
    "CriterionSpec",
    "CriterionStatus",
    "cited_node_ids",
    "criterion_result",
    "evaluate_criteria",
    "execute_criteria",
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
    "tests/test_ci_workflow.py",
    "tests/test_phase0_criteria.py",
    "tests/test_phase0_proof.py",
    "tests/test_phase1_criteria.py",
    "tests/test_phase1_proof.py",
    "tests/test_phase2_criteria.py",
    "tests/test_phase2_proof.py",
    "tests/test_verification_reuse.py",
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


CriterionStatusValue = Annotated[
    CriterionStatus, BeforeValidator(parse_str_enum(CriterionStatus))
]
NodeIdSequence = Annotated[tuple[str, ...], BeforeValidator(require_ordered_sequence)]


class CriterionResult(ContractModel):
    """One acceptance criterion of any phase, after its cited tests were executed."""

    number: str
    statement: str
    status: CriterionStatusValue
    passed: bool
    reason_code: str
    detail: str
    cited_node_ids: NodeIdSequence = Field(strict=False)
    missing_node_ids: NodeIdSequence = Field(strict=False)
    failed_node_ids: NodeIdSequence = Field(strict=False)


def _ordered(node_ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(node_ids))


def criterion_result(spec: CriterionSpec, outcome: SelectionOutcome) -> CriterionResult:
    """Decide one criterion from its recorded status and what its cited tests did.

    Execution beats the table in every direction that makes the gate stricter and in no
    direction that makes it looser. A criterion the definition calls covered is a gap if
    a cited test is missing or red; a criterion the definition calls a gap stays a gap
    however green its citations are.
    """
    cited = _ordered(spec.cited_node_ids)
    missing = _ordered(outcome.missing.intersection(cited))
    failed = _ordered(outcome.failed.intersection(cited))

    def result(status: CriterionStatus, reason_code: str, detail: str) -> CriterionResult:
        return CriterionResult(
            number=spec.number,
            statement=spec.statement,
            status=status,
            passed=status is not CriterionStatus.GAP,
            reason_code=reason_code,
            detail=detail,
            cited_node_ids=cited,
            missing_node_ids=missing,
            failed_node_ids=failed,
        )

    if outcome.execution_error is not None:
        return result(
            CriterionStatus.GAP,
            "criterion_execution_failed",
            (
                "The cited tests could not be executed, so this criterion is unproved: "
                f"{outcome.execution_error}"
            ),
        )
    if missing:
        return result(
            CriterionStatus.GAP,
            "cited_test_missing",
            (
                "pytest cannot collect every test this criterion cites, so the citation no "
                "longer means anything. Missing: "
                + ", ".join(missing)
                + ". Either the test was renamed or deleted, or the phase's criteria "
                "definition is wrong."
            ),
        )
    if failed:
        return result(
            CriterionStatus.GAP,
            "cited_test_failed",
            (
                "Cited tests ran and did not pass, so this criterion is a gap regardless of the "
                "status recorded for it. Not passing: " + ", ".join(failed) + "."
            ),
        )
    if spec.status is CriterionStatus.GAP:
        return result(
            CriterionStatus.GAP,
            "recorded_gap",
            " ".join(spec.gaps),
        )
    if spec.status is CriterionStatus.DEFERRED:
        return result(
            CriterionStatus.DEFERRED,
            "deferred_by_recorded_decision",
            (
                f"Deferred. Reason: {spec.deferral_reason} "
                f"Becomes live again when: {spec.deferral_trigger}"
            ),
        )
    return result(
        CriterionStatus.COVERED,
        "ok",
        (
            f"{len(spec.proving_node_ids)} proving and {len(spec.supporting_node_ids)} "
            "supporting tests were executed and all passed."
        ),
    )


def evaluate_criteria(
    specs: Sequence[CriterionSpec],
    outcome: SelectionOutcome,
) -> tuple[CriterionResult, ...]:
    return tuple(criterion_result(spec, outcome) for spec in specs)


def execute_criteria(
    repo_root: Path,
    specs: Sequence[CriterionSpec],
) -> tuple[CriterionResult, ...]:
    """Run every node id the criteria cite, then decide each criterion from the result."""
    cited = sorted(cited_node_ids(specs))
    outcome = run_node_ids(repo_root, cited)
    return evaluate_criteria(specs, outcome)
