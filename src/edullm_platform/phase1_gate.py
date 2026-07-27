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
give a reader something green to look at while a criterion was red.

The note this report emits states no fact it does not compute. It once ended with a
sentence saying four criteria were gaps, which was true when it was written and was still
being printed beside ``passed: true`` after the build path closed them. It is now built by
``edullm_platform.status_prose`` from the criteria this run computed, and the same reader
that refuses a proof bundle whose prose disagrees with the gate is run over it here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Final

from pydantic import BeforeValidator, Field, computed_field, model_validator

from edullm_platform.contracts.base import ContractModel, require_ordered_sequence
from edullm_platform.criteria import CriterionResult, execute_criteria
from edullm_platform.phase1_criteria import PHASE1_CRITERION_COUNT, phase1_criteria
from edullm_platform.status_prose import checked_phase_criteria_note

__all__ = ["PHASE", "Phase1GateReport", "evaluate_phase1_criteria", "evaluate_repository"]

PHASE: Final = "Phase 1"


class Phase1GateReport(ContractModel):
    """The whole Phase 1 gate. Criteria only; see the module docstring for why."""

    phase_criteria: Annotated[
        tuple[CriterionResult, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=PHASE1_CRITERION_COUNT, strict=False)
    phase_criteria_note: str = ""

    @model_validator(mode="after")
    def _derive_and_check_the_note(self) -> Phase1GateReport:
        # The note is derived rather than defaulted, so a caller cannot supply one and the
        # field cannot hold a sentence nothing computed. The model is frozen, which is why
        # this is written through object.__setattr__ at the end of validation.
        object.__setattr__(
            self,
            "phase_criteria_note",
            checked_phase_criteria_note(self.phase_criteria, phase=PHASE),
        )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        return all(criterion.passed for criterion in self.phase_criteria)


def evaluate_phase1_criteria(repo_root: Path) -> tuple[CriterionResult, ...]:
    return execute_criteria(repo_root, phase1_criteria())


def evaluate_repository(repo_root: Path) -> Phase1GateReport:
    return Phase1GateReport(phase_criteria=evaluate_phase1_criteria(repo_root))
