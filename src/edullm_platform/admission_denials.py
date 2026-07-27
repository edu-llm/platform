"""What the admission session must not be able to do, and what counts as being refused.

The admission role may do exactly one thing: start one Step Functions state machine, and
read that execution back. Everything else — launching compute, writing a lineage record
itself, starting some other state machine, stopping an admission that is already running,
minting a role — must be refused. That is a fact about a committed template, and a
template is not a deployment: the role is created from a laptop and is not redeployed by
CI, so a policy widened in the console leaves every test in this repository green. This
module is the other half, and it is the Phase 2 counterpart of
:mod:`edullm_platform.publisher_denials`: it attempts, under a real admission session, the
calls the role must not be able to make, and refuses to call anything but a genuine
authorization failure a denial.

**Read Phase 1 first.** The discipline is entirely Phase 1's and is not restated here:
a failure is not a denial; a refusal is not reliably worded, so it is recognised by its
error code and the operation it names rather than by its text; a permitted call must not
do anything; nothing captured is echoed; and one run reports every probe rather than
stopping at its first anomaly. :data:`~edullm_platform.publisher_denials.PROBE_SELECTION_LESSONS`
is the short version and it is worth reading before this module is changed.

What is *not* Phase 1's is below, because three things about this matrix are different in
ways that would each have produced a matrix that passed while proving nothing.

**The lineage bucket refuses some calls on its own behalf.** ``infra/lineage-bucket.yaml``
carries an explicit ``Deny`` on ``s3:PutObject`` for ``Principal: "*"`` whenever
``s3:if-none-match`` is absent. A ``PutObject`` probe that does not send the header is
therefore refused for every caller in the account, whatever the admission role holds — and
S3 attributes nothing, so the refusal arrives as the two words "Access Denied" and is
indistinguishable from the identity being refused. That probe would have reported the
bucket's own policy as proof that the role cannot write lineage records, on a run where
the role could write whatever it liked. The probe sends ``--if-none-match '*'``, which
satisfies the bucket's condition and leaves the identity policy as the only thing left
that can say no. This is the entry in the matrix that matters most: the lineage record is
a statement by the platform rather than by its caller precisely because only the state
machine can write one.

**EC2 has its own vocabulary for both answers.** A refused EC2 mutation is
``UnauthorizedOperation``, not ``AccessDenied``, so Phase 1's two-code set would have filed
the probe that stops this session touching EC2 as "failed for another reason" — a failure
that proves nothing, forever. And a *permitted* dry run also exits non-zero, with
``DryRunOperation``: the one probe whose success does not look like success. A matrix that
recognised a permitted call only by ``returncode == 0`` would have reported the worst
outcome in the matrix as an inconclusive probe. Both are per-probe facts about a service,
so :class:`AdmissionDenialProbe` carries the codes rather than the module holding one set.

**And a service that validates before it authorizes cannot be probed with an absent
resource.** This entry was ``ec2:RunInstances`` and could not be made to answer. Its first
live run came back ``attempt_failed_for_another_reason: InvalidAMIID.Malformed``, and
every attempt to fix it moved the complaint rather than removing it — format, then
``InvalidAMIID.NotFound``, then ``VPCIdNotSpecified``. What settled it is that IAM reports
``ec2:RunInstances`` as ``implicitDeny`` for this role while the role's own probe answered
``NotFound``: the lookup runs first, so no absent image can reach authorization.
``ec2:CreateKeyPair`` has no resource preconditions and answers from authorization alone.
The entry now claims something narrower and provable — this session is refused EC2
mutation — and the compute path the platform actually uses is covered by
``batch:SubmitJob``, which is conclusively denied. The matrix refusing to submit the run
on one inconclusive probe, rather than counting five denials as good enough, is the
behaviour that surfaced all of this.

**One probe is not inert, and says so.** S3 has no dry run, and the bucket has to be the
real one — a made-up name is answered ``NoSuchBucket`` before anybody is authorized, which
is Phase 1's first lesson. So a permitted ``PutObject`` writes an object, once: the header
the bucket policy forces means the second such write is answered 412 rather than
overwriting anything, and a 412 is read here as the permission being present for the same
reason a dry run that would have succeeded is. What is bounded is written down beside the
probe; what is not bounded is that the first object exists. It can be deleted afterwards:
the bucket carries Object Lock with no default retention rule, so a stray probe object is
a cleanup rather than a thirty-day tenant.

**Reasons, records and the classifier itself are Phase 1's.**
:class:`~edullm_platform.publisher_denials.PublisherDenialReason` names every way an
attempt can fail to establish a denial; not one of its members is about ECR, and a second
enum saying the same twelve things would be a second thing to keep in step. The record is
:class:`~edullm_platform.publisher_denials.AttemptedDenial` unchanged, so a Phase 2 refusal
completes into phase evidence through the same seam. :func:`require_denial` is spelled
again here, and only because its Phase 1 form hard-codes the code set that EC2 does not
use; the order of its checks, and what each one refuses, is the same list.

**No credentials were available when this was written.** Phase 1's lessons were bought
with live runs against the account. The two lessons below were read out of the committed
templates instead, which is cheaper and less certain: they say what the deployed policies
are written to do, not what the services were observed to do. The first live run of this
matrix should be read with that in mind.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import BeforeValidator, Field, ValidationError, model_validator

from edullm_platform.contracts.base import (
    SANDBOX_BUCKET_PREFIX,
    ContractModel,
    require_ordered_sequence,
)
from edullm_platform.publisher_denials import (
    ABSENT_JOB_DEFINITION,
    ABSENT_JOB_QUEUE,
    AUTHORIZATION_ERROR_CODES,
    DENIAL_PROBE_JOB_NAME,
    NOT_AUTHORIZED_PHRASE,
    RESOURCE_POLICY_PHRASE,
    UNASSUMABLE_TRUST_POLICY,
    AttemptedDenial,
    AwsApiError,
    DenialNotProvenError,
    DenialProbe,
    ProbeLesson,
    ProbeOutcome,
    PublisherDenialReason,
    assumed_role_identity,
    parse_aws_cli_error,
    record_denial,
    run_aws,
)

__all__ = [
    "ADMISSION_DENIED_ACTIONS",
    "ADMISSION_PROBE_LESSONS",
    "ADMISSION_ROLE_NAME",
    "ADMISSION_STATE_MACHINE_NAME",
    "EC2_AUTHORIZATION_ERROR_CODES",
    "EC2_PERMITTED_ERROR_CODES",
    "LINEAGE_BUCKET",
    "LINEAGE_PROBE_KEY",
    "LINEAGE_RECORD_PREFIXES",
    "S3_PERMITTED_ERROR_CODES",
    "AdmissionDenialMatrix",
    "AdmissionDenialMatrixRun",
    "AdmissionDenialProbe",
    "AdmissionSetupError",
    "AdmissionSetupReason",
    "AdmissionStateMachine",
    "admission_denial_probes",
    "attempt_admission_denials",
    "caller_identity",
    "mask_encoded_authorization_failure",
    "read_lineage_bucket",
    "read_state_machine_arn",
    "record_admission_denial",
    "require_denial",
]

#: The three deployed names this matrix is about, as the committed templates spell them.
#: They are names rather than ARNs for the reason every Phase 1 record is: a name
#: identifies the thing within one account and an ARN adds the account ID.
ADMISSION_ROLE_NAME: Final = "sbsandbox-intern-edullm-admission"
ADMISSION_STATE_MACHINE_NAME: Final = "sbsandbox-intern-edullm-admission"
LINEAGE_BUCKET: Final = "sbsandbox-intern-edullm-lineage"

#: The six actions attempted, in order. The role's inline policy allows
#: ``states:StartExecution`` on one state machine ARN and two read-only execution actions,
#: and these are the six ways that grant could have been wider than it reads.
ADMISSION_DENIED_ACTIONS: Final = (
    "batch:SubmitJob",
    "ec2:CreateKeyPair",
    "s3:PutObject",
    "states:StartExecution",
    "states:StopExecution",
    "iam:CreateRole",
)

#: What EC2 calls an authorization failure. Every other service in this matrix uses one
#: of Phase 1's two codes; EC2 answers ``UnauthorizedOperation`` and always has. Phase 1's
#: pair is kept alongside rather than replaced, because a service adding ``AccessDenied``
#: later should widen what is recognised rather than break the probe.
EC2_AUTHORIZATION_ERROR_CODES: Final = AUTHORIZATION_ERROR_CODES | {"UnauthorizedOperation"}

#: What EC2 says when a dry run was *allowed*: "Request would have succeeded, but DryRun
#: flag is set". It arrives as an error with a non-zero exit status, and it is the worst
#: outcome this matrix can meet — the role can mutate EC2 — so it is classified as the
#: permission being present rather than as a call that failed for some other reason.
#:
#: One code and not two, and that is worth pinning. A not-found answer looks like the same
#: thing and is not: it means the permission is present only if the service looks the
#: resource up *after* authorizing, and EC2 does not. See DENIAL_PROBE_KEY_PAIR_NAME.
EC2_PERMITTED_ERROR_CODES: Final = frozenset({"DryRunOperation"})

#: What S3 says when a conditional write was *allowed* and the key was already there.
#: ``If-None-Match: *`` is evaluated after the request is authorized, so a 412 is a caller
#: who may write meeting an object that already exists — which is the permission being
#: present, exactly as a dry run that would have succeeded is. It also means this probe
#: can create at most one object ever: after a first permitted write, every later run is
#: answered 412 and changes nothing.
S3_PERMITTED_ERROR_CODES: Final = frozenset({"PreconditionFailed"})

#: The key pair the EC2 probe names. Nothing creates it and ``--dry-run`` means nothing
#: would, so the name only ever appears in a refusal.
#:
#: ``ec2:CreateKeyPair`` is here because ``ec2:RunInstances`` could not be made to answer
#: the question. RunInstances validates its parameters ahead of authorization, in at least
#: three stages, each of which hides the answer behind a different complaint. Measured
#: against us-east-1 on 2026-07-27: ``ami-00000000000000000`` and ``ami-0123456789abcdef0``
#: are refused ``InvalidAMIID.Malformed`` on format alone; ``ami-00000000`` passes format
#: and is refused ``InvalidAMIID.NotFound``; and a real, current AMI gets past the lookup
#: only to be refused ``VPCIdNotSpecified``, because this account has no default VPC.
#:
#: The lookup runs before authorization, which the first live matrix proved rather than
#: inferred: IAM reports ``ec2:RunInstances`` as ``implicitDeny`` for the admission role,
#: and the role's probe still came back ``InvalidAMIID.NotFound`` instead of
#: ``UnauthorizedOperation``. Making it conclusive would mean hardcoding a real AMI and a
#: subnet borrowed from another team's VPC, and would still rest on an unmeasured guess
#: about where authorization sits among the remaining validations.
#:
#: CreateKeyPair has no resource preconditions at all, so authorization is the only gate:
#: verified ``DryRunOperation`` from a permitted identity, and ``implicitDeny`` for the
#: admission role. What the entry claims is therefore narrower and true -- this session is
#: refused EC2 mutation -- rather than wider and unprovable. The compute path this
#: platform actually uses is Batch, and ``batch:SubmitJob`` is proved separately above.
DENIAL_PROBE_KEY_PAIR_NAME: Final = "sbsandbox-intern-edullm-admission-denial-probe"

#: Appended to the admission state machine's ARN, which puts the probe beside the one
#: machine the role may start rather than somewhere unrelated, on a name nothing creates.
#: A permitted ``StartExecution`` against it therefore starts nothing.
ABSENT_STATE_MACHINE_SUFFIX: Final = "-denial-probe-absent"

#: Execution names nothing else mints. The stop probe names an execution of the *real*
#: admission machine, because the claim is that this role cannot abort an admission — but
#: it names one that was never started, so a permitted stop aborts nothing.
DENIAL_PROBE_EXECUTION_NAME: Final = "edullm-denial-probe-absent-execution"
DENIAL_PROBE_START_NAME: Final = "edullm-denial-probe-must-not-start"

#: Where the write probe points inside the real lineage bucket. Deliberately not under
#: ``intent/``, ``decision/`` or ``conflicts/``: those three prefixes are the lineage
#: record itself, and an object of this project's own making inside one of them would be
#: a forged statement by the platform. The key says what it is, so an operator who finds
#: it knows both what wrote it and that something is wrong.
LINEAGE_PROBE_PREFIX: Final = "denial-probe/"
LINEAGE_PROBE_KEY: Final = f"{LINEAGE_PROBE_PREFIX}this-object-must-never-exist.txt"

#: The prefixes the lineage record lives under, which no probe may name.
LINEAGE_RECORD_PREFIXES: Final = ("intent/", "decision/", "conflicts/")

#: The state machine ARN this matrix is aimed at, as the workflow passes it in. The
#: account ID is matched so the rest can be read and is never captured into a group: the
#: two ARNs the probes need are built by extending the ARN or by rewriting its resource
#: type, so the account travels through argv and never lands in a variable.
STATE_MACHINE_ARN_PATTERN: Final = re.compile(
    r"^arn:aws[a-z0-9-]*:states:(?P<region>[a-z]{2}(?:-[a-z]+)+-[0-9]):"
    r"[0-9]{12}:stateMachine:(?P<name>[A-Za-z0-9_-]{1,80})$"
)

#: What a bucket name may be, and it must be one of this project's. Pointing the write
#: probe at somebody else's bucket in the shared sandbox would read a refusal out of their
#: policy and report it as a fact about this role.
BUCKET_NAME_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")

#: EC2 appends an encoded authorization failure message to a refusal. It is an opaque blob
#: that only ``sts:DecodeAuthorizationMessage`` can read — which this session cannot call —
#: and it is not a credential, but it is a long base64 run and ``scan_for_secrets`` cannot
#: tell the difference, so a message carrying one would be withheld whole and the refusal
#: would go unrecorded. It is masked by the label that introduces it rather than by shape,
#: exactly as ``tools/probe_conditional_write.py`` masks S3's extended request ID, so
#: nothing else that merely looks like a credential is hidden by this.
ENCODED_AUTHORIZATION_FAILURE: Final = re.compile(
    r"(?i)(?P<label>encoded authorization failure message)"
    r"(?P<gap>\s*[:=]\s*)"
    r"(?P<value>[A-Za-z0-9/+=_-]{20,})"
)
ENCODED_AUTHORIZATION_FAILURE_PLACEHOLDER: Final = "<encoded-authorization-failure-message>"


#: What choosing a probe has cost in Phase 2. Phase 1's list comes first and still
#: applies; these two are what reading the Phase 2 templates added to it. Neither was
#: learned from a run, because there are no credentials in the environment this was
#: written in, and a rule read off a template is a claim about what should happen.
ADMISSION_PROBE_LESSONS: Final[tuple[ProbeLesson, ...]] = (
    ProbeLesson(
        rule=(
            "A probe whose target carries a resource policy can be refused by that policy "
            "instead of by the identity, and S3 does not say which one refused it."
        ),
        learned_from=(
            "Reading infra/lineage-bucket.yaml while writing the s3:PutObject probe. The "
            "bucket denies s3:PutObject to Principal '*' whenever s3:if-none-match is "
            "absent, so an unconditional write is refused for every caller in the account "
            "no matter what the admission role holds -- and the refusal is the two words "
            "'Access Denied', with nothing in it that names a policy."
        ),
        detail=(
            "The direction of the failure is what makes it serious. The probe would have "
            "answered AccessDenied on every run, including every run on which the role had "
            "been widened to write lineage records, so the matrix would have reported the "
            "most important entry in it as proved at exactly the moment it stopped being "
            "true. Phase 1's resource-policy check does not catch this: it reads the "
            "message, and there is no message to read.\n"
            "\n"
            "The fix is to shape the call so the resource policy has nothing to say about "
            "it. Sending --if-none-match '*' satisfies the bucket's condition, so the Deny "
            "does not apply and the identity policy is the only thing left that can refuse "
            "the call. It also means the write is conditional, so it can never overwrite an "
            "object that is already there.\n"
            "\n"
            "The general rule is to read the target's own policy before pointing a probe at "
            "it. Phase 1 could state that none of its five targets carried one; this matrix "
            "cannot, because the claim it makes is about this bucket and no other target "
            "would prove it."
        ),
    ),
    ProbeLesson(
        rule=(
            "A service words both answers in its own vocabulary, so the codes that mean "
            "'refused' and 'allowed' belong to the probe rather than to the matrix."
        ),
        learned_from=(
            "Writing the EC2 probe against Phase 1's classifier. EC2 answers a refusal "
            "with UnauthorizedOperation rather than AccessDenied, and answers a permitted "
            "dry run with DryRunOperation -- an error, with a non-zero exit status, that "
            "means the role can mutate EC2."
        ),
        detail=(
            "Two failures, in opposite directions, from one assumption that every service "
            "spells these two answers the way IAM and S3 do. Read with Phase 1's code set, "
            "a genuine EC2 refusal is 'failed for another reason', so the probe could never "
            "prove anything and the matrix would have been quietly one entry short. Read "
            "with 'permitted means returncode == 0', a role that can launch GPU instances "
            "reports as an inconclusive probe rather than as the emergency it is.\n"
            "\n"
            "Making a probe inert is what created the second half: --dry-run is the only "
            "way to ask EC2 this question without a permitted call starting an instance, "
            "and it is precisely what turns success into an error. A technique that makes a "
            "probe harmless will often change what its answers look like, and both have to "
            "be read together."
        ),
    ),
    ProbeLesson(
        rule="A probe that cannot be made inert is written down as one, not quietly shipped.",
        learned_from=(
            "The s3:PutObject probe. S3 has no dry run, the bucket must be the real one -- "
            "Phase 1's first lesson is that an absent bucket is answered NoSuchBucket "
            "before anybody is authorized -- and there is no form of PutObject that reaches "
            "authorization and cannot write."
        ),
        detail=(
            "What is bounded: the body is empty, the key is under denial-probe/ and never "
            "under intent/, decision/ or conflicts/, so a permitted write cannot forge or "
            "overwrite a lineage record; --if-none-match '*' means it cannot overwrite "
            "anything at all; and because that header makes every later write of the same "
            "key a 412, a permitted probe can create one object and never a second. What "
            "is not bounded: that first object exists. It can be removed, because the "
            "bucket enables Object Lock but sets no default retention rule, so nothing "
            "holds a stray probe object beyond somebody noticing it.\n"
            "\n"
            "Two alternatives were considered and rejected. A deliberately wrong "
            "--content-md5 would make a permitted write fail after authorization, but "
            "whether S3 validates the digest before or after it authorizes cannot be "
            "settled without a live run, and getting it wrong makes the most important "
            "entry in the matrix permanently unprovable. Writing to a bucket this project "
            "creates for the purpose proves the role cannot write to that bucket, which is "
            "not the claim. Both trade a bounded, visible cost for an unbounded, invisible "
            "one."
        ),
    ),
)


class AdmissionSetupReason(StrEnum):
    """Why a run could not be attempted at all, as distinct from what it found.

    These are the failures that leave no probe worth making: an argument that does not
    describe the deployed admission machine, or a session that no record could describe.
    They are the tool's exit-2 conditions, because none of them is a finding about how
    wide the role is. Every value that Phase 1 also has is spelled the same way, so an
    operator reading a runner log does not have to learn two vocabularies.
    """

    AWS_CLI_UNAVAILABLE = "aws_cli_unavailable"
    CALLER_IDENTITY_UNREADABLE = "caller_identity_unreadable"
    CALLER_IS_NOT_AN_ASSUMED_ROLE = "caller_is_not_an_assumed_role"
    CALLER_IS_NOT_THE_ADMISSION_ROLE = "caller_is_not_the_admission_role"
    STATE_MACHINE_ARN_UNUSABLE = "state_machine_arn_unusable"
    STATE_MACHINE_ARN_NAMES_ANOTHER_MACHINE = "state_machine_arn_names_another_machine"
    STATE_MACHINE_ARN_IS_IN_ANOTHER_REGION = "state_machine_arn_is_in_another_region"
    LINEAGE_BUCKET_UNUSABLE = "lineage_bucket_unusable"


class AdmissionSetupError(RuntimeError):
    """The run could not be set up, so nothing was attempted.

    Carries the reason and nothing else. The values it is raised with are argument text
    and identity text — a state machine ARN holds the account ID, and a role name in this
    shared sandbox may be somebody's own — and the string form of this error reaches a
    world-readable runner log.
    """

    def __init__(self, reason: AdmissionSetupReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True)
class AdmissionDenialProbe(DenialProbe):
    """One call the admission session must not be allowed to make.

    A :class:`~edullm_platform.publisher_denials.DenialProbe` that also carries the two
    code sets its service uses, because they are not the same for every service and
    getting either wrong produces a matrix that reports the wrong thing rather than one
    that fails. ``authorization_error_codes`` is what a refusal of this call looks like;
    ``permitted_error_codes`` is what an *allowed* call looks like when the technique that
    made the probe inert also made success arrive as an error.

    The two must not overlap: a code in both would leave the order of the checks deciding
    whether the role is narrow or wide open.
    """

    authorization_error_codes: frozenset[str] = AUTHORIZATION_ERROR_CODES
    permitted_error_codes: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.authorization_error_codes & self.permitted_error_codes:
            raise ValueError("one error code cannot mean both refused and allowed")


@dataclass(frozen=True)
class AdmissionStateMachine:
    """The one state machine the admission role may start, as a validated ARN.

    Held whole. The two ARNs the matrix needs are derived from it by extending the name or
    by rewriting the resource type, so this class never has to take the account ID out.
    """

    arn: str
    region: str
    name: str

    @property
    def another_state_machine_arn(self) -> str:
        """A state machine ARN in this account that is not the one the grant names."""
        return f"{self.arn}{ABSENT_STATE_MACHINE_SUFFIX}"

    def execution_arn(self, execution_name: str) -> str:
        """An execution ARN under this machine. ``StopExecution`` names one of these."""
        return f"{self.arn.replace(':stateMachine:', ':execution:')}:{execution_name}"


def read_state_machine_arn(arn: str, *, region: str) -> AdmissionStateMachine:
    """Check the ARN describes the deployed admission machine in the region being probed.

    Three separate refusals rather than one. An ARN that is not a state machine ARN is a
    typo; one that names a different machine would make the fourth probe compare the wrong
    two names and the fifth aim at another machine's executions; and one in another region
    would send every call to a resource that is not there, which is answered by absence
    rather than by authorization. All three are setup failures: none of them says anything
    about how wide the role is.
    """
    match = STATE_MACHINE_ARN_PATTERN.fullmatch(arn.strip())
    if match is None:
        raise AdmissionSetupError(AdmissionSetupReason.STATE_MACHINE_ARN_UNUSABLE)
    if match.group("name") != ADMISSION_STATE_MACHINE_NAME:
        raise AdmissionSetupError(AdmissionSetupReason.STATE_MACHINE_ARN_NAMES_ANOTHER_MACHINE)
    if match.group("region") != region:
        raise AdmissionSetupError(AdmissionSetupReason.STATE_MACHINE_ARN_IS_IN_ANOTHER_REGION)
    return AdmissionStateMachine(
        arn=arn.strip(), region=match.group("region"), name=match.group("name")
    )


def read_lineage_bucket(bucket: str) -> str:
    """Check the write probe is aimed at a bucket this project owns.

    A name that is not a bucket name, or a bucket belonging to another team in the shared
    sandbox, cannot prove anything about this role: the first is answered ``NoSuchBucket``
    and the second is answered by somebody else's policy.
    """
    candidate = bucket.strip()
    if BUCKET_NAME_PATTERN.fullmatch(candidate) is None:
        raise AdmissionSetupError(AdmissionSetupReason.LINEAGE_BUCKET_UNUSABLE)
    if not candidate.startswith(SANDBOX_BUCKET_PREFIX):
        raise AdmissionSetupError(AdmissionSetupReason.LINEAGE_BUCKET_UNUSABLE)
    return candidate


def admission_denial_probes(
    *,
    region: str,
    state_machine: AdmissionStateMachine,
    lineage_bucket: str,
    role_name: str,
) -> tuple[AdmissionDenialProbe, ...]:
    """The matrix, aimed at this account's admission machine and this session's role.

    ``role_name`` is the caller's own role, and it is the target of the create-role probe
    for Phase 1's reason: a name IAM already holds cannot be created twice.
    """
    return (
        # Absent queue, absent job definition: Batch authorizes before it looks either up,
        # so a refusal is observable and a permitted call submits nothing. Both names are
        # Phase 1's, because they name the same absent things.
        AdmissionDenialProbe(
            action="batch:SubmitJob",
            operation="SubmitJob",
            event_source="batch.amazonaws.com",
            resource_name=ABSENT_JOB_QUEUE,
            arguments=(
                "batch",
                "submit-job",
                "--region",
                region,
                "--job-name",
                DENIAL_PROBE_JOB_NAME,
                "--job-queue",
                ABSENT_JOB_QUEUE,
                "--job-definition",
                ABSENT_JOB_DEFINITION,
            ),
        ),
        # --dry-run comes first in the argument list so that a reader checking whether
        # this call could start an instance sees the answer before anything else. It is
        # what makes the probe inert, and it is also why a permitted call arrives here as
        # an error code rather than as a zero exit status.
        AdmissionDenialProbe(
            action="ec2:CreateKeyPair",
            operation="CreateKeyPair",
            event_source="ec2.amazonaws.com",
            resource_name=DENIAL_PROBE_KEY_PAIR_NAME,
            arguments=(
                "ec2",
                "create-key-pair",
                "--dry-run",
                "--region",
                region,
                "--key-name",
                DENIAL_PROBE_KEY_PAIR_NAME,
            ),
            authorization_error_codes=EC2_AUTHORIZATION_ERROR_CODES,
            permitted_error_codes=EC2_PERMITTED_ERROR_CODES,
        ),
        # The real bucket, because a made-up one is answered NoSuchBucket; --if-none-match
        # because without it the bucket's own policy refuses everybody and the answer says
        # nothing about this role; no --body, so a permitted write is a zero-byte object
        # under a key nothing reads. This is the one probe that is not inert, and the
        # conditional header is also what bounds it: it can create an object once and
        # never overwrite one. A CLI too old to know --if-none-match answers with a usage
        # error rather than an API error, which this classifier reads as proving nothing.
        AdmissionDenialProbe(
            action="s3:PutObject",
            operation="PutObject",
            event_source="s3.amazonaws.com",
            resource_name=f"{lineage_bucket}/{LINEAGE_PROBE_KEY}",
            arguments=(
                "s3api",
                "put-object",
                "--region",
                region,
                "--bucket",
                lineage_bucket,
                "--key",
                LINEAGE_PROBE_KEY,
                "--if-none-match",
                "*",
            ),
            permitted_error_codes=S3_PERMITTED_ERROR_CODES,
        ),
        # The grant is one state machine ARN, not the service. This lands one name beside
        # it, on a machine nothing creates, so a permitted call starts nothing.
        AdmissionDenialProbe(
            action="states:StartExecution",
            operation="StartExecution",
            event_source="states.amazonaws.com",
            resource_name=f"{state_machine.name}{ABSENT_STATE_MACHINE_SUFFIX}",
            arguments=(
                "stepfunctions",
                "start-execution",
                "--region",
                region,
                "--state-machine-arn",
                state_machine.another_state_machine_arn,
                "--name",
                DENIAL_PROBE_START_NAME,
            ),
        ),
        # An execution of the real admission machine, because the claim is that a
        # submitter cannot abort an admission that is already recording its decision. The
        # execution named was never started, so a permitted stop stops nothing.
        AdmissionDenialProbe(
            action="states:StopExecution",
            operation="StopExecution",
            event_source="states.amazonaws.com",
            resource_name=f"{state_machine.name}:{DENIAL_PROBE_EXECUTION_NAME}",
            arguments=(
                "stepfunctions",
                "stop-execution",
                "--region",
                region,
                "--execution-arn",
                state_machine.execution_arn(DENIAL_PROBE_EXECUTION_NAME),
            ),
        ),
        AdmissionDenialProbe(
            action="iam:CreateRole",
            operation="CreateRole",
            event_source="iam.amazonaws.com",
            resource_name=role_name,
            arguments=(
                "iam",
                "create-role",
                "--region",
                region,
                "--role-name",
                role_name,
                "--assume-role-policy-document",
                UNASSUMABLE_TRUST_POLICY,
            ),
        ),
    )


def _names_another_action(message: str, action: str) -> bool:
    """Whether a message that spelled out an action spelled out a different one.

    Phase 1's helper, spelled again because it is private there and Phase 1's acceptance
    criteria cite that module as it stands. The two must agree, and the cases that pin
    this one are the cases that pin that one.

    EC2's short form, "You are not authorized to perform this operation.", does not
    contain the phrase — which ends in a colon — so it names no action and is read on the
    code and the operation, exactly as S3's "Access Denied" is.
    """
    if NOT_AUTHORIZED_PHRASE not in message:
        return False
    # The trailing guard is why this is a pattern rather than a substring: a refusal of
    # states:StartExecutionAndMore contains the whole of states:StartExecution.
    pattern = rf"{re.escape(NOT_AUTHORIZED_PHRASE)}\s+{re.escape(action)}(?![A-Za-z0-9])"
    return re.search(pattern, message) is None


def require_denial(probe: AdmissionDenialProbe, *, returncode: int, stderr: str) -> AwsApiError:
    """Return the refusal this attempt met, or raise because it met something else.

    Phase 1's order, with one check inserted. A call that succeeded is the worst case and
    is checked first; then that there is an AWS error at all; then that it is an error for
    the call this probe made. Then — this is the new one — that the error is not this
    service's way of saying the call was *allowed*: a dry run EC2 would have let through,
    a conditional write S3 authorized and then found an object under. Both must be
    reported as the permission being present rather than as a probe that established
    nothing, which is what a matrix that read only the exit status would do, and what a
    matrix that read the code as merely unrecognised would also do. Then that the code is
    an authorization failure
    rather than the service declining to process the request: this is the check that stops
    a typo in a resource name being scored as proof of a denial, because a NoSuchBucket, a
    validation error and a throttle are all what a *permitted* call looks like when it is
    pointed at something that is not there. The last two read the message where the service
    wrote one: it must not name a different action, and it must not attribute the refusal
    to somebody's resource policy.
    """
    if returncode == 0:
        raise DenialNotProvenError(PublisherDenialReason.ATTEMPT_PERMITTED, action=probe.action)
    error = parse_aws_cli_error(stderr)
    if error is None:
        raise DenialNotProvenError(
            PublisherDenialReason.ATTEMPT_FAILED_WITHOUT_AN_AWS_ERROR,
            action=probe.action,
        )
    if error.operation != probe.operation:
        raise DenialNotProvenError(
            PublisherDenialReason.ATTEMPT_CALLED_ANOTHER_OPERATION,
            action=probe.action,
            error_code=error.code,
        )
    if error.code in probe.permitted_error_codes:
        raise DenialNotProvenError(
            PublisherDenialReason.ATTEMPT_PERMITTED,
            action=probe.action,
            error_code=error.code,
        )
    if error.code not in probe.authorization_error_codes:
        raise DenialNotProvenError(
            PublisherDenialReason.ATTEMPT_FAILED_FOR_ANOTHER_REASON,
            action=probe.action,
            error_code=error.code,
        )
    if _names_another_action(error.message, probe.action):
        raise DenialNotProvenError(
            PublisherDenialReason.DENIAL_NAMED_ANOTHER_ACTION,
            action=probe.action,
            error_code=error.code,
        )
    if RESOURCE_POLICY_PHRASE in error.message:
        raise DenialNotProvenError(
            PublisherDenialReason.DENIAL_CAME_FROM_A_RESOURCE_POLICY,
            action=probe.action,
            error_code=error.code,
        )
    return error


def mask_encoded_authorization_failure(message: str) -> str:
    """Replace EC2's encoded authorization failure blob with a placeholder."""
    return ENCODED_AUTHORIZATION_FAILURE.sub(
        lambda match: (
            f"{match['label']}{match['gap']}{ENCODED_AUTHORIZATION_FAILURE_PLACEHOLDER}"
        ),
        message,
    )


