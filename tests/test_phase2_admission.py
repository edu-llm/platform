from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from edullm_platform.admission import (
    AdmissionOutcome,
    UnreadableManifestError,
    admit,
    image_scan_refusal_detail,
)
from edullm_platform.canonical import sha256_digest
from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import AdmissionReason, ApprovalEnvironment
from edullm_platform.contracts.authorization import AuthorizationReason
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.execution import ExecutionTargetCatalog
from edullm_platform.contracts.image import GitHubWorkflowRunReference
from edullm_platform.contracts.image_scan import (
    ImageScanExceptionRegistry,
    ImageScanStatus,
    ImageScanSummary,
    ImageScanVerdict,
    ReviewedVulnerability,
    ScanFinding,
    image_scan_verdict,
)
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.policy import ApprovalClass, ApprovalPolicy
from edullm_platform.contracts.repository_registry import RepositoryRegistry
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.manifest_helpers import load_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_FIXTURES_DIR = PROJECT_ROOT / "fixtures" / "manifests"

# THE MODULE'S DEFAULT SUBMISSION IS THE FAN-OUT NOW, AND IT WAS cpu-routine.yaml. Policy
# v5 releases a single cell under five hundred dollars by nobody, so the CPU fixture stopped
# being a submission a lead sees and the only reviewed manifest that still is one is the
# five-cell sweep. Nearly every test here is about a gate, an approver or a record, so the
# default has to be a shape that reaches a person.
ROUTINE_MANIFEST = "multiseed-routine.yaml"

#: The same fixture the default was, kept as the automatic case rather than dropped. It is
#: what a submission looks like when nobody releases it, which is now the ordinary path.
AUTOMATIC_MANIFEST = "cpu-routine.yaml"

#: A second manifest that is not the default, for the two tests that swap one for another
#: after approval. Which one it is does not matter to them; that it hashes differently does.
OTHER_MANIFEST = "gpu-exception.yaml"

ADMIN = "philote-dev"
LEAD = "ericrcwu001"
MEMBER = "GMatherne"
OTHER_MEMBER = "nzhao721"
OUTSIDER = "not-a-member"

RUN_ID = "run_0198f0a1-2b3c-7d4e-8f01-23456789abcd"
RECORDED_AT = datetime(2026, 7, 27, 9, 15, 30, 123456, tzinfo=UTC)
UNMATCHED_DIGEST = "sha256:" + "0" * 64

#: Twelve digits that are not this account's. Admission builds queue and job-definition
#: ARNs from whatever account it is told about, and a real one in a committed test file is
#: the account id every capture tool then has to redact.
ACCOUNT_ID = "123456789012"

#: The profile ``OTHER_MANIFEST`` names. Priced, and not backed by anything Phase 3
#: deploys, which is a refusal in its own right -- see the note on ``backed_by_a_target``.
OTHER_COMPUTE_PROFILE = "gpu-4xa10g"

UNREGISTERED_DATASET = "dolma-2026-99"
UNREGISTERED_REPOSITORY = "not-a-registered-repository"
UNREGISTERED_COMPUTE_PROFILE = "cpu-1024vcpu"

#: Four criticals with a review recorded against each, which is what the image-scan refusals
#: below vary the arithmetic around rather than the reviews. Four because that is how many of
#: ``olmo-eval-full``'s thirteen a default-sized page of findings carried.
REVIEWED_CVES = ("CVE-2026-57433", "CVE-2026-12087", "CVE-2026-13221", "CVE-2026-4067")


def load_organization_inventory() -> OrganizationInventory:
    return load_yaml(PROJECT_ROOT / "config" / "organization.yaml", OrganizationInventory)


