"""The Phase 5 acceptance gate: the fifteen criteria, executed rather than read.

Every rule this gate applies is the shared one. The three statuses, what a citation may be,
and the decision that execution overrules the recorded table all live in
:mod:`edullm_platform.criteria`; the nested-execution guard and the refusal to hand pytest
anything but explicit node ids live in :mod:`edullm_platform.criteria_runner`; and what a
phase report computes and what its command prints live in :mod:`edullm_platform.phase_gate`.
This module holds what is specific to Phase 5, which is the definition it reads and the
number of criteria it holds the report to. There is deliberately no second copy of any of
the above, for the reason Phase 1 gives and every phase since repeats: a phase that could
restate the rule could restate it more kindly.

**This gate passes, and it passes with something outstanding, which is a state worth
reading slowly.** Fourteen of fifteen are covered and the fifteenth is deferred. That one
wants a GPU run under a team other than ``platform`` writing a checkpoint, and it is
outstanding for want of a submission rather than for want of a mechanism: the three pilot
runs all went to the CPU profile carrying a print statement and none of them wrote a
checkpoint. The observation moved to Phase 6's closeout on 2026-07-31, where it still
carries this phase's gate; ``phase5_criteria`` argues the move where a reader of the gate's
output will see it.

**The pilot verdict is the one worth reading, and it opened on the same word.** Ten of the
fifteen are pilot-blocking -- still the highest proportion of any phase, because this phase
*is* the pilot rung and almost every check is a precondition for a real person's run being
real rather than a demonstration staged for them. All ten pass. The eleventh marker came off
criterion 6 because the shared contract refuses a criterion that is both deferred and
pilot-blocking, so the rung and the gate moved together on one status change rather than on
two decisions.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Final

from pydantic import Field

from edullm_platform.criteria import CriterionResult, execute_criteria
from edullm_platform.phase5_criteria import PHASE5_CRITERION_COUNT, phase5_criteria
from edullm_platform.phase_gate import OrderedCriteria, PhaseGateReport

__all__ = ["PHASE", "Phase5GateReport", "evaluate_phase5_criteria", "evaluate_repository"]

PHASE: Final = "Phase 5"


class Phase5GateReport(PhaseGateReport):
    """The whole Phase 5 gate. Criteria only; see the module docstring for why."""

    phase: ClassVar[str] = PHASE

    phase_criteria: OrderedCriteria = Field(min_length=PHASE5_CRITERION_COUNT, strict=False)


def evaluate_phase5_criteria(repo_root: Path) -> tuple[CriterionResult, ...]:
    return execute_criteria(repo_root, phase5_criteria())


def evaluate_repository(repo_root: Path) -> Phase5GateReport:
    return Phase5GateReport(phase_criteria=evaluate_phase5_criteria(repo_root))
