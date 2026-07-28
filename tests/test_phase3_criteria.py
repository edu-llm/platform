"""What the Phase 3 criteria definition has to be true of, before any gate runs it.

The gate executes citations, so most of the discipline is enforced there. What is left for
this module is the part a gate run cannot tell you: whether the definition describes Phase 3
rather than some other set of twenty-two statements, whether the gaps say enough to be acted
on, and -- the one that matters most in this phase -- whether the honest status has been
kept.

**The pressure this module exists to resist.** Nine of the twenty-two criteria are still
gaps -- three cancellation checks with no component behind them, four scenarios nobody has
run, and two observations a per-run capture cannot make -- so ``tools/validate_phase3.py``
exits 1 and will go on exiting 1 until a job can be stopped. That is uncomfortable in
exactly the way that invites the wrong fix: relabel the remaining ones as deferrals, and
the gate goes green without anything changing in the account. A deferral is a decision not
to do something with a trigger that makes it live again; unfinished work is a gap. The
cases below hold that line, and one of them holds it in the direction that will matter
later -- a deferral, if one is ever added, must carry a trigger nobody has to remember.

**The pressure in the other direction, now that thirteen are covered.** A criterion covered
on a capture is only as good as the capture, and the failure mode is a citation that
outlives the evidence under it. So the cases here check that every covered criterion's
citations rest on files that are actually in the tree, and that the ones resting on live
captures are the ones a live run could reach -- not a template test relabelled.

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
from edullm_platform.phase3_capture import PHASE3_CAPTURE_DIR, RUNS_SUBDIR
from edullm_platform.phase3_criteria import (
    PHASE3_CRITERION_COUNT,
    phase3_criteria,
)
from tests.gate_support import evidence_not_in_the_tree, fixtures_backing

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: The two criteria that rest on nothing the account has to answer for: one reads a
#: committed CloudFormation template, the other a decision recorded where it is enforced.
#: Named rather than counted, so that a third one arriving is a change somebody argued for.
CRITERIA_THAT_DO_NOT_WAIT_FOR_A_DEPLOY = ("20", "22")

#: The criteria covered by reading what four completed runs left behind. Every one of them
#: was a gap until the runs happened, and every one goes back to being a gap when the
#: captures expire.
COVERED_ON_A_CAPTURED_RUN = ("1", "2", "3", "4", "8", "9", "15", "16", "17", "19", "21")

#: The three criteria that need a cancellation path built before anything can be observed.
NEEDING_A_COMPONENT_BUILT = ("5", "6", "7")

#: The four that need a run aimed at them and no new infrastructure.
NEEDING_A_RUN_AIMED_AT_THEM = ("10", "11", "12", "13")

#: The two whose evidence a per-run capture cannot produce by construction: one is about
#: two roles belonging to Phase 2's registry, the other about the lineage store as a whole.
NEEDING_A_DIFFERENT_SHAPE_OF_CAPTURE = ("14", "18")


def test_the_definition_lists_every_check_the_phase_plan_names() -> None:
    specs = phase3_criteria()

    assert len(specs) == PHASE3_CRITERION_COUNT == 22
    assert [spec.number for spec in specs] == [str(n) for n in range(1, 23)]
    assert all(spec.statement.strip() for spec in specs)


def test_the_covered_criteria_are_exactly_the_ones_the_evidence_reaches() -> None:
    """Mutation: mark a criterion covered that no run and no committed artifact reaches.

    Every criterion is in exactly one of four groups, and each group is named rather than
    counted so that moving one between them is visible in a diff. Two rest on committed
    artifacts alone; eleven rest on what four completed runs left behind; nine are still
    open, for three different reasons that decide what closing each one costs.

    The count is asserted too, but only as a cross-check on the naming. A test that
    asserted "thirteen are covered" would go on passing after somebody covered a
    cancellation criterion and un-covered the timeout one.
    """
    specs = phase3_criteria()
    covered = [spec.number for spec in specs if spec.status is CriterionStatus.COVERED]
    gaps = [spec.number for spec in specs if spec.status is CriterionStatus.GAP]
    expected_gaps = sorted(
        [
            *NEEDING_A_COMPONENT_BUILT,
            *NEEDING_A_RUN_AIMED_AT_THEM,
            *NEEDING_A_DIFFERENT_SHAPE_OF_CAPTURE,
        ],
        key=int,
    )

    assert sorted(covered, key=int) == sorted(
        [*CRITERIA_THAT_DO_NOT_WAIT_FOR_A_DEPLOY, *COVERED_ON_A_CAPTURED_RUN], key=int
    )
    assert sorted(gaps, key=int) == expected_gaps
    assert set(covered) | set(gaps) == {spec.number for spec in specs}
    assert set(covered).isdisjoint(gaps)


def test_every_criterion_covered_on_a_run_cites_the_module_that_reads_the_captures() -> None:
    """Mutation: mark a live criterion covered on a template test.

    The eleven criteria above are about the account, and the way that claim goes wrong is
    subtle: a template test proves what the repository asks for, passes forever, and reads
    like proof of a deployment. So each of the eleven must have at least one *proving*
    citation in the module that reads the committed captures -- supporting citations may
    come from anywhere, and several deliberately do.
    """
    by_number = {spec.number: spec for spec in phase3_criteria()}

    for number in COVERED_ON_A_CAPTURED_RUN:
        spec = by_number[number]
        assert spec.status is CriterionStatus.COVERED, number
        proving = [
            node_id
            for node_id in spec.proving_node_ids
            if node_id.startswith("tests/test_phase3_run_evidence.py::")
        ]
        assert proving, (
            f"criterion {number} is covered on a live claim and no proving citation reads "
            "the committed captures"
        )


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
    """Two shared sentences, and which one a gap carries is the estimate.

    Mutation: write a per-criterion variation of "capture it and cite a test". Slightly
    different sentences read as different procedures, and the one that differed by accident
    would be indistinguishable from the one that differed for a reason.

    Two sentences rather than one, because the gaps are not all the same size and saying so
    is the point. ``NEEDS_A_COMPONENT_BUILT`` means code has to be written and deployed
    before a run could show anything; ``CAPTURE_A_RUN_AIMED_AT_IT`` means the mechanism is
    there and nobody has pointed a run at it. Collapsing them would tell a reader planning
    the next session that cancellation and a duplicate submission cost the same.

    Two criteria carry neither, and each says why in its own words: 14 needs two roles that
    belong to another phase's registry captured, and 18 needs an inventory of the whole
    lineage store rather than of one run. Naming them here is what stops a third exception
    being added because the shared sentences were slightly awkward.
    """
    from edullm_platform.phase3_criteria import (
        CAPTURE_A_RUN_AIMED_AT_IT,
        NEEDS_A_COMPONENT_BUILT,
    )

    gaps = [spec for spec in phase3_criteria() if spec.status is CriterionStatus.GAP]
    needing_a_build = [spec.number for spec in gaps if NEEDS_A_COMPONENT_BUILT in spec.gaps]
    needing_a_run = [spec.number for spec in gaps if CAPTURE_A_RUN_AIMED_AT_IT in spec.gaps]
    stating_their_own_reason = [
        spec.number
        for spec in gaps
        if NEEDS_A_COMPONENT_BUILT not in spec.gaps
        and CAPTURE_A_RUN_AIMED_AT_IT not in spec.gaps
    ]

    assert needing_a_build == list(NEEDING_A_COMPONENT_BUILT)
    assert needing_a_run == list(NEEDING_A_RUN_AIMED_AT_THEM)
    assert stating_their_own_reason == list(NEEDING_A_DIFFERENT_SHAPE_OF_CAPTURE)
    # No gap carries both, which would say a component has to be built and that nothing
    # needs building.
    assert set(needing_a_build).isdisjoint(needing_a_run)


def test_a_covered_criterion_never_rests_on_evidence_that_is_not_committed() -> None:
    """A criterion may be covered on a capture only while that capture is in the tree.

    The line this definition walks. A criterion may be covered on committed artifacts -- a
    CloudFormation template this repository commits, a decision recorded where it is
    enforced -- and may not be covered on anything an account would have to answer for.

    Mutation: delete a capture a covered criterion rests on and leave the criterion
    covered. That is the general case and ``evidence_not_in_the_tree`` is what catches it.

    **This check used to say the opposite and the deploy inverted it.** While Wave 5 was
    held, no covered criterion was allowed to rest on anything under
    ``fixtures/evidence/phase-3/``, because the only capture committed there had been taken
    before the stack it would describe existed -- so resting on it meant reporting a deploy
    that had not happened. Eleven criteria now rest on captures of four completed runs, and
    the rule that would have caught yesterday's mistake would today forbid the evidence
    from being used at all. What replaces it is the same idea pointed the right way: the
    captures those criteria rest on have to be in the tree, and they have to include the
    run captures rather than only the account measurements taken before the deploy.

    Expressed against the tree rather than against module names. The version before that
    refused node ids containing ``phase3_evidence``, ``phase3_capture`` or ``run_evidence``;
    no test module was named for the first two and none was cited matching the third, so
    all three held over an empty loop body. Nothing below reads the name of a module.
    """
    covered = [spec for spec in phase3_criteria() if spec.status is CriterionStatus.COVERED]
    assert covered

    assert evidence_not_in_the_tree(covered) == ()

    # Not merely "some phase-3 fixture exists". The account measurements were committed
    # before any stack was applied, so a covered criterion resting only on those is in
    # exactly the state this check used to forbid. What has to be present is a capture of a
    # run, which cannot exist unless a container ran.
    resting_on = frozenset().union(*fixtures_backing(covered, CriterionStatus.COVERED).values())
    assert [path for path in sorted(resting_on) if "phase-3" in path.parts], (
        "eleven criteria are covered on live captures and none is in the tree"
    )
    captured_runs = sorted(
        child.name
        for child in (PROJECT_ROOT / PHASE3_CAPTURE_DIR / RUNS_SUBDIR).glob("run_*")
        if child.is_dir()
    )
    assert captured_runs, (
        "the only committed Phase 3 captures are the account measurements taken before the "
        "deploy, and no covered criterion may rest on those alone"
    )


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
    a dependency this phase does not have and would send a reader looking for a borrowing
    arrangement nobody made.

    The second mutation, and the one available now that the environment exists: cover this
    on the template that asks for the networking rather than on the environment that has
    it. A stack applied from a laptop can land somewhere other than where its template
    says, and a record copied from the template would agree with itself forever -- so the
    scope limits have to say the ids were read back from the account.
    """
    (networking,) = [spec for spec in phase3_criteria() if spec.number == "21"]
    written = " ".join(networking.scope_limits)

    assert networking.status is CriterionStatus.COVERED
    assert networking.gaps == ()
    assert "L-F678F1CE" in written
    assert "our own VPC" in written
    assert "read back from the account" in written


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
