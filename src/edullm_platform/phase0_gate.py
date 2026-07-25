from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal, Protocol

from pydantic import computed_field

from edullm_platform.config import load_yaml
from edullm_platform.contracts.base import ContractModel
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.policy import (
    ApprovalClass,
    ApprovalPolicy,
    RequestFacts,
    classify_request,
)
from edullm_platform.contracts.workload import CostInputs, WorkloadCatalog

CapacityVerdict = Literal["verified", "increase_required", "blocked"]


class GitHubPlanEvidenceLike(Protocol):
    organization: str
    plan_name: str


class ServiceQuotasEvidenceLike(Protocol):
    environment: str
    region: str
    capacity_verdict: CapacityVerdict
    capacity_verdict_note: str

EXPECTED_ADMINS: Final = ("philote-dev", "BritishAmericqn")
EXPECTED_PILOTS: Final = ("OLMo-core", "dolma")
EXPECTED_GITHUB_ORG: Final = "edu-llm"
EXPECTED_AWS_REGION: Final = "us-east-1"
REGISTERED_DATASET_RELEASES: Final = frozenset({"dolma-2026-07"})

REQUIRED_DENIED_OUTRIGHT: Final = (
    "unregistered_repository",
    "unregistered_dataset",
    "unregistered_compute_profile",
    "mutable_repository_revision",
    "mutable_image_reference",
)

REPRESENTATIVE_MANIFEST_FILENAMES: Final = (
    "cpu-routine.yaml",
    "gpu-routine.yaml",
    "gpu-exception.yaml",
)

REVIEWED_MANIFEST_COSTS: Final = {
    "cpu-routine.yaml": Decimal("2.86"),
    "gpu-routine.yaml": Decimal("5.67"),
    "gpu-exception.yaml": Decimal("73.74"),
}

PHASE1_PRIVATE_REPO_GITHUB_PLANS: Final = frozenset({"team", "enterprise"})

EXPECTED_CHECK_IDS: Final = frozenset(
    {
        "ownership",
        "pilots",
        "workload_coverage",
        "approval_paths",
        "checkpoint_expectations",
        "github_plan",
        "aws_capacity",
        "representative_manifests",
        "cost_estimates",
    }
)

COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class GateCheck(ContractModel):
    check_id: str
    passed: bool
    reason_code: str
    detail: str


class Phase0GateResult(ContractModel):
    checks: tuple[GateCheck, ...]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


@dataclass(frozen=True)
class Phase0Inputs:
    inventory: OrganizationInventory
    catalog: WorkloadCatalog
    policy: ApprovalPolicy
    github_plan: GitHubPlanEvidenceLike
    aws_capacity: ServiceQuotasEvidenceLike
    manifests: tuple[tuple[str, RunManifest], ...]


def ok_check(check_id: str, detail: str) -> GateCheck:
    return GateCheck(check_id=check_id, passed=True, reason_code="ok", detail=detail)


def fail_check(check_id: str, reason_code: str, detail: str) -> GateCheck:
    return GateCheck(check_id=check_id, passed=False, reason_code=reason_code, detail=detail)


def load_json_fixture(path: Path, model_type: type[ContractModel]) -> ContractModel:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return model_type.model_validate(payload)


def evidence_models() -> tuple[type[ContractModel], type[ContractModel]]:
    import sys

    tools_dir = Path(__file__).resolve().parents[2] / "tools"
    tools_dir_str = str(tools_dir)
    if tools_dir_str not in sys.path:
        sys.path.insert(0, tools_dir_str)
    from capture_phase0_evidence import GitHubPlanEvidence, ServiceQuotasEvidence

    return GitHubPlanEvidence, ServiceQuotasEvidence


def load_representative_manifests(repo_root: Path) -> tuple[tuple[str, RunManifest], ...]:
    manifest_dir = repo_root / "fixtures" / "manifests"
    manifests: list[tuple[str, RunManifest]] = []
    for filename in REPRESENTATIVE_MANIFEST_FILENAMES:
        manifests.append((filename, load_yaml(manifest_dir / filename, RunManifest)))
    return tuple(manifests)


