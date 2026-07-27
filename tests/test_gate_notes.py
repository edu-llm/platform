"""What a gate's own note is allowed to say, and what stops it saying anything else.

The gate emits its note in the same JSON document as the verdict, so a note that states
how many criteria are gaps is making a claim about the very numbers beside it. It shipped
as a hand-written sentence, and the sentence went stale the day the build path ran: the
gate printed ``passed: true`` with eight covered criteria and a note saying four of them
were gaps. These tests hold the note to the computation and hold the guard to the note.
"""

from __future__ import annotations

import pytest

from edullm_platform.criteria import CriterionResult, CriterionSpec, CriterionStatus
from edullm_platform.phase0_gate import GateCheck, Phase0GateReport
from edullm_platform.phase1_gate import Phase1GateReport
from edullm_platform.status_prose import (
    NUMBER_WORDS,
    contradicting_status_claims,
    phase_criteria_note,
    spell,
    status_count_claims,
    status_summary_sentence,
)


def result(number: str, status: CriterionStatus) -> CriterionResult:
    return CriterionResult(
        number=number,
        statement=f"Criterion {number}.",
        status=status,
        passed=status is not CriterionStatus.GAP,
        reason_code="ok",
        detail="",
        cited_node_ids=(),
        missing_node_ids=(),
        failed_node_ids=(),
    )


def covered(number: str) -> CriterionSpec:
    return CriterionSpec(
        number=number,
        statement=f"Criterion {number}.",
        status=CriterionStatus.COVERED,
        proving_node_ids=(f"tests/test_thing.py::test_{number}",),
    )


def gap(number: str) -> CriterionSpec:
    return CriterionSpec(
        number=number,
        statement=f"Criterion {number}.",
        status=CriterionStatus.GAP,
        gaps=("Nothing proves it.",),
    )


def inventory_check(*, passed: bool = True) -> GateCheck:
    return GateCheck(check_id="inventory_ownership", passed=passed, reason_code="ok", detail="")


# --------------------------------------------------------------------------------------
# The note derives its facts
# --------------------------------------------------------------------------------------


def test_the_summary_counts_what_the_run_computed_rather_than_what_was_written() -> None:
    records = (
        result("1", CriterionStatus.COVERED),
        result("2", CriterionStatus.COVERED),
        result("3", CriterionStatus.DEFERRED),
        result("4", CriterionStatus.GAP),
    )

    summary = status_summary_sentence(records)

    assert "four acceptance criteria" in summary
    assert "two criteria are covered" in summary
    assert "one criterion is deferred" in summary
    assert "one criterion is a gap" in summary


def test_the_summary_says_none_rather_than_omitting_a_status_nothing_holds() -> None:
    # A status left out of the sentence reads as unknown. Saying "no criteria are gaps"
    # is the claim the reader wants and the one the guard can check.
    summary = status_summary_sentence((result("1", CriterionStatus.COVERED),))

    assert "one acceptance criterion" in summary
    assert "one criterion is covered" in summary
    assert "no criteria are deferred" in summary
    assert "no criteria are gaps" in summary


@pytest.mark.parametrize(("count", "word"), [(0, "no"), (1, "one"), (8, "eight"), (13, "thirteen")])
def test_counts_are_spelled_the_way_the_guard_reads_them(count: int, word: str) -> None:
    assert spell(count) == word
    assert NUMBER_WORDS[word] == count


def test_a_count_past_the_spelled_range_falls_back_to_digits() -> None:
    assert spell(42) == "42"


def test_the_note_keeps_the_durable_rule_and_derives_only_the_facts() -> None:
    note = phase_criteria_note((result("1", CriterionStatus.COVERED),), phase="Phase 1")

    # The part that is a statement about the three-status model is not a fact about this
    # run, and survives unchanged.
    assert "Only three statuses exist" in note
    assert "whatever status the definition records" in note
    assert "Phase 1 acceptance criteria" in note
    assert "one criterion is covered" in note


# --------------------------------------------------------------------------------------
# The guard reads an aggregate claim, not only a numbered one
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prose", "count", "status"),
    [
        ("Four criteria are gaps today.", 4, CriterionStatus.GAP),
        ("Two criteria are deferred.", 2, CriterionStatus.DEFERRED),
        ("All eight criteria are covered.", 8, CriterionStatus.COVERED),
        ("No criteria are gaps.", 0, CriterionStatus.GAP),
        ("One criterion is a gap.", 1, CriterionStatus.GAP),
        ("3 criteria are covered.", 3, CriterionStatus.COVERED),
    ],
)
def test_every_shape_of_aggregate_status_prose_is_read(
    prose: str,
    count: int,
    status: CriterionStatus,
) -> None:
    assert status_count_claims(prose) == ((count, status),)


