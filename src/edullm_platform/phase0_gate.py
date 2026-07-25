from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, TypeVar, cast

from pydantic import ValidationError, computed_field

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
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.evidence import (
    BatchQuotaRecord,
    GitHubPlanEvidence,
    QuotaRecord,
    ServiceQuotasEvidence,
    batch_quota_issues,
    ec2_quota_coverage_issues,
    evidence_load_reason_code,
    validate_observed_at,
)
from edullm_platform.manifest_helpers import (
    compute_manifest_maximum_cost,
    is_compute_profile_registered,
    is_workload_profile_registered,
    load_manifests_from_directory,
    manifest_has_immutable_image,
    manifest_has_immutable_revision,
)

T = TypeVar("T", bound=ContractModel)

EXPECTED_ADMINS: Final = ("philote-dev", "BritishAmericqn")
EXPECTED_PILOTS: Final = ("OLMo-core", "dolma")
EXPECTED_GITHUB_ORG: Final = "edu-llm"
EXPECTED_AWS_REGION: Final = "us-east-1"
REGISTERED_DATASET_RELEASES: Final = frozenset({"dolma-2026-07"})
PROGRAM_MAXIMUM_COST_USD: Final = Decimal(500)

REQUIRED_DENIED_OUTRIGHT: Final = (
    "unregistered_repository",
    "unregistered_dataset",
    "unregistered_compute_profile",
    "mutable_repository_revision",
    "mutable_image_reference",
)

REQUIRED_REPRESENTATIVE_MANIFESTS: Final = frozenset(
    {
        "cpu-routine.yaml",
        "gpu-routine.yaml",
        "gpu-exception.yaml",
    }
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

STALE_EVIDENCE_DETAIL: Final = (
    "Operational evidence is stale; re-run tools/capture_phase0_evidence.py to refresh it."
)


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
    github_plan: GitHubPlanEvidence | None
    github_plan_load_error: str | None
    aws_capacity: ServiceQuotasEvidence | None
    aws_capacity_load_error: str | None
    manifests: tuple[tuple[str, RunManifest], ...]


def ok_check(check_id: str, detail: str) -> GateCheck:
    return GateCheck(check_id=check_id, passed=True, reason_code="ok", detail=detail)


def fail_check(check_id: str, reason_code: str, detail: str) -> GateCheck:
    return GateCheck(check_id=check_id, passed=False, reason_code=reason_code, detail=detail)


def load_github_plan_evidence(path: Path) -> tuple[GitHubPlanEvidence | None, str | None]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    try:
        return GitHubPlanEvidence.model_validate(payload), None
    except ValidationError as exc:
        return None, evidence_load_reason_code(exc)


def load_aws_capacity_evidence(path: Path) -> tuple[ServiceQuotasEvidence | None, str | None]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None, "evidence_invalid"
    try:
        return ServiceQuotasEvidence.model_validate(payload), None
    except ValidationError as exc:
        reason = evidence_load_reason_code(exc)
        if reason == "evidence_stale":
            return None, reason
        return load_service_quotas_evidence_lenient(payload)


def load_service_quotas_evidence_lenient(
    payload: dict[str, object],
) -> tuple[ServiceQuotasEvidence | None, str | None]:
    try:
        raw_quotas = payload["quotas"]
        raw_batch_quotas = payload["batch_quotas"]
        if not isinstance(raw_quotas, list) or not isinstance(raw_batch_quotas, list):
            return None, "evidence_invalid"
        quotas = tuple(QuotaRecord.model_validate(quota) for quota in raw_quotas)
        batch_quotas = tuple(
            BatchQuotaRecord.model_validate(quota) for quota in raw_batch_quotas
        )
        observed_at = payload.get("observed_at")
        if not isinstance(observed_at, str):
            return None, "evidence_invalid"
        validate_observed_at(datetime.fromisoformat(observed_at.removesuffix("Z") + "+00:00"))
    except (ValidationError, ValueError, KeyError, TypeError):
        return None, "evidence_invalid"
    return (
        ServiceQuotasEvidence.model_construct(
            **cast(Any, {
                key: value
                for key, value in payload.items()
                if key not in {"quotas", "batch_quotas"}
            }),
            quotas=quotas,
            batch_quotas=batch_quotas,
        ),
        None,
    )


def load_json_evidence[T: ContractModel](path: Path, model_type: type[T]) -> tuple[T | None, str | None]:
    if model_type is GitHubPlanEvidence:
        github_plan, error = load_github_plan_evidence(path)
        return cast(tuple[T | None, str | None], (github_plan, error))
    if model_type is ServiceQuotasEvidence:
        aws_capacity, error = load_aws_capacity_evidence(path)
        return cast(tuple[T | None, str | None], (aws_capacity, error))
    payload = json.loads(path.read_text(encoding="utf-8"))
    try:
        return model_type.model_validate(payload), None
    except ValidationError as exc:
        return None, evidence_load_reason_code(exc)


def load_phase0_inputs(repo_root: Path) -> Phase0Inputs:
    github_plan, github_plan_load_error = load_json_evidence(
        repo_root / "fixtures" / "evidence" / "github-plan.sanitized.json",
        GitHubPlanEvidence,
    )
    aws_capacity, aws_capacity_load_error = load_json_evidence(
        repo_root / "fixtures" / "evidence" / "service-quotas.sanitized.json",
        ServiceQuotasEvidence,
    )
    return Phase0Inputs(
        inventory=load_yaml(repo_root / "config" / "organization.yaml", OrganizationInventory),
        catalog=load_yaml(repo_root / "config" / "workload-catalog.yaml", WorkloadCatalog),
        policy=load_yaml(repo_root / "config" / "policy.yaml", ApprovalPolicy),
        github_plan=github_plan,
        github_plan_load_error=github_plan_load_error,
        aws_capacity=aws_capacity,
        aws_capacity_load_error=aws_capacity_load_error,
        manifests=load_manifests_from_directory(repo_root / "fixtures" / "manifests"),
    )


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
        "Retryable representative workloads declare checkpoint contracts; single-attempt "
        "workloads declare checkpoint: null explicitly.",
    )


