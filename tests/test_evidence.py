from __future__ import annotations

import ast
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from edullm_platform.config import load_yaml
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.evidence import (
    AWS_ACCOUNT_ID_PATTERN,
    AWS_ACCOUNT_ID_PLACEHOLDER,
    EVIDENCE_STALE_CODE,
    BatchQuotaRecord,
    CapturedServiceQuotasEvidence,
    GitHubPlanEvidence,
    QuotaRecord,
    ServiceQuotasEvidence,
    evidence_load_reason_code,
    profiles_requiring_capacity_evidence,
    quota_capacity_issues,
    redact_aws_account_ids,
    redact_content_digests,
    scan_for_secrets,
)
from tools.capture_phase0_evidence import (
    allowed_output_root,
    assess_capacity_verdict,
    build_github_plan_evidence,
    build_service_quotas_evidence,
    capture_phase0_evidence,
    ec2_quota_targets_from_catalog,
    resolve_output_dir,
    run_command,
    sanitize_github_org,
    sanitize_github_repository,
    sanitize_quota_record,
    write_json,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PROJECT_ROOT / "fixtures" / "evidence"
AWS_EXAMPLE_ACCOUNT_ID = "123456789012"
# AWS's documented example keys, assembled at import so the literals never appear in
# the file. They authenticate nothing, but written out they match GitHub's secret
# scanning patterns and would block pushes touching this file.
AWS_EXAMPLE_ACCESS_KEY_ID = "AKIA" + "IOSFODNN7EXAMPLE"
AWS_EXAMPLE_TEMP_ACCESS_KEY_ID = "ASIA" + "IOSFODNN7EXAMPLE"
ALLOWED_ACCOUNT_ID_INTS = {int(AWS_EXAMPLE_ACCOUNT_ID)}
TRACKED_TREE_PATHS: tuple[str, ...] = ()
EXCLUDED_TRACKED_FILENAMES = frozenset({"uv.lock"})

# Content digests routinely carry twelve consecutive decimal digits. These two hold one
# on purpose, so a redaction that reached inside them would be visible.
ACCOUNT_ID_INSIDE_A_DIGEST = "a" * 26 + AWS_EXAMPLE_ACCOUNT_ID + "b" * 26
ACCOUNT_ID_INSIDE_A_COMMIT_SHA = "c" * 14 + AWS_EXAMPLE_ACCOUNT_ID + "d" * 14
# Forty characters of the secret-access-key alphabet wrapped around an account ID.
# Masking the account ID alone breaks the forty-character run that makes this a
# credential, and the scanner then accepts what is left.
SECRET_KEY_WRAPPING_AN_ACCOUNT_ID = "wJalrXUtnFEMIK" + AWS_EXAMPLE_ACCOUNT_ID + "bPxRfiCYEXAMPL"


def is_git_checkout(root: Path) -> bool:
    git_path = root / ".git"
    if not (git_path.is_dir() or git_path.is_file()):
        return False
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def tracked_tree_files() -> list[Path] | None:
    if not is_git_checkout(PROJECT_ROOT):
        return None
    args = ["git", "ls-files", *TRACKED_TREE_PATHS] if TRACKED_TREE_PATHS else ["git", "ls-files"]
    result = subprocess.run(
        args,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [
        PROJECT_ROOT / relative_path
        for relative_path in result.stdout.splitlines()
        if relative_path and Path(relative_path).name not in EXCLUDED_TRACKED_FILENAMES
    ]


def suspicious_account_id_int(value: int) -> bool:
    if value in ALLOWED_ACCOUNT_ID_INTS:
        return False
    digit_count = len(str(abs(value)))
    if digit_count == 12:
        return AWS_ACCOUNT_ID_PATTERN.search(str(value)) is not None
    if digit_count == 11:
        return AWS_ACCOUNT_ID_PATTERN.search(f"{value:012d}") is not None
    return False


def forbidden_account_id_substrings(source: str) -> list[str]:
    return [
        match.group(0)
        for match in AWS_ACCOUNT_ID_PATTERN.finditer(redact_content_digests(source))
        if match.group(0) != AWS_EXAMPLE_ACCOUNT_ID
    ]


def eval_string_concat(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = eval_string_concat(node.left)
        right = eval_string_concat(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def find_concatenated_account_id_substrings(source: str) -> list[str]:
    tree = ast.parse(source)
    matches: list[str] = []
    for node in ast.walk(tree):
        candidate_nodes: list[ast.AST] = []
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            candidate_nodes.append(node)
        if isinstance(node, ast.JoinedStr):
            candidate_nodes.extend(
                part.value for part in node.values if isinstance(part, ast.FormattedValue)
            )
        for candidate in candidate_nodes:
            value = eval_string_concat(candidate)
            if value is None:
                continue
            forbidden_ids = forbidden_account_id_substrings(value)
            matches.extend(forbidden_ids)
    return matches


def find_suspicious_account_id_ints(source: str) -> list[int]:
    tree = ast.parse(source)
    matches: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, int)
            and suspicious_account_id_int(node.value)
        ):
            matches.append(node.value)
    return matches


def assert_validation_error(
    error: ValidationError,
    *,
    loc_suffix: tuple[str | int, ...],
    error_type: str,
    message_fragment: str | None = None,
) -> None:
    matching_errors = [
        item
        for item in error.errors()
        if item["type"] == error_type and item["loc"][-len(loc_suffix) :] == loc_suffix
    ]
    assert matching_errors, (
        f"expected {error_type!r} at {loc_suffix!r}, got {error.errors()}"
    )
    if message_fragment is not None:
        assert any(message_fragment in item["msg"] for item in matching_errors), (
            f"expected {message_fragment!r} in {error_type!r} messages at {loc_suffix!r}, "
            f"got {[item['msg'] for item in matching_errors]}"
        )


def recent_observed_at() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stale_observed_at() -> str:
    stale = datetime.now(tz=UTC) - timedelta(days=31)
    return stale.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def github_plan_payload(**overrides: object) -> dict[str, object]:
    payload = {
        "source": "github",
        "environment": "sandbox",
        "organization": "edu-llm",
        "repository": "platform",
        "visibility": "public",
        "observed_at": recent_observed_at(),
        "status": "ok",
        "plan_name": "free",
    }
    payload.update(overrides)
    return payload


def service_quotas_payload(**overrides: object) -> dict[str, object]:
    payload = {
        "source": "aws",
        "environment": "sandbox",
        "account_alias": "dev-techsuperbuilders-sbsandbox",
        "region": "us-east-1",
        "observed_at": recent_observed_at(),
        "status": "ok",
        "capacity_verdict": "verified",
        "capacity_verdict_note": (
            "Sandbox applied quotas satisfy representative gpu-4xa10g and cpu-32vcpu "
            "profiles; does not attest production capacity."
        ),
        "quotas": [
            {
                "service_code": "ec2",
                "quota_code": "L-DB2E81BA",
                "quota_name": "Running On-Demand G and VT instances",
                "applied_value": 768,
                "unit": "vCPU",
                "quota_applied_at_level": "ACCOUNT",
                "workload_profile": "gpu-4xa10g",
                "required_vcpus": 48,
            },
            {
                "service_code": "ec2",
                "quota_code": "L-1216C47A",
                "quota_name": "Running On-Demand Standard (A, C, D, H, I, M, R, T, Z) instances",
                "applied_value": 1152,
                "unit": "vCPU",
                "quota_applied_at_level": "ACCOUNT",
                "workload_profile": "cpu-32vcpu",
                "required_vcpus": 32,
            },
        ],
        "batch_quotas": [
            {
                "service_code": "batch",
                "quota_code": "L-144F0CA5",
                "quota_name": "Compute environment limit",
                "applied_value": 50,
                "quota_applied_at_level": "ACCOUNT",
            },
            {
                "service_code": "batch",
                "quota_code": "L-4CEA37AD",
                "quota_name": "Job queue limit",
                "applied_value": 50,
                "quota_applied_at_level": "ACCOUNT",
            },
        ],
    }
    payload.update(overrides)
    return payload


def workload_catalog() -> WorkloadCatalog:
    return load_yaml(PROJECT_ROOT / "config" / "workload-catalog.yaml", WorkloadCatalog)


def test_github_plan_fixture_is_fresh_and_complete() -> None:
    payload = json.loads((FIXTURES_DIR / "github-plan.sanitized.json").read_text(encoding="utf-8"))
    evidence = GitHubPlanEvidence.model_validate(payload)
    assert evidence.organization == "edu-llm"
    assert evidence.repository == "platform"
    assert evidence.visibility == "public"
    assert evidence.plan_name == "free"


def test_service_quotas_fixture_is_fresh_and_complete() -> None:
    payload = json.loads(
        (FIXTURES_DIR / "service-quotas.sanitized.json").read_text(encoding="utf-8")
    )
    evidence = ServiceQuotasEvidence.model_validate(payload)
    assert evidence.environment == "sandbox"
    assert evidence.account_alias == "dev-techsuperbuilders-sbsandbox"
    assert evidence.capacity_verdict == "verified"


def test_github_plan_accepts_valid_payload() -> None:
    evidence = GitHubPlanEvidence.model_validate(github_plan_payload())
    assert evidence.plan_name == "free"


def test_service_quotas_accepts_valid_payload() -> None:
    evidence = ServiceQuotasEvidence.model_validate(service_quotas_payload())
    assert evidence.capacity_verdict == "verified"
    assert len(evidence.quotas) == 2


def test_github_plan_rejects_missing_observed_at() -> None:
    payload = github_plan_payload()
    del payload["observed_at"]
    with pytest.raises(ValidationError) as exc_info:
        GitHubPlanEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("observed_at",),
        error_type="missing",
    )


def test_github_plan_rejects_stale_observation() -> None:
    payload = github_plan_payload(observed_at=stale_observed_at())
    with pytest.raises(ValidationError) as exc_info:
        GitHubPlanEvidence.model_validate(payload)
    assert evidence_load_reason_code(exc_info.value) == EVIDENCE_STALE_CODE
    assert_validation_error(
        exc_info.value,
        loc_suffix=("observed_at",),
        error_type="value_error",
        message_fragment=EVIDENCE_STALE_CODE,
    )


def test_github_plan_rejects_future_observation() -> None:
    future = datetime.now(tz=UTC) + timedelta(days=1)
    observed_at = future.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = github_plan_payload(observed_at=observed_at)
    with pytest.raises(ValidationError) as exc_info:
        GitHubPlanEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("observed_at",),
        error_type="value_error",
        message_fragment="observation timestamps must not be in the future",
    )


