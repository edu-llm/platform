"""The pilot rung: the flag, the verdict beside the gate's, and the shipped markers.

The master plan splits every phase's checks into the ones that must pass before anybody
outside the build team uses the capability and the ones that need only close before the
gate does. It marks the first kind with a bold prefix in prose, and prose cannot fail a
build. This module is the other end of that: it holds the rule the prefix states, the
verdict a gate reaches from it, and a table recording which of each phase's criteria the
plan marks -- so that the document and the code go out of step loudly rather than
silently.

Every case below names the mutation it exists to fail on, because a control that would
survive its own defect is the thing this repository keeps finding and repairing.

Nothing here starts a gate. The four command-line entry points are exercised with their
evaluation replaced, so what is measured is what they print and what they return, not a
second execution of a suite that is already running. That is why the entry points are
reached through a module name built from a phase number rather than spelled out: a module
that names one of them, or that calls a gate's own entry point, has to be listed in
``REENTRANT_TEST_MODULES``, and listing this one would say something about it that is not
true.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from edullm_platform.criteria import (
    CriteriaDefinitionError,
    CriterionResult,
    CriterionSpec,
    CriterionStatus,
    PilotReadiness,
    PilotVerdict,
    criterion_result,
    pilot_verdict,
)
from edullm_platform.criteria_runner import SelectionOutcome
from edullm_platform.phase1_criteria import phase1_criteria
from edullm_platform.phase1_gate import Phase1GateReport
from edullm_platform.phase2_criteria import phase2_criteria
from edullm_platform.phase2_gate import Phase2GateReport
from edullm_platform.phase3_criteria import phase3_criteria
from edullm_platform.phase3_gate import Phase3GateReport
from edullm_platform.phase4_criteria import phase4_criteria
from edullm_platform.phase4_gate import Phase4GateReport
from edullm_platform.phase5_criteria import phase5_criteria
from edullm_platform.phase5_gate import Phase5GateReport
from edullm_platform.status_prose import gate_and_pilot_line, status_claims, status_count_claims

A_REAL_NODE_ID = "tests/test_canonical.py::test_canonical_json_bytes_sorts_keys"


def result(number: str, *, passed: bool, pilot_blocking: bool) -> CriterionResult:
    return CriterionResult(
        number=number,
        statement=f"Criterion {number}.",
        status=CriterionStatus.COVERED if passed else CriterionStatus.GAP,
        passed=passed,
        pilot_blocking=pilot_blocking,
        reason_code="ok" if passed else "recorded_gap",
        detail="",
        cited_node_ids=(),
        missing_node_ids=(),
        failed_node_ids=(),
    )


def results(*flags: tuple[bool, bool]) -> tuple[CriterionResult, ...]:
    """One result per ``(passed, pilot_blocking)`` pair, numbered from one."""
    return tuple(
        result(str(index), passed=passed, pilot_blocking=blocking)
        for index, (passed, blocking) in enumerate(flags, start=1)
    )


# --------------------------------------------------------------------------------------
# A deferral can never block a pilot
# --------------------------------------------------------------------------------------


def test_a_deferred_criterion_marked_pilot_blocking_is_rejected_at_load() -> None:
    """Mutation: accept the combination, or check it in a reviewer's head.

    A deferral is a recorded decision that the criterion is intentionally false today.
    Requiring it before a pilot would make the rung unreachable rather than make it
    safe, so the combination is refused where a deferral without a reason and a trigger
    is already refused -- when the spec is constructed, before any gate reads it.
    """
    with pytest.raises(CriteriaDefinitionError, match="deferred and marked pilot-blocking"):
        CriterionSpec(
            number="1",
            statement="Wrong-team lead approval is refused.",
            status=CriterionStatus.DEFERRED,
            pilot_blocking=True,
            supporting_node_ids=(A_REAL_NODE_ID,),
            deferral_reason="team bindings are empty",
            deferral_trigger="team bindings are populated",
        )


def test_the_same_deferral_without_the_flag_is_accepted() -> None:
    # Anchors the case above, which would pass just as well if construction refused
    # every deferral, or every spec.
    accepted = CriterionSpec(
        number="1",
        statement="Wrong-team lead approval is refused.",
        status=CriterionStatus.DEFERRED,
        supporting_node_ids=(A_REAL_NODE_ID,),
        deferral_reason="team bindings are empty",
        deferral_trigger="team bindings are populated",
    )

    assert accepted.pilot_blocking is False


@pytest.mark.parametrize(
    ("status", "extra"),
    [
        (CriterionStatus.COVERED, {"proving_node_ids": (A_REAL_NODE_ID,)}),
        (CriterionStatus.GAP, {"gaps": ("Nothing proves it yet.",)}),
    ],
    ids=["covered", "gap"],
)
def test_the_other_two_statuses_may_be_pilot_blocking(
    status: CriterionStatus,
    extra: dict[str, object],
) -> None:
    # The refusal above is about deferrals and nothing else. A gap that blocks a pilot is
    # the ordinary case: it is exactly how a phase reports that the rung is closed.
    spec = CriterionSpec(
        number="1",
        statement="A criterion.",
        status=status,
        pilot_blocking=True,
        **extra,  # type: ignore[arg-type]
    )

    assert spec.pilot_blocking is True


def test_the_flag_reaches_the_result_the_gate_reports() -> None:
    # Mutation: compute the verdict from the specs and let the results drop the flag.
    # Every consumer reads results, so a flag that stops at the spec is a flag nothing
    # downstream can act on.
    spec = CriterionSpec(
        number="1",
        statement="A criterion.",
        status=CriterionStatus.COVERED,
        pilot_blocking=True,
        proving_node_ids=(A_REAL_NODE_ID,),
    )
    outcome = SelectionOutcome(
        requested=frozenset({A_REAL_NODE_ID}),
        collected=frozenset({A_REAL_NODE_ID}),
        passed=frozenset({A_REAL_NODE_ID}),
        exit_code=0,
    )

    assert criterion_result(spec, outcome).pilot_blocking is True


# --------------------------------------------------------------------------------------
# Flagging nothing is not the same as passing everything
# --------------------------------------------------------------------------------------


def test_a_phase_that_flags_nothing_reports_not_assessed_rather_than_ready() -> None:
    """Mutation: return ``all(...)`` over the pilot-blocking criteria and stop there.

    The conjunction of an empty set is true, so a phase that has marked nothing would
    report as pilot-ready on the strength of nothing at all. That is the empty-set defect
    this repository has now shipped twice, and it is the reason readiness is three values
    rather than a bool.
    """
    verdict = pilot_verdict(results((True, False), (True, False), (False, False)))

    assert verdict.readiness is PilotReadiness.NOT_ASSESSED
    assert verdict.readiness is not PilotReadiness.READY
    assert verdict.blocking_criteria == ()
    assert "not assessed" in verdict.note
    assert "control that cannot fail" in verdict.note


def test_a_phase_with_no_criteria_at_all_is_also_not_assessed() -> None:
    # The degenerate end of the same defect: nothing evaluated cannot be ready either.
    verdict = pilot_verdict(())

    assert verdict.readiness is PilotReadiness.NOT_ASSESSED
    assert verdict.evaluated_criteria == 0


def test_one_flagged_and_passing_criterion_is_enough_to_be_ready() -> None:
    # Anchors the two cases above, which a readiness that was never READY would satisfy.
    verdict = pilot_verdict(results((True, True), (False, False)))

    assert verdict.readiness is PilotReadiness.READY


# --------------------------------------------------------------------------------------
# The two verdicts see different criteria
# --------------------------------------------------------------------------------------


def test_the_pilot_verdict_ignores_a_criterion_that_is_not_pilot_blocking() -> None:
    """Mutation: drop the flag from the filter and AND everything.

    A failing criterion nobody marked is the ordinary state of a phase at the pilot rung.
    If it closed the rung, the flag would be decoration and the rung would be the gate
    under another name.
    """
    verdict = pilot_verdict(results((True, True), (False, False), (True, True)))

    assert verdict.readiness is PilotReadiness.READY
    assert verdict.blocking_criteria == ("1", "3")
    assert verdict.unmet_criteria == ()


def test_the_gate_verdict_still_includes_a_criterion_the_pilot_verdict_ignores() -> None:
    """Mutation: make the gate verdict the AND of the pilot-blocking criteria.

    This is the state Phase 3 is in and the reason the split exists at all: pilot-ready
    and gate-red at the same time. The gate keeps meaning that every criterion passed,
    and nothing about the pilot rung is allowed to soften it.
    """
    report = Phase1GateReport(
        phase_criteria=(
            *(result(str(n), passed=True, pilot_blocking=True) for n in range(1, 8)),
            result("8", passed=False, pilot_blocking=False),
        )
    )

    assert report.pilot.readiness is PilotReadiness.READY
    assert report.passed is False


def test_a_failing_flagged_criterion_closes_the_rung_and_is_named() -> None:
    verdict = pilot_verdict(results((False, True), (True, True), (False, False)))

    assert verdict.readiness is PilotReadiness.BLOCKED
    assert verdict.unmet_criteria == ("1",)
    # Not "2", which passed, and not "3", which nobody marked.
    assert verdict.blocking_criteria == ("1", "2")


def test_the_blocking_list_keeps_the_phase_order_rather_than_sorting_it() -> None:
    # Mutation: sort the numbers. They are strings, so sorting puts 10 before 2 and the
    # list stops reading as the phase's own order.
    verdict = pilot_verdict(
        tuple(result(str(n), passed=False, pilot_blocking=True) for n in (1, 2, 10, 11))
    )

    assert verdict.blocking_criteria == ("1", "2", "10", "11")


def test_a_verdict_cannot_name_an_unmet_criterion_it_did_not_flag() -> None:
    # Mutation: build the two lists from different filters. A verdict whose unmet list is
    # not drawn from its blocking list is one that closed the rung on a check it never
    # said was blocking, which is the mirror image of opening it on nothing.
    with pytest.raises(ValueError, match="without being pilot-blocking"):
        PilotVerdict(
            evaluated_criteria=2,
            blocking_criteria=("1",),
            unmet_criteria=("2",),
        )


def test_a_verdict_cannot_flag_more_criteria_than_the_run_evaluated() -> None:
    with pytest.raises(ValueError, match="out of"):
        PilotVerdict(
            evaluated_criteria=1,
            blocking_criteria=("1", "2"),
            unmet_criteria=(),
        )


# --------------------------------------------------------------------------------------
# What the verdict is allowed to say
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verdict",
    [
        pilot_verdict(results((True, False), (True, False))),
        pilot_verdict(results((True, True), (True, False))),
        pilot_verdict(results((False, True), (True, True))),
    ],
    ids=["not assessed", "ready", "blocked"],
)
def test_the_note_states_the_numbers_the_verdict_computed(verdict: PilotVerdict) -> None:
    # Mutation: hand-write the note. The Phase 1 gate shipped a hand-written sentence
    # that was true when written and was still being printed beside the verdict it
    # contradicted a fortnight later.
    if verdict.blocking_criteria:
        assert f"{len(verdict.blocking_criteria)} of the {verdict.evaluated_criteria}" in (
            verdict.note
        )
        assert "exit code" in verdict.note
    if verdict.unmet_criteria:
        assert f"{len(verdict.unmet_criteria)} of those did not pass" in verdict.note


@pytest.mark.parametrize(
    "verdict",
    [
        pilot_verdict(results((True, False), (True, False))),
        pilot_verdict(results((True, True), (True, False))),
        pilot_verdict(results((False, True), (True, True))),
    ],
    ids=["not assessed", "ready", "blocked"],
)
def test_the_pilot_note_makes_no_criterion_status_claim(verdict: PilotVerdict) -> None:
    """Mutation: write "gap" or "covered" into the pilot note.

    The pilot note is a statement about adoption and sits in the same document as a
    statement about status. The reader that refuses a gate note contradicting its own
    criteria would read a status word here as a claim about those criteria, and a
    sentence about the rung would start asserting something about the table.
    """
    assert status_claims(verdict.note) == ()
    assert status_count_claims(verdict.note) == ()


# --------------------------------------------------------------------------------------
# The four gates report both verdicts, and the exit code stays the gate's
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PhaseUnderTest:
    phase: int
    label: str
    criteria: Callable[[], tuple[CriterionSpec, ...]]
    report: Callable[..., object]


PHASES = (
    PhaseUnderTest(1, "Phase 1", phase1_criteria, Phase1GateReport),
    PhaseUnderTest(2, "Phase 2", phase2_criteria, Phase2GateReport),
    PhaseUnderTest(3, "Phase 3", phase3_criteria, Phase3GateReport),
    PhaseUnderTest(4, "Phase 4", phase4_criteria, Phase4GateReport),
    PhaseUnderTest(5, "Phase 5", phase5_criteria, Phase5GateReport),
)


def synthetic_report(subject: PhaseUnderTest, *, gate_passes: bool) -> object:
    """A report of the right size for one phase, with one criterion of each kind."""
    total = len(subject.criteria())
    records = [result(str(n), passed=True, pilot_blocking=True) for n in range(1, total + 1)]
    records[-1] = result(str(total), passed=gate_passes, pilot_blocking=False)
    return subject.report(phase_criteria=tuple(records))


@pytest.mark.parametrize("subject", PHASES, ids=lambda subject: subject.label)
@pytest.mark.parametrize("gate_passes", [True, False], ids=["gate green", "gate red"])
def test_the_command_prints_both_verdicts_and_exits_on_the_gate(
    subject: PhaseUnderTest,
    gate_passes: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: return on the pilot verdict, or print only one of the two.

    A pilot-ready phase with a red gate still exits 1, because the exit code has always
    meant that the phase is complete and a caller reading it for that has not been told
    the question changed. Both verdicts are printed so that a reader who sees exit 1
    beside an open rung is not left deciding which of them is wrong.
    """
    command = importlib.import_module(f"tools.validate_phase{subject.phase}")
    report = synthetic_report(subject, gate_passes=gate_passes)
    monkeypatch.setattr(command, "evaluate_repository", lambda _root: report)

    exit_code = command.main()
    printed = capsys.readouterr()

    assert exit_code == (0 if gate_passes else 1)
    payload = json.loads(printed.out)
    assert payload["passed"] is gate_passes
    assert payload["pilot"]["readiness"] == PilotReadiness.READY.value
    assert f"{subject.label} gate: {'pass' if gate_passes else 'fail'}" in printed.err
    assert f"{subject.label} pilot rung: ready" in printed.err


