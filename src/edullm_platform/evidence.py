from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BeforeValidator,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from edullm_platform.contracts.base import ContractModel, require_ordered_sequence
from edullm_platform.contracts.workload import WorkloadCatalog

AWS_ACCOUNT_ID_PATTERN = re.compile(r"(?<![0-9])\d{12}(?![0-9])")
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

FRESHNESS_WINDOW = timedelta(days=30)

CapacityVerdict = Literal["verified", "increase_required", "blocked"]
EvidenceEnvironment = Literal["sandbox"]
EvidenceStatus = Literal["ok"]
QuotaAppliedAtLevel = Literal["ACCOUNT"]

BATCH_QUOTA_TARGETS: tuple[dict[str, str], ...] = (
    {"quota_code": "L-144F0CA5", "quota_name": "Compute environment limit"},
    {"quota_code": "L-4CEA37AD", "quota_name": "Job queue limit"},
)


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
        raise ValueError("observation must be at most 30 days old")
    return observed_at


def evidence_load_reason_code(error: ValidationError) -> str:
    if any("observation must be at most 30 days old" in item["msg"] for item in error.errors()):
        return "evidence_stale"
    return "evidence_invalid"


class FreshEvidenceModel(ContractModel):
    observed_at: datetime = Field(strict=False)

    @field_validator("observed_at")
    @classmethod
    def validate_fresh_observation(cls, value: datetime) -> datetime:
        return validate_observed_at(value)


class GitHubPlanEvidence(FreshEvidenceModel):
    source: Literal["github"]
    environment: EvidenceEnvironment
    organization: SecretFreeStr = Field(min_length=1)
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


def quota_capacity_issues(
    quotas: tuple[QuotaRecord, ...],
) -> tuple[bool, list[str]]:
    incomplete = False
    insufficient: list[str] = []
    for quota in quotas:
        if quota.required_vcpus is None or quota.workload_profile is None:
            incomplete = True
            continue
        if quota.applied_value < quota.required_vcpus:
            insufficient.append(
                f"{quota.workload_profile} requires {quota.required_vcpus} vCPU "
                f"but {quota.quota_code} applied quota is {quota.applied_value:g}"
            )
    return incomplete, insufficient


def ec2_quota_coverage_issues(
    *,
    catalog: WorkloadCatalog,
    quotas: tuple[QuotaRecord, ...],
) -> tuple[str | None, str | None]:
    required_profiles = {profile.name for profile in catalog.compute_profiles}
    covered_profiles = {quota.workload_profile for quota in quotas if quota.workload_profile is not None}
    missing_profiles = sorted(required_profiles - covered_profiles)
    if missing_profiles:
        return (
            "capacity_blocked",
            f"Missing EC2 quota records for representative profiles: {', '.join(missing_profiles)}.",
        )
    incomplete, insufficient = quota_capacity_issues(quotas)
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
        record = by_code.get(quota_code)
        if record is None:
            issues.append(f"missing batch quota record {quota_code}")
            continue
        if record.applied_value < 1:
            issues.append(
                f"{quota_code} applied quota {record.applied_value:g} is below the minimum of 1"
            )
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
