"""The Phase 1 acceptance criteria and the status recorded for each one.

The eight statements below are transcribed from the master plan. They are the
specification; ``edullm_platform.phase1_criteria`` is the implementation. Comparing them
verbatim stops the definition quietly rewriting the criterion it claims to satisfy.

Phase 1 has produced no live run, so half of it is honestly unproved. The tests here
exist mostly to hold that line: they pin which criteria are gaps, that a gap cites
nothing, and that nothing was relabelled ``DEFERRED`` to make the count look better.

This module is listed in ``REENTRANT_TEST_MODULES`` ahead of the Wave 4 gate and
proof-bundle tests that will live here, so no criterion can ever cite it.
"""

from __future__ import annotations

import pytest

from edullm_platform.criteria import REENTRANT_TEST_MODULES, CriterionSpec, CriterionStatus
from edullm_platform.phase1_criteria import PHASE1_CRITERION_COUNT, phase1_criteria

#: The eight Phase 1 checks as the master plan states them.
PHASE1_STATEMENTS = (
    "A pushed branch commit produces a digest.",
    (
        "Rebuilding identical inputs is explainable even if byte-level image "
        "reproducibility differs."
    ),
    "A dirty or unpushed commit is rejected.",
    "A commit from an unauthorized repository is rejected.",
    "A pull-request test job cannot request AWS credentials.",
    "The publisher role cannot submit jobs, read datasets, alter IAM, or modify Batch.",
    "An immutable tag cannot be overwritten.",
    "A run manifest using a tag instead of a digest is rejected.",
)

#: What each criterion is recorded as, stated here rather than read from the definition
#: so that promoting a gap to covered has to be done twice and reviewed once.
EXPECTED_STATUSES = {
    "1": CriterionStatus.GAP,
    "2": CriterionStatus.GAP,
    "3": CriterionStatus.COVERED,
    "4": CriterionStatus.COVERED,
    "5": CriterionStatus.COVERED,
    "6": CriterionStatus.GAP,
    "7": CriterionStatus.GAP,
    "8": CriterionStatus.COVERED,
}

#: The criteria that describe what the live path did. Nothing has run it, so no test in
#: this repository can prove them and none of them may cite one.
AWAITING_A_LIVE_RUN = ("1", "2", "6", "7")


@pytest.fixture(scope="module")
def criteria() -> tuple[CriterionSpec, ...]:
    return phase1_criteria()


def by_number(criteria: tuple[CriterionSpec, ...], number: str) -> CriterionSpec:
    return next(check for check in criteria if check.number == number)


def test_the_definition_states_the_eight_criteria_verbatim(
    criteria: tuple[CriterionSpec, ...],
) -> None:
    assert tuple(check.statement for check in criteria) == PHASE1_STATEMENTS


def test_the_criteria_are_numbered_one_to_eight(criteria: tuple[CriterionSpec, ...]) -> None:
    assert [check.number for check in criteria] == [str(index) for index in range(1, 9)]
    assert len(criteria) == PHASE1_CRITERION_COUNT


def test_each_criterion_records_the_status_its_evidence_supports(
    criteria: tuple[CriterionSpec, ...],
) -> None:
    assert {check.number: check.status for check in criteria} == EXPECTED_STATUSES


def test_nothing_is_deferred_because_no_deferral_was_decided(
    criteria: tuple[CriterionSpec, ...],
) -> None:
    # A deferral requires a written reason and a written trigger, and is a recorded
    # decision not to satisfy a criterion yet. No such decision has been taken for any
    # Phase 1 check, so an unproved check here is a gap and fails the gate.
    assert [check.number for check in criteria if check.status is CriterionStatus.DEFERRED] == []


@pytest.mark.parametrize("number", AWAITING_A_LIVE_RUN)
def test_a_criterion_awaiting_a_live_run_is_a_gap_that_cites_nothing(
    criteria: tuple[CriterionSpec, ...],
    number: str,
) -> None:
    # A citation on one of these would say a test proves something about a path that has
    # never executed. Naming the nearest test instead of nothing is how a matrix starts
    # gesturing at coverage it does not have.
    check = by_number(criteria, number)

    assert check.status is CriterionStatus.GAP
    assert check.cited_node_ids == ()
    assert check.gaps
    assert all(text.strip() for text in check.gaps)


def test_every_covered_criterion_cites_a_proving_test(
    criteria: tuple[CriterionSpec, ...],
) -> None:
    covered = [check for check in criteria if check.status is CriterionStatus.COVERED]

    assert [check.number for check in covered] == ["3", "4", "5", "8"]
    for check in covered:
        assert check.proving_node_ids, check.number
        assert not check.gaps, check.number


def test_the_pull_request_case_cites_both_mechanisms_that_close_it(
    criteria: tuple[CriterionSpec, ...],
) -> None:
    # The gate job holds no id-token permission, so it cannot request a token; and the
    # publisher trust policy matches sub against ref:refs/heads/*, which no pull-request
    # subject satisfies. Either one alone would close the criterion, so citing one would
    # hide the loss of the other.
    modules = {node_id.split("::", 1)[0] for node_id in by_number(criteria, "5").cited_node_ids}

    assert "tests/test_build_research_image_workflow.py" in modules
    assert "tests/test_phase1_infrastructure.py" in modules


def test_source_identity_is_what_rejects_a_bad_commit_or_repository(
    criteria: tuple[CriterionSpec, ...],
) -> None:
    for number in ("3", "4"):
        modules = {
            node_id.split("::", 1)[0] for node_id in by_number(criteria, number).cited_node_ids
        }
        assert "tests/test_source_identity.py" in modules, number
        assert "tests/test_verify_source_identity_cli.py" in modules, number


def test_no_criterion_cites_a_module_that_starts_a_gate(
    criteria: tuple[CriterionSpec, ...],
) -> None:
    for check in criteria:
        for node_id in check.cited_node_ids:
            assert node_id.split("::", 1)[0] not in REENTRANT_TEST_MODULES


def test_this_module_can_never_be_cited() -> None:
    assert "tests/test_phase1_criteria.py" in REENTRANT_TEST_MODULES
