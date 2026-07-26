"""Evidence contracts for Phase 1: what one branch commit became, and under what session.

Phase 0 evidence describes standing facts about the account, a plan or a quota. Phase 1
evidence describes one run: the workflow that built an image, the digest the registry
stored, what the scanner found, the session the publisher held, and what that session
was refused. Every record here is a :class:`FreshEvidenceModel`, so one older than the
freshness window fails to load with the reason code Phase 0 already uses.

Three conventions hold throughout, and each exists because of the secret scan.

Names, not ARNs. A role name, a repository name and a region identify the thing
uniquely within one account; the ARN adds only the account ID, which is the one value
``scan_for_secrets`` refuses. Where captured text has no name to fall back on, the
capture tool passes it through ``redact_aws_account_ids`` before it reaches a field
here, and this contract is what refuses the text if it does not.

Content identifiers carry an exact pattern instead of the scan. A forty-character commit
SHA matches ``AWS_SECRET_ACCESS_KEY_PATTERN`` and a sixty-four-character digest matches
``LONG_BASE64_CREDENTIAL_PATTERN``, so scanning them would refuse every valid value. The
pattern is the stricter constraint in any case: it admits one shape and nothing else.

Everything else is ``SecretFreeStr``, including the twelve-character image tag. That tag
is a commit prefix, so roughly one commit in two hundred produces twelve decimal digits
that no reader could tell from an account ID, and that capture fails here rather than
recording something ambiguous. The failure is loud and the remedy is another commit;
the alternative is a field an account ID fits exactly and nothing checks.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Final, Literal, Self

from pydantic import AfterValidator, Field, model_validator

from edullm_platform.contracts.base import ContractModel, Sha256Digest
from edullm_platform.contracts.image import GitHubWorkflowRunReference
from edullm_platform.contracts.repository_registry import ECR_REPOSITORY_PATTERN
from edullm_platform.contracts.source_identity import SourceIdentity
from edullm_platform.evidence import (
    EvidenceEnvironment,
    EvidenceStatus,
    FreshEvidenceModel,
    SecretFreeStr,
    redact_content_digests,
    scan_for_secrets,
)

__all__ = [
    "BuildProvenanceEvidence",
    "DenialEvidence",
    "EcrImageEvidence",
    "ImageScanEvidence",
    "ImageScanFindingCounts",
    "OidcSessionEvidence",
]

#: The publish workflow tags every image with the first twelve characters of the commit
#: SHA, so the tag is lowercase hexadecimal and exactly that long.
IMAGE_TAG_PATTERN: Final = r"^[0-9a-f]{12}$"
AWS_REGION_PATTERN: Final = r"^[a-z]{2}(?:-[a-z]+)+-[0-9]$"
CLOUDTRAIL_EVENT_ID_PATTERN: Final = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
#: The characters IAM allows in a role name, and separately in a session name.
IAM_NAME_PATTERN: Final = r"^[A-Za-z0-9+=,.@_-]+$"
#: One concrete API call, service prefix and operation. No wildcard: an action that was
#: attempted is a single call, and ``batch:*`` is a policy statement rather than a call.
IAM_ACTION_PATTERN: Final = r"^[a-z0-9-]{2,64}:[A-Z][A-Za-z0-9]{0,127}$"
AWS_ERROR_CODE_PATTERN: Final = r"^[A-Za-z][A-Za-z0-9.]{0,127}$"
AWS_SERVICE_PRINCIPAL_PATTERN: Final = r"^[a-z0-9.-]{2,64}\.amazonaws\.com$"

#: Every state ECR reports for an image scan, basic and enhanced alike. A record that
#: could not spell what the registry returned would force either a lie or a crash.
ImageScanStatus = Literal[
    "ACTIVE",
    "COMPLETE",
    "FAILED",
    "FINDINGS_UNAVAILABLE",
    "IMAGE_ARCHIVED",
    "IN_PROGRESS",
    "LIMIT_EXCEEDED",
    "PENDING",
    "SCAN_ELIGIBILITY_EXPIRED",
    "UNSUPPORTED_IMAGE",
]

#: The two states in which ECR has findings to report: ``COMPLETE`` for a basic scan,
#: ``ACTIVE`` for continuous enhanced scanning. Every other state means the question
#: was never answered, which is not the same answer as none.
SCAN_STATUSES_WITH_FINDINGS: Final = frozenset({"ACTIVE", "COMPLETE"})


def validate_instant(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


#: A timestamp on captured evidence other than ``observed_at``, which carries its own
#: freshness rule. Naive values are refused rather than assumed to be UTC.
EvidenceInstant = Annotated[datetime, Field(strict=False), AfterValidator(validate_instant)]

# Written out rather than layered onto SecretFreeStr so the pattern is checked before
# the scan. Annotated metadata applies outwards, so wrapping SecretFreeStr would report
# a value the pattern already excludes as a suspected credential.
EcrRepositoryName = Annotated[
    str,
    Field(min_length=1, max_length=256, pattern=ECR_REPOSITORY_PATTERN),
    AfterValidator(scan_for_secrets),
]
AwsRegion = Annotated[
    str,
    Field(pattern=AWS_REGION_PATTERN),
    AfterValidator(scan_for_secrets),
]
IamRoleName = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=IAM_NAME_PATTERN),
    AfterValidator(scan_for_secrets),
]
IamSessionName = Annotated[
    str,
    Field(min_length=2, max_length=64, pattern=IAM_NAME_PATTERN),
    AfterValidator(scan_for_secrets),
]
CloudTrailEventId = Annotated[
    str,
    Field(pattern=CLOUDTRAIL_EVENT_ID_PATTERN),
    AfterValidator(scan_for_secrets),
]


def scan_reused_contract_strings(*values: str) -> None:
    """Apply the secret scan to strings from contracts that predate ``SecretFreeStr``.

    ``GitHubWorkflowRunReference`` and ``SourceIdentity`` constrain their fields by
    pattern, and those patterns admit an account ID: a repository may be named in
    digits, and so may a branch. Content digests are masked first, because a commit SHA
    is forty characters of the alphabet a secret access key is drawn from and would be
    refused on sight.
    """
    for value in values:
        scan_for_secrets(redact_content_digests(value))


class BuildProvenanceEvidence(FreshEvidenceModel):
    """The workflow run that produced one image, joined to the commit it was built from.

    This is the observed counterpart of ``ImageProvenance``, which the publish workflow
    writes as it runs. Both are recorded because they answer different questions: the
    provenance record says what the run claimed, and this says what a later reader found
    when they went looking. The two shipped contracts are reused rather than restated,
    so a field cannot drift between the claim and the observation.

    ``source_identity`` carries ``clean`` and ``verified`` as literal ``True``, so a
    build from a dirty or unverified tree cannot be recorded as provenance at all.
    """

    source: Literal["github"]
    environment: EvidenceEnvironment
    status: EvidenceStatus
    workflow_run: GitHubWorkflowRunReference
    source_identity: SourceIdentity
    image_digest: Sha256Digest
    run_conclusion: Literal["success"]
    run_completed_at: EvidenceInstant

    @model_validator(mode="after")
    def scan_the_reused_contracts(self) -> Self:
        scan_reused_contract_strings(
            self.workflow_run.run_repository,
            self.workflow_run.workflow_repository,
            self.workflow_run.workflow_path,
            self.workflow_run.workflow_ref,
            self.source_identity.repository,
            self.source_identity.ref,
        )
        return self


class EcrImageEvidence(FreshEvidenceModel):
    """One image as the registry holds it: repository, digest, tag, and base.

    The registry ID is deliberately absent. It is the account ID and nothing else, and
    the repository name identifies the repository uniquely within the account already.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    status: EvidenceStatus
    region: AwsRegion
    repository_name: EcrRepositoryName
    image_digest: Sha256Digest
    image_tag: SecretFreeStr = Field(pattern=IMAGE_TAG_PATTERN)
    base_image_digest: Sha256Digest
    image_pushed_at: EvidenceInstant

    @model_validator(mode="after")
    def validate_the_image_differs_from_its_base(self) -> Self:
        if self.image_digest == self.base_image_digest:
            raise ValueError("an image cannot be its own base image")
        return self


