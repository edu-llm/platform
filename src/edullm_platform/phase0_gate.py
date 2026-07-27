from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Final, TypeVar, cast

from pydantic import BeforeValidator, Field, ValidationError, computed_field

from edullm_platform.config import load_yaml
from edullm_platform.contracts.base import (
    ContractModel,
    require_ordered_sequence,
)
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.policy import (
    ApprovalClass,
    ApprovalPolicy,
    RequestFacts,
    classify_request,
)
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.criteria import CriterionResult, execute_criteria
from edullm_platform.evidence import (
    EVIDENCE_STALE_CODE,
    GitHubPlanEvidence,
    ServiceQuotasEvidence,
    batch_quota_issues,
    ec2_quota_coverage_issues,
    evidence_load_reason_code,
)
from edullm_platform.manifest_helpers import (
    REPRESENTATIVE_MANIFEST_COSTS,
    build_request_facts,
    compute_manifest_maximum_cost,
    is_compute_profile_registered,
    is_workload_profile_registered,
    load_manifests_from_directory,
)
from edullm_platform.phase0_criteria import discover_fixtures, phase0_criteria

T = TypeVar("T", bound=ContractModel)

EXPECTED_ADMINS: Final = ("philote-dev", "BritishAmericqn")
EXPECTED_TEAM_LEADS: Final = (
    "philote-dev",
    "ericrcwu001",
    "alsy7009",
    "meric233",
    "syz2026",
    "gorpyshortlegs",
    "hiyasvyas",
    "pianomaster99",
)
EXPECTED_PILOTS: Final = ("OLMo-core", "dolma")
EXPECTED_GITHUB_ORG: Final = "edu-llm"
EXPECTED_GITHUB_REPOSITORY: Final = "platform"
EXPECTED_AWS_REGION: Final = "us-east-1"
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
        "olmo-branch-routine.yaml",
        "sagemaker-routine.yaml",
        "multiseed-routine.yaml",
    }
)

PHASE1_PRIVATE_REPO_GITHUB_PLANS: Final = frozenset({"team", "enterprise"})

OPERATIONAL_INVENTORY_CHECK_IDS: Final = frozenset(
    {
        "inventory_ownership",
        "inventory_pilots",
        "inventory_workload_coverage",
        "inventory_approval_paths",
        "inventory_checkpoint_expectations",
        "inventory_github_plan",
        "inventory_aws_capacity",
        "inventory_representative_manifests",
        "inventory_cost_estimates",
    }
)

STALE_EVIDENCE_DETAIL: Final = (
    "Operational evidence is stale; re-run tools/capture_phase0_evidence.py to refresh it."
)

PHASE_CRITERIA_NOTE: Final = (
    "phase_criteria are the thirteen Phase 0 acceptance criteria. Every pytest node id cited "
    "for a criterion was executed by this run. A criterion whose cited tests do not all exist "
    "and pass is a gap and fails the gate, whatever status the definition records. Only three "
    "statuses exist: covered passes, deferred passes and requires a written reason and a "
    "written trigger, gap fails."
)

OPERATIONAL_INVENTORY_NOTE: Final = (
    "operational_inventory_checks are NOT Phase 0 acceptance criteria. They came from an "
    "earlier definition of the phase and are retained because they are useful: they check that "
    "the roster, pilot repositories, workload catalog, approval paths, GitHub plan, AWS "
    "capacity, representative manifests, and reviewed costs are sane. All nine passing says "
    "nothing about whether Phase 0 is done. Read phase_criteria for that."
)

class GateCheck(ContractModel):
    check_id: str
    passed: bool
    reason_code: str
    detail: str


