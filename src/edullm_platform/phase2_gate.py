"""The Phase 2 acceptance gate: the twenty-two criteria, executed rather than read.

Every rule this gate applies is the shared one. The three statuses, what a citation may
be, and the decision that execution overrules the recorded table all live in
``edullm_platform.criteria``; the nested-execution guard and the refusal to hand pytest
anything but explicit node ids live in ``edullm_platform.criteria_runner``; and what a
phase report computes and what its command prints live in ``edullm_platform.phase_gate``.
This module holds what is specific to Phase 2, which is the definition it reads and the
number of criteria it holds the report to. There is deliberately no second copy of any of
the above, for the reason Phase 1 gives: a phase that could restate the rule could restate
it more kindly.

Criteria only, matching Phase 1. Phase 0 retains a group of operational inventory checks
because they came from an earlier definition of that phase; nothing similar exists here,
and adding one would give a reader something green to look at while a criterion was red.

**This gate is expected to fail today, and that is the report working.** Phase 2's path
ran end to end on 2026-07-27, but almost nothing about those runs is committed, so most
criteria are recorded as gaps whose text says what was observed and what would close them.
A gate that passed on the strength of runs nobody captured would be measuring the memory of
whoever watched them.

Phase 2 is also where the two verdicts first disagree in a way that matters: the path ran
end to end and most of what it did was never captured, so criteria are red for want of
evidence rather than for want of a mechanism.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Final

from pydantic import Field

from edullm_platform.criteria import CriterionResult, execute_criteria
from edullm_platform.phase2_criteria import PHASE2_CRITERION_COUNT, phase2_criteria
from edullm_platform.phase_gate import OrderedCriteria, PhaseGateReport

__all__ = ["PHASE", "Phase2GateReport", "evaluate_phase2_criteria", "evaluate_repository"]

PHASE: Final = "Phase 2"


class Phase2GateReport(PhaseGateReport):
    """The whole Phase 2 gate. Criteria only; see the module docstring for why."""

    phase: ClassVar[str] = PHASE

    phase_criteria: OrderedCriteria = Field(min_length=PHASE2_CRITERION_COUNT, strict=False)


def evaluate_phase2_criteria(repo_root: Path) -> tuple[CriterionResult, ...]:
    return execute_criteria(repo_root, phase2_criteria())


def evaluate_repository(repo_root: Path) -> Phase2GateReport:
    return Phase2GateReport(phase_criteria=evaluate_phase2_criteria(repo_root))
