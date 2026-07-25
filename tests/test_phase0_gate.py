from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.policy import (
    ApprovalClass,
    RequestFacts,
    classify_request,
)
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.phase0_gate import (
    EXPECTED_CHECK_IDS,
    Phase0GateResult,
    Phase0Inputs,
    evaluate_phase0,
    evaluate_repository,
    load_phase0_inputs,
)
from tests.test_manifest import (
    REPRESENTATIVE_MANIFEST_COSTS,
    compute_manifest_maximum_cost,
    is_compute_profile_registered,
    load_representative_manifest,
    manifest_has_immutable_image,
    manifest_has_immutable_revision,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTERED_DATASET_RELEASES = frozenset({"dolma-2026-07"})


def get_check(result: Phase0GateResult, check_id: str):
    matching = [check for check in result.checks if check.check_id == check_id]
    assert len(matching) == 1, f"expected one check {check_id!r}, got {result.checks}"
    return matching[0]


def request_facts_from_manifest(
    manifest: RunManifest,
    *,
    inventory: OrganizationInventory,
    catalog: WorkloadCatalog,
    estimated_cost_usd: Decimal,
) -> RequestFacts:
    return RequestFacts(
        repository_registered=manifest.repository in inventory.pilot_repositories,
        dataset_registered=manifest.dataset_release in REGISTERED_DATASET_RELEASES,
        compute_profile_registered=is_compute_profile_registered(manifest, catalog),
        immutable_revision=manifest_has_immutable_revision(manifest),
        immutable_image=manifest_has_immutable_image(manifest),
        estimated_cost_usd=estimated_cost_usd,
        maximum_runtime_hours=manifest.maximum_runtime_hours,
        maximum_attempts=manifest.maximum_attempts,
    )


def expected_classification(filename: str) -> ApprovalClass:
    if filename.endswith("-exception.yaml"):
        return ApprovalClass.EXCEPTION
    if filename.endswith("-routine.yaml"):
        return ApprovalClass.ROUTINE
    raise AssertionError(f"unexpected fixture filename: {filename!r}")


def loaded_inputs() -> Phase0Inputs:
    return load_phase0_inputs(PROJECT_ROOT)


def test_repository_gate_reports_github_plan_as_only_blocker() -> None:
    result = evaluate_repository(PROJECT_ROOT)
    assert result.passed is False
    assert {check.check_id for check in result.checks} == EXPECTED_CHECK_IDS
    failing = [check for check in result.checks if not check.passed]
    assert [check.check_id for check in failing] == ["github_plan"]
    assert failing[0].reason_code == "plan_insufficient_for_private_repo_controls"
    assert all(check.passed for check in result.checks if check.check_id != "github_plan")


def test_gate_passes_when_github_plan_supports_private_repo_controls() -> None:
    inputs = loaded_inputs()
    team_plan = inputs.github_plan.model_copy(update={"plan_name": "team"})
    result = evaluate_phase0(replace(inputs, github_plan=team_plan))
    assert result.passed is True
    assert all(check.passed for check in result.checks)
    assert all(check.reason_code == "ok" for check in result.checks)


def test_gate_executes_every_check_even_after_failure() -> None:
    inputs = loaded_inputs()
    broken_inventory = inputs.inventory.model_copy(update={"admins": ("philote-dev", "philote-dev")})
    result = evaluate_phase0(
        replace(
            inputs,
            inventory=broken_inventory,
            github_plan=inputs.github_plan.model_copy(update={"plan_name": "team"}),
        )
    )
    assert result.passed is False
    assert len(result.checks) == len(EXPECTED_CHECK_IDS)
    assert not get_check(result, "ownership").passed
    assert get_check(result, "github_plan").passed


@pytest.mark.parametrize("check_id", sorted(EXPECTED_CHECK_IDS))
def test_passing_gate_reports_ok_reason_code(check_id: str) -> None:
    inputs = loaded_inputs()
    team_plan = inputs.github_plan.model_copy(update={"plan_name": "team"})
    result = evaluate_phase0(replace(inputs, github_plan=team_plan))
    check = get_check(result, check_id)
    assert check.passed is True
    assert check.reason_code == "ok"


def test_ownership_fails_for_unexpected_admin_roster() -> None:
    inputs = loaded_inputs()
    inventory = inputs.inventory.model_copy(update={"admins": ("philote-dev", "ericrcwu001")})
    result = evaluate_phase0(replace(inputs, inventory=inventory))
    check = get_check(result, "ownership")
    assert check.passed is False
    assert check.reason_code == "admin_roster_mismatch"


def test_pilots_fails_for_single_pilot_repository() -> None:
    inputs = loaded_inputs()
    inventory = inputs.inventory.model_copy(update={"pilot_repositories": ("OLMo-core",)})
    result = evaluate_phase0(replace(inputs, inventory=inventory))
    check = get_check(result, "pilots")
    assert check.passed is False
    assert check.reason_code == "pilot_repository_mismatch"


def test_workload_coverage_fails_without_gpu_representative() -> None:
    inputs = loaded_inputs()
    workloads = list(inputs.catalog.workloads)
    workloads[1] = workloads[1].model_copy(update={"compute_profile": "cpu-32vcpu"})
    catalog = inputs.catalog.model_copy(update={"workloads": tuple(workloads)})
    result = evaluate_phase0(replace(inputs, catalog=catalog))
    check = get_check(result, "workload_coverage")
    assert check.passed is False
    assert check.reason_code == "missing_gpu_representative"


def test_approval_paths_fails_when_denial_paths_incomplete() -> None:
    inputs = loaded_inputs()
    policy = inputs.policy.model_copy(
        update={"denied_outright": ("unregistered_repository", "unregistered_dataset")}
    )
    result = evaluate_phase0(replace(inputs, policy=policy))
    check = get_check(result, "approval_paths")
    assert check.passed is False
    assert check.reason_code == "denied_outright_incomplete"


def test_checkpoint_expectations_fails_for_retry_without_checkpoint() -> None:
    inputs = loaded_inputs()
    workloads = list(inputs.catalog.workloads)
    workloads[0] = workloads[0].model_copy(update={"maximum_attempts": 2, "checkpoint": None})
    catalog = inputs.catalog.model_copy(update={"workloads": tuple(workloads)})
    result = evaluate_phase0(replace(inputs, catalog=catalog))
    check = get_check(result, "checkpoint_expectations")
    assert check.passed is False
    assert check.reason_code == "retry_missing_checkpoint"


def test_github_plan_fails_for_free_plan_on_private_repository() -> None:
    inputs = loaded_inputs()
    result = evaluate_phase0(inputs)
    check = get_check(result, "github_plan")
    assert check.passed is False
    assert check.reason_code == "plan_insufficient_for_private_repo_controls"
    assert "private" in check.detail.lower()


def test_aws_capacity_fails_when_verdict_is_blocked() -> None:
    inputs = loaded_inputs()
    aws_capacity = inputs.aws_capacity.model_copy(
        update={
            "capacity_verdict": "blocked",
            "capacity_verdict_note": "Capacity review blocked because representative workload mapping is incomplete.",
        }
    )
    result = evaluate_phase0(replace(inputs, aws_capacity=aws_capacity))
    check = get_check(result, "aws_capacity")
    assert check.passed is False
    assert check.reason_code == "capacity_blocked"


def test_representative_manifests_fails_on_classification_mismatch() -> None:
    inputs = loaded_inputs()
    manifest = load_representative_manifest("gpu-exception.yaml")
    broken_manifest = manifest.model_copy(update={"maximum_runtime_hours": Decimal(1)})
    manifests = tuple(
        (filename, broken_manifest if filename == "gpu-exception.yaml" else loaded)
        for filename, loaded in inputs.manifests
    )
    result = evaluate_phase0(replace(inputs, manifests=manifests))
    check = get_check(result, "representative_manifests")
    assert check.passed is False
    assert check.reason_code == "classification_mismatch"


def test_cost_estimates_fails_when_reviewed_cost_does_not_match() -> None:
    inputs = loaded_inputs()
    profiles = list(inputs.catalog.compute_profiles)
    profiles[0] = profiles[0].model_copy(update={"hourly_rate_usd": Decimal("9.999")})
    catalog = inputs.catalog.model_copy(update={"compute_profiles": tuple(profiles)})
    result = evaluate_phase0(replace(inputs, catalog=catalog))
    check = get_check(result, "cost_estimates")
    assert check.passed is False
    assert check.reason_code == "reviewed_cost_mismatch"


def test_load_phase0_inputs_rejects_invalid_organization_yaml(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "config").mkdir(parents=True)
    for relative in (
        "config/workload-catalog.yaml",
        "config/policy.yaml",
        "fixtures/evidence/github-plan.sanitized.json",
        "fixtures/evidence/service-quotas.sanitized.json",
        "fixtures/manifests/cpu-routine.yaml",
        "fixtures/manifests/gpu-routine.yaml",
        "fixtures/manifests/gpu-exception.yaml",
    ):
        source = PROJECT_ROOT / relative
        destination = repo_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (repo_root / "config" / "organization.yaml").write_text(
        "admins: []\nteam_leads: []\nmembers: []\npilot_repositories: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as exc_info:
        load_phase0_inputs(repo_root)
    assert exc_info.value.errors()[0]["loc"] == ("members",)


def test_validate_phase0_exits_one_for_current_repository_state() -> None:
    completed = subprocess.run(
        [sys.executable, "tools/validate_phase0.py"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["passed"] is False
    failing = [check for check in payload["checks"] if not check["passed"]]
    assert [check["check_id"] for check in failing] == ["github_plan"]


def test_validate_phase0_exits_two_for_unreadable_inputs(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    completed = subprocess.run(
        [sys.executable, "tools/validate_phase0.py"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert completed.stdout == ""


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
            estimated_cost_usd=estimated_cost,
        )
        assert classify_request(facts, policy.thresholds) == expected_classification(filename)