def load_repository_registry() -> RepositoryRegistry:
    return load_yaml(PROJECT_ROOT / "config" / "repositories.yaml", RepositoryRegistry)


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
    image_scan_findings: tuple[ScanFinding, ...] | None = None,
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
        repositories=load_repository_registry(),
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
        # Defaults to none supplied, which pairs with the clean summary above: a scan with
        # no blocking findings needs no list, and the gate never reaches the branch that
        # would want one. A test wanting the reviewed-vulnerability path passes both.
        image_scan_findings=image_scan_findings,
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
        # THE DIRECTION THAT MUST STAY REFUSED, WHICH IS NOT THE ONE THIS ROW USED TO NAME.
        # It was `gpu-exception.yaml` through the lead gate, and under v5 that manifest
        # classifies as automatic, so the row was really asserting that a lead releasing a
        # run needing nobody is a mismatch. v6 permits exactly that pair, because the daily
        # ceiling produces it. The refusal worth keeping a fixture for is the opposite one:
        # the fan-out below needs a lead and arrived through the gate with no reviewer.
        AdmissionReason.APPROVAL_ENVIRONMENT_MISMATCH,
        {"approver": None, "approving_environment": ApprovalEnvironment.AUTOMATIC},
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
    assert decision.cost.maximum_compute_cost_usd == Decimal("20.12")
    assert decision.run_id == RUN_ID
    assert decision.manifest_sha256 == digest_of(manifest_payload())


def test_a_submission_nobody_released_is_admitted_through_the_automatic_gate() -> None:
    """The row that replaced the admin one, and the ordinary path under policy v5.

    ``cpu-routine.yaml`` is $2.86 in one cell, so it reaches ``run-approval-automatic``,
    which is a real environment with a branch policy and no reviewer. There is no approver
    on the record because nobody was asked.

    Mutation: move the automatic branch of ``evaluate_authorization`` below the
    self-approval test. ``GMatherne`` is neither a lead nor an admin, so the absent approver
    reads as self-approval and the run is refused with
    ``self_approval_not_permitted_for_member``. The cheapest submissions on the platform go
    from unattended to impossible, which is the whole of what that ordering protects.
    """
    outcome = admit_submission(
        manifest_name=AUTOMATIC_MANIFEST,
        approver=None,
        approving_environment=ApprovalEnvironment.AUTOMATIC,
    )
    decision = outcome.decision

    assert decision.accepted is True
    assert decision.approval_class is ApprovalClass.AUTOMATIC
    assert decision.approving_environment is ApprovalEnvironment.AUTOMATIC
    assert decision.authorization is not None
    assert decision.authorization.approver is None
    assert decision.authorization.reason is (
        AuthorizationReason.AUTOMATIC_BELOW_APPROVAL_THRESHOLDS
    )
    assert decision.cost is not None
    assert decision.cost.maximum_compute_cost_usd == Decimal("2.86")


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
        manifest_name=OTHER_MANIFEST,
        approved_manifest_sha256=approved,
        submitter=LEAD,
        approver=ADMIN,
        approving_environment=ApprovalEnvironment.ADMIN,
    )

    assert outcome.decision.reason is AdmissionReason.MANIFEST_HASH_MISMATCH
    assert outcome.intent.manifest == load_manifest_fixture(OTHER_MANIFEST)


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


def test_a_run_a_lead_should_have_seen_is_refused_when_the_automatic_gate_released_it() -> (
    None
):
    """The mismatch that matters most under policy v5, and it points the other way now.

    Under v4 the dangerous direction was an exception released by the lead gate, because a
    lead was the weaker approver. There is no weaker approver than the automatic gate, which
    has no reviewer at all, so the submission worth refusing is a fan-out that arrived
    through it. Nothing in the workflow can produce that today; admission refuses it anyway,
    because the subject claim says which gate was passed and not that it was the right one.

    Mutation: read the class off the gate rather than re-deriving it in ``admit``. This is
    accepted, and a five-cell sweep runs with nobody having looked at it.
    """
    outcome = admit_submission(
        approver=None,
        approving_environment=ApprovalEnvironment.AUTOMATIC,
    )
    decision = outcome.decision

    assert decision.accepted is False
    assert decision.reason is AdmissionReason.APPROVAL_ENVIRONMENT_MISMATCH
    assert decision.approval_class is ApprovalClass.ROUTINE
    assert decision.approving_environment is ApprovalEnvironment.AUTOMATIC
    assert "run-approval-lead" in decision.detail
    assert "run-approval-automatic" in decision.detail


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
    """One submission through all three gates, and only one of them is its own.

    The fan-out classifies as routine whichever environment says it released it, and only
    ``run-approval-lead`` is accepted. Parametrised over the whole enum rather than over the
    two interesting values, so a fourth environment cannot be added without a row for it.

    Mutation: take the class from ``approving_environment`` instead of calling
    ``classify_request``. Every row is accepted, and the gate a run passed becomes the
    definition of the gate it needed.
    """
    outcome = admit_submission(
        approver=None if approving_environment is ApprovalEnvironment.AUTOMATIC else ADMIN,
        approving_environment=approving_environment,
    )

    assert outcome.decision.approval_class is ApprovalClass.ROUTINE
    assert outcome.decision.accepted is (approving_environment is ApprovalEnvironment.LEAD)


