from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from edullm_platform.config import load_yaml
from edullm_platform.contracts.workload import CostInputs, WorkloadCatalog


def catalog_payload() -> dict[str, object]:
    return {
        "compute_profiles": [
            {
                "name": "cpu-32vcpu",
                "accelerator": "cpu",
                "nodes": 1,
                "hourly_rate_usd": "1.428",
                "pricing_source": "test",
                "pricing_observed_at": "2026-07-24",
            },
            {
                "name": "gpu-4xa10g",
                "accelerator": "gpu",
                "nodes": 1,
                "hourly_rate_usd": "5.672",
                "pricing_source": "test",
                "pricing_observed_at": "2026-07-24",
            },
        ],
        "workloads": [
            {
                "name": "dolma-tokenize-smoke",
                "repository": "dolma",
                "compute_profile": "cpu-32vcpu",
                "maximum_runtime_hours": "2",
                "maximum_attempts": 1,
                "checkpoint": None,
            },
            {
                "name": "olmo-core-train-smoke",
                "repository": "OLMo-core",
                "compute_profile": "gpu-4xa10g",
                "maximum_runtime_hours": "1",
                "maximum_attempts": 1,
                "checkpoint": {
                    "interval_minutes": 30,
                    "destination_prefix": "s3://edullm-checkpoints/runs/",
                    "resume_required": False,
                },
            },
        ],
    }


def assert_validation_error(
    error: ValidationError,
    *,
    error_type: str,
    message_fragment: str | None = None,
) -> None:
    matching_errors = [item for item in error.errors() if item["type"] == error_type]
    assert matching_errors, f"expected error type {error_type!r}, got {error.errors()}"
    if message_fragment is not None:
        assert any(message_fragment in item["msg"] for item in matching_errors), (
            f"expected {message_fragment!r} in {error_type!r} messages, "
            f"got {[item['msg'] for item in matching_errors]}"
        )


def test_cost_estimate_is_deterministic() -> None:
    inputs = CostInputs(
        hourly_rate_usd=Decimal("12.25"),
        nodes=2,
        maximum_runtime_hours=Decimal(6),
        maximum_attempts=2,
    )
    assert inputs.maximum_compute_cost_usd == Decimal("294.00")


@pytest.mark.parametrize(
    ("probe_name", "payload"),
    [
        (
            "reviewer original",
            {
                "hourly_rate_usd": "5.672",
                "nodes": 10**13,
                "maximum_runtime_hours": "24",
                "maximum_attempts": 10**13,
            },
        ),
        (
            "max under new bounds",
            {
                "hourly_rate_usd": "9" * 28,
                "nodes": 1_000_000,
                "maximum_runtime_hours": "9" * 28,
                "maximum_attempts": 1_000_000,
            },
        ),
        (
            "modest overflow probe",
            {
                "hourly_rate_usd": "9" * 20,
                "nodes": 10**13,
                "maximum_runtime_hours": "9" * 20,
                "maximum_attempts": 10**13,
            },
        ),
    ],
)
def test_cost_inputs_reject_overflow_product_probes(
    probe_name: str,
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        CostInputs.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="maximum compute cost exceeds representable precision",
    )


def test_valid_cost_inputs_canonical_json_bytes_does_not_raise() -> None:
    from edullm_platform.canonical import canonical_json_bytes

    inputs = CostInputs(
        hourly_rate_usd=Decimal("12.25"),
        nodes=2,
        maximum_runtime_hours=Decimal(6),
        maximum_attempts=2,
    )
    encoded = canonical_json_bytes(inputs)
    assert b'"maximum_compute_cost_usd":"294.00"' in encoded


