import re
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from edullm_platform.config import load_yaml
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.workload import CostInputs, WorkloadCatalog

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_FIXTURES_DIR = PROJECT_ROOT / "fixtures" / "manifests"

COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

REPRESENTATIVE_MANIFEST_FILENAMES = tuple(
    sorted(path.name for path in MANIFEST_FIXTURES_DIR.glob("*.yaml"))
)

REPRESENTATIVE_MANIFEST_COSTS = {
    "cpu-routine.yaml": Decimal("2.86"),
    "gpu-routine.yaml": Decimal("5.67"),
    "gpu-exception.yaml": Decimal("73.74"),
}


def load_representative_manifest(filename: str) -> RunManifest:
    return load_yaml(MANIFEST_FIXTURES_DIR / filename, RunManifest)


def load_workload_catalog() -> WorkloadCatalog:
    return load_yaml(PROJECT_ROOT / "config" / "workload-catalog.yaml", WorkloadCatalog)


def manifest_has_immutable_revision(manifest: RunManifest) -> bool:
    return COMMIT_SHA_PATTERN.fullmatch(manifest.commit_sha) is not None


def manifest_has_immutable_image(manifest: RunManifest) -> bool:
    return IMAGE_DIGEST_PATTERN.fullmatch(manifest.image_digest) is not None


def is_compute_profile_registered(manifest: RunManifest, catalog: WorkloadCatalog) -> bool:
    registered_names = {profile.name for profile in catalog.compute_profiles}
    return manifest.compute_profile in registered_names


def is_workload_profile_registered(manifest: RunManifest, catalog: WorkloadCatalog) -> bool:
    registered_names = {workload.name for workload in catalog.workloads}
    return manifest.workload_profile in registered_names


def compute_manifest_maximum_cost(manifest: RunManifest, catalog: WorkloadCatalog) -> Decimal:
    if not is_compute_profile_registered(manifest, catalog):
        raise AssertionError(f"unregistered compute profile: {manifest.compute_profile!r}")
    profile_by_name = {profile.name: profile for profile in catalog.compute_profiles}
    profile = profile_by_name[manifest.compute_profile]
    return CostInputs(
        hourly_rate_usd=profile.hourly_rate_usd,
        nodes=profile.nodes,
        maximum_runtime_hours=manifest.maximum_runtime_hours,
        maximum_attempts=manifest.maximum_attempts,
    ).maximum_compute_cost_usd


def manifest_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "repository": "OLMo-core",
        "commit_sha": "a" * 40,
        "image_digest": "sha256:" + "b" * 64,
        "dataset_release": "dolma-2026-07",
        "command": ["python", "-m", "train"],
        "team": "modeling",
        "wandb_project": "olmo",
        "workload_profile": "gpu-training-smoke",
        "compute_profile": "gpu-single-node",
        "maximum_runtime_hours": "6",
        "maximum_attempts": 2,
        "checkpoint": {
            "interval_minutes": 30,
            "destination_prefix": "s3://edullm-checkpoints/runs/",
            "resume_required": True,
        },
    }


def assert_validation_error(
    error: ValidationError,
    *,
    error_type: str,
    loc: tuple[str | int, ...],
    message_fragment: str | None = None,
) -> None:
    matching_errors = [
        item for item in error.errors() if item["type"] == error_type and item["loc"] == loc
    ]
    assert matching_errors, (
        f"expected error type {error_type!r} at loc {loc!r}, got {error.errors()}"
    )
    if message_fragment is not None:
        assert any(message_fragment in item["msg"] for item in matching_errors), (
            f"expected {message_fragment!r} in {error_type!r} messages at {loc!r}, "
            f"got {[item['msg'] for item in matching_errors]}"
        )


def test_manifest_validates_complete_payload() -> None:
    manifest = RunManifest.model_validate(manifest_payload())
    assert manifest.repository == "OLMo-core"
    assert manifest.commit_sha == "a" * 40
    assert manifest.command == ("python", "-m", "train")
    assert manifest.maximum_runtime_hours == Decimal(6)
    assert manifest.maximum_attempts == 2
    assert manifest.checkpoint is not None
    assert manifest.checkpoint.resume_required is True


def test_manifest_rejects_mutable_commit_sha() -> None:
    payload = manifest_payload()
    payload["commit_sha"] = "main"
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("commit_sha",),
    )


def test_manifest_rejects_mutable_image_digest() -> None:
    payload = manifest_payload()
    payload["image_digest"] = "latest"
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("image_digest",),
    )


def test_manifest_rejects_uppercase_commit_sha() -> None:
    payload = manifest_payload()
    payload["commit_sha"] = "A" * 40
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("commit_sha",),
    )


