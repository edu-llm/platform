"""What the Phase 2 criteria definition has to be true of, before any gate runs it.

The gate executes citations, so most of the discipline is enforced there. What is left for
this module is the part a gate run cannot tell you: whether the definition describes Phase
2 rather than some other set of twenty-two statements, and whether the gaps say enough to
be acted on.

The most valuable test here is the dullest. Every cited node id must be collectible by
pytest, and this asserts it by asking pytest to collect them rather than by reading the
strings. Phase 2 shipped with ten citations that looked correct and named nothing, because
they were written from a grep over ``def test`` and the tests were parametrized -- a
mistake invisible to any amount of reading. The gate does catch it, and reports the
criteria as gaps with ``cited_test_missing``, which is the right verdict and a slow way to
find out.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from edullm_platform.criteria import (
    REENTRANT_TEST_MODULES,
    CriterionStatus,
    cited_node_ids,
)
from edullm_platform.phase2_criteria import PHASE2_CRITERION_COUNT, phase2_criteria

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_the_definition_lists_every_check_the_phase_plan_names() -> None:
    specs = phase2_criteria()

    assert len(specs) == PHASE2_CRITERION_COUNT == 22
    assert [spec.number for spec in specs] == [str(n) for n in range(1, 23)]
    assert all(spec.statement.strip() for spec in specs)


def test_the_one_deferral_is_the_inherited_one_and_carries_its_trigger() -> None:
    # A deferral without a trigger is a gap wearing a deferral's label. This one is
    # inherited from Phase 0 and its trigger is a configuration change, which is the
    # property that makes it re-enter the gate on its own rather than when somebody
    # remembers.
    deferred = [spec for spec in phase2_criteria() if spec.status is CriterionStatus.DEFERRED]

    assert len(deferred) == 1
    (wrong_team,) = deferred
    assert wrong_team.number == "4"
    assert "team_bindings" in (wrong_team.deferral_reason or "")
    assert "team_bindings" in (wrong_team.deferral_trigger or "")
    assert not wrong_team.proving_node_ids


def test_every_gap_says_what_would_close_it() -> None:
    # A gap with no written explanation is refused at construction, so what this adds is
    # that the explanation is long enough to act on. Most of these gaps exist because a
    # run happened and nothing captured it, and the difference between "not proved" and
    # "not proved, here is the artifact to capture" is the difference between a scoreboard
    # and a checklist.
    gaps = [spec for spec in phase2_criteria() if spec.status is CriterionStatus.GAP]

    assert gaps, "a Phase 2 with no gaps has either finished or stopped being honest"
    for spec in gaps:
        written = " ".join(spec.gaps)
        assert len(written) > 200, f"criterion {spec.number}'s gap text is too thin to act on"


def test_a_covered_criterion_never_rests_on_evidence_that_is_not_committed() -> None:
    # The line this definition walks. A criterion may be covered on committed artifacts --
    # a workflow file GitHub reads as-is, the admission core the Lambda carries -- and may
    # not be covered on a run somebody watched. Anything citing a Phase 2 capture must
    # wait until such a capture exists, and none does.
    covered = [spec for spec in phase2_criteria() if spec.status is CriterionStatus.COVERED]

    assert covered
    for spec in covered:
        for node_id in spec.cited_node_ids:
            assert "phase2_evidence" not in node_id
            assert "phase2_capture" not in node_id


def test_no_criterion_cites_a_module_that_would_re_enter_the_gate() -> None:
    modules = {node_id.split("::", 1)[0] for node_id in cited_node_ids(phase2_criteria())}

    assert not modules.intersection(REENTRANT_TEST_MODULES)


@pytest.mark.slow
def test_pytest_can_collect_every_node_id_the_definition_cites() -> None:
    # Asked of pytest rather than of the strings, because the failure this catches is a
    # citation that reads correctly and names nothing. Ten of these were parametrized
    # tests cited without their parameter suffix, which no amount of looking at the
    # definition would have revealed.
    cited = sorted(cited_node_ids(phase2_criteria()))
    assert cited

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-header", *cited],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # pytest prints only a count when a node id does not resolve, so the node ids it did
    # collect cannot be read off stdout in that case. The exit code and the "not found"
    # lines on stderr are what carry the answer, and reading the wrong one made this test
    # report every citation as missing when one was.
    not_found = sorted(
        line.split("::", 1)[1].strip()
        for line in completed.stderr.splitlines()
        if line.startswith("ERROR: not found:") and "::" in line
    )

    assert not not_found, (
        "these citations name no test pytest can collect, so the criteria resting on "
        f"them prove nothing: {not_found}"
    )
    assert completed.returncode == 0, completed.stderr[-2000:]

    collected = {
        line.strip()
        for line in completed.stdout.splitlines()
        if line.startswith("tests/") and "::" in line
    }
    assert set(cited) <= collected, sorted(set(cited) - collected)