@pytest.mark.parametrize("released_by", list(ApprovalEnvironment))
@pytest.mark.parametrize("required", list(ApprovalEnvironment))
def test_only_one_gate_pair_that_is_not_equal_is_accepted(
    required: ApprovalEnvironment,
    released_by: ApprovalEnvironment,
) -> None:
    """EVERY PAIR OF GATES, BECAUSE THE ONE THAT MATTERS IS THE ONE NOBODY WRITES A CASE FOR.

    ``satisfies`` is what stands between a submission that needed a person and a run that
    reached AWS through a gate with no reviewer, so the interesting rows here are the seven
    that must be refused rather than the two that are accepted. Asserted as a full grid over
    the enum rather than as the cases somebody thought of, so a fourth environment cannot be
    added without a decision about every one of its pairs.

    Mutation: replace the body with a strength ordering, which is how this was first
    written. The row where ``ADMIN`` released a run needing ``LEAD`` flips to accepted, and
    that refusal is deliberate and predates this change: a run reaching a gate policy did not
    name means the routing went wrong somewhere, and admission is the only thing that would
    notice.

    Mutation: return ``self is required``, which is what this was before v6. The one accepted
    raise flips to refused, and every submission the daily ceiling routes to a lead is
    refused inside AWS after that lead has already released it.
    """
    permitted = released_by is required or (
        required is ApprovalEnvironment.AUTOMATIC and released_by is ApprovalEnvironment.LEAD
    )

    assert released_by.satisfies(required) is permitted


def test_the_gate_the_ceiling_raises_a_run_to_is_accepted_and_recorded_as_what_it_was() -> (
    None
):
    """The pair the daily ceiling produces, through ``admit`` rather than through the method.

    Mutation: keep the equality check in ``admit``. This is refused with
    ``approval_environment_mismatch`` after a lead has released it, which is the one outcome
    worse than having no ceiling at all: it spends an approver on every run the mechanism
    fires on and then throws that approval away.

    The record is asserted as well as the acceptance, and both halves are the point. This
    validator re-derives ``automatic`` because it cannot see the ledger that raised the
    class, and the record says exactly that: automatic, beside ``run-approval-lead``, beside
    an approver's name. Three true facts that together say a person looked at a run which
    needed nobody. A record claiming ``routine`` would be admission asserting a
    classification it did not make and could not check.
    """
    outcome = admit_submission(
        manifest_overrides={"fanout": None},
        submitter=MEMBER,
        approver=LEAD,
        approving_environment=ApprovalEnvironment.LEAD,
    )
    decision = outcome.decision

    assert decision.accepted is True
    assert decision.approval_class is ApprovalClass.AUTOMATIC
    assert decision.approving_environment is ApprovalEnvironment.LEAD
    assert decision.authorization is not None
    assert decision.authorization.approver == LEAD


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
    # THE CLASS ON A DENIED RECORD IS ROUTINE UNDER v5 AND WAS EXCEPTION UNDER v4. Nothing
    # classifies as an exception any more, and what an unresolvable input must never be is
    # automatic: "released by nobody" is the wrong sentence for a request nobody may
    # release, and it is the sentence a reader of this record would take away.
    #
    # Mutation: drop the ``INPUTS_THAT_MUST_RESOLVE`` test from ``classify_request``. All
    # three rows here are cheap enough to be automatic, so the class flips and the refusal
    # goes on being correct while the record beside it says no approver was needed.
    assert decision.approval_class is ApprovalClass.ROUTINE
    assert decision.approving_environment is ApprovalEnvironment.LEAD, (
        "an unresolvable input is denied outright before the gate is compared at all, so "
        "the environment on the record is whichever one released it"
    )


