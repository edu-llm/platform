from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from edullm_platform.admission import AdmissionOutcome, UnreadableManifestError, admit
from edullm_platform.canonical import sha256_digest
from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import AdmissionReason, ApprovalEnvironment
from edullm_platform.contracts.authorization import AuthorizationReason
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.execution import ExecutionTargetCatalog
from edullm_platform.contracts.image import GitHubWorkflowRunReference
from edullm_platform.contracts.image_scan import (
    ImageScanExceptionRegistry,
    ImageScanSummary,
)
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.policy import ApprovalClass, ApprovalPolicy
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.manifest_helpers import load_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_FIXTURES_DIR = PROJECT_ROOT / "fixtures" / "manifests"

ROUTINE_MANIFEST = "cpu-routine.yaml"
EXCEPTION_MANIFEST = "gpu-exception.yaml"

ADMIN = "philote-dev"
LEAD = "ericrcwu001"
MEMBER = "caiiris"
OTHER_MEMBER = "nzhao721"
OUTSIDER = "not-a-member"

RUN_ID = "run_0198f0a1-2b3c-7d4e-8f01-23456789abcd"
RECORDED_AT = datetime(2026, 7, 27, 9, 15, 30, 123456, tzinfo=UTC)
UNMATCHED_DIGEST = "sha256:" + "0" * 64

#: Twelve digits that are not this account's. Admission builds queue and job-definition
#: ARNs from whatever account it is told about, and a real one in a committed test file is
#: the account id every capture tool then has to redact.
ACCOUNT_ID = "123456789012"

#: The profile the exception fixture names. Priced, and not backed by anything Phase 3
#: deploys, which is a refusal in its own right -- see the note on ``backed_by_a_target``.
EXCEPTION_COMPUTE_PROFILE = "gpu-4xa10g"

UNREGISTERED_DATASET = "dolma-2026-99"
UNREGISTERED_REPOSITORY = "not-a-pilot-repository"
UNREGISTERED_COMPUTE_PROFILE = "cpu-1024vcpu"


def load_organization_inventory() -> OrganizationInventory:
    return load_yaml(PROJECT_ROOT / "config" / "organization.yaml", OrganizationInventory)


def load_approval_policy() -> ApprovalPolicy:
    return load_yaml(PROJECT_ROOT / "config" / "policy.yaml", ApprovalPolicy)


def load_workload_catalog() -> WorkloadCatalog:
    return load_yaml(PROJECT_ROOT / "config" / "workload-catalog.yaml", WorkloadCatalog)


def load_dataset_registry() -> DatasetRegistry:
    return load_yaml(PROJECT_ROOT / "config" / "datasets.yaml", DatasetRegistry)


def load_execution_targets() -> ExecutionTargetCatalog:
    return load_yaml(PROJECT_ROOT / "config" / "execution-targets.yaml", ExecutionTargetCatalog)


def backed_by_a_target(profile_name: str) -> tuple[WorkloadCatalog, ExecutionTargetCatalog]:
    """A catalog and a target file that both say this profile can run.

    Phase 3 made "nowhere to run" a refusal, and eleven of the twelve profiles are in that
    state -- including the one the exception fixture names. The two tests below are about
    which gate may release an exception, not about whether capacity exists, so they run
    against configuration that backs the profile rather than being refused for a reason
    that has nothing to do with what they assert.

    The refusal itself is not skipped by this: it is the subject of
    ``tests/test_phase3_execution.py``, where it is asserted directly instead of arriving
    here as an incidental failure that would pass whatever the gate did.
    """
    catalog = load_workload_catalog()
    profiles = tuple(
        profile.model_copy(update={"provisioned": True})
        if profile.name == profile_name
        else profile
        for profile in catalog.compute_profiles
    )
    backed = catalog.model_copy(update={"compute_profiles": profiles})
    deployed = load_execution_targets()
    template = deployed.targets[0]
    targets = (
        deployed.targets
        if profile_name in deployed.backed_profiles
        else (*deployed.targets, template.model_copy(update={"compute_profile": profile_name}))
    )
    return backed, deployed.model_copy(update={"targets": targets})


