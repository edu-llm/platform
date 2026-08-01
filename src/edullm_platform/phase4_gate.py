"""The Phase 4 acceptance gate: the eleven criteria, executed rather than read.

Every rule this gate applies is the shared one. The three statuses, what a citation may be,
and the decision that execution overrules the recorded table all live in
:mod:`edullm_platform.criteria`; the nested-execution guard and the refusal to hand pytest
anything but explicit node ids live in :mod:`edullm_platform.criteria_runner`; and what a
phase report computes and what its command prints live in :mod:`edullm_platform.phase_gate`.
This module holds what is specific to Phase 4, which is the definition it reads and the
number of criteria it holds the report to. There is deliberately no second copy of any of
the above, for the reason Phase 1 gives and every phase since repeats: a phase that could
restate the rule could restate it more kindly.

**This gate passes today, and what it is passing on is worth reading before the exit code
is quoted.** Nine of eleven criteria are covered by tests reading committed captures of
three real GPU jobs. The other two are deferrals with written triggers: the queue-wait
detector, which needs building rather than configuring, and the alternate instance shape,
where the one-item list is itself the cost control. Neither has been observed and the gate
says so in the detail it prints beside each of them.

**The gate was red until 2026-07-31 and the change was not a run.** It was red on capacity
failure, which could not be closed by running anything -- Batch leaves a job it cannot place
in RUNNABLE indefinitely, so nothing surfaces it until the detector of criterion 10 exists,
and that detector is unbuilt. A criterion blocked on a mechanism nothing here builds
measures that work rather than this phase, so it moved out with its sentence and its number
intact, on the terms Phase 3's cancellation criteria moved on. What it protected is on the
pilot limitations page instead. The hole where criterion 9 used to be is deliberate and
:mod:`edullm_platform.phase4_criteria` says why.

**The pilot verdict is the one worth reading.** Nine of the eleven are pilot-blocking -- the
highest proportion of any phase, because a GPU instance bills whether or not the container
is using it -- and every one of them is covered. The two that are not marked are the two
deferrals, which the contract would refuse the marker on in any case: what a deferred check
would have protected belongs on the limitations page instead. One paragraph there carries
both of them, because waiting for capacity and waiting for the one shape a job can run on
are the same wait to the person doing it.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Final

from pydantic import Field

from edullm_platform.criteria import CriterionResult, execute_criteria
from edullm_platform.phase4_criteria import PHASE4_CRITERION_COUNT, phase4_criteria
from edullm_platform.phase_gate import OrderedCriteria, PhaseGateReport

__all__ = ["PHASE", "Phase4GateReport", "evaluate_phase4_criteria", "evaluate_repository"]

PHASE: Final = "Phase 4"


class Phase4GateReport(PhaseGateReport):
    """The whole Phase 4 gate. Criteria only; see the module docstring for why."""

    phase: ClassVar[str] = PHASE

    phase_criteria: OrderedCriteria = Field(min_length=PHASE4_CRITERION_COUNT, strict=False)


def evaluate_phase4_criteria(repo_root: Path) -> tuple[CriterionResult, ...]:
    return execute_criteria(repo_root, phase4_criteria())


def evaluate_repository(repo_root: Path) -> Phase4GateReport:
    return Phase4GateReport(phase_criteria=evaluate_phase4_criteria(repo_root))