def test_github_plan_rejects_timezone_naive_observation() -> None:
    payload = github_plan_payload(observed_at="2026-07-25T03:24:36")
    with pytest.raises(ValidationError) as exc_info:
        GitHubPlanEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("observed_at",),
        error_type="value_error",
        message_fragment="observation timestamps must be timezone-aware",
    )


def test_github_plan_rejects_empty_plan_name() -> None:
    payload = github_plan_payload(plan_name="")
    with pytest.raises(ValidationError) as exc_info:
        GitHubPlanEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("plan_name",),
        error_type="string_too_short",
    )


def test_service_quotas_rejects_missing_observed_at() -> None:
    payload = service_quotas_payload()
    del payload["observed_at"]
    with pytest.raises(ValidationError) as exc_info:
        ServiceQuotasEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("observed_at",),
        error_type="missing",
    )


def test_service_quotas_rejects_stale_observation() -> None:
    payload = service_quotas_payload(observed_at=stale_observed_at())
    with pytest.raises(ValidationError) as exc_info:
        ServiceQuotasEvidence.model_validate(payload)
    assert evidence_load_reason_code(exc_info.value) == EVIDENCE_STALE_CODE
    assert_validation_error(
        exc_info.value,
        loc_suffix=("observed_at",),
        error_type="value_error",
        message_fragment=EVIDENCE_STALE_CODE,
    )


