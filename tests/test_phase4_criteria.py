"""What the Phase 4 criteria definition must be true of, independent of the phase itself.

Two kinds of test. The first kind reads the definition against the master plan: the right
number of checks, the right ones marked pilot-blocking, every gap saying what would close
it. The second kind is the one that has caught real defects in every phase so far -- asking
pytest whether the cited node ids collect. A citation that reads correctly and names
nothing turns a criterion into a claim nobody checks, and no amount of reading the
definition reveals it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from edullm_platform.criteria import CriterionStatus, cited_node_ids
from edullm_platform.phase4_capture import CAPTURE_ROOT
from edullm_platform.phase4_criteria import (
    PHASE4_CRITERION_COUNT,
    phase4_criteria,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: The eleven from the master plan, plus the twelfth this phase added. Nine of the plan's
#: are marked, and the twelfth is marked too, so ten of twelve.
PILOT_BLOCKING = ("1", "2", "3", "4", "5", "6", "7", "8", "9", "12")

#: The two that are open, and why they are different. Nine has no observation to capture;
#: eleven has a configuration nobody has deliberately widened.
OPEN = ("9", "11")

#: The module that reads the committed captures. Every criterion about the account rather
#: than about pure Python has to cite it, or it is proving something about a fixture.
RUN_EVIDENCE = "tests/test_phase4_run_evidence.py"


def test_the_definition_lists_every_check_the_phase_plan_names() -> None:
    """Mutation: drop a criterion.

    The count is the plan's eleven plus the prefix-agreement check this phase added when it
    inherited three answers to where a run writes its output. A definition short by one
    reports a phase closer to done than it is, and nothing else notices.
    """
    specs = phase4_criteria()

    assert len(specs) == PHASE4_CRITERION_COUNT
    assert [spec.number for spec in specs] == [str(n) for n in range(1, 13)]


def test_the_pilot_markers_are_the_plans_and_not_a_later_judgement() -> None:
    """Mutation: mark criterion 10 or 11 pilot-blocking, or unmark one of the nine.

    Marking is not conservatism. Every marked criterion is one the pilot rung waits on, so
    marking a criterion that costs a pilot user nothing makes the rung unreachable rather
    than safer -- and unmarking one that costs them money is the failure the marker exists
    to prevent.
    """
    marked = tuple(spec.number for spec in phase4_criteria() if spec.pilot_blocking)

    assert marked == PILOT_BLOCKING


def test_the_two_unmarked_criteria_are_the_two_about_waiting_rather_than_harm() -> None:
    """Mutation: mark either of them, or explain neither.

    Saying no is what makes the marker mean anything. Both unmarked criteria are about a
    job that waits, and a job that waits bills nothing -- which is a statement about cost
    that has to be written down, because "not marked" on its own reads like an oversight.
    """
    unmarked = {
        spec.number: " ".join((*spec.scope_limits, *spec.gaps, spec.deferral_reason or ""))
        for spec in phase4_criteria()
        if not spec.pilot_blocking
    }

    assert set(unmarked) == {"10", "11"}
    assert all("bills nothing" in written or "costs nothing" in written
               for written in unmarked.values())


def test_every_open_criterion_says_what_would_close_it() -> None:
    """Mutation: record a gap with no explanation, or with one that restates the criterion.

    A gap that does not say what would close it is a criterion nobody can pick up. Each of
    these names a different amount of work, and the difference is what somebody choosing
    what to do next is choosing between.
    """
    gaps = {spec.number: spec.gaps for spec in phase4_criteria() if spec.status is CriterionStatus.GAP}

    # Compared as a set: sorted() on these puts '11' before '9', which is a true fact
    # about strings and a confusing one to read in a failure message.
    assert set(gaps) == set(OPEN)
    for number, written in gaps.items():
        assert written, number
        assert all(len(entry) > 80 for entry in written), (
            f"criterion {number} records a gap too short to say what would close it"
        )


def test_the_one_deferral_carries_a_trigger_that_can_actually_fire() -> None:
    """Mutation: defer something with a reason and no trigger.

    The contract already refuses that at construction, so this checks the softer failure it
    cannot see: a trigger written as a condition nobody will ever observe. This one fires
    on a run somebody notices waiting, or on a second team, both of which are events that
    happen rather than states somebody has to go and measure.
    """
    deferred = [spec for spec in phase4_criteria() if spec.status is CriterionStatus.DEFERRED]

    assert [spec.number for spec in deferred] == ["10"]
    assert deferred[0].deferral_trigger
    assert "second team" in (deferred[0].deferral_trigger or "")
    assert "no CloudWatch metric" in (deferred[0].deferral_reason or "")


def test_every_criterion_about_the_account_cites_the_module_that_reads_the_captures() -> None:
    """Mutation: cover an account-level criterion on a template test.

    A test that reads a committed CloudFormation template reads what the account will be
    asked for rather than what it holds, and a compute environment edited in a console
    leaves every such citation green.
    """
    about_the_account = ("1", "3", "4", "7", "8", "12")

    for spec in phase4_criteria():
        if spec.number not in about_the_account:
            continue
        modules = {node_id.split("::", 1)[0] for node_id in spec.proving_node_ids}
        assert RUN_EVIDENCE in modules, (
            f"criterion {spec.number} is about the deployed account and proves it without "
            "reading a capture of it"
        )


def test_the_two_checkpoint_criteria_are_proved_on_functions_rather_than_on_one_run() -> None:
    """Mutation: prove them only on the committed capture.

    One real checkpoint establishes that one real checkpoint is resumable. It cannot
    establish that an *incomplete* one is refused, because the run did not produce one --
    and manufacturing an incomplete checkpoint in the bucket to capture it would be writing
    corruption into a store on purpose.
    """
    numbers = {spec.number: spec for spec in phase4_criteria()}

    for number in ("5", "6"):
        modules = {node_id.split("::", 1)[0] for node_id in numbers[number].proving_node_ids}
        assert "tests/test_phase4_checkpoints.py" in modules, number
    assert RUN_EVIDENCE in {
        node_id.split("::", 1)[0] for node_id in numbers["5"].proving_node_ids
    }, "the resumable half is also proved against the checkpoint a real run wrote"


def test_the_cross_team_criterion_admits_it_reads_a_policy_rather_than_a_refusal() -> None:
    """Mutation: state it as though a container had been told no.

    It has not. The workload role's trust policy names the Batch and ECS task services, so
    no laptop can assume it and be refused. Overstating this is the exact failure mode a
    security check has -- it reads as stronger than it is, and nobody rechecks a criterion
    that says covered.
    """
    (isolation,) = [spec for spec in phase4_criteria() if spec.number == "7"]
    written = " ".join(isolation.scope_limits)

    assert isolation.status is CriterionStatus.COVERED
    assert "rather than from a denial anybody" in written
    assert "SimulatePrincipalPolicy" in written


def test_no_criterion_cites_an_evidence_file_instead_of_a_test() -> None:
    # Evidence is proved only by tests the gate executes, so a citation is a node id and
    # never a path into fixtures/. The contract refuses anything that is not a tests/ node
    # id at construction; this says the same thing about the shape a near-miss would take.
    for node_id in cited_node_ids(phase4_criteria()):
        assert node_id.startswith("tests/")
        assert "::" in node_id
        assert "fixtures/" not in node_id
        assert not node_id.endswith((".json", ".yaml", ".md"))


def test_a_covered_criterion_never_rests_on_evidence_that_is_not_committed() -> None:
    """Mutation: cover a criterion against a capture still in the working directory.

    ``docs-frank/`` is local-only and is not in the repository, so a citation reading from
    there passes on the machine that took the capture and fails everywhere else -- or worse,
    passes vacuously because the reader treats an absent file as nothing to prove.
    """
    committed = sorted(path.name for path in CAPTURE_ROOT.glob("*.sanitized.json"))
    runs = sorted(path.name for path in (CAPTURE_ROOT / "runs").iterdir() if path.is_dir())

    assert committed, "no covered criterion may rest on captures nobody committed"
    assert len(runs) == 3


@pytest.mark.slow
def test_pytest_can_collect_every_node_id_the_definition_cites() -> None:
    # Asked of pytest rather than of the strings, because the failure this catches is a
    # citation that reads correctly and names nothing. Ten of Phase 2's were parametrized
    # tests cited without their parameter suffix, which no amount of looking at the
    # definition would have revealed.
    cited = sorted(cited_node_ids(phase4_criteria()))
    assert cited

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-header", *cited],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
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
