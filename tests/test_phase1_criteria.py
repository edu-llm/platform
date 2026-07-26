"""The Phase 1 acceptance criteria, the status recorded for each one, and the gate.

The eight statements below are transcribed from the master plan. They are the
specification; ``edullm_platform.phase1_criteria`` is the implementation. Comparing them
verbatim stops the definition quietly rewriting the criterion it claims to satisfy.

Phase 1 has produced no live run, so half of it is honestly unproved. The tests here
exist mostly to hold that line: they pin which criteria are gaps, that a gap cites
nothing, and that nothing was relabelled ``DEFERRED`` to make the count look better.

The gate cases at the end of this module start ``tools/validate_phase1.py`` and
``evaluate_repository``, which is why the module is listed in ``REENTRANT_TEST_MODULES``:
no criterion may cite a test that would re-enter the runner that selected it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from edullm_platform.canonical import canonical_json_bytes
from edullm_platform.criteria import (
    REENTRANT_TEST_MODULES,
    CriterionSpec,
    CriterionStatus,
    cited_node_ids,
    evaluate_criteria,
)
from edullm_platform.criteria_runner import NESTED_GATE_ENV, SelectionOutcome
from edullm_platform.phase1_criteria import (
    DEPLOYED_ROLES_MATCH_THEIR_TEMPLATES,
    PHASE1_CRITERION_COUNT,
    phase1_criteria,
)
from edullm_platform.phase1_gate import Phase1GateReport, evaluate_repository
from tests.gate_support import copy_gate_repo, run_validate_phase1

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: A path a criterion's prose points at, as a reader would type it. ``tests`` is here
#: because a gap that names the test standing in for it is a direction to nowhere once
#: that test is renamed.
REFERENCED_PATH = re.compile(r"\b(?:tests|tools|src|infra|fixtures|config)/[\w./-]*[\w]")

#: The citation that expires. Named here so the case below cannot drift from the one the
#: criteria carry: this is the node id whose failure is what thirty days looks like.
CAPTURE_IS_FRESH = (
    "tests/test_phase1_deployed_roles.py"
    "::test_every_committed_capture_is_inside_its_freshness_window"
)

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


def test_every_path_a_criterion_names_is_one_that_exists(
    criteria: tuple[CriterionSpec, ...],
) -> None:
    # A gap says what would close it, and half of those sentences name a tool. A tool
    # that was renamed, or one that was described and never written, turns the honest
    # part of this module into a set of directions to nowhere.
    named = [
        (check.number, path)
        for check in criteria
        for text in (*check.gaps, *check.scope_limits)
        for path in REFERENCED_PATH.findall(text)
    ]

    assert [path for _number, path in named if not (PROJECT_ROOT / path).exists()] == []
    # Anchors the assertion above, which would pass on prose that names nothing at all.
    assert {path for _number, path in named} >= {
        "tools/capture_phase1_evidence.py",
        "tools/verify_publisher_denials.py",
        "fixtures/evidence/phase-1/roles",
        "tests/test_phase1_deployed_roles.py",
    }


def test_the_gap_the_comparison_bears_on_says_which_half_of_it_moved(
    criteria: tuple[CriterionSpec, ...],
) -> None:
    # Criterion 6 is the one the comparison was built for, and it is still a gap. Half of
    # it moved: the deployed role has now been compared to the template and matches, so
    # the template's silence about Batch, S3 and IAM is a fact about the account. The
    # other half did not, and the risk is that the first reads later as having closed the
    # second, so the gap text has to be explicit about both.
    gaps = " ".join(by_number(criteria, "6").gaps)

    assert "role_drift" in gaps
    assert "tools/capture_phase1_evidence.py" in gaps
    assert "fixtures/evidence/phase-1/roles" in gaps
    assert "denial" in gaps.lower()
    assert "CloudTrail" in gaps
    assert by_number(criteria, "6").status is CriterionStatus.GAP


def test_the_criteria_that_rest_on_the_capture_cite_every_part_of_the_claim(
    criteria: tuple[CriterionSpec, ...],
) -> None:
    # Three separate facts make a committed capture worth citing: one exists for each
    # role, it is inside its window, and it matches the template. Citing the third alone
    # would let a deleted or expired record pass as agreement.
    for number in ("4", "5"):
        check = by_number(criteria, number)

        assert set(DEPLOYED_ROLES_MATCH_THEIR_TEMPLATES) <= set(check.supporting_node_ids), number
        assert not set(DEPLOYED_ROLES_MATCH_THEIR_TEMPLATES) & set(check.proving_node_ids), number
    assert CAPTURE_IS_FRESH in DEPLOYED_ROLES_MATCH_THEIR_TEMPLATES


def test_an_expired_capture_takes_the_criteria_resting_on_it_back_to_a_gap(
    criteria: tuple[CriterionSpec, ...],
) -> None:
    # What thirty days does, without waiting thirty days. That the reader reports an aged
    # record as stale is proved in tests/test_phase1_deployed_roles.py; that the citation
    # fails when it does is the assertion above it. This is the consequence: the criteria
    # resting on that citation stop reading as covered, and the gate is red.
    cited = cited_node_ids(criteria)
    expired = SelectionOutcome(
        requested=cited,
        collected=cited,
        passed=cited - {CAPTURE_IS_FRESH},
        exit_code=1,
    )

    results = {result.number: result for result in evaluate_criteria(criteria, expired)}

    for number in ("4", "5"):
        assert results[number].status is CriterionStatus.GAP, number
        assert results[number].reason_code == "cited_test_failed", number
        assert results[number].failed_node_ids == (CAPTURE_IS_FRESH,), number
    # A criterion that does not rest on the capture is untouched by its expiry.
    assert results["3"].passed is True
    assert results["8"].passed is True


def test_no_criterion_cites_a_module_that_starts_a_gate(
    criteria: tuple[CriterionSpec, ...],
) -> None:
    for check in criteria:
        for node_id in check.cited_node_ids:
            assert node_id.split("::", 1)[0] not in REENTRANT_TEST_MODULES


def test_this_module_can_never_be_cited() -> None:
    assert "tests/test_phase1_criteria.py" in REENTRANT_TEST_MODULES


# --------------------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def report() -> Phase1GateReport:
    return evaluate_repository(PROJECT_ROOT)


def test_the_gate_reports_one_result_for_every_criterion_in_order(
    report: Phase1GateReport,
    criteria: tuple[CriterionSpec, ...],
) -> None:
    assert [result.number for result in report.phase_criteria] == [
        check.number for check in criteria
    ]
    assert [result.statement for result in report.phase_criteria] == list(PHASE1_STATEMENTS)


def test_the_gate_executed_every_node_id_the_criteria_cite(
    report: Phase1GateReport,
    criteria: tuple[CriterionSpec, ...],
) -> None:
    # A citation nothing ran is a citation that means nothing. Neither list may be
    # non-empty for any criterion, and every cited id has to appear in the report.
    for result in report.phase_criteria:
        assert result.missing_node_ids == (), result.number
        assert result.failed_node_ids == (), result.number
    assert {node_id for result in report.phase_criteria for node_id in result.cited_node_ids} == {
        node_id for check in criteria for node_id in check.cited_node_ids
    }


def test_the_gate_fails_and_names_the_four_criteria_no_run_has_established(
    report: Phase1GateReport,
) -> None:
    # This is the honest state of the phase rather than a broken gate. A green Phase 1
    # gate today would mean the definition had been softened, not that the phase was done.
    failing = [result.number for result in report.phase_criteria if not result.passed]

    assert failing == list(AWAITING_A_LIVE_RUN)
    assert report.passed is False
    for result in report.phase_criteria:
        if result.number in AWAITING_A_LIVE_RUN:
            assert result.reason_code == "recorded_gap"
            assert result.status is CriterionStatus.GAP
        else:
            assert result.reason_code == "ok"


def test_the_command_exits_one_and_prints_the_verdict_the_gate_reached(
    report: Phase1GateReport,
) -> None:
    completed = run_validate_phase1(PROJECT_ROOT)

    assert completed.returncode == 1
    printed = json.loads(completed.stdout)
    assert printed["passed"] is False
    assert [result["number"] for result in printed["phase_criteria"]] == [
        result.number for result in report.phase_criteria
    ]
    assert completed.stdout.encode("utf-8") == canonical_json_bytes(report) + b"\n"


def test_the_command_refuses_to_run_from_inside_a_gate_run() -> None:
    # The gate runs pytest and pytest runs this module. Without the guard, a Phase 1 gate
    # started from inside a Phase 0 criteria run would spawn another level of both.
    completed = run_validate_phase1(PROJECT_ROOT, **{NESTED_GATE_ENV: "1"})

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "would recurse" in completed.stderr


def test_a_tree_with_no_tests_proves_no_criterion(tmp_path: Path) -> None:
    # A checkout the cited tests cannot be collected from is not a checkout that passes
    # them, and the covered criteria have to fail closed on it.
    repo_root = copy_gate_repo(tmp_path)

    results = evaluate_repository(repo_root).phase_criteria

    assert [result.passed for result in results] == [False] * PHASE1_CRITERION_COUNT
    covered = [result for result in results if result.number not in AWAITING_A_LIVE_RUN]
    assert {result.reason_code for result in covered} == {"cited_test_missing"}