#: A scan with nothing in it, for the tests that are about admission rather than about
#: scanning. Passing a clean summary rather than omitting the arguments keeps these tests
#: on the same code path production uses; omitting them would take the opt-out branch and
#: quietly stop exercising the gate at all.
def clean_image_scan() -> ImageScanSummary:
    return ImageScanSummary(
        schema_version=1,
        status="COMPLETE",
        scanned_at=datetime(2026, 7, 26, 22, 5, 49, tzinfo=UTC),
    )


def load_image_scan_registry() -> ImageScanExceptionRegistry:
    return load_yaml(
        PROJECT_ROOT / "config" / "image-exceptions.yaml", ImageScanExceptionRegistry
    )


class TripwireDatasetRegistry(DatasetRegistry):
    """A registry that reports an ordering violation instead of answering."""

    def is_registered(self, release_id: str) -> bool:
        raise AssertionError(
            "admission derived a fact from a manifest before checking that the manifest "
            "hashed to the value a reviewer approved"
        )


def tripwire_dataset_registry() -> TripwireDatasetRegistry:
    return TripwireDatasetRegistry.model_validate(
        load_dataset_registry().model_dump(mode="json")
    )


def load_manifest_fixture(filename: str) -> RunManifest:
    return load_manifest(MANIFEST_FIXTURES_DIR / filename)