def load_phase0_inputs(repo_root: Path) -> Phase0Inputs:
    github_plan_model, service_quotas_model = evidence_models()
    return Phase0Inputs(
        inventory=load_yaml(repo_root / "config" / "organization.yaml", OrganizationInventory),
        catalog=load_yaml(repo_root / "config" / "workload-catalog.yaml", WorkloadCatalog),
        policy=load_yaml(repo_root / "config" / "policy.yaml", ApprovalPolicy),
        github_plan=load_json_fixture(  # type: ignore[arg-type]
            repo_root / "fixtures" / "evidence" / "github-plan.sanitized.json",
            github_plan_model,
        ),
        aws_capacity=load_json_fixture(  # type: ignore[arg-type]
            repo_root / "fixtures" / "evidence" / "service-quotas.sanitized.json",
            service_quotas_model,
        ),
        manifests=load_representative_manifests(repo_root),
    )


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
    profile_by_name = {profile.name: profile for profile in catalog.compute_profiles}
    profile = profile_by_name[manifest.compute_profile]
    return CostInputs(
        hourly_rate_usd=profile.hourly_rate_usd,
        nodes=profile.nodes,
        maximum_runtime_hours=manifest.maximum_runtime_hours,
        maximum_attempts=manifest.maximum_attempts,
    ).maximum_compute_cost_usd


def expected_manifest_classification(filename: str) -> ApprovalClass:
    if filename.endswith("-exception.yaml"):
        return ApprovalClass.EXCEPTION
    if filename.endswith("-routine.yaml"):
        return ApprovalClass.ROUTINE
    raise ValueError(f"unexpected representative manifest filename: {filename}")


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


def check_ownership(inventory: OrganizationInventory) -> GateCheck:
    if inventory.admins != EXPECTED_ADMINS:
        return fail_check(
            "ownership",
            "admin_roster_mismatch",
            (
                "Phase 0 requires exactly Frank Gonzalez (philote-dev) and Benjamin "
                f"(BritishAmericqn) as platform admins; got {inventory.admins!r}."
            ),
        )
    member_logins = [member.github_login for member in inventory.members]
    if len(member_logins) != len(set(member_logins)):
        return fail_check(
            "ownership",
            "duplicate_member_login",
            "Organization roster must deduplicate members by GitHub login.",
        )
    unknown_roles = (set(inventory.admins) | set(inventory.team_leads)) - set(member_logins)
    if unknown_roles:
        return fail_check(
            "ownership",
            "role_not_in_roster",
            f"Every admin and team lead must appear in the member roster: {sorted(unknown_roles)!r}.",
        )
    if len(inventory.team_leads) != 8 or len(set(inventory.team_leads)) != 8:
        return fail_check(
            "ownership",
            "team_lead_roster_invalid",
            "Phase 0 requires exactly eight distinct team leads recorded in the roster.",
        )
    return ok_check(
        "ownership",
        "Platform admins, team leads, and member roster satisfy Phase 0 ownership requirements.",
    )


def check_pilots(inventory: OrganizationInventory) -> GateCheck:
    if inventory.pilot_repositories != EXPECTED_PILOTS:
        return fail_check(
            "pilots",
            "pilot_repository_mismatch",
            (
                "Phase 0 requires OLMo-core and dolma as the two pilot repositories; "
                f"got {inventory.pilot_repositories!r}."
            ),
        )
    return ok_check(
        "pilots",
        "OLMo-core and dolma are recorded as the two Phase 0 pilot repositories.",
    )


def check_workload_coverage(catalog: WorkloadCatalog) -> GateCheck:
    profile_by_name = {profile.name: profile for profile in catalog.compute_profiles}
    accelerators = {
        profile_by_name[workload.compute_profile].accelerator for workload in catalog.workloads
    }
    if accelerators != {"cpu", "gpu"}:
        missing: list[str] = []
        if "cpu" not in accelerators:
            missing.append("cpu")
        if "gpu" not in accelerators:
            missing.append("gpu")
        reason = "missing_gpu_representative" if missing == ["gpu"] else "missing_cpu_representative"
        if len(missing) == 2:
            reason = "missing_cpu_and_gpu_representatives"
        return fail_check(
            "workload_coverage",
            reason,
            "Representative CPU and GPU workloads must both be explicit in the workload catalog.",
        )
    return ok_check(
        "workload_coverage",
        "Workload catalog includes explicit representative CPU and GPU workloads.",
    )