class ImageScanFindingCounts(ContractModel):
    """Findings by severity, with every severity ECR defines present.

    All six are required. ECR omits a severity from ``findingSeverityCounts`` when its
    count is zero, so a record that allowed a severity to be absent could not say
    whether the scan found none or the capture dropped it.
    """

    critical: int = Field(ge=0)
    high: int = Field(ge=0)
    medium: int = Field(ge=0)
    low: int = Field(ge=0)
    informational: int = Field(ge=0)
    undefined: int = Field(ge=0)

    @property
    def total(self) -> int:
        return (
            self.critical + self.high + self.medium + self.low + self.informational + self.undefined
        )


class ImageScanEvidence(FreshEvidenceModel):
    """What the registry scanner found, and whether it finished looking.

    A scan that has not completed is not a scan with no findings, and the difference is
    the whole point of recording the status beside the counts. The counts are present
    exactly when the status says there are findings to report, so neither state can be
    read as the other, and a scan that did not complete has to say why.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    status: EvidenceStatus
    region: AwsRegion
    repository_name: EcrRepositoryName
    image_digest: Sha256Digest
    scan_status: ImageScanStatus
    scan_status_description: SecretFreeStr | None = Field(min_length=1, max_length=1024)
    scan_completed_at: EvidenceInstant | None
    finding_counts: ImageScanFindingCounts | None

    @property
    def scan_reported_findings(self) -> bool:
        return self.scan_status in SCAN_STATUSES_WITH_FINDINGS

    @model_validator(mode="after")
    def validate_findings_match_the_scan_status(self) -> Self:
        if self.scan_reported_findings:
            if self.finding_counts is None:
                raise ValueError("a completed scan must record its finding counts")
            if self.scan_completed_at is None:
                raise ValueError("a completed scan must record when it completed")
        else:
            if self.finding_counts is not None:
                raise ValueError("only a completed scan may record finding counts")
            if self.scan_status_description is None:
                raise ValueError("a scan that did not complete must record why")
        return self


class OidcSessionEvidence(FreshEvidenceModel):
    """The bounded publisher session, as CloudTrail recorded it.

    Bounded means the record cannot describe a session without an end: ``expires_at``
    is required, and it has to fall after ``assumed_at``. How long the window may be is
    the role's ``MaxSessionDuration``, which ``DeployedRoleEvidence`` records and a
    comparison against the template checks. It is deliberately not asserted here.
    CloudTrail timestamps have one-second resolution, so a ceiling written into this
    contract would refuse an honest hour-long session that rounded to 3601 seconds, and
    a contract that fails on rounding teaches its readers to work around it.

    The role is named rather than given by ARN, and the session is identified by the
    CloudTrail event ID, which is what a reviewer needs to find the record itself.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    status: EvidenceStatus
    region: AwsRegion
    event_id: CloudTrailEventId
    event_name: Literal["AssumeRoleWithWebIdentity"]
    event_source: Literal["sts.amazonaws.com"]
    role_name: IamRoleName
    session_name: IamSessionName
    oidc_issuer: SecretFreeStr = Field(min_length=1, max_length=255)
    oidc_audience: SecretFreeStr = Field(min_length=1, max_length=255)
    oidc_subject: SecretFreeStr = Field(min_length=1, max_length=1024)
    assumed_at: EvidenceInstant
    expires_at: EvidenceInstant

    @property
    def session_duration(self) -> timedelta:
        return self.expires_at - self.assumed_at

    @model_validator(mode="after")
    def validate_the_session_window(self) -> Self:
        if self.expires_at <= self.assumed_at:
            raise ValueError("a session must expire after it was assumed")
        return self


