from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Final, Literal, Self, TypedDict

from pydantic import (
    AfterValidator,
    BeforeValidator,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from edullm_platform.contracts.base import ContractModel, require_ordered_sequence
from edullm_platform.contracts.workload import ComputeProfile, WorkloadCatalog

AWS_ACCOUNT_ID_PATTERN = re.compile(r"(?<![0-9])\d{12}(?![0-9])")
SHA256_DIGEST_TOKEN = re.compile(r"sha256:[0-9a-f]{64}")
GIT_COMMIT_SHA_TOKEN = re.compile(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])")
AWS_ACCESS_KEY_ID_PATTERN = re.compile(r"(?i)(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])")
AWS_SECRET_ACCESS_KEY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])"
)
AWS_STS_SESSION_TOKEN_PATTERN = re.compile(r"(?i)(?:FwoGZXIv|IQoJb3)[A-Za-z0-9/+]{20,}")
LONG_BASE64_CREDENTIAL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{60,}(?![A-Za-z0-9/+=])"
)
GITHUB_TOKEN_PATTERN = re.compile(
    r"(?:ghp_[A-Za-z0-9_]{36,}|gho_[A-Za-z0-9_]{36,}|ghs_[A-Za-z0-9_]{36,}|"
    r"ghu_[A-Za-z0-9_]{36,}|github_pat_[A-Za-z0-9_]{22,})"
)
PEM_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)
BEARER_TOKEN_PATTERN = re.compile(r"Bearer [A-Za-z0-9\-._~+/]+=*")
JWT_PATTERN = re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    AWS_ACCOUNT_ID_PATTERN,
    AWS_ACCESS_KEY_ID_PATTERN,
    AWS_SECRET_ACCESS_KEY_PATTERN,
    AWS_STS_SESSION_TOKEN_PATTERN,
    LONG_BASE64_CREDENTIAL_PATTERN,
    GITHUB_TOKEN_PATTERN,
    PEM_PRIVATE_KEY_PATTERN,
    BEARER_TOKEN_PATTERN,
    JWT_PATTERN,
)

#: Everything ``scan_for_secrets`` refuses that is not an account ID. Derived rather
#: than listed, so a pattern added above is guarded against without a second edit.
NON_ACCOUNT_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    pattern for pattern in SECRET_PATTERNS if pattern is not AWS_ACCOUNT_ID_PATTERN
)

AWS_ACCOUNT_ID_PLACEHOLDER: Final = "<aws-account-id>"

# Twelve digits or more, masked as one run. A thirteenth digit beside an account ID
# matches neither AWS_ACCOUNT_ID_PATTERN nor a mask that stopped at twelve, so masking
# exactly what the scanner refuses would leave the account ID in text the scanner then
# passes. The cost is that any long decimal run in free text is masked too.
DIGIT_RUN_HOLDING_AN_ACCOUNT_ID = re.compile(r"(?<![0-9])[0-9]{12,}(?![0-9])")

# Content digests are alternatives of the same expression rather than a separate pass,
# so the engine consumes each one whole before the digit run can reach inside it. A
# sha256 digest or a commit SHA routinely contains twelve consecutive decimal digits,
# and masking them would corrupt the identifier the evidence exists to record.
ACCOUNT_ID_IN_FREE_TEXT = re.compile(
    f"(?P<digest>{SHA256_DIGEST_TOKEN.pattern})"
    f"|(?P<commit>{GIT_COMMIT_SHA_TOKEN.pattern})"
    f"|(?P<account>{DIGIT_RUN_HOLDING_AN_ACCOUNT_ID.pattern})"
)

FRESHNESS_WINDOW = timedelta(days=30)
EVIDENCE_STALE_CODE: Final = "evidence_stale"

CapacityVerdict = Literal["verified", "increase_required", "blocked"]
EvidenceEnvironment = Literal["sandbox"]
EvidenceStatus = Literal["ok"]
QuotaAppliedAtLevel = Literal["ACCOUNT"]
RepositoryVisibility = Literal["public", "private", "internal"]

BATCH_QUOTA_TARGETS: tuple[dict[str, str], ...] = (
    {"quota_code": "L-144F0CA5", "quota_name": "Compute environment limit"},
    {"quota_code": "L-4CEA37AD", "quota_name": "Job queue limit"},
)


class InstanceEvidence(TypedDict):
    required_vcpus: int
    quota_code: str


INSTANCE_EVIDENCE: dict[str, InstanceEvidence] = {
    "g5.12xlarge": {"required_vcpus": 48, "quota_code": "L-DB2E81BA"},
    "c7i.8xlarge": {"required_vcpus": 32, "quota_code": "L-1216C47A"},
}

WORKLOAD_PROFILE_REQUIRED_VCPUS: Final = {
    "gpu-4xa10g": INSTANCE_EVIDENCE["g5.12xlarge"]["required_vcpus"],
    "cpu-32vcpu": INSTANCE_EVIDENCE["c7i.8xlarge"]["required_vcpus"],
}


