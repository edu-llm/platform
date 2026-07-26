import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from edullm_platform.phase0_criteria import (
    CriteriaDefinitionError,
    CriterionSpec,
    CriterionStatus,
)
from tools.build_phase0_proof import (
    BUNDLE_FILENAMES,
    GENERATOR_TEST_PATH,
    GOLDENS_FILENAME,
    NESTED_RUN_ENV,
    GoldenDigestDriftError,
    MissingTestNodeError,
    ProofBundleError,
    Verification,
    _count_naming,
    _pytest_environment,
    assert_secret_free,
    build_bundle,
    compute_goldens,
    discover_fixtures,
    golden_drift,
    goldens_path,
    known_limitations,
    load_recorded_goldens,
    main,
    phase0_criteria,
    recorded_checks,
    redact_own_digests,
    related_deferrals,
    render_matrix,
    verify_repository,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIRST_INSTANT = datetime(2026, 1, 1, tzinfo=UTC)
SECOND_INSTANT = datetime(2026, 6, 30, 12, 34, 56, tzinfo=UTC)
GENERATED_AT_PREFIX = "Generated: "


@pytest.fixture(scope="session")
def fixtures() -> tuple[object, ...]:
    return discover_fixtures(PROJECT_ROOT)


@pytest.fixture(scope="session")
def verification() -> Verification:
    return verify_repository(PROJECT_ROOT)


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


@pytest.fixture(scope="session")
def first_bundle(tmp_path_factory: pytest.TempPathFactory, verification: Verification) -> dict[
    str, str
]:
    return build_into(tmp_path_factory.mktemp("first"), verification, FIRST_INSTANT)


@pytest.fixture(scope="session")
def second_bundle(tmp_path_factory: pytest.TempPathFactory, verification: Verification) -> dict[
    str, str
]:
    return build_into(tmp_path_factory.mktemp("second"), verification, FIRST_INSTANT)


@pytest.fixture(scope="session")
def later_bundle(tmp_path_factory: pytest.TempPathFactory, verification: Verification) -> dict[
    str, str
]:
    return build_into(tmp_path_factory.mktemp("later"), verification, SECOND_INSTANT)


def test_the_bundle_contains_every_expected_document(first_bundle: dict[str, str]) -> None:
    assert set(first_bundle) == set(BUNDLE_FILENAMES)


def test_two_runs_at_the_same_instant_are_byte_identical(
    first_bundle: dict[str, str],
    second_bundle: dict[str, str],
) -> None:
    assert first_bundle == second_bundle


def test_a_later_run_differs_only_in_its_generated_at_line(
    first_bundle: dict[str, str],
    later_bundle: dict[str, str],
) -> None:
    assert first_bundle != later_bundle
    assert strip_generated_at(first_bundle) == strip_generated_at(later_bundle)


def test_exactly_one_line_of_the_bundle_carries_a_timestamp(first_bundle: dict[str, str]) -> None:
    timestamped = [
        (name, line)
        for name, text in first_bundle.items()
        for line in text.splitlines()
        if line.startswith(GENERATED_AT_PREFIX)
    ]
    assert timestamped == [("README.md", f"{GENERATED_AT_PREFIX}{FIRST_INSTANT.isoformat()}")]


def test_every_bundle_document_passes_the_secret_scan(first_bundle: dict[str, str]) -> None:
    for name, text in first_bundle.items():
        assert_secret_free(name, text)


@pytest.mark.parametrize(
    ("label", "credential"),
    [
        ("access key id", "AKIAIOSFODNN7EXAMPLE"),
        ("github token", "ghp_" + ("a" * 36)),
        ("aws account id", "123456789012"),
        ("private key header", "-----BEGIN RSA PRIVATE KEY-----"),
        ("secret access key", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
        ("bearer token", "Bearer abc123DEF456ghi789"),
    ],
    ids=lambda value: value if isinstance(value, str) else str(value),
)
def test_the_digest_exemption_does_not_blind_the_secret_scan(
    label: str,
    credential: str,
) -> None:
    planted = f"| fixture | sha256:{'a' * 64} | {credential} |\n"
    with pytest.raises(ProofBundleError, match="did not pass the evidence secret scan"):
        assert_secret_free("planted.md", planted)


def test_the_digest_exemption_masks_only_digest_shaped_tokens() -> None:
    masked = redact_own_digests(f"sha256:{'b' * 64} {'c' * 40} wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXA")
    assert "<sha256-content-digest>" in masked
    assert "<git-commit-sha>" in masked
    assert "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXA" in masked


def test_the_recorded_goldens_cover_every_fixture(first_bundle: dict[str, str]) -> None:
    recorded = json.loads(first_bundle[GOLDENS_FILENAME])["fixtures"]
    assert [entry["relative_path"] for entry in recorded] == [
        reference.relative_path for reference in discover_fixtures(PROJECT_ROOT)
    ]
    assert all(entry["digest"].startswith("sha256:") for entry in recorded)
    assert len(recorded) == 9


def tampered_bundle(directory: Path, source: dict[str, str]) -> tuple[Path, str]:
    directory.mkdir(parents=True, exist_ok=True)
    payload = json.loads(source[GOLDENS_FILENAME])
    drifted = payload["fixtures"][0]
    drifted["digest"] = "sha256:" + ("0" * 64)
    goldens_path(directory).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return directory, drifted["fixture"]


def test_a_drifted_golden_digest_refuses_the_build(
    tmp_path: Path,
    first_bundle: dict[str, str],
    verification: Verification,
) -> None:
    directory, fixture = tampered_bundle(tmp_path / "drifted", first_bundle)
    with pytest.raises(GoldenDigestDriftError) as error:
        build_bundle(
            PROJECT_ROOT,
            directory,
            generated_at=FIRST_INSTANT,
            verification=verification,
        )
    message = str(error.value)
    assert fixture in message
    assert "sha256:" + ("0" * 64) in message
    assert "--regenerate-goldens" in message
    assert "regression" in message


def test_a_drifted_golden_digest_is_not_silently_overwritten(
    tmp_path: Path,
    first_bundle: dict[str, str],
    verification: Verification,
) -> None:
    directory, _fixture = tampered_bundle(tmp_path / "untouched", first_bundle)
    before = goldens_path(directory).read_text(encoding="utf-8")
    with pytest.raises(GoldenDigestDriftError):
        build_bundle(
            PROJECT_ROOT,
            directory,
            generated_at=FIRST_INSTANT,
            verification=verification,
        )
    assert goldens_path(directory).read_text(encoding="utf-8") == before


def test_regenerating_goldens_records_the_live_digest(
    tmp_path: Path,
    first_bundle: dict[str, str],
    verification: Verification,
) -> None:
    directory, _fixture = tampered_bundle(tmp_path / "regenerated", first_bundle)
    build_bundle(
        PROJECT_ROOT,
        directory,
        generated_at=FIRST_INSTANT,
        regenerate_goldens=True,
        verification=verification,
    )
    assert goldens_path(directory).read_text(encoding="utf-8") == first_bundle[GOLDENS_FILENAME]


def test_an_unrecorded_fixture_counts_as_drift() -> None:
    live = compute_goldens(PROJECT_ROOT, discover_fixtures(PROJECT_ROOT))
    drift = golden_drift(live[1:], live)
    assert [entry.fixture for entry in drift] == [live[0].fixture]
    assert drift[0].recorded == "not recorded"


def test_a_removed_fixture_counts_as_drift() -> None:
    live = compute_goldens(PROJECT_ROOT, discover_fixtures(PROJECT_ROOT))
    drift = golden_drift(live, live[1:])
    assert [entry.fixture for entry in drift] == [live[0].fixture]
    assert drift[0].live == "fixture no longer present"


def test_the_generator_refuses_to_run_inside_its_own_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(NESTED_RUN_ENV, "1")
    assert main([]) == 2


def test_the_nested_guard_is_set_for_every_pytest_subprocess() -> None:
    assert _pytest_environment()[NESTED_RUN_ENV] == "1"


def test_the_verification_run_never_selects_the_generators_own_tests(
    verification: Verification,
) -> None:
    assert any(
        node_id.startswith(GENERATOR_TEST_PATH) for node_id in verification.collected_node_ids
    )
    assert not any(
        node_id.startswith(GENERATOR_TEST_PATH) for node_id in verification.selected_node_ids
    )


def test_the_verification_run_executed_every_cited_node_id(verification: Verification) -> None:
    cited = {
        node_id
        for check in recorded_checks(discover_fixtures(PROJECT_ROOT))
        for node_id in check.cited_node_ids
    }
    assert cited
    assert cited <= set(verification.selected_node_ids)
    assert verification.selected.tests == len(verification.selected_node_ids)
    assert verification.selected.green
    assert verification.failed_node_ids == ()


def test_the_full_suite_ran_green_inside_the_generator(verification: Verification) -> None:
    assert verification.full_suite.green
    assert verification.full_suite.failures == 0
    assert verification.full_suite.tests < len(verification.collected_node_ids)


def test_a_citation_pytest_cannot_collect_aborts_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invented = CriterionSpec(
        number="X",
        statement="An invented mapping.",
        status=CriterionStatus.COVERED,
        proving_node_ids=("tests/test_manifest.py::test_this_test_does_not_exist",),
    )
    monkeypatch.setattr("tools.build_phase0_proof.recorded_checks", lambda _refs: (invented,))
    with pytest.raises(MissingTestNodeError, match="test_this_test_does_not_exist"):
        verify_repository(PROJECT_ROOT)


def test_the_generator_refuses_to_select_a_test_that_would_re_enter_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Defeating the construction-time guard on purpose: this test is about the second
    # layer, which catches a reentrant citation that somehow reached the selection.
    monkeypatch.setattr(CriterionSpec, "__post_init__", lambda _self: None)
    reentrant = CriterionSpec(
        number="X",
        statement="A citation that would recurse.",
        status=CriterionStatus.COVERED,
        proving_node_ids=(
            f"{GENERATOR_TEST_PATH}::test_the_bundle_contains_every_expected_document",
        ),
    )
    monkeypatch.setattr("tools.build_phase0_proof.recorded_checks", lambda _refs: (reentrant,))
    with pytest.raises(ProofBundleError, match="would recurse"):
        verify_repository(PROJECT_ROOT)


def test_the_shipped_specs_load_without_raising() -> None:
    assert recorded_checks(discover_fixtures(PROJECT_ROOT))


def test_a_criteria_definition_error_is_reported_rather_than_written(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def explode(_references: object) -> tuple[CriterionSpec, ...]:
        raise CriteriaDefinitionError("criterion X: is deferred without a written trigger")

    monkeypatch.setattr("tools.build_phase0_proof.phase0_criteria", explode)
    exit_code = main(["--output-dir", str(tmp_path / "broken")])
    assert exit_code == 1
    assert "written trigger" in capsys.readouterr().err


def test_every_phase0_criterion_and_the_related_deferral_are_present() -> None:
    references = discover_fixtures(PROJECT_ROOT)
    assert [check.number for check in phase0_criteria(references)] == [
        str(index) for index in range(1, 14)
    ]
    assert [check.number for check in related_deferrals(references)] == ["D1"]


def rendered_matrix(verification: Verification) -> str:
    references = discover_fixtures(PROJECT_ROOT)
    return render_matrix(phase0_criteria(references), related_deferrals(references), verification)


def test_the_matrix_reports_gaps_before_the_per_check_detail(
    verification: Verification,
) -> None:
    checks = recorded_checks(discover_fixtures(PROJECT_ROOT))
    rendered = rendered_matrix(verification)
    detail_headings = rendered.index("## Checks")
    gapped = [check for check in checks if check.gaps]
    if not gapped:
        assert "## Gaps" not in rendered
        return
    assert rendered.index("## Gaps") < detail_headings
    for check in gapped:
        assert rendered.index(check.gaps[0]) < detail_headings


def test_a_check_with_no_proving_test_says_so_in_the_matrix(
    verification: Verification,
) -> None:
    checks = recorded_checks(discover_fixtures(PROJECT_ROOT))
    rendered = rendered_matrix(verification)
    unproved = [check for check in checks if not check.proving_node_ids]
    assert unproved
    assert rendered.count("No test proves this check.") == len(unproved)


def test_every_deferral_shows_both_its_reason_and_its_trigger(
    verification: Verification,
) -> None:
    checks = recorded_checks(discover_fixtures(PROJECT_ROOT))
    deferred = [check for check in checks if check.status is CriterionStatus.DEFERRED]
    assert [check.number for check in deferred] == ["9", "10", "D1"]
    rendered = rendered_matrix(verification)
    assert "## Deferred by explicit decision" in rendered
    for check in deferred:
        assert check.deferral_reason is not None
        assert check.deferral_trigger is not None
        assert rendered.index(check.deferral_reason) < rendered.index("## Checks")
        assert check.deferral_trigger in rendered


def test_the_matrix_uses_only_the_three_statuses(verification: Verification) -> None:
    checks = recorded_checks(discover_fixtures(PROJECT_ROOT))
    rendered = rendered_matrix(verification)
    assert "PARTIAL" not in rendered
    permitted = {status.name for status in CriterionStatus}
    assert permitted == {"COVERED", "DEFERRED", "GAP"}
    for check in checks:
        assert f"| {check.number} | {check.status.name} |" in rendered
    assert "| 9 | DEFERRED |" in rendered
    assert "| 10 | DEFERRED |" in rendered
    assert "| D1 | DEFERRED |" in rendered


def test_the_matrix_names_the_single_definition_it_was_rendered_from(
    verification: Verification,
) -> None:
    assert "src/edullm_platform/phase0_criteria.py" in rendered_matrix(verification)


def test_the_index_reports_the_gate_verdict_that_matches_the_recorded_gaps(
    first_bundle: dict[str, str],
) -> None:
    index = first_bundle["README.md"]
    checks = recorded_checks(discover_fixtures(PROJECT_ROOT))
    gap_numbers = [check.number for check in checks if check.status is CriterionStatus.GAP]
    deferred = [check.number for check in checks if check.status is CriterionStatus.DEFERRED]
    if gap_numbers:
        assert "`tools/validate_phase0.py` exits 1 against this tree" in index
        for number in gap_numbers:
            assert f"criterion {number} is a GAP" in index or f"({number}" in index
    else:
        assert "`tools/validate_phase0.py` exits 0 against this tree" in index
        assert "exits 1 against this tree" not in index
    assert f"criteria GAP (each one fails the gate) | {_count_naming(gap_numbers)}" in index
    assert f"criteria DEFERRED | {_count_naming([n for n in deferred if n.isdigit()])}" in index


def test_known_limitations_name_the_unprovisioned_compute_and_empty_team_bindings() -> None:
    limitations = known_limitations(PROJECT_ROOT)
    assert any("No compute profile is provisioned" in item for item in limitations)
    assert any("Team bindings are empty" in item for item in limitations)


def test_main_writes_a_complete_bundle_to_a_chosen_directory(tmp_path: Path) -> None:
    output_dir = tmp_path / "bundle"
    exit_code = main(
        [
            "--output-dir",
            str(output_dir),
            "--generated-at",
            FIRST_INSTANT.isoformat(),
        ]
    )
    assert exit_code == 0
    assert set(read_bundle(output_dir)) == set(BUNDLE_FILENAMES)
    assert load_recorded_goldens(goldens_path(output_dir)) == compute_goldens(
        PROJECT_ROOT, discover_fixtures(PROJECT_ROOT)
    )


def test_main_reports_a_drifted_golden_digest_as_a_failure(
    tmp_path: Path,
    first_bundle: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    directory, fixture = tampered_bundle(tmp_path / "main-drift", first_bundle)
    exit_code = main(["--output-dir", str(directory), "--generated-at", FIRST_INSTANT.isoformat()])
    assert exit_code == 1
    assert fixture in capsys.readouterr().err