def test_the_printed_line_names_the_exit_code_it_is_not_derived_from() -> None:
    # Mutation: derive the printed exit code from the pilot verdict. The line exists to
    # tell a reader which of the two verdicts the number belongs to, so getting that
    # wrong is worse than not printing it.
    verdict = pilot_verdict(results((True, True),))

    line = gate_and_pilot_line(phase="Phase 3", gate_passed=False, verdict=verdict)

    assert "Phase 3 gate: fail, exit 1" in line
    assert "Phase 3 pilot rung: ready" in line


@pytest.mark.parametrize("subject", PHASES, ids=lambda subject: subject.label)
def test_the_report_carries_the_pilot_verdict_into_its_json(subject: PhaseUnderTest) -> None:
    report = synthetic_report(subject, gate_passes=True)
    payload = json.loads(report.model_dump_json())  # type: ignore[attr-defined]

    assert set(payload["pilot"]) == {
        "blocking_criteria",
        "evaluated_criteria",
        "note",
        "readiness",
        "unmet_criteria",
    }


@pytest.mark.parametrize("subject", PHASES, ids=lambda subject: subject.label)
def test_a_caller_cannot_supply_a_pilot_verdict_of_their_own(subject: PhaseUnderTest) -> None:
    # Derived rather than stored, for the reason the criteria note is: a field a caller
    # could set is a field that can disagree with the criteria printed beside it.
    total = len(subject.criteria())
    records = tuple(result(str(n), passed=True, pilot_blocking=False) for n in range(1, total + 1))

    with pytest.raises(ValidationError) as refused:
        subject.report(
            phase_criteria=records,
            pilot=PilotVerdict(
                evaluated_criteria=total,
                blocking_criteria=("1",),
                unmet_criteria=(),
            ),
        )

    assert [error["loc"] for error in refused.value.errors()] == [("pilot",)]
    assert [error["type"] for error in refused.value.errors()] == ["extra_forbidden"]
    # And what it does report is what the criteria say, which is that nothing is marked.
    assert subject.report(phase_criteria=records).pilot.readiness is (  # type: ignore[attr-defined]
        PilotReadiness.NOT_ASSESSED
    )


