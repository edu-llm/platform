"""What the Phase 5 criteria definition must be true of, independent of the phase itself.

Two kinds of test. The first kind reads the definition against the master plan: the right
number of checks, the right ones marked pilot-blocking, everything not covered saying what
would close it, and the one rewritten criterion carrying the sentence it replaced. The
second kind is the one that has caught real defects in every phase so far -- asking pytest
whether the cited node ids collect. A citation that reads correctly and names nothing turns
a criterion into a claim nobody checks, and no amount of reading the definition reveals it.

**Criterion 6 became a deferral, stopped being one, and became one again, all on 2026-07-31,
and these tests were rewritten with it each time.** The deferral is granted on an exchange: a
deferral may never be pilot-blocking, so the harm criterion 6's marker had been carrying has
to go somewhere a reader can act on it. The first grant put it on the pilot limitations page.
That page was then taken out of the README and moved to a local, gitignored document, the
exchange lapsed, and the deferral was withdrawn rather than left to expire quietly. It was
re-granted against a warning printed on the run summary to the submissions it applies to.

**So the tests below assert the exchange rather than the status, which is the lesson from
the round trip.** A deferral passes the gate and unmarks a criterion, so relabelling a gap is
two controls disabled by one word; what changed underneath that guard twice in a day was not
the status but whether the compensating warning existed and where. A test keyed on the status
would have been green through all three states and measured none of them, so
``test_the_one_deferral_is_paid_for_by_a_warning_a_test_holds_in_place`` asserts that every
deferral here names the test holding its payment in place -- a page can be moved by a decision
that never mentions the criterion, and a cited node id cannot.
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

#: Ten of fifteen, which is still the highest proportion of any phase. Criteria 1 to 5, 7 and
#: 8 are the run itself and the person taking it; 12, 14 and 15 are the derivation checks
#: whose absence costs a lineage record. Criterion 6 is not among them and was twice: its
#: marker comes off whenever it is deferred, because the contract refuses a criterion that is
#: both deferred and pilot-blocking, and it went back on for the hours the deferral was
#: withdrawn on 2026-07-31. It is off again because the deferral was re-granted against a
#: warning printed on the run summary rather than against a page.
PILOT_BLOCKING = ("1", "2", "3", "4", "5", "7", "8", "12", "14", "15")

#: The one criterion this phase does not cover. Criterion 6 wants a GPU run under a team
#: other than platform writing a checkpoint; every pilot run went to the CPU profile. It is
#: deferred rather than a gap, which is a status that has to be paid for --
#: ``test_the_one_deferral_is_paid_for_by_a_warning_a_test_holds_in_place`` is the check that
#: it was.
OPEN = ("6",)

#: The module holding the warnings a submitter reads, and the payment for any deferral here.
LIMITATIONS = "tests/test_pilot_limitations.py"

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
    converts the control into a description of whatever already passed. Criterion 6 is worth
    watching here specifically: its marker came off in exchange for a written limitation, and
    the exchange is the only legitimate way a marker comes off. The test that stops the status
    being changed for convenience is
    ``test_the_one_deferral_is_paid_for_by_a_warning_a_test_holds_in_place``.
    """
    marked = tuple(spec.number for spec in phase5_criteria() if spec.pilot_blocking)

    assert marked == PILOT_BLOCKING


def test_every_unmarked_criterion_says_why_it_is_not_pilot_blocking() -> None:
    """Mutation: mark any of them, or explain none of them.

    Saying no is what makes the marker mean anything. Two of the five are conditions on
    granting write access rather than guards on a run -- nobody's run loses money, data,
    attribution or lineage integrity if they are absent, and what is at risk is the account.
    One is a refusal whose value is its position and its wording, and one is the warning text
    a submitter reads.

    Criterion 6 is the interesting one and the reason this test reads deferral fields as well
    as gaps. It is unmarked because it is deferred, and the contract refuses a criterion that
    is both; the harm it stopped carrying did not disappear, it moved onto a warning printed
    to the runs it applies to. So it has to say so here, and it does.
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
    against whichever is in use: criterion 6 was a gap, a deferral, a gap and a deferral again
    inside 2026-07-31, and a check keyed on one status would have stopped measuring at each
    switch without going red.
    """
    unproved = {
        spec.number: (*spec.gaps, *(text for text in (spec.deferral_trigger,) if text))
        for spec in phase5_criteria()
        if spec.status is not CriterionStatus.COVERED
    }

    assert set(unproved) == set(OPEN)
    for number, written in unproved.items():
        assert written, number
        assert all(len(entry) > 80 for entry in written), (
            f"criterion {number} says what closes it too briefly to be actionable"
        )


