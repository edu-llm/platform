"""Judge one submission, inside AWS, against policy AWS has deployed.

The point of doing this here rather than in the workflow that submits is that nothing a
caller sends is taken as a finding. The caller supplies a manifest and the hash a reviewer
approved; everything else — whether the repository, dataset and compute profile are
registered, what the run may cost, which class it falls in, and whether the approver may
release it — is re-derived from configuration packaged with this code. A caller that lies
about a derived value does not change the outcome, because the derived value is never read
from the input.

Three orderings matter and are not interchangeable.

The manifest hash is checked before anything is derived from the manifest. An environment
gate approves a job, not content, so until the hash matches, the manifest is a document of
unknown provenance and deriving facts from it would mean judging something nobody approved.

The approving environment is checked against the classification rather than trusted. The
workflow routes a submission to a gate by computing its class in a job that holds no
credentials, and AWS accepts the resulting subject claim as proof the gate was passed — but
the claim says only *which* gate, not that it was the right one. Re-deriving the class here
and comparing is what stops an exception being released by a lead.

Authorization is evaluated last, because it is the only question whose answer depends on a
person rather than on the request.

One thing is resolved after the decision rather than as part of it. Where an accepted run
would go -- the queue, the job definition, the two roles -- is read from deployed
configuration once everything else has said yes, and a profile with nowhere to run becomes
a refusal with its own reason rather than an exception. That ordering is not cosmetic: a
submission refused for want of capacity has already been classified, priced and authorized,
and a reader of the record can see that the only thing wrong with it was the profile.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from pydantic import ValidationError

from edullm_platform.canonical import sha256_digest
from edullm_platform.contracts.admission import (
    AdmissionReason,
    ApprovalEnvironment,
    DecisionRecord,
    IntentRecord,
)
from edullm_platform.contracts.authorization import (
    AuthorizationDecision,
    evaluate_authorization,
)
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.execution import (
    ExecutionTarget,
    ExecutionTargetCatalog,
    UnbackedComputeProfileError,
)
from edullm_platform.contracts.image import GitHubWorkflowRunReference
from edullm_platform.contracts.image_scan import (
    ImageScanExceptionRegistry,
    ImageScanPolicy,
    ImageScanSummary,
    ImageScanVerdict,
    ScanFinding,
    image_scan_verdict,
    unreviewed_blocking_findings,
)
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.policy import (
    ApprovalClass,
    ApprovalPolicy,
    RequestFacts,
    classify_request,
)
from edullm_platform.contracts.repository_registry import RepositoryRegistry
from edullm_platform.contracts.workload import (
    ComputeProfileResolutionError,
    CostInputs,
    WorkloadCatalog,
)
from edullm_platform.execution import resolve_execution_target
from edullm_platform.manifest_helpers import (
    build_request_facts,
    compute_manifest_cost_inputs,
)

__all__ = [
    "AdmissionOutcome",
    "UnreadableManifestError",
    "admit",
    "denied_outright_conditions",
    "image_scan_refusal_detail",
]

#: How many unreviewed findings a refusal names before it stops listing them. An image with
#: forty of them produces a decision record nobody reads and a detail field wider than the
#: column it is printed in; the count is stated either way, so the list is an example rather
#: than the evidence.
_NAMED_FINDINGS_LIMIT = 5

#: Which ``denied_outright`` condition each fact, when false, corresponds to. Kept as data
#: rather than a chain of ``if`` statements so that a condition named in policy and never
#: checked here is detectable rather than silently inert.
_CONDITION_FOR_FALSE_FACT: dict[str, str] = {
    "repository_registered": "unregistered_repository",
    "dataset_registered": "unregistered_dataset",
    "dataset_is_a_corpus": "dataset_is_not_a_corpus",
    "compute_profile_registered": "unregistered_compute_profile",
    "immutable_revision": "mutable_repository_revision",
    "immutable_image": "mutable_image_reference",
    "image_scan_reviewed": "image_scan_findings_unreviewed",
}


class UnreadableManifestError(ValueError):
    """The submitted payload is not a run manifest at all.

    Distinct from a rejection. A rejected submission is one this system understood and
    refused, and it earns a decision record saying so. A payload that does not parse cannot
    be described by a record whose shape embeds a manifest, so it fails the execution
    instead and is left to the execution history. No compute follows either way.
    """


@dataclass(frozen=True)
class AdmissionOutcome:
    intent: IntentRecord
    decision: DecisionRecord
    #: Where this run goes, and ``None`` whenever it is not going anywhere. Populated only
    #: for an accepted decision, because a target resolved for a refused submission would
    #: be a queue and a job definition attached to a run nobody may start -- and the state
    #: machine's Choice reads the acceptance, not this.
    execution: ExecutionTarget | None = None

    @property
    def accepted(self) -> bool:
        return self.decision.accepted


def denied_outright_conditions(
    facts: RequestFacts, policy: ApprovalPolicy
) -> tuple[str, ...]:
    """Which of policy's denied-outright conditions this request trips, in policy order.

    Two of the five are unreachable from here, and deliberately so.
    ``mutable_repository_revision`` and ``mutable_image_reference`` are enforced by
    :class:`~edullm_platform.contracts.manifest.RunManifest` itself, whose patterns admit
    only a full commit SHA and a ``sha256:`` digest — so a submission naming a tag is
    refused as an unreadable manifest before any fact is derived, and never reaches a
    decision record. They stay listed in policy because policy states what is forbidden
    rather than which layer forbids it, and moving the enforcement earlier made it
    stronger, not weaker: a mutable reference cannot be represented, let alone approved.
    """
    tripped = {
        condition
        for fact_name, condition in _CONDITION_FOR_FALSE_FACT.items()
        if not getattr(facts, fact_name)
    }
    return tuple(
        condition for condition in policy.denied_outright if condition in tripped
    )


def image_scan_refusal_detail(
    verdict: ImageScanVerdict,
    *,
    summary: ImageScanSummary | None,
    policy: ImageScanPolicy,
    registry: ImageScanExceptionRegistry,
    blocking_findings: Sequence[ScanFinding] | None,
) -> str:
    """The sentence a refused image earns, chosen by which kind of no the gate reached.

    Written as four separate sentences rather than one parameterised one because they ask
    four different things of whoever reads the decision record, and only one of them asks
    for a review. A refusal that named unreviewed findings when the findings had never been
    fetched sent an operator to write reviews for vulnerabilities that already had them --
    the image stayed refused, correctly, and nothing they could do to the exception file
    would have changed that. Saying which of the four happened is the difference between a
    person fixing the image and a person fixing this platform.

    The counts are quoted rather than described because the arithmetic is the evidence: a
    reader who can see thirteen counted against four received does not have to take the
    conclusion on trust.
    """
    counted = policy.blocking_findings(summary) if summary is not None else 0
    severities = ", ".join(severity.value for severity in policy.blocking_severities)
    if verdict is ImageScanVerdict.SCAN_UNREADABLE:
        return (
            "No registry scan result reached this decision, so nothing is known about this "
            "image's findings. Recording a review cannot clear this: the scan has to be "
            "read before there is anything to review."
        )
    if verdict is ImageScanVerdict.SCAN_INCOMPLETE:
        status = summary.status.value if summary is not None else "unknown"
        return (
            f"The registry reports this image's scan as {status} rather than COMPLETE, so "
            "there is no settled set of findings. Recording a review cannot clear this: the "
            "scan has to finish first."
        )
    if verdict is ImageScanVerdict.FINDINGS_UNREAD:
        received = 0 if blocking_findings is None else len(blocking_findings)
        return (
            f"The registry counts {counted} findings at {severities} against this image and "
            f"{received} reached this decision, so this platform did not read them all. "
            "Recording a review cannot clear this: the findings a review would name are not "
            "here to be matched against one."
        )
    unreviewed = unreviewed_blocking_findings(
        blocking_findings=blocking_findings or (), registry=registry
    )
    named = ", ".join(
        f"{finding.vulnerability_id} in {finding.package_name}"
        for finding in unreviewed[:_NAMED_FINDINGS_LIMIT]
    )
    remainder = len(unreviewed) - _NAMED_FINDINGS_LIMIT
    listed = named if remainder <= 0 else f"{named}, and {remainder} more"
    return (
        f"The registry counts {counted} findings at {severities} against this image, all "
        f"{counted} reached this decision, and {len(unreviewed)} of them "
        f"{'carries' if len(unreviewed) == 1 else 'carry'} no recorded review: {listed}. "
        "Recording a review in config/image-exceptions.yaml clears this, as does rebuilding "
        "the image without them."
    )


def _parse_manifest(payload: Mapping[str, object]) -> RunManifest:
    try:
        return RunManifest.model_validate(dict(payload))
    except ValidationError as exc:
        raise UnreadableManifestError(
            f"the submitted payload is not a valid run manifest: {exc.error_count()} problems"
        ) from exc


def admit(
    *,
    manifest_payload: Mapping[str, object],
    approved_manifest_sha256: str,
    run_id: str,
    submitter: str,
    approver: str | None,
    approving_environment: ApprovalEnvironment,
    workflow_run: GitHubWorkflowRunReference,
    policy: ApprovalPolicy,
    inventory: OrganizationInventory,
    repositories: RepositoryRegistry,
    catalog: WorkloadCatalog,
    execution_targets: ExecutionTargetCatalog,
    account_id: str,
    dataset_registry: DatasetRegistry,
    image_scan_registry: ImageScanExceptionRegistry,
    image_scan_summary: ImageScanSummary | None,
    image_scan_findings: Sequence[ScanFinding] | None,
    recorded_at: datetime,
) -> AdmissionOutcome:
    manifest = _parse_manifest(manifest_payload)
    recomputed = sha256_digest(manifest)

    intent = IntentRecord(
        schema_version=1,
        run_id=run_id,
        submitter=submitter,
        manifest=manifest,
        manifest_sha256=recomputed,
        approving_environment=approving_environment,
        workflow_run=workflow_run,
        recorded_at=recorded_at,
    )

    def decide(
        *,
        reason: AdmissionReason,
        detail: str,
        approval_class: ApprovalClass,
        authorization: AuthorizationDecision | None,
        cost: CostInputs | None,
        execution: ExecutionTarget | None = None,
    ) -> AdmissionOutcome:
        return AdmissionOutcome(
            intent=intent,
            execution=execution,
            decision=DecisionRecord(
                schema_version=1,
                run_id=run_id,
                manifest_sha256=recomputed,
                policy_version=policy.policy_version,
                approval_class=approval_class,
                approving_environment=approving_environment,
                authorization=authorization,
                cost=cost,
                accepted=reason is AdmissionReason.ACCEPTED,
                reason=reason,
                detail=detail,
                recorded_at=recorded_at,
            ),
        )

    if recomputed != approved_manifest_sha256:
        return decide(
            reason=AdmissionReason.MANIFEST_HASH_MISMATCH,
            detail=(
                "The manifest presented after approval does not hash to the value that was "
                "approved. An environment gate releases a job, not its content, so this is "
                "refused without evaluating anything derived from the manifest."
            ),
            approval_class=ApprovalClass.EXCEPTION,
            authorization=None,
            cost=None,
        )

    cost: CostInputs | None
    try:
        cost = compute_manifest_cost_inputs(manifest, catalog)
        estimated_cost_usd = cost.maximum_compute_cost_usd
    except ValueError:
        # The profile is not in the catalog, so it has no rate and no total. The request is
        # already denied outright below; the placeholder never reaches a record.
        cost = None
        estimated_cost_usd = Decimal(0)

    facts = build_request_facts(
        manifest,
        repositories=repositories,
        catalog=catalog,
        dataset_registry=dataset_registry,
        estimated_cost_usd=estimated_cost_usd,
        # Re-derived here rather than taken from the caller, for the same reason the
        # manifest hash is recomputed: the compile step read the scan from a provenance
        # record on a runner, and this is the side that decides. A caller that lied about
        # the findings gets the answer ECR gives, not the one it supplied.
        image_scan_policy=policy.image_scan,
        image_scan_registry=image_scan_registry,
        image_scan_summary=image_scan_summary,
        image_scan_findings=image_scan_findings,
    )
    # RE-DERIVED HERE AND NOT TAKEN FROM THE RUNNER, WHICH IS THE POINT OF THIS FUNCTION.
    # The rate that used to be passed beside the facts is gone with the ceiling that read
    # it, so the class is a function of the facts and the deployed thresholds alone.
    #
    # ``capacity_block_backed`` is one of those facts and is derived from the catalog inside
    # AWS, so a submitter who edited a compile step's answer cannot demote a block-backed
    # request to a team lead's gate. That is the same asymmetry the registration flags have and
    # it matters more here, because the thing on the other side of it has already been paid for.
    approval_class = classify_request(facts, policy.thresholds)
    authorization = evaluate_authorization(
        submitter=submitter,
        approver=approver,
        request=facts,
        policy=policy,
        inventory=inventory,
    )

    tripped = denied_outright_conditions(facts, policy)
    if tripped:
        # The condition names say which gate refused and nothing about why, which is enough
        # for the four inputs that are simply absent from a registry: a reader can go and
        # look at the file. An image scan is the one condition whose name was actively
        # misleading, because the same name covered a finding nobody had reviewed and a scan
        # this platform had failed to read, and those send a reader to opposite places.
        scan_detail = (
            ""
            if _CONDITION_FOR_FALSE_FACT["image_scan_reviewed"] not in tripped
            else " "
            + image_scan_refusal_detail(
                image_scan_verdict(
                    image_digest=manifest.image_digest,
                    summary=image_scan_summary,
                    policy=policy.image_scan,
                    registry=image_scan_registry,
                    blocking_findings=image_scan_findings,
                ),
                summary=image_scan_summary,
                policy=policy.image_scan,
                registry=image_scan_registry,
                blocking_findings=image_scan_findings,
            )
        )
        return decide(
            reason=AdmissionReason.DENIED_OUTRIGHT,
            detail=(
                "The submission trips conditions policy denies outright rather than "
                f"classifies: {', '.join(tripped)}. These are not expensive requests "
                "somebody may approve; they are requests whose inputs cannot be resolved."
                f"{scan_detail}"
            ),
            approval_class=approval_class,
            authorization=authorization,
            cost=cost,
        )

    # AT LEAST AS STRONG, RATHER THAN EQUAL, AND THE ASYMMETRY IS THE WHOLE OF THE CHECK.
    # A run that needed a lead and arrived through the gate that asks nobody is refused
    # here, which is the property this line has always been for and is unchanged. A run
    # that needed nobody and arrived through a lead is accepted, because a person looking
    # at a run that required no person cannot be an escalation of anything.
    #
    # IT WAS EQUALITY UNTIL v6 AND THE THING THAT MADE EQUALITY WRONG IS THE DAILY CEILING.
    # `daily_ceiling.class_under_the_ceiling` raises a submission from automatic to routine
    # when the day's unattended commitments have crossed the bound in config/policy.yaml, so
    # the compile job sends it to a lead. This validator cannot re-derive that: the ledger it
    # would have to read is a branch in GitHub rather than anything in AWS, and if it could
    # reach it, it would read a later day-total than the compile job did minutes earlier and
    # refuse runs on the difference. Under equality every run the ceiling routed would have
    # been refused after a lead had already released it, which is the one outcome worse than
    # not having the ceiling.
    #
    # So the ledger-reading half stays on the side that has the ledger, and this side checks
    # the thing it can check without reading anything: that nobody got a weaker gate than the
    # facts demand. `classify_request` is still re-derived here from the manifest and the
    # deployed thresholds, and it still decides the floor.
    required_environment = ApprovalEnvironment.for_approval_class(approval_class)
    if not approving_environment.satisfies(required_environment):
        return decide(
            reason=AdmissionReason.APPROVAL_ENVIRONMENT_MISMATCH,
            detail=(
                f"The submission classifies as {approval_class.value} and so requires the "
                f"{required_environment.value} gate or a stronger one, but it was released "
                f"by {approving_environment.value}."
            ),
            approval_class=approval_class,
            authorization=authorization,
            cost=cost,
        )

    if not authorization.granted:
        return decide(
            reason=AdmissionReason.AUTHORIZATION_DENIED,
            detail=(
                f"Authorization was refused: {authorization.reason.value}."
            ),
            approval_class=approval_class,
            authorization=authorization,
            cost=cost,
        )

    try:
        execution = resolve_execution_target(
            compute_profile=manifest.compute_profile,
            catalog=catalog,
            targets=execution_targets,
            account_id=account_id,
        )
    except (ComputeProfileResolutionError, UnbackedComputeProfileError) as exc:
        # A policy refusal, not a crash. The profile is registered and priced -- an
        # unregistered one was denied outright above -- so what happened is that the
        # platform was asked for capacity it does not have, which is a thing to record and
        # tell the submitter rather than a reason for the validator to fail the execution
        # and leave no decision behind.
        #
        # THIS BRANCH WAS PROPOSED FOR REMOVAL ON 2026-08-05 AND IS KEPT, WHICH IS WORTH
        # THE PARAGRAPH BECAUSE THE PROPOSAL WAS RIGHT ABOUT EVERYTHING EXCEPT WHAT COMES
        # AFTER IT. It has fired twice in 158 submissions, both on gpu-1xa10g, both past
        # the approval gate and therefore both with somebody's signature already spent,
        # and that is a fair description of a refusal in the wrong place.
        #
        # What it cannot be is removed, because there is nothing to fall through to.
        # ``resolve_execution_target`` failing means no queue ARN and no job-definition
        # ARN exist for this profile, so the alternative to refusing is this function
        # raising and the state machine failing the execution with no decision record
        # written -- the submitter gets less, the record gets nothing, and the account
        # still cannot run the job. That is worse on every axis than the refusal.
        #
        # It is also already unreachable from the submission form, which is where the two
        # refusals should have been prevented and now are. The compute_profile input is a
        # `choice`, and tests/test_submission_form_options.py holds its option list equal
        # in both directions to the set this resolver can answer for. What remains
        # reachable is the case that actually produced both refusals: this zip carries its
        # own copy of config/, so a profile promoted on main and not yet released to the
        # validator resolves on the runner and fails here. No check before the gate can see
        # that, because every check before the gate reads the fresh files. Cutting the
        # release is the fix, and infra/README.md and tests/test_pending_releases.py are
        # where that is enforced.
        return decide(
            reason=AdmissionReason.NO_EXECUTION_TARGET,
            detail=(
                f"The submission was authorized and has nowhere to run: {exc.reason_code}. "
                f"Compute profile {manifest.compute_profile!r} is registered and priced, "
                "and no compute environment deployed by this platform backs it. The "
                "submission form does not offer a profile in this state, so the usual "
                "cause is that this validator is running an older config/ than main: "
                "check infra/admission-validator-release.yaml against the catalog."
            ),
            approval_class=approval_class,
            authorization=authorization,
            cost=cost,
        )

    return decide(
        reason=AdmissionReason.ACCEPTED,
        detail=(
            f"Admitted as {approval_class.value} under policy {policy.policy_version}, "
            f"released by {approving_environment.value} and authorized as "
            f"{authorization.reason.value}."
        ),
        approval_class=approval_class,
        authorization=authorization,
        cost=cost,
        execution=execution,
    )
