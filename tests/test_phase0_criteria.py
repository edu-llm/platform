"""Tests for the Phase 0 acceptance criteria, the gate that executes them, and the CLI.

Everything in this module either runs the gate or runs pytest, which is why the module
is listed in ``REENTRANT_TEST_MODULES`` and can never be cited by a criterion. That is
the structural half of the recursion guard;
``test_no_test_module_that_starts_the_gate_is_citable`` keeps the list honest as more
tests are written.

A handful of the checks here are repository-wide rather than Phase 0's own: that only a
``phase*_criteria.py`` module defines a criterion, that the criterion contract itself is
declared once, that no consumer keeps a second copy of a statement, and that every node
id any phase cites can be collected. They live here because this module already pays for
a full pytest collection and already may not be cited, so a later phase gets them without
a second collection run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

import tools.build_phase0_proof as proof_generator
from edullm_platform import phase0_gate
from edullm_platform.canonical import canonical_json_bytes
from edullm_platform.criteria import (
    REENTRANT_TEST_MODULES,
    CriteriaDefinitionError,
    CriterionResult,
    CriterionSpec,
    CriterionStatus,
    cited_node_ids,
    criterion_result,
    evaluate_criteria,
    execute_criteria,
)
from edullm_platform.criteria_runner import (
    NESTED_GATE_ENV,
    NestedExecutionError,
    SelectionOutcome,
    collect_node_ids,
    refuse_nested_execution,
    run_node_ids,
    subprocess_environment,
)
from edullm_platform.phase0_criteria import (
    discover_fixtures,
    phase0_criteria,
    recorded_checks,
    related_deferrals,
)
from edullm_platform.phase0_gate import GateCheck, Phase0GateReport
from edullm_platform.phase1_criteria import phase1_criteria
from tests.gate_support import (
    copy_gate_repo,
    run_validate_phase0,
    synthetic_account_id_alias,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORIES = (PROJECT_ROOT / "src", PROJECT_ROOT / "tools")

#: The thirteen criteria as the phase states them. This list is the specification; the
#: definition in the library is the implementation. They are compared verbatim so the
#: mapping cannot quietly rewrite the criterion it claims to satisfy.
PHASE0_STATEMENTS = (
    "Valid fixtures compile identically across repeated runs.",
    "Field ordering does not change the canonical hash.",
    "Unknown schema versions fail closed.",
    "Missing commit, image, data, runtime, or authorization fields fail closed.",
    "Mutable image tags are rejected.",
    "Short commit SHAs are rejected.",
    (
        "Arbitrary IAM roles, queues, networking, instance types, and mounts are "
        "rejected."
    ),
    "Logical run IDs and attempt IDs cannot be confused.",
    (
        "Cross-team attribution fails; a submission naming a team the submitter does "
        "not belong to is rejected. Approver scope is a separate question and follows "
        "`approval_scope`."
    ),
    "Lead self-authorization succeeds only within the lead's bound team and policy.",
    "A fan-out is priced across the whole submission, not per cell.",
    (
        "A fan-out whose total exceeds the routine ceiling classifies as an exception, "
        "so a costly sweep cannot be decomposed into routine single runs."
    ),
    (
        "A fan-out mixing compute profiles, image digests, or dataset releases is "
        "rejected."
    ),
)

#: Anything that starts a gate run, a proof-bundle build, or a pytest subprocess. A test
#: module containing one of these must be in REENTRANT_TEST_MODULES. Both phases are
#: named where the name differs and the generic markers cover the rest, which is why the
#: Phase 1 gate's own entry point is called ``evaluate_repository`` like Phase 0's.
#:
#: The two generator filenames are here for a module that runs a generator as a
#: subprocess and never mentions ``build_bundle`` or ``verify_repository`` in its own
#: text, which the function markers alone would miss.
GATE_INVOCATION_MARKERS = (
    "run_validate_phase0",
    "run_validate_phase1",
    "run_gate(",
    "validate_phase0.py",
    "validate_phase1.py",
    "build_phase0_proof.py",
    "build_phase1_proof.py",
    "evaluate_repository(",
    "evaluate_phase0_criteria(",
    "evaluate_phase1_criteria(",
    "execute_criteria(",
    "run_node_ids(",
    "collect_node_ids(",
    "build_bundle(",
    "verify_repository(",
)

#: Test modules that certainly start a gate today. They anchor the marker list, which
#: would otherwise be able to detect nothing at all and still look satisfied. Both phases
#: are anchored, because a marker list that only recognised Phase 0 would pass this while
#: leaving every Phase 1 module free to recurse.
KNOWN_GATE_INVOKING_MODULES = (
    "tests/test_phase0_criteria.py",
    "tests/test_phase0_proof.py",
    "tests/test_phase1_criteria.py",
    "tests/test_phase1_proof.py",
)

#: Markers that only appear where a criterion is defined.
DEFINITION_MARKERS = (
    "CriterionSpec(",
    "proving_node_ids=",
    "supporting_node_ids=",
    "deferral_reason=",
    "deferral_trigger=",
)

#: Markers that only appear where the criterion contract itself is declared. The three
#: statuses, the spec, its error type, and the verdict a gate reaches for one criterion
#: are one contract shared by every phase; a second declaration of any of them is a
#: second contract wearing the same names.
CONTRACT_DECLARATION_MARKERS = (
    "class CriterionSpec",
    "class CriterionStatus",
    "class CriteriaDefinitionError",
    "class CriterionResult",
    "def criterion_result",
)

#: Where the shared machinery lives, and where a criterion may be defined.
CONTRACT_MODULE = "src/edullm_platform/criteria.py"
DEFINITION_GLOB = "phase*_criteria.py"
LIBRARY_DIRECTORY = PROJECT_ROOT / "src" / "edullm_platform"

MINI_SUITE = """
def test_mini_passes() -> None:
    assert True


