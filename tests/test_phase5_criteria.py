"""What the Phase 5 criteria definition must be true of, independent of the phase itself.

Two kinds of test. The first kind reads the definition against the master plan: the right
number of checks, the right ones marked pilot-blocking, everything not covered saying what
would close it, and the one rewritten criterion carrying the sentence it replaced. The
second kind is the one that has caught real defects in every phase so far -- asking pytest
whether the cited node ids collect. A citation that reads correctly and names nothing turns
a criterion into a claim nobody checks, and no amount of reading the definition reveals it.

**Criterion 6 moved from a gap to a deferral on 2026-07-31, and the tests that guarded
against exactly that move were rewritten rather than deleted.** The guard was right about
the danger and wrong about this instance: a deferral passes the gate and may never be
pilot-blocking, so relabelling a gap is two controls disabled by one word, and that is worth
a test whichever way it lands. What makes this one a postponement rather than a relabelling
is that the observation is owned somewhere real -- the check moved to Phase 6's closeout,
where it carries Phase 5's gate rather than Phase 6's -- and no Phase 6 build item stands in
front of it. So the tests below assert the shape of a relocation instead of asserting that
no deferral exists: that there is exactly one, that it names what fires it, and that it does
not let the phase read as though a research workload had been run.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from edullm_platform.criteria import CriterionStatus, cited_node_ids
from edullm_platform.phase5_criteria import (
    PHASE5_CRITERION_COUNT,
    phase5_criteria,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Ten of fifteen, which is still the highest proportion of any phase. Criteria 1 to 5, 7
#: and 8 are the run itself and the person taking it; 12, 14 and 15 are the derivation
#: checks whose absence costs a lineage record. Criterion 6 was the eleventh until it was
#: deferred; the contract refuses a deferred criterion that is also pilot-blocking, so the
#: marker came off as a consequence of the deferral rather than as a judgement about harm.
PILOT_BLOCKING = ("1", "2", "3", "4", "5", "7", "8", "12", "14", "15")

#: The one criterion this phase does not cover. It wants a GPU run under a team other than
#: platform writing a checkpoint; every pilot run went to the CPU profile, and the
#: observation moved to Phase 6's closeout rather than being abandoned here.
DEFERRED = ("6",)

#: The module that reads the committed captures of the pilot runs. Every criterion that is a
#: claim about people rather than about pure Python has to cite it, or it is proving
#: something about a fixture.
RUN_EVIDENCE = "tests/test_phase5_run_evidence.py"


def test_the_definition_lists_every_check_the_phase_plan_names() -> None:
    """Mutation: drop a criterion, or renumber after retiring one.

    Fifteen is the migration document's eleven checks followed by the four that deriving the
    image from its commit owes over merely comparing the two. The derivation four are
    appended rather than interleaved so that nothing already argued about was renumbered --
    and criterion 14 keeps its number after being rewritten, for the same reason. A
    definition short by one reports a phase closer to done than it is, and nothing else
    notices.
    """
    specs = phase5_criteria()

    assert len(specs) == PHASE5_CRITERION_COUNT
    assert [spec.number for spec in specs] == [str(n) for n in range(1, 16)]


def test_the_pilot_markers_are_the_plans_and_not_a_later_judgement() -> None:
    """Mutation: unmark one of the run criteria, or mark an access-grant condition.

    A marked criterion is one the rung waits on, so unmarking one because it is inconvenient
    converts the control into a description of whatever already passed. Criterion 6 is the
    exception and it is not a loophole: the shared contract refuses a criterion that is both
    deferred and pilot-blocking, so its marker cannot be reasoned about separately from its
    status. The test that stops the status itself being changed for convenience is
    ``test_the_one_deferral_is_a_relocation_and_names_what_fires_it``.
    """
    marked = tuple(spec.number for spec in phase5_criteria() if spec.pilot_blocking)

    assert marked == PILOT_BLOCKING


def test_every_unmarked_criterion_says_why_it_is_not_pilot_blocking() -> None:
    """Mutation: mark any of them, or explain none of them.

    Saying no is what makes the marker mean anything. Three of the five are conditions on
    granting write access rather than guards on a run -- nobody's run loses money, data,
    attribution or lineage integrity if they are absent, and what is at risk is the account.
    One is a refusal whose value is its position and its wording. The fifth is the deferral,
    which is unmarked by the contract rather than by a judgement, and which therefore owes
    the loudest explanation of the five.
    """
    unmarked = {
        spec.number: " ".join(
            (
                *spec.scope_limits,
                *spec.gaps,
                spec.deferral_reason or "",
                spec.deferral_trigger or "",
            )
        )
        for spec in phase5_criteria()
        if not spec.pilot_blocking
    }

    assert set(unmarked) == {"6", "9", "10", "11", "13"}
    for number, written in unmarked.items():
        assert written.strip(), f"criterion {number} is unmarked and says nothing about why"


def test_every_criterion_that_is_not_covered_says_what_would_close_it() -> None:
    """Mutation: record a gap or a deferral with an explanation that restates the criterion.

    Anything not covered is work somebody has to pick up, and one that does not say what
    would close it is a criterion nobody can. Written against both open statuses rather than
    against gaps alone, because the phase has no gap left and a check keyed on gaps would
    have quietly stopped measuring anything on the day the last one was deferred.
    """
    unproved = {
        spec.number: (*spec.gaps, *(text for text in (spec.deferral_trigger,) if text))
        for spec in phase5_criteria()
        if spec.status is not CriterionStatus.COVERED
    }

    assert set(unproved) == set(DEFERRED)
    for number, written in unproved.items():
        assert written, number
        assert all(len(entry) > 80 for entry in written), (
            f"criterion {number} says what closes it too briefly to be actionable"
        )


def test_the_one_deferral_is_a_relocation_and_names_what_fires_it() -> None:
    """Mutation: defer a second criterion, or defer this one without saying where it went.

    A deferral passes the gate and may never be pilot-blocking, so relabelling a gap is two
    controls disabled by one word. What separates this from that move is that the
    observation is owned somewhere real rather than owned nowhere: it moved to Phase 6's
    closeout, carrying Phase 5's gate, with no Phase 6 build item in front of it. Both
    halves are asserted -- the reason has to say the mechanism is built and unexercised, and
    the trigger has to be the one submission that resolves it -- because a deferral whose
    reason is "not yet" is the label this test exists to refuse.
    """
    deferred = [
        spec for spec in phase5_criteria() if spec.status is CriterionStatus.DEFERRED
    ]

    assert [spec.number for spec in deferred] == list(DEFERRED)
    six = deferred[0]
    reason = six.deferral_reason or ""
    trigger = six.deferral_trigger or ""

    assert "Phase 6" in reason, "the deferral does not say where the observation went"
    assert "5.5" in reason, (
        "the deferral does not say the grant that makes the checkpoint write permitted"
    )
    assert "unexercised" in reason.lower(), (
        "the deferral does not say the mechanism is built, which is what makes it a "
        "postponement rather than unfinished work"
    )
    assert "GPU" in trigger and "checkpoint" in trigger
    assert "submission" in trigger, (
        "the trigger reads as work rather than as the one run that closes it"
    )
    assert not six.proving_node_ids, "a deferred criterion is not proved"
    assert six.supporting_node_ids, (
        "the deferral cites nothing, so a reader cannot see which half is already built"
    )


def test_the_deferral_does_not_let_the_phase_read_as_a_workload_that_ran() -> None:
    """Mutation: defer criterion 6 with a reason that stops at "no GPU run happened".

    A green gate on this phase will be read as the platform having carried a second person's
    research run, and it has not. All three pilot runs went to the CPU profile carrying a
    print statement, so what Phase 5 established is that the two-person path completes --
    which had never been established -- and not that anything was trained. The gate prints a
    deferral's scope limits where a reader of the verdict will see them, which is the only
    place that sentence survives being skim-read.
    """
    six = next(spec for spec in phase5_criteria() if spec.number == "6")
    written = " ".join(six.scope_limits)

    assert "print statement" in written
    assert "cpu-32vcpu" in written
    assert "not a pass" in written.lower(), (
        "nothing in the record tells a reader the re-cut is a relocation rather than a pass"
    )


def test_the_rewritten_criterion_carries_the_sentence_it_replaced() -> None:
    """Mutation: rewrite criterion 14's statement and delete the old one.

    A criterion whose statement quietly changes is a moved goalpost, and the only thing that
    makes a rewrite honest rather than a retreat is that a reader can see both sentences and
    judge the swap. Phase 4 criterion 7 and Phase 2 criterion 8 set the precedent; this
    follows it. The replacement has to be stronger than the original, and the record has to
    say why the original became unreachable rather than merely untested.
    """
    fourteen = next(spec for spec in phase5_criteria() if spec.number == "14")
    written = " ".join(fourteen.scope_limits)

    assert "at most one image" in fourteen.statement
    assert "built more than once" in written, (
        "the retired sentence is not recorded, so the rewrite reads as the original"
    )
    assert "IMMUTABLE" in written
    assert "pre-flight" in written
    assert "unreachable" in written or "cannot occur" in written


def test_the_criterion_about_code_owner_review_says_it_is_about_a_member() -> None:
    """Mutation: state the master plan's unqualified sentence.

    The plan asks that a change to a workflow file cannot reach main without a code-owner
    review, and that is false for the three admins because ``enforce_admins`` is off by
    decision. A gate asserting the unqualified sentence would be asserting something untrue
    about this account, which is worse than a narrower claim that holds.
    """
    ten = next(spec for spec in phase5_criteria() if spec.number == "10")

    assert "member" in ten.statement
    assert "enforce_admins" in " ".join(ten.scope_limits)


def test_every_criterion_about_a_person_cites_the_module_that_reads_the_captures() -> None:
    """Mutation: cover a criterion about two people on a workflow-file test.

    A test that reads a committed workflow reads what the platform will do rather than what
    somebody did, and no amount of reading YAML establishes that a lead released a run they
    did not submit. These four are claims about events, so they have to rest on a record of
    one.
    """
    about_a_person = ("1", "2", "3", "4")

    for spec in phase5_criteria():
        if spec.number not in about_a_person:
            continue
        modules = {node_id.split("::", 1)[0] for node_id in spec.proving_node_ids}
        assert RUN_EVIDENCE in modules, (
            f"criterion {spec.number} is about something a person did and proves it without "
            "reading a capture of it"
        )


def test_every_cited_node_id_names_a_test_pytest_can_collect() -> None:
    """Mutation: rename a cited test and leave the citation.

    The defect this catches is silent in every other direction: the citation still reads as
    a sentence, the test still passes under its new name, and the criterion reports itself
    covered by a check that selects nothing. Every phase so far has had one.

    Collection only, so this stays cheap enough to run on every pull request and so that it
    is measuring the citation rather than re-running the suite that is already running.
    """
    cited = sorted(cited_node_ids(phase5_criteria()))

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-header", *cited],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, (
        "a criterion cites a node id pytest cannot collect:\n"
        f"{completed.stdout[-4000:]}\n{completed.stderr[-2000:]}"
    )