def test_quota_capacity_issues_uses_authoritative_required_vcpus_not_self_reported() -> None:
    payload = service_quotas_payload()
    quotas = list(payload["quotas"])  # type: ignore[arg-type]
    quotas[0]["required_vcpus"] = 1
    quotas[0]["applied_value"] = 2.0
    evidence = ServiceQuotasEvidence.model_validate(payload)
    incomplete, insufficient = quota_capacity_issues(evidence.quotas)
    assert incomplete is False
    assert insufficient == [
        "gpu-4xa10g requires 48 vCPU but L-DB2E81BA applied quota is 2"
    ]


def test_service_quotas_base_model_allows_unjustified_verified_verdict() -> None:
    payload = service_quotas_payload()
    quotas = list(payload["quotas"])  # type: ignore[arg-type]
    quotas[0]["applied_value"] = 1.0
    payload["quotas"] = quotas
    evidence = ServiceQuotasEvidence.model_validate(payload)
    assert evidence.capacity_verdict == "verified"


def test_service_quotas_rejects_missing_quota_code() -> None:
    payload = service_quotas_payload()
    quotas = list(payload["quotas"])  # type: ignore[arg-type]
    del quotas[0]["quota_code"]
    payload["quotas"] = quotas
    with pytest.raises(ValidationError) as exc_info:
        ServiceQuotasEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("quotas", 0, "quota_code"),
        error_type="missing",
    )


def test_service_quotas_rejects_non_positive_applied_value() -> None:
    payload = service_quotas_payload()
    quotas = list(payload["quotas"])  # type: ignore[arg-type]
    quotas[0]["applied_value"] = 0
    payload["quotas"] = quotas
    with pytest.raises(ValidationError) as exc_info:
        ServiceQuotasEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("quotas", 0, "applied_value"),
        error_type="greater_than",
    )


def test_service_quotas_rejects_default_level_quota_claim() -> None:
    payload = service_quotas_payload()
    quotas = list(payload["quotas"])  # type: ignore[arg-type]
    quotas[0]["quota_applied_at_level"] = "DEFAULT"
    payload["quotas"] = quotas
    with pytest.raises(ValidationError) as exc_info:
        ServiceQuotasEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("quotas", 0, "quota_applied_at_level"),
        error_type="literal_error",
    )