def test_mini_also_passes() -> None:
    assert True


def test_mini_fails() -> None:
    raise AssertionError("this test exists to be red")


def test_mini_is_skipped() -> None:
    import pytest

    pytest.skip("deliberately skipped")
"""

SLOW_SUITE = """
def test_mini_sleeps() -> None:
    import time

    time.sleep(120)
"""


@pytest.fixture(scope="session")
def references() -> tuple[object, ...]:
    return discover_fixtures(PROJECT_ROOT)


@pytest.fixture(scope="session")
def criteria() -> tuple[CriterionSpec, ...]:
    return phase0_criteria(discover_fixtures(PROJECT_ROOT))


@pytest.fixture(scope="session")
def collected() -> frozenset[str]:
    return collect_node_ids(PROJECT_ROOT)


def source_files() -> list[Path]:
    return sorted(
        path
        for directory in SOURCE_DIRECTORIES
        for path in directory.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def files_containing(marker: str, paths: list[Path]) -> list[str]:
    return [
        str(path.relative_to(PROJECT_ROOT))
        for path in paths
        if marker in path.read_text(encoding="utf-8")
    ]


def criteria_definition_files() -> list[str]:
    """Every source file allowed to define a criterion, as a repository-relative path."""
    return sorted(
        str(path.relative_to(PROJECT_ROOT)) for path in LIBRARY_DIRECTORY.glob(DEFINITION_GLOB)
    )


def statements_by_definition() -> list[tuple[str, tuple[str, ...]]]:
    """Each phase definition and the statements it owns, read from the definition itself.

    Reading the live statements rather than a transcription means a criterion added to
    either phase is covered by the duplication check without anyone remembering to add
    it here. The transcriptions in PHASE1_STATEMENTS and PHASE0_STATEMENTS are checked
    separately, against the phase that owns them.
    """
    return [
        (
            "src/edullm_platform/phase0_criteria.py",
            tuple(check.statement for check in recorded_checks(discover_fixtures(PROJECT_ROOT))),
        ),
        (
            "src/edullm_platform/phase1_criteria.py",
            tuple(check.statement for check in phase1_criteria()),
        ),
    ]


def every_recorded_check(references: tuple[object, ...]) -> tuple[CriterionSpec, ...]:
    return recorded_checks(references) + phase1_criteria()  # type: ignore[arg-type]


def spec(
    number: str = "X",
    statement: str = "A criterion.",
    status: CriterionStatus = CriterionStatus.COVERED,
    **overrides: object,
) -> CriterionSpec:
    fields: dict[str, object] = {
        "number": number,
        "statement": statement,
        "status": status,
        "proving_node_ids": ("tests/test_canonical.py::test_canonical_json_bytes_sorts_keys",),
    }
    fields.update(overrides)
    return CriterionSpec(**fields)  # type: ignore[arg-type]


def outcome_for(
    specs: tuple[CriterionSpec, ...],
    *,
    missing: tuple[str, ...] = (),
    failed: tuple[str, ...] = (),
    execution_error: str | None = None,
) -> SelectionOutcome:
    requested = cited_node_ids(specs)
    collected_ids = requested - frozenset(missing)
    return SelectionOutcome(
        requested=requested,
        collected=collected_ids,
        passed=collected_ids - frozenset(failed),
        exit_code=0,
        execution_error=execution_error,
    )


def write_suite(root: Path, body: str) -> Path:
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "test_mini.py").write_text(body, encoding="utf-8")
    return root


# --------------------------------------------------------------------------------------
# One source of truth for the criterion-to-test mapping
# --------------------------------------------------------------------------------------


def test_the_gate_and_the_proof_generator_call_the_same_definition() -> None:
    assert proof_generator.phase0_criteria is phase0_criteria
    assert phase0_gate.phase0_criteria is phase0_criteria
    assert proof_generator.discover_fixtures is discover_fixtures
    assert phase0_gate.discover_fixtures is discover_fixtures


def test_the_gate_and_the_proof_generator_see_identical_criteria(
    references: tuple[object, ...],
) -> None:
    from_gate = phase0_gate.phase0_criteria(references)  # type: ignore[arg-type]
    from_generator = proof_generator.phase0_criteria(references)  # type: ignore[arg-type]
    assert from_gate == from_generator
    assert cited_node_ids(from_gate) == cited_node_ids(from_generator)


def test_a_phase_criteria_module_exists_to_be_checked() -> None:
    # Everything below is expressed as "no file outside this set", which a glob matching
    # nothing would satisfy without proving anything.
    assert criteria_definition_files() == [
        "src/edullm_platform/phase0_criteria.py",
        "src/edullm_platform/phase1_criteria.py",
        "src/edullm_platform/phase2_criteria.py",
        "src/edullm_platform/phase3_criteria.py",
    ]


@pytest.mark.parametrize("marker", DEFINITION_MARKERS)
def test_only_a_phase_criteria_module_defines_criteria(marker: str) -> None:
    definitions = set(criteria_definition_files())
    found = files_containing(marker, source_files())

    assert found, f"{marker!r} appears in no criteria definition at all"
    assert sorted(set(found) - definitions) == [], (
        f"{marker!r} appears outside a {DEFINITION_GLOB} definition"
    )


@pytest.mark.parametrize("marker", CONTRACT_DECLARATION_MARKERS)
def test_exactly_one_source_file_declares_the_criterion_contract(marker: str) -> None:
    assert files_containing(marker, source_files()) == [CONTRACT_MODULE], (
        f"{marker!r} appears outside the shared criteria contract"
    )


def test_no_consumer_keeps_its_own_criterion_statement() -> None:
    for definition, statements in statements_by_definition():
        others = [
            path for path in source_files() if str(path.relative_to(PROJECT_ROOT)) != definition
        ]
        assert statements, f"{definition} states no criterion"
        for statement in statements:
            assert files_containing(statement, others) == [], (
                f"a second copy of {statement!r} exists outside {definition}"
            )


def test_the_definition_states_the_thirteen_criteria_verbatim(
    criteria: tuple[CriterionSpec, ...],
) -> None:
    assert tuple(check.statement for check in criteria) == PHASE0_STATEMENTS


def test_the_criteria_are_numbered_one_to_thirteen(
    criteria: tuple[CriterionSpec, ...],
) -> None:
    assert [check.number for check in criteria] == [str(index) for index in range(1, 14)]


def test_a_related_deferral_is_recorded_but_is_not_a_phase_criterion(
    references: tuple[object, ...],
    criteria: tuple[CriterionSpec, ...],
) -> None:
    deferrals = related_deferrals(references)  # type: ignore[arg-type]
    assert [check.number for check in deferrals] == ["D1"]
    assert not {check.number for check in deferrals} & {check.number for check in criteria}
    assert recorded_checks(references) == criteria + deferrals  # type: ignore[arg-type]


def test_the_gate_executes_every_node_id_the_matrix_cites(
    references: tuple[object, ...],
    criteria: tuple[CriterionSpec, ...],
) -> None:
    assert cited_node_ids(criteria) <= cited_node_ids(recorded_checks(references))  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# Exactly three statuses
# --------------------------------------------------------------------------------------


def test_there_are_exactly_three_statuses() -> None:
    assert set(CriterionStatus) == {
        CriterionStatus.COVERED,
        CriterionStatus.DEFERRED,
        CriterionStatus.GAP,
    }


def test_partial_no_longer_exists_anywhere_in_the_gate_or_the_generator() -> None:
    assert not hasattr(CriterionStatus, "PARTIAL")
    assert files_containing("PARTIAL", source_files()) == []
    assert files_containing("CheckStatus", source_files()) == []


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        ("no reason", {"deferral_trigger": "bindings are populated"}),
        ("empty reason", {"deferral_reason": "", "deferral_trigger": "populated"}),
        (
            "whitespace reason",
            {"deferral_reason": "   \n ", "deferral_trigger": "populated"},
        ),
        ("no trigger", {"deferral_reason": "waiting on sub-teams"}),
        ("empty trigger", {"deferral_reason": "waiting", "deferral_trigger": ""}),
        (
            "whitespace trigger",
            {"deferral_reason": "waiting", "deferral_trigger": "\t "},
        ),
        ("neither", {}),
    ],
    ids=lambda value: value if isinstance(value, str) else "overrides",
)
def test_a_deferral_without_a_written_reason_and_trigger_is_rejected(
    label: str,
    overrides: dict[str, str],
) -> None:
    with pytest.raises(CriteriaDefinitionError):
        spec(status=CriterionStatus.DEFERRED, proving_node_ids=(), **overrides)


def test_a_deferral_with_both_a_reason_and_a_trigger_is_accepted() -> None:
    accepted = spec(
        status=CriterionStatus.DEFERRED,
        proving_node_ids=(),
        supporting_node_ids=("tests/test_canonical.py::test_canonical_json_bytes_sorts_keys",),
        deferral_reason="team bindings are empty",
        deferral_trigger="team bindings are populated",
    )
    assert accepted.status is CriterionStatus.DEFERRED


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        (
            "deferred citing proof",
            {
                "status": CriterionStatus.DEFERRED,
                "deferral_reason": "waiting",
                "deferral_trigger": "populated",
            },
        ),
        (
            "deferred recording a gap",
            {
                "status": CriterionStatus.DEFERRED,
                "proving_node_ids": (),
                "deferral_reason": "waiting",
                "deferral_trigger": "populated",
                "gaps": ("also a gap",),
            },
        ),
        ("gap citing proof", {"status": CriterionStatus.GAP, "gaps": ("stated",)}),
        ("gap without explanation", {"status": CriterionStatus.GAP, "proving_node_ids": ()}),
        ("covered without proof", {"proving_node_ids": ()}),
        ("covered hiding a gap", {"gaps": ("hidden",)}),
        (
            "covered carrying a deferral",
            {"deferral_reason": "waiting", "deferral_trigger": "populated"},
        ),
        ("statement missing", {"statement": "  "}),
        ("citation outside the suite", {"proving_node_ids": ("src/edullm_platform/x.py::y",)}),
        ("citation without a node name", {"proving_node_ids": ("tests/test_canonical.py",)}),
        (
            "duplicate citation",
            {
                "proving_node_ids": (
                    "tests/test_canonical.py::test_canonical_json_bytes_sorts_keys",
                ),
                "supporting_node_ids": (
                    "tests/test_canonical.py::test_canonical_json_bytes_sorts_keys",
                ),
            },
        ),
        (
            "citation that would re-enter the gate",
            {
                "proving_node_ids": (
                    "tests/test_phase0_criteria.py::test_there_are_exactly_three_statuses",
                )
            },
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else "overrides",
)
def test_an_inconsistent_criterion_is_rejected_when_it_is_constructed(
    label: str,
    overrides: dict[str, object],
) -> None:
    with pytest.raises(CriteriaDefinitionError):
        spec(**overrides)


def test_only_a_covered_criterion_cites_proving_tests(
    criteria: tuple[CriterionSpec, ...],
    references: tuple[object, ...],
) -> None:
    for check in recorded_checks(references):  # type: ignore[arg-type]
        if check.status is CriterionStatus.COVERED:
            assert check.proving_node_ids, check.number
        else:
            assert check.proving_node_ids == (), check.number
    assert criteria


def test_every_shipped_deferral_states_a_reason_and_a_trigger(
    references: tuple[object, ...],
) -> None:
    deferred = [
        check
        for check in recorded_checks(references)  # type: ignore[arg-type]
        if check.status is CriterionStatus.DEFERRED
    ]
    assert deferred
    for check in deferred:
        assert check.deferral_reason and check.deferral_reason.strip()
        assert check.deferral_trigger and check.deferral_trigger.strip()


def test_every_shipped_gap_states_what_is_missing(
    references: tuple[object, ...],
) -> None:
    gaps = [
        check
        for check in recorded_checks(references)  # type: ignore[arg-type]
        if check.status is CriterionStatus.GAP
    ]
    for check in gaps:
        assert check.gaps
        assert all(text.strip() for text in check.gaps)


# --------------------------------------------------------------------------------------
# The gate decides from what the tests did, not from the table
# --------------------------------------------------------------------------------------


def test_a_covered_criterion_whose_tests_all_pass_is_covered() -> None:
    specs = (spec(status=CriterionStatus.COVERED),)
    (result,) = evaluate_criteria(specs, outcome_for(specs))
    assert result.status is CriterionStatus.COVERED
    assert result.passed is True
    assert result.reason_code == "ok"


def test_a_deferred_criterion_whose_tests_all_pass_is_deferred_and_passes() -> None:
    specs = (
        spec(
            status=CriterionStatus.DEFERRED,
            proving_node_ids=(),
            supporting_node_ids=("tests/test_canonical.py::test_canonical_json_bytes_sorts_keys",),
            deferral_reason="team bindings are empty",
            deferral_trigger="team bindings are populated",
        ),
    )
    (result,) = evaluate_criteria(specs, outcome_for(specs))
    assert result.status is CriterionStatus.DEFERRED
    assert result.passed is True
    assert result.reason_code == "deferred_by_recorded_decision"
    assert "team bindings are populated" in result.detail


def test_a_covered_criterion_with_a_missing_citation_is_a_gap() -> None:
    node_id = "tests/test_canonical.py::test_canonical_json_bytes_sorts_keys"
    specs = (spec(status=CriterionStatus.COVERED),)
    (result,) = evaluate_criteria(specs, outcome_for(specs, missing=(node_id,)))
    assert result.status is CriterionStatus.GAP
    assert result.passed is False
    assert result.reason_code == "cited_test_missing"
    assert result.missing_node_ids == (node_id,)


def test_a_covered_criterion_with_a_failing_citation_is_a_gap() -> None:
    node_id = "tests/test_canonical.py::test_canonical_json_bytes_sorts_keys"
    specs = (spec(status=CriterionStatus.COVERED),)
    (result,) = evaluate_criteria(specs, outcome_for(specs, failed=(node_id,)))
    assert result.status is CriterionStatus.GAP
    assert result.passed is False
    assert result.reason_code == "cited_test_failed"
    assert result.failed_node_ids == (node_id,)


def test_a_deferred_criterion_with_a_failing_citation_is_a_gap() -> None:
    node_id = "tests/test_canonical.py::test_canonical_json_bytes_sorts_keys"
    specs = (
        spec(
            status=CriterionStatus.DEFERRED,
            proving_node_ids=(),
            supporting_node_ids=(node_id,),
            deferral_reason="team bindings are empty",
            deferral_trigger="team bindings are populated",
        ),
    )
    (result,) = evaluate_criteria(specs, outcome_for(specs, failed=(node_id,)))
    assert result.status is CriterionStatus.GAP
    assert result.passed is False


def test_a_recorded_gap_stays_a_gap_however_green_its_citations_are() -> None:
    specs = (
        spec(
            status=CriterionStatus.GAP,
            proving_node_ids=(),
            supporting_node_ids=("tests/test_canonical.py::test_canonical_json_bytes_sorts_keys",),
            gaps=("the authorization fixtures have no reordering test",),
        ),
    )
    outcome = outcome_for(specs)
    assert outcome.failed == frozenset()
    (result,) = evaluate_criteria(specs, outcome)
    assert result.status is CriterionStatus.GAP
    assert result.passed is False
    assert result.reason_code == "recorded_gap"
    assert "reordering test" in result.detail


def test_an_execution_failure_fails_every_criterion_closed() -> None:
    specs = (
        spec(number="1", status=CriterionStatus.COVERED),
        spec(
            number="2",
            status=CriterionStatus.DEFERRED,
            proving_node_ids=(),
            supporting_node_ids=("tests/test_canonical.py::test_canonical_json_bytes_sorts_keys",),
            deferral_reason="waiting",
            deferral_trigger="populated",
        ),
    )
    results = evaluate_criteria(specs, outcome_for(specs, execution_error="pytest timed out"))
    assert [result.status for result in results] == [CriterionStatus.GAP, CriterionStatus.GAP]
    assert all(result.reason_code == "criterion_execution_failed" for result in results)
    assert all("pytest timed out" in result.detail for result in results)


def test_a_missing_citation_outranks_a_failing_one_in_the_reason_code() -> None:
    missing = "tests/test_canonical.py::test_canonical_json_bytes_sorts_hand_built_payload"
    failing = "tests/test_canonical.py::test_canonical_json_bytes_sorts_keys"
    specs = (spec(proving_node_ids=(failing, missing)),)
    (result,) = evaluate_criteria(
        specs, outcome_for(specs, missing=(missing,), failed=(failing,))
    )
    assert result.reason_code == "cited_test_missing"
    assert result.missing_node_ids == (missing,)
    assert result.failed_node_ids == (failing,)


def test_a_criterion_result_reports_every_node_id_it_cites() -> None:
    specs = (
        spec(
            proving_node_ids=("tests/test_canonical.py::test_canonical_json_bytes_sorts_keys",),
            supporting_node_ids=(
                "tests/test_canonical.py::test_canonical_json_bytes_sorts_hand_built_payload",
            ),
        ),
    )
    (result,) = evaluate_criteria(specs, outcome_for(specs))
    assert result.cited_node_ids == tuple(sorted(specs[0].cited_node_ids))


# --------------------------------------------------------------------------------------
# The runner really runs pytest
# --------------------------------------------------------------------------------------


@pytest.mark.slow
def test_the_runner_reports_passing_failing_skipped_and_missing_node_ids(
    tmp_path: Path,
) -> None:
    root = write_suite(tmp_path, MINI_SUITE)
    outcome = run_node_ids(
        root,
        [
            "tests/test_mini.py::test_mini_passes",
            "tests/test_mini.py::test_mini_fails",
            "tests/test_mini.py::test_mini_is_skipped",
            "tests/test_mini.py::test_mini_does_not_exist",
        ],
    )
    assert outcome.passed == {"tests/test_mini.py::test_mini_passes"}
    assert outcome.missing == {"tests/test_mini.py::test_mini_does_not_exist"}
    assert outcome.failed == {
        "tests/test_mini.py::test_mini_fails",
        "tests/test_mini.py::test_mini_is_skipped",
    }


@pytest.mark.slow
def test_a_missing_node_id_does_not_stop_the_rest_of_the_selection_running(
    tmp_path: Path,
) -> None:
    root = write_suite(tmp_path, MINI_SUITE)
    outcome = run_node_ids(
        root,
        [
            "tests/test_mini.py::test_mini_passes",
            "tests/test_mini.py::test_mini_also_passes",
            "tests/test_mini.py::test_gone",
        ],
    )
    assert outcome.passed == {
        "tests/test_mini.py::test_mini_passes",
        "tests/test_mini.py::test_mini_also_passes",
    }


@pytest.mark.slow
def test_a_tree_with_no_test_suite_reports_every_citation_as_missing(tmp_path: Path) -> None:
    outcome = run_node_ids(tmp_path, ["tests/test_mini.py::test_mini_passes"])
    assert outcome.collected == frozenset()
    assert outcome.missing == {"tests/test_mini.py::test_mini_passes"}
    assert outcome.execution_error is None


@pytest.mark.slow
def test_a_pytest_run_that_never_finishes_is_reported_as_an_execution_failure(
    tmp_path: Path,
) -> None:
    root = write_suite(tmp_path, SLOW_SUITE)
    # The child sleeps for two minutes, so any timeout at all expires against it. Two
    # seconds spent proving that is two seconds of the suite spent watching a clock.
    outcome = run_node_ids(root, ["tests/test_mini.py::test_mini_sleeps"], timeout=0.3)
    assert outcome.execution_error is not None
    assert "did not finish" in outcome.execution_error
    assert outcome.passed == frozenset()


@pytest.mark.slow
def test_the_runner_executes_real_cited_node_ids_from_this_repository() -> None:
    node_ids = [
        "tests/test_manifest.py::test_manifest_rejects_short_commit_sha",
        "tests/test_manifest.py::test_manifest_rejects_uppercase_commit_sha",
    ]
    outcome = run_node_ids(PROJECT_ROOT, node_ids)
    assert outcome.passed == set(node_ids)
    assert outcome.missing == frozenset()
    assert outcome.exit_code == 0


@pytest.mark.slow
def test_every_node_id_the_criteria_cite_can_be_collected(
    references: tuple[object, ...],
    collected: frozenset[str],
) -> None:
    assert collected, "pytest collected nothing; the runner cannot verify anything"
    uncollectable = sorted(cited_node_ids(every_recorded_check(references)) - collected)
    assert uncollectable == []


@pytest.mark.slow
def test_executing_the_real_criteria_agrees_with_the_recorded_statuses(
    criteria: tuple[CriterionSpec, ...],
    collected: frozenset[str],
) -> None:
    outcome = SelectionOutcome(
        requested=cited_node_ids(criteria),
        collected=collected,
        passed=cited_node_ids(criteria) & collected,
        exit_code=0,
    )
    results = evaluate_criteria(criteria, outcome)
    assert [result.status for result in results] == [check.status for check in criteria]


# --------------------------------------------------------------------------------------
# Recursion guards
# --------------------------------------------------------------------------------------


def test_every_pytest_subprocess_carries_the_nested_flag() -> None:
    assert subprocess_environment({})[NESTED_GATE_ENV] == "1"
    assert subprocess_environment()[NESTED_GATE_ENV] == "1"


def test_the_runner_refuses_to_start_when_it_is_already_nested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("the runner started a subprocess despite the nested guard")

    monkeypatch.setenv(NESTED_GATE_ENV, "1")
    monkeypatch.setattr(subprocess, "run", explode)
    with pytest.raises(NestedExecutionError):
        refuse_nested_execution()
    with pytest.raises(NestedExecutionError):
        run_node_ids(PROJECT_ROOT, ["tests/test_manifest.py::test_manifest_rejects_short_commit_sha"])
    with pytest.raises(NestedExecutionError):
        collect_node_ids(PROJECT_ROOT)


@pytest.mark.slow
def test_a_child_of_the_gate_refuses_to_be_a_gate() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from edullm_platform.criteria_runner import refuse_nested_execution\n"
                "refuse_nested_execution()\n"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=subprocess_environment(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "NestedExecutionError" in completed.stderr


@pytest.mark.parametrize(
    "selection",
    [("tests",), ("tests/test_manifest.py",), ("",)],
    ids=["directory", "file", "empty string"],
)
def test_the_runner_refuses_anything_that_is_not_an_explicit_node_id(
    selection: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="explicit pytest node ids"):
        run_node_ids(PROJECT_ROOT, selection)


def gate_invoking_test_modules() -> set[str]:
    return {
        f"tests/{path.name}"
        for path in sorted((PROJECT_ROOT / "tests").glob("test_*.py"))
        if any(
            marker in path.read_text(encoding="utf-8") for marker in GATE_INVOCATION_MARKERS
        )
    }


def test_no_test_module_that_starts_the_gate_is_citable() -> None:
    # Only one direction of this can stay an equality. REENTRANT_TEST_MODULES is allowed
    # to name a module that does not exist yet, or that exists but has not grown its gate
    # tests yet, because listing one early only refuses citations that were never wanted.
    # The direction that matters — a module that starts the gate and is not listed, which
    # a criterion could then cite and recurse into — stays exact.
    unlisted = sorted(gate_invoking_test_modules() - set(REENTRANT_TEST_MODULES))

    assert unlisted == [], (
        "a test module that starts the gate or the proof generator is not listed in "
        f"REENTRANT_TEST_MODULES, so a criterion could cite it and recurse: {unlisted}"
    )


@pytest.mark.parametrize("module", KNOWN_GATE_INVOKING_MODULES)
def test_the_markers_still_recognize_a_module_that_starts_the_gate(module: str) -> None:
    # Anchors the check above, which is a subset assertion and would pass on an empty
    # marker list.
    assert module in gate_invoking_test_modules()


def test_every_reentrant_entry_names_a_test_module() -> None:
    # A listed module is no longer required to exist, so a typo in the path would
    # otherwise be silently unenforced rather than loudly wrong.
    for module in REENTRANT_TEST_MODULES:
        assert module.startswith("tests/test_") and module.endswith(".py"), module


def test_no_criterion_cites_a_reentrant_module(references: tuple[object, ...]) -> None:
    for node_id in cited_node_ids(every_recorded_check(references)):
        assert node_id.split("::", 1)[0] not in REENTRANT_TEST_MODULES


def test_the_proof_generator_refuses_to_select_a_reentrant_module() -> None:
    assert proof_generator.GENERATOR_TEST_PATH in REENTRANT_TEST_MODULES


# --------------------------------------------------------------------------------------
# Two groups, one verdict
# --------------------------------------------------------------------------------------


def passing_criterion(number: str = "1", passed: bool = True) -> CriterionResult:
    return CriterionResult(
        number=number,
        statement="A criterion.",
        status=CriterionStatus.COVERED if passed else CriterionStatus.GAP,
        passed=passed,
        reason_code="ok" if passed else "recorded_gap",
        detail="",
        cited_node_ids=(),
        missing_node_ids=(),
        failed_node_ids=(),
    )


def inventory_check(passed: bool = True) -> GateCheck:
    return GateCheck(
        check_id="inventory_ownership",
        passed=passed,
        reason_code="ok" if passed else "admin_roster_mismatch",
        detail="",
    )


@pytest.mark.parametrize(
    ("criteria_pass", "inventory_pass", "expected"),
    [(True, True, True), (True, False, False), (False, True, False), (False, False, False)],
)
def test_the_verdict_is_the_and_of_both_groups(
    criteria_pass: bool,
    inventory_pass: bool,
    expected: bool,
) -> None:
    report = Phase0GateReport(
        phase_criteria=(passing_criterion(passed=criteria_pass),),
        operational_inventory_checks=(inventory_check(passed=inventory_pass),),
    )
    assert report.phase_criteria_passed is criteria_pass
    assert report.operational_inventory_passed is inventory_pass
    assert report.passed is expected


def test_the_json_output_keeps_the_two_groups_apart() -> None:
    report = Phase0GateReport(
        phase_criteria=(passing_criterion(),),
        operational_inventory_checks=(inventory_check(),),
    )
    payload = json.loads(canonical_json_bytes(report).decode("utf-8"))
    assert set(payload) == {
        "phase_criteria",
        "phase_criteria_note",
        "phase_criteria_passed",
        "operational_inventory_checks",
        "operational_inventory_note",
        "operational_inventory_passed",
        "passed",
    }
    # Both notes count the group they describe rather than a number somebody wrote once,
    # so this one-criterion report says one; tests/test_gate_notes.py holds the derivation.
    assert "Phase 0 acceptance criteria" in payload["phase_criteria_note"]
    assert "one criterion is covered" in payload["phase_criteria_note"]
    assert "NOT Phase 0 acceptance criteria" in payload["operational_inventory_note"]
    assert "All one of them passing" in payload["operational_inventory_note"]


def test_the_report_round_trips_through_contract_json() -> None:
    report = Phase0GateReport(
        phase_criteria=(passing_criterion(),),
        operational_inventory_checks=(inventory_check(),),
    )
    payload = json.loads(canonical_json_bytes(report).decode("utf-8"))
    for computed in ("passed", "phase_criteria_passed", "operational_inventory_passed"):
        payload.pop(computed)
    assert Phase0GateReport.model_validate(payload) == report


@pytest.mark.slow
def test_execute_criteria_runs_the_cited_tests_and_returns_one_result_each(
    tmp_path: Path,
) -> None:
    root = write_suite(tmp_path, MINI_SUITE)
    specs = (
        CriterionSpec(
            number="1",
            statement="The green one.",
            status=CriterionStatus.COVERED,
            proving_node_ids=("tests/test_mini.py::test_mini_passes",),
        ),
        CriterionSpec(
            number="2",
            statement="The red one.",
            status=CriterionStatus.COVERED,
            proving_node_ids=("tests/test_mini.py::test_mini_fails",),
        ),
    )
    results = execute_criteria(root, specs)
    assert [result.number for result in results] == ["1", "2"]
    assert results[0].passed is True
    assert results[1].passed is False
    assert results[1].reason_code == "cited_test_failed"


# --------------------------------------------------------------------------------------
# The acceptance gate CLI, end to end
# --------------------------------------------------------------------------------------


def inventory_checks_of(stdout: str) -> list[dict[str, object]]:
    payload = json.loads(stdout)
    checks = payload["operational_inventory_checks"]
    assert isinstance(checks, list)
    return checks


@pytest.mark.slow
def test_a_tree_with_no_test_suite_fails_the_gate_closed(tmp_path: Path) -> None:
    repo_root = copy_gate_repo(tmp_path)
    completed = run_validate_phase0(repo_root)
    assert completed.returncode == 1, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["operational_inventory_passed"] is True
    assert payload["phase_criteria_passed"] is False
    assert payload["passed"] is False
    assert len(payload["phase_criteria"]) == 13
    assert {criterion["reason_code"] for criterion in payload["phase_criteria"]} == {
        "cited_test_missing"
    }


@pytest.mark.slow
def test_the_nine_inventory_checks_still_all_pass_for_a_faithful_copy(tmp_path: Path) -> None:
    repo_root = copy_gate_repo(tmp_path)
    completed = run_validate_phase0(repo_root)
    checks = inventory_checks_of(completed.stdout)
    assert len(checks) == 9
    assert all(check["passed"] for check in checks)
    assert all(check["reason_code"] == "ok" for check in checks)
    assert all(str(check["check_id"]).startswith("inventory_") for check in checks)


@pytest.mark.slow
def test_unregistered_compute_profile_cli_emits_structured_json(tmp_path: Path) -> None:
    repo_root = copy_gate_repo(tmp_path)
    manifest_path = repo_root / "fixtures" / "manifests" / "cpu-routine.yaml"
    text = manifest_path.read_text(encoding="utf-8").replace(
        "cpu-32vcpu", "not-a-registered-profile"
    )
    manifest_path.write_text(text, encoding="utf-8")
    completed = run_validate_phase0(repo_root)
    assert completed.returncode == 1
    assert completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["passed"] is False
    assert payload["operational_inventory_passed"] is False
    failing = [check for check in inventory_checks_of(completed.stdout) if not check["passed"]]
    assert any(check["reason_code"] == "unregistered_compute_profile" for check in failing)


@pytest.mark.slow
def test_missing_gpu_quota_cli_emits_structured_json(tmp_path: Path) -> None:
    repo_root = copy_gate_repo(tmp_path)
    evidence_path = repo_root / "fixtures" / "evidence" / "service-quotas.sanitized.json"
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    payload["quotas"] = [
        quota for quota in payload["quotas"] if quota.get("workload_profile") != "gpu-4xa10g"
    ]
    payload["capacity_verdict"] = "blocked"
    payload["capacity_verdict_note"] = (
        "Capacity review blocked because representative workload mapping is incomplete."
    )
    evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    completed = run_validate_phase0(repo_root)
    assert completed.returncode == 1
    assert completed.stdout
    aws_check = next(
        check
        for check in inventory_checks_of(completed.stdout)
        if check["check_id"] == "inventory_aws_capacity"
    )
    assert aws_check["passed"] is False
    assert aws_check["reason_code"] == "capacity_blocked"


@pytest.mark.slow
def test_validate_phase0_reports_a_private_repository_without_a_team_plan(
    tmp_path: Path,
) -> None:
    repo_root = copy_gate_repo(tmp_path)
    evidence_path = repo_root / "fixtures" / "evidence" / "github-plan.sanitized.json"
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    payload["visibility"] = "private"
    evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    completed = run_validate_phase0(repo_root)
    assert completed.returncode == 1
    parsed = json.loads(completed.stdout)
    assert parsed["passed"] is False
    failing = [check for check in inventory_checks_of(completed.stdout) if not check["passed"]]
    assert [check["check_id"] for check in failing] == ["inventory_github_plan"]
    assert failing[0]["reason_code"] == "plan_insufficient_for_private_repo_controls"


@pytest.mark.slow
def test_validate_phase0_exits_two_for_invalid_config(tmp_path: Path) -> None:
    repo_root = copy_gate_repo(tmp_path)
    (repo_root / "config" / "organization.yaml").write_text(
        "admins: []\nteam_leads: []\nmembers: []\npilot_repositories: []\n",
        encoding="utf-8",
    )
    completed = run_validate_phase0(repo_root)
    assert completed.returncode == 2
    assert completed.stdout == ""


@pytest.mark.slow
def test_validate_phase0_exits_two_for_non_utf8_config(tmp_path: Path) -> None:
    repo_root = copy_gate_repo(tmp_path)
    (repo_root / "config" / "organization.yaml").write_bytes(b"\xff\xfe")
    completed = run_validate_phase0(repo_root)
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr


@pytest.mark.slow
def test_validate_phase0_reports_invalid_aws_capacity_evidence(tmp_path: Path) -> None:
    repo_root = copy_gate_repo(tmp_path)
    evidence_path = repo_root / "fixtures" / "evidence" / "service-quotas.sanitized.json"
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    del payload["region"]
    evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    completed = run_validate_phase0(repo_root)
    assert completed.returncode == 1
    assert completed.stdout
    aws_check = next(
        check
        for check in inventory_checks_of(completed.stdout)
        if check["check_id"] == "inventory_aws_capacity"
    )
    assert aws_check["passed"] is False
    assert aws_check["reason_code"] == "evidence_invalid"


@pytest.mark.slow
def test_validate_phase0_reports_production_environment_as_invalid_aws_capacity(
    tmp_path: Path,
) -> None:
    repo_root = copy_gate_repo(tmp_path)
    evidence_path = repo_root / "fixtures" / "evidence" / "service-quotas.sanitized.json"
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    payload["environment"] = "production"
    evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    completed = run_validate_phase0(repo_root)
    assert completed.returncode == 1
    assert completed.stdout
    aws_check = next(
        check
        for check in inventory_checks_of(completed.stdout)
        if check["check_id"] == "inventory_aws_capacity"
    )
    assert aws_check["passed"] is False
    assert aws_check["reason_code"] == "evidence_invalid"


@pytest.mark.slow
def test_validate_phase0_reports_account_id_in_alias_as_invalid_aws_capacity(
    tmp_path: Path,
) -> None:
    repo_root = copy_gate_repo(tmp_path)
    evidence_path = repo_root / "fixtures" / "evidence" / "service-quotas.sanitized.json"
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    payload["account_alias"] = synthetic_account_id_alias()
    evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    completed = run_validate_phase0(repo_root)
    assert completed.returncode == 1
    assert completed.stdout
    aws_check = next(
        check
        for check in inventory_checks_of(completed.stdout)
        if check["check_id"] == "inventory_aws_capacity"
    )
    assert aws_check["passed"] is False
    assert aws_check["reason_code"] == "evidence_invalid"


def test_the_criterion_result_helper_is_the_one_the_gate_uses() -> None:
    specs = (spec(),)
    (direct,) = (criterion_result(specs[0], outcome_for(specs)),)
    (through_evaluate,) = evaluate_criteria(specs, outcome_for(specs))
    assert direct == through_evaluate
    assert replace(specs[0], number="2").number == "2"
