from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from edullm_platform.canonical import canonical_json_bytes
from edullm_platform.contracts.dataset_registry import (
    DatasetRegistry,
    RegisteredDatasetRelease,
)
from edullm_platform.contracts.policy import classify_request
from edullm_platform.manifest_helpers import (
    REPRESENTATIVE_MANIFEST_COSTS,
    compute_manifest_maximum_cost,
    load_manifest,
)
from edullm_platform.phase0_gate import (
    OPERATIONAL_INVENTORY_CHECK_IDS,
    GateCheck,
    Phase0GateResult,
    evaluate_phase0,
    expected_manifest_classification,
    load_aws_capacity_evidence,
    load_phase0_inputs,
    request_facts_from_manifest,
)
from tests.gate_support import (
    copy_gate_repo,
    loaded_inputs,
    synthetic_account_id_alias,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def get_check(result: Phase0GateResult, check_id: str) -> GateCheck:
    matching = [check for check in result.checks if check.check_id == check_id]
    assert len(matching) == 1, f"expected one check {check_id!r}, got {result.checks}"
    return matching[0]


def test_repository_gate_passes_with_public_platform_repository() -> None:
    result = evaluate_phase0(loaded_inputs())
    assert result.passed is True
    assert {check.check_id for check in result.checks} == OPERATIONAL_INVENTORY_CHECK_IDS
    assert all(check.passed for check in result.checks)
    assert all(check.reason_code == "ok" for check in result.checks)


def test_every_inventory_check_is_named_so_it_cannot_be_read_as_a_phase_criterion() -> None:
    result = evaluate_phase0(loaded_inputs())
    assert len(result.checks) == 9
    assert all(check.check_id.startswith("inventory_") for check in result.checks)


def test_gate_passes_when_github_plan_supports_private_repo_controls() -> None:
    inputs = loaded_inputs()
    github_plan = inputs.github_plan
    assert github_plan is not None
    result = evaluate_phase0(
        replace(
            inputs,
            github_plan=github_plan.model_copy(
                update={"plan_name": "team", "visibility": "private"}
            ),
        )
    )
    assert result.passed is True
    assert all(check.passed for check in result.checks)
    assert all(check.reason_code == "ok" for check in result.checks)


def test_gate_executes_every_check_even_after_failure() -> None:
    inputs = loaded_inputs()
    broken_inventory = inputs.inventory.model_copy(update={"admins": ("philote-dev", "philote-dev")})
    github_plan = inputs.github_plan
    assert github_plan is not None
    result = evaluate_phase0(
        replace(
            inputs,
            inventory=broken_inventory,
            github_plan=github_plan.model_copy(
                update={"plan_name": "team", "visibility": "private"}
            ),
        )
    )
    assert result.passed is False
    assert len(result.checks) == len(OPERATIONAL_INVENTORY_CHECK_IDS)
    assert not get_check(result, "inventory_ownership").passed
    assert get_check(result, "inventory_github_plan").passed


@pytest.mark.parametrize("check_id", sorted(OPERATIONAL_INVENTORY_CHECK_IDS))
def test_passing_gate_reports_ok_reason_code(check_id: str) -> None:
    result = evaluate_phase0(loaded_inputs())
    check = get_check(result, check_id)
    assert check.passed is True
    assert check.reason_code == "ok"


def test_ownership_fails_for_unexpected_admin_roster() -> None:
    inputs = loaded_inputs()
    inventory = inputs.inventory.model_copy(update={"admins": ("philote-dev", "ericrcwu001")})
    result = evaluate_phase0(replace(inputs, inventory=inventory))
    check = get_check(result, "inventory_ownership")
    assert check.passed is False
    assert check.reason_code == "admin_roster_mismatch"


def test_ownership_fails_for_unexpected_team_lead_roster() -> None:
    inputs = loaded_inputs()
    team_leads = list(inputs.inventory.team_leads)
    team_leads[team_leads.index("hiyasvyas")] = "katiehehe"
    inventory = inputs.inventory.model_copy(update={"team_leads": tuple(team_leads)})
    result = evaluate_phase0(replace(inputs, inventory=inventory))
    check = get_check(result, "inventory_ownership")
    assert check.passed is False
    assert check.reason_code == "team_lead_roster_mismatch"


def test_pilots_fails_for_single_pilot_repository() -> None:
    inputs = loaded_inputs()
    inventory = inputs.inventory.model_copy(update={"pilot_repositories": ("OLMo-core",)})
    result = evaluate_phase0(replace(inputs, inventory=inventory))
    check = get_check(result, "inventory_pilots")
    assert check.passed is False
    assert check.reason_code == "pilot_repository_mismatch"


def test_workload_coverage_fails_without_gpu_representative() -> None:
    inputs = loaded_inputs()
    workloads = list(inputs.catalog.workloads)
    workloads[1] = workloads[1].model_copy(update={"compute_profile": "cpu-32vcpu"})
    catalog = inputs.catalog.model_copy(update={"workloads": tuple(workloads)})
    result = evaluate_phase0(replace(inputs, catalog=catalog))
    check = get_check(result, "inventory_workload_coverage")
    assert check.passed is False
    assert check.reason_code == "missing_gpu_representative"


def test_approval_paths_fails_when_denial_paths_incomplete() -> None:
    inputs = loaded_inputs()
    policy = inputs.policy.model_copy(
        update={"denied_outright": ("unregistered_repository", "unregistered_dataset")}
    )
    result = evaluate_phase0(replace(inputs, policy=policy))
    check = get_check(result, "inventory_approval_paths")
    assert check.passed is False
    assert check.reason_code == "denied_outright_incomplete"


def test_approval_paths_fails_when_routine_approver_missing() -> None:
    inputs = loaded_inputs()
    policy = inputs.policy.model_copy(update={"routine_approver_role": "platform_admin"})
    result = evaluate_phase0(replace(inputs, policy=policy))
    check = get_check(result, "inventory_approval_paths")
    assert check.passed is False
    assert check.reason_code == "routine_approver_missing"


def test_approval_paths_fails_when_exception_approver_missing() -> None:
    inputs = loaded_inputs()
    policy = inputs.policy.model_copy(update={"exception_approver_roles": ("team_lead",)})
    result = evaluate_phase0(replace(inputs, policy=policy))
    check = get_check(result, "inventory_approval_paths")
    assert check.passed is False
    assert check.reason_code == "exception_approver_missing"


def test_checkpoint_expectations_fails_for_retry_without_checkpoint() -> None:
    inputs = loaded_inputs()
    workloads = list(inputs.catalog.workloads)
    workloads[0] = workloads[0].model_copy(update={"maximum_attempts": 2, "checkpoint": None})
    catalog = inputs.catalog.model_copy(update={"workloads": tuple(workloads)})
    result = evaluate_phase0(replace(inputs, catalog=catalog))
    check = get_check(result, "inventory_checkpoint_expectations")
    assert check.passed is False
    assert check.reason_code == "retry_missing_checkpoint"


def test_github_plan_fails_for_free_plan_on_private_repository() -> None:
    inputs = loaded_inputs()
    github_plan = inputs.github_plan
    assert github_plan is not None
    result = evaluate_phase0(
        replace(inputs, github_plan=github_plan.model_copy(update={"visibility": "private"}))
    )
    check = get_check(result, "inventory_github_plan")
    assert check.passed is False
    assert check.reason_code == "plan_insufficient_for_private_repo_controls"
    assert "private" in check.detail.lower()


def test_github_plan_passes_for_public_repository_on_free_plan() -> None:
    inputs = loaded_inputs()
    github_plan = inputs.github_plan
    assert github_plan is not None
    result = evaluate_phase0(
        replace(
            inputs,
            github_plan=github_plan.model_copy(update={"plan_name": "free", "visibility": "public"}),
        )
    )
    check = get_check(result, "inventory_github_plan")
    assert check.passed is True
    assert check.reason_code == "ok"
    assert "public" in check.detail.lower()


def test_github_plan_passes_for_private_repository_on_team_plan() -> None:
    inputs = loaded_inputs()
    github_plan = inputs.github_plan
    assert github_plan is not None
    result = evaluate_phase0(
        replace(
            inputs,
            github_plan=github_plan.model_copy(update={"plan_name": "team", "visibility": "private"}),
        )
    )
    check = get_check(result, "inventory_github_plan")
    assert check.passed is True
    assert check.reason_code == "ok"


def test_github_plan_passes_for_private_repository_on_enterprise_plan() -> None:
    inputs = loaded_inputs()
    github_plan = inputs.github_plan
    assert github_plan is not None
    result = evaluate_phase0(
        replace(
            inputs,
            github_plan=github_plan.model_copy(
                update={"plan_name": "enterprise", "visibility": "private"}
            ),
        )
    )
    check = get_check(result, "inventory_github_plan")
    assert check.passed is True
    assert check.reason_code == "ok"


def test_github_plan_fails_for_unknown_plan_on_private_repository() -> None:
    inputs = loaded_inputs()
    github_plan = inputs.github_plan
    assert github_plan is not None
    result = evaluate_phase0(
        replace(
            inputs,
            github_plan=github_plan.model_copy(
                update={"plan_name": "unknown-plan", "visibility": "private"}
            ),
        )
    )
    check = get_check(result, "inventory_github_plan")
    assert check.passed is False
    assert check.reason_code == "plan_insufficient_for_private_repo_controls"


def test_github_plan_fails_for_organization_mismatch() -> None:
    inputs = loaded_inputs()
    github_plan = inputs.github_plan
    assert github_plan is not None
    result = evaluate_phase0(
        replace(inputs, github_plan=github_plan.model_copy(update={"organization": "other-org"}))
    )
    check = get_check(result, "inventory_github_plan")
    assert check.passed is False
    assert check.reason_code == "organization_mismatch"


def test_aws_capacity_fails_when_gpu_quota_mapping_incomplete() -> None:
    inputs = loaded_inputs()
    aws_capacity = inputs.aws_capacity
    assert aws_capacity is not None
    quotas = tuple(
        quota for quota in aws_capacity.quotas if quota.workload_profile != "gpu-4xa10g"
    )
    result = evaluate_phase0(
        replace(inputs, aws_capacity=aws_capacity.model_copy(update={"quotas": quotas}))
    )
    check = get_check(result, "inventory_aws_capacity")
    assert check.passed is False
    assert check.reason_code == "capacity_blocked"


def test_aws_capacity_fails_when_gpu_quota_insufficient() -> None:
    inputs = loaded_inputs()
    aws_capacity = inputs.aws_capacity
    assert aws_capacity is not None
    quotas = list(aws_capacity.quotas)
    quotas[0] = quotas[0].model_copy(update={"applied_value": 16.0})
    result = evaluate_phase0(
        replace(inputs, aws_capacity=aws_capacity.model_copy(update={"quotas": tuple(quotas)}))
    )
    check = get_check(result, "inventory_aws_capacity")
    assert check.passed is False
    assert check.reason_code == "capacity_increase_required"


def test_aws_capacity_fails_for_wrong_region() -> None:
    inputs = loaded_inputs()
    aws_capacity = inputs.aws_capacity
    assert aws_capacity is not None
    result = evaluate_phase0(
        replace(inputs, aws_capacity=aws_capacity.model_copy(update={"region": "us-west-2"}))
    )
    check = get_check(result, "inventory_aws_capacity")
    assert check.passed is False
    assert check.reason_code == "wrong_region"


def test_aws_capacity_fails_when_batch_quota_missing() -> None:
    inputs = loaded_inputs()
    aws_capacity = inputs.aws_capacity
    assert aws_capacity is not None
    result = evaluate_phase0(
        replace(
            inputs,
            aws_capacity=aws_capacity.model_copy(update={"batch_quotas": (aws_capacity.batch_quotas[0],)}),
        )
    )
    check = get_check(result, "inventory_aws_capacity")
    assert check.passed is False
    assert check.reason_code == "capacity_blocked"


def test_representative_manifests_fails_for_unregistered_compute_profile() -> None:
    inputs = loaded_inputs()
    filename, manifest = inputs.manifests[0]
    broken_manifest = manifest.model_copy(update={"compute_profile": "not-a-registered-profile"})
    manifests = tuple(
        (name, broken_manifest if name == filename else current)
        for name, current in inputs.manifests
    )
    result = evaluate_phase0(replace(inputs, manifests=manifests))
    check = get_check(result, "inventory_representative_manifests")
    assert check.passed is False
    assert check.reason_code == "unregistered_compute_profile"


def test_representative_manifests_fails_for_unregistered_workload_profile() -> None:
    inputs = loaded_inputs()
    filename, manifest = inputs.manifests[0]
    broken_manifest = manifest.model_copy(update={"workload_profile": "missing-workload"})
    manifests = tuple(
        (name, broken_manifest if name == filename else current)
        for name, current in inputs.manifests
    )
    result = evaluate_phase0(replace(inputs, manifests=manifests))
    check = get_check(result, "inventory_representative_manifests")
    assert check.passed is False
    assert check.reason_code == "unregistered_workload_profile"


def test_representative_manifests_fails_for_unregistered_dataset() -> None:
    inputs = loaded_inputs()
    filename, manifest = inputs.manifests[0]
    broken_manifest = manifest.model_copy(update={"dataset_release": "unknown-dataset"})
    manifests = tuple(
        (name, broken_manifest if name == filename else current)
        for name, current in inputs.manifests
    )
    result = evaluate_phase0(replace(inputs, manifests=manifests))
    check = get_check(result, "inventory_representative_manifests")
    assert check.passed is False
    assert check.reason_code == "unregistered_dataset"


def test_representative_manifests_fails_when_the_registry_stops_listing_their_dataset() -> None:
    inputs = loaded_inputs()
    assert inputs.dataset_registry.is_registered("dolma-2026-07")
    successor_only = DatasetRegistry(
        schema_version=1,
        releases=(RegisteredDatasetRelease(release_id="dolma-2026-08"),),
    )
    result = evaluate_phase0(replace(inputs, dataset_registry=successor_only))
    check = get_check(result, "inventory_representative_manifests")
    assert check.passed is False
    assert check.reason_code == "unregistered_dataset", (
        "the registered set is reviewed configuration the gate is handed, so withdrawing a "
        "release there has to fail the manifests that name it"
    )


def test_representative_manifests_fails_on_classification_mismatch() -> None:
    inputs = loaded_inputs()
    manifest = load_manifest(PROJECT_ROOT / "fixtures" / "manifests" / "gpu-exception.yaml")
    broken_manifest = manifest.model_copy(update={"maximum_runtime_hours": Decimal(1)})
    manifests = tuple(
        (filename, broken_manifest if filename == "gpu-exception.yaml" else current)
        for filename, current in inputs.manifests
    )
    result = evaluate_phase0(replace(inputs, manifests=manifests))
    check = get_check(result, "inventory_representative_manifests")
    assert check.passed is False
    assert check.reason_code == "classification_mismatch"


def test_representative_manifests_fails_for_unreviewed_manifest(tmp_path: Path) -> None:
    inputs = loaded_inputs()
    extra_manifest = inputs.manifests[0][1].model_copy(
        update={"maximum_runtime_hours": Decimal(999)}
    )
    manifests = (*inputs.manifests, ("extra-long.yaml", extra_manifest))
    result = evaluate_phase0(replace(inputs, manifests=manifests))
    check = get_check(result, "inventory_representative_manifests")
    assert check.passed is False
    assert check.reason_code == "unexpected_manifest"


def test_cost_estimates_fails_when_reviewed_cost_does_not_match() -> None:
    inputs = loaded_inputs()
    profiles = list(inputs.catalog.compute_profiles)
    profiles[0] = profiles[0].model_copy(update={"hourly_rate_usd": Decimal("9.999")})
    catalog = inputs.catalog.model_copy(update={"compute_profiles": tuple(profiles)})
    result = evaluate_phase0(replace(inputs, catalog=catalog))
    check = get_check(result, "inventory_cost_estimates")
    assert check.passed is False
    assert check.reason_code == "reviewed_cost_mismatch"


def test_cost_estimates_fails_when_routine_manifest_exceeds_program_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = loaded_inputs()
    profiles = list(inputs.catalog.compute_profiles)
    profiles[0] = profiles[0].model_copy(update={"hourly_rate_usd": Decimal(300)})
    catalog = inputs.catalog.model_copy(update={"compute_profiles": tuple(profiles)})
    monkeypatch.setitem(REPRESENTATIVE_MANIFEST_COSTS, "cpu-routine.yaml", Decimal("600.00"))
    result = evaluate_phase0(replace(inputs, catalog=catalog))
    check = get_check(result, "inventory_cost_estimates")
    assert check.passed is False
    assert check.reason_code == "exceeds_program_budget"


def test_cost_estimates_does_not_apply_program_budget_to_exception_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = loaded_inputs()
    inflated_cost = compute_manifest_maximum_cost(
        next(manifest for name, manifest in inputs.manifests if name == "gpu-exception.yaml").model_copy(
            update={"maximum_runtime_hours": Decimal(100)}
        ),
        inputs.catalog,
    )
    assert inflated_cost > Decimal(500)
    monkeypatch.setitem(REPRESENTATIVE_MANIFEST_COSTS, "gpu-exception.yaml", inflated_cost)
    manifests = tuple(
        (
            name,
            manifest.model_copy(update={"maximum_runtime_hours": Decimal(100)})
            if name == "gpu-exception.yaml"
            else manifest,
        )
        for name, manifest in inputs.manifests
    )
    result = evaluate_phase0(
        replace(
            inputs,
            manifests=manifests,
        )
    )
    check = get_check(result, "inventory_cost_estimates")
    assert check.passed is True
    assert check.reason_code == "ok"
    assert inflated_cost > Decimal(500)


def test_load_phase0_inputs_rejects_invalid_organization_yaml(tmp_path: Path) -> None:
    repo_root = copy_gate_repo(tmp_path)
    (repo_root / "config" / "organization.yaml").write_text(
        "admins: []\nteam_leads: []\nmembers: []\npilot_repositories: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as exc_info:
        load_phase0_inputs(repo_root)
    too_short_locations = {
        item["loc"] for item in exc_info.value.errors() if item["type"] == "too_short"
    }
    assert too_short_locations == {
        ("admins",),
        ("team_leads",),
        ("members",),
        ("pilot_repositories",),
    }


def test_aws_capacity_fails_for_wrong_environment() -> None:
    inputs = loaded_inputs()
    aws_capacity = inputs.aws_capacity
    assert aws_capacity is not None
    result = evaluate_phase0(
        replace(
            inputs,
            aws_capacity=aws_capacity.model_copy(update={"environment": "production"}),  # type: ignore[arg-type]
        )
    )
    check = get_check(result, "inventory_aws_capacity")
    assert check.passed is False
    assert check.reason_code == "wrong_environment"


def test_aws_capacity_fails_when_self_reported_required_vcpus_is_too_low() -> None:
    inputs = loaded_inputs()
    aws_capacity = inputs.aws_capacity
    assert aws_capacity is not None
    quotas = list(aws_capacity.quotas)
    quotas[0] = quotas[0].model_copy(update={"required_vcpus": 1, "applied_value": 2.0})
    result = evaluate_phase0(
        replace(inputs, aws_capacity=aws_capacity.model_copy(update={"quotas": tuple(quotas)}))
    )
    check = get_check(result, "inventory_aws_capacity")
    assert check.passed is False
    assert check.reason_code == "capacity_increase_required"


def test_aws_capacity_fails_when_node_count_requires_more_vcpus_than_quota() -> None:
    inputs = loaded_inputs()
    aws_capacity = inputs.aws_capacity
    catalog = inputs.catalog
    assert aws_capacity is not None
    gpu_profile = next(
        profile for profile in catalog.compute_profiles if profile.name == "gpu-4xa10g"
    )
    scaled_catalog = catalog.model_copy(
        update={
            "compute_profiles": tuple(
                profile.model_copy(update={"nodes": 32})
                if profile.name == "gpu-4xa10g"
                else profile
                for profile in catalog.compute_profiles
            )
        }
    )
    result = evaluate_phase0(replace(inputs, catalog=scaled_catalog))
    check = get_check(result, "inventory_aws_capacity")
    assert check.passed is False
    assert check.reason_code == "capacity_increase_required"
    assert gpu_profile.nodes == 1


def test_phase0_gate_result_round_trips_through_contract_json() -> None:
    result = evaluate_phase0(loaded_inputs())
    payload = json.loads(canonical_json_bytes(result).decode("utf-8"))
    expected_passed = payload.pop("passed")
    round_tripped = Phase0GateResult.model_validate(payload)
    assert round_tripped.passed == expected_passed
    assert round_tripped == result


def test_load_aws_capacity_evidence_rejects_production_environment(tmp_path: Path) -> None:
    repo_root = copy_gate_repo(tmp_path)
    evidence_path = repo_root / "fixtures" / "evidence" / "service-quotas.sanitized.json"
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    payload["environment"] = "production"
    evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    evidence, load_error = load_aws_capacity_evidence(evidence_path)
    assert evidence is None
    assert load_error == "evidence_invalid"


def test_load_aws_capacity_evidence_rejects_account_id_in_alias(tmp_path: Path) -> None:
    repo_root = copy_gate_repo(tmp_path)
    evidence_path = repo_root / "fixtures" / "evidence" / "service-quotas.sanitized.json"
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    payload["account_alias"] = synthetic_account_id_alias()
    evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    evidence, load_error = load_aws_capacity_evidence(evidence_path)
    assert evidence is None
    assert load_error == "evidence_invalid"


def test_load_aws_capacity_evidence_rejects_missing_region(tmp_path: Path) -> None:
    repo_root = copy_gate_repo(tmp_path)
    evidence_path = repo_root / "fixtures" / "evidence" / "service-quotas.sanitized.json"
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    del payload["region"]
    evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    evidence, load_error = load_aws_capacity_evidence(evidence_path)
    assert evidence is None
    assert load_error == "evidence_invalid"


def test_evidence_stale_fails_github_plan_and_aws_capacity_checks(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    repo_root = copy_gate_repo(tmp_path)
    stale_at = (
        datetime.now(tz=UTC) - timedelta(days=31)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    for filename in ("github-plan.sanitized.json", "service-quotas.sanitized.json"):
        evidence_path = repo_root / "fixtures" / "evidence" / filename
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        payload["observed_at"] = stale_at
        evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    inputs = load_phase0_inputs(repo_root)
    assert inputs.github_plan is None
    assert inputs.github_plan_load_error == "evidence_stale"
    assert inputs.aws_capacity is None
    assert inputs.aws_capacity_load_error == "evidence_stale"
    result = evaluate_phase0(inputs)
    github_check = get_check(result, "inventory_github_plan")
    aws_check = get_check(result, "inventory_aws_capacity")
    assert github_check.passed is False
    assert github_check.reason_code == "evidence_stale"
    assert aws_check.passed is False
    assert aws_check.reason_code == "evidence_stale"


def test_representative_manifest_classifications_match_policy_expectations() -> None:
    inputs = loaded_inputs()
    policy = inputs.policy
    catalog = inputs.catalog
    inventory = inputs.inventory
    for filename, manifest in inputs.manifests:
        estimated_cost = compute_manifest_maximum_cost(manifest, catalog)
        assert estimated_cost == REPRESENTATIVE_MANIFEST_COSTS[filename]
        facts = request_facts_from_manifest(
            manifest,
            inventory=inventory,
            catalog=catalog,
            dataset_registry=inputs.dataset_registry,
            estimated_cost_usd=estimated_cost,
        )
        assert classify_request(facts, policy.thresholds) == expected_manifest_classification(filename)
