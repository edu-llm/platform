"""What the publisher session must not be able to do, and what counts as being refused.

The publisher role is meant to hold nine ECR actions on one repository and nothing else.
That is a fact about a committed template; the role was deployed once from a laptop and
is not redeployed by CI, so a policy widened in the console leaves every test in this
repository green. This module is the other half: it attempts, under a real publisher
session, the calls the role must not be able to make, and refuses to call anything but a
genuine authorization failure a denial.

Four constraints shape everything here, and the first live run of this matrix is why
three of them are worded the way they are.

**A failure is not a denial.** A not-found, a malformed parameter, a throttle, an expired
token and a network timeout are all failures, and every one of them is also what a
*permitted* call looks like when it is pointed at a resource that is not there. Counting
any of them as a refusal would let the widening this matrix exists to catch pass as proof
that it did not happen.

**A refusal is not reliably worded.** The matrix first required the error text to read
``is not authorized to perform: <action>``. IAM and Batch do say that; S3 does not. Its
error table gives ``AccessDenied`` the description "Access Denied" and that is the whole
message, so the one service the criterion is most about was the one that could never
satisfy the test. What every service does agree on is the error *code* and the *operation*
it names, so that pair is what a denial rests on here. The wording is still read when
there is any: a message that names an action must name this one, and a message that
attributes the refusal to a resource-based policy is refused whatever else it says, since
somebody else's policy says nothing about how wide this role is. The probes are chosen so
that the second check has nothing to miss — none of the five targets can carry a resource
policy of its own — but a terse refusal is accepted on the code, and that limit is real.

**A permitted call must not do anything.** Each probe either targets a resource that is
not there, so a permitted call fails on the resource, or is inert for its own reason:
``s3:ListAllMyBuckets`` only reads and names nothing; ``iam:CreateRole`` names a role that
already exists, so a permitted call collides rather than creating; ``ecr:DeleteRepository``
names a repository beside the registered one, because a permitted delete of that would
take the published images with it. That requirement is in direct conflict with the one
above for anything aimed at an S3 bucket: S3 routes a request to a bucket before it
authorizes it, so a bucket chosen not to exist answers ``NoSuchBucket`` and the refusal is
never observable. The first live run reported exactly that. ``ListBuckets`` is the way out
— it names no bucket, so nothing about it can be absent — at the price described below.

**Nothing captured is echoed.** An AWS denial message names the account, the role and
usually the resource ARN. It reaches a record only through ``redact_aws_account_ids``,
and the contract below is what refuses it if this module forgets. The reason tokens this
module raises carry the action and the AWS error code and nothing else, so a failure is
diagnosable from a world-readable runner log without the log becoming the leak.

What the S3 probe proves, and what it does not: a session refused ``ListBuckets`` holds no
account-wide S3 permission, which is what a policy widened to include S3 would most likely
grant. It is weaker than the criterion's "cannot read datasets", because a policy granting
only ``s3:GetObject`` on one bucket would be refused ``ListBuckets`` just the same. Closing
that difference needs an object read that reaches authorization, which needs a bucket this
project owns and a zero-byte object in it that exists — an absent key in an existing bucket
is no good either, since a caller without ``s3:ListBucket`` is answered 403 for a missing
object and the refusal would be ambiguous. No such bucket is deployed yet, and pointing
the probe at another team's bucket in the shared account would be reading a refusal from
their policy rather than ours.

What is deliberately not here is the CloudTrail identity of each refusal.
:class:`~edullm_platform.phase1_evidence.DenialEvidence` requires an ``event_id``, and
the publisher session cannot read CloudTrail — the whole point of it is that it can read
almost nothing. Minting an event ID would put a fact in an evidence record that nothing
established, so an attempt is recorded as :class:`AttemptedDenial`, which carries every
other field the evidence needs, and :func:`denial_evidence` completes the record once a
capture with CloudTrail credentials has looked the event up. All five calls are management
events, so all five are in the trail without data events being switched on.

**One run says everything that is wrong.** The matrix stopped at its first anomaly once,
which taught us about S3 and nothing about the other four probes, and every fix after that
would have cost another live run to learn one more thing. :func:`attempt_denials` attempts
every action, records every outcome, and leaves the deciding to its caller.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import BeforeValidator, Field, ValidationError, model_validator

from edullm_platform.contracts.base import ContractModel, require_ordered_sequence
from edullm_platform.evidence import (
    SecretFreeStr,
    redact_aws_account_ids,
    redact_content_digests,
)
from edullm_platform.phase1_evidence import (
    AWS_ERROR_CODE_PATTERN,
    AWS_SERVICE_PRINCIPAL_PATTERN,
    IAM_ACTION_PATTERN,
    AwsRegion,
    DenialEvidence,
    EvidenceInstant,
    IamRoleName,
    IamSessionName,
)

__all__ = [
    "EVIDENCE_ONLY_FIELDS",
    "PUBLISHER_DENIED_ACTIONS",
    "AttemptedDenial",
    "AwsApiError",
    "DenialMatrixRun",
    "DenialNotProvenError",
    "DenialProbe",
    "ProbeOutcome",
    "PublisherDenialMatrix",
    "PublisherDenialReason",
    "assumed_role_identity",
    "attempt_denials",
    "denial_evidence",
    "denial_probes",
    "record_denial",
    "require_denial",
    "run_aws",
]

#: How long one probe may take before the answer stops being worth waiting for. A hung
#: call is not a denial, and the job's own timeout is far too coarse to say so.
AWS_CALL_TIMEOUT_SECONDS: Final = 60

#: The two codes the services in this matrix return for an authorization failure. IAM and
#: S3 answer with ``AccessDenied``; Batch and ECR answer with ``AccessDeniedException``.
#: This is the whole of what a denial is recognised by, together with the operation, so
#: the list stays short: an unrecognised code stops the run, which is the direction an
#: unknown answer should fail in. Every code an allowed call can come back with — a
#: not-found, a validation error, a throttle, an expired token, a server fault — is
#: outside it and therefore cannot be filed as a refusal.
AUTHORIZATION_ERROR_CODES: Final = frozenset({"AccessDenied", "AccessDeniedException"})

#: Resource names nothing in this account creates, so a permitted call fails on the
#: resource rather than doing something. The ECR probe derives its name from the
#: registered repository instead, so the call lands beside the one repository the role
#: may touch rather than somewhere unrelated.
ABSENT_JOB_QUEUE: Final = "edullm-denial-probe-absent-queue"
ABSENT_JOB_DEFINITION: Final = "edullm-denial-probe-absent-job-definition"
ABSENT_COMPUTE_ENVIRONMENT: Final = "edullm-denial-probe-absent-compute-environment"
ABSENT_REPOSITORY_SUFFIX: Final = "-denial-probe-absent"
DENIAL_PROBE_JOB_NAME: Final = "edullm-denial-probe"

#: A trust policy that admits nobody. The create-role probe cannot create anything —
#: the name it asks for is already taken — and this is the second reason it would not
#: matter if it could. It is well-formed on purpose: a document IAM rejects might be
#: rejected before the request is authorized, which would turn a real refusal into a
#: malformed-parameter failure and leave that probe unproven.
UNASSUMABLE_TRUST_POLICY: Final = json.dumps(
    {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Deny", "Principal": {"AWS": "*"}, "Action": "sts:AssumeRole"}],
    },
    separators=(",", ":"),
)

#: The five actions attempted, in order. The first four are the master plan's check —
#: the publisher role cannot submit jobs, read datasets, alter IAM, or modify Batch. The
#: fifth is the one that check does not name: the role publishes images, and a role that
#: could delete the repository holding them would be able to destroy what it published.
PUBLISHER_DENIED_ACTIONS: Final = (
    "batch:SubmitJob",
    "s3:ListAllMyBuckets",
    "iam:CreateRole",
    "batch:UpdateComputeEnvironment",
    "ecr:DeleteRepository",
)

#: What :class:`~edullm_platform.phase1_evidence.DenialEvidence` carries that one attempt
#: cannot supply: the evidence envelope, and the CloudTrail identity of the record.
EVIDENCE_ONLY_FIELDS: Final = frozenset(
    {"source", "environment", "status", "observed_at", "event_id"}
)

#: The AWS CLI's rendering of a service error. The code and the operation are patterned
#: rather than captured loosely, because both are printed in the reason this module
#: raises and a log line is not the place to discover that the text was something else.
#: The optional parenthetical is the CLI's retry annotation.
AWS_CLI_ERROR_PATTERN: Final = re.compile(
    r"An error occurred \((?P<code>[A-Za-z][A-Za-z0-9.]{0,127})\) "
    r"when calling the (?P<operation>[A-Za-z][A-Za-z0-9]{0,127}) operation"
    r"(?: \([^)]*\))?: (?P<message>.+)",
    re.DOTALL,
)

#: An assumed-role caller, as STS spells it. The account ID is matched so the rest can be
#: read, and is never carried out of this function.
ASSUMED_ROLE_ARN_PATTERN: Final = re.compile(
    r"^arn:aws[a-z0-9-]*:sts::[0-9]{12}:assumed-role/(?P<role>[^/]{1,64})/(?P<session>[^/]{2,64})$"
)

#: How a service that spells its refusal out begins. Its absence is not suspicious — S3
#: never says it — but its presence means the message named an action, and a message that
#: named an action is held to naming this one.
NOT_AUTHORIZED_PHRASE: Final = "is not authorized to perform:"

#: The refusal has to be the identity's own. A resource policy can refuse a call the
#: identity was perfectly well allowed to make, and reading that as a denial would report
#: the role as narrow when it had been widened.
RESOURCE_POLICY_PHRASE: Final = "resource-based policy"


class PublisherDenialReason(StrEnum):
    """Why a run cannot claim the publisher role is still narrow."""

    ATTEMPT_PERMITTED = "attempt_permitted"
    ATTEMPT_FAILED_WITHOUT_AN_AWS_ERROR = "attempt_failed_without_an_aws_error"
    ATTEMPT_FAILED_FOR_ANOTHER_REASON = "attempt_failed_for_another_reason"
    ATTEMPT_CALLED_ANOTHER_OPERATION = "attempt_called_another_operation"
    ATTEMPT_TIMED_OUT = "attempt_timed_out"
    AWS_CLI_UNAVAILABLE = "aws_cli_unavailable"
    DENIAL_NAMED_ANOTHER_ACTION = "denial_named_another_action"
    DENIAL_CAME_FROM_A_RESOURCE_POLICY = "denial_came_from_a_resource_policy"
    DENIAL_MESSAGE_HOLDS_A_CREDENTIAL = "denial_message_holds_a_credential"
    DENIAL_COULD_NOT_BE_RECORDED = "denial_could_not_be_recorded"
    CALLER_IDENTITY_UNREADABLE = "caller_identity_unreadable"
    CALLER_IS_NOT_AN_ASSUMED_ROLE = "caller_is_not_an_assumed_role"


class DenialNotProvenError(RuntimeError):
    """One attempt did not establish a denial.

    Carries the reason, the action it happened on, and the AWS error code where there
    was one. Nothing else: the message the service returned stays in the caller's hands
    until it has been masked, and the string form of this error reaches a public log.

    A run collects these per probe rather than stopping at the first one. Raised out of
    :func:`attempt_denials` it means something else: a precondition that leaves no probe
    worth attempting, such as a session that cannot be described.
    """

    def __init__(
        self,
        reason: PublisherDenialReason,
        *,
        action: str | None = None,
        error_code: str | None = None,
    ) -> None:
        self.reason = reason
        self.action = action
        self.error_code = error_code
        detail = reason.value if action is None else f"{reason.value}:{action}"
        super().__init__(detail if error_code is None else f"{detail}:{error_code}")


@dataclass(frozen=True)
class AwsApiError:
    """One service error, as the AWS CLI rendered it."""

    code: str
    operation: str
    message: str


@dataclass(frozen=True)
class DenialProbe:
    """One call the publisher session must not be allowed to make.

    ``operation`` is the API operation the command invokes, which is checked against the
    error the CLI reports so that the command and the action recorded beside it cannot
    drift apart. ``event_source`` and ``operation`` are also what CloudTrail logs the call
    as, and they are not always the action's own words. ``resource_name`` is ``None`` for
    a call that names no resource, because inventing one would invent the only part of
    the record a reader would use to check the claim.
    """

    action: str
    operation: str
    event_source: str
    resource_name: str | None
    arguments: tuple[str, ...]


def denial_probes(
    *,
    region: str,
    ecr_repository: str,
    role_name: str,
) -> tuple[DenialProbe, ...]:
    """The matrix, aimed at this account's registered repository and this session's role.

    ``role_name`` is the caller's own role, and it is the target of the create-role probe
    because a name IAM already holds cannot be created twice.
    """
    return (
        DenialProbe(
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
        DenialProbe(
            action="s3:ListAllMyBuckets",
            operation="ListBuckets",
            event_source="s3.amazonaws.com",
            resource_name=None,
            arguments=("s3api", "list-buckets", "--region", region),
        ),
        DenialProbe(
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
        DenialProbe(
            action="batch:UpdateComputeEnvironment",
            operation="UpdateComputeEnvironment",
            event_source="batch.amazonaws.com",
            resource_name=ABSENT_COMPUTE_ENVIRONMENT,
            arguments=(
                "batch",
                "update-compute-environment",
                "--region",
                region,
                "--compute-environment",
                ABSENT_COMPUTE_ENVIRONMENT,
            ),
        ),
        DenialProbe(
            action="ecr:DeleteRepository",
            operation="DeleteRepository",
            event_source="ecr.amazonaws.com",
            resource_name=f"{ecr_repository}{ABSENT_REPOSITORY_SUFFIX}",
            arguments=(
                "ecr",
                "delete-repository",
                "--region",
                region,
                "--repository-name",
                f"{ecr_repository}{ABSENT_REPOSITORY_SUFFIX}",
            ),
        ),
    )


def parse_aws_cli_error(stderr: str) -> AwsApiError | None:
    """Read the service error out of the CLI's stderr, or report that there is not one."""
    match = AWS_CLI_ERROR_PATTERN.search(stderr)
    if match is None:
        return None
    message = match.group("message").strip()
    if not message:
        return None
    return AwsApiError(
        code=match.group("code"), operation=match.group("operation"), message=message
    )