def reviews_for(identifiers: tuple[str, ...]) -> ImageScanExceptionRegistry:
    return ImageScanExceptionRegistry(
        schema_version=1,
        reviewed_vulnerabilities=tuple(
            ReviewedVulnerability(
                vulnerability_id=identifier,
                package_name="perl",
                reason=(
                    "Inherited from the pinned base, unreachable from the entrypoint, and "
                    "unfixable from this repository until Debian ships a patched package."
                ),
                recorded_by=ADMIN,
                recorded_at=RECORDED_AT,
            )
            for identifier in identifiers
        ),
    )


def scan_sentence(
    *, counted: int, findings: tuple[ScanFinding, ...] | None, reviews: tuple[str, ...] = ()
) -> str:
    """What one unreviewed scan is described as, so two of them can be read side by side.

    ASKED OF THE FUNCTION RATHER THAN OFF A DECISION RECORD, WHICH IS WHERE THIS SENTENCE
    LIVES NOW. Until policy v4 it reached a reader only as the tail of a denied-outright
    refusal detail, and reading it back off ``admit`` was the honest way to test it.
    ``image_scan_findings_unreviewed`` is an exception rather than an outright denial now,
    so the same sentence is what ``render_approver_context`` puts in front of the admin who
    can release the run, and ``admit`` still appends it if any other denied-outright
    condition fires beside a bad scan. One function, two readers, and this asks the
    function.
    """
    policy = load_approval_policy()
    registry = reviews_for(reviews)
    summary = ImageScanSummary(
        schema_version=1,
        status=ImageScanStatus.COMPLETE,
        scanned_at=RECORDED_AT,
        critical=counted,
    )
    return image_scan_refusal_detail(
        image_scan_verdict(
            image_digest=UNMATCHED_DIGEST,
            summary=summary,
            policy=policy.image_scan,
            registry=registry,
            blocking_findings=findings,
        ),
        summary=summary,
        policy=policy.image_scan,
        registry=registry,
        blocking_findings=findings,
    )


def unreviewed_criticals() -> tuple[ScanFinding, ...]:
    """Four CRITICAL findings the registry reports and nobody has recorded a review for."""
    return tuple(
        ScanFinding(vulnerability_id=identifier, package_name="perl")
        for identifier in REVIEWED_CVES
    )


def a_scan_reporting(findings: tuple[ScanFinding, ...]) -> ImageScanSummary:
    return ImageScanSummary(
        schema_version=1,
        status=ImageScanStatus.COMPLETE,
        scanned_at=RECORDED_AT,
        critical=len(findings),
    )


def test_an_unreviewed_image_is_a_lead_release_rather_than_a_wall() -> None:
    """Policy v5. Mutation: put image_scan_findings_unreviewed back in denied_outright.

    Denied outright meant refusable by nobody, so a wrong answer cost the whole route and
    the remedy on offer was editing a security file. It fired twice, both times against the
    owner self-approving as admin, and config/image-exceptions.yaml holds zero exceptions
    against sixteen reviewed findings, so nobody was ever going to write one. v4 made it an
    admin's call and v5 makes it a lead's.

    What the softening keeps is asserted beside what it drops. The findings still come from
    the registry rather than from the submitter, the run still reaches a person, and the
    person can now say yes.
    """
    findings = unreviewed_criticals()
    outcome = admit_submission(
        approver=LEAD,
        approving_environment=ApprovalEnvironment.LEAD,
        image_scan_registry=ImageScanExceptionRegistry(schema_version=1),
        image_scan_summary=a_scan_reporting(findings),
        image_scan_findings=findings,
    )

    assert outcome.decision.approval_class is ApprovalClass.ROUTINE
    assert outcome.decision.accepted is True
    assert outcome.decision.reason is AdmissionReason.ACCEPTED