def manifest_payload(filename: str = ROUTINE_MANIFEST, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = dict(load_manifest_fixture(filename).model_dump(mode="json"))
    payload.update(overrides)
    return payload


def digest_of(payload: Mapping[str, object]) -> str:
    return sha256_digest(RunManifest.model_validate(dict(payload)))


def workflow_run() -> GitHubWorkflowRunReference:
    return GitHubWorkflowRunReference(
        run_repository="edu-llm/platform",
        workflow_repository="edu-llm/platform",
        workflow_path=".github/workflows/submit-run.yml",
        workflow_ref="refs/heads/main",
        run_id=1704,
        run_attempt=1,
    )


def admit_submission(
    *,
    manifest_name: str = ROUTINE_MANIFEST,
    manifest_overrides: Mapping[str, object] | None = None,
    payload: Mapping[str, object] | None = None,
    approved_manifest_sha256: str | None = None,
    submitter: str = MEMBER,
    approver: str | None = LEAD,
    approving_environment: ApprovalEnvironment = ApprovalEnvironment.LEAD,
    policy: ApprovalPolicy | None = None,
    catalog: WorkloadCatalog | None = None,
    execution_targets: ExecutionTargetCatalog | None = None,
    dataset_registry: DatasetRegistry | None = None,
    image_scan_registry: ImageScanExceptionRegistry | None = None,
    image_scan_summary: ImageScanSummary | None = None,
) -> AdmissionOutcome:
    submitted = (
        payload
        if payload is not None
        else manifest_payload(manifest_name, **dict(manifest_overrides or {}))
    )
    return admit(
        manifest_payload=submitted,
        approved_manifest_sha256=(
            approved_manifest_sha256
            if approved_manifest_sha256 is not None
            else digest_of(submitted)
        ),
        run_id=RUN_ID,
        submitter=submitter,
        approver=approver,
        approving_environment=approving_environment,
        workflow_run=workflow_run(),
        policy=policy if policy is not None else load_approval_policy(),
        inventory=load_organization_inventory(),
        catalog=catalog if catalog is not None else load_workload_catalog(),
        execution_targets=(
            execution_targets if execution_targets is not None else load_execution_targets()
        ),
        account_id=ACCOUNT_ID,
        dataset_registry=(
            dataset_registry if dataset_registry is not None else load_dataset_registry()
        ),
        image_scan_registry=(
            image_scan_registry
            if image_scan_registry is not None
            else ImageScanExceptionRegistry(schema_version=1)
        ),
        image_scan_summary=(
            image_scan_summary if image_scan_summary is not None else clean_image_scan()
        ),
        recorded_at=RECORDED_AT,
    )


#: One rejected submission of every kind admission can record, as keyword arguments.
REJECTED_SUBMISSIONS: tuple[tuple[AdmissionReason, dict[str, object]], ...] = (
    (
        AdmissionReason.MANIFEST_HASH_MISMATCH,
        {"approved_manifest_sha256": UNMATCHED_DIGEST},
    ),
    (
        AdmissionReason.DENIED_OUTRIGHT,
        {"manifest_overrides": {"dataset_release": UNREGISTERED_DATASET}},
    ),
    (
        AdmissionReason.APPROVAL_ENVIRONMENT_MISMATCH,
        {"manifest_name": EXCEPTION_MANIFEST, "submitter": LEAD, "approver": ADMIN},
    ),
    (
        AdmissionReason.AUTHORIZATION_DENIED,
        {"approver": OTHER_MEMBER},
    ),
)

REJECTION_IDS = [reason.value for reason, _kwargs in REJECTED_SUBMISSIONS]


def test_a_correct_submission_through_the_right_gate_is_admitted() -> None:
    outcome = admit_submission()
    decision = outcome.decision

    assert outcome.accepted is True
    assert decision.accepted is True
    assert decision.reason is AdmissionReason.ACCEPTED
    assert decision.approval_class is ApprovalClass.ROUTINE
    assert decision.approving_environment is ApprovalEnvironment.LEAD
    assert decision.authorization is not None
    assert decision.authorization.granted is True
    assert decision.authorization.reason is AuthorizationReason.ROUTINE_APPROVED_BY_LEAD_OR_ADMIN
    assert decision.cost is not None
    assert decision.cost.maximum_compute_cost_usd == Decimal("2.86")
    assert decision.run_id == RUN_ID
    assert decision.manifest_sha256 == digest_of(manifest_payload())


def test_an_exception_released_by_an_admin_through_the_admin_gate_is_admitted() -> None:
    catalog, targets = backed_by_a_target(EXCEPTION_COMPUTE_PROFILE)
    outcome = admit_submission(
        manifest_name=EXCEPTION_MANIFEST,
        submitter=LEAD,
        approver=ADMIN,
        approving_environment=ApprovalEnvironment.ADMIN,
        catalog=catalog,
        execution_targets=targets,
    )
    decision = outcome.decision

    assert decision.accepted is True
    assert decision.approval_class is ApprovalClass.EXCEPTION
    assert decision.approving_environment is ApprovalEnvironment.ADMIN
    assert decision.authorization is not None
    assert decision.authorization.reason is AuthorizationReason.EXCEPTION_APPROVED_BY_ADMIN


@pytest.mark.parametrize("policy_version", ["v1", "v2", "v41"])
def test_the_decision_cites_the_policy_version_aws_deployed(policy_version: str) -> None:
    policy = load_approval_policy().model_copy(update={"policy_version": policy_version})
    outcome = admit_submission(policy=policy)

    assert outcome.decision.policy_version == policy_version
    assert f"policy {policy_version}" in outcome.decision.detail


def test_a_submission_cannot_smuggle_a_policy_version_past_the_deployed_one() -> None:
    payload = manifest_payload()
    approved = digest_of(payload)
    payload["policy_version"] = "v99"

    with pytest.raises(UnreadableManifestError):
        admit_submission(payload=payload, approved_manifest_sha256=approved)

    assert admit_submission().decision.policy_version == load_approval_policy().policy_version


def test_a_manifest_that_does_not_hash_to_what_was_approved_is_refused() -> None:
    payload = manifest_payload()
    outcome = admit_submission(payload=payload, approved_manifest_sha256=UNMATCHED_DIGEST)
    decision = outcome.decision

    assert decision.accepted is False
    assert decision.reason is AdmissionReason.MANIFEST_HASH_MISMATCH
    assert decision.authorization is None
    assert decision.cost is None
    assert decision.manifest_sha256 == digest_of(payload) != UNMATCHED_DIGEST
    assert outcome.intent.manifest_sha256 == digest_of(payload), (
        "the intent record carries the hash admission recomputed, so a record whose two "
        "halves disagree is detectable after the fact"
    )


def test_a_manifest_swapped_for_another_after_approval_is_refused() -> None:
    approved = digest_of(manifest_payload(ROUTINE_MANIFEST))
    outcome = admit_submission(
        manifest_name=EXCEPTION_MANIFEST,
        approved_manifest_sha256=approved,
        submitter=LEAD,
        approver=ADMIN,
        approving_environment=ApprovalEnvironment.ADMIN,
    )

    assert outcome.decision.reason is AdmissionReason.MANIFEST_HASH_MISMATCH
    assert outcome.intent.manifest == load_manifest_fixture(EXCEPTION_MANIFEST)


def test_the_hash_is_checked_before_any_fact_is_derived_from_the_manifest() -> None:
    outcome = admit_submission(
        approved_manifest_sha256=UNMATCHED_DIGEST,
        dataset_registry=tripwire_dataset_registry(),
    )

    assert outcome.decision.reason is AdmissionReason.MANIFEST_HASH_MISMATCH


def test_the_ordering_tripwire_does_fire_once_the_hash_matches() -> None:
    with pytest.raises(AssertionError, match="before checking that the manifest hashed"):
        admit_submission(dataset_registry=tripwire_dataset_registry())


@pytest.mark.parametrize(
    ("manifest_overrides", "condition"),
    [
        ({"dataset_release": UNREGISTERED_DATASET}, "unregistered_dataset"),
        ({"repository": UNREGISTERED_REPOSITORY}, "unregistered_repository"),
        ({"compute_profile": UNREGISTERED_COMPUTE_PROFILE}, "unregistered_compute_profile"),
    ],
)
def test_a_mismatched_hash_outranks_every_finding_the_manifest_would_have_produced(
    manifest_overrides: dict[str, object],
    condition: str,
) -> None:
    outcome = admit_submission(
        manifest_overrides=manifest_overrides,
        approved_manifest_sha256=UNMATCHED_DIGEST,
        submitter=OUTSIDER,
        approver=None,
        approving_environment=ApprovalEnvironment.ADMIN,
    )
    decision = outcome.decision

    assert decision.reason is AdmissionReason.MANIFEST_HASH_MISMATCH
    assert decision.authorization is None, (
        "nothing derived from an unapproved manifest is trustworthy, including who its team "
        "is, so a manufactured denial reason would put a finding in the record that nothing "
        "established"
    )
    assert decision.cost is None
    assert condition not in decision.detail


def test_an_exception_released_by_the_lead_gate_is_refused() -> None:
    outcome = admit_submission(
        manifest_name=EXCEPTION_MANIFEST,
        submitter=LEAD,
        approver=ADMIN,
        approving_environment=ApprovalEnvironment.LEAD,
    )
    decision = outcome.decision

    assert decision.accepted is False
    assert decision.reason is AdmissionReason.APPROVAL_ENVIRONMENT_MISMATCH
    assert decision.approval_class is ApprovalClass.EXCEPTION
    assert decision.approving_environment is ApprovalEnvironment.LEAD
    assert "run-approval-admin" in decision.detail
    assert "run-approval-lead" in decision.detail
    assert decision.authorization is not None
    assert decision.authorization.granted is True, (
        "an admin did approve this run; routing it to the weaker gate is refused anyway, "
        "because the subject claim says which gate was passed and not that it was the right one"
    )


def test_a_routine_submission_released_by_the_admin_gate_is_refused() -> None:
    outcome = admit_submission(
        submitter=MEMBER,
        approver=ADMIN,
        approving_environment=ApprovalEnvironment.ADMIN,
    )
    decision = outcome.decision

    assert decision.reason is AdmissionReason.APPROVAL_ENVIRONMENT_MISMATCH
    assert decision.approval_class is ApprovalClass.ROUTINE
    assert "run-approval-lead" in decision.detail


@pytest.mark.parametrize("approving_environment", list(ApprovalEnvironment))
def test_the_class_is_re_derived_rather_than_read_from_the_gate_that_released_it(
    approving_environment: ApprovalEnvironment,
) -> None:
    catalog, targets = backed_by_a_target(EXCEPTION_COMPUTE_PROFILE)
    outcome = admit_submission(
        manifest_name=EXCEPTION_MANIFEST,
        submitter=LEAD,
        approver=ADMIN,
        approving_environment=approving_environment,
        catalog=catalog,
        execution_targets=targets,
    )

    assert outcome.decision.approval_class is ApprovalClass.EXCEPTION
    assert outcome.decision.accepted is (
        approving_environment is ApprovalEnvironment.ADMIN
    )


@pytest.mark.parametrize(
    ("manifest_overrides", "condition"),
    [
        ({"dataset_release": UNREGISTERED_DATASET}, "unregistered_dataset"),
        ({"repository": UNREGISTERED_REPOSITORY}, "unregistered_repository"),
        ({"compute_profile": UNREGISTERED_COMPUTE_PROFILE}, "unregistered_compute_profile"),
    ],
)
def test_an_input_that_cannot_be_resolved_is_denied_outright_and_the_condition_named(
    manifest_overrides: dict[str, object],
    condition: str,
) -> None:
    outcome = admit_submission(manifest_overrides=manifest_overrides)
    decision = outcome.decision

    assert decision.accepted is False
    assert decision.reason is AdmissionReason.DENIED_OUTRIGHT
    assert condition in decision.detail
    assert decision.approval_class is ApprovalClass.EXCEPTION
    assert decision.approving_environment is ApprovalEnvironment.LEAD, (
        "this submission is also at the wrong gate for its class, and it is still denied "
        "outright: an unresolvable input is not an expensive request somebody may approve"
    )


def test_every_tripped_condition_is_named_in_the_order_policy_lists_them() -> None:
    outcome = admit_submission(
        manifest_overrides={
            "repository": UNREGISTERED_REPOSITORY,
            "dataset_release": UNREGISTERED_DATASET,
            "compute_profile": UNREGISTERED_COMPUTE_PROFILE,
        }
    )

    assert outcome.decision.reason is AdmissionReason.DENIED_OUTRIGHT
    assert (
        "unregistered_repository, unregistered_dataset, unregistered_compute_profile"
        in outcome.decision.detail
    )


def test_an_unregistered_compute_profile_leaves_the_cost_unstated() -> None:
    outcome = admit_submission(
        manifest_overrides={"compute_profile": UNREGISTERED_COMPUTE_PROFILE}
    )

    assert outcome.decision.reason is AdmissionReason.DENIED_OUTRIGHT
    assert outcome.decision.cost is None, (
        "an unregistered profile has no rate, and a zero would read as a free run rather "
        "than an unpriceable one"
    )


def test_a_denied_submission_whose_profile_is_registered_still_states_its_cost() -> None:
    outcome = admit_submission(manifest_overrides={"dataset_release": UNREGISTERED_DATASET})

    assert outcome.decision.reason is AdmissionReason.DENIED_OUTRIGHT
    assert outcome.decision.cost is not None
    assert outcome.decision.cost.maximum_compute_cost_usd == Decimal("2.86")


@pytest.mark.parametrize(
    ("submitter", "approver", "expected_reason"),
    [
        (MEMBER, OTHER_MEMBER, AuthorizationReason.APPROVER_LACKS_LEAD_OR_ADMIN_ROLE),
        (MEMBER, None, AuthorizationReason.SELF_APPROVAL_NOT_PERMITTED_FOR_MEMBER),
        (OUTSIDER, LEAD, AuthorizationReason.SUBMITTER_NOT_IN_ROSTER),
        (MEMBER, OUTSIDER, AuthorizationReason.APPROVER_NOT_IN_ROSTER),
    ],
)
def test_a_submission_its_approver_may_not_release_is_refused(
    submitter: str,
    approver: str | None,
    expected_reason: AuthorizationReason,
) -> None:
    outcome = admit_submission(submitter=submitter, approver=approver)
    decision = outcome.decision

    assert decision.accepted is False
    assert decision.reason is AdmissionReason.AUTHORIZATION_DENIED
    assert decision.authorization is not None
    assert decision.authorization.granted is False
    assert decision.authorization.reason is expected_reason
    assert expected_reason.value in decision.detail


def test_an_exception_at_the_right_gate_still_needs_an_approver_who_may_release_it() -> None:
    outcome = admit_submission(
        manifest_name=EXCEPTION_MANIFEST,
        submitter=MEMBER,
        approver=LEAD,
        approving_environment=ApprovalEnvironment.ADMIN,
    )
    decision = outcome.decision

    assert decision.reason is AdmissionReason.AUTHORIZATION_DENIED
    assert decision.authorization is not None
    assert decision.authorization.reason is AuthorizationReason.APPROVER_LACKS_ADMIN_ROLE


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"schema_version": 1},
        {"nothing": "here"},
        manifest_payload(schema_version=2),
        manifest_payload(maximum_attempts=0),
        manifest_payload(command=[]),
        {key: value for key, value in manifest_payload().items() if key != "team"},
    ],
    ids=[
        "empty",
        "version-only",
        "unrelated-mapping",
        "unknown-schema-version",
        "zero-attempts",
        "empty-command",
        "missing-team",
    ],
)
def test_a_payload_that_is_not_a_manifest_produces_no_records_at_all(
    payload: dict[str, object],
) -> None:
    with pytest.raises(UnreadableManifestError) as exc_info:
        admit_submission(payload=payload, approved_manifest_sha256=UNMATCHED_DIGEST)
    assert "not a valid run manifest" in str(exc_info.value)


