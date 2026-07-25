from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from edullm_platform.contracts.policy import classify_request
from edullm_platform.manifest_helpers import (
    compute_manifest_maximum_cost,
    load_manifest,
)
from edullm_platform.phase0_gate import (
    EXPECTED_CHECK_IDS,
    REVIEWED_MANIFEST_COSTS,
    Phase0GateResult,
    Phase0Inputs,
    evaluate_phase0,
    evaluate_repository,
    expected_manifest_classification,
    load_phase0_inputs,
    request_facts_from_manifest,
)
from tests.test_manifest import REPRESENTATIVE_MANIFEST_COSTS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALIDATE_CLI = PROJECT_ROOT / "tools" / "validate_phase0.py"


def get_check(result: Phase0GateResult, check_id: str):
    matching = [check for check in result.checks if check.check_id == check_id]
    assert len(matching) == 1, f"expected one check {check_id!r}, got {result.checks}"
    return matching[0]


def loaded_inputs() -> Phase0Inputs:
    return load_phase0_inputs(PROJECT_ROOT)


def run_validate_phase0(repo_root: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(repo_root / "src"), str(repo_root)])
    return subprocess.run(
        [sys.executable, str(repo_root / "tools" / "validate_phase0.py")],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def copy_gate_repo(destination: Path) -> Path:
    repo_root = destination / "repo"
    for relative in (
        "config",
        "fixtures",
        "src",
        "tools",
        "pyproject.toml",
    ):
        source = PROJECT_ROOT / relative
        target = repo_root / relative
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return repo_root


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
    team_plan = inputs.github_plan
    assert team_plan is not None
    result = evaluate_phase0(
        replace(inputs, github_plan=team_plan.model_copy(update={"plan_name": "team"}))
    )
    assert result.passed is True
    assert all(check.passed for check in result.checks)
    assert all(check.reason_code == "ok" for check in result.checks)


def test_gate_executes_every_check_even_after_failure() -> None:
    inputs = loaded_inputs()
    broken_inventory = inputs.inventory.model_copy(update={"admins": ("philote-dev", "philote-dev")})
    team_plan = inputs.github_plan
    assert team_plan is not None
    result = evaluate_phase0(
        replace(
            inputs,
            inventory=broken_inventory,
            github_plan=team_plan.model_copy(update={"plan_name": "team"}),
        )
    )
    assert result.passed is False
    assert len(result.checks) == len(EXPECTED_CHECK_IDS)
    assert not get_check(result, "ownership").passed
    assert get_check(result, "github_plan").passed


@pytest.mark.parametrize("check_id", sorted(EXPECTED_CHECK_IDS))
def test_passing_gate_reports_ok_reason_code(check_id: str) -> None:
    inputs = loaded_inputs()
    team_plan = inputs.github_plan
    assert team_plan is not None
    result = evaluate_phase0(
        replace(inputs, github_plan=team_plan.model_copy(update={"plan_name": "team"}))
    )
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


def test_approval_paths_fails_when_routine_approver_missing() -> None:
    inputs = loaded_inputs()
    policy = inputs.policy.model_copy(update={"routine_approver_role": "platform_admin"})
    result = evaluate_phase0(replace(inputs, policy=policy))
    check = get_check(result, "approval_paths")
    assert check.passed is False
    assert check.reason_code == "routine_approver_missing"


def test_approval_paths_fails_when_exception_approver_missing() -> None:
    inputs = loaded_inputs()
    policy = inputs.policy.model_copy(update={"exception_approver_roles": ("team_lead",)})
    result = evaluate_phase0(replace(inputs, policy=policy))
    check = get_check(result, "approval_paths")
    assert check.passed is False
    assert check.reason_code == "exception_approver_missing"


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


def test_github_plan_fails_for_organization_mismatch() -> None:
    inputs = loaded_inputs()
    github_plan = inputs.github_plan
    assert github_plan is not None
    result = evaluate_phase0(
        replace(inputs, github_plan=github_plan.model_copy(update={"organization": "other-org"}))
    )
    check = get_check(result, "github_plan")
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
    check = get_check(result, "aws_capacity")
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
    check = get_check(result, "aws_capacity")
    assert check.passed is False
    assert check.reason_code == "capacity_increase_required"


def test_aws_capacity_fails_for_wrong_region() -> None:
    inputs = loaded_inputs()
    aws_capacity = inputs.aws_capacity
    assert aws_capacity is not None
    result = evaluate_phase0(
        replace(inputs, aws_capacity=aws_capacity.model_copy(update={"region": "us-west-2"}))
    )
    check = get_check(result, "aws_capacity")
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
    check = get_check(result, "aws_capacity")
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
    check = get_check(result, "representative_manifests")
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
    check = get_check(result, "representative_manifests")
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
    check = get_check(result, "representative_manifests")
    assert check.passed is False
    assert check.reason_code == "unregistered_dataset"


def test_representative_manifests_fails_on_classification_mismatch() -> None:
    inputs = loaded_inputs()
    manifest = load_manifest(PROJECT_ROOT / "fixtures" / "manifests" / "gpu-exception.yaml")
    broken_manifest = manifest.model_copy(update={"maximum_runtime_hours": Decimal(1)})
    manifests = tuple(
        (filename, broken_manifest if filename == "gpu-exception.yaml" else current)
        for filename, current in inputs.manifests
    )
    result = evaluate_phase0(replace(inputs, manifests=manifests))
    check = get_check(result, "representative_manifests")
    assert check.passed is False
    assert check.reason_code == "classification_mismatch"


def test_representative_manifests_fails_for_unreviewed_manifest(tmp_path: Path) -> None:
    inputs = loaded_inputs()
    extra_manifest = inputs.manifests[0][1].model_copy(
        update={"maximum_runtime_hours": Decimal(999)}
    )
    manifests = (*inputs.manifests, ("extra-long.yaml", extra_manifest))
    result = evaluate_phase0(replace(inputs, manifests=manifests))
    check = get_check(result, "representative_manifests")
    assert check.passed is False
    assert check.reason_code == "unexpected_manifest"


def test_cost_estimates_fails_when_reviewed_cost_does_not_match() -> None:
    inputs = loaded_inputs()
    profiles = list(inputs.catalog.compute_profiles)
    profiles[0] = profiles[0].model_copy(update={"hourly_rate_usd": Decimal("9.999")})
    catalog = inputs.catalog.model_copy(update={"compute_profiles": tuple(profiles)})
    result = evaluate_phase0(replace(inputs, catalog=catalog))
    check = get_check(result, "cost_estimates")
    assert check.passed is False
    assert check.reason_code == "reviewed_cost_mismatch"


def test_cost_estimates_fails_when_routine_manifest_exceeds_program_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = loaded_inputs()
    profiles = list(inputs.catalog.compute_profiles)
    profiles[0] = profiles[0].model_copy(update={"hourly_rate_usd": Decimal(300)})
    catalog = inputs.catalog.model_copy(update={"compute_profiles": tuple(profiles)})
    monkeypatch.setitem(REVIEWED_MANIFEST_COSTS, "cpu-routine.yaml", Decimal("600.00"))
    result = evaluate_phase0(replace(inputs, catalog=catalog))
    check = get_check(result, "cost_estimates")
    assert check.passed is False
    assert check.reason_code == "exceeds_program_budget"


def test_cost_estimates_does_not_apply_program_budget_to_exception_manifest() -> None:
    inputs = loaded_inputs()
    manifests = tuple(
        (
            name,
            manifest.model_copy(update={"maximum_runtime_hours": Decimal(100)})
            if name == "gpu-exception.yaml"
            else manifest,
        )
        for name, manifest in inputs.manifests
    )
    result = evaluate_phase0(replace(inputs, manifests=manifests))
    check = get_check(result, "cost_estimates")
    assert check.reason_code == "reviewed_cost_mismatch"
    assert check.reason_code != "exceeds_program_budget"


def test_unregistered_compute_profile_cli_emits_structured_json(tmp_path: Path) -> None:
    repo_root = copy_gate_repo(tmp_path)
    manifest_path = repo_root / "fixtures" / "manifests" / "cpu-routine.yaml"
    text = manifest_path.read_text(encoding="utf-8").replace("cpu-32vcpu", "not-a-registered-profile")
    manifest_path.write_text(text, encoding="utf-8")
    completed = run_validate_phase0(repo_root)
    assert completed.returncode == 1
    assert completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["passed"] is False
    failing = [check for check in payload["checks"] if not check["passed"]]
    assert any(check["reason_code"] == "unregistered_compute_profile" for check in failing)


def test_missing_gpu_quota_cli_emits_structured_json(tmp_path: Path) -> None:
    repo_root = copy_gate_repo(tmp_path)
    evidence_path = repo_root / "fixtures" / "evidence" / "service-quotas.sanitized.json"
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    payload["quotas"] = [
        quota
        for quota in payload["quotas"]
        if quota.get("workload_profile") != "gpu-4xa10g"
    ]
    payload["capacity_verdict"] = "blocked"
    payload["capacity_verdict_note"] = (
        "Capacity review blocked because representative workload mapping is incomplete."
    )
    evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    completed = run_validate_phase0(repo_root)
    assert completed.returncode == 1
    assert completed.stdout
    parsed = json.loads(completed.stdout)
    aws_check = next(check for check in parsed["checks"] if check["check_id"] == "aws_capacity")
    assert aws_check["passed"] is False
    assert aws_check["reason_code"] == "capacity_blocked"


def test_load_phase0_inputs_rejects_invalid_organization_yaml(tmp_path: Path) -> None:
    repo_root = copy_gate_repo(tmp_path)
    (repo_root / "config" / "organization.yaml").write_text(
        "admins: []\nteam_leads: []\nmembers: []\npilot_repositories: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as exc_info:
        load_phase0_inputs(repo_root)
    assert exc_info.value.errors()[0]["loc"] == ("members",)


def test_validate_phase0_exits_one_for_current_repository_state() -> None:
    completed = run_validate_phase0(PROJECT_ROOT)
    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["passed"] is False
    failing = [check for check in payload["checks"] if not check["passed"]]
    assert [check["check_id"] for check in failing] == ["github_plan"]


def test_validate_phase0_exits_zero_when_all_checks_pass(tmp_path: Path) -> None:
    repo_root = copy_gate_repo(tmp_path)
    evidence_path = repo_root / "fixtures" / "evidence" / "github-plan.sanitized.json"
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    payload["plan_name"] = "team"
    evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    completed = run_validate_phase0(repo_root)
    assert completed.returncode == 0, completed.stderr
    parsed = json.loads(completed.stdout)
    assert parsed["passed"] is True


def test_validate_phase0_exits_two_for_invalid_config(tmp_path: Path) -> None:
    repo_root = copy_gate_repo(tmp_path)
    (repo_root / "config" / "organization.yaml").write_text(
        "admins: []\nteam_leads: []\nmembers: []\npilot_repositories: []\n",
        encoding="utf-8",
    )
    completed = run_validate_phase0(repo_root)
    assert completed.returncode == 2
    assert completed.stdout == ""


def test_evidence_stale_fails_github_plan_and_aws_capacity_checks() -> None:
    inputs = loaded_inputs()
    github_plan = inputs.github_plan
    aws_capacity = inputs.aws_capacity
    assert github_plan is not None and aws_capacity is not None
    result = evaluate_phase0(
        replace(
            inputs,
            github_plan=None,
            github_plan_load_error="evidence_stale",
            aws_capacity=None,
            aws_capacity_load_error="evidence_stale",
        )
    )
    github_check = get_check(result, "github_plan")
    aws_check = get_check(result, "aws_capacity")
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
            estimated_cost_usd=estimated_cost,
        )
        assert classify_request(facts, policy.thresholds) == expected_manifest_classification(filename)