# --------------------------------------------------------------------------------------
# The shipped markers, against what the master plan records
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanRecord:
    """One phase, as the master plan's check list records it and as the module ships it.

    ``plan_checks`` and ``plan_pilot_checks`` are the plan's own numbers. ``from_the_plan``
    maps each of those checks onto the criterion that carries it -- the plan's checks are
    the first criteria of each module, in the plan's order -- and holds the marker the
    plan puts on it. ``judged_here`` holds every remaining criterion, which had no
    counterpart in the plan's list at all and whose flag was decided by the ladder's own
    test, with the argument recorded in that criterion's ``scope_limits``.

    ``moved_to_a_later_phase`` holds the plan checks whose criterion this module no longer
    carries, because the work they describe was reassigned to a phase that will build it.
    They are listed rather than subtracted from ``plan_checks``, for two reasons. The
    plan's list is still the size the plan records, and a table that quietly got shorter
    would make a check disappearing from a phase indistinguishable from a transcription
    error. And the numbers are not reused, so naming them here is what says the hole in
    the criterion list is intended.
    """

    label: str
    criteria: Callable[[], tuple[CriterionSpec, ...]]
    plan_checks: int
    plan_pilot_checks: int
    pilot_criteria: int
    from_the_plan: Mapping[str, bool]
    judged_here: Mapping[str, bool]
    moved_to_a_later_phase: tuple[str, ...] = ()

    @property
    def expected(self) -> dict[str, bool]:
        return {**self.from_the_plan, **self.judged_here}