def test_service_quotas_rejects_missing_environment() -> None:
    payload = service_quotas_payload()
    del payload["environment"]
    with pytest.raises(ValidationError) as exc_info:
        ServiceQuotasEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("environment",),
        error_type="missing",
    )


def test_service_quotas_rejects_non_sandbox_environment() -> None:
    payload = service_quotas_payload(environment="production")
    with pytest.raises(ValidationError) as exc_info:
        ServiceQuotasEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("environment",),
        error_type="literal_error",
    )


def test_service_quotas_rejects_missing_account_alias() -> None:
    payload = service_quotas_payload()
    del payload["account_alias"]
    with pytest.raises(ValidationError) as exc_info:
        ServiceQuotasEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("account_alias",),
        error_type="missing",
    )


def test_service_quotas_rejects_unjustified_verified_verdict() -> None:
    payload = service_quotas_payload()
    quotas = list(payload["quotas"])  # type: ignore[arg-type]
    quotas[0]["applied_value"] = 1.0
    payload["quotas"] = quotas
    with pytest.raises(ValidationError) as exc_info:
        CapturedServiceQuotasEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=(),
        error_type="value_error",
        message_fragment="verified verdict requires every quota record to cover its required vcpus",
    )


def test_service_quotas_rejects_increase_required_without_insufficient_quota() -> None:
    payload = service_quotas_payload(
        capacity_verdict="increase_required",
        capacity_verdict_note="Sandbox quota increase required before Phase 1: test",
    )
    with pytest.raises(ValidationError) as exc_info:
        CapturedServiceQuotasEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=(),
        error_type="value_error",
        message_fragment="increase_required verdict requires at least one insufficient quota",
    )


def test_service_quotas_accepts_blocked_for_non_quota_reason() -> None:
    payload = service_quotas_payload(
        capacity_verdict="blocked",
        capacity_verdict_note=(
            "Regional g5.12xlarge capacity unavailable in us-east-1 during review."
        ),
    )
    evidence = CapturedServiceQuotasEvidence.model_validate(payload)
    assert evidence.capacity_verdict == "blocked"


def test_service_quotas_rejects_blocked_without_reason_note() -> None:
    payload = service_quotas_payload(
        capacity_verdict="blocked",
        capacity_verdict_note="   ",
    )
    with pytest.raises(ValidationError) as exc_info:
        CapturedServiceQuotasEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=(),
        error_type="value_error",
        message_fragment="blocked verdict requires a non-empty reason note",
    )


def test_github_plan_rejects_secrets_in_plan_name() -> None:
    payload = github_plan_payload(plan_name=AWS_EXAMPLE_ACCESS_KEY_ID)
    with pytest.raises(ValidationError) as exc_info:
        GitHubPlanEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("plan_name",),
        error_type="value_error",
        message_fragment="must not contain credentials or raw AWS account IDs",
    )


@pytest.mark.parametrize(
    ("field", "secret_value"),
    [
        ("capacity_verdict_note", AWS_EXAMPLE_TEMP_ACCESS_KEY_ID),
        ("account_alias", AWS_EXAMPLE_ACCOUNT_ID),
    ],
)
def test_service_quotas_reject_secrets_and_account_ids(field: str, secret_value: str) -> None:
    payload = service_quotas_payload(**{field: secret_value})
    with pytest.raises(ValidationError) as exc_info:
        ServiceQuotasEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=(field,),
        error_type="value_error",
        message_fragment="must not contain credentials or raw AWS account IDs",
    )


@pytest.mark.parametrize(
    ("probe", "value"),
    [
        ("embedded account id", f"acct_{AWS_EXAMPLE_ACCOUNT_ID}_prod"),
        ("lowercase access key", "prefix akiaiosfodnn7example suffix"),
        ("40-char secret key", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
        ("github pat", "github_pat_11AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"),
        ("github ghs token", "ghs_abcdefghijklmnopqrstuvwxyz1234567890ABCD"),
        ("github ghu token", "ghu_abcdefghijklmnopqrstuvwxyz1234567890ABCD"),
        ("pem header", "-----BEGIN RSA PRIVATE KEY-----"),
        ("bearer token", "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"),
        (
            "sts prefix only",
            "FwoGZXIv" + "A" * 20,
        ),
        (
            "sts token FQo",
            "FQoGZXIvYXdzEBQaD"
            + "A" * 60,
        ),
        (
            "sts token AQo",
            "AQoDYXdzEJr"
            + "B" * 60,
        ),
        (
            "sts token IQo",
            "IQoDYXdzEjr"
            + "C" * 60,
        ),
        (
            "jwt",
            (
                "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
                "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
                "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
            ),
        ),
    ],
)
def test_scan_for_secrets_rejects_additional_credential_patterns(probe: str, value: str) -> None:
    with pytest.raises(ValueError, match="must not contain credentials or raw AWS account IDs"):
        scan_for_secrets(value)


def test_service_quotas_rejects_secrets_in_quota_unit() -> None:
    payload = service_quotas_payload()
    quotas = list(payload["quotas"])  # type: ignore[arg-type]
    quotas[0]["unit"] = f"acct_{AWS_EXAMPLE_ACCOUNT_ID}_prod"
    payload["quotas"] = quotas
    with pytest.raises(ValidationError) as exc_info:
        ServiceQuotasEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("quotas", 0, "unit"),
        error_type="value_error",
        message_fragment="must not contain credentials or raw AWS account IDs",
    )


