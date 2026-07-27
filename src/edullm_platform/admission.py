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
"""

from __future__ import annotations

from collections.abc import Mapping
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
from edullm_platform.contracts.image import GitHubWorkflowRunReference
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.policy import (
    ApprovalClass,
    ApprovalPolicy,
    RequestFacts,
    classify_request,
)
from edullm_platform.contracts.workload import CostInputs, WorkloadCatalog
from edullm_platform.manifest_helpers import (
    build_request_facts,
    compute_manifest_cost_inputs,
)

__all__ = [
    "AdmissionOutcome",
    "UnreadableManifestError",
    "admit",
    "denied_outright_conditions",
]

#: Which ``denied_outright`` condition each fact, when false, corresponds to. Kept as data
#: rather than a chain of ``if`` statements so that a condition named in policy and never
#: checked here is detectable rather than silently inert.
_CONDITION_FOR_FALSE_FACT: dict[str, str] = {
    "repository_registered": "unregistered_repository",
    "dataset_registered": "unregistered_dataset",
    "compute_profile_registered": "unregistered_compute_profile",
    "immutable_revision": "mutable_repository_revision",
    "immutable_image": "mutable_image_reference",
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

    @property
    def accepted(self) -> bool:
        return self.decision.accepted


def denied_outright_conditions(
    facts: RequestFacts, policy: ApprovalPolicy
) -> tuple[str, ...]:
    """Which of policy's denied-outright conditions this request trips, in policy order."""
    tripped = {
        condition
        for fact_name, condition in _CONDITION_FOR_FALSE_FACT.items()
        if not getattr(facts, fact_name)
    }
    return tuple(
        condition for condition in policy.denied_outright if condition in tripped
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
    catalog: WorkloadCatalog,
    dataset_registry: DatasetRegistry,
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
    ) -> AdmissionOutcome:
        return AdmissionOutcome(
            intent=intent,
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
        # The profile is not in the catalog, so it has no rate. The request is already
        # denied outright below; the placeholder never reaches a record.
        cost = None
        estimated_cost_usd = Decimal(0)

    facts = build_request_facts(
        manifest,
        inventory=inventory,
        catalog=catalog,
        dataset_registry=dataset_registry,
        estimated_cost_usd=estimated_cost_usd,
    )
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
        return decide(
            reason=AdmissionReason.DENIED_OUTRIGHT,
            detail=(
                "The submission trips conditions policy denies outright rather than "
                f"classifies: {', '.join(tripped)}. These are not expensive requests "
                "somebody may approve; they are requests whose inputs cannot be resolved."
            ),
            approval_class=approval_class,
            authorization=authorization,
            cost=cost,
        )

    required_environment = ApprovalEnvironment.for_approval_class(approval_class)
    if approving_environment is not required_environment:
        return decide(
            reason=AdmissionReason.APPROVAL_ENVIRONMENT_MISMATCH,
            detail=(
                f"The submission classifies as {approval_class.value} and so requires the "
                f"{required_environment.value} gate, but it was released by "
                f"{approving_environment.value}."
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
    )