def test_manifest_rejects_short_commit_sha() -> None:
    payload = manifest_payload()
    payload["commit_sha"] = "a" * 7
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("commit_sha",),
    )


def test_manifest_rejects_commit_sha_with_trailing_suffix() -> None:
    payload = manifest_payload()
    payload["commit_sha"] = ("a" * 40) + "extra"
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("commit_sha",),
    )


def test_manifest_rejects_image_digest_with_trailing_tag() -> None:
    payload = manifest_payload()
    payload["image_digest"] = "sha256:" + ("b" * 64) + ":latest"
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("image_digest",),
    )


def test_manifest_rejects_non_sha256_image_digest() -> None:
    payload = manifest_payload()
    payload["image_digest"] = "sha512:" + ("b" * 64)
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("image_digest",),
    )


def test_manifest_rejects_bare_image_digest_without_algorithm_prefix() -> None:
    payload = manifest_payload()
    payload["image_digest"] = "b" * 64
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("image_digest",),
    )


def test_manifest_rejects_unordered_command() -> None:
    payload = manifest_payload()
    payload["command"] = {"python", "-m", "train"}
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=("command",),
        message_fragment="ordered sequences must be provided as a list or tuple",
    )


def test_manifest_rejects_retryable_without_checkpoint() -> None:
    payload = manifest_payload()
    payload["checkpoint"] = None
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=(),
        message_fragment="retryable workloads require a checkpoint contract",
    )


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        ("schema_version", 2, "literal_error"),
        ("repository", "", "string_too_short"),
        ("dataset_release", "", "string_too_short"),
        ("team", "", "string_too_short"),
        ("wandb_project", "", "string_too_short"),
        ("workload_profile", "", "string_too_short"),
        ("compute_profile", "", "string_too_short"),
        ("maximum_attempts", 0, "greater_than_equal"),
    ],
)
def test_manifest_rejects_invalid_field_values(
    field: str,
    value: object,
    error_type: str,
) -> None:
    payload = manifest_payload()
    payload[field] = value
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type=error_type,
        loc=(field,),
    )


def test_manifest_rejects_empty_command() -> None:
    payload = manifest_payload()
    payload["command"] = []
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="too_short",
        loc=("command",),
    )


def test_manifest_rejects_non_decimal_runtime_hours() -> None:
    payload = manifest_payload()
    payload["maximum_runtime_hours"] = 6
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=("maximum_runtime_hours",),
        message_fragment="decimal values must be non-negative base-10 strings",
    )


def test_manifest_rejects_zero_runtime_hours() -> None:
    payload = manifest_payload()
    payload["maximum_runtime_hours"] = "0"
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="greater_than",
        loc=("maximum_runtime_hours",),
    )


def test_manifest_allows_single_attempt_without_checkpoint() -> None:
    payload = manifest_payload()
    payload["maximum_attempts"] = 1
    payload["checkpoint"] = None
    manifest = RunManifest.model_validate(payload)
    assert manifest.checkpoint is None
    assert manifest.maximum_attempts == 1


def test_every_manifest_fixture_has_reviewed_cost_expectation() -> None:
    assert set(REPRESENTATIVE_MANIFEST_FILENAMES) == set(REPRESENTATIVE_MANIFEST_COSTS)


@pytest.mark.parametrize("filename", REPRESENTATIVE_MANIFEST_FILENAMES)
def test_representative_manifest_validates(filename: str) -> None:
    manifest = load_representative_manifest(filename)
    assert manifest.schema_version == 1


@pytest.mark.parametrize("filename", REPRESENTATIVE_MANIFEST_FILENAMES)
def test_representative_manifest_profiles_are_registered(filename: str) -> None:
    manifest = load_representative_manifest(filename)
    catalog = load_workload_catalog()
    assert is_compute_profile_registered(manifest, catalog), (
        f"{filename}: compute profile {manifest.compute_profile!r} is not in the catalog"
    )
    assert is_workload_profile_registered(manifest, catalog), (
        f"{filename}: workload profile {manifest.workload_profile!r} is not in the catalog"
    )


@pytest.mark.parametrize(
    ("filename", "expected_cost_usd"),
    list(REPRESENTATIVE_MANIFEST_COSTS.items()),
    ids=list(REPRESENTATIVE_MANIFEST_COSTS.keys()),
)
def test_representative_manifest_maximum_cost(
    filename: str,
    expected_cost_usd: Decimal,
) -> None:
    manifest = load_representative_manifest(filename)
    catalog = load_workload_catalog()
    assert compute_manifest_maximum_cost(manifest, catalog) == expected_cost_usd