@pytest.mark.parametrize("field", ["organization"])
def test_github_plan_rejects_secrets_in_scanned_fields(field: str) -> None:
    payload = github_plan_payload(**{field: AWS_EXAMPLE_ACCESS_KEY_ID})
    with pytest.raises(ValidationError) as exc_info:
        GitHubPlanEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=(field,),
        error_type="value_error",
        message_fragment="must not contain credentials or raw AWS account IDs",
    )


@pytest.mark.parametrize("field", ["region"])
def test_service_quotas_rejects_secrets_in_scanned_fields(field: str) -> None:
    payload = service_quotas_payload(**{field: AWS_EXAMPLE_ACCESS_KEY_ID})
    with pytest.raises(ValidationError) as exc_info:
        ServiceQuotasEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=(field,),
        error_type="value_error",
        message_fragment="must not contain credentials or raw AWS account IDs",
    )


@pytest.mark.parametrize("field", ["quota_code", "quota_name", "workload_profile"])
def test_quota_records_reject_secrets_in_scanned_fields(field: str) -> None:
    payload = service_quotas_payload()
    quotas = list(payload["quotas"])  # type: ignore[arg-type]
    quotas[0][field] = AWS_EXAMPLE_ACCESS_KEY_ID
    payload["quotas"] = quotas
    with pytest.raises(ValidationError) as exc_info:
        ServiceQuotasEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("quotas", 0, field),
        error_type="value_error",
        message_fragment="must not contain credentials or raw AWS account IDs",
    )


def test_scan_for_secrets_rejects_access_key() -> None:
    with pytest.raises(ValueError, match="must not contain credentials or raw AWS account IDs"):
        scan_for_secrets(f"prefix {AWS_EXAMPLE_ACCESS_KEY_ID} suffix")


def test_sanitize_github_org_extracts_plan_name() -> None:
    raw = {
        "login": "edu-llm",
        "plan": {"name": "free", "space": 1, "private_repos": 1, "filled_seats": 1, "seats": 1},
    }
    organization, plan_name = sanitize_github_org(raw, observed_at=recent_observed_at())
    assert plan_name == "free"
    assert organization == "edu-llm"


def test_sanitize_github_repository_extracts_visibility() -> None:
    raw = {"name": "platform", "visibility": "public"}
    repository, visibility = sanitize_github_repository(raw)
    assert repository == "platform"
    assert visibility == "public"


def test_build_github_plan_evidence_combines_org_and_repository() -> None:
    evidence = build_github_plan_evidence(
        organization="edu-llm",
        plan_name="free",
        repository="platform",
        visibility="public",
        observed_at=recent_observed_at(),
    )
    assert evidence.repository == "platform"
    assert evidence.visibility == "public"
    assert evidence.plan_name == "free"


def test_sanitize_quota_record_rejects_default_level_quota() -> None:
    raw = {
        "ServiceCode": "ec2",
        "QuotaCode": "L-DB2E81BA",
        "QuotaName": "Running On-Demand G and VT instances",
        "Value": 768.0,
        "Unit": "None",
        "QuotaAppliedAtLevel": "DEFAULT",
    }
    with pytest.raises(ValueError, match="quota evidence must use account-level applied quotas"):
        sanitize_quota_record(raw, workload_profile="gpu-4xa10g", required_vcpus=48)


def test_assess_capacity_verdict_blocked_when_mapping_incomplete() -> None:
    payload = service_quotas_payload(
        capacity_verdict="blocked",
        capacity_verdict_note=(
            "Capacity review blocked because representative workload mapping is incomplete."
        ),
    )
    quotas = list(payload["quotas"])  # type: ignore[arg-type]
    quotas[0]["workload_profile"] = None
    payload["quotas"] = quotas
    evidence = ServiceQuotasEvidence.model_validate(payload)
    verdict, note = assess_capacity_verdict(evidence.quotas)
    assert verdict == "blocked"
    assert "incomplete" in note