def check_approval_paths(policy: ApprovalPolicy) -> GateCheck:
    missing_denials = [
        condition
        for condition in REQUIRED_DENIED_OUTRIGHT
        if condition not in policy.denied_outright
    ]
    if missing_denials:
        return fail_check(
            "approval_paths",
            "denied_outright_incomplete",
            (
                "Approval policy must enumerate routine, exception, and outright-denial paths; "
                f"missing denied_outright conditions: {missing_denials!r}."
            ),
        )
    if policy.routine_approver_role != "team_lead":
        return fail_check(
            "approval_paths",
            "routine_approver_missing",
            "Routine approvals must route to team_lead.",
        )
    if "platform_admin" not in policy.exception_approver_roles:
        return fail_check(
            "approval_paths",
            "exception_approver_missing",
            "Exception approvals must include platform_admin.",
        )
    return ok_check(
        "approval_paths",
        "Routine, exception, and outright-denial approval paths are explicit and reviewed.",
    )


def check_checkpoint_expectations(catalog: WorkloadCatalog) -> GateCheck:
    for workload in catalog.workloads:
        if workload.maximum_attempts > 1 and workload.checkpoint is None:
            return fail_check(
                "checkpoint_expectations",
                "retry_missing_checkpoint",
                (
                    f"Workload {workload.name!r} allows retries but does not declare a "
                    "checkpoint contract."
                ),
            )
    return ok_check(
        "checkpoint_expectations",
        "Every representative workload declares explicit retry and checkpoint expectations.",
    )


def check_github_plan(evidence: GitHubPlanEvidenceLike) -> GateCheck:
    if evidence.organization != EXPECTED_GITHUB_ORG:
        return fail_check(
            "github_plan",
            "organization_mismatch",
            (
                f"GitHub plan evidence must describe organization {EXPECTED_GITHUB_ORG!r}; "
                f"got {evidence.organization!r}."
            ),
        )
    plan_name = evidence.plan_name.lower()
    if plan_name not in PHASE1_PRIVATE_REPO_GITHUB_PLANS:
        return fail_check(
            "github_plan",
            "plan_insufficient_for_private_repo_controls",
            (
                f"GitHub organization plan {evidence.plan_name!r} does not support protected "
                "branch rulesets or CODEOWNERS-backed review assignment on private repositories. "
                "Team or Enterprise is required before Phase 1 can enforce the platform control "
                "plane on the private platform repository."
            ),
        )
    return ok_check(
        "github_plan",
        (
            f"GitHub organization plan {evidence.plan_name!r} supports Phase 1 private-repository "
            "governance controls."
        ),
    )


def check_aws_capacity(evidence: ServiceQuotasEvidenceLike) -> GateCheck:
    if evidence.environment != "sandbox":
        return fail_check(
            "aws_capacity",
            "wrong_environment",
            "AWS capacity evidence must be labeled environment: sandbox.",
        )
    if evidence.region != EXPECTED_AWS_REGION:
        return fail_check(
            "aws_capacity",
            "wrong_region",
            f"AWS capacity evidence must cover region {EXPECTED_AWS_REGION!r}.",
        )
    if evidence.capacity_verdict == "verified":
        return ok_check(
            "aws_capacity",
            "Sandbox applied GPU and Batch quotas in us-east-1 satisfy representative workloads.",
        )
    if evidence.capacity_verdict == "increase_required":
        return fail_check(
            "aws_capacity",
            "capacity_increase_required",
            evidence.capacity_verdict_note,
        )
    return fail_check(
        "aws_capacity",
        "capacity_blocked",
        evidence.capacity_verdict_note,
    )


