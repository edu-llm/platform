from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, Self, TypedDict

from pydantic import AfterValidator, BeforeValidator, Field, field_validator, model_validator

from edullm_platform.config import load_yaml
from edullm_platform.contracts.base import ContractModel, require_ordered_sequence
from edullm_platform.contracts.workload import WorkloadCatalog

AWS_ACCOUNT_ID_PATTERN = re.compile(r"(?<![0-9])\d{12}(?![0-9])")
AWS_ACCESS_KEY_ID_PATTERN = re.compile(r"(?i)(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])")
AWS_SECRET_ACCESS_KEY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])"
)
AWS_STS_SESSION_TOKEN_PATTERN = re.compile(r"(?i)(?:FwoGZXIv|IQoJb3)[A-Za-z0-9/+]{20,}")
GITHUB_TOKEN_PATTERN = re.compile(
    r"(?:ghp_[A-Za-z0-9_]{36,}|gho_[A-Za-z0-9_]{36,}|github_pat_[A-Za-z0-9_]{22,})"
)
PEM_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)
BEARER_TOKEN_PATTERN = re.compile(r"Bearer [A-Za-z0-9\-._~+/]+=*")
JWT_PATTERN = re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
INSTANCE_TYPE_PATTERN = re.compile(r"\b([a-z0-9]+\.[a-z0-9]+)\b")

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    AWS_ACCOUNT_ID_PATTERN,
    AWS_ACCESS_KEY_ID_PATTERN,
    AWS_SECRET_ACCESS_KEY_PATTERN,
    AWS_STS_SESSION_TOKEN_PATTERN,
    GITHUB_TOKEN_PATTERN,
    PEM_PRIVATE_KEY_PATTERN,
    BEARER_TOKEN_PATTERN,
    JWT_PATTERN,
)

FRESHNESS_WINDOW = timedelta(days=30)
ALLOWED_OUTPUT_SUFFIX = Path("docs-frank/working/phase-0-evidence")

CapacityVerdict = Literal["verified", "increase_required", "blocked"]
EvidenceEnvironment = Literal["sandbox"]
EvidenceStatus = Literal["ok"]
QuotaAppliedAtLevel = Literal["ACCOUNT"]

class InstanceEvidence(TypedDict):
    required_vcpus: int
    quota_code: str


INSTANCE_EVIDENCE: dict[str, InstanceEvidence] = {
    "g5.12xlarge": {"required_vcpus": 48, "quota_code": "L-DB2E81BA"},
    "c7i.8xlarge": {"required_vcpus": 32, "quota_code": "L-1216C47A"},
}


class Ec2QuotaTarget(TypedDict):
    quota_code: str
    workload_profile: str
    required_vcpus: int
    instance_type: str


class BatchQuotaTarget(TypedDict):
    quota_code: str
    quota_name: str


