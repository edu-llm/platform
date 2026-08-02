"""What admission was asked for, and what it concluded.

Two records rather than one, written separately and keyed the same. The split is not
tidiness: an intent record says a submission reached AWS and is true whatever the outcome,
while a decision record says how it was judged. Folding them together would mean the only
durable trace of a rejected submission was a record whose existence had to be read as *not*
an acceptance, and would leave a request that failed part-way through admission with no
record at all.

Nothing here marks a run as accepted except :attr:`DecisionRecord.accepted`. An intent
record's existence must never be readable as approval, because it is written before the
judgement it precedes.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, model_validator

from .authorization import AuthorizationDecision
from .base import ContractModel, Sha256Digest, UtcTimestamp, parse_str_enum
from .bindings import GitHubLogin
from .identity import RunId
from .image import GitHubWorkflowRunReference
from .manifest import RunManifest
from .policy import POLICY_VERSION_PATTERN, ApprovalClass
from .workload import CostInputs

__all__ = [
    "AdmissionReason",
    "ApprovalEnvironment",
    "DecisionRecord",
    "IntentRecord",
]


class ApprovalEnvironment(StrEnum):
    """The GitHub environment a job passed through to reach AWS.

    These strings are load-bearing in three places at once and must agree in all of them:
    the ``environment:`` key of the submission workflow, the GitHub environment names
    themselves, and the ``sub`` condition of the admission role's trust policy, which
    enumerates them exactly rather than matching a wildcard. Renaming one here without
    renaming it in the other two silently revokes the trust — and the failure surfaces as
    an ``AssumeRole`` denial that reads like a broken role ARN.

    ``AUTOMATIC`` is a real environment carrying real protection, and the one thing it does
    not carry is a reviewer. It is pinned to ``main`` by a deployment branch policy and has
    ``can_admins_bypass`` false, exactly as the other two do, so what it removes is the
    person and not the gate. ``prevent_self_review`` is absent on it rather than false: the
    API refuses to set that flag on an environment with no reviewers, answering 422, so a
    reader comparing the three captures will see the field derived as false from an absent
    rule instead of configured off. There is nobody to prevent from reviewing.
    """

    AUTOMATIC = "run-approval-automatic"
    LEAD = "run-approval-lead"
    ADMIN = "run-approval-admin"

    @classmethod
    def for_approval_class(cls, approval_class: ApprovalClass) -> ApprovalEnvironment:
        """Which gate a submission of this class must pass through.

        Policy picks the gate; the submitter never does. The workflow reads this through a
        job that holds no credentials, so the routing decision is made before anything can
        reach AWS, and admission re-derives it afterwards to check that the environment
        which actually approved is the one the class demanded.

        Every class is named rather than falling through to a default. This used to end
        ``return cls.LEAD``, which was correct while there were two classes and became a
        silent lie the moment there was a third: a new class would route to the lead gate,
        a lead would release the run, and the decision record would go on claiming the
        class that asked for no lead. Nothing in the tree caught that, because a record
        whose environment matches what its class demands is exactly what the accepted-
        decision validator checks for.
        """
        match approval_class:
            case ApprovalClass.AUTOMATIC:
                return cls.AUTOMATIC
            case ApprovalClass.ROUTINE:
                return cls.LEAD
            case ApprovalClass.EXCEPTION:
                return cls.ADMIN


ApprovalEnvironmentValue = Annotated[
    ApprovalEnvironment, BeforeValidator(parse_str_enum(ApprovalEnvironment))
]


class AdmissionReason(StrEnum):
    ACCEPTED = "accepted"
    #: The manifest presented after approval does not hash to the value approved before
    #: it. An environment gate approves a job, not content.
    MANIFEST_HASH_MISMATCH = "manifest_hash_mismatch"
    #: A condition in ``denied_outright`` held: an unregistered repository, dataset or
    #: compute profile, or a mutable revision or image reference. Not an expensive request
    #: somebody may approve — a request whose inputs cannot be resolved.
    DENIED_OUTRIGHT = "denied_outright"
    #: The approver may not release this submitter's request.
    AUTHORIZATION_DENIED = "authorization_denied"
    #: The submission was classified into one approval path and arrived through the other.
    APPROVAL_ENVIRONMENT_MISMATCH = "approval_environment_mismatch"
    #: The compute profile is registered and priced, and nothing backs it: either the
    #: catalog does not call it provisioned, or it does and no execution target names it.
    #: A refusal rather than an exception, because a manifest asking for capacity that
    #: does not exist is a request this platform understood and declined -- and a
    #: submission that crashed the validator would leave no decision record saying so.
    #: Which of the two it was is in ``detail``; both mean there is nowhere to run.
    NO_EXECUTION_TARGET = "no_execution_target"


AdmissionReasonValue = Annotated[
    AdmissionReason, BeforeValidator(parse_str_enum(AdmissionReason))
]


class IntentRecord(ContractModel):
    """What one submission asked for, recorded before it was judged."""

    schema_version: Literal[1]
    run_id: RunId
    submitter: GitHubLogin
    manifest: RunManifest
    #: The digest of the manifest as approved. Recomputed inside AWS from ``manifest`` and
    #: compared, so a record whose two halves disagree is detectable after the fact rather
    #: than only at the moment of writing.
    manifest_sha256: Sha256Digest
    approving_environment: ApprovalEnvironmentValue
    workflow_run: GitHubWorkflowRunReference
    recorded_at: UtcTimestamp


class DecisionRecord(ContractModel):
    """How admission judged one submission."""

    schema_version: Literal[1]
    run_id: RunId
    manifest_sha256: Sha256Digest
    #: Which reviewed policy produced this, so a later reader can tell a decision that was
    #: routine under the rules of its day from one that would not be under today's.
    policy_version: str = Field(pattern=POLICY_VERSION_PATTERN)
    approval_class: Annotated[ApprovalClass, BeforeValidator(parse_str_enum(ApprovalClass))]
    approving_environment: ApprovalEnvironmentValue
    #: ``None`` only where no approver question was ever reached, which happens exactly
    #: when the manifest hash did not match. Nothing derived from an unapproved manifest is
    #: trustworthy, including who its team is, so recording a manufactured denial reason
    #: would put a finding in the record that nothing established.
    authorization: AuthorizationDecision | None
    #: The inputs as well as the total, so a later reading can tell an underestimate from
    #: a policy change. A total alone cannot distinguish the two.
    #:
    #: ``None`` only where the cost genuinely could not be computed, which happens exactly
    #: when the compute profile is unregistered and so has no rate. Recording a zero there
    #: would be cheaper and would be a lie — it would read as a free run rather than an
    #: unpriceable one, and it is the cheapest-looking value in the field's range.
    cost: CostInputs | None
    accepted: bool
    reason: AdmissionReasonValue
    detail: str = Field(min_length=1)
    recorded_at: UtcTimestamp

    @model_validator(mode="after")
    def validate_outcome_matches_reason(self) -> Self:
        if self.accepted != (self.reason is AdmissionReason.ACCEPTED):
            raise ValueError("admission outcome must match the recorded reason")
        if self.accepted and (self.authorization is None or not self.authorization.granted):
            raise ValueError("an accepted decision must record a granted authorization")
        if self.authorization is None and self.reason is not AdmissionReason.MANIFEST_HASH_MISMATCH:
            raise ValueError(
                "only a manifest-hash mismatch may omit the authorization evaluation"
            )
        if self.accepted and self.cost is None:
            raise ValueError("an accepted decision must record what it is expected to cost")
        if self.accepted and self.approving_environment is not ApprovalEnvironment.for_approval_class(
            self.approval_class
        ):
            raise ValueError(
                "an accepted decision must have passed the gate its classification demands"
            )
        return self
