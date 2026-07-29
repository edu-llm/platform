"""The Phase 3 acceptance gate: the nineteen criteria, executed rather than read.

Every rule this gate applies is the shared one. The three statuses, what a citation may be,
and the decision that execution overrules the recorded table all live in
``edullm_platform.criteria``; the nested-execution guard and the refusal to hand pytest
anything but explicit node ids live in ``edullm_platform.criteria_runner``. This module
holds what is specific to Phase 3, which is the definition it reads and the report it
renders. There is deliberately no second copy of any of the above, for the reason Phase 1
gives and Phase 2 repeats: a phase that could restate the rule could restate it more kindly.

Criteria only, matching Phase 1 and Phase 2. Phase 0 retains a group of operational
inventory checks because they came from an earlier definition of that phase; nothing similar
exists here, and adding one would give a reader something green to look at while the checks
the phase is about were red.

**This gate fails today, and that is the report working.** The phase is deployed and four
runs have completed, so most of the criteria now rest on captures of what those runs left
behind. Every one still open names an observation nobody has made -- a case no run has been
aimed at, or a capture of a shape the per-run records cannot produce by construction -- and
each one's text says what is missing and what would close it. No count is repeated here,
because a count in a docstring is a count nothing recomputes; the definition holds them and
``tests/test_phase3_criteria.py`` asserts them.
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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pilot(self) -> PilotVerdict:
        """The adoption verdict, computed beside the gate's and never folded into it.

        This is the phase the split was worked out on, and the verdict is not kind to it:
        every pilot-blocking criterion here waits on the first container run, so the
        rung is closed rather than merely unevidenced.
        """
        return pilot_verdict(self.phase_criteria)


def evaluate_phase3_criteria(repo_root: Path) -> tuple[CriterionResult, ...]:
    return execute_criteria(repo_root, phase3_criteria())


def evaluate_repository(repo_root: Path) -> Phase3GateReport:
    return Phase3GateReport(phase_criteria=evaluate_phase3_criteria(repo_root))
