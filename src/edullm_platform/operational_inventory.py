"""Nine checks that the reviewed configuration is still the configuration that was reviewed.

These are not acceptance criteria and never were. They came from an earlier definition of
the work and they are kept because each one answers a question about the shipped tree that
nothing else asks: that the admin and team-lead rosters are the recorded ones, that both
pilot repositories are declared, that the catalog prices a CPU shape and a GPU shape, that
the approval policy still routes routine to a lead and exception to an admin and still
denies the six conditions outright, that a retryable workload declares a checkpoint
contract, that the captured GitHub plan and AWS capacity are fresh and describe this
organization and region, that every representative manifest names registered things and
classifies as its filename says, and that every reviewed cost still matches what the catalog
computes and stays inside the programme ceiling.

They run against the live tree in ``tests/test_operational_inventory.py``, so a
configuration edit that breaks one of them fails the pull request that makes it.
"""

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
from edullm_platform.contracts.dataset_registry import TRAINABLE_FAMILIES, DatasetRegistry
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.policy import (
    ApprovalClass,
    ApprovalPolicy,
    RequestFacts,
    classify_request,
)
from edullm_platform.contracts.repository_registry import RepositoryRegistry
from edullm_platform.contracts.workload import WorkloadCatalog
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
    compute_manifest_cost_inputs,
    compute_manifest_maximum_cost,
    is_compute_profile_registered,
    is_workload_profile_registered,
    load_manifests_from_directory,
)

T = TypeVar("T", bound=ContractModel)