def _names_another_action(message: str, action: str) -> bool:
    """Whether a message that spelled out an action spelled out a different one."""
    if NOT_AUTHORIZED_PHRASE not in message:
        return False
    # The trailing guard is why this is a pattern rather than a substring: a refusal of
    # s3:ListAllMyBucketsAndMore contains the whole of s3:ListAllMyBuckets.
    pattern = rf"{re.escape(NOT_AUTHORIZED_PHRASE)}\s+{re.escape(action)}(?![A-Za-z0-9])"
    return re.search(pattern, message) is None


def require_denial(probe: DenialProbe, *, returncode: int, stderr: str) -> AwsApiError:
    """Return the refusal this attempt met, or raise because it met something else.

    The order matters. A call that succeeded is the worst case and is checked first; then
    that there is an AWS error at all; then that it is an error for the call this probe
    made; then that it is an authorization failure rather than the service declining to
    process the request. Those four are what a denial is. The last two read the message
    where the service wrote one: it must not name a different action, and it must not
    attribute the refusal to somebody's resource policy.
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
    if error.code not in AUTHORIZATION_ERROR_CODES:
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


def sanitize_denial_message(message: str, *, action: str) -> str:
    """Mask what a denial message says about the account before a record can hold it.

    Account IDs first, then content digests, which is the order the proof bundle uses and
    the only order that works: masking a digest first would leave twelve of its digits
    looking like an account ID. Text carrying any other credential is refused outright
    rather than masked, because masking inside a secret access key breaks the run that
    identifies it and leaves a live credential the scan then accepts.
    """
    try:
        without_account = redact_aws_account_ids(message)
    except ValueError as exc:
        raise DenialNotProvenError(
            PublisherDenialReason.DENIAL_MESSAGE_HOLDS_A_CREDENTIAL,
            action=action,
        ) from exc
    return redact_content_digests(without_account)


class AttemptedDenial(ContractModel):
    """One action the publisher session attempted, and the refusal that came back.

    Every field :class:`~edullm_platform.phase1_evidence.DenialEvidence` needs is here
    except the evidence envelope and the CloudTrail event ID, which this session cannot
    read. The two are held in step by :data:`EVIDENCE_ONLY_FIELDS` and the test that
    derives one field set from the other, so a field added to the evidence record cannot
    quietly go unrecorded here.
    """

    region: AwsRegion
    role_name: IamRoleName
    session_name: IamSessionName
    attempted_action: SecretFreeStr = Field(pattern=IAM_ACTION_PATTERN)
    attempted_resource: SecretFreeStr | None = Field(min_length=1, max_length=2048)
    attempted_at: EvidenceInstant
    outcome: Literal["denied"]
    error_code: SecretFreeStr = Field(pattern=AWS_ERROR_CODE_PATTERN)
    error_message: SecretFreeStr = Field(min_length=1, max_length=4096)
    event_name: SecretFreeStr = Field(min_length=1, max_length=128)
    event_source: SecretFreeStr = Field(pattern=AWS_SERVICE_PRINCIPAL_PATTERN)


class PublisherDenialMatrix(ContractModel):
    """Every action the publisher session was refused in one run.

    The attempts have to be the whole matrix, in order. A run that proved four of the
    five refusals proved the criterion for four of them, and a file able to hold the four
    would be read later as though it had proved all five.
    """

    schema_version: Literal[1]
    attempts: Annotated[tuple[AttemptedDenial, ...], BeforeValidator(require_ordered_sequence)] = (
        Field(min_length=len(PUBLISHER_DENIED_ACTIONS), strict=False)
    )

    @model_validator(mode="after")
    def validate_every_action_in_the_matrix_was_attempted(self) -> Self:
        attempted = tuple(attempt.attempted_action for attempt in self.attempts)
        if attempted != PUBLISHER_DENIED_ACTIONS:
            raise ValueError("the record must hold one denial per matrix action, in matrix order")
        return self


def record_denial(
    probe: DenialProbe,
    error: AwsApiError,
    *,
    region: str,
    role_name: str,
    session_name: str,
    attempted_at: datetime,
) -> AttemptedDenial:
    """Write down one refusal, masked, in the terms the evidence record uses."""
    return AttemptedDenial(
        region=region,
        role_name=role_name,
        session_name=session_name,
        attempted_action=probe.action,
        attempted_resource=probe.resource_name,
        attempted_at=attempted_at,
        outcome="denied",
        error_code=error.code,
        error_message=sanitize_denial_message(error.message, action=probe.action),
        event_name=probe.operation,
        event_source=probe.event_source,
    )


@dataclass(frozen=True)
class ProbeOutcome:
    """What one probe established: a refusal, or the reason it established nothing.

    Exactly one of the two, because a probe that established both would be a bug wearing
    an answer. The string form is what a public runner log gets, and it is the action and
    the AWS error code without a word of what the service said.
    """

    action: str
    denial: AttemptedDenial | None
    unproven: DenialNotProvenError | None

    def __post_init__(self) -> None:
        if (self.denial is None) == (self.unproven is None):
            raise ValueError("a probe establishes either a refusal or a reason there was not one")

    def __str__(self) -> str:
        if self.denial is not None:
            return f"denied:{self.action}:{self.denial.error_code}"
        return str(self.unproven)


@dataclass(frozen=True)
class DenialMatrixRun:
    """Every probe's outcome from one run, whether or not the run proved the matrix.

    A run reports rather than decides, because the run before this one stopped at its
    first anomaly and told us about one probe when it could have told us about five.
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

    def matrix(self) -> PublisherDenialMatrix:
        """The record of the run, which exists only if every action was refused."""
        denials = tuple(outcome.denial for outcome in self.outcomes if outcome.denial is not None)
        if len(denials) != len(self.outcomes):
            raise ValueError("a run that did not refuse every action has no matrix to write")
        return PublisherDenialMatrix(schema_version=1, attempts=denials)