def record_admission_denial(
    probe: AdmissionDenialProbe,
    error: AwsApiError,
    *,
    region: str,
    role_name: str,
    session_name: str,
    attempted_at: datetime,
) -> AttemptedDenial:
    """Write down one refusal in the terms the Phase 1 evidence record already uses.

    Only one thing happens here that Phase 1 does not do, and it happens before Phase 1's
    masking rather than instead of it: EC2's encoded authorization failure message is
    replaced by a placeholder. It is not a credential, but the secret scan cannot tell a
    long base64 blob from one, so leaving it in would have the whole refusal withheld as
    though it carried a token. Everything after that — account IDs, then content digests,
    then a refusal to launder text holding a real credential — is
    :func:`~edullm_platform.publisher_denials.record_denial` unchanged.
    """
    masked = replace(error, message=mask_encoded_authorization_failure(error.message))
    return record_denial(
        probe,
        masked,
        region=region,
        role_name=role_name,
        session_name=session_name,
        attempted_at=attempted_at,
    )


class AdmissionDenialMatrix(ContractModel):
    """Every action the admission session was refused in one run.

    The attempts have to be the whole matrix, in order, for Phase 1's reason: a run that
    proved five of the six refusals proved five of them, and a file able to hold the five
    would be read later as though it had proved all six.
    """

    schema_version: Literal[1]
    attempts: Annotated[tuple[AttemptedDenial, ...], BeforeValidator(require_ordered_sequence)] = (
        Field(min_length=len(ADMISSION_DENIED_ACTIONS), strict=False)
    )

    @model_validator(mode="after")
    def validate_every_action_in_the_matrix_was_attempted(self) -> Self:
        attempted = tuple(attempt.attempted_action for attempt in self.attempts)
        if attempted != ADMISSION_DENIED_ACTIONS:
            raise ValueError("the record must hold one denial per matrix action, in matrix order")
        return self