def check_github_plan(
    evidence: GitHubPlanEvidence | None,
    load_error: str | None,
) -> GateCheck:
    if load_error == "evidence_stale":
        return fail_check("github_plan", "evidence_stale", STALE_EVIDENCE_DETAIL)
    if evidence is None:
        return fail_check(
            "github_plan",
            "evidence_invalid",
            "GitHub plan evidence failed schema validation.",
        )
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


def check_aws_capacity(
    evidence: ServiceQuotasEvidence | None,
    load_error: str | None,
    catalog: WorkloadCatalog,
) -> GateCheck:
    if load_error == "evidence_stale":
        return fail_check("aws_capacity", "evidence_stale", STALE_EVIDENCE_DETAIL)
    if evidence is None:
        return fail_check(
            "aws_capacity",
            "evidence_invalid",
            "AWS capacity evidence failed schema validation.",
        )
    if evidence.region != EXPECTED_AWS_REGION:
        return fail_check(
            "aws_capacity",
            "wrong_region",
            f"AWS capacity evidence must cover region {EXPECTED_AWS_REGION!r}.",
        )
    reason_code, detail = ec2_quota_coverage_issues(catalog=catalog, quotas=evidence.quotas)
    if reason_code is not None and detail is not None:
        return fail_check("aws_capacity", reason_code, detail)
    batch_issues = batch_quota_issues(evidence.batch_quotas)
    if batch_issues:
        return fail_check(
            "aws_capacity",
            "capacity_blocked",
            "Batch quota evidence is incomplete: " + "; ".join(batch_issues),
        )
    return ok_check(
        "aws_capacity",
        "Sandbox applied GPU and Batch quotas in us-east-1 satisfy representative workloads.",
    )


def check_representative_manifests(
    *,
    inventory: OrganizationInventory,
    catalog: WorkloadCatalog,
    policy: ApprovalPolicy,
    manifests: tuple[tuple[str, RunManifest], ...],
) -> GateCheck:
    manifest_names = {filename for filename, _manifest in manifests}
    missing_required = sorted(REQUIRED_REPRESENTATIVE_MANIFESTS - manifest_names)
    if missing_required:
        return fail_check(
            "representative_manifests",
            "missing_required_manifest",
            f"Required representative manifests are missing: {missing_required!r}.",
        )
    unexpected = sorted(manifest_names - set(REVIEWED_MANIFEST_COSTS))
    if unexpected:
        return fail_check(
            "representative_manifests",
            "unexpected_manifest",
            (
                "Every manifest under fixtures/manifests/ must have a reviewed cost expectation; "
                f"unexpected files: {unexpected!r}."
            ),
        )
    for filename, manifest in manifests:
        if not is_compute_profile_registered(manifest, catalog):
            return fail_check(
                "representative_manifests",
                "unregistered_compute_profile",
                (
                    f"Manifest {filename!r} references unregistered compute profile "
                    f"{manifest.compute_profile!r}."
                ),
            )
        if not is_workload_profile_registered(manifest, catalog):
            return fail_check(
                "representative_manifests",
                "unregistered_workload_profile",
                (
                    f"Manifest {filename!r} references unregistered workload profile "
                    f"{manifest.workload_profile!r}."
                ),
            )
        if manifest.dataset_release not in REGISTERED_DATASET_RELEASES:
            return fail_check(
                "representative_manifests",
                "unregistered_dataset",
                (
                    f"Manifest {filename!r} references unregistered dataset release "
                    f"{manifest.dataset_release!r}."
                ),
            )
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
    return ok_check(
        "representative_manifests",
        "CPU routine, GPU routine, and GPU exception manifests validate and classify as expected.",
    )


def check_cost_estimates(
    *,
    catalog: WorkloadCatalog,
    manifests: tuple[tuple[str, RunManifest], ...],
) -> GateCheck:
    for filename, manifest in manifests:
        if filename not in REVIEWED_MANIFEST_COSTS:
            return fail_check(
                "cost_estimates",
                "missing_reviewed_cost",
                f"No reviewed maximum cost is recorded for manifest {filename!r}.",
            )
        if not is_compute_profile_registered(manifest, catalog):
            return fail_check(
                "cost_estimates",
                "unregistered_compute_profile",
                (
                    f"Manifest {filename!r} references unregistered compute profile "
                    f"{manifest.compute_profile!r}."
                ),
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
        if filename.endswith("-routine.yaml") and estimated_cost > PROGRAM_MAXIMUM_COST_USD:
            return fail_check(
                "cost_estimates",
                "exceeds_program_budget",
                (
                    f"Manifest {filename!r} maximum cost {estimated_cost} exceeds the explicit "
                    f"program budget ceiling {PROGRAM_MAXIMUM_COST_USD}."
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
        check_github_plan(inputs.github_plan, inputs.github_plan_load_error),
        check_aws_capacity(inputs.aws_capacity, inputs.aws_capacity_load_error, inputs.catalog),
        check_representative_manifests(
            inventory=inputs.inventory,
            catalog=inputs.catalog,
            policy=inputs.policy,
            manifests=inputs.manifests,
        ),
        check_cost_estimates(
            catalog=inputs.catalog,
            manifests=inputs.manifests,
        ),
    )
    return Phase0GateResult(checks=checks)


def evaluate_repository(repo_root: Path) -> Phase0GateResult:
    return evaluate_phase0(load_phase0_inputs(repo_root))
