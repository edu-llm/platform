"""The Phase 4 acceptance gate: the twelve criteria, executed rather than read.

Every rule this gate applies is the shared one. The three statuses, what a citation may be,
and the decision that execution overrules the recorded table all live in
:mod:`edullm_platform.criteria`; the nested-execution guard and the refusal to hand pytest
anything but explicit node ids live in :mod:`edullm_platform.criteria_runner`. This module
holds what is specific to Phase 4, which is the definition it reads and the report it
renders. There is deliberately no second copy of any of the above, for the reason Phase 1
gives and every phase since repeats: a phase that could restate the rule could restate it
more kindly.

**This gate fails today, and by a much narrower margin than any previous phase's did at the
same point.** Nine of twelve criteria are covered by tests reading committed captures of
three real GPU jobs. What is left is one gap nobody can close without causing a capacity
failure, one gap that is a cost decision rather than a defect, and one deferral with a
written trigger. A gate that passed would be claiming that a job held in RUNNABLE gets
noticed, which nothing here does.

**The pilot verdict is the one worth reading.** Ten of the twelve are pilot-blocking -- the
highest proportion of any phase, because a GPU instance bills whether or not the container
is using it -- and exactly one of those ten is open.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Final

from pydantic import BeforeValidator, Field, computed_field, model_validator

from edullm_platform.contracts.base import ContractModel, require_ordered_sequence
from edullm_platform.criteria import (
    CriterionResult,
    PilotVerdict,
    execute_criteria,
    pilot_verdict,
)
from edullm_platform.phase4_criteria import PHASE4_CRITERION_COUNT, phase4_criteria
from edullm_platform.status_prose import checked_phase_criteria_note

__all__ = ["PHASE", "Phase4GateReport", "evaluate_phase4_criteria", "evaluate_repository"]

PHASE: Final = "Phase 4"


class Phase4GateReport(ContractModel):
    """The whole Phase 4 gate. Criteria only; see the module docstring for why."""

    phase_criteria: Annotated[
        tuple[CriterionResult, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=PHASE4_CRITERION_COUNT, strict=False)
    phase_criteria_note: str = ""

    @model_validator(mode="after")
    def _derive_and_check_the_note(self) -> Phase4GateReport:
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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pilot(self) -> PilotVerdict:
        """The adoption verdict, computed beside the gate's and never folded into it.

        This is the first phase where the two verdicts are likely to differ in the
        interesting direction: the gate is red on criteria nobody can close tonight, while
        the capability a pilot user would actually reach has been exercised end to end.
        Keeping them separate is what lets both be said without one softening the other.
        """
        return pilot_verdict(self.phase_criteria)


def evaluate_phase4_criteria(repo_root: Path) -> tuple[CriterionResult, ...]:
    return execute_criteria(repo_root, phase4_criteria())


def evaluate_repository(repo_root: Path) -> Phase4GateReport:
    return Phase4GateReport(phase_criteria=evaluate_phase4_criteria(repo_root))