@dataclass(frozen=True)
class AdmissionDenialMatrixRun:
    """Every probe's outcome from one run, whether or not the run proved the matrix.

    The same three members as Phase 1's :class:`DenialMatrixRun` over a different record.
    It is spelled again rather than subclassed because ``matrix()`` returns a different
    contract, and an override that returned something the base class does not produce
    would be a lie about the base class for the sake of saving ten lines.
    """

    outcomes: tuple[ProbeOutcome, ...]

    @property
    def proven(self) -> bool:
        """Whether every action in the matrix came back as a refusal."""
        return all(outcome.denial is not None for outcome in self.outcomes)

    @property
    def summary(self) -> tuple[str, ...]:
        """One line per action, in matrix order, safe for a world-readable log."""
        return tuple(str(outcome) for outcome in self.outcomes)

    def matrix(self) -> AdmissionDenialMatrix:
        """The record of the run, which exists only if every action was refused."""
        denials = tuple(outcome.denial for outcome in self.outcomes if outcome.denial is not None)
        if len(denials) != len(self.outcomes):
            raise ValueError("a run that did not refuse every action has no matrix to write")
        return AdmissionDenialMatrix(schema_version=1, attempts=denials)


def caller_identity(*, region: str) -> tuple[str, str]:
    """Ask STS who this session is, and refuse to proceed as anybody else.

    ``GetCallerIdentity`` requires no permission and cannot be denied by a policy, so it
    is the one call in this job that says nothing about how wide the role is.

    Phase 1 read the identity to fill in the record. This also checks it, because the
    whole matrix is a claim about one named role: run under any other session — the
    publisher role, a person's own role in the shared sandbox — every probe would be
    refused and the run would report the admission role as narrow without having tested
    it once. The offending role name is deliberately not printed: per-person roles in this
    account carry personal names, and this reason reaches a public runner log.
    """
    try:
        completed = run_aws(
            ("sts", "get-caller-identity", "--region", region, "--query", "Arn", "--output", "text")
        )
    except DenialNotProvenError as exc:
        if exc.reason is PublisherDenialReason.AWS_CLI_UNAVAILABLE:
            raise AdmissionSetupError(AdmissionSetupReason.AWS_CLI_UNAVAILABLE) from exc
        raise AdmissionSetupError(AdmissionSetupReason.CALLER_IDENTITY_UNREADABLE) from exc
    if completed.returncode != 0:
        raise AdmissionSetupError(AdmissionSetupReason.CALLER_IDENTITY_UNREADABLE)
    try:
        role_name, session_name = assumed_role_identity(completed.stdout)
    except DenialNotProvenError as exc:
        raise AdmissionSetupError(AdmissionSetupReason.CALLER_IS_NOT_AN_ASSUMED_ROLE) from exc
    if role_name != ADMISSION_ROLE_NAME:
        raise AdmissionSetupError(AdmissionSetupReason.CALLER_IS_NOT_THE_ADMISSION_ROLE)
    return role_name, session_name


