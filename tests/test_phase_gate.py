"""The one gate the four criteria-only phases share, held to what each of them needs.

Phases 1 to 4 used to be four copies of one report model and four copies of one command,
and what each copy differed in was a phase name and a count. They are one of each now, so
the thing worth testing is that the shared piece still knows which phase it is working
for: a base that derived the note from the wrong phase, or held every report to the same
minimum, would leave all four gates green and all four of them lying.

Every case is parameterised over all four phases rather than written once against a
representative, because "representative" is the assumption that fails here -- the count is
per phase by construction, and Phase 4 had no direct test of its gate at all before this
module existed.

Nothing here starts a gate. The report models are built from synthetic results and the
command is given an evaluation of its own, so what is measured is what the shared code
computes and prints rather than a second execution of the suite already running.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from edullm_platform.canonical import canonical_json_bytes
from edullm_platform.criteria import (
    CriteriaDefinitionError,
    CriterionResult,
    CriterionStatus,
    PilotReadiness,
)
from edullm_platform.criteria_runner import NestedExecutionError
from edullm_platform.phase1_criteria import PHASE1_CRITERION_COUNT
from edullm_platform.phase1_gate import Phase1GateReport
from edullm_platform.phase2_criteria import PHASE2_CRITERION_COUNT
from edullm_platform.phase2_gate import Phase2GateReport
from edullm_platform.phase3_criteria import PHASE3_CRITERION_COUNT
from edullm_platform.phase3_gate import Phase3GateReport
from edullm_platform.phase4_criteria import PHASE4_CRITERION_COUNT
from edullm_platform.phase4_gate import Phase4GateReport
from edullm_platform.phase_gate import PhaseGateReport, run_gate_command


@dataclass(frozen=True)
class PhaseUnderTest:
    """One phase's report type, beside the two facts it is supposed to carry alone."""

    label: str
    report: type[PhaseGateReport]
    criterion_count: int


PHASES = (
    PhaseUnderTest("Phase 1", Phase1GateReport, PHASE1_CRITERION_COUNT),
    PhaseUnderTest("Phase 2", Phase2GateReport, PHASE2_CRITERION_COUNT),
    PhaseUnderTest("Phase 3", Phase3GateReport, PHASE3_CRITERION_COUNT),
    PhaseUnderTest("Phase 4", Phase4GateReport, PHASE4_CRITERION_COUNT),
)


def result(number: str, *, passed: bool = True) -> CriterionResult:
    return CriterionResult(
        number=number,
        statement=f"Criterion {number}.",
        status=CriterionStatus.COVERED if passed else CriterionStatus.GAP,
        passed=passed,
        pilot_blocking=True,
        reason_code="ok" if passed else "recorded_gap",
        detail="",
        cited_node_ids=(),
        missing_node_ids=(),
        failed_node_ids=(),
    )


def results(count: int) -> tuple[CriterionResult, ...]:
    return tuple(result(str(number)) for number in range(1, count + 1))


def report_for(subject: PhaseUnderTest, **overrides: object) -> PhaseGateReport:
    fields: dict[str, object] = {"phase_criteria": results(subject.criterion_count)}
    fields.update(overrides)
    return subject.report(**fields)


def raising(exception: Exception) -> Callable[[Path], PhaseGateReport]:
    def evaluate(_repo_root: Path) -> PhaseGateReport:
        raise exception

    return evaluate


by_label = pytest.mark.parametrize("subject", PHASES, ids=lambda subject: subject.label)


# --------------------------------------------------------------------------------------
# Each report still knows which phase it is
# --------------------------------------------------------------------------------------


@by_label
def test_every_phase_report_states_its_own_phase_in_the_note(subject: PhaseUnderTest) -> None:
    """Mutation: hardcode a phase in the shared base, or read it from the first result.

    The note is the sentence a reader sees beside the verdict, and it opens by naming the
    phase whose criteria it is summarising. One base deriving it means one place where
    every phase could start claiming to be Phase 1.
    """
    note = report_for(subject).phase_criteria_note

    assert note.startswith(f"phase_criteria are the {subject.label} acceptance criteria.")
    assert subject.report.phase == subject.label


def test_the_four_phases_do_not_share_a_phase_name() -> None:
    # Anchors the case above, which a base that returned the same label for every subclass
    # would satisfy if the table happened to be built from that same label.
    assert len({subject.report.phase for subject in PHASES}) == len(PHASES)


@by_label
def test_a_caller_cannot_tell_a_report_which_phase_it_is_about(subject: PhaseUnderTest) -> None:
    # The phase is a class variable rather than a field for this reason: a supplied one is
    # one that can name a phase the criteria in the same document are not from.
    with pytest.raises(ValidationError) as refused:
        report_for(subject, phase="Phase 9")

    assert [error["type"] for error in refused.value.errors()] == ["extra_forbidden"]


# --------------------------------------------------------------------------------------
# Each report is still held to its own phase's count
# --------------------------------------------------------------------------------------


