"""The Phase 1 proof bundle, and the two things it must never be able to do.

A bundle exists so a reviewer can decide whether the phase is done without reading the
suite, so the failures worth testing are the ones that would mislead that reader: prose
that gives a criterion a status the gate did not reach, and a recorded role digest
silently re-recorded after the template it describes was widened.

This module builds bundles, so it is listed in ``REENTRANT_TEST_MODULES`` and no criterion
may cite it. It is also excluded from the verification run inside every generator, which
is why building a bundle here does not recurse.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.criteria import CriterionSpec, CriterionStatus
from edullm_platform.evidence import FRESHNESS_WINDOW
from edullm_platform.open_decisions import open_decisions
from edullm_platform.phase1_capture import (
    CAPTURE_SUFFIX,
    ROLE_CAPTURE_DIR,
    RUN_CAPTURE_DIR,
    only_a_pending_deploy_stands_in_the_way,
    read_committed_role_captures,
    read_committed_run_evidence,
)
from edullm_platform.proof_bundle import (
    BundleWaitingOnADeployError,
    GoldenDigestDriftError,
    MissingTestNodeError,
    ProofBundleError,
    contradicting_status_claims,
    load_recorded_goldens,
)
from edullm_platform.publisher_denials import PROBE_SELECTION_LESSONS
from edullm_platform.rebuild_comparison import NONDETERMINISM_CAUSES
from edullm_platform.role_drift import COMMITTED_ROLE_TEMPLATES
from tools.build_phase1_proof import (
    BUNDLE_FILENAMES,
    GENERATOR_TEST_PATH,
    GOLDENS_FILENAME,
    GOLDENS_REPORT_FILENAME,
    NESTED_RUN_ENV,
    Verification,
    build_bundle,
    compute_goldens,
    goldens_path,
    known_limitations,
    main,
    phase1_criteria,
    verify_repository,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIRST_INSTANT = datetime(2026, 1, 1, tzinfo=UTC)
SECOND_INSTANT = datetime(2026, 6, 30, 12, 34, 56, tzinfo=UTC)
GENERATED_AT_PREFIX = "Generated: "
PUBLISHER_ROLE = "sbsandbox-intern-edullm-ecr-publisher"


def long_ago() -> str:
    """An observation timestamp far enough past the window to be stale on any machine."""
    return (datetime.now(tz=UTC) - FRESHNESS_WINDOW - timedelta(days=1)).isoformat()


def committed_payload(role_name: str) -> dict[str, Any]:
    path = PROJECT_ROOT / ROLE_CAPTURE_DIR / f"{role_name}{CAPTURE_SUFFIX}"
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def write_capture(directory: Path, role_name: str, payload: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{role_name}{CAPTURE_SUFFIX}").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def widen_the_publisher(payload: dict[str, Any]) -> None:
    if payload["role_name"] != PUBLISHER_ROLE:
        return
    payload["inline_policies"][0]["statements"][1]["action_match"]["actions"].append(
        "ecr:DeleteRepository"
    )


@pytest.fixture(scope="session")
def verification() -> Verification:
    return verify_repository(PROJECT_ROOT)


def shipped_checks() -> tuple[CriterionSpec, ...]:
    return phase1_criteria()


def read_bundle(directory: Path) -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(directory.iterdir())
        if path.is_file()
    }


def build_into(directory: Path, verification: Verification, moment: datetime) -> dict[str, str]:
    build_bundle(PROJECT_ROOT, directory, generated_at=moment, verification=verification)
    return read_bundle(directory)


def strip_generated_at(documents: dict[str, str]) -> dict[str, str]:
    return {
        name: "\n".join(
            line for line in text.splitlines() if not line.startswith(GENERATED_AT_PREFIX)
        )
        for name, text in documents.items()
    }


def captures_waiting_on_a_deploy() -> tuple[str, ...]:
    """Roles whose committed capture is behind its template because of a pending deploy.

    A template amendment lands before the stack that carries it, because the stacks holding
    these roles are applied from a laptop and this repository cannot obtain those
    credentials. Between the two commits the account is genuinely behind the template, the
    comparison is genuinely right to say so, and the generator is genuinely right to refuse:
    a bundle records criteria 4 and 5 as covered on the strength of those captures, so
    building one now would print a status the gate does not reach.

    **This asks the precise question, and it used to ask a looser one.** It was every
    capture that had stopped holding, for any reason, which meant an expired capture and an
    undeployed amendment produced the same skip below — an expiry disappearing into a
    "waiting on a deploy" message is exactly the substitution the freshness window exists to
    prevent. It now reads
    :func:`~edullm_platform.phase1_capture.only_a_pending_deploy_stands_in_the_way`, which
    returns nothing at all if anything else has also stopped holding, so those cases fail
    loudly instead of standing down for a reason that is not the recorded one.

    The refusal is the behaviour under test rather than an obstacle to it, which is why the
    cases that need a *complete* bundle are skipped rather than weakened, and why
    :func:`test_the_generator_refuses_exactly_while_a_capture_is_waiting_on_a_deploy` runs in
    every state. Skipping without that case would let the skip outlive the deploy.
    """
    return tuple(
        capture.role_name
        for capture in only_a_pending_deploy_stands_in_the_way(
            read_committed_role_captures(PROJECT_ROOT)
        )
    )


def build_as_far_as_the_pending_deploy_allows(
    directory: Path,
    verification: Verification,
) -> None:
    """Build, tolerating the one refusal a laptop deploy nobody has run would cause.

    For the two cases below that need the recorded goldens to exist and do not need a
    complete bundle. The goldens pair is written before that refusal, deliberately: it
    describes the committed templates and says nothing about the account, so a deploy
    nobody has run must not stop the digests being recorded.

    Only :class:`BundleWaitingOnADeployError` is tolerated, so every other refusal still
    fails these cases, and once the deploy lands nothing is suppressed at all and both
    cases read exactly as they did before this state existed.
    """
    try:
        build_bundle(PROJECT_ROOT, directory, generated_at=FIRST_INSTANT, verification=verification)
    except BundleWaitingOnADeployError:
        pass


def test_the_generator_refuses_exactly_while_a_capture_is_waiting_on_a_deploy(
    tmp_path: Path,
    verification: Verification,
) -> None:
    """Whichever state the tree is in, the generator must be in the matching one.

    Runs in both directions on purpose. While a capture is behind its template the build has
    to refuse, with the class that names *this* reason rather than any refusal at all, and
    name the role; once the deploy lands it has to succeed. The second half is what clears
    the skip below, because the day it becomes unnecessary this case is what fails if
    nobody has removed the record it is waiting on.

    It also asserts the goldens pair either way. That is the part a reviewer should read
    twice: the digests of what a role template grants are recorded even in the refusing
    state, so the tripwire stays armed against the template as amended rather than against
    the one that was committed before the amendment.
    """
    waiting = captures_waiting_on_a_deploy()

    if waiting:
        with pytest.raises(BundleWaitingOnADeployError) as refusal:
            build_into(tmp_path, verification, FIRST_INSTANT)
        for role_name in waiting:
            assert role_name in str(refusal.value)
        assert sorted(path.name for path in tmp_path.iterdir()) == sorted(
            (GOLDENS_FILENAME, GOLDENS_REPORT_FILENAME)
        )
        return

    documents = build_into(tmp_path, verification, FIRST_INSTANT)
    assert set(documents) == set(BUNDLE_FILENAMES)


def test_the_recorded_goldens_are_written_even_while_a_deploy_is_outstanding(
    tmp_path: Path,
    verification: Verification,
) -> None:
    """A digest of a committed template does not depend on the account, so nothing about
    the account may stop it being recorded.

    This is the case that keeps the two mechanisms from fighting. The golden tripwire
    reports that a role template changed; the pending-amendment record reports that the
    account has not caught up with that change. They are the same event seen from two
    sides, and the tripwire is the one with a remedy — re-record it. Before the goldens
    were written ahead of this refusal, that remedy was unreachable until a deploy no test
    can perform, so the suite carried a permanently red test restating what the pending
    record already said.
    """
    build_as_far_as_the_pending_deploy_allows(tmp_path, verification)

    assert load_recorded_goldens(goldens_path(tmp_path)) == compute_goldens(PROJECT_ROOT)
    for record in compute_goldens(PROJECT_ROOT):
        assert record.digest in (tmp_path / GOLDENS_REPORT_FILENAME).read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def first_bundle(
    tmp_path_factory: pytest.TempPathFactory,
    verification: Verification,
) -> dict[str, str]:
    waiting = captures_waiting_on_a_deploy()
    if waiting:
        pytest.skip(
            "a committed role capture is waiting on a laptop deploy, so no complete bundle "
            f"can be built: {', '.join(waiting)}. See "
            "test_the_generator_refuses_exactly_while_a_capture_is_waiting_on_a_deploy, "
            "which fails once the deploy lands and this skip stops being true."
        )
    return build_into(tmp_path_factory.mktemp("first"), verification, FIRST_INSTANT)


@pytest.mark.slow
def test_the_bundle_contains_every_document_it_declares(first_bundle: dict[str, str]) -> None:
    assert set(first_bundle) == set(BUNDLE_FILENAMES)


@pytest.mark.slow
def test_two_runs_at_the_same_instant_are_byte_identical(
    tmp_path_factory: pytest.TempPathFactory,
    verification: Verification,
    first_bundle: dict[str, str],
) -> None:
    again = build_into(tmp_path_factory.mktemp("second"), verification, FIRST_INSTANT)

    assert again == first_bundle


@pytest.mark.slow
def test_a_later_run_differs_only_in_its_generated_at_line(
    tmp_path_factory: pytest.TempPathFactory,
    verification: Verification,
    first_bundle: dict[str, str],
) -> None:
    later = build_into(tmp_path_factory.mktemp("later"), verification, SECOND_INSTANT)

    assert later != first_bundle
    assert strip_generated_at(later) == strip_generated_at(first_bundle)


@pytest.mark.slow
def test_every_document_passes_the_evidence_secret_scan(first_bundle: dict[str, str]) -> None:
    # The bundle quotes IAM policy resources, which is where an account ID would arrive.
    # Nothing here should carry one, and the generator refuses to write if it does.
    from edullm_platform.proof_bundle import assert_secret_free

    for name, text in first_bundle.items():
        assert_secret_free(name, text)


# --------------------------------------------------------------------------------------
# The bundle may not state a status the gate did not reach
# --------------------------------------------------------------------------------------


@pytest.mark.slow
def test_no_prose_in_the_bundle_contradicts_a_recorded_check_status(
    first_bundle: dict[str, str],
) -> None:
    assert contradicting_status_claims(first_bundle, shipped_checks()) == ()


@pytest.mark.parametrize(
    ("prose", "expected"),
    [
        ("Check 6 remains a gap.", "gap"),
        ("Criterion 3 remains a gap.", "gap"),
        ("Check 12 is covered.", "covered"),
    ],
    ids=["covered called a gap", "another covered called a gap", "criterion that does not exist"],
)
def test_a_sentence_that_disagrees_with_the_definition_is_caught(
    prose: str,
    expected: str,
) -> None:
    problems = contradicting_status_claims({"README.md": prose}, shipped_checks())

    assert len(problems) == 1
    assert expected in problems[0]


@pytest.mark.slow
def test_the_generator_refuses_to_write_a_bundle_whose_prose_contradicts_the_gate(
    tmp_path: Path,
    verification: Verification,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The one defect a bundle cannot survive. A reviewer who trusts it without reading the
    # suite would come away believing a gap was closed.
    monkeypatch.setattr(
        "tools.build_phase1_proof.known_limitations",
        lambda repo_root, checks, reports, run: ("Check 6 remains a gap.",),
    )

    with pytest.raises(ProofBundleError, match="did not reach"):
        build_bundle(PROJECT_ROOT, tmp_path, generated_at=FIRST_INSTANT, verification=verification)

    assert not (tmp_path / "README.md").exists()


@pytest.mark.slow
def test_the_generator_refuses_a_bundle_that_miscounts_the_criteria_in_the_aggregate(
    tmp_path: Path,
    verification: Verification,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The shape the Phase 1 gate note went stale in: no check is named, so the numbered
    # reader sees nothing, and the sentence is still a status claim about this phase.
    monkeypatch.setattr(
        "tools.build_phase1_proof.known_limitations",
        lambda repo_root, checks, reports, run: ("Four criteria are gaps today.",),
    )

    with pytest.raises(ProofBundleError, match="four criteria are gap"):
        build_bundle(PROJECT_ROOT, tmp_path, generated_at=FIRST_INSTANT, verification=verification)

    assert not (tmp_path / "README.md").exists()


def test_a_limitation_that_names_a_check_takes_its_status_from_the_definition() -> None:
    # The limitations are the one place prose and status meet, so the status word is read
    # off the checks rather than typed. This is what makes the guard above never fire.
    limitations = known_limitations(
        PROJECT_ROOT, shipped_checks(), (), read_committed_run_evidence(PROJECT_ROOT)
    )

    assert any("check 1 is covered" in text for text in limitations)
    assert any("check 7 is covered" in text for text in limitations)


def test_a_limitation_naming_a_check_the_phase_does_not_have_is_refused() -> None:
    with pytest.raises(ProofBundleError, match="does not record"):
        known_limitations(
            PROJECT_ROOT,
            shipped_checks()[:0],
            (),
            read_committed_run_evidence(PROJECT_ROOT),
        )


# --------------------------------------------------------------------------------------
# Golden digests over what a role template grants
# --------------------------------------------------------------------------------------


def test_the_recorded_goldens_cover_every_committed_role() -> None:
    goldens = compute_goldens(PROJECT_ROOT)
    recorded = load_recorded_goldens(goldens_path(PROJECT_ROOT / "proof" / "phase-1"))

    assert {record.fixture for record in recorded} == {record.fixture for record in goldens}
    assert {record.digest for record in recorded} == {record.digest for record in goldens}


@pytest.mark.slow
def test_a_widened_template_refuses_the_build_rather_than_being_re_recorded(
    tmp_path: Path,
    verification: Verification,
) -> None:
    # The digest is over what the role grants, so this is a template that gained an
    # action. Re-recording it has to be a deliberate act, because the account was last
    # compared against the projection this replaces.
    build_as_far_as_the_pending_deploy_allows(tmp_path, verification)
    recorded = json.loads(goldens_path(tmp_path).read_text(encoding="utf-8"))
    recorded["fixtures"][0]["digest"] = "sha256:" + "0" * 64
    goldens_path(tmp_path).write_text(json.dumps(recorded, indent=2, sort_keys=True) + "\n")

    with pytest.raises(GoldenDigestDriftError, match="--regenerate-goldens"):
        build_bundle(PROJECT_ROOT, tmp_path, generated_at=FIRST_INSTANT, verification=verification)


@pytest.mark.slow
def test_regenerating_records_the_live_digest(tmp_path: Path, verification: Verification) -> None:
    build_as_far_as_the_pending_deploy_allows(tmp_path, verification)
    recorded = json.loads(goldens_path(tmp_path).read_text(encoding="utf-8"))
    recorded["fixtures"][0]["digest"] = "sha256:" + "0" * 64
    goldens_path(tmp_path).write_text(json.dumps(recorded, indent=2, sort_keys=True) + "\n")

    try:
        build_bundle(
            PROJECT_ROOT,
            tmp_path,
            generated_at=FIRST_INSTANT,
            regenerate_goldens=True,
            verification=verification,
        )
    except BundleWaitingOnADeployError:
        pass

    assert load_recorded_goldens(goldens_path(tmp_path)) == compute_goldens(PROJECT_ROOT)


def test_main_reports_a_drifted_digest_as_a_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    goldens_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    goldens_path(tmp_path).write_text(
        json.dumps({"fixtures": [], "phase": "phase-1", "schema_version": 1}) + "\n"
    )

    exit_code = main(["--output-dir", str(tmp_path)])

    assert exit_code == 1
    assert "no longer serializes to its recorded canonical digest" in capsys.readouterr().err


# --------------------------------------------------------------------------------------
# The verification run
# --------------------------------------------------------------------------------------


@pytest.mark.slow
def test_the_verification_run_never_selects_a_module_that_would_recurse(
    verification: Verification,
) -> None:
    assert any(
        node_id.startswith(GENERATOR_TEST_PATH) for node_id in verification.collected_node_ids
    )
    assert not any(
        node_id.startswith(GENERATOR_TEST_PATH) for node_id in verification.selected_node_ids
    )
    assert not any(
        node_id.startswith("tests/test_phase1_criteria.py")
        for node_id in verification.selected_node_ids
    )


@pytest.mark.slow
def test_the_verification_run_executed_every_cited_node_id(verification: Verification) -> None:
    cited = {node_id for check in shipped_checks() for node_id in check.cited_node_ids}

    assert cited <= set(verification.selected_node_ids)
    assert verification.failed_node_ids == ()
    assert verification.selected.green


@pytest.mark.slow
def test_the_full_suite_ran_green_inside_the_generator(verification: Verification) -> None:
    assert verification.full_suite.green
    assert verification.full_suite.tests > 0


def test_a_citation_pytest_cannot_collect_aborts_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A matrix may not print a citation nothing ran. Writing the bundle anyway is how a
    # renamed test turns into a green tick beside a claim nobody checked.
    monkeypatch.setattr(
        "tools.build_phase1_proof.collect_node_ids",
        lambda repo_root, *, nested_env: ("tests/test_manifest.py::test_something_else",),
    )

    with pytest.raises(MissingTestNodeError, match="may not claim coverage it cannot run"):
        verify_repository(PROJECT_ROOT)


def test_the_generator_refuses_to_run_from_inside_its_own_verification(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(NESTED_RUN_ENV, "1")

    assert main([]) == 2
    assert "would" not in capsys.readouterr().out


# --------------------------------------------------------------------------------------
# What the bundle says about the account
# --------------------------------------------------------------------------------------


@pytest.mark.slow
def test_the_drift_document_reports_the_comparison_it_actually_ran(
    first_bundle: dict[str, str],
) -> None:
    # A bundle that described the machinery and compared nothing was the state before a
    # capture existed. Now that one does, the document has to say what it found and when
    # what it found stops being true.
    drift = first_bundle["deployed-role-drift.md"]
    captures = read_committed_role_captures(PROJECT_ROOT)

    assert "## What this bundle compared" in drift
    assert "Nothing." not in drift
    assert str(ROLE_CAPTURE_DIR) in drift
    for capture in captures:
        assert capture.expires_at is not None
        assert capture.role_name in drift
        assert capture.expires_at.date().isoformat() in drift


@pytest.mark.slow
def test_the_index_counts_the_roles_compared_and_what_the_comparison_found(
    first_bundle: dict[str, str],
) -> None:
    readme = first_bundle["README.md"]

    assert f"| roles compared to their template | {len(COMMITTED_ROLE_TEMPLATES)} |" in readme
    assert "| role drift findings | 0 |" in readme


@pytest.mark.slow
@pytest.mark.parametrize(
    ("break_the_capture", "expected"),
    [
        (lambda payload: payload.update({"observed_at": long_ago()}), "evidence_stale"),
        (widen_the_publisher, "role_drift"),
        (lambda payload: payload.clear(), "evidence_invalid"),
    ],
    ids=["expired", "drifted", "unreadable"],
)
def test_the_generator_refuses_to_build_on_a_capture_that_no_longer_holds(
    tmp_path: Path,
    verification: Verification,
    monkeypatch: pytest.MonkeyPatch,
    break_the_capture: Callable[[dict[str, Any]], None],
    expected: str,
) -> None:
    # The matrix prints the status the definition records, and the definition records
    # criteria 4 and 5 as covered on the strength of this capture. Once the capture stops
    # holding, the gate disagrees with that, and writing the bundle anyway would hand a
    # reviewer a document whose prose the gate contradicts.
    broken = tmp_path / "captures"
    for role_name, _template in COMMITTED_ROLE_TEMPLATES:
        payload = committed_payload(role_name)
        break_the_capture(payload)
        write_capture(broken, role_name, payload)
    monkeypatch.setattr(
        "tools.build_phase1_proof.read_committed_role_captures",
        lambda repo_root: read_committed_role_captures(repo_root, capture_dir=broken),
    )

    with pytest.raises(ProofBundleError, match=expected):
        build_bundle(
            PROJECT_ROOT,
            tmp_path / "bundle",
            generated_at=FIRST_INSTANT,
            verification=verification,
        )

    assert not (tmp_path / "bundle" / "README.md").exists()


@pytest.mark.slow
def test_the_refusal_says_what_to_do_about_it(
    tmp_path: Path,
    verification: Verification,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A refusal an operator cannot act on is a wall. Both honest responses are named,
    # including the one that removes the citations rather than renewing them.
    aged = tmp_path / "captures"
    for role_name, _template in COMMITTED_ROLE_TEMPLATES:
        payload = committed_payload(role_name)
        payload["observed_at"] = long_ago()
        write_capture(aged, role_name, payload)
    monkeypatch.setattr(
        "tools.build_phase1_proof.read_committed_role_captures",
        lambda repo_root: read_committed_role_captures(repo_root, capture_dir=aged),
    )

    with pytest.raises(ProofBundleError) as raised:
        build_bundle(
            PROJECT_ROOT, tmp_path / "bundle", generated_at=FIRST_INSTANT, verification=verification
        )

    assert "tools/capture_phase1_evidence.py" in str(raised.value)
    assert "phase1_criteria.py" in str(raised.value)


@pytest.mark.slow
def test_the_matrix_reports_the_gaps_before_the_per_check_detail(
    first_bundle: dict[str, str],
) -> None:
    matrix = first_bundle["negative-case-matrix.md"]
    gaps = [check.number for check in shipped_checks() if check.status is CriterionStatus.GAP]

    if gaps:
        assert matrix.index("## Gaps") < matrix.index("## Checks")
    else:
        # No gaps, so no Gaps section. The section is generated from the criteria rather
        # than always printed, which is what stops it becoming an empty heading a reader
        # skims past on the day it stops being empty again.
        assert "## Gaps" not in matrix
    for number in gaps:
        assert f"### Check {number} (GAP)" in matrix


@pytest.mark.slow
def test_the_index_reports_the_verdict_the_gate_reaches(first_bundle: dict[str, str]) -> None:
    gaps = [check.number for check in shipped_checks() if check.status is CriterionStatus.GAP]
    index = first_bundle["README.md"]

    if gaps:
        assert (
            "`tools/validate_phase1.py` exits 1 against this tree. Phase 1 is not accepted: "
            f"criteria {', '.join(gaps)} are GAPs." in index
        )
    else:
        assert "`tools/validate_phase1.py` exits 0 against this tree" in index


@pytest.mark.slow
def test_the_index_does_not_open_by_calling_a_finished_phase_unfinished(
    first_bundle: dict[str, str],
) -> None:
    # The opening sentence used to say "It is not done" unconditionally, which was true
    # when it was written and would have gone on being printed after it stopped being.
    gaps = [check.number for check in shipped_checks() if check.status is CriterionStatus.GAP]
    opening = first_bundle["README.md"]

    if gaps:
        assert "It is not done" in opening
    else:
        assert "It is not done" not in opening
        assert "Every criterion is covered and the gate is green" in opening


@pytest.mark.slow
def test_the_goldens_document_names_the_regeneration_command(
    tmp_path: Path,
    verification: Verification,
) -> None:
    # Read off a written file rather than off the renderer, and off one written in
    # whatever state the tree is in: the guidance a reader needs in order to re-record a
    # digest is least useful in exactly the state where the rest of the bundle refuses.
    build_as_far_as_the_pending_deploy_allows(tmp_path, verification)
    goldens = (tmp_path / GOLDENS_REPORT_FILENAME).read_text(encoding="utf-8")

    assert "--regenerate-goldens" in goldens
    assert GOLDENS_FILENAME in goldens
    assert "tests/test_phase1_golden.py" in goldens


# --------------------------------------------------------------------------------------
# The run, the rebuild comparison and the open decisions
# --------------------------------------------------------------------------------------


@pytest.mark.slow
def test_the_bundle_refuses_a_run_record_that_no_longer_holds(
    tmp_path: Path,
    verification: Verification,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Criteria 1, 6 and 7 are recorded as covered on the strength of these records, so a
    # bundle that printed them covered after the records expired would be stating a
    # status the gate does not reach. Refusing is also what makes the expiry visible.
    monkeypatch.setattr(
        "tools.build_phase1_proof.read_committed_run_evidence",
        lambda repo_root: read_committed_run_evidence(repo_root, directory=tmp_path / "empty"),
    )
    (tmp_path / "empty").mkdir()

    with pytest.raises(ProofBundleError, match="no longer holds"):
        build_bundle(PROJECT_ROOT, tmp_path, generated_at=FIRST_INSTANT, verification=verification)

    assert not (tmp_path / "README.md").exists()


@pytest.mark.slow
def test_the_denial_matrix_document_names_every_refusal_and_its_event(
    first_bundle: dict[str, str],
) -> None:
    document = first_bundle["publisher-denial-matrix.md"]
    run = read_committed_run_evidence(PROJECT_ROOT)

    for denial in run.denials:
        assert denial.attempted_action in document
        assert denial.event_id in document


@pytest.mark.slow
def test_the_denial_matrix_document_carries_the_probe_lessons(
    first_bundle: dict[str, str],
) -> None:
    # The register of what choosing a probe has cost lives in the library; the bundle
    # renders it so that a reviewer who never opens the source still meets it.
    document = first_bundle["publisher-denial-matrix.md"]

    for lesson in PROBE_SELECTION_LESSONS:
        assert lesson.rule in document
        assert lesson.learned_from in document


@pytest.mark.slow
def test_the_rebuild_document_names_every_cause_and_no_unexplained_field(
    first_bundle: dict[str, str],
) -> None:
    document = first_bundle["image-rebuild-comparison.md"]

    for cause in NONDETERMINISM_CAUSES:
        assert cause.name in document
    assert "resumes to the digest already in the registry" in document


@pytest.mark.slow
def test_a_rebuild_difference_nothing_explains_refuses_the_bundle(
    tmp_path: Path,
    verification: Verification,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The phase's account of its own nondeterminism has to stay complete. A build that
    # started differing somewhere nobody has explained is exactly what criterion 2 exists
    # to surface, so the bundle stops rather than printing it as a row.
    monkeypatch.setattr(
        "tools.build_phase1_proof.unexplained", lambda differences: ("config.User",)
    )

    with pytest.raises(ProofBundleError, match="no cause explains"):
        build_bundle(PROJECT_ROOT, tmp_path, generated_at=FIRST_INSTANT, verification=verification)


@pytest.mark.slow
def test_the_open_decisions_document_records_the_question_without_answering_it(
    first_bundle: dict[str, str],
) -> None:
    document = first_bundle["open-decisions.md"]
    decision = open_decisions()[0]

    assert decision.question in document
    for option in decision.options:
        assert option in document
    assert decision.lands_in in document


@pytest.mark.slow
def test_the_index_sends_a_reviewer_to_the_open_decisions(
    first_bundle: dict[str, str],
) -> None:
    # A limitation that says a question is open and does not say where to read it is a
    # limitation nobody follows up.
    index = first_bundle["README.md"]

    assert "`open-decisions.md`" in index
    assert "scan" in index


@pytest.mark.slow
def test_the_index_measures_every_committed_run_record(first_bundle: dict[str, str]) -> None:
    # Read off the tree rather than listed, so a record added to the run directory is
    # measured without a second edit to the generator.
    index = first_bundle["README.md"]
    committed = sorted(
        str(path.relative_to(PROJECT_ROOT))
        for path in (PROJECT_ROOT / RUN_CAPTURE_DIR).rglob("*.json")
    )

    assert committed
    for path in committed:
        assert path in index