BATCH_QUOTA_TARGETS: tuple[BatchQuotaTarget, ...] = (
    {"quota_code": "L-144F0CA5", "quota_name": "Compute environment limit"},
    {"quota_code": "L-4CEA37AD", "quota_name": "Job queue limit"},
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def allowed_output_root(base_dir: Path | None = None) -> Path:
    root = base_dir if base_dir is not None else project_root()
    return (root / ALLOWED_OUTPUT_SUFFIX).resolve()


def resolve_output_dir(output_dir: Path, *, base_dir: Path | None = None) -> Path:
    root = base_dir if base_dir is not None else project_root()
    candidate = output_dir if output_dir.is_absolute() else (root / output_dir)
    resolved = candidate.resolve()
    allowed = allowed_output_root(root)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise ValueError(
            "output_dir must be under docs-frank/working/phase-0-evidence/"
        ) from exc
    return resolved


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
        elif self.capacity_verdict == "blocked" and not incomplete:
            raise ValueError("blocked verdict requires incomplete workload mapping")
        return self


def instance_type_from_pricing_source(pricing_source: str) -> str:
    match = INSTANCE_TYPE_PATTERN.search(pricing_source)
    if match is None:
        raise ValueError(f"pricing_source missing instance type: {pricing_source!r}")
    return match.group(1)


def ec2_quota_targets_from_catalog(catalog: WorkloadCatalog) -> tuple[Ec2QuotaTarget, ...]:
    targets: list[Ec2QuotaTarget] = []
    for profile in catalog.compute_profiles:
        instance_type = instance_type_from_pricing_source(profile.pricing_source)
        metadata = INSTANCE_EVIDENCE.get(instance_type)
        if metadata is None:
            raise ValueError(f"unsupported instance type for evidence capture: {instance_type}")
        targets.append(
            {
                "quota_code": metadata["quota_code"],
                "workload_profile": profile.name,
                "required_vcpus": metadata["required_vcpus"],
                "instance_type": instance_type,
            }
        )
    return tuple(targets)


def load_workload_catalog(config_root: Path | None = None) -> WorkloadCatalog:
    root = config_root if config_root is not None else project_root()
    return load_yaml(root / "config" / "workload-catalog.yaml", WorkloadCatalog)


def run_command(args: list[str]) -> object:
    completed = subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "command failed"
        raise RuntimeError(message)
    return json.loads(completed.stdout)


def sanitize_github_org(raw: dict[str, object], *, observed_at: str) -> GitHubPlanEvidence:
    login = raw.get("login")
    plan = raw.get("plan")
    if not isinstance(login, str) or not login:
        raise ValueError("GitHub organization response missing login")
    if not isinstance(plan, dict):
        raise TypeError("GitHub organization response missing plan")
    plan_name = plan.get("name")
    if not isinstance(plan_name, str) or not plan_name:
        raise ValueError("GitHub organization response missing non-empty plan name")
    return GitHubPlanEvidence.model_validate(
        {
            "source": "github",
            "environment": "sandbox",
            "organization": login,
            "observed_at": observed_at,
            "status": "ok",
            "plan_name": plan_name,
        }
    )


def sanitize_quota_record(
    raw: dict[str, object],
    *,
    workload_profile: str | None = None,
    required_vcpus: int | None = None,
) -> QuotaRecord:
    quota_applied_at_level = raw.get("QuotaAppliedAtLevel")
    if quota_applied_at_level != "ACCOUNT":
        raise ValueError("quota evidence must use account-level applied quotas")
    unit = raw.get("Unit")
    quota_name = raw.get("QuotaName")
    value = raw.get("Value")
    if not isinstance(unit, str):
        raise TypeError("quota response missing unit")
    service_code = raw.get("ServiceCode")
    if unit == "None" and service_code == "ec2":
        normalized_unit = "vCPU"
    else:
        normalized_unit = unit
    return QuotaRecord.model_validate(
        {
            "service_code": raw.get("ServiceCode"),
            "quota_code": raw.get("QuotaCode"),
            "quota_name": quota_name,
            "applied_value": value,
            "unit": normalized_unit,
            "quota_applied_at_level": quota_applied_at_level,
            "workload_profile": workload_profile,
            "required_vcpus": required_vcpus,
        }
    )


def sanitize_batch_quota_record(raw: dict[str, object]) -> BatchQuotaRecord:
    quota_applied_at_level = raw.get("QuotaAppliedAtLevel")
    if quota_applied_at_level != "ACCOUNT":
        raise ValueError("batch quota evidence must use account-level applied quotas")
    return BatchQuotaRecord.model_validate(
        {
            "service_code": "batch",
            "quota_code": raw.get("QuotaCode"),
            "quota_name": raw.get("QuotaName"),
            "applied_value": raw.get("Value"),
            "quota_applied_at_level": quota_applied_at_level,
        }
    )


def assess_capacity_verdict(
    quotas: tuple[QuotaRecord, ...],
) -> tuple[CapacityVerdict, str]:
    incomplete, insufficient = quota_capacity_issues(quotas)
    if incomplete:
        return (
            "blocked",
            "Capacity review blocked because representative workload mapping is incomplete.",
        )
    if insufficient:
        return (
            "increase_required",
            "Sandbox quota increase required before Phase 1: " + "; ".join(insufficient),
        )
    profile_names = " and ".join(
        quota.workload_profile
        for quota in quotas
        if quota.workload_profile is not None
    )
    return (
        "verified",
        (
            f"Sandbox applied quotas satisfy representative {profile_names} profiles; "
            "does not attest production capacity."
        ),
    )


def build_service_quotas_evidence(
    *,
    environment: EvidenceEnvironment,
    account_alias: str,
    aws_region: str,
    observed_at: str,
    quota_records: list[QuotaRecord],
    batch_records: list[BatchQuotaRecord],
) -> ServiceQuotasEvidence:
    verdict, note = assess_capacity_verdict(tuple(quota_records))
    payload = {
        "source": "aws",
        "environment": environment,
        "account_alias": account_alias,
        "region": aws_region,
        "observed_at": observed_at,
        "status": "ok",
        "capacity_verdict": verdict,
        "capacity_verdict_note": note,
        "quotas": quota_records,
        "batch_quotas": batch_records,
    }
    return ServiceQuotasEvidence.model_validate(payload)


def fetch_account_alias(*, aws_profile: str, aws_region: str) -> str:
    raw = run_command(
        [
            "aws",
            "iam",
            "list-account-aliases",
            "--profile",
            aws_profile,
            "--region",
            aws_region,
            "--output",
            "json",
        ]
    )
    if not isinstance(raw, dict):
        raise TypeError("account alias response must be a JSON object")
    aliases = raw.get("AccountAliases")
    if not isinstance(aliases, list) or not aliases or not isinstance(aliases[0], str):
        raise ValueError("account alias response missing AccountAliases")
    return aliases[0]


def fetch_service_quota(
    *,
    service_code: str,
    quota_code: str,
    aws_profile: str,
    aws_region: str,
) -> dict[str, object]:
    raw = run_command(
        [
            "aws",
            "service-quotas",
            "get-service-quota",
            "--service-code",
            service_code,
            "--quota-code",
            quota_code,
            "--profile",
            aws_profile,
            "--region",
            aws_region,
            "--output",
            "json",
        ]
    )
    if not isinstance(raw, dict):
        raise TypeError("service quota response must be a JSON object")
    quota = raw.get("Quota")
    if not isinstance(quota, dict):
        raise TypeError("service quota response missing Quota")
    return quota


def capture_github_plan(*, github_org: str) -> tuple[GitHubPlanEvidence, dict[str, object]]:
    observed_at = datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    raw = run_command(["gh", "api", f"orgs/{github_org}"])
    if not isinstance(raw, dict):
        raise TypeError("GitHub organization response must be a JSON object")
    evidence = sanitize_github_org(raw, observed_at=observed_at)
    return evidence, raw


def capture_service_quotas(
    *,
    aws_profile: str,
    aws_region: str,
    environment: EvidenceEnvironment,
    catalog: WorkloadCatalog | None = None,
) -> tuple[ServiceQuotasEvidence, dict[str, object]]:
    observed_at = datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    workload_catalog = catalog if catalog is not None else load_workload_catalog()
    ec2_targets = ec2_quota_targets_from_catalog(workload_catalog)
    account_alias = fetch_account_alias(aws_profile=aws_profile, aws_region=aws_region)
    raw_payload: dict[str, object] = {
        "account_alias": account_alias,
        "ec2_quotas": [],
        "batch_quotas": [],
    }
    ec2_raw_quotas: list[dict[str, object]] = []
    batch_raw_quotas: list[dict[str, object]] = []

    quota_records: list[QuotaRecord] = []
    for ec2_target in ec2_targets:
        quota_code = ec2_target["quota_code"]
        raw_quota = fetch_service_quota(
            service_code="ec2",
            quota_code=quota_code,
            aws_profile=aws_profile,
            aws_region=aws_region,
        )
        ec2_raw_quotas.append(raw_quota)
        quota_records.append(
            sanitize_quota_record(
                raw_quota,
                workload_profile=ec2_target["workload_profile"],
                required_vcpus=ec2_target["required_vcpus"],
            )
        )

    batch_records: list[BatchQuotaRecord] = []
    for batch_target in BATCH_QUOTA_TARGETS:
        quota_code = batch_target["quota_code"]
        raw_quota = fetch_service_quota(
            service_code="batch",
            quota_code=quota_code,
            aws_profile=aws_profile,
            aws_region=aws_region,
        )
        batch_raw_quotas.append(raw_quota)
        batch_records.append(sanitize_batch_quota_record(raw_quota))

    raw_payload["ec2_quotas"] = ec2_raw_quotas
    raw_payload["batch_quotas"] = batch_raw_quotas

    evidence = build_service_quotas_evidence(
        environment=environment,
        account_alias=account_alias,
        aws_region=aws_region,
        observed_at=observed_at,
        quota_records=quota_records,
        batch_records=batch_records,
    )
    return evidence, raw_payload


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_sanitized_evidence(path: Path, evidence: ContractModel) -> None:
    payload = evidence.model_dump(mode="json", by_alias=True, exclude_none=False)
    write_json(path, payload)


def capture_phase0_evidence(
    *,
    github_org: str,
    aws_profile: str,
    aws_region: str,
    environment: EvidenceEnvironment,
    output_dir: Path,
    base_dir: Path | None = None,
) -> tuple[GitHubPlanEvidence, ServiceQuotasEvidence]:
    resolved_output_dir = resolve_output_dir(output_dir, base_dir=base_dir)
    raw_dir = resolved_output_dir / "raw"
    sanitized_dir = resolved_output_dir / "sanitized"

    github_evidence, github_raw = capture_github_plan(github_org=github_org)
    quotas_evidence, quotas_raw = capture_service_quotas(
        aws_profile=aws_profile,
        aws_region=aws_region,
        environment=environment,
    )

    write_json(raw_dir / "github-org.json", github_raw)
    write_json(raw_dir / "service-quotas.json", quotas_raw)
    write_sanitized_evidence(sanitized_dir / "github-plan.sanitized.json", github_evidence)
    write_sanitized_evidence(sanitized_dir / "service-quotas.sanitized.json", quotas_evidence)
    return github_evidence, quotas_evidence


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture Phase 0 GitHub and AWS evidence.")
    parser.add_argument("--github-org", required=True)
    parser.add_argument("--aws-profile", required=True)
    parser.add_argument("--aws-region", required=True)
    parser.add_argument("--environment", choices=["sandbox"], required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    github_evidence, quotas_evidence = capture_phase0_evidence(
        github_org=args.github_org,
        aws_profile=args.aws_profile,
        aws_region=args.aws_region,
        environment=args.environment,
        output_dir=Path(args.output_dir),
    )
    print(
        json.dumps(
            {
                "github_plan": github_evidence.model_dump(mode="json"),
                "service_quotas": quotas_evidence.model_dump(mode="json"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