def test_catalog_requires_cpu_and_gpu_workload_representatives() -> None:
    with pytest.raises(ValidationError) as exc_info:
        WorkloadCatalog.model_validate(
            {
                "compute_profiles": [
                    {
                        "name": "cpu-small",
                        "accelerator": "cpu",
                        "nodes": 1,
                        "hourly_rate_usd": "1.00",
                        "pricing_source": "test",
                        "pricing_observed_at": "2026-07-24",
                    },
                    {
                        "name": "gpu-small",
                        "accelerator": "gpu",
                        "nodes": 1,
                        "hourly_rate_usd": "2.00",
                        "pricing_source": "test",
                        "pricing_observed_at": "2026-07-24",
                    },
                ],
                "workloads": [
                    {
                        "name": "tokenize-smoke",
                        "repository": "dolma",
                        "compute_profile": "cpu-small",
                        "maximum_runtime_hours": "2",
                        "maximum_attempts": 1,
                        "checkpoint": None,
                    },
                    {
                        "name": "prep-smoke",
                        "repository": "dolma",
                        "compute_profile": "cpu-small",
                        "maximum_runtime_hours": "1",
                        "maximum_attempts": 1,
                        "checkpoint": None,
                    },
                ],
            }
        )
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="representative CPU and GPU workloads are required",
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "cpu_only_workloads",
        "duplicate_profile_name",
        "duplicate_workload_name",
        "unknown_compute_profile",
        "retryable_without_checkpoint",
        "invalid_destination_prefix",
    ],
)
def test_catalog_rejects_invalid_binding_rules(mutation: str) -> None:
    payload = catalog_payload()
    if mutation == "cpu_only_workloads":
        workloads = list(payload["workloads"])  # type: ignore[arg-type]
        workloads[1] = {
            **workloads[1],
            "compute_profile": "cpu-32vcpu",
        }
        payload["workloads"] = workloads
        expected_type = "value_error"
        expected_message = "representative CPU and GPU workloads are required"
    elif mutation == "duplicate_profile_name":
        profiles = list(payload["compute_profiles"])  # type: ignore[arg-type]
        profiles[1] = {**profiles[1], "name": profiles[0]["name"]}
        payload["compute_profiles"] = profiles
        expected_type = "value_error"
        expected_message = "compute profile names must be unique"
    elif mutation == "duplicate_workload_name":
        workloads = list(payload["workloads"])  # type: ignore[arg-type]
        workloads[1] = {**workloads[1], "name": workloads[0]["name"]}
        payload["workloads"] = workloads
        expected_type = "value_error"
        expected_message = "workload names must be unique"
    elif mutation == "unknown_compute_profile":
        workloads = list(payload["workloads"])  # type: ignore[arg-type]
        workloads[0] = {**workloads[0], "compute_profile": "missing-profile"}
        payload["workloads"] = workloads
        expected_type = "value_error"
        expected_message = "unknown compute profile: missing-profile"
    elif mutation == "retryable_without_checkpoint":
        workloads = list(payload["workloads"])  # type: ignore[arg-type]
        workloads[0] = {
            **workloads[0],
            "maximum_attempts": 2,
            "checkpoint": None,
        }
        payload["workloads"] = workloads
        expected_type = "value_error"
        expected_message = "retryable workloads require a checkpoint contract"
    else:
        workloads = list(payload["workloads"])  # type: ignore[arg-type]
        checkpoint = dict(workloads[1]["checkpoint"])  # type: ignore[index]
        checkpoint["destination_prefix"] = "s3://edullm-checkpoints"
        workloads[1] = {**workloads[1], "checkpoint": checkpoint}
        payload["workloads"] = workloads
        expected_type = "string_pattern_mismatch"
        expected_message = None

    with pytest.raises(ValidationError) as exc_info:
        WorkloadCatalog.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type=expected_type,
        message_fragment=expected_message,
    )


def test_workload_catalog_yaml_validates_against_contract() -> None:
    project_root = Path(__file__).resolve().parents[1]
    config_path = project_root / "config" / "workload-catalog.yaml"
    catalog = load_yaml(config_path, WorkloadCatalog)
    assert len(catalog.compute_profiles) == 2
    assert len(catalog.workloads) == 2
    profile_by_name = {profile.name: profile for profile in catalog.compute_profiles}
    cpu_workload = next(
        workload for workload in catalog.workloads if workload.name == "dolma-tokenize-smoke"
    )
    gpu_workload = next(
        workload for workload in catalog.workloads if workload.name == "olmo-core-train-smoke"
    )
    cpu_profile = profile_by_name[cpu_workload.compute_profile]
    gpu_profile = profile_by_name[gpu_workload.compute_profile]
    cpu_cost = CostInputs(
        hourly_rate_usd=cpu_profile.hourly_rate_usd,
        nodes=cpu_profile.nodes,
        maximum_runtime_hours=cpu_workload.maximum_runtime_hours,
        maximum_attempts=cpu_workload.maximum_attempts,
    )
    gpu_cost = CostInputs(
        hourly_rate_usd=gpu_profile.hourly_rate_usd,
        nodes=gpu_profile.nodes,
        maximum_runtime_hours=gpu_workload.maximum_runtime_hours,
        maximum_attempts=gpu_workload.maximum_attempts,
    )
    assert cpu_cost.maximum_compute_cost_usd == Decimal("2.86")
    assert gpu_cost.maximum_compute_cost_usd == Decimal("5.67")
