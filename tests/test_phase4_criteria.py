"""What the Phase 4 criteria definition must be true of, independent of the phase itself.

Two kinds of test. The first kind reads the definition against the master plan: the right
number of checks, the right ones marked pilot-blocking, every criterion that is not covered
saying what would close it. The second kind is the one that has caught real defects in every
phase so far -- asking pytest whether the cited node ids collect. A citation that reads
correctly and names nothing turns a criterion into a claim nobody checks, and no amount of
reading the definition reveals it.

**Both of this phase's gaps were disposed of on 2026-07-31, in two different ways, and the
tests that guarded against exactly those moves were rewritten rather than deleted.** They
were right about the danger -- a gate goes green the moment somebody relabels the checks it
is red on -- and being right about the danger is why they now assert the shape of each move
instead of asserting that no such move has happened.

Criterion 9 was capacity failure. It left the phase for Phase 8, which is where the
queue-wait detector it waits on is built, and its number was not reused. So the numbering
case pins the hole rather than the range, on the same reasoning Phase 3 pins the hole where
its three cancellation criteria used to be: a criterion number is an identifier, and closing
the gap up would silently rewrite every citation written against the old list.

Criterion 11 became a deferral. It wants a job placed on an alternate instance type, and the
GPU compute environment lists exactly one -- deliberately, because that list is what stops a
submission for one A10G being placed on four. The case below therefore holds it to naming
the cost control and to naming the event that makes it live again, because a deferral whose
reason is "not yet" is the relabelling the three-status rule exists to make visible.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from edullm_platform import phase4_criteria as criteria_module
from edullm_platform.criteria import CriterionStatus, cited_node_ids
from edullm_platform.phase4_capture import CAPTURE_ROOT
from edullm_platform.phase4_criteria import (
    PHASE4_CRITERION_COUNT,
    phase4_criteria,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Eight of the plan's eleven are marked, and the twelfth this phase added is marked too, so
#: nine of eleven. It was ten of twelve until criterion 9 left for Phase 8. That criterion
#: was marked, so the pilot set lost a member with it -- as a consequence of the transfer
#: rather than because anybody's judgement about harm changed.
PILOT_BLOCKING = ("1", "2", "3", "4", "5", "6", "7", "8", "12")

#: The number no criterion carries. Capacity failure moved to Phase 8, which is where the
#: queue-wait detector it cannot be closed without is built, and the number was not reused,
#: so a citation written against the old list still names what it named. Recorded here so
#: that reinstating it is a change to this file too.
MOVED_TO_A_LATER_PHASE = ("9",)

#: The two this phase does not cover, both recorded decisions carrying a trigger. Ten is the
#: queue-wait detector nobody has built; eleven is the single-item instance list that is
#: itself the control on what a job can be placed onto.
DEFERRED = ("10", "11")

#: The module that reads the committed captures. Every criterion about the account rather
#: than about pure Python has to cite it, or it is proving something about a fixture.
RUN_EVIDENCE = "tests/test_phase4_run_evidence.py"


def test_the_definition_lists_every_check_the_phase_plan_names() -> None:
    """Mutation: drop a criterion, or renumber the list to close the hole 9 left behind.

    The count is the plan's eleven plus the prefix-agreement check this phase added when it
    inherited three answers to where a run writes its output, less the one that moved to
    Phase 8. A definition short by one reports a phase closer to done than it is, and
    nothing else notices.

    Closing the hole up would read as a tidy-up and would be a silent rewrite of every
    citation. A criterion number is an identifier -- plan documents and decisions already
    written down name Phase 4 criteria by number -- so moving 10 down to 9 changes what
    "criterion 10" means in text nobody is going to re-read. The shared contract requires
    the numbers to be unique and says nothing about them being contiguous, which is what
    permits the hole; this is what keeps it open.
    """
    specs = phase4_criteria()
    expected = [str(n) for n in range(1, 13) if str(n) not in MOVED_TO_A_LATER_PHASE]

    assert len(specs) == PHASE4_CRITERION_COUNT == 11
    assert [spec.number for spec in specs] == expected
    assert all(spec.statement.strip() for spec in specs)


def test_the_transferred_criterion_left_the_phase_rather_than_being_softened() -> None:
    """Mutation: keep criterion 9 and reword it into something this phase can close.

    That is the move this case exists to refuse, and it is the cheaper of the two available:
    "capacity failure is surfaced" becomes "the intent record survives a capacity failure",
    which is already true, and a check the phase was red on turns green without anything
    changing in the account. The transfer keeps the sentence and moves it whole.

    What makes the removal legible rather than silent is the module saying where the check
    went. A criterion that vanishes with no destination is a check nobody owns, which is the
    outcome a transfer is supposed to prevent rather than produce -- so the docstring has to
    name both the number and the phase now carrying it.
    """
    numbers = {spec.number for spec in phase4_criteria()}
    written = criteria_module.__doc__ or ""

    assert numbers.isdisjoint(MOVED_TO_A_LATER_PHASE)
    assert "Phase 8" in written, "nothing says which phase carries the transferred check"
    for number in MOVED_TO_A_LATER_PHASE:
        assert f"Criterion {number}" in written or f"criterion {number}" in written


def test_the_pilot_markers_are_the_plans_and_not_a_later_judgement() -> None:
    """Mutation: mark criterion 10 or 11 pilot-blocking, or unmark one of the nine.

    Marking is not conservatism. Every marked criterion is one the pilot rung waits on, so
    marking a criterion that costs a pilot user nothing makes the rung unreachable rather
    than safer -- and unmarking one that costs them money is the failure the marker exists
    to prevent.

    The set lost criterion 9 when criterion 9 left the phase, and that is the only reason it
    moved. A marker coming off a criterion still in the list would be a judgement about harm
    and would fail here.
    """
    marked = tuple(spec.number for spec in phase4_criteria() if spec.pilot_blocking)

    assert marked == PILOT_BLOCKING


def test_the_two_unmarked_criteria_are_the_two_about_waiting_rather_than_harm() -> None:
    """Mutation: explain neither.

    Saying no is what makes the marker mean anything. Both unmarked criteria are about a
    job that waits, and a job that waits bills nothing -- which is a statement about cost
    that has to be written down, because "not marked" on its own reads like an oversight.

    Both are now deferrals, so the contract refuses the marker on them outright and the
    other half of the old mutation cannot happen. What is left for this case is the half no
    contract can check: that the cost argument is written down where a reader meets the
    criterion, rather than having been made once in somebody's head.
    """
    unmarked = {
        spec.number: " ".join((*spec.scope_limits, *spec.gaps, spec.deferral_reason or ""))
        for spec in phase4_criteria()
        if not spec.pilot_blocking
    }

    assert set(unmarked) == set(DEFERRED)
    assert all("bills nothing" in written or "costs nothing" in written
               for written in unmarked.values())


def test_every_criterion_that_is_not_covered_says_what_would_close_it() -> None:
    """Mutation: record a deferral with an explanation that restates the criterion.

    Anything not covered is work somebody has to pick up, and one that does not say what
    would close it is a criterion nobody can. Written against both open statuses rather than
    against gaps alone, because this phase has no gap left and a case keyed on gaps would
    have quietly stopped measuring anything on the day the last one was disposed of.
    """
    unproved = {
        spec.number: (*spec.gaps, *(text for text in (spec.deferral_trigger,) if text))
        for spec in phase4_criteria()
        if spec.status is not CriterionStatus.COVERED
    }

    assert set(unproved) == set(DEFERRED)
    for number, written in unproved.items():
        assert written, number
        assert all(len(entry) > 80 for entry in written), (
            f"criterion {number} says what closes it too briefly to be acted on"
        )


def test_both_deferrals_carry_a_trigger_that_can_actually_fire() -> None:
    """Mutation: defer something with a reason and no trigger.

    The contract already refuses that at construction, so this checks the softer failure it
    cannot see: a trigger written as a condition nobody will ever observe. Ten fires on a
    run somebody notices waiting, or on a second team. Eleven fires on a decision somebody
    takes or on contention somebody feels. All four are events that happen rather than
    states somebody has to go and measure.
    """
    deferred = [spec for spec in phase4_criteria() if spec.status is CriterionStatus.DEFERRED]

    assert [spec.number for spec in deferred] == list(DEFERRED)
    for spec in deferred:
        assert (spec.deferral_trigger or "").strip(), spec.number
        assert not spec.proving_node_ids, spec.number

    queue_wait, instance_shape = deferred
    assert "second team" in (queue_wait.deferral_trigger or "")
    assert "no CloudWatch metric" in (queue_wait.deferral_reason or "")
    assert "single-GPU" in (instance_shape.deferral_trigger or "")
    assert "contention" in (instance_shape.deferral_trigger or "")


def test_the_instance_shape_deferral_reads_as_a_cost_control_rather_than_an_omission() -> None:
    """Mutation: defer criterion 11 with a reason that stops at "one instance type is listed".

    That sentence is true and it is the relabelling this case exists to refuse: it describes
    unfinished configuration, which is a gap, and a deferral is a decision. What makes this
    one a decision is that the single-item list is itself the control -- widening it is the
    one line that lets a job which asked for one A10G at $1.006/hr be placed on four at
    $5.672 -- so the reason has to say the narrowness is deliberate and has to name what it
    is buying.

    The prices are asserted because they are the argument. A reason saying "cost" without
    them is one a reader cannot weigh, and weighing it is exactly what the trigger asks
    somebody to do.
    """
    eleven = next(spec for spec in phase4_criteria() if spec.number == "11")
    reason = eleven.deferral_reason or ""

    assert eleven.status is CriterionStatus.DEFERRED
    assert "deliberate" in reason.lower(), (
        "the deferral does not say the single-item list is a choice, so it reads as an "
        "omission that nobody got round to"
    )
    assert "1.006" in reason and "5.672" in reason, (
        "the deferral argues from cost and does not say what the cost is"
    )
    assert eleven.scope_limits, (
        "the gap prose said things a reader still needs and it was dropped rather than kept"
    )


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
