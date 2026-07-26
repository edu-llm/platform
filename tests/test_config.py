import subprocess
import tempfile
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from edullm_platform.contracts.base import ContractModel, StrictDecimal


class ExampleContract(ContractModel):
    count: int


class DecimalContract(ContractModel):
    amount: StrictDecimal


def test_contracts_are_strict_and_forbid_extra_fields() -> None:
    try:
        ExampleContract.model_validate({"count": "1", "extra": True})
    except ValidationError as error:
        error_types = {item["type"] for item in error.errors()}
        assert "int_type" in error_types
        assert "extra_forbidden" in error_types
    else:
        raise AssertionError("invalid contract unexpectedly validated")


@pytest.mark.parametrize(
    "value",
    [
        "0",
        "0.25",
        Decimal("1.00"),
    ],
)
def test_strict_decimal_accepts_valid_values(value: object) -> None:
    contract = DecimalContract.model_validate({"amount": value})
    assert isinstance(contract.amount, Decimal)


def test_strict_decimal_returns_decimal_in_python_dump() -> None:
    contract = DecimalContract.model_validate({"amount": "6.0"})
    dumped = contract.model_dump()
    assert dumped["amount"] == Decimal(6)
    assert isinstance(dumped["amount"], Decimal)
    json_dumped = contract.model_dump(mode="json")
    assert json_dumped["amount"] == "6"
    assert isinstance(json_dumped["amount"], str)


@pytest.mark.parametrize(
    "value",
    [
        0.25,
        "1e2",
        "-1",
        "+1",
        " 1",
        "1 ",
        "01",
        "00.5",
    ],
)
def test_strict_decimal_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValidationError):
        DecimalContract.model_validate({"amount": value})


@pytest.mark.parametrize(
    "value",
    [
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ],
)
def test_strict_decimal_rejects_non_finite_values(value: Decimal) -> None:
    with pytest.raises(ValidationError) as exc_info:
        DecimalContract.model_validate({"amount": value})
    matching_errors = [
        item
        for item in exc_info.value.errors()
        if item["type"] == "value_error" and item["loc"] == ("amount",)
    ]
    assert matching_errors
    assert any("decimal values must be finite" in item["msg"] for item in matching_errors)


def test_strict_decimal_collapses_negative_zero() -> None:
    contract = DecimalContract.model_validate({"amount": Decimal("-0")})
    assert contract.amount == Decimal(0)
    assert contract.amount.is_signed() is False


def test_strict_decimal_rejects_over_28_digits() -> None:
    with pytest.raises(ValidationError) as exc_info:
        DecimalContract.model_validate({"amount": "1" + ("0" * 28)})
    matching_errors = [
        item
        for item in exc_info.value.errors()
        if item["type"] == "value_error" and item["loc"] == ("amount",)
    ]
    assert matching_errors
    assert any("decimal values must not exceed 28 digits" in item["msg"] for item in matching_errors)


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
            "destination_prefix": "s3://sbsandbox-intern-edullm-checkpoints/runs/",
            "resume_required": True,
        },
    }


def inventory_payload() -> dict[str, object]:
    return {
        "admins": ["philote-dev", "BritishAmericqn"],
        "team_leads": [
            "philote-dev",
            "ericrcwu001",
            "alsy7009",
            "meric233",
            "syz2026",
            "gorpyshortlegs",
            "hiyasvyas",
            "pianomaster99",
        ],
        "members": [
            {"github_login": "philote-dev", "display_name": "Example Admin"},
            {"github_login": "BritishAmericqn"},
            {"github_login": "ericrcwu001"},
            {"github_login": "alsy7009"},
            {"github_login": "meric233"},
            {"github_login": "syz2026"},
            {"github_login": "gorpyshortlegs"},
            {"github_login": "hiyasvyas"},
            {"github_login": "pianomaster99"},
        ],
        "pilot_repositories": ["OLMo-core", "dolma"],
    }


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
                    "destination_prefix": "s3://sbsandbox-intern-edullm-checkpoints/runs/",
                    "resume_required": False,
                },
            },
        ],
    }


