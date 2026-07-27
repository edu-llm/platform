"""The Phase 3 acceptance gate: the twenty-two criteria, executed rather than read.

Every rule this gate applies is the shared one. The three statuses, what a citation may be,
and the decision that execution overrules the recorded table all live in
``edullm_platform.criteria``; the nested-execution guard and the refusal to hand pytest
anything but explicit node ids live in ``edullm_platform.criteria_runner``. This module
holds what is specific to Phase 3, which is the definition it reads and the report it
renders. There is deliberately no second copy of any of the above, for the reason Phase 1
gives and Phase 2 repeats: a phase that could restate the rule could restate it more kindly.

Criteria only, matching Phase 1 and Phase 2. Phase 0 retains a group of operational
inventory checks because they came from an earlier definition of that phase; nothing similar
exists here, and adding one would give a reader something green to look at while twenty
criteria were red.

**This gate fails today, by a wide margin, and that is the report working.** Wave 5 is held:
nothing Phase 3 describes has been deployed and no Batch job has ever run in this account.
Twenty of the twenty-two criteria are therefore recorded as gaps whose text says what is
missing and what would close it. A gate that passed on the strength of templates would be
reporting that a container ran, which nothing has observed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Final

from pydantic import BeforeValidator, Field, computed_field, model_validator

from edullm_platform.contracts.base import ContractModel, require_ordered_sequence
from edullm_platform.criteria import CriterionResult, execute_criteria
from edullm_platform.phase3_criteria import PHASE3_CRITERION_COUNT, phase3_criteria
from edullm_platform.status_prose import checked_phase_criteria_note

__all__ = ["PHASE", "Phase3GateReport", "evaluate_phase3_criteria", "evaluate_repository"]

PHASE: Final = "Phase 3"


class Phase3GateReport(ContractModel):
    """The whole Phase 3 gate. Criteria only; see the module docstring for why."""

    phase_criteria: Annotated[
        tuple[CriterionResult, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=PHASE3_CRITERION_COUNT, strict=False)
    phase_criteria_note: str = ""

    @model_validator(mode="after")
    def _derive_and_check_the_note(self) -> Phase3GateReport:
        # Derived rather than defaulted, so a caller cannot supply one and the field cannot
        # hold a sentence nothing computed. The model is frozen, which is why this is
        # written through object.__setattr__ at the end of validation.
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


def evaluate_phase3_criteria(repo_root: Path) -> tuple[CriterionResult, ...]:
    return execute_criteria(repo_root, phase3_criteria())


def evaluate_repository(repo_root: Path) -> Phase3GateReport:
    return Phase3GateReport(phase_criteria=evaluate_phase3_criteria(repo_root))