def marked(*numbers: int) -> dict[str, bool]:
    return {str(number): True for number in numbers}


def unmarked(*numbers: int) -> dict[str, bool]:
    return {str(number): False for number in numbers}


#: Three phases, not the four above. Phase 4's markers are held to the plan's list in
#: ``tests/test_phase4_criteria.py``, which was written after this table and reads the
#: definition directly; transcribing them here too would be a second copy of the plan's
#: list, and a second copy is what this table exists to make unnecessary.
PLAN = (
    PlanRecord(
        label="Phase 1",
        criteria=phase1_criteria,
        plan_checks=8,
        plan_pilot_checks=7,
        pilot_criteria=7,
        # One to one: this module's eight statements are the plan's eight checks, in
        # order, so nothing here was a judgement. The unmarked one is the only check in
        # the list that asks for an explanation rather than a refusal.
        from_the_plan={**marked(1, 3, 4, 5, 6, 7, 8), **unmarked(2)},
        judged_here={},
    ),
    PlanRecord(
        label="Phase 2",
        criteria=phase2_criteria,
        plan_checks=14,
        plan_pilot_checks=11,
        pilot_criteria=19,
        # The plan's fourteen checks are criteria 1 to 14. The three it does not mark are
        # the interim happy path, the deferred wrong-team check, and the approver display.
        from_the_plan={**marked(1, 2, 5, 6, 7, 8, 9, 10, 12, 13, 14), **unmarked(3, 4, 11)},
        # Eight criteria the plan's list never reached, all of them controls or record
        # properties in the phase where the money and the attribution live.
        judged_here=marked(15, 16, 17, 18, 19, 20, 21, 22),
    ),
    PlanRecord(
        label="Phase 3",
        criteria=phase3_criteria,
        plan_checks=11,
        plan_pilot_checks=6,
        pilot_criteria=13,
        # The plan's eleven checks were criteria 1 to 11. The five it does not mark are the
        # log stream and the four cancellation and duplicate-event cases, each bounded by
        # something else in the list. Three of the five have since left the phase with the
        # cancellation work, and none of them was marked, which is why the pilot count did
        # not move when they went.
        from_the_plan={**marked(1, 3, 4, 8, 9, 10), **unmarked(2, 11)},
        moved_to_a_later_phase=("5", "6", "7"),
        # Eleven criteria the plan's list never reached. Seven are marked; the four that
        # are not are argued one by one in their own scope limits.
        judged_here={**marked(12, 13, 14, 16, 17, 18, 19), **unmarked(15, 20, 21, 22)},
    ),
)