def test_sanitize_quota_record_requires_account_level_applied_quota() -> None:
    raw = {
        "ServiceCode": "ec2",
        "QuotaCode": "L-DB2E81BA",
        "QuotaName": "Running On-Demand G and VT instances",
        "Value": 768.0,
        "Unit": "None",
        "QuotaAppliedAtLevel": "ACCOUNT",
    }
    record = sanitize_quota_record(
        raw,
        workload_profile="gpu-4xa10g",
        required_vcpus=48,
    )
    assert record.quota_code == "L-DB2E81BA"
    assert record.applied_value == 768.0


def test_assess_capacity_verdict_verified_when_quotas_cover_profiles() -> None:
    payload = service_quotas_payload()
    evidence = ServiceQuotasEvidence.model_validate(payload)
    verdict, note = assess_capacity_verdict(evidence.quotas)
    assert verdict == "verified"
    assert "gpu-4xa10g and cpu-32vcpu" in note
    assert "does not attest production capacity" in note


def test_assess_capacity_verdict_increase_required_when_gpu_quota_insufficient() -> None:
    payload = service_quotas_payload(
        capacity_verdict="increase_required",
        capacity_verdict_note="Sandbox quota increase required before Phase 1: pending",
    )
    quotas = list(payload["quotas"])  # type: ignore[arg-type]
    quotas[0]["applied_value"] = 16
    payload["quotas"] = quotas
    evidence = ServiceQuotasEvidence.model_validate(payload)
    verdict, _note = assess_capacity_verdict(evidence.quotas)
    assert verdict == "increase_required"


def test_ec2_quota_targets_derive_from_workload_catalog() -> None:
    catalog = workload_catalog()
    targets = ec2_quota_targets_from_catalog(catalog)
    profiles = {target["workload_profile"] for target in targets}
    assert profiles == {
        profile.name for profile in profiles_requiring_capacity_evidence(catalog)
    }
    gpu_target = next(target for target in targets if target["workload_profile"] == "gpu-4xa10g")
    cpu_target = next(target for target in targets if target["workload_profile"] == "cpu-32vcpu")
    assert gpu_target["instance_type"] == "g5.12xlarge"
    assert gpu_target["required_vcpus"] == 48
    assert cpu_target["instance_type"] == "c7i.8xlarge"
    assert cpu_target["required_vcpus"] == 32


def test_build_service_quotas_evidence_scans_verdict_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def bad_assess(_quotas: tuple[QuotaRecord, ...]) -> tuple[str, str]:
        return ("increase_required", f"token {AWS_EXAMPLE_TEMP_ACCESS_KEY_ID}")

    monkeypatch.setattr("tools.capture_phase0_evidence.assess_capacity_verdict", bad_assess)
    with pytest.raises(ValidationError) as exc_info:
        build_service_quotas_evidence(
            environment="sandbox",
            account_alias="dev-techsuperbuilders-sbsandbox",
            aws_region="us-east-1",
            observed_at=recent_observed_at(),
            quota_records=[
                QuotaRecord.model_validate(
                    {
                        "service_code": "ec2",
                        "quota_code": "L-DB2E81BA",
                        "quota_name": "Running On-Demand G and VT instances",
                        "applied_value": 16,
                        "unit": "vCPU",
                        "quota_applied_at_level": "ACCOUNT",
                        "workload_profile": "gpu-4xa10g",
                        "required_vcpus": 48,
                    }
                )
            ],
            batch_records=[
                BatchQuotaRecord.model_validate(
                    {
                        "service_code": "batch",
                        "quota_code": "L-144F0CA5",
                        "quota_name": "Compute environment limit",
                        "applied_value": 50,
                        "quota_applied_at_level": "ACCOUNT",
                    }
                )
            ],
        )
    assert_validation_error(
        exc_info.value,
        loc_suffix=("capacity_verdict_note",),
        error_type="value_error",
        message_fragment="must not contain credentials or raw AWS account IDs",
    )


def test_build_service_quotas_evidence_revalidates_verdict_fields() -> None:
    evidence = build_service_quotas_evidence(
        environment="sandbox",
        account_alias="dev-techsuperbuilders-sbsandbox",
        aws_region="us-east-1",
        observed_at=recent_observed_at(),
        quota_records=[
            QuotaRecord.model_validate(
                {
                    "service_code": "ec2",
                    "quota_code": "L-DB2E81BA",
                    "quota_name": "Running On-Demand G and VT instances",
                    "applied_value": 16,
                    "unit": "vCPU",
                    "quota_applied_at_level": "ACCOUNT",
                    "workload_profile": "gpu-4xa10g",
                    "required_vcpus": 48,
                }
            )
        ],
        batch_records=[
            BatchQuotaRecord.model_validate(
                {
                    "service_code": "batch",
                    "quota_code": "L-144F0CA5",
                    "quota_name": "Compute environment limit",
                    "applied_value": 50,
                    "quota_applied_at_level": "ACCOUNT",
                }
            )
        ],
    )
    assert evidence.capacity_verdict == "increase_required"
    assert "gpu-4xa10g requires 48 vCPU" in evidence.capacity_verdict_note


