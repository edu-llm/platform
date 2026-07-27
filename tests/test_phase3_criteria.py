"""What the Phase 3 criteria definition has to be true of, before any gate runs it.

The gate executes citations, so most of the discipline is enforced there. What is left for
this module is the part a gate run cannot tell you: whether the definition describes Phase 3
rather than some other set of twenty-two statements, whether the gaps say enough to be acted
on, and -- the one that matters most in this phase -- whether the honest status has been
kept.

**The pressure this module exists to resist.** Twenty of the twenty-two criteria are gaps
because Wave 5 is held, so ``tools/validate_phase3.py`` exits 1 and will go on exiting 1
until a container has actually run. That is uncomfortable in exactly the way that invites
the wrong fix: relabel the live ones as deferrals, and the gate goes green without anything
changing in the account. A deferral is a decision not to do something with a trigger that
makes it live again; unfinished work with a deploy in front of it is a gap. The cases below
hold that line, and one of them holds it in the direction that will matter later -- a
deferral, if one is ever added, must carry a trigger nobody has to remember.

The dullest test here is the most valuable. Every cited node id must be collectible by
pytest, and this asks pytest rather than reading the strings. Phase 2 shipped with ten
citations that looked correct and named nothing, because they were written from a grep over
``def test`` and the tests were parametrized -- a mistake invisible to any amount of reading.
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
from edullm_platform.phase3_criteria import (
    PHASE3_CRITERION_COUNT,
    phase3_criteria,
)
from tests.gate_support import evidence_not_in_the_tree, fixtures_backing

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: The two criteria that rest on nothing the account has to answer for. Named here rather
#: than counted, so that a third one arriving is a change somebody argued for.
CRITERIA_THAT_DO_NOT_WAIT_FOR_A_DEPLOY = ("20", "22")


def test_the_definition_lists_every_check_the_phase_plan_names() -> None:
    specs = phase3_criteria()

    assert len(specs) == PHASE3_CRITERION_COUNT == 22
    assert [spec.number for spec in specs] == [str(n) for n in range(1, 23)]
    assert all(spec.statement.strip() for spec in specs)


def test_only_the_two_criteria_that_need_no_deployment_are_covered() -> None:
    """Mutation: mark a live criterion covered, or relabel one as deferred.

    Phase 3's claim is that a container ran. Nothing has, so every criterion that would be
    established by observing one is a gap, and the two that are not are the two that read a
    committed template and a committed decision. Naming them rather than counting them is
    what makes a third arrival visible in a diff.
    """
    specs = phase3_criteria()
    covered = [spec.number for spec in specs if spec.status is CriterionStatus.COVERED]
    gaps = [spec.number for spec in specs if spec.status is CriterionStatus.GAP]

    assert covered == list(CRITERIA_THAT_DO_NOT_WAIT_FOR_A_DEPLOY)
    assert len(gaps) == 20
    assert set(covered) | set(gaps) == {spec.number for spec in specs}


def test_nothing_is_deferred_because_nothing_here_is_postponed() -> None:
    """Mutation: record a live criterion as deferred to make the gate exit 0.

    This is the change that would make Phase 3 look finished, and it is available at any
    time to anybody who finds the red gate inconvenient. A deferral is a decision not to
    satisfy a criterion, with a written trigger; Phase 3's live checks are unfinished work
    with a deploy in front of them, which is a different thing and has a different word.

    The second half is what this case is really for. If a deferral is ever added it may be
    added deliberately, and the shared contract already requires a reason and a trigger --
    so what is asserted here is that today there is none, and that any that arrives is
    checked by the same rule rather than exempted by having got in early.
    """
    deferred = [spec for spec in phase3_criteria() if spec.status is CriterionStatus.DEFERRED]

    assert deferred == []
    for spec in deferred:  # pragma: no cover - runs the day the assertion above changes
        assert (spec.deferral_reason or "").strip()
        assert (spec.deferral_trigger or "").strip()
        assert not spec.proving_node_ids


def test_every_gap_says_what_would_close_it() -> None:
    # A gap with no written explanation is refused at construction, so what this adds is
    # that the explanation is long enough to act on. Nineteen of these exist because
    # nothing is deployed, and the difference between "not proved" and "not proved, here
    # is the artifact to capture" is the difference between a scoreboard and a checklist.
    gaps = [spec for spec in phase3_criteria() if spec.status is CriterionStatus.GAP]

    assert gaps, "a Phase 3 with no gaps has either finished or stopped being honest"
    for spec in gaps:
        written = " ".join(spec.gaps)
        assert len(written) > 200, f"criterion {spec.number}'s gap text is too thin to act on"


def test_every_gap_says_how_to_close_it_and_says_it_the_same_way() -> None:
    """One sentence, twenty times, and a reader should not have to check the twentieth.

    Mutation: write a per-criterion variation of "capture it and cite a test". Twenty
    slightly different sentences would read as twenty different procedures, and the one
    that differed by accident would be indistinguishable from the one that differed for a
    reason.

    Three criteria state their own reason for being open rather than the shared one, and
    each is a genuinely different reason: 3 needs no live call at all and still has no
    committed capture to read, 6 names a component nobody has built, and 21 has terms the
    plan did not anticipate because the quota landed. Naming them here is what stops a
    fourth exception being added because the shared sentence was slightly awkward.
    """
    from edullm_platform.phase3_criteria import NEEDS_THE_LIVE_MATRIX, NOTHING_IS_DEPLOYED

    gaps = [spec for spec in phase3_criteria() if spec.status is CriterionStatus.GAP]
    stating_their_own_reason = [
        spec.number for spec in gaps if NOTHING_IS_DEPLOYED not in spec.gaps
    ]

    assert len(gaps) == 20
    for spec in gaps:
        assert NEEDS_THE_LIVE_MATRIX in spec.gaps, spec.number
    assert stating_their_own_reason == ["3", "6", "21"]


def test_a_covered_criterion_never_rests_on_evidence_that_is_not_committed() -> None:
    """A criterion may be covered on a capture only while that capture is in the tree.

    The line this definition walks. A criterion may be covered on committed artifacts -- a
    CloudFormation template this repository commits, a decision recorded where it is
    enforced -- and may not be covered on anything an account would have to answer for.

    Mutation: mark criterion 21 covered on ``tests/test_phase3_account_measurements.py``.
    The account measurements are the only Phase 3 capture that exists, and they record
    what the account was probed for before any stack was applied, so a covered criterion
    resting on anything under ``fixtures/evidence/phase-3/`` today is reporting a deploy
    that has not happened. The second mutation is the general one: delete a capture a
    covered criterion rests on and leave the criterion covered.

    Expressed against the tree rather than against module names, and that is the repair.
    The previous version refused node ids containing ``phase3_evidence``, ``phase3_capture``
    or ``run_evidence``. No test module is named for either of the first two, and no Phase 3
    criterion cites the one module the third matches, so all three held over an empty loop
    body. Nothing below reads the name of a module or of a capture.
    """
    covered = [spec for spec in phase3_criteria() if spec.status is CriterionStatus.COVERED]
    assert covered

    assert evidence_not_in_the_tree(covered) == ()

    # The Phase 3 half of the same line, and what makes this more than a file-exists check
    # while Wave 5 is held: no covered criterion may rest on a Phase 3 capture, because the
    # only one committed was taken before the stack it would describe existed. Criterion 22
    # picks up Phase 1's run capture through the module its citation shares with the
    # decisions that do read it, which is an over-approximation this check is content with:
    # it can only ever ask for more to be committed, never for less.
    resting_on = frozenset().union(*fixtures_backing(covered, CriterionStatus.COVERED).values())
    assert [path for path in sorted(resting_on) if "phase-3" in path.parts] == []


def test_no_criterion_cites_an_evidence_file_instead_of_a_test() -> None:
    # Evidence is proved only by tests the gate executes, so a citation is a node id and
    # never a path into fixtures/. The contract refuses anything that is not a tests/ node
    # id at construction; this says the same thing about the shape a near-miss would take.
    for node_id in cited_node_ids(phase3_criteria()):
        assert node_id.startswith("tests/")
        assert "::" in node_id
        assert "fixtures/" not in node_id
        assert not node_id.endswith((".json", ".yaml", ".md"))


def test_no_criterion_cites_a_module_that_would_re_enter_the_gate() -> None:
    modules = {node_id.split("::", 1)[0] for node_id in cited_node_ids(phase3_criteria())}

    assert not modules.intersection(REENTRANT_TEST_MODULES)


def test_the_deployer_criterion_states_both_unscoped_statements() -> None:
    """The plan said "exactly the six measured ones" and the deployer carries two groups.

    Mutation: restore the plan's wording. Ten read-only ``ec2:Describe*`` actions are also
    on ``"*"``, for a different reason -- EC2's account-wide model rather than the
    resource-type probe -- and a criterion that mentioned only the six would either read as
    violated by a template that is correct, or be quietly satisfied by folding the ten into
    the measured statement, which is the worse of the two.
    """
    (deployer,) = [spec for spec in phase3_criteria() if spec.number == "20"]
    scope = " ".join(deployer.scope_limits)

    assert "two statements" in deployer.statement
    assert "ec2:Describe*" in scope
    assert "account-wide" in scope
    assert deployer.status is CriterionStatus.COVERED


def test_the_networking_criterion_records_the_terms_it_actually_has() -> None:
    """The plan assumed a borrowed VPC and the quota landed, so the terms are different.

    Mutation: leave the plan's "largest known limitation" wording in place. It would report
    a dependency this phase does not have, and it would hide the one that remains -- that
    nothing is deployed, so no id of the networking the environment uses exists to record.
    """
    (networking,) = [spec for spec in phase3_criteria() if spec.number == "21"]
    written = " ".join(networking.gaps)

    assert networking.status is CriterionStatus.GAP
    assert "L-F678F1CE" in written
    assert "our own VPC" in written
    assert "not deployed" in written


@pytest.mark.slow
def test_pytest_can_collect_every_node_id_the_definition_cites() -> None:
    # Asked of pytest rather than of the strings, because the failure this catches is a
    # citation that reads correctly and names nothing. Ten of Phase 2's were parametrized
    # tests cited without their parameter suffix, which no amount of looking at the
    # definition would have revealed.
    cited = sorted(cited_node_ids(phase3_criteria()))
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
    # lines on stderr are what carry the answer.
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
