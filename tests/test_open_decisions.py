"""The questions the repository has surfaced and has not answered.

The register is only worth having if it cannot quietly turn into a set of answers, so
most of what is checked here is the shape rather than the content: an entry with one
option is a decision somebody took, an entry with no landing point is a note nobody will
read at the moment it matters, and an entry that reads as a statement is an opinion
wearing a question mark.

The one content assertion is that the scan question is in there, because it is the
question this phase surfaced and the whole reason the register exists.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edullm_platform.open_decisions import (
    OPEN_DECISION_COUNT,
    OpenDecision,
    OpenDecisionsDefinitionError,
    open_decisions,
    validate_open_decisions,
)
from edullm_platform.phase1_capture import RUN_CAPTURE_DIR

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def a_decision(**overrides: object) -> OpenDecision:
    fields: dict[str, object] = {
        "number": "9",
        "question": "Should something happen?",
        "why_it_matters": ("Because otherwise it will be decided by accident.",),
        "what_is_known": ("Very little.",),
        "options": ("Do it.", "Do not do it."),
        "lands_in": "Before the thing happens.",
        "raised_by": "A run that went one way and could have gone the other.",
    }
    fields.update(overrides)
    return OpenDecision(**fields)  # type: ignore[arg-type]


def test_the_register_holds_what_it_says_it_holds() -> None:
    decisions = open_decisions()

    assert len(decisions) == OPEN_DECISION_COUNT
    assert [decision.number for decision in decisions] == ["2"]


def test_the_scan_question_is_gone_because_it_was_answered() -> None:
    # Phase 1 ran a scan that found four critical and eight high, and blocked nothing.
    # Phase 3 answered whether it should have been able to. The register's own rule is that
    # answering means deleting the entry and putting the answer where it is enforced, so
    # the assertion is that no scan question survives here -- not that one reads correctly.
    #
    # Where the answer went is asserted in tests/test_phase3_image_scan.py, against the
    # shipped policy and the shipped exception registry rather than against this module.
    for decision in open_decisions():
        assert "image scan" not in decision.question.lower()


def test_the_scan_that_raised_the_answered_question_is_still_committed() -> None:
    # The question is gone; the evidence that made it urgent is not, and the answer in
    # config/policy.yaml only makes sense beside it. Four criticals is why blocking on a
    # severity threshold alone was rejected.
    scan = json.loads(
        (PROJECT_ROOT / RUN_CAPTURE_DIR / "image-scan.sanitized.json").read_text(encoding="utf-8")
    )

    assert scan["finding_counts"]["critical"] == 4
    assert scan["finding_counts"]["high"] == 8


def test_every_open_question_says_when_it_has_to_be_answered() -> None:
    for decision in open_decisions():

        assert "before" in decision.lands_in.lower()


def test_a_question_with_one_option_is_refused() -> None:
    # This is the failure mode the register exists to prevent: an entry becoming a
    # decision by having its alternatives quietly deleted.
    with pytest.raises(OpenDecisionsDefinitionError, match="fewer than two options"):
        a_decision(options=("Just do it.",))


def test_a_statement_is_not_a_question() -> None:
    with pytest.raises(OpenDecisionsDefinitionError, match="not written as a question"):
        a_decision(question="We should block on criticals.")


@pytest.mark.parametrize("field", ["why_it_matters", "what_is_known"])
def test_a_question_with_nothing_behind_it_is_refused(field: str) -> None:
    with pytest.raises(OpenDecisionsDefinitionError):
        a_decision(**{field: ()})


@pytest.mark.parametrize("field", ["lands_in", "raised_by"])
def test_a_question_with_no_landing_point_or_no_origin_is_refused(field: str) -> None:
    with pytest.raises(OpenDecisionsDefinitionError):
        a_decision(**{field: "   "})


def test_two_questions_cannot_share_a_number() -> None:
    with pytest.raises(OpenDecisionsDefinitionError, match="must be unique"):
        validate_open_decisions([a_decision(), a_decision()])