@by_label
def test_a_report_short_of_the_criteria_its_phase_records_is_refused(
    subject: PhaseUnderTest,
) -> None:
    """Mutation: declare the criteria field once in the base with a shared minimum.

    The count is the one thing that is genuinely per phase, and it is the constraint that
    catches a definition that lost a criterion between being read and being reported. A
    shared minimum would be either no constraint at all or the wrong one for three of the
    four phases, and the report would go on printing a verdict over a short table.
    """
    with pytest.raises(ValidationError) as refused:
        report_for(subject, phase_criteria=results(subject.criterion_count - 1))

    assert [error["type"] for error in refused.value.errors()] == ["too_short"]


@by_label
def test_the_number_of_criteria_the_phase_records_is_accepted(subject: PhaseUnderTest) -> None:
    # Anchors the case above, which a field that refused every length would satisfy.
    assert len(report_for(subject).phase_criteria) == subject.criterion_count


def test_the_four_phases_do_not_all_record_the_same_number_of_criteria() -> None:
    # And anchors it the other way: if every phase happened to want the same minimum, the
    # case above would pass against a base that hardcoded one.
    assert len({subject.criterion_count for subject in PHASES}) > 1


# --------------------------------------------------------------------------------------
# Both verdicts, from the criteria and from nothing else
# --------------------------------------------------------------------------------------


@by_label
def test_one_failing_criterion_fails_the_whole_gate(subject: PhaseUnderTest) -> None:
    # The gate verdict is the AND, and it stays the AND for every phase that inherits it.
    criteria = (*results(subject.criterion_count - 1), result(str(subject.criterion_count),
                                                              passed=False))

    report = report_for(subject, phase_criteria=criteria)

    assert report.passed is False
    assert report_for(subject).passed is True


@by_label
def test_a_note_a_caller_supplies_is_replaced_by_the_computed_one(
    subject: PhaseUnderTest,
) -> None:
    # A field a caller can set is a field that can disagree with the criteria printed
    # beside it, which is what the Phase 1 note did for a fortnight.
    supplied = report_for(subject, phase_criteria_note="Four criteria are gaps today.")

    assert "Four criteria are gaps" not in supplied.phase_criteria_note
    assert supplied.phase_criteria_note == report_for(subject).phase_criteria_note


@by_label
def test_the_pilot_verdict_is_computed_beside_the_gate_verdict(subject: PhaseUnderTest) -> None:
    report = report_for(subject)

    assert report.pilot.readiness is PilotReadiness.READY
    assert report.pilot.evaluated_criteria == subject.criterion_count


# --------------------------------------------------------------------------------------
# What the shared command prints, and what it exits
# --------------------------------------------------------------------------------------


@by_label
@pytest.mark.parametrize("gate_passes", [True, False], ids=["gate green", "gate red"])
def test_the_command_writes_the_canonical_report_and_both_verdicts(
    subject: PhaseUnderTest,
    gate_passes: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stdout is the report a caller parses; stderr is the line a person reads.

    Mutation: print the verdict line on stdout. Everything downstream of a gate parses
    stdout as one JSON document, so a sentence on the end of it is a parse error rather
    than a nicety.
    """
    last = result(str(subject.criterion_count), passed=gate_passes)
    report = report_for(subject, phase_criteria=(*results(subject.criterion_count - 1), last))

    exit_code = run_gate_command(phase=subject.label, evaluate=lambda _root: report)
    printed = capsys.readouterr()

    assert exit_code == (0 if gate_passes else 1)
    assert printed.out.encode("utf-8") == canonical_json_bytes(report) + b"\n"
    assert json.loads(printed.out)["passed"] is gate_passes
    assert f"{subject.label} gate: {'pass' if gate_passes else 'fail'}" in printed.err
    assert f"{subject.label} pilot rung:" in printed.err


@by_label
def test_an_unusable_criteria_definition_says_which_phase_it_belongs_to(
    subject: PhaseUnderTest,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The one failure whose message names the phase. A reader running four gates in a row
    # needs to know which definition stopped being usable.
    refusal = CriteriaDefinitionError("criterion 3 cites nothing")

    exit_code = run_gate_command(phase=subject.label, evaluate=raising(refusal))
    printed = capsys.readouterr()

    assert exit_code == 2
    assert printed.out == ""
    assert printed.err == (
        f"the {subject.label} criteria definition is not usable: criterion 3 cites nothing\n"
    )


@pytest.mark.parametrize(
    "failure",
    [
        NestedExecutionError("a gate started from inside a gate would recurse"),
        OSError("config/organization.yaml is not readable"),
        json.JSONDecodeError("Expecting value", "", 0),
        TypeError("the criteria definition returned something that is not a sequence"),
        RuntimeError("nobody anticipated this one"),
    ],
    ids=["nested gate", "unreadable input", "malformed json", "wrong type", "unanticipated"],
)
def test_every_other_failure_is_an_exit_two_with_nothing_on_stdout(
    failure: Exception,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: let an unanticipated failure escape as a traceback.

    Exit 2 means the gate could not run, which is a different answer from exit 1's "it ran
    and the phase is not done". A traceback is neither, and a caller reading the exit code
    to decide whether a phase is accepted would read a crash as a failed phase.
    """
    exit_code = run_gate_command(phase="Phase 1", evaluate=raising(failure))
    printed = capsys.readouterr()

    assert exit_code == 2
    assert printed.out == ""
    assert printed.err == f"{failure}\n"
