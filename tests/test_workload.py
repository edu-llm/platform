from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from edullm_platform.config import load_yaml
from edullm_platform.contracts.workload import (
    CheckpointContract,
    ComputeProfileResolutionError,
    CostInputs,
    UnprovisionedComputeProfileError,
    UnregisteredComputeProfileError,
    WorkloadCatalog,
    resolve_compute_profile_for_execution,
)

COMPLIANT_DESTINATION_PREFIX = "s3://sbsandbox-intern-edullm-checkpoints/runs/"


def catalog_payload() -> dict[str, object]:
    return {
        "compute_profiles": [
            {
                "name": "cpu-32vcpu",
                "instance_type": "c7i.8xlarge",
                "accelerator": "cpu",
                "nodes": 1,
                "hourly_rate_usd": "1.428",
                "pricing_source": "test",
                "pricing_observed_at": "2026-07-24",
                "provisioned": False,
            },
            {
                "name": "gpu-4xa10g",
                "instance_type": "g5.12xlarge",
                "accelerator": "gpu",
                "nodes": 1,
                "hourly_rate_usd": "5.672",
                "pricing_source": "test",
                "pricing_observed_at": "2026-07-24",
                "provisioned": False,
            },
        ],
        "workloads": [
            {
                "name": "dolma-tokenize",
                "repository": "dolma",
                "compute_profile": "cpu-32vcpu",
                "maximum_runtime_hours": "2",
                "maximum_attempts": 1,
                "checkpoint": None,
            },
            {
                "name": "olmo-core-train-4gpu",
                "repository": "OLMo-core",
                "compute_profile": "gpu-4xa10g",
                "maximum_runtime_hours": "1",
                "maximum_attempts": 1,
                "checkpoint": {
                    "interval_minutes": 30,
                    "destination_prefix": COMPLIANT_DESTINATION_PREFIX,
                    "resume_required": False,
                },
            },
        ],
    }


def checkpoint_payload(destination_prefix: str) -> dict[str, object]:
    return {
        "interval_minutes": 30,
        "destination_prefix": destination_prefix,
        "resume_required": False,
    }


def catalog_with_provisioned(*provisioned_names: str) -> WorkloadCatalog:
    payload = catalog_payload()
    payload["compute_profiles"] = [
        {**profile, "provisioned": profile["name"] in provisioned_names}
        for profile in payload["compute_profiles"]  # type: ignore[union-attr]
    ]
    return WorkloadCatalog.model_validate(payload)


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
                        "instance_type": "c7i.xlarge",
                        "accelerator": "cpu",
                        "nodes": 1,
                        "hourly_rate_usd": "1.00",
                        "pricing_source": "test",
                        "pricing_observed_at": "2026-07-24",
                        "provisioned": False,
                    },
                    {
                        "name": "gpu-small",
                        "instance_type": "g5.xlarge",
                        "accelerator": "gpu",
                        "nodes": 1,
                        "hourly_rate_usd": "2.00",
                        "pricing_source": "test",
                        "pricing_observed_at": "2026-07-24",
                        "provisioned": False,
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
    # Thirteen since gpu-8xa10g, the g5.48xlarge the catalog had never priced. Same tripwire
    # role as the workload count below: a profile arriving without a deliberate edit.
    assert len(catalog.compute_profiles) == 13
    # Eight since MixLaw onboarded edullm-p1. The count is the tripwire for a workload
    # appearing without a deliberate edit, so it moves with the edit and not before.
    assert len(catalog.workloads) == 8
    # The CPU workload Phase 3 runs. It names OLMo-core, which was the only registered
    # repository with a published image when this was written; dolma-tokenize is the same
    # shape against a repository that still has neither.
    runnable_cpu = next(
        workload for workload in catalog.workloads if workload.name == "olmo-core-check-cpu"
    )
    assert runnable_cpu.repository == "OLMo-core"
    assert runnable_cpu.compute_profile == "cpu-32vcpu"
    profile_by_name = {profile.name: profile for profile in catalog.compute_profiles}
    cpu_workload = next(
        workload for workload in catalog.workloads if workload.name == "dolma-tokenize"
    )
    gpu_workload = next(
        workload for workload in catalog.workloads if workload.name == "olmo-core-train-4gpu"
    )
    mixlaw = next(
        workload
        for workload in catalog.workloads
        if workload.name == "mixlaw-validation-370m-8xa100"
    )
    assert mixlaw.repository == "edullm-p1"
    assert mixlaw.compute_profile == "gpu-8xa100"
    assert mixlaw.maximum_runtime_hours == Decimal("4")
    assert mixlaw.maximum_attempts == 1
    assert mixlaw.checkpoint is not None
    assert mixlaw.checkpoint.resume_required is False
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
    # Was 5.67, which was one hour and one attempt on four A10G. Both bounds were raised to
    # olmo-core-train-1gpu's, so this is twelve hours across two attempts at $5.672.
    assert gpu_cost.maximum_compute_cost_usd == Decimal("136.13")


def test_catalog_rejects_duplicate_profile_name_when_every_other_field_differs() -> None:
    payload = catalog_payload()
    profiles = list(payload["compute_profiles"])  # type: ignore[arg-type]
    profiles.append(
        {
            "name": "gpu-4xa10g",
            "instance_type": "p5.48xlarge",
            "accelerator": "gpu",
            "nodes": 4,
            "hourly_rate_usd": "55.0400",
            "pricing_source": "other",
            "pricing_observed_at": "2026-07-25",
            "provisioned": True,
        }
    )
    payload["compute_profiles"] = profiles
    with pytest.raises(ValidationError) as exc_info:
        WorkloadCatalog.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="compute profile names must be unique",
    )