@pytest.mark.parametrize(
    "prose",
    [
        "Two criteria rest on the run capture.",
        "Five refusals proved the criterion for five actions.",
        "Two mechanisms close this and both are covered by citations.",
    ],
)
def test_prose_that_ascribes_no_status_to_a_count_is_not_read_as_a_claim(prose: str) -> None:
    assert status_count_claims(prose) == ()


def test_the_total_is_read_from_the_acceptance_criteria_idiom() -> None:
    claims = status_count_claims("These are the thirteen Phase 0 acceptance criteria.")

    assert claims == ((13, None),)


def test_a_phase_number_is_not_read_as_a_count() -> None:
    # "the eight Phase 1 acceptance criteria" claims eight, not one.
    assert status_count_claims("the eight Phase 1 acceptance criteria") == ((8, None),)


def test_an_aggregate_claim_the_definition_does_not_support_is_reported() -> None:
    # The sentence that shipped in the Phase 1 gate note after the build path ran.
    planted = {
        "gate note": (
            "Four criteria are gaps today because the build path has never run against the account."
        )
    }

    problems = contradicting_status_claims(planted, tuple(covered(str(n)) for n in range(1, 9)))

    assert len(problems) == 1
    assert "gate note" in problems[0]
    assert "four criteria" in problems[0]
    assert "gap" in problems[0]


def test_a_wrong_total_is_reported() -> None:
    problems = contradicting_status_claims(
        {"gate note": "the thirteen Phase 1 acceptance criteria"},
        tuple(covered(str(n)) for n in range(1, 9)),
    )

    assert len(problems) == 1
    assert "thirteen" in problems[0] or "13" in problems[0]


def test_an_aggregate_claim_the_definition_supports_is_not_reported() -> None:
    checks = (*(covered(str(n)) for n in range(1, 8)), gap("8"))

    assert (
        contradicting_status_claims(
            {
                "gate note": (
                    "These are the eight Phase 1 acceptance criteria. Seven criteria are "
                    "covered, no criteria are deferred, and one criterion is a gap."
                )
            },
            checks,
        )
        == ()
    )


def test_the_numbered_guard_still_reads_a_per_check_claim() -> None:
    problems = contradicting_status_claims(
        {"README.md": "Check 2 is a gap."},
        tuple(covered(str(n)) for n in range(1, 9)),
    )

    assert len(problems) == 1
    assert "check 2" in problems[0]


# --------------------------------------------------------------------------------------
# The gate holds its own note to the guard
# --------------------------------------------------------------------------------------


def test_the_phase1_note_is_derived_from_the_criteria_the_run_computed() -> None:
    report = Phase1GateReport(
        phase_criteria=tuple(result(str(n), CriterionStatus.COVERED) for n in range(1, 9))
    )

    assert "eight criteria are covered" in report.phase_criteria_note
    assert "no criteria are gaps" in report.phase_criteria_note
    assert "Four criteria are gaps" not in report.phase_criteria_note


def test_a_phase1_note_supplied_by_a_caller_cannot_override_the_computed_one() -> None:
    report = Phase1GateReport(
        phase_criteria=tuple(result(str(n), CriterionStatus.COVERED) for n in range(1, 9)),
        phase_criteria_note="Four criteria are gaps today.",
    )

    assert "Four criteria are gaps" not in report.phase_criteria_note
    assert "eight criteria are covered" in report.phase_criteria_note


def test_a_phase1_note_that_contradicts_the_computed_criteria_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The mutation the fix exists to catch: a hardcoded status claim put back into the
    # durable part of the note, where no caller can see it and no test reads it directly.
    monkeypatch.setattr(
        "edullm_platform.status_prose.PHASE_CRITERIA_NOTE_PREAMBLE",
        "Four criteria are gaps today.",
    )

    with pytest.raises(ValueError, match="four criteria"):
        Phase1GateReport(
            phase_criteria=tuple(result(str(n), CriterionStatus.COVERED) for n in range(1, 9))
        )


def test_the_phase0_notes_are_derived_from_what_the_run_computed() -> None:
    report = Phase0GateReport(
        phase_criteria=(result("1", CriterionStatus.COVERED),),
        operational_inventory_checks=(inventory_check(), inventory_check()),
    )

    assert "Phase 0 acceptance criteria" in report.phase_criteria_note
    assert "one criterion is covered" in report.phase_criteria_note
    assert "thirteen" not in report.phase_criteria_note
    assert "All two of them passing" in report.operational_inventory_note
    assert "nine" not in report.operational_inventory_note


def test_a_phase0_note_that_contradicts_the_computed_criteria_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "edullm_platform.status_prose.PHASE_CRITERIA_NOTE_PREAMBLE",
        "Two criteria are deferred.",
    )

    with pytest.raises(ValueError, match="two criteria"):
        Phase0GateReport(
            phase_criteria=(result("1", CriterionStatus.COVERED),),
            operational_inventory_checks=(inventory_check(),),
        )
