from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from edullm_platform.config import load_yaml
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.policy import (
    ApprovalClass,
    ApprovalPolicy,
    PolicyThresholds,
    RequestFacts,
    classify_request,
)
from edullm_platform.phase0_gate import (
    expected_manifest_classification,
    request_facts_from_manifest,
)
from tests.test_manifest import (
    PROJECT_ROOT,
    REPRESENTATIVE_MANIFEST_FILENAMES,
    compute_manifest_maximum_cost,
    is_workload_profile_registered,
    load_representative_manifest,
    load_workload_catalog,
)


def expected_classification(filename: str) -> ApprovalClass:
    return expected_manifest_classification(filename)


def numeric_bound_violations(
    facts: RequestFacts,
    thresholds: PolicyThresholds,
) -> frozenset[str]:
    violations: set[str] = set()
    if facts.estimated_cost_usd > thresholds.routine_maximum_cost_usd:
        violations.add("cost")
    if facts.maximum_runtime_hours > thresholds.routine_maximum_runtime_hours:
        violations.add("runtime")
    if facts.maximum_attempts > thresholds.routine_maximum_attempts:
        violations.add("attempts")
    if facts.fanout_size > thresholds.routine_maximum_fanout_size:
        violations.add("fanout_size")
    if facts.fanout_parallelism > thresholds.routine_maximum_parallelism:
        violations.add("parallelism")
    return frozenset(violations)


def load_organization_inventory() -> OrganizationInventory:
    return load_yaml(PROJECT_ROOT / "config" / "organization.yaml", OrganizationInventory)


def load_approval_policy() -> ApprovalPolicy:
    return load_yaml(PROJECT_ROOT / "config" / "policy.yaml", ApprovalPolicy)


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


def thresholds_payload() -> dict[str, object]:
    return {
        "routine_maximum_cost_usd": "500",
        "routine_maximum_runtime_hours": "24",
        "routine_maximum_attempts": 2,
        "routine_maximum_fanout_size": 64,
        "routine_maximum_parallelism": 8,
    }


def request_facts_payload(**overrides: object) -> dict[str, object]:
    payload = {
        "claimed_team": "memory-split",
        "repository_registered": True,
        "dataset_registered": True,
        "compute_profile_registered": True,
        "immutable_revision": True,
        "immutable_image": True,
        "estimated_cost_usd": "499.99",
        "maximum_runtime_hours": "24",
        "maximum_attempts": 2,
    }
    payload.update(overrides)
    return payload


def thresholds() -> PolicyThresholds:
    return PolicyThresholds(
        routine_maximum_cost_usd=Decimal(500),
        routine_maximum_runtime_hours=Decimal(24),
        routine_maximum_attempts=2,
        routine_maximum_fanout_size=64,
        routine_maximum_parallelism=8,
    )


def routine_facts(**overrides: object) -> RequestFacts:
    return RequestFacts.model_validate(request_facts_payload(**overrides))


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


def test_registered_request_within_all_bounds_is_routine() -> None:
    assert classify_request(routine_facts(), thresholds()) is ApprovalClass.ROUTINE


def test_any_policy_violation_is_exception() -> None:
    facts = RequestFacts(
        claimed_team="memory-split",
        repository_registered=True,
        dataset_registered=True,
        compute_profile_registered=True,
        immutable_revision=True,
        immutable_image=False,
        estimated_cost_usd=Decimal(1),
        maximum_runtime_hours=Decimal(1),
        maximum_attempts=1,
    )
    assert classify_request(facts, thresholds()) is ApprovalClass.EXCEPTION


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository_registered", False),
        ("dataset_registered", False),
        ("compute_profile_registered", False),
        ("immutable_revision", False),
        ("immutable_image", False),
    ],
)
def test_unregistered_or_mutable_facts_classify_as_exception(
    field: str,
    value: bool,
) -> None:
    facts = routine_facts(**{field: value})
    assert classify_request(facts, thresholds()) is ApprovalClass.EXCEPTION


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("estimated_cost_usd", Decimal("500.01")),
        ("maximum_runtime_hours", Decimal("24.01")),
        ("maximum_attempts", 3),
        ("fanout_size", 65),
        ("fanout_parallelism", 9),
    ],
)
def test_numeric_bound_violations_classify_as_exception(
    field: str,
    value: object,
) -> None:
    facts = routine_facts(**{field: value})
    assert classify_request(facts, thresholds()) is ApprovalClass.EXCEPTION


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("estimated_cost_usd", Decimal(500)),
        ("maximum_runtime_hours", Decimal(24)),
        ("maximum_attempts", 2),
        ("fanout_size", 64),
        ("fanout_parallelism", 8),
    ],
)
def test_numeric_values_at_threshold_remain_routine(
    field: str,
    value: object,
) -> None:
    facts = routine_facts(**{field: value})
    assert classify_request(facts, thresholds()) is ApprovalClass.ROUTINE