def test_the_one_deferral_is_paid_for_by_a_warning_a_test_holds_in_place() -> None:
    """Mutation: defer a criterion without buying the marker off, or buy it with prose.

    **A deferral passes the gate and may never be pilot-blocking, so relabelling a gap is two
    controls disabled by one word.** That makes the price the whole mechanism. What a deferral
    owes is the harm its marker was carrying, written where a reader can act on it, and this
    is the check that the debt was paid rather than described.

    Criterion 6 has now been through the cycle twice in one day, which is the best evidence
    available that the price is real. It was deferred on 2026-07-31 against a sentence on the
    pilot limitations page; the page then left the README on an unrelated decision about what
    this repository publishes; the condition lapsed and the deferral was withdrawn. It was
    re-granted the same day against a warning printed on the run summary to the submissions it
    applies to.

    **So the assertion is against a cited test rather than against wording, and that is the
    lesson from the first attempt.** A page can be moved by a decision that never mentions the
    criterion, and nothing goes red. A warning held in place by a cited node id cannot go quiet
    without the gate executing that node id and failing. Every deferral here must name the test
    that holds its payment, which is a rule about mechanism rather than about this criterion.
    """
    specs = phase5_criteria()
    deferred = [spec for spec in specs if spec.status is CriterionStatus.DEFERRED]
    gaps = {spec.number: spec for spec in specs if spec.status is CriterionStatus.GAP}

    assert not gaps, (
        f"{sorted(gaps)} are recorded as gaps. Either close them or record the decision that "
        "defers them, with what buys the marker off."
    )
    assert [spec.number for spec in deferred] == list(OPEN)

    for spec in deferred:
        written = f"{spec.deferral_reason or ''} {spec.deferral_trigger or ''}"
        assert spec.deferral_trigger, f"criterion {spec.number} defers with no trigger"
        # The payment, named as a test rather than described. Anything else is a promise.
        paying = [node for node in spec.supporting_node_ids if LIMITATIONS in node]
        assert paying, (
            f"criterion {spec.number} is deferred and cites no test from {LIMITATIONS}, so "
            "nothing holds the warning that bought its pilot-blocking marker off. A deferral "
            "paid for in prose is a gap with better manners."
        )
        assert any(node.split("::")[-1] in written for node in paying), (
            f"criterion {spec.number} cites a paying test its own reason never mentions, so a "
            "reader of the verdict cannot tell which check is holding the warning up."
        )

    six = next(spec for spec in deferred if spec.number == "6")
    reason = six.deferral_reason or ""
    assert "5.5" in reason, (
        "the reason does not say the grant that makes the checkpoint write permitted"
    )
    assert "GPU submission" in six.deferral_trigger, (
        "the trigger reads as work rather than as the one run that closes it"
    )
    assert not six.pilot_blocking, (
        "criterion 6 is deferred and marked pilot-blocking, which the contract refuses"
    )


def test_the_open_criteria_do_not_let_the_phase_read_as_a_workload_that_ran() -> None:
    """Mutation: record criterion 6 with text that stops at "no GPU run happened".

    Any verdict on this phase will be read as the platform having carried a second person's
    research run, and it has not. All three pilot runs went to the CPU profile carrying a
    print statement, so what Phase 5 established is that the two-person path completes --
    which had never been established -- and not that anything was trained. The gate prints
    scope limits where a reader of the verdict will see them, which is the only place that
    sentence survives being skim-read.
    """
    six = next(spec for spec in phase5_criteria() if spec.number == "6")
    written = " ".join(six.scope_limits)

    assert "print statement" in written
    assert "cpu-32vcpu" in written
    assert "not a pass" in written.lower(), (
        "nothing in the record tells a reader that closing this criterion is not a pass on "
        "the larger question"
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