def test_an_unreviewed_image_is_never_released_by_nobody_however_cheap_the_run_is() -> None:
    """The half of the gate neither softening touches, and the one v5 made load-bearing.

    ``cpu-routine.yaml`` is $2.86 in one cell, so every other test of it reaches
    ``run-approval-automatic``. With unreviewed CRITICAL findings behind the digest it
    reaches a lead instead, and admission refuses the automatic gate for it.

    Mutation: drop ``image_scan_reviewed`` from ``classify_request``. This is accepted, the
    container starts, and the findings ``render_approver_context`` prints are printed to
    nobody. Under v4 the same mutation only downgraded the approver, which is why the
    version of this test that asked about the lead gate would now pass against it.
    """
    findings = unreviewed_criticals()
    outcome = admit_submission(
        manifest_name=AUTOMATIC_MANIFEST,
        approver=None,
        approving_environment=ApprovalEnvironment.AUTOMATIC,
        image_scan_registry=ImageScanExceptionRegistry(schema_version=1),
        image_scan_summary=a_scan_reporting(findings),
        image_scan_findings=findings,
    )

    assert outcome.decision.approval_class is ApprovalClass.ROUTINE
    assert outcome.decision.accepted is False
    assert outcome.decision.reason is AdmissionReason.APPROVAL_ENVIRONMENT_MISMATCH
    assert ApprovalEnvironment.LEAD.value in outcome.decision.detail


def test_a_scan_this_platform_did_not_read_in_full_says_so_rather_than_blaming_a_reviewer(
) -> None:
    """WHAT THIS RECORDED BEFORE IS WHY THE WHOLE CHANGE EXISTS.

    ``olmo-eval-full`` carries thirteen criticals, every one of them reviewed, and the read
    returned four. The refusal named ``image_scan_findings_unreviewed`` and nothing else, so
    the reading available to an operator was that the reviews were missing -- and writing
    more of them could not have worked, because the findings that would have needed one
    were never fetched.
    """
    detail = scan_sentence(
        counted=13,
        findings=tuple(
            ScanFinding(vulnerability_id=identifier, package_name="perl")
            for identifier in REVIEWED_CVES
        ),
        reviews=REVIEWED_CVES,
    )

    assert "13" in detail and "4" in detail
    assert "did not read them all" in detail
    assert "Recording a review cannot clear this" in detail


def test_a_finding_nobody_reviewed_still_asks_for_a_review_and_names_it() -> None:
    """The other half of the pair, unchanged in outcome and now unambiguous in words.

    Every finding the registry counted arrived, so the platform can name the one that has
    no review against it and can say that recording one is what clears it. That sentence is
    true here and was false in the refusal above, which is the distinction being drawn.
    """
    detail = scan_sentence(
        counted=5,
        findings=tuple(
            ScanFinding(vulnerability_id=identifier, package_name="perl")
            for identifier in (*REVIEWED_CVES, "CVE-2026-99999")
        ),
        reviews=REVIEWED_CVES,
    )

    assert "CVE-2026-99999 in perl" in detail
    assert "config/image-exceptions.yaml" in detail
    assert "did not read them all" not in detail


def test_the_two_image_scan_verdicts_do_not_read_the_same() -> None:
    """Stated once, directly, because the two being indistinguishable is the defect.

    Both are the same condition, so what must differ is what the sentence asks the reader to
    do. That matters more since v4 rather than less: an admin who can release the run is
    being asked to tell a set of findings nobody reviewed from a set this platform failed to
    fetch, and only the first of those is a judgement anybody can make.
    """
    findings = tuple(
        ScanFinding(vulnerability_id=identifier, package_name="perl")
        for identifier in REVIEWED_CVES
    )
    unread = scan_sentence(counted=13, findings=findings, reviews=REVIEWED_CVES)
    unreviewed = scan_sentence(counted=4, findings=findings, reviews=REVIEWED_CVES[:3])

    assert "did not read them all" in unread
    assert "carries no recorded review" in unreviewed
    assert unread != unreviewed


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        (ImageScanVerdict.SCAN_UNREADABLE, "No registry scan result reached this decision"),
        (ImageScanVerdict.SCAN_INCOMPLETE, "IN_PROGRESS"),
    ],
)
def test_a_scan_that_was_never_read_is_not_reported_as_an_unreviewed_finding(
    verdict: ImageScanVerdict, expected: str
) -> None:
    """The two remaining ways of not knowing, which also used to read as unreviewed.

    ``ReadImageScan`` catches every failure and hands the error object to the validator in
    place of the findings, so an image nobody could read and an image whose scan has not
    finished both arrive as an absent or unfinished summary. Neither is a question a
    reviewer can answer, and both said they were.
    """
    unfinished = ImageScanSummary(
        schema_version=1, status=ImageScanStatus.IN_PROGRESS, scanned_at=RECORDED_AT
    )
    detail = image_scan_refusal_detail(
        verdict,
        summary=None if verdict is ImageScanVerdict.SCAN_UNREADABLE else unfinished,
        policy=load_approval_policy().image_scan,
        registry=ImageScanExceptionRegistry(schema_version=1),
        blocking_findings=None,
    )

    assert expected in detail
    assert "Recording a review cannot clear this" in detail