def test_request_facts_describe_a_single_cell_when_no_fanout_is_declared() -> None:
    facts = routine_facts()
    assert facts.fanout_size == 1
    assert facts.fanout_parallelism == 1
    assert numeric_bound_violations(facts, thresholds()) == frozenset()


def test_policy_yaml_validates_against_contract() -> None:
    project_root = Path(__file__).resolve().parents[1]
    config_path = project_root / "config" / "policy.yaml"
    policy = load_yaml(config_path, ApprovalPolicy)
    assert policy.thresholds.routine_maximum_cost_usd == Decimal(500)
    assert policy.thresholds.routine_maximum_runtime_hours == Decimal(12)
    assert policy.thresholds.routine_maximum_attempts == 2
    assert policy.thresholds.routine_maximum_fanout_size == 64
    assert policy.thresholds.routine_maximum_parallelism == 8
    assert policy.routine_approver_role == "team_lead"
    assert policy.exception_approver_roles == ("platform_admin",)
    assert policy.denied_outright == (
        "unregistered_repository",
        "unregistered_dataset",
        "unregistered_compute_profile",
        "mutable_repository_revision",
        "mutable_image_reference",
    )


def test_approval_policy_rejects_routine_role_satisfying_exception() -> None:
    payload = policy_payload()
    payload["exception_approver_roles"] = ["team_lead"]
    with pytest.raises(ValidationError) as exc_info:
        ApprovalPolicy.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="routine approver role must not satisfy exception approval on its own",
    )


def test_approval_policy_rejects_unknown_denied_outright_condition() -> None:
    payload = policy_payload()
    payload["denied_outright"] = ["unregistered_repository", "unknown_condition"]
    with pytest.raises(ValidationError) as exc_info:
        ApprovalPolicy.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="literal_error",
    )


def test_policy_thresholds_reject_non_decimal_cost() -> None:
    with pytest.raises(ValidationError) as exc_info:
        PolicyThresholds.model_validate({**thresholds_payload(), "routine_maximum_cost_usd": 500})
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="decimal values must be non-negative base-10 strings",
    )


def test_request_facts_reject_non_decimal_runtime() -> None:
    with pytest.raises(ValidationError) as exc_info:
        RequestFacts.model_validate(
            {
                "claimed_team": "memory-split",
                "repository_registered": True,
                "dataset_registered": True,
                "compute_profile_registered": True,
                "immutable_revision": True,
                "immutable_image": True,
                "estimated_cost_usd": "1",
                "maximum_runtime_hours": 24,
                "maximum_attempts": 1,
            }
        )
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="decimal values must be non-negative base-10 strings",
    )


def test_request_facts_require_an_explicit_claimed_team() -> None:
    payload = request_facts_payload()
    del payload["claimed_team"]
    with pytest.raises(ValidationError) as exc_info:
        RequestFacts.model_validate(payload)
    assert_validation_error(exc_info.value, error_type="missing")
    assert exc_info.value.errors()[0]["loc"] == ("claimed_team",), (
        "attribution must be supplied deliberately; a default would let a caller skip it"
    )


@pytest.mark.parametrize("claimed_team", ["", "Memory Split", "memory_split", "-memory-split"])
def test_request_facts_reject_a_claimed_team_that_is_not_a_team_identifier(
    claimed_team: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        RequestFacts.model_validate(request_facts_payload(claimed_team=claimed_team))
    assert exc_info.value.errors()[0]["loc"] == ("claimed_team",)


@pytest.mark.parametrize(
    ("payload_override", "field", "error_type"),
    [
        ({"routine_maximum_cost_usd": Decimal(-1)}, "routine_maximum_cost_usd", "greater_than_equal"),
        ({"routine_maximum_runtime_hours": Decimal(0)}, "routine_maximum_runtime_hours", "greater_than"),
        ({"routine_maximum_attempts": 0}, "routine_maximum_attempts", "greater_than_equal"),
        (
            {"routine_maximum_fanout_size": 0},
            "routine_maximum_fanout_size",
            "greater_than_equal",
        ),
        (
            {"routine_maximum_parallelism": 0},
            "routine_maximum_parallelism",
            "greater_than_equal",
        ),
    ],
)
def test_policy_thresholds_reject_out_of_range_values(
    payload_override: dict[str, object],
    field: str,
    error_type: str,
) -> None:
    payload = {**thresholds_payload(), **payload_override}
    with pytest.raises(ValidationError) as exc_info:
        PolicyThresholds.model_validate(payload)
    assert_validation_error(exc_info.value, error_type=error_type)
    assert exc_info.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize(
    ("payload_override", "field", "error_type"),
    [
        ({"estimated_cost_usd": Decimal(-5)}, "estimated_cost_usd", "greater_than_equal"),
        ({"maximum_runtime_hours": Decimal(0)}, "maximum_runtime_hours", "greater_than"),
        ({"maximum_attempts": 0}, "maximum_attempts", "greater_than_equal"),
        ({"fanout_size": 0}, "fanout_size", "greater_than_equal"),
        ({"fanout_parallelism": 0}, "fanout_parallelism", "greater_than_equal"),
    ],
)
def test_request_facts_reject_out_of_range_values(
    payload_override: dict[str, object],
    field: str,
    error_type: str,
) -> None:
    payload = request_facts_payload(**payload_override)
    with pytest.raises(ValidationError) as exc_info:
        RequestFacts.model_validate(payload)
    assert_validation_error(exc_info.value, error_type=error_type)
    assert exc_info.value.errors()[0]["loc"] == (field,)


def test_approval_policy_rejects_empty_routine_approver_role() -> None:
    payload = policy_payload()
    payload["routine_approver_role"] = ""
    with pytest.raises(ValidationError) as exc_info:
        ApprovalPolicy.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="string_too_short",
    )
    assert exc_info.value.errors()[0]["loc"] == ("routine_approver_role",)