def test_a_refusal_earns_records_where_an_unreadable_payload_earns_none() -> None:
    refused = admit_submission(manifest_overrides={"dataset_release": UNREGISTERED_DATASET})

    assert refused.decision.reason is AdmissionReason.DENIED_OUTRIGHT
    assert refused.intent.manifest.dataset_release == UNREGISTERED_DATASET

    with pytest.raises(UnreadableManifestError):
        admit_submission(payload={"schema_version": 1}, approved_manifest_sha256=UNMATCHED_DIGEST)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("commit_sha", "main"),
        ("commit_sha", "v1.0.0"),
        ("commit_sha", "1234567890121234567890121234567890120001 "),
        ("image_digest", "latest"),
        ("image_digest", "ghcr.io/edu-llm/olmo-core:latest"),
    ],
)
def test_a_mutable_reference_is_refused_as_an_unreadable_manifest(
    field: str,
    value: str,
) -> None:
    with pytest.raises(UnreadableManifestError):
        admit_submission(
            manifest_overrides={field: value},
            approved_manifest_sha256=UNMATCHED_DIGEST,
        )


@pytest.mark.parametrize(
    ("expected_reason", "submission"), REJECTED_SUBMISSIONS, ids=REJECTION_IDS
)
def test_the_intent_record_is_written_even_for_a_submission_that_is_refused(
    expected_reason: AdmissionReason,
    submission: dict[str, object],
) -> None:
    outcome = admit_submission(**submission)
    intent = outcome.intent

    assert outcome.decision.reason is expected_reason
    assert outcome.decision.accepted is False
    assert intent.run_id == outcome.decision.run_id
    assert intent.manifest_sha256 == outcome.decision.manifest_sha256
    assert intent.recorded_at == outcome.decision.recorded_at
    assert intent.workflow_run == workflow_run()


@pytest.mark.parametrize(
    ("expected_reason", "submission"), REJECTED_SUBMISSIONS, ids=REJECTION_IDS
)
def test_nothing_in_an_intent_record_marks_a_run_as_accepted(
    expected_reason: AdmissionReason,
    submission: dict[str, object],
) -> None:
    document = admit_submission(**submission).intent.model_dump(mode="json")

    assert expected_reason is not AdmissionReason.ACCEPTED
    assert "accepted" not in document
    assert not any(isinstance(value, bool) for value in document.values()), (
        "an intent record is written before the judgement it precedes, so its existence "
        "must never be readable as approval"
    )


def test_the_two_records_of_one_submission_are_keyed_the_same() -> None:
    outcome = admit_submission()

    assert outcome.intent.run_id == outcome.decision.run_id == RUN_ID
    assert outcome.intent.manifest_sha256 == outcome.decision.manifest_sha256
    assert outcome.intent.approving_environment is outcome.decision.approving_environment
    assert outcome.intent.recorded_at == outcome.decision.recorded_at == RECORDED_AT