def test_checkpoint_accepts_sandbox_owned_destination_prefix() -> None:
    checkpoint = CheckpointContract.model_validate(
        checkpoint_payload(COMPLIANT_DESTINATION_PREFIX)
    )
    assert checkpoint.destination_prefix == COMPLIANT_DESTINATION_PREFIX


@pytest.mark.parametrize(
    "destination_prefix",
    [
        "s3://edullm-checkpoints/runs/",
        "s3://sbsandbox-intern/runs/",
        "s3://not-sbsandbox-intern-checkpoints/runs/",
        "s3://SBSANDBOX-INTERN-checkpoints/runs/",
        "s3://sbsandbox-intern-checkpoints/runs",
        "s3://sbsandbox-intern-checkpoints/",
        "s3://sbsandbox-intern-/runs/",
        "s3://sbsandbox-intern-checkpoints-/runs/",
    ],
)
def test_checkpoint_rejects_destination_prefix_outside_sandbox_bucket_namespace(
    destination_prefix: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        CheckpointContract.model_validate(checkpoint_payload(destination_prefix))
    assert_validation_error(exc_info.value, error_type="string_pattern_mismatch")


def test_catalog_rejects_workload_checkpoint_outside_sandbox_bucket_namespace() -> None:
    payload = catalog_payload()
    workloads = list(payload["workloads"])  # type: ignore[arg-type]
    workloads[1] = {
        **workloads[1],
        "checkpoint": checkpoint_payload("s3://edullm-checkpoints/runs/"),
    }
    payload["workloads"] = workloads
    with pytest.raises(ValidationError) as exc_info:
        WorkloadCatalog.model_validate(payload)
    assert_validation_error(exc_info.value, error_type="string_pattern_mismatch")


def test_provisioned_profile_resolves_for_execution() -> None:
    catalog = catalog_with_provisioned("gpu-4xa10g")
    profile = resolve_compute_profile_for_execution(catalog, "gpu-4xa10g")
    assert profile.name == "gpu-4xa10g"
    assert profile.provisioned is True


def test_resolving_unprovisioned_profile_reports_missing_capacity_not_missing_profile() -> None:
    catalog = catalog_with_provisioned()
    with pytest.raises(UnprovisionedComputeProfileError) as exc_info:
        resolve_compute_profile_for_execution(catalog, "gpu-4xa10g")
    assert exc_info.value.reason_code == "unprovisioned_compute_profile"
    assert isinstance(exc_info.value, ComputeProfileResolutionError)
    assert not isinstance(exc_info.value, UnregisteredComputeProfileError)
    assert "g5.12xlarge" in str(exc_info.value)


def test_resolving_unknown_profile_reports_unregistered_profile() -> None:
    catalog = catalog_with_provisioned("gpu-4xa10g")
    with pytest.raises(UnregisteredComputeProfileError) as exc_info:
        resolve_compute_profile_for_execution(catalog, "gpu-8xh100")
    assert exc_info.value.reason_code == "unregistered_compute_profile"
    assert isinstance(exc_info.value, ComputeProfileResolutionError)
    assert not isinstance(exc_info.value, UnprovisionedComputeProfileError)


def test_unprovisioned_and_unregistered_resolution_failures_are_distinguishable() -> None:
    catalog = catalog_with_provisioned()
    with pytest.raises(UnregisteredComputeProfileError) as unregistered:
        resolve_compute_profile_for_execution(catalog, "gpu-8xh100")
    with pytest.raises(UnprovisionedComputeProfileError) as unprovisioned:
        resolve_compute_profile_for_execution(catalog, "gpu-4xa10g")
    assert type(unregistered.value) is not type(unprovisioned.value)
    assert unregistered.value.reason_code != unprovisioned.value.reason_code
    assert str(unregistered.value) != str(unprovisioned.value)


def test_resolution_failures_remain_value_errors() -> None:
    catalog = catalog_with_provisioned()
    for profile_name in ("gpu-8xh100", "gpu-4xa10g"):
        with pytest.raises(ValueError):
            resolve_compute_profile_for_execution(catalog, profile_name)