def policy_payload() -> dict[str, object]:
    return {
        "thresholds": {
            "routine_maximum_cost_usd": "500",
            "routine_maximum_runtime_hours": "12",
            "routine_maximum_attempts": 2,
            "routine_maximum_fanout_size": 64,
            "routine_maximum_parallelism": 8,
        },
        "approval_scope": "organization",
        "routine_approver_role": "team_lead",
        "exception_approver_roles": ["platform_admin"],
        "denied_outright": [
            "unregistered_repository",
            "unregistered_dataset",
            "unregistered_compute_profile",
            "mutable_repository_revision",
            "mutable_image_reference",
        ],
    }


@pytest.mark.parametrize(
    ("model", "field", "payload_factory", "unordered_value"),
    [
        (
            "RunManifest",
            "command",
            "manifest",
            {"python", "-m", "train"},
        ),
        (
            "OrganizationInventory",
            "admins",
            "inventory",
            {"philote-dev", "BritishAmericqn"},
        ),
        (
            "OrganizationInventory",
            "team_leads",
            "inventory",
            {
                "philote-dev",
                "ericrcwu001",
                "alsy7009",
                "meric233",
                "syz2026",
                "gorpyshortlegs",
                "hiyasvyas",
                "pianomaster99",
            },
        ),
        (
            "OrganizationInventory",
            "members",
            "inventory",
            "iter",
        ),
        (
            "OrganizationInventory",
            "pilot_repositories",
            "inventory",
            {"OLMo-core", "dolma"},
        ),
        (
            "WorkloadCatalog",
            "compute_profiles",
            "catalog",
            "iter",
        ),
        (
            "WorkloadCatalog",
            "workloads",
            "catalog",
            "iter",
        ),
        (
            "ApprovalPolicy",
            "exception_approver_roles",
            "policy",
            {"platform_admin"},
        ),
        (
            "ApprovalPolicy",
            "denied_outright",
            "policy",
            {"unregistered_repository", "mutable_image_reference"},
        ),
    ],
)
def test_sequence_fields_reject_unordered_containers(
    model: str,
    field: str,
    payload_factory: str,
    unordered_value: object | str,
) -> None:
    from edullm_platform.contracts.inventory import OrganizationInventory
    from edullm_platform.contracts.manifest import RunManifest
    from edullm_platform.contracts.policy import ApprovalPolicy
    from edullm_platform.contracts.workload import WorkloadCatalog

    factories = {
        "manifest": manifest_payload,
        "inventory": inventory_payload,
        "catalog": catalog_payload,
        "policy": policy_payload,
    }
    models = {
        "RunManifest": RunManifest,
        "OrganizationInventory": OrganizationInventory,
        "WorkloadCatalog": WorkloadCatalog,
        "ApprovalPolicy": ApprovalPolicy,
    }

    payload = dict(factories[payload_factory]())
    if unordered_value == "iter":
        payload[field] = iter(list(payload[field]))  # type: ignore[arg-type]
    else:
        payload[field] = unordered_value

    with pytest.raises(ValidationError) as exc_info:
        models[model].model_validate(payload)
    matching_errors = [
        item
        for item in exc_info.value.errors()
        if item["type"] == "value_error" and item["loc"] == (field,)
    ]
    assert matching_errors
    assert any(
        "ordered sequences must be provided as a list or tuple" in item["msg"]
        for item in matching_errors
    )


def test_py_typed_is_present_in_built_wheel() -> None:
    project_root = Path(__file__).resolve().parents[1]

    with tempfile.TemporaryDirectory() as temp_dir:
        dist_dir = Path(temp_dir) / "dist"
        dist_dir.mkdir()

        subprocess.run(
            ["uv", "build", "--out-dir", str(dist_dir)],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )

        wheel_paths = list(dist_dir.glob("*.whl"))
        assert len(wheel_paths) == 1

        with zipfile.ZipFile(wheel_paths[0]) as wheel:
            assert "edullm_platform/py.typed" in wheel.namelist()