@pytest.mark.parametrize("record", PLAN, ids=lambda record: record.label)
def test_the_plan_check_list_is_transcribed_at_the_size_the_plan_records(
    record: PlanRecord,
) -> None:
    """Mutation: quietly drop a plan check out of the table, or re-mark one.

    This half of the table is not a judgement and must not become one. It is the plan's
    own check list and its own markers, and the two counts beside it are what a reader of
    the plan would count. A check that disappears from here disappears from every
    assertion below it, so the size is asserted before anything is compared.

    A check whose criterion has left the phase is accounted for rather than dropped. It
    still came out of the plan's list and the plan's list is still that long, so the two
    tables together have to add up to the size the plan records -- which is what stops a
    reassigned check and a mistyped one looking the same here.
    """
    assert len(record.from_the_plan) + len(record.moved_to_a_later_phase) == record.plan_checks
    assert sum(record.from_the_plan.values()) == record.plan_pilot_checks
    # The plan's checks are the first criteria of each module, in the plan's order, which
    # is what makes a positional mapping honest rather than convenient.
    assert sorted([*record.from_the_plan, *record.moved_to_a_later_phase], key=int) == [
        str(n) for n in range(1, record.plan_checks + 1)
    ]


@pytest.mark.parametrize("record", PLAN, ids=lambda record: record.label)
def test_every_criterion_is_either_a_plan_check_or_a_recorded_judgement(
    record: PlanRecord,
) -> None:
    """Mutation: add a criterion and leave its pilot flag to a default.

    A criterion in neither table has had no decision taken about it, and the default it
    would fall to is the one that reads as safe while sorting nothing. This is what makes
    a new criterion a decision somebody has to write down.

    The third table is held to the opposite claim. A number listed as moved to a later
    phase has to be absent from the module, or the table is describing a removal that did
    not happen while the criterion goes on failing the gate here.
    """
    shipped = [spec.number for spec in record.criteria()]

    assert set(record.from_the_plan).isdisjoint(record.judged_here)
    assert sorted(record.expected, key=int) == shipped
    assert set(record.moved_to_a_later_phase).isdisjoint(shipped)