class DenialEvidence(FreshEvidenceModel):
    """One action attempted under the publisher session, and the denial that came back.

    ``outcome`` is the literal ``denied`` and nothing else. A call that succeeded is not
    denial evidence; it is a criterion failing, and it belongs in the criterion's own
    text rather than in a record whose name asserts the opposite. A capture tool that
    meets one has to stop rather than file it here.

    ``attempted_action`` is the IAM action a policy would have to allow, and
    ``event_name`` is what CloudTrail logged. Both are recorded because they are not
    always the same word: ``s3:ListBucket`` is logged as ``ListObjects``. Nothing here
    cross-checks them for that reason.

    ``error_message`` is the one field with no name to fall back on, and it is where an
    account ID arrives. The capture tool passes it through ``redact_aws_account_ids``;
    this contract is what refuses it if the tool forgets.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    status: EvidenceStatus
    region: AwsRegion
    role_name: IamRoleName
    session_name: IamSessionName
    attempted_action: SecretFreeStr = Field(pattern=IAM_ACTION_PATTERN)
    attempted_resource: SecretFreeStr | None = Field(min_length=1, max_length=2048)
    attempted_at: EvidenceInstant
    outcome: Literal["denied"]
    error_code: SecretFreeStr = Field(pattern=AWS_ERROR_CODE_PATTERN)
    error_message: SecretFreeStr = Field(min_length=1, max_length=4096)
    event_id: CloudTrailEventId
    event_name: SecretFreeStr = Field(min_length=1, max_length=128)
    event_source: SecretFreeStr = Field(pattern=AWS_SERVICE_PRINCIPAL_PATTERN)