def test_resolve_output_dir_accepts_allowed_root() -> None:
    resolved = resolve_output_dir(
        Path("docs-frank/working/phase-0-evidence"),
        base_dir=PROJECT_ROOT,
    )
    assert resolved == allowed_output_root(PROJECT_ROOT)


def test_resolve_output_dir_rejects_path_outside_allowed_root() -> None:
    with pytest.raises(ValueError, match="output_dir must be under docs-frank/working/phase-0-evidence/"):
        resolve_output_dir(Path("fixtures/evidence"), base_dir=PROJECT_ROOT)


def test_run_command_uses_no_shell_and_checks_return_code(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, Any] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed["args"] = args
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(list(args[0]), 1, stdout="", stderr="boom")  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="boom"):
        run_command(["gh", "api", "orgs/edu-llm"])
    assert observed["kwargs"]["shell"] is False


def test_run_command_returns_parsed_json_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(args[0]), 0, stdout='{"ok": true}', stderr="")  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert run_command(["gh", "api", "orgs/edu-llm"]) == {"ok": True}


def test_capture_phase0_evidence_writes_under_allowed_output_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed = tmp_path / "docs-frank" / "working" / "phase-0-evidence"
    allowed.mkdir(parents=True)
    forbidden = tmp_path / "fixtures" / "evidence"
    forbidden.mkdir(parents=True)

    github_evidence = GitHubPlanEvidence.model_validate(github_plan_payload())
    quotas_evidence = ServiceQuotasEvidence.model_validate(service_quotas_payload())
    written: list[Path] = []

    monkeypatch.setattr(
        "tools.capture_phase0_evidence.capture_github_plan",
        lambda **_: (
            github_evidence,
            {"organization": {"login": "edu-llm"}, "repository": {"name": "platform"}},
        ),
    )
    monkeypatch.setattr(
        "tools.capture_phase0_evidence.capture_service_quotas",
        lambda **_: (quotas_evidence, {"account_alias": "dev-techsuperbuilders-sbsandbox"}),
    )

    def record_write_json(path: Path, payload: object) -> None:
        written.append(path)
        write_json(path, payload)

    monkeypatch.setattr("tools.capture_phase0_evidence.write_json", record_write_json)

    capture_phase0_evidence(
        github_org="edu-llm",
        aws_profile="sbsandbox",
        aws_region="us-east-1",
        environment="sandbox",
        output_dir=Path("docs-frank/working/phase-0-evidence"),
        base_dir=tmp_path,
    )
    assert written
    assert all(path.resolve().is_relative_to(allowed.resolve()) for path in written)

    with pytest.raises(ValueError, match="output_dir must be under docs-frank/working/phase-0-evidence/"):
        capture_phase0_evidence(
            github_org="edu-llm",
            aws_profile="sbsandbox",
            aws_region="us-east-1",
            environment="sandbox",
            output_dir=forbidden,
            base_dir=tmp_path,
        )


def test_digit_runs_inside_content_digests_are_not_account_ids() -> None:
    inside_sha256 = "sha256:31eacaa510964426782f8e5f8c7be431880538739ea3c5c7a94cc66340621ca1f"
    inside_commit_sha = "a8727f150891357935b660adafba82b94046dc28"
    assert forbidden_account_id_substrings(inside_sha256) == []
    assert forbidden_account_id_substrings(inside_commit_sha) == []


def test_the_digest_exemption_still_reports_a_bare_account_id() -> None:
    synthetic = AWS_EXAMPLE_ACCOUNT_ID[::-1]
    assert len(synthetic) == 12
    assert synthetic != AWS_EXAMPLE_ACCOUNT_ID
    bare = f"arn:aws:iam::{synthetic}:role/sbsandbox-intern-example"
    alongside_a_digest = f"sha256:{'a' * 64} {synthetic}"
    assert forbidden_account_id_substrings(bare) == [synthetic]
    assert forbidden_account_id_substrings(alongside_a_digest) == [synthetic]


def test_redaction_masks_an_account_id_that_free_text_cannot_avoid() -> None:
    message = (
        f"User: arn:aws:sts::{AWS_EXAMPLE_ACCOUNT_ID}:assumed-role/"
        "sbsandbox-intern-edullm-ecr-publisher/publish is not authorized to perform "
        "batch:SubmitJob"
    )
    redacted = redact_aws_account_ids(message)
    assert AWS_EXAMPLE_ACCOUNT_ID not in redacted
    assert AWS_ACCOUNT_ID_PLACEHOLDER in redacted
    assert "assumed-role/sbsandbox-intern-edullm-ecr-publisher" in redacted
    assert scan_for_secrets(redacted) == redacted