def test_a_pilot_repository_with_no_registration_is_refused_rather_than_admitted() -> None:
    """MEASURED BY PROBE BEFORE IT WAS FIXED, AND IT WAS ACCEPTED.

    ``repository_registered`` used to be membership of ``config/organization.yaml``'s
    ``pilot_repositories``, which lists OLMo-core and dolma. So a submission naming
    ``repository: dolma`` was a fact-for-fact valid one: it compiled, classified routine,
    routed to a lead, and admission accepted it -- and the state machine would then have
    submitted it to the CPU queue, where it would have run the OLMo-core image, because the
    image is pinned in the job definition rather than chosen by the submission.

    Nothing about that failure is visible from the outside. The job starts, the image runs,
    and the lineage record says a dolma run happened.
    """
    outcome = admit_submission(manifest_overrides={"repository": "dolma"})
    decision = outcome.decision

    assert decision.accepted is False
    assert decision.reason is AdmissionReason.DENIED_OUTRIGHT
    assert "unregistered_repository" in decision.detail
    # And the pilot list still says dolma, which is the point: the two files disagree and
    # only one of them is being asked.
    assert "dolma" in load_organization_inventory().pilot_repositories


def test_a_registered_repository_that_is_not_a_pilot_is_admitted() -> None:
    """The quieter direction of the same defect, which had not arrived yet.

    ``edullm-data`` is registered -- it has an ECR repository, a reviewed base image and a
    Dockerfile path -- and is not in the pilot list. Under the old derivation the first
    workload written against it would have been denied outright as an unregistered
    repository, and the reader would have gone to look at ``config/repositories.yaml``,
    where the registration was sitting.
    """
    outcome = admit_submission(manifest_overrides={"repository": "edullm-data"})

    assert outcome.decision.accepted is True
    assert "unregistered_repository" not in outcome.decision.detail
    assert "edullm-data" not in load_organization_inventory().pilot_repositories


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
    assert outcome.decision.cost.maximum_compute_cost_usd == Decimal("20.12")


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


def test_the_gate_being_right_is_not_the_same_as_the_approver_being_allowed() -> None:
    """Two questions, asked separately, and this one is the second.

    A member releasing their own fan-out through the lead gate has the right gate for the
    class and the wrong person behind it. Admission compares the environment first and the
    approver second, so this is refused on authorization rather than on routing.

    It asked the same thing of an exception at the admin gate until policy v5, which is a
    class no run reaches now. The property is unchanged and only the class it is asked of
    moved.

    Mutation: accept a submission whose environment matches without evaluating the
    authorization. This is admitted and a member releases their own five-cell sweep.
    """
    outcome = admit_submission(
        submitter=MEMBER,
        approver=MEMBER,
        approving_environment=ApprovalEnvironment.LEAD,
    )
    decision = outcome.decision

    assert decision.approval_class is ApprovalClass.ROUTINE
    assert decision.reason is AdmissionReason.AUTHORIZATION_DENIED
    assert decision.authorization is not None
    assert decision.authorization.reason is (
        AuthorizationReason.SELF_APPROVAL_NOT_PERMITTED_FOR_MEMBER
    )


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
