"""The Phase 3 acceptance gate: the nineteen criteria, executed rather than read.

Every rule this gate applies is the shared one. The three statuses, what a citation may be,
and the decision that execution overrules the recorded table all live in
``edullm_platform.criteria``; the nested-execution guard and the refusal to hand pytest
anything but explicit node ids live in ``edullm_platform.criteria_runner``; and what a
phase report computes and what its command prints live in ``edullm_platform.phase_gate``.
This module holds what is specific to Phase 3, which is the definition it reads and the
number of criteria it holds the report to. There is deliberately no second copy of any of
the above, for the reason Phase 1 gives and Phase 2 repeats: a phase that could restate the
rule could restate it more kindly.

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

This is the phase the pilot rung was worked out on, and the verdict is not kind to it:
every pilot-blocking criterion here waits on the first container run, so the rung is closed
rather than merely unevidenced.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Final

from pydantic import Field

from edullm_platform.criteria import CriterionResult, execute_criteria
from edullm_platform.phase3_criteria import PHASE3_CRITERION_COUNT, phase3_criteria
from edullm_platform.phase_gate import OrderedCriteria, PhaseGateReport

__all__ = ["PHASE", "Phase3GateReport", "evaluate_phase3_criteria", "evaluate_repository"]

PHASE: Final = "Phase 3"


class Phase3GateReport(PhaseGateReport):
    """The whole Phase 3 gate. Criteria only; see the module docstring for why."""

    phase: ClassVar[str] = PHASE

    phase_criteria: OrderedCriteria = Field(min_length=PHASE3_CRITERION_COUNT, strict=False)


def evaluate_phase3_criteria(repo_root: Path) -> tuple[CriterionResult, ...]:
    return execute_criteria(repo_root, phase3_criteria())


def evaluate_repository(repo_root: Path) -> Phase3GateReport:
    return Phase3GateReport(phase_criteria=evaluate_phase3_criteria(repo_root))