def test_approval_policy_rejects_empty_exception_approver_roles() -> None:
    payload = policy_payload()
    payload["exception_approver_roles"] = []
    with pytest.raises(ValidationError) as exc_info:
        ApprovalPolicy.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="too_short",
    )
    assert exc_info.value.errors()[0]["loc"] == ("exception_approver_roles",)


def test_approval_policy_rejects_empty_denied_outright() -> None:
    payload = policy_payload()
    payload["denied_outright"] = []
    with pytest.raises(ValidationError) as exc_info:
        ApprovalPolicy.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="too_short",
    )
    assert exc_info.value.errors()[0]["loc"] == ("denied_outright",)


@pytest.mark.parametrize("filename", REPRESENTATIVE_MANIFEST_FILENAMES)
def test_representative_manifest_classifies_as_expected(filename: str) -> None:
    manifest = load_representative_manifest(filename)
    inventory = load_organization_inventory()
    catalog = load_workload_catalog()
    policy = load_approval_policy()
    estimated_cost_usd = compute_manifest_maximum_cost(manifest, catalog)
    facts = request_facts_from_manifest(
        manifest,
        inventory=inventory,
        catalog=catalog,
        estimated_cost_usd=estimated_cost_usd,
    )
    expected = expected_classification(filename)
    assert (
        classify_request(facts, policy.thresholds) == expected
    ), f"{filename} classification mismatch for {facts=}"


def test_gpu_exception_has_full_registration_and_only_runtime_violation() -> None:
    filename = "gpu-exception.yaml"
    manifest = load_representative_manifest(filename)
    inventory = load_organization_inventory()
    catalog = load_workload_catalog()
    policy = load_approval_policy()
    estimated_cost_usd = compute_manifest_maximum_cost(manifest, catalog)
    facts = request_facts_from_manifest(
        manifest,
        inventory=inventory,
        catalog=catalog,
        estimated_cost_usd=estimated_cost_usd,
    )

    assert facts.repository_registered is True
    assert facts.dataset_registered is True
    assert facts.compute_profile_registered is True
    assert is_workload_profile_registered(manifest, catalog)
    assert facts.immutable_revision is True
    assert facts.immutable_image is True
    assert numeric_bound_violations(facts, policy.thresholds) == frozenset({"runtime"})


@pytest.mark.parametrize(
    "filename",
    [name for name in REPRESENTATIVE_MANIFEST_FILENAMES if name.endswith("-routine.yaml")],
)
def test_routine_manifest_has_full_registration_and_no_bound_violations(
    filename: str,
) -> None:
    manifest = load_representative_manifest(filename)
    inventory = load_organization_inventory()
    catalog = load_workload_catalog()
    policy = load_approval_policy()
    estimated_cost_usd = compute_manifest_maximum_cost(manifest, catalog)
    facts = request_facts_from_manifest(
        manifest,
        inventory=inventory,
        catalog=catalog,
        estimated_cost_usd=estimated_cost_usd,
    )

    assert facts.repository_registered is True
    assert facts.dataset_registered is True
    assert facts.compute_profile_registered is True
    assert is_workload_profile_registered(manifest, catalog)
    assert facts.immutable_revision is True
    assert facts.immutable_image is True
    assert numeric_bound_violations(facts, policy.thresholds) == frozenset()


def test_request_facts_from_manifest_rejects_unregistered_repository() -> None:
    manifest = load_representative_manifest("cpu-routine.yaml")
    inventory = load_organization_inventory()
    catalog = load_workload_catalog()
    broken_manifest = manifest.model_copy(update={"repository": "not-a-pilot-repository"})
    facts = request_facts_from_manifest(
        broken_manifest,
        inventory=inventory,
        catalog=catalog,
        estimated_cost_usd=Decimal(1),
    )
    assert facts.repository_registered is False
    assert classify_request(facts, thresholds()) is ApprovalClass.EXCEPTION