@pytest.mark.parametrize("record", PLAN, ids=lambda record: record.label)
def test_the_shipped_flags_are_the_ones_the_table_records(record: PlanRecord) -> None:
    """Mutation: flip a flag in the module and leave the plan's markers where they are.

    The document and the code drifting apart is the failure this table exists for. Both
    directions fail here: a marker added in the plan and not in the module, and a flag
    added in the module that no plan check and no recorded judgement accounts for.
    """
    shipped = {spec.number: spec.pilot_blocking for spec in record.criteria()}

    assert shipped == record.expected


@pytest.mark.parametrize("record", PLAN, ids=lambda record: record.label)
def test_the_pilot_count_is_the_number_this_phase_records(record: PlanRecord) -> None:
    # Two recorded numbers and one computed one. The count has to match the table entry
    # by entry and has to match the total a reader is told, so a compensating pair of
    # edits fails as loudly as a single one.
    specs = record.criteria()

    assert sum(spec.pilot_blocking for spec in specs) == record.pilot_criteria
    assert record.pilot_criteria == record.plan_pilot_checks + sum(record.judged_here.values())


@pytest.mark.parametrize("record", PLAN, ids=lambda record: record.label)
def test_no_shipped_deferral_blocks_a_pilot(record: PlanRecord) -> None:
    # The contract refuses the combination at construction, so this can only fail if the
    # contract stops refusing it. That is the point: the rule is asserted against what
    # actually ships rather than only against a synthetic spec.
    deferred = [
        spec for spec in record.criteria() if spec.status is CriterionStatus.DEFERRED
    ]

    assert [spec.number for spec in deferred if spec.pilot_blocking] == []