class StaleEvidenceError(ValueError):
    pass


def redact_content_digests(text: str) -> str:
    masked = SHA256_DIGEST_TOKEN.sub("<sha256-content-digest>", text)
    return GIT_COMMIT_SHA_TOKEN.sub("<git-commit-sha>", masked)


def _mask_account_id(match: re.Match[str]) -> str:
    if match.group("account") is None:
        return match.group(0)
    return AWS_ACCOUNT_ID_PLACEHOLDER


def redact_aws_account_ids(text: str) -> str:
    """Mask AWS account IDs in captured free text so it can pass ``scan_for_secrets``.

    Phase 1 evidence records names rather than ARNs wherever a name identifies the thing
    uniquely, which keeps account IDs out of most fields entirely. What is left is text
    nobody here composed: an ``AccessDenied`` message, a CloudTrail record. The account
    ID in those is unavoidable, and this is the only sanctioned way to record them.

    Two things this refuses to do, both of which would make the mask worse than useless:

    It will not redact text that carries any other credential. A forty-character secret
    access key can contain twelve consecutive digits, and masking them would break the
    forty-character run that identifies it, leaving a live credential the scanner then
    accepts. Text like that is refused rather than laundered, which also means the
    caller finds out instead of committing it. Text holding a bare sixty-character
    hexadecimal token is refused for the same reason, since the scanner would refuse it
    too; a digest written with its ``sha256:`` prefix is recognised and kept. Kept, not
    accepted: ``scan_for_secrets`` still refuses a digest, so free text carrying one
    needs ``redact_content_digests`` after this, which is the order the proof bundle
    already uses.

    It will not mask only what the scanner refuses. A digit beside an account ID hides
    it from ``AWS_ACCOUNT_ID_PATTERN``, so any run of twelve or more digits is masked
    whole. A long decimal literal in captured text is masked along with it, which is the
    price of a mask that cannot be stepped around.
    """
    without_digests = redact_content_digests(text)
    if any(pattern.search(without_digests) for pattern in NON_ACCOUNT_SECRET_PATTERNS):
        raise ValueError("refusing to redact text that carries a credential")
    return ACCOUNT_ID_IN_FREE_TEXT.sub(_mask_account_id, text)


def scan_for_secrets(value: str) -> str:
    if any(pattern.search(value) for pattern in SECRET_PATTERNS):
        raise ValueError("must not contain credentials or raw AWS account IDs")
    return value


SecretFreeStr = Annotated[str, AfterValidator(scan_for_secrets)]