def attempt_admission_denials(
    *,
    region: str,
    state_machine_arn: str,
    lineage_bucket: str,
) -> AdmissionDenialMatrixRun:
    """Attempt every action in the matrix and report what each one answered.

    The arguments are checked before the session is, and the session before any probe is
    made, so a typo costs nothing and a run under the wrong identity attempts nothing.
    After that, nothing about one probe stops another: an action that was permitted, or
    that failed for a reason that proves nothing, is one outcome among six rather than the
    end of the run. Reaching this account costs a workflow run under a real OIDC session,
    and a matrix that stopped at its first surprise would turn one run into one fact.
    """
    machine = read_state_machine_arn(state_machine_arn, region=region)
    bucket = read_lineage_bucket(lineage_bucket)
    role_name, session_name = caller_identity(region=region)
    probes = admission_denial_probes(
        region=region,
        state_machine=machine,
        lineage_bucket=bucket,
        role_name=role_name,
    )
    outcomes = [
        _attempt(probe, region=region, role_name=role_name, session_name=session_name)
        for probe in probes
    ]
    return AdmissionDenialMatrixRun(outcomes=tuple(outcomes))


def _attempt(
    probe: AdmissionDenialProbe,
    *,
    region: str,
    role_name: str,
    session_name: str,
) -> ProbeOutcome:
    """Make one call and say what it established, without letting it stop the others."""
    attempted_at = datetime.now(tz=UTC)
    try:
        completed = run_aws(probe.arguments, action=probe.action)
        error = require_denial(probe, returncode=completed.returncode, stderr=completed.stderr)
        denial = record_admission_denial(
            probe,
            error,
            region=region,
            role_name=role_name,
            session_name=session_name,
            attempted_at=attempted_at,
        )
    except DenialNotProvenError as unproven:
        return ProbeOutcome(action=probe.action, denial=None, unproven=unproven)
    except ValidationError:
        # A refusal the contract will not hold is a bug here rather than a finding about
        # the role, and it still stops the submission: a denial that cannot be written
        # down cannot be evidence. The message stays out of the reason, as everywhere.
        return ProbeOutcome(
            action=probe.action,
            denial=None,
            unproven=DenialNotProvenError(
                PublisherDenialReason.DENIAL_COULD_NOT_BE_RECORDED,
                action=probe.action,
            ),
        )
    return ProbeOutcome(action=probe.action, denial=denial, unproven=None)