@pytest.mark.parametrize("record", PLAN, ids=lambda record: record.label)
def test_every_criterion_the_plan_did_not_reach_records_why_it_was_decided(
    record: PlanRecord,
) -> None:
    """Mutation: flag a criterion the plan never mentioned and say nothing about it.

    A flag set without an argument is a flag nobody can review, and the argument belongs
    beside the criterion rather than in a commit message nobody reads twice. Only the
    criteria the plan's list never reached are held to this: the rest carry the plan's
    marker, and repeating its reasoning here would be a second copy to keep in step.
    """
    by_number = {spec.number: spec for spec in record.criteria()}

    for number in record.judged_here:
        written = " ".join(by_number[number].scope_limits)
        assert "pilot-blocking" in written.lower(), number
        assert len(written) > 200, number


def test_the_shipped_phases_reach_the_verdicts_their_criteria_support() -> None:
    """The three answers a reader wants, computed from the definitions rather than run.

    Phase 1 is pilot-ready because every criterion it marks is covered. Phase 2 and
    Phase 3 are not, and this states it without starting a gate: the recorded status is
    what a green suite would produce, and execution can only make a criterion worse.
    """
    verdicts = {
        record.label: pilot_verdict(
            tuple(
                result(
                    spec.number,
                    passed=spec.status is not CriterionStatus.GAP,
                    pilot_blocking=spec.pilot_blocking,
                )
                for spec in record.criteria()
            )
        )
        for record in PLAN
    }

    assert verdicts["Phase 1"].readiness is PilotReadiness.READY
    assert verdicts["Phase 2"].readiness is PilotReadiness.BLOCKED
    # Criterion 9 left this list on 2026-07-31, when the team-leads team's membership was
    # captured and compared against config/organization.yaml in both directions. It was
    # the one entry here waiting on a capture nobody had taken rather than on a run nobody
    # had made, which is why it moved on its own.
    assert verdicts["Phase 2"].unmet_criteria == ("2", "6", "7", "12", "14", "19")
    assert verdicts["Phase 3"].readiness is PilotReadiness.BLOCKED
    # Phase 3's rung is now partly open rather than closed, and naming which five are left
    # is the point. Four completed runs moved eight of the thirteen marked criteria at
    # once; what remains is a duplicate submission, a committed capture of the denial
    # matrix, the workload matrix from inside a container, the two Phase 2 roles the
    # validator and state machine hold, and an inventory of the whole lineage store.
    assert verdicts["Phase 3"].unmet_criteria == ("10", "12", "13", "14", "18")
    assert set(verdicts["Phase 3"].unmet_criteria) < set(
        verdicts["Phase 3"].blocking_criteria
    ), "a strict subset, or the run evidence has stopped counting for anything"


def test_the_phases_that_are_blocked_say_so_in_words_a_reader_can_act_on() -> None:
    blocked: Sequence[str] = ("Phase 2", "Phase 3")

    for record in PLAN:
        if record.label not in blocked:
            continue
        verdict = pilot_verdict(
            tuple(
                result(
                    spec.number,
                    passed=spec.status is not CriterionStatus.GAP,
                    pilot_blocking=spec.pilot_blocking,
                )
                for spec in record.criteria()
            )
        )
        assert "pilot rung is closed" in verdict.note, record.label
        assert verdict.unmet_criteria, record.label