class Phase0GateResult(ContractModel):
    """The operational inventory checks. Not the phase acceptance criteria."""

    checks: Annotated[tuple[GateCheck, ...], BeforeValidator(require_ordered_sequence)] = Field(
        strict=False
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


class Phase0GateReport(ContractModel):
    """The whole gate: the thirteen phase criteria and the nine inventory checks.

    The two groups are reported separately so nobody reads the inventory checks as
    acceptance criteria again. The verdict is the AND of both.
    """

    phase_criteria: Annotated[
        tuple[CriterionResult, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(strict=False)
    operational_inventory_checks: Annotated[
        tuple[GateCheck, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(strict=False)
    phase_criteria_note: str = PHASE_CRITERIA_NOTE
    operational_inventory_note: str = OPERATIONAL_INVENTORY_NOTE

    @computed_field  # type: ignore[prop-decorator]
    @property
    def phase_criteria_passed(self) -> bool:
        return all(criterion.passed for criterion in self.phase_criteria)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def operational_inventory_passed(self) -> bool:
        return all(check.passed for check in self.operational_inventory_checks)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        return self.phase_criteria_passed and self.operational_inventory_passed


@dataclass(frozen=True)
class Phase0Inputs:
    inventory: OrganizationInventory
    catalog: WorkloadCatalog
    policy: ApprovalPolicy
    dataset_registry: DatasetRegistry
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
        return None, evidence_load_reason_code(exc)


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
        dataset_registry=load_yaml(repo_root / "config" / "datasets.yaml", DatasetRegistry),
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
    dataset_registry: DatasetRegistry,
    estimated_cost_usd: Decimal,
) -> RequestFacts:
    """Retained as this module's entry point; the derivation itself lives in
    :func:`~edullm_platform.manifest_helpers.build_request_facts`.

    Admission evaluates the same facts inside AWS and must not import a gate module to do
    it, so the one implementation lives somewhere both callers can reach. The registry is
    an argument for the same reason the inventory and catalog already were: the set of
    registered datasets is reviewed configuration, and it used to be a literal defined here
    — which made the verification tooling, rather than the configuration, the authority on
    what admission would accept.
    """
    return build_request_facts(
        manifest,
        inventory=inventory,
        catalog=catalog,
        dataset_registry=dataset_registry,
        estimated_cost_usd=estimated_cost_usd,
    )


def check_ownership(inventory: OrganizationInventory) -> GateCheck:
    if inventory.admins != EXPECTED_ADMINS:
        return fail_check(
            "inventory_ownership",
            "admin_roster_mismatch",
            (
                f"The reviewed inventory requires exactly {EXPECTED_ADMINS!r} as platform admins; "
                f"got {inventory.admins!r}."
            ),
        )
    if inventory.team_leads != EXPECTED_TEAM_LEADS:
        return fail_check(
            "inventory_ownership",
            "team_lead_roster_mismatch",
            (
                "The reviewed inventory requires the recorded team-lead roster; "
                f"got {inventory.team_leads!r}."
            ),
        )
    return ok_check(
        "inventory_ownership",
        "Platform admins, team leads, and member roster match the reviewed inventory.",
    )


def check_pilots(inventory: OrganizationInventory) -> GateCheck:
    if inventory.pilot_repositories != EXPECTED_PILOTS:
        return fail_check(
            "inventory_pilots",
            "pilot_repository_mismatch",
            (
                "The reviewed inventory requires OLMo-core and dolma as the two pilot repositories; "
                f"got {inventory.pilot_repositories!r}."
            ),
        )
    return ok_check(
        "inventory_pilots",
        "OLMo-core and dolma are recorded as the two pilot repositories.",
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
            "inventory_workload_coverage",
            reason,
            "Representative CPU and GPU workloads must both be explicit in the workload catalog.",
        )
    return ok_check(
        "inventory_workload_coverage",
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
            "inventory_approval_paths",
            "denied_outright_incomplete",
            (
                "Approval policy must enumerate routine, exception, and outright-denial paths; "
                f"missing denied_outright conditions: {missing_denials!r}."
            ),
        )
    if policy.routine_approver_role != "team_lead":
        return fail_check(
            "inventory_approval_paths",
            "routine_approver_missing",
            "Routine approvals must route to team_lead.",
        )
    if "platform_admin" not in policy.exception_approver_roles:
        return fail_check(
            "inventory_approval_paths",
            "exception_approver_missing",
            "Exception approvals must include platform_admin.",
        )
    return ok_check(
        "inventory_approval_paths",
        "Routine, exception, and outright-denial approval paths are explicit and reviewed.",
    )


def check_checkpoint_expectations(catalog: WorkloadCatalog) -> GateCheck:
    for workload in catalog.workloads:
        if workload.maximum_attempts > 1 and workload.checkpoint is None:
            return fail_check(
                "inventory_checkpoint_expectations",
                "retry_missing_checkpoint",
                (
                    f"Workload {workload.name!r} allows retries but does not declare a "
                    "checkpoint contract."
                ),
            )
    return ok_check(
        "inventory_checkpoint_expectations",
        "Retryable representative workloads declare checkpoint contracts; single-attempt "
        "workloads declare checkpoint: null explicitly.",
    )


def check_github_plan(
    evidence: GitHubPlanEvidence | None,
    load_error: str | None,
) -> GateCheck:
    if load_error == EVIDENCE_STALE_CODE:
        return fail_check("inventory_github_plan", EVIDENCE_STALE_CODE, STALE_EVIDENCE_DETAIL)
    if evidence is None:
        return fail_check(
            "inventory_github_plan",
            "evidence_invalid",
            "GitHub plan evidence failed schema validation.",
        )
    if evidence.organization != EXPECTED_GITHUB_ORG:
        return fail_check(
            "inventory_github_plan",
            "organization_mismatch",
            (
                f"GitHub plan evidence must describe organization {EXPECTED_GITHUB_ORG!r}; "
                f"got {evidence.organization!r}."
            ),
        )
    if evidence.repository != EXPECTED_GITHUB_REPOSITORY:
        return fail_check(
            "inventory_github_plan",
            "repository_mismatch",
            (
                f"GitHub plan evidence must describe repository {EXPECTED_GITHUB_REPOSITORY!r}; "
                f"got {evidence.repository!r}."
            ),
        )
    plan_name = evidence.plan_name.lower()
    controls_via_visibility = evidence.visibility == "public"
    controls_via_plan = plan_name in PHASE1_PRIVATE_REPO_GITHUB_PLANS
    if not controls_via_visibility and not controls_via_plan:
        return fail_check(
            "inventory_github_plan",
            "plan_insufficient_for_private_repo_controls",
            (
                f"GitHub organization plan {evidence.plan_name!r} does not support protected "
                "branch rulesets or CODEOWNERS-backed review assignment on private repositories. "
                "Team or Enterprise is required before Phase 1 can enforce the platform control "
                "plane on the private platform repository."
            ),
        )
    if controls_via_visibility:
        detail = (
            f"GitHub repository {evidence.repository!r} is public, so Phase 1 governance controls "
            f"are available under organization plan {evidence.plan_name!r}."
        )
    else:
        detail = (
            f"GitHub organization plan {evidence.plan_name!r} supports Phase 1 governance controls "
            f"for the {evidence.visibility} platform repository."
        )
    return ok_check("inventory_github_plan", detail)


def check_aws_capacity(
    evidence: ServiceQuotasEvidence | None,
    load_error: str | None,
    catalog: WorkloadCatalog,
) -> GateCheck:
    if load_error == EVIDENCE_STALE_CODE:
        return fail_check("inventory_aws_capacity", EVIDENCE_STALE_CODE, STALE_EVIDENCE_DETAIL)
    if evidence is None:
        return fail_check(
            "inventory_aws_capacity",
            "evidence_invalid",
            "AWS capacity evidence failed schema validation.",
        )
    if evidence.environment != "sandbox":
        return fail_check(
            "inventory_aws_capacity",
            "wrong_environment",
            "AWS capacity evidence must describe the sandbox environment.",
        )
    if evidence.region != EXPECTED_AWS_REGION:
        return fail_check(
            "inventory_aws_capacity",
            "wrong_region",
            f"AWS capacity evidence must cover region {EXPECTED_AWS_REGION!r}.",
        )
    reason_code, detail = ec2_quota_coverage_issues(catalog=catalog, quotas=evidence.quotas)
    if reason_code is not None and detail is not None:
        return fail_check("inventory_aws_capacity", reason_code, detail)
    batch_issues = batch_quota_issues(evidence.batch_quotas)
    if batch_issues:
        return fail_check(
            "inventory_aws_capacity",
            "capacity_blocked",
            "Batch quota evidence is missing required records: " + "; ".join(batch_issues),
        )
    return ok_check(
        "inventory_aws_capacity",
        "Sandbox applied GPU and Batch quotas in us-east-1 satisfy representative workloads.",
    )


def check_representative_manifests(
    *,
    inventory: OrganizationInventory,
    catalog: WorkloadCatalog,
    policy: ApprovalPolicy,
    dataset_registry: DatasetRegistry,
    manifests: tuple[tuple[str, RunManifest], ...],
) -> GateCheck:
    manifest_names = {filename for filename, _manifest in manifests}
    missing_required = sorted(REQUIRED_REPRESENTATIVE_MANIFESTS - manifest_names)
    if missing_required:
        return fail_check(
            "inventory_representative_manifests",
            "missing_required_manifest",
            f"Required representative manifests are missing: {missing_required!r}.",
        )
    unexpected = sorted(manifest_names - set(REPRESENTATIVE_MANIFEST_COSTS))
    if unexpected:
        return fail_check(
            "inventory_representative_manifests",
            "unexpected_manifest",
            (
                "Every manifest under fixtures/manifests/ must have a reviewed cost expectation; "
                f"unexpected files: {unexpected!r}."
            ),
        )
    for filename, manifest in manifests:
        if not is_compute_profile_registered(manifest, catalog):
            return fail_check(
                "inventory_representative_manifests",
                "unregistered_compute_profile",
                (
                    f"Manifest {filename!r} references unregistered compute profile "
                    f"{manifest.compute_profile!r}."
                ),
            )
        if not is_workload_profile_registered(manifest, catalog):
            return fail_check(
                "inventory_representative_manifests",
                "unregistered_workload_profile",
                (
                    f"Manifest {filename!r} references unregistered workload profile "
                    f"{manifest.workload_profile!r}."
                ),
            )
        if not dataset_registry.is_registered(manifest.dataset_release):
            return fail_check(
                "inventory_representative_manifests",
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
            dataset_registry=dataset_registry,
            estimated_cost_usd=estimated_cost,
        )
        expected = expected_manifest_classification(filename)
        actual = classify_request(facts, policy.thresholds)
        if actual != expected:
            return fail_check(
                "inventory_representative_manifests",
                "classification_mismatch",
                (
                    f"Manifest {filename!r} classifies as {actual.value}; "
                    f"expected {expected.value}."
                ),
            )
    return ok_check(
        "inventory_representative_manifests",
        (
            f"All {len(REQUIRED_REPRESENTATIVE_MANIFESTS)} required representative manifests "
            "validate and classify as expected."
        ),
    )


def check_cost_estimates(
    *,
    catalog: WorkloadCatalog,
    manifests: tuple[tuple[str, RunManifest], ...],
) -> GateCheck:
    for filename, manifest in manifests:
        if filename not in REPRESENTATIVE_MANIFEST_COSTS:
            return fail_check(
                "inventory_cost_estimates",
                "missing_reviewed_cost",
                f"No reviewed maximum cost is recorded for manifest {filename!r}.",
            )
        if not is_compute_profile_registered(manifest, catalog):
            return fail_check(
                "inventory_cost_estimates",
                "unregistered_compute_profile",
                (
                    f"Manifest {filename!r} references unregistered compute profile "
                    f"{manifest.compute_profile!r}."
                ),
            )
        estimated_cost = compute_manifest_maximum_cost(manifest, catalog)
        reviewed_cost = REPRESENTATIVE_MANIFEST_COSTS[filename]
        if estimated_cost != reviewed_cost:
            return fail_check(
                "inventory_cost_estimates",
                "reviewed_cost_mismatch",
                (
                    f"Manifest {filename!r} maximum cost {estimated_cost} does not match the "
                    f"reviewed expectation {reviewed_cost}."
                ),
            )
        if filename.endswith("-routine.yaml") and estimated_cost > PROGRAM_MAXIMUM_COST_USD:
            return fail_check(
                "inventory_cost_estimates",
                "exceeds_program_budget",
                (
                    f"Manifest {filename!r} maximum cost {estimated_cost} exceeds the explicit "
                    f"program budget ceiling {PROGRAM_MAXIMUM_COST_USD}."
                ),
            )
    return ok_check(
        "inventory_cost_estimates",
        "Representative maximum costs are deterministic, source-dated, and within the program budget.",
    )


def evaluate_phase0_criteria(repo_root: Path) -> tuple[CriterionResult, ...]:
    return execute_criteria(repo_root, phase0_criteria(discover_fixtures(repo_root)))


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
            dataset_registry=inputs.dataset_registry,
            manifests=inputs.manifests,
        ),
        check_cost_estimates(
            catalog=inputs.catalog,
            manifests=inputs.manifests,
        ),
    )
    return Phase0GateResult(checks=checks)


def evaluate_repository(repo_root: Path) -> Phase0GateReport:
    """The whole Phase 0 gate for a repository checkout.

    The inventory inputs load first, so a repository whose configuration does not parse
    fails before a pytest subprocess is ever started.
    """
    inventory = evaluate_phase0(load_phase0_inputs(repo_root))
    return Phase0GateReport(
        phase_criteria=evaluate_phase0_criteria(repo_root),
        operational_inventory_checks=inventory.checks,
    )