def validate_observed_at(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("observation timestamps must be timezone-aware")
    observed_at = value.astimezone(UTC)
    now = datetime.now(tz=UTC)
    if observed_at > now:
        raise ValueError("observation timestamps must not be in the future")
    if now - observed_at > FRESHNESS_WINDOW:
        raise StaleEvidenceError(EVIDENCE_STALE_CODE)
    return observed_at


def evidence_load_reason_code(error: ValidationError) -> str:
    for item in error.errors():
        if item["loc"] != ("observed_at",):
            continue
        ctx_error = item.get("ctx", {}).get("error")
        if isinstance(ctx_error, ValueError) and str(ctx_error) == EVIDENCE_STALE_CODE:
            return EVIDENCE_STALE_CODE
        if item.get("msg") == EVIDENCE_STALE_CODE:
            return EVIDENCE_STALE_CODE
    return "evidence_invalid"


class FreshEvidenceModel(ContractModel):
    observed_at: datetime = Field(strict=False)

    @field_validator("observed_at")
    @classmethod
    def validate_fresh_observation(cls, value: datetime) -> datetime:
        try:
            return validate_observed_at(value)
        except StaleEvidenceError as exc:
            raise ValueError(str(exc)) from exc


class GitHubPlanEvidence(FreshEvidenceModel):
    source: Literal["github"]
    environment: EvidenceEnvironment
    organization: SecretFreeStr = Field(min_length=1)
    repository: SecretFreeStr = Field(min_length=1)
    visibility: RepositoryVisibility
    status: EvidenceStatus
    plan_name: SecretFreeStr = Field(min_length=1)


class QuotaRecord(ContractModel):
    service_code: SecretFreeStr = Field(min_length=1)
    quota_code: SecretFreeStr = Field(min_length=1)
    quota_name: SecretFreeStr = Field(min_length=1)
    applied_value: float = Field(gt=0)
    unit: SecretFreeStr = Field(min_length=1)
    quota_applied_at_level: QuotaAppliedAtLevel
    workload_profile: SecretFreeStr | None = Field(default=None, min_length=1)
    required_vcpus: int | None = Field(default=None, gt=0)


class BatchQuotaRecord(ContractModel):
    service_code: Literal["batch"]
    quota_code: SecretFreeStr = Field(min_length=1)
    quota_name: SecretFreeStr = Field(min_length=1)
    applied_value: float = Field(gt=0)
    quota_applied_at_level: QuotaAppliedAtLevel


def authoritative_required_vcpus(workload_profile: str | None) -> int | None:
    if workload_profile is None:
        return None
    return WORKLOAD_PROFILE_REQUIRED_VCPUS.get(workload_profile)


def required_vcpus_for_workload_profile(
    catalog: WorkloadCatalog,
    workload_profile: str | None,
) -> int | None:
    per_instance = authoritative_required_vcpus(workload_profile)
    if per_instance is None:
        return None
    profile_by_name = {profile.name: profile for profile in catalog.compute_profiles}
    compute_profile = profile_by_name.get(workload_profile or "")
    if compute_profile is None:
        return per_instance
    return per_instance * compute_profile.nodes


def quota_capacity_issues(
    quotas: tuple[QuotaRecord, ...],
    *,
    catalog: WorkloadCatalog | None = None,
) -> tuple[bool, list[str]]:
    incomplete = False
    insufficient: list[str] = []
    for quota in quotas:
        if catalog is not None:
            required_vcpus = required_vcpus_for_workload_profile(catalog, quota.workload_profile)
        else:
            required_vcpus = authoritative_required_vcpus(quota.workload_profile)
        if quota.workload_profile is None or required_vcpus is None:
            incomplete = True
            continue
        if quota.applied_value < required_vcpus:
            insufficient.append(
                f"{quota.workload_profile} requires {required_vcpus} vCPU "
                f"but {quota.quota_code} applied quota is {quota.applied_value:g}"
            )
    return incomplete, insufficient


def profiles_requiring_capacity_evidence(
    catalog: WorkloadCatalog,
) -> tuple[ComputeProfile, ...]:
    representative_profiles = {workload.compute_profile for workload in catalog.workloads}
    return tuple(
        profile
        for profile in catalog.compute_profiles
        if profile.provisioned or profile.name in representative_profiles
    )


def ec2_quota_coverage_issues(
    *,
    catalog: WorkloadCatalog,
    quotas: tuple[QuotaRecord, ...],
) -> tuple[str | None, str | None]:
    required_profiles = {
        profile.name for profile in profiles_requiring_capacity_evidence(catalog)
    }
    covered_profiles = {quota.workload_profile for quota in quotas if quota.workload_profile is not None}
    missing_profiles = sorted(required_profiles - covered_profiles)
    if missing_profiles:
        return (
            "capacity_blocked",
            (
                "Missing EC2 quota records for representative or provisioned profiles: "
                f"{', '.join(missing_profiles)}."
            ),
        )
    incomplete, insufficient = quota_capacity_issues(quotas, catalog=catalog)
    if incomplete:
        return (
            "capacity_blocked",
            "Capacity review blocked because representative workload quota mapping is incomplete.",
        )
    if insufficient:
        return (
            "capacity_increase_required",
            "Sandbox quota increase required before Phase 1: " + "; ".join(insufficient),
        )
    return None, None


def batch_quota_issues(
    batch_quotas: tuple[BatchQuotaRecord, ...],
) -> list[str]:
    by_code = {record.quota_code: record for record in batch_quotas}
    issues: list[str] = []
    for target in BATCH_QUOTA_TARGETS:
        quota_code = target["quota_code"]
        if quota_code not in by_code:
            issues.append(f"missing batch quota record {quota_code}")
    return issues


class ServiceQuotasEvidence(FreshEvidenceModel):
    source: Literal["aws"]
    environment: EvidenceEnvironment
    account_alias: SecretFreeStr = Field(min_length=1)
    region: SecretFreeStr = Field(min_length=1)
    status: EvidenceStatus
    capacity_verdict: CapacityVerdict
    capacity_verdict_note: SecretFreeStr = Field(min_length=1)
    quotas: Annotated[tuple[QuotaRecord, ...], BeforeValidator(require_ordered_sequence)] = Field(
        min_length=1,
        strict=False,
    )
    batch_quotas: Annotated[
        tuple[BatchQuotaRecord, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)


class CapturedServiceQuotasEvidence(ServiceQuotasEvidence):
    @model_validator(mode="after")
    def validate_capacity_verdict_matches_quotas(self) -> Self:
        incomplete, insufficient = quota_capacity_issues(self.quotas)
        if self.capacity_verdict == "verified":
            if incomplete or insufficient:
                raise ValueError(
                    "verified verdict requires every quota record to cover its required vcpus"
                )
        elif self.capacity_verdict == "increase_required":
            if incomplete:
                raise ValueError(
                    "increase_required verdict requires complete workload mapping"
                )
            if not insufficient:
                raise ValueError(
                    "increase_required verdict requires at least one insufficient quota"
                )
        elif self.capacity_verdict == "blocked" and not self.capacity_verdict_note.strip():
            raise ValueError("blocked verdict requires a non-empty reason note")
        return self
