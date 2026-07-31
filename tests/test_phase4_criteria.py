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

#: The measurement of what the workload role reaches, and the test that guards the reader
#: behind it. Two of the measurement's four assertions are negative, and a negative
#: assertion is worth exactly what its reader is worth -- so a criterion that cites the
#: first without the second is protected by an instrument nothing in the gate checks.
THE_REACH_MEASUREMENT = "test_the_role_permits_exactly_the_prefix_shape_the_platform_derives"
THE_GUARD_ON_THE_REACH_MEASUREMENT = (
    "test_a_grant_on_another_bucket_does_not_widen_what_the_outputs_reach_reports"
)


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


def test_every_criterion_reading_the_reach_measurement_also_runs_the_test_guarding_it() -> None:
    """Mutation: cite the reach measurement and leave its guard to the full suite.

    ``execute_criteria`` runs the node ids the criteria name and nothing else, so a test
    that is only in the full suite protects nothing any criterion rests on. The guard here
    catches ``capture_role_scope`` going back to recording the key portion of every S3
    object ARN without looking at the bucket -- which would make the reach measurement's
    two negative assertions pass by measuring nothing, with three pilot-blocking criteria
    still green.

    The first assertion is what keeps this from passing over an empty loop. If a criterion
    starts or stops reading the reach measurement, that is the moment to decide whether the
    guard follows it, rather than a silent change to what the gate covers.
    """
    reach = f"{RUN_EVIDENCE}::{THE_REACH_MEASUREMENT}"
    guard = f"{RUN_EVIDENCE}::{THE_GUARD_ON_THE_REACH_MEASUREMENT}"
    citing = {
        spec.number: spec.cited_node_ids
        for spec in phase4_criteria()
        if reach in spec.cited_node_ids
    }

    assert sorted(citing, key=int) == ["4", "7", "12"]
    for number, cited in citing.items():
        assert guard in cited, (
            f"criterion {number} is decided on what the role reaches and does not run the "
            "test that the reader measuring reach still discriminates by bucket"
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


def test_the_cross_team_criterion_rests_on_a_refusal_and_says_which_one() -> None:
    """Mutation: leave it citing the policy reader after a container was refused.

    This criterion rested on the deployed policy document until a run probed four prefixes
    and recorded what S3 said. The upgrade has to reach the citations, not just the prose:
    a criterion whose scope limits describe a refusal while its proving tests read a
    template is claiming the stronger thing and checking the weaker one.

    The distinction the prose has to keep is AccessDenied against NoSuchKey. The second
    means the role was permitted to look and found nothing -- which is what a role granting
    everything returns from an empty prefix, and establishes no isolation at all.
    """
    (isolation,) = [spec for spec in phase4_criteria() if spec.number == "7"]
    written = " ".join(isolation.scope_limits)

    assert isolation.status is CriterionStatus.COVERED
    assert "AccessDenied" in written and "NoSuchKey" in written
    assert "SimulatePrincipalPolicy" in written
    assert any(
        "was_refused" in node_id for node_id in isolation.proving_node_ids
    ), "the proving citation must read the refusal, not the grant"


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
    # Not a count: runs accumulate, and a number here becomes something somebody bumps.
    assert runs, "no run capture is committed, so every criterion citing one proves nothing"


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
