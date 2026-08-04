"""A committed template amendment the account has not caught up with yet.

Every role this repository compares against a template is created or amended from a
laptop, because ``infra/README.md`` exists to keep role creation out of a pipeline. So a
template change and the deploy that realises it are two acts with a window between them,
and inside that window the comparison in :mod:`edullm_platform.role_drift` reports the
deployed role as ``NARROWER`` than the template. It is right to: the committed template
has stopped describing the account.

**Why this is a library module rather than a note in a test.** It was a note in a test,
and the cost showed up immediately. A test module knew which difference was expected;
:mod:`edullm_platform.phase1_capture` did not, so it reported the role as ``DRIFTED`` --
the same verdict a role widened in the console gets -- and every consumer downstream had
to re-derive "is this the expected one?" from whatever it happened to have. The proof
generator did not re-derive it at all: it treated *any* capture that stopped holding as
the pending case, so an expired capture and an undeployed amendment produced the same
skip. An expiry that reads as a deploy nobody has run yet is exactly the kind of quiet
substitution the freshness window exists to prevent.

So the record lives here, the capture reader consults it, and the state gets its own
verdict: :attr:`~edullm_platform.phase1_capture.CaptureVerdict.PENDING_DEPLOY`.

**Naming the state is not the same as excusing it.** A capture waiting on a deploy still
does not hold, the criteria resting on it are still not certified, and the proof generator
still refuses to write a bundle. What changes is that the refusal can say which of the two
things happened, and that a reader downstream can tell an expected difference from an
unexplained one without guessing.

**Every record is self-clearing, and that is the whole design.** An entry carries the two
things a ``DEFERRED`` criterion carries -- a :attr:`~PendingAmendment.reason` a reader can
weigh and a :attr:`~PendingAmendment.cleared_by` that says what ends it -- plus a third a
criterion does not: the :attr:`~PendingAmendment.findings` themselves, compared for
equality. So the record fails the moment the account stops differing in exactly this way,
in either direction. It cannot outlive the deploy it is waiting for, and it cannot quietly
absorb a second difference that arrives while it is open.

**Every finding must be ``NARROWER``.** An undeployed template change can only leave the
account behind the template. A deployed role that grants something its template does not
is a security finding, and nothing pending explains one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from edullm_platform.phase2_evidence import PHASE2_ROLE_TEMPLATES
from edullm_platform.role_drift import (
    COMMITTED_ROLE_TEMPLATES,
    PHASE3_ROLE_TEMPLATES,
    DriftDirection,
    RoleDriftFinding,
)

__all__ = [
    "PENDING_AMENDMENTS",
    "PendingAmendment",
    "PendingAmendmentError",
    "declared_role_templates",
    "pending_amendments",
    "pending_for",
]

DEPLOYER_ROLE_NAME: Final = "sbsandbox-intern-edullm-infra-deployer"

#: The five compute profiles ``infra/iam/admission-service-roles.yaml`` names and the
#: deployed admission states role does not reach, in the order the drift comparison sorts
#: them. Read off the capture rather than chosen: these are the profiles a submission cannot
#: currently be placed on, and the list is here so the record below can be checked against
#: the account by a reader who is not going to re-derive fifteen ARNs by hand.
UNREACHABLE_COMPUTE_PROFILES: Final = (
    "gpu-1xh100",
    "gpu-1xl40s",
    "gpu-8xl4",
    "gpu-8xl40s",
    "gpu-8xt4",
)

#: The ARN form the drift comparison folds an account's identifiers into before reporting.
#: Spelled here rather than substituted, because the finding this record must equal is
#: written in the redacted form and comparing against a real account id would make the
#: record fail on the day somebody captures from a second account.
_REDACTED_BATCH_ARN: Final = "arn:<partition>:batch:<region>:<account>"


def _missing_queue_resources() -> str:
    """The exact ``detail`` the comparison reports for both narrowed resource lists.

    Built rather than pasted. The two statements report an identical string -- SubmitJob and
    TagResource name the same resources, which is the property the template argues for at
    length -- and a fifteen-ARN literal written out twice is two chances to introduce a typo
    that would make this record stop explaining the finding it exists for.
    """
    resources = sorted(
        [
            f"{_REDACTED_BATCH_ARN}:job-definition/sbsandbox-intern-edullm-{profile}-run{suffix}"
            for profile in UNREACHABLE_COMPUTE_PROFILES
            for suffix in ("", ":*")
        ]
        + [
            f"{_REDACTED_BATCH_ARN}:job-queue/sbsandbox-intern-edullm-{profile}"
            for profile in UNREACHABLE_COMPUTE_PROFILES
        ]
    )
    return "the template declares resources the deployed role does not: " + ", ".join(resources)


MISSING_QUEUE_RESOURCES: Final = _missing_queue_resources()


class PendingAmendmentError(ValueError):
    """A recorded pending amendment is not something a reader could act on."""


def declared_role_templates() -> dict[str, str]:
    """Every role some committed template declares, across all three phases.

    The three registries stay separate where they are defined, because a Phase 3 role
    drifting must not fail a Phase 1 capture. They are merged only here, and only to
    answer one question: is there a template that will ever compare this role? A pending
    amendment for a role nothing compares would never clear, because nothing would ever
    report the findings it is waiting to stop seeing.
    """
    return {
        **dict(COMMITTED_ROLE_TEMPLATES),
        **dict(PHASE2_ROLE_TEMPLATES),
        **dict(PHASE3_ROLE_TEMPLATES),
    }


@dataclass(frozen=True)
class PendingAmendment:
    """A template amendment that is committed and has not been applied to the account."""

    role_name: str
    reason: str
    cleared_by: str
    findings: tuple[RoleDriftFinding, ...]

    def __post_init__(self) -> None:
        if not self.findings:
            raise self._fail(
                "records no findings; a pending amendment that expects no difference is "
                "a record with nothing to clear"
            )
        for name in ("reason", "cleared_by"):
            if not getattr(self, name).strip():
                raise self._fail(f"does not say {name.replace('_', ' ')}")
        ahead = [
            finding for finding in self.findings if finding.direction is not DriftDirection.NARROWER
        ]
        if ahead:
            raise self._fail(
                "records a finding that is not narrower: "
                + ", ".join(f"{one.direction.value} at {one.element}" for one in ahead)
                + ". An undeployed template change leaves the account behind the "
                "template; a role that grants more than its template is a security "
                "finding and no pending deploy explains it"
            )

    def _fail(self, problem: str) -> PendingAmendmentError:
        return PendingAmendmentError(f"pending amendment for {self.role_name}: {problem}")

    def explains(self, findings: Sequence[RoleDriftFinding]) -> bool:
        """Whether these are exactly the differences this record is waiting on.

        Equality rather than containment, in both directions. Containment would let a
        second difference arrive under cover of the first, and would go on reading as
        explained after the deploy removed only part of what is recorded.
        """
        return tuple(findings) == self.findings


def pending_amendments() -> tuple[PendingAmendment, ...]:
    """Every committed template amendment the account has not caught up with yet."""
    # Empty, which is the state this registry is meant to spend most of its life in. An
    # entry lives here only between a template amendment being committed and the laptop
    # deploy that realises it. Three have been removed so far: the Phase 2 deployer
    # amendment and the Phase 3 one -- a third job_workflow_ref for deploy-phase3-batch.yml
    # and the deploy-phase3-batch-stacks inline policy -- both on 2026-07-27 when
    # sbsandbox-intern-edullm-infra-deployer-iam was applied, and the
    # sbsandbox-intern-edullm-batch-workload `read-the-dataset-airlock` policy on
    # 2026-08-04 when sbsandbox-intern-edullm-phase3-batch-iam was applied and the
    # re-capture reported no findings on any of the four Phase 3 roles.
    #
    # Removal rather than exemption is the rule. The findings are compared for equality,
    # so a record left here after its deploy fails rather than lingering, and nothing in
    # this module offers a way to keep one that no longer describes a difference.
    #
    # THE ONE ENTRY BELOW IS NOT THE ORDINARY CASE THIS MODULE WAS BUILT FOR, AND A READER
    # SHOULD NOT FILE IT AS ONE. Every other record here has described a template amendment
    # waiting on somebody to find a laptop. This one describes a template that IAM refuses
    # to accept: the deploy has been attempted and it failed on a service limit, so no
    # amount of redeploying clears it. What clears it is a change to the template, and
    # `cleared_by` says which change and why the obvious one is wrong.
    amendments: tuple[PendingAmendment, ...] = (
        PendingAmendment(
            role_name="sbsandbox-intern-edullm-admission-states",
            reason=(
                "infra/iam/admission-service-roles.yaml enumerates sixteen job queues on "
                "both batch:SubmitJob and batch:TagResource; the deployed role carries "
                "eleven. The five absent from the account are gpu-1xh100, gpu-1xl40s, "
                "gpu-8xl4, gpu-8xl40s and gpu-8xt4, and a submission naming any of them "
                "fails at the submit state with a 403 rather than being refused at "
                "admission. This is not a deploy nobody has run. It was run on 2026-08-02 "
                "and CloudFormation rolled it back: rendered with the account's own "
                "partition, region and id, run-admission-workflow is 10599 bytes and IAM "
                "caps the aggregate of a role's inline policies at 10240, so the update "
                "failed with ServiceLimitExceeded and the stack has sat in "
                "UPDATE_ROLLBACK_COMPLETE since. Re-running the same deploy reproduces the "
                "same rollback."
            ),
            cleared_by=(
                "getting run-admission-workflow under 10240 bytes and deploying it, then "
                "re-running tools/capture_phase3_evidence.py --target phase2-roles and "
                "deleting this record. Splitting the document into two inline policies "
                "does not work and is the first thing anybody will try: the IAM quota is "
                "on the aggregate of every inline policy on the role, not on each one. A "
                "customer managed policy is ruled out where the template says so -- "
                "InternSandboxBoundary denies iam:CreatePolicyVersion, so it could be "
                "created once and never amended. What is left is spending fewer bytes on "
                "the same reach, and the template's own argument for the RegisterJobDefinition "
                "scope shows the way: an IAM wildcard matches ':' like any other character, "
                "so the paired `-run` and `-run:*` entries collapse to one `-run*` entry "
                "each. That is 32 ARNs across the two statements and roughly 3000 bytes, "
                "against the 359 needed. It widens each job-definition scope from two exact "
                "names to one prefix, which is a real if small widening and is a judgement "
                "for whoever owns that template rather than for whoever next reads this."
            ),
            findings=(
                RoleDriftFinding(
                    direction=DriftDirection.NARROWER,
                    element="inline policy 'run-admission-workflow' statement 3 resources",
                    detail=MISSING_QUEUE_RESOURCES,
                ),
                RoleDriftFinding(
                    direction=DriftDirection.NARROWER,
                    element="inline policy 'run-admission-workflow' statement 4 resources",
                    detail=MISSING_QUEUE_RESOURCES,
                ),
            ),
        ),
    )
    declared = declared_role_templates()
    for amendment in amendments:
        if amendment.role_name not in declared:
            raise PendingAmendmentError(
                f"pending amendment for {amendment.role_name}: no committed template "
                "declares that role, so nothing here will ever compare it and the record "
                "would never clear"
            )
    names = [amendment.role_name for amendment in amendments]
    if len(set(names)) != len(names):
        raise PendingAmendmentError(f"one role may carry one pending amendment; got {names}")
    return amendments


PENDING_AMENDMENTS: Final = pending_amendments()


def pending_for(role_name: str) -> PendingAmendment | None:
    """The amendment recorded for this role, or ``None`` if none is."""
    return next(
        (amendment for amendment in PENDING_AMENDMENTS if amendment.role_name == role_name), None
    )