def denial_evidence(
    attempt: AttemptedDenial,
    *,
    event_id: str,
    observed_at: datetime,
) -> DenialEvidence:
    """Complete one attempt into phase evidence, once CloudTrail has named the event.

    The publisher session cannot look the event up: reading CloudTrail is exactly the
    kind of permission it does not have. So the join happens later, from a capture with
    credentials that can, and this is the seam between the two.
    """
    return DenialEvidence.model_validate(
        {
            "source": "aws",
            "environment": "sandbox",
            "status": "ok",
            "observed_at": observed_at,
            "event_id": event_id,
            **attempt.model_dump(),
        }
    )


def run_aws(
    arguments: Sequence[str],
    *,
    action: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one AWS CLI command, bounded, capturing both streams and echoing neither."""
    try:
        return subprocess.run(
            ["aws", *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=AWS_CALL_TIMEOUT_SECONDS,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DenialNotProvenError(PublisherDenialReason.ATTEMPT_TIMED_OUT, action=action) from exc
    except OSError as exc:
        raise DenialNotProvenError(
            PublisherDenialReason.AWS_CLI_UNAVAILABLE,
            action=action,
        ) from exc


def assumed_role_identity(arn: str) -> tuple[str, str]:
    """Read the role name and session name out of an assumed-role ARN.

    The account ID in the ARN is matched so the rest can be read and is never returned.
    An identity that is not a role session cannot be written down at all, and guessing a
    role name would put a name in an evidence record that nothing established.
    """
    match = ASSUMED_ROLE_ARN_PATTERN.fullmatch(arn.strip())
    if match is None:
        raise DenialNotProvenError(PublisherDenialReason.CALLER_IS_NOT_AN_ASSUMED_ROLE)
    return match.group("role"), match.group("session")


def caller_identity(*, region: str) -> tuple[str, str]:
    """Ask STS who this session is.

    ``GetCallerIdentity`` requires no permission and cannot be denied by a policy, so it
    is the one call in this job that says nothing about how wide the role is.
    """
    completed = run_aws(
        ("sts", "get-caller-identity", "--region", region, "--query", "Arn", "--output", "text")
    )
    if completed.returncode != 0:
        raise DenialNotProvenError(PublisherDenialReason.CALLER_IDENTITY_UNREADABLE)
    return assumed_role_identity(completed.stdout)


def attempt_denials(*, region: str, ecr_repository: str) -> DenialMatrixRun:
    """Attempt every action in the matrix and report what each one answered.

    Nothing about one probe stops another. An action that was permitted, or that failed
    for a reason that proves nothing, is one outcome among five rather than the end of
    the run, so a single run says everything that is wrong instead of the first thing.

    Raises :class:`DenialNotProvenError` only for what leaves no probe worth attempting:
    a session that cannot be described has no record for a refusal to be written in.
    """
    role_name, session_name = caller_identity(region=region)
    outcomes = []
    for probe in denial_probes(region=region, ecr_repository=ecr_repository, role_name=role_name):
        outcomes.append(
            _attempt(probe, region=region, role_name=role_name, session_name=session_name)
        )
    return DenialMatrixRun(outcomes=tuple(outcomes))


def _attempt(
    probe: DenialProbe,
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
        denial = record_denial(
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
        # the role, and it still stops the publish: a denial that cannot be written down
        # cannot be evidence. The message stays out of the reason, as it does everywhere.
        return ProbeOutcome(
            action=probe.action,
            denial=None,
            unproven=DenialNotProvenError(
                PublisherDenialReason.DENIAL_COULD_NOT_BE_RECORDED,
                action=probe.action,
            ),
        )
    return ProbeOutcome(action=probe.action, denial=denial, unproven=None)
