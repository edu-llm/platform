"""What the Phase 5 criteria definition must be true of, independent of the phase itself.

Two kinds of test. The first kind reads the definition against the master plan: the right
number of checks, the right ones marked pilot-blocking, every gap saying what would close
it, and the one rewritten criterion carrying the sentence it replaced. The second kind is
the one that has caught real defects in every phase so far -- asking pytest whether the
cited node ids collect. A citation that reads correctly and names nothing turns a criterion
into a claim nobody checks, and no amount of reading the definition reveals it.
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

#: Eleven of fifteen, which is the highest proportion of any phase. Criteria 1 to 8 are the
#: run itself and the person taking it; 12, 14 and 15 are the derivation checks whose
#: absence costs a lineage record. The four that are not marked are 9, 10, 11 and 13.
PILOT_BLOCKING = ("1", "2", "3", "4", "5", "6", "7", "8", "12", "14", "15")

#: The one that is open. It wants a GPU run under a team other than platform writing a
#: checkpoint, and every pilot run so far went to the CPU profile.
OPEN = ("6",)

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
    """Mutation: unmark criterion 6, or mark one of the access-grant conditions.

    Unmarking 6 is the tempting one, because it is the only thing standing between this
    phase and a ready rung -- and it is exactly the move the marker exists to prevent. A
    marked criterion is one the rung waits on, so unmarking one because it is inconvenient
    converts the control into a description of whatever already passed.
    """
    marked = tuple(spec.number for spec in phase5_criteria() if spec.pilot_blocking)

    assert marked == PILOT_BLOCKING


def test_the_four_unmarked_criteria_are_the_three_conditions_and_the_one_refusal() -> None:
    """Mutation: mark any of them, or explain none of them.

    Saying no is what makes the marker mean anything. Three of the four are conditions on
    granting write access rather than guards on a run -- nobody's run loses money, data,
    attribution or lineage integrity if they are absent, and what is at risk is the account.
    The fourth is a refusal whose value is its position and its wording.
    """
    unmarked = {
        spec.number: " ".join((*spec.scope_limits, *spec.gaps))
        for spec in phase5_criteria()
        if not spec.pilot_blocking
    }

    assert set(unmarked) == {"9", "10", "11", "13"}
    for number, written in unmarked.items():
        assert written, f"criterion {number} is unmarked and says nothing about why"


def test_every_open_criterion_says_what_would_close_it() -> None:
    """Mutation: record a gap with no explanation, or with one that restates the criterion.

    A gap that does not say what would close it is a criterion nobody can pick up. This one
    is unusual in being closeable by a submission rather than by work, and saying so is the
    difference between a reader scheduling an afternoon and a reader scheduling a sprint.
    """
    gaps = {
        spec.number: spec.gaps
        for spec in phase5_criteria()
        if spec.status is CriterionStatus.GAP
    }

    assert set(gaps) == set(OPEN)
    for number, written in gaps.items():
        assert written, number
        assert all(len(entry) > 80 for entry in written), (
            f"criterion {number} records a gap too short to say what would close it"
        )


def test_the_phase_records_no_deferral_and_therefore_hides_nothing_behind_one() -> None:
    """Mutation: defer criterion 6 instead of recording it as a gap.

    A deferral passes the gate. Criterion 6 is one submission away from closing, so
    deferring it would turn the gate green without anything changing in the account -- and
    a deferral may never be pilot-blocking, so it would also open the rung. That is two
    controls disabled by one word, which is precisely why the three-status rule exists.
    """
    deferred = [
        spec.number
        for spec in phase5_criteria()
        if spec.status is CriterionStatus.DEFERRED
    ]

    assert deferred == []


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