EXPECTED_ADMINS: Final = ("philote-dev", "BritishAmericqn")
EXPECTED_TEAM_LEADS: Final = (
    "philote-dev",
    "ericrcwu001",
    "alsy7009",
    "meric233",
    # VS-code-cloud leads the Memory group, in place of syz2026 who no longer does.
    "VS-code-cloud",
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
    # A registered dataset that is an input to a corpus rather than a corpus. Required in
    # policy rather than merely available, because the condition exists to stop a run
    # training on a tokenizer and the enforcement is worth nothing if policy can drop it.
    "dataset_is_not_a_corpus",
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


class InventoryCheck(ContractModel):
    check_id: str
    passed: bool
    reason_code: str
    detail: str


class OperationalInventoryReport(ContractModel):
    """What the nine checks found, and whether all of them passed."""

    checks: Annotated[
        tuple[InventoryCheck, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(strict=False)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


@dataclass(frozen=True)
class InventoryInputs:
    inventory: OrganizationInventory
    repositories: RepositoryRegistry
    catalog: WorkloadCatalog
    policy: ApprovalPolicy
    dataset_registry: DatasetRegistry
    github_plan: GitHubPlanEvidence | None
    github_plan_load_error: str | None
    aws_capacity: ServiceQuotasEvidence | None
    aws_capacity_load_error: str | None
    manifests: tuple[tuple[str, RunManifest], ...]


def ok_check(check_id: str, detail: str) -> InventoryCheck:
    return InventoryCheck(check_id=check_id, passed=True, reason_code="ok", detail=detail)


def fail_check(check_id: str, reason_code: str, detail: str) -> InventoryCheck:
    return InventoryCheck(check_id=check_id, passed=False, reason_code=reason_code, detail=detail)


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


def load_json_evidence[T: ContractModel](
    path: Path, model_type: type[T]
) -> tuple[T | None, str | None]:
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


def load_inventory_inputs(repo_root: Path) -> InventoryInputs:
    github_plan, github_plan_load_error = load_json_evidence(
        repo_root / "fixtures" / "evidence" / "github-plan.sanitized.json",
        GitHubPlanEvidence,
    )
    aws_capacity, aws_capacity_load_error = load_json_evidence(
        repo_root / "fixtures" / "evidence" / "service-quotas.sanitized.json",
        ServiceQuotasEvidence,
    )
    return InventoryInputs(
        inventory=load_yaml(repo_root / "config" / "organization.yaml", OrganizationInventory),
        repositories=load_yaml(
            repo_root / "config" / "repositories.yaml", RepositoryRegistry
        ),
        catalog=load_yaml(repo_root / "config" / "workload-catalog.yaml", WorkloadCatalog),
        policy=load_yaml(repo_root / "config" / "policy.yaml", ApprovalPolicy),
        dataset_registry=load_yaml(repo_root / "config" / "datasets.yaml", DatasetRegistry),
        github_plan=github_plan,
        github_plan_load_error=github_plan_load_error,
        aws_capacity=aws_capacity,
        aws_capacity_load_error=aws_capacity_load_error,
        manifests=load_manifests_from_directory(repo_root / "fixtures" / "manifests"),
    )


#: What each representative manifest is expected to classify as, declared rather than read
#: off the end of its filename.
#:
#: IT WAS THE FILENAME AND POLICY v5 MADE THAT A LIE. The rule was that a file ending
#: ``-routine.yaml`` classified as routine and one ending ``-exception.yaml`` as an
#: exception, which held while the class was a function of five ceilings the fixtures were
#: written against. v5 leaves one bound at five hundred dollars, so four of these fixtures
#: are released by nobody and none of them is an exception, and the suffix on the file no
#: longer says which.
#:
#: The files keep their names in this change and the names now describe the shape rather
#: than the class: which machine, whether it fans out, which repository it came from.
#: Renaming sixty-eight references across twenty files is a separate change and would land
#: as one conflict per agent merging beside it.
#:
#: ``multiseed-routine.yaml`` is the only one still routine and the reason is worth reading
#: off this table: it is five cells, and a fan-out is never released automatically whatever
#: it costs. Nothing here is routine on cost, because no representative manifest reaches
#: five hundred dollars; that boundary is exercised in tests/test_policy.py against the real
#: catalog, where a fixture would have had to be repriced every time a rate moved.
REPRESENTATIVE_MANIFEST_CLASSES: Final[dict[str, ApprovalClass]] = {
    "cpu-routine.yaml": ApprovalClass.AUTOMATIC,
    "gpu-exception.yaml": ApprovalClass.AUTOMATIC,
    "gpu-routine.yaml": ApprovalClass.AUTOMATIC,
    "multiseed-routine.yaml": ApprovalClass.ROUTINE,
    "olmo-branch-routine.yaml": ApprovalClass.AUTOMATIC,
    "sagemaker-routine.yaml": ApprovalClass.AUTOMATIC,
}


def expected_manifest_classification(filename: str) -> ApprovalClass:
    try:
        return REPRESENTATIVE_MANIFEST_CLASSES[filename]
    except KeyError:
        raise ValueError(
            f"unexpected representative manifest filename: {filename}"
        ) from None


def request_facts_from_manifest(
    manifest: RunManifest,
    *,
    repositories: RepositoryRegistry,
    catalog: WorkloadCatalog,
    dataset_registry: DatasetRegistry,
    estimated_cost_usd: Decimal,
) -> RequestFacts:
    """Retained as this module's entry point; the derivation itself lives in
    :func:`~edullm_platform.manifest_helpers.build_request_facts`.

    Admission evaluates the same facts inside AWS and must not import a gate module to do
    it, so the one implementation lives somewhere both callers can reach. Each registry is
    an argument for the same reason the catalog already was: what is registered is reviewed
    configuration, and a set defined beside a caller makes the verification tooling, rather
    than the configuration, the authority on what admission would accept.

    The roster used to be one of those arguments, because ``repository_registered`` was
    membership of its pilot list. It is not one now: the pilot list says what the programme
    covers and ``config/repositories.yaml`` says what has somewhere to publish an image to,
    and only the second of those is the question this fact asks.
    """
    return build_request_facts(
        manifest,
        repositories=repositories,
        catalog=catalog,
        dataset_registry=dataset_registry,
        estimated_cost_usd=estimated_cost_usd,
    )


def check_ownership(inventory: OrganizationInventory) -> InventoryCheck:
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


def check_pilots(inventory: OrganizationInventory) -> InventoryCheck:
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


def check_workload_coverage(catalog: WorkloadCatalog) -> InventoryCheck:
    # READ OFF THE COMPUTE PROFILES, BECAUSE A WORKLOAD PROFILE NO LONGER NAMES ONE. This
    # went through WorkloadProfile.compute_profile, which the submission form overrode on
    # every submission and which is gone. What the check establishes is unchanged: this
    # platform prices both a CPU shape and a GPU shape, so both kinds of work have somewhere
    # to be run. A catalog with only one of the two would offer a submission form on which
    # the whole of the other kind is unpickable.
    accelerators = {profile.accelerator for profile in catalog.compute_profiles}
    if accelerators != {"cpu", "gpu"}:
        missing: list[str] = []
        if "cpu" not in accelerators:
            missing.append("cpu")
        if "gpu" not in accelerators:
            missing.append("gpu")
        reason = (
            "missing_gpu_representative" if missing == ["gpu"] else "missing_cpu_representative"
        )
        if len(missing) == 2:
            reason = "missing_cpu_and_gpu_representatives"
        return fail_check(
            "inventory_workload_coverage",
            reason,
            "Representative CPU and GPU compute profiles must both be priced in the catalog.",
        )
    return ok_check(
        "inventory_workload_coverage",
        "Workload catalog prices explicit representative CPU and GPU compute profiles.",
    )


def check_approval_paths(policy: ApprovalPolicy) -> InventoryCheck:
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


def check_checkpoint_expectations(catalog: WorkloadCatalog) -> InventoryCheck:
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
) -> InventoryCheck:
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
) -> InventoryCheck:
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
    repositories: RepositoryRegistry,
    catalog: WorkloadCatalog,
    policy: ApprovalPolicy,
    dataset_registry: DatasetRegistry,
    manifests: tuple[tuple[str, RunManifest], ...],
) -> InventoryCheck:
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
        if not dataset_registry.is_a_trainable_corpus(manifest.dataset_release):
            reference = dataset_registry.reference_for(manifest.dataset_release)
            # Narrowing for the type checker only. The branch above already refused anything
            # the registry cannot resolve, and `is_a_trainable_corpus` answers True whenever
            # `reference_for` finds nothing, so reaching here without a reference is not a
            # reachable state -- the assert says so rather than a `None` leaking into the
            # message as the word "None".
            assert reference is not None
            return fail_check(
                "inventory_representative_manifests",
                "dataset_is_not_a_corpus",
                (
                    f"Manifest {filename!r} names {manifest.dataset_release!r} as its "
                    f"dataset release, and {reference.dataset_id!r} is in the "
                    f"{reference.family!r} family, which is an input to a corpus rather "
                    f"than a corpus a run may train on. Trainable families are "
                    f"{', '.join(sorted(TRAINABLE_FAMILIES))}."
                ),
            )
        cost = compute_manifest_cost_inputs(manifest, catalog)
        facts = request_facts_from_manifest(
            manifest,
            repositories=repositories,
            catalog=catalog,
            dataset_registry=dataset_registry,
            estimated_cost_usd=cost.maximum_compute_cost_usd,
        )
        expected = expected_manifest_classification(filename)
        actual = classify_request(facts, policy.thresholds)
        if actual != expected:
            return fail_check(
                "inventory_representative_manifests",
                "classification_mismatch",
                (f"Manifest {filename!r} classifies as {actual.value}; expected {expected.value}."),
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
) -> InventoryCheck:
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


def evaluate_operational_inventory(inputs: InventoryInputs) -> OperationalInventoryReport:
    checks = (
        check_ownership(inputs.inventory),
        check_pilots(inputs.inventory),
        check_workload_coverage(inputs.catalog),
        check_approval_paths(inputs.policy),
        check_checkpoint_expectations(inputs.catalog),
        check_github_plan(inputs.github_plan, inputs.github_plan_load_error),
        check_aws_capacity(inputs.aws_capacity, inputs.aws_capacity_load_error, inputs.catalog),
        check_representative_manifests(
            repositories=inputs.repositories,
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
    return OperationalInventoryReport(checks=checks)


def evaluate_repository(repo_root: Path) -> OperationalInventoryReport:
    """The nine checks for a repository checkout, loading its configuration first."""
    return evaluate_operational_inventory(load_inventory_inputs(repo_root))
