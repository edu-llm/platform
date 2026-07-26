"""The Phase 1 acceptance gate: the eight criteria, executed rather than read.

Every rule this gate applies is the shared one. The three statuses, what a citation may
be, and the decision that execution overrules the recorded table all live in
``edullm_platform.criteria``, and the nested-execution guard and the refusal to hand
pytest anything but explicit node ids live in ``edullm_platform.criteria_runner``. This
module holds what is specific to Phase 1, which is the criteria definition it reads and
the report it renders. There is deliberately no second copy of any of the above: a phase
that could restate the rule could restate it more kindly.

Phase 1 has no counterpart to Phase 0's operational inventory checks. Those were retained
there because they came from an earlier definition of that phase and are useful; nothing
similar exists here, so the report is the criteria and nothing else, and the verdict is
the AND of them. A gate that reported an extra group of checks nobody had asked for would
give a reader something green to look at while four criteria were red.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Final

from pydantic import BeforeValidator, Field, computed_field

from edullm_platform.contracts.base import ContractModel, require_ordered_sequence
from edullm_platform.criteria import CriterionResult, execute_criteria
from edullm_platform.phase1_criteria import PHASE1_CRITERION_COUNT, phase1_criteria

__all__ = ["Phase1GateReport", "evaluate_phase1_criteria", "evaluate_repository"]

PHASE_CRITERIA_NOTE: Final = (
    "phase_criteria are the eight Phase 1 acceptance criteria. Every pytest node id cited "
    "for a criterion was executed by this run. A criterion whose cited tests do not all exist "
    "and pass is a gap and fails the gate, whatever status the definition records. Only three "
    "statuses exist: covered passes, deferred passes and requires a written reason and a "
    "written trigger, gap fails. Four criteria are gaps today because the build path has "
    "never run against the account, and no test can stand in for a run that has not happened."
)


class Phase1GateReport(ContractModel):
    """The whole Phase 1 gate. Criteria only; see the module docstring for why."""

    phase_criteria: Annotated[
        tuple[CriterionResult, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=PHASE1_CRITERION_COUNT, strict=False)
    phase_criteria_note: str = PHASE_CRITERIA_NOTE

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        return all(criterion.passed for criterion in self.phase_criteria)


def evaluate_phase1_criteria(repo_root: Path) -> tuple[CriterionResult, ...]:
    return execute_criteria(repo_root, phase1_criteria())


def evaluate_repository(repo_root: Path) -> Phase1GateReport:
    return Phase1GateReport(phase_criteria=evaluate_phase1_criteria(repo_root))