def check_representative_manifests(
    *,
    inventory: OrganizationInventory,
    catalog: WorkloadCatalog,
    policy: ApprovalPolicy,
    manifests: tuple[tuple[str, RunManifest], ...],
) -> GateCheck:
    manifest_names = {filename for filename, _manifest in manifests}
    missing = [
        filename
        for filename in REPRESENTATIVE_MANIFEST_FILENAMES
        if filename not in manifest_names
    ]
    if missing:
        return fail_check(
            "representative_manifests",
            "missing_manifest",
            f"Representative manifests are missing: {missing!r}.",
        )
    for filename, manifest in manifests:
        estimated_cost = compute_manifest_maximum_cost(manifest, catalog)
        facts = request_facts_from_manifest(
            manifest,
            inventory=inventory,
            catalog=catalog,
            estimated_cost_usd=estimated_cost,
        )
        expected = expected_manifest_classification(filename)
        actual = classify_request(facts, policy.thresholds)
        if actual != expected:
            return fail_check(
                "representative_manifests",
                "classification_mismatch",
                (
                    f"Manifest {filename!r} classifies as {actual.value}; "
                    f"expected {expected.value}."
                ),
            )
        if not is_workload_profile_registered(manifest, catalog):
            return fail_check(
                "representative_manifests",
                "unregistered_workload_profile",
                f"Manifest {filename!r} references unregistered workload profile "
                f"{manifest.workload_profile!r}.",
            )
    return ok_check(
        "representative_manifests",
        "CPU routine, GPU routine, and GPU exception manifests validate and classify as expected.",
    )


def check_cost_estimates(
    *,
    catalog: WorkloadCatalog,
    policy: ApprovalPolicy,
    manifests: tuple[tuple[str, RunManifest], ...],
) -> GateCheck:
    for profile in catalog.compute_profiles:
        if not profile.pricing_source or not profile.pricing_observed_at:
            return fail_check(
                "cost_estimates",
                "missing_pricing_metadata",
                f"Compute profile {profile.name!r} must include source-dated pricing metadata.",
            )
    for filename, manifest in manifests:
        if filename not in REVIEWED_MANIFEST_COSTS:
            return fail_check(
                "cost_estimates",
                "missing_reviewed_cost",
                f"No reviewed maximum cost is recorded for manifest {filename!r}.",
            )
        estimated_cost = compute_manifest_maximum_cost(manifest, catalog)
        reviewed_cost = REVIEWED_MANIFEST_COSTS[filename]
        if estimated_cost != reviewed_cost:
            return fail_check(
                "cost_estimates",
                "reviewed_cost_mismatch",
                (
                    f"Manifest {filename!r} maximum cost {estimated_cost} does not match the "
                    f"reviewed expectation {reviewed_cost}."
                ),
            )
        if estimated_cost > policy.thresholds.routine_maximum_cost_usd:
            return fail_check(
                "cost_estimates",
                "exceeds_program_budget",
                (
                    f"Manifest {filename!r} maximum cost {estimated_cost} exceeds the reviewed "
                    f"program budget ceiling {policy.thresholds.routine_maximum_cost_usd}."
                ),
            )
    return ok_check(
        "cost_estimates",
        "Representative maximum costs are deterministic, source-dated, and within the program budget.",
    )


def evaluate_phase0(inputs: Phase0Inputs) -> Phase0GateResult:
    checks = (
        check_ownership(inputs.inventory),
        check_pilots(inputs.inventory),
        check_workload_coverage(inputs.catalog),
        check_approval_paths(inputs.policy),
        check_checkpoint_expectations(inputs.catalog),
        check_github_plan(inputs.github_plan),
        check_aws_capacity(inputs.aws_capacity),
        check_representative_manifests(
            inventory=inputs.inventory,
            catalog=inputs.catalog,
            policy=inputs.policy,
            manifests=inputs.manifests,
        ),
        check_cost_estimates(
            catalog=inputs.catalog,
            policy=inputs.policy,
            manifests=inputs.manifests,
        ),
    )
    return Phase0GateResult(checks=checks)


def evaluate_repository(repo_root: Path) -> Phase0GateResult:
    return evaluate_phase0(load_phase0_inputs(repo_root))