@pytest.mark.parametrize("padding", ["", "0", "00", "9876"])
def test_adjacent_digits_do_not_defeat_the_account_id_redaction(padding: str) -> None:
    # Only the unpadded spelling is what SECRET_PATTERNS calls an account ID. Every
    # padded one hides the same twelve digits from the scanner, so a mask that matched
    # the scanner exactly would hand back text the scanner then waves through.
    text = f"account {padding}{AWS_EXAMPLE_ACCOUNT_ID}{padding} denied"
    redacted = redact_aws_account_ids(text)
    assert AWS_EXAMPLE_ACCOUNT_ID not in redacted
    assert AWS_ACCOUNT_ID_PLACEHOLDER in redacted
    assert scan_for_secrets(redacted) == redacted


def test_redaction_leaves_a_content_digest_and_a_commit_sha_intact() -> None:
    text = f"image sha256:{ACCOUNT_ID_INSIDE_A_DIGEST} built from {ACCOUNT_ID_INSIDE_A_COMMIT_SHA}"
    assert redact_aws_account_ids(text) == text
    assert scan_for_secrets(redact_content_digests(text)) == redact_content_digests(text)


def test_redaction_refuses_a_credential_a_naive_mask_would_have_hidden() -> None:
    with pytest.raises(ValueError, match="refusing to redact text that carries a credential"):
        redact_aws_account_ids(SECRET_KEY_WRAPPING_AN_ACCOUNT_ID)
    naive = AWS_ACCOUNT_ID_PATTERN.sub(
        AWS_ACCOUNT_ID_PLACEHOLDER,
        SECRET_KEY_WRAPPING_AN_ACCOUNT_ID,
    )
    assert scan_for_secrets(naive) == naive


@pytest.mark.parametrize(
    ("probe", "credential"),
    [
        ("access key id", AWS_EXAMPLE_ACCESS_KEY_ID),
        ("temporary access key id", AWS_EXAMPLE_TEMP_ACCESS_KEY_ID),
        ("github token", "ghp_" + "a" * 36),
        ("private key header", "-----BEGIN RSA PRIVATE KEY-----"),
        ("bearer token", "Bearer abc123DEF456ghi789"),
    ],
)
def test_redaction_refuses_any_text_that_carries_a_credential(
    probe: str,
    credential: str,
) -> None:
    with pytest.raises(ValueError, match="refusing to redact text that carries a credential"):
        redact_aws_account_ids(f"account {AWS_EXAMPLE_ACCOUNT_ID} used {credential}")


def test_the_placeholder_does_not_fuse_its_neighbours_into_a_credential() -> None:
    text = "A" * 20 + AWS_EXAMPLE_ACCOUNT_ID + "B" * 20
    redacted = redact_aws_account_ids(text)
    assert scan_for_secrets(redacted) == redacted
    with pytest.raises(ValueError, match="must not contain credentials or raw AWS account IDs"):
        scan_for_secrets("A" * 20 + "B" * 20)


def test_redaction_masks_every_account_id_in_one_pass() -> None:
    other = AWS_EXAMPLE_ACCOUNT_ID[::-1]
    redacted = redact_aws_account_ids(f"source {AWS_EXAMPLE_ACCOUNT_ID} target {other} done")
    assert redacted == (
        f"source {AWS_ACCOUNT_ID_PLACEHOLDER} target {AWS_ACCOUNT_ID_PLACEHOLDER} done"
    )


@pytest.mark.slow
def test_tracked_tree_contains_no_aws_account_id_patterns() -> None:
    tracked_files = tracked_tree_files()
    if tracked_files is None:
        pytest.skip("not in a git checkout")

    pattern_matches: list[str] = []
    int_matches: list[str] = []
    concat_matches: list[str] = []
    for path in tracked_files:
        source = path.read_text(encoding="utf-8")
        forbidden_ids = forbidden_account_id_substrings(source)
        if forbidden_ids:
            pattern_matches.append(
                f"{path.relative_to(PROJECT_ROOT)}: {forbidden_ids}"
            )
        if path.suffix != ".py":
            continue
        suspicious_ints = find_suspicious_account_id_ints(source)
        if suspicious_ints:
            int_matches.append(
                f"{path.relative_to(PROJECT_ROOT)}: {suspicious_ints}"
            )
        concatenated_ids = find_concatenated_account_id_substrings(source)
        if concatenated_ids:
            concat_matches.append(
                f"{path.relative_to(PROJECT_ROOT)}: {concatenated_ids}"
            )

    assert not pattern_matches, f"12-digit account ID pattern matched in {pattern_matches}"
    assert not int_matches, f"reconstructible account ID ints found in {int_matches}"
    assert not concat_matches, f"concatenated account ID substrings found in {concat_matches}"


def test_tracked_tree_guard_skips_outside_git_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        f"{__name__}.is_git_checkout",
        lambda _root: False,
    )
    assert tracked_tree_files() is None
