from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from edullm_platform.canonical import canonical_json_bytes
from edullm_platform.contracts.base import ContractModel
from edullm_platform.evidence import (
    EVIDENCE_STALE_CODE,
    evidence_load_reason_code,
    redact_aws_account_ids,
    scan_for_secrets,
)
from edullm_platform.phase1_evidence import (
    BuildProvenanceEvidence,
    DenialEvidence,
    EcrImageEvidence,
    ImageScanEvidence,
    OidcSessionEvidence,
)

AWS_EXAMPLE_ACCOUNT_ID = "123456789012"
# AWS's documented example key, assembled at import so the literal never appears in the
# file. It authenticates nothing, but written out it matches GitHub's secret scanning
# patterns and would block pushes touching this file.
AWS_EXAMPLE_ACCESS_KEY_ID = "AKIA" + "IOSFODNN7EXAMPLE"

COMMIT_SHA = "9f2c1d4e" + "0" * 32
IMAGE_TAG = COMMIT_SHA[:12]
IMAGE_DIGEST = "sha256:" + "1a" * 32
BASE_IMAGE_DIGEST = "sha256:" + "2b" * 32
ECR_REPOSITORY = "sbsandbox-intern-edullm-olmo-core"
PUBLISHER_ROLE_NAME = "sbsandbox-intern-edullm-ecr-publisher"
CLOUDTRAIL_EVENT_ID = "7c1f2a3b-4d5e-4f60-a71b-8c9d0e1f2a3b"
OTHER_CLOUDTRAIL_EVENT_ID = "5e4d3c2b-1a0f-4e9d-b8c7-6a5b4c3d2e1f"
OIDC_SUBJECT = "repo:edu-llm@306859726/OLMo-core@1306868157:ref:refs/heads/pin-the-base-image"

#: The shape of an STS denial, with the account ID the message cannot avoid. The
#: capture tool is expected to redact it; the unredacted spelling is kept here so a
#: test can prove the contract refuses it.
RAW_DENIAL_MESSAGE = (
    f"User: arn:aws:sts::{AWS_EXAMPLE_ACCOUNT_ID}:assumed-role/{PUBLISHER_ROLE_NAME}/"
    "build-research-image is not authorized to perform: batch:SubmitJob because no "
    "identity-based policy allows the batch:SubmitJob action"
)
REDACTED_DENIAL_MESSAGE = redact_aws_account_ids(RAW_DENIAL_MESSAGE)

PayloadBuilder = Callable[..., dict[str, object]]


def recent_observed_at() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stale_observed_at() -> str:
    stale = datetime.now(tz=UTC) - timedelta(days=31)
    return stale.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def moments_ago(seconds: int) -> str:
    earlier = datetime.now(tz=UTC).replace(microsecond=0) - timedelta(seconds=seconds)
    return earlier.isoformat().replace("+00:00", "Z")


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
    if not loc_suffix:
        matching_errors = [item for item in error.errors() if item["type"] == error_type]
    assert matching_errors, f"expected {error_type!r} at {loc_suffix!r}, got {error.errors()}"
    if message_fragment is not None:
        assert any(message_fragment in item["msg"] for item in matching_errors), (
            f"expected {message_fragment!r} in {error_type!r} messages at {loc_suffix!r}, "
            f"got {[item['msg'] for item in matching_errors]}"
        )


def workflow_run_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "run_repository": "edu-llm/OLMo-core",
        "workflow_repository": "edu-llm/platform",
        "workflow_path": ".github/workflows/build-research-image.yml",
        "workflow_ref": "refs/heads/main",
        "run_id": 1234567890,
        "run_attempt": 1,
    }
    payload.update(overrides)
    return payload


def source_identity_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "repository": "OLMo-core",
        "github_repository_id": 1306868157,
        "ref": "refs/heads/pin-the-base-image",
        "commit_sha": COMMIT_SHA,
        "clean": True,
        "verified": True,
    }
    payload.update(overrides)
    return payload


def build_provenance_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "source": "github",
        "environment": "sandbox",
        "observed_at": recent_observed_at(),
        "status": "ok",
        "workflow_run": workflow_run_payload(),
        "source_identity": source_identity_payload(),
        "image_digest": IMAGE_DIGEST,
        "run_conclusion": "success",
        "run_completed_at": moments_ago(60),
    }
    payload.update(overrides)
    return payload


def ecr_image_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "source": "aws",
        "environment": "sandbox",
        "observed_at": recent_observed_at(),
        "status": "ok",
        "region": "us-east-1",
        "repository_name": ECR_REPOSITORY,
        "image_digest": IMAGE_DIGEST,
        "image_tag": IMAGE_TAG,
        "base_image_digest": BASE_IMAGE_DIGEST,
        "image_pushed_at": moments_ago(120),
    }
    payload.update(overrides)
    return payload


def finding_counts_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "critical": 0,
        "high": 1,
        "medium": 2,
        "low": 3,
        "informational": 4,
        "undefined": 0,
    }
    payload.update(overrides)
    return payload


def image_scan_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "source": "aws",
        "environment": "sandbox",
        "observed_at": recent_observed_at(),
        "status": "ok",
        "region": "us-east-1",
        "repository_name": ECR_REPOSITORY,
        "image_digest": IMAGE_DIGEST,
        "scan_status": "COMPLETE",
        "scan_status_description": "The scan was completed successfully.",
        "scan_completed_at": moments_ago(90),
        "finding_counts": finding_counts_payload(),
    }
    payload.update(overrides)
    return payload


def oidc_session_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "source": "aws",
        "environment": "sandbox",
        "observed_at": recent_observed_at(),
        "status": "ok",
        "region": "us-east-1",
        "event_id": CLOUDTRAIL_EVENT_ID,
        "event_name": "AssumeRoleWithWebIdentity",
        "event_source": "sts.amazonaws.com",
        "role_name": PUBLISHER_ROLE_NAME,
        "session_name": "build-research-image",
        "oidc_issuer": "token.actions.githubusercontent.com",
        "oidc_audience": "sts.amazonaws.com",
        "oidc_subject": OIDC_SUBJECT,
        "assumed_at": moments_ago(3600),
        "expires_at": recent_observed_at(),
    }
    payload.update(overrides)
    return payload


def denial_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "source": "aws",
        "environment": "sandbox",
        "observed_at": recent_observed_at(),
        "status": "ok",
        "region": "us-east-1",
        "role_name": PUBLISHER_ROLE_NAME,
        "session_name": "build-research-image",
        "attempted_action": "batch:SubmitJob",
        "attempted_resource": "sbsandbox-intern-edullm-gpu-queue",
        "attempted_at": moments_ago(1800),
        "outcome": "denied",
        "error_code": "AccessDeniedException",
        "error_message": REDACTED_DENIAL_MESSAGE,
        "event_id": OTHER_CLOUDTRAIL_EVENT_ID,
        "event_name": "SubmitJob",
        "event_source": "batch.amazonaws.com",
    }
    payload.update(overrides)
    return payload


EVIDENCE_MODELS: tuple[tuple[type[ContractModel], PayloadBuilder], ...] = (
    (BuildProvenanceEvidence, build_provenance_payload),
    (EcrImageEvidence, ecr_image_payload),
    (ImageScanEvidence, image_scan_payload),
    (OidcSessionEvidence, oidc_session_payload),
    (DenialEvidence, denial_payload),
)

EVIDENCE_MODEL_IDS = [model_type.__name__ for model_type, _builder in EVIDENCE_MODELS]

#: Fields whose legitimate values are close enough to a twelve-digit account ID that a
#: reader could not tell one from the other, so each is proved to refuse one.
ACCOUNT_ID_PROBES: tuple[tuple[type[ContractModel], PayloadBuilder, str, str], ...] = (
    (EcrImageEvidence, ecr_image_payload, "image_tag", AWS_EXAMPLE_ACCOUNT_ID),
    (
        ImageScanEvidence,
        image_scan_payload,
        "scan_status_description",
        f"scan of arn:aws:ecr:us-east-1:{AWS_EXAMPLE_ACCOUNT_ID}:repository/x failed",
    ),
    (
        OidcSessionEvidence,
        oidc_session_payload,
        "event_id",
        f"{'a' * 8}-aaaa-4aaa-baaa-{AWS_EXAMPLE_ACCOUNT_ID}",
    ),
    (OidcSessionEvidence, oidc_session_payload, "session_name", AWS_EXAMPLE_ACCOUNT_ID),
    (
        OidcSessionEvidence,
        oidc_session_payload,
        "oidc_subject",
        f"repo:edu-llm@{AWS_EXAMPLE_ACCOUNT_ID}/OLMo-core@1306868157:ref:refs/heads/main",
    ),
    (DenialEvidence, denial_payload, "error_message", RAW_DENIAL_MESSAGE),
    (DenialEvidence, denial_payload, "role_name", AWS_EXAMPLE_ACCOUNT_ID),
    (
        DenialEvidence,
        denial_payload,
        "attempted_resource",
        f"arn:aws:batch:us-east-1:{AWS_EXAMPLE_ACCOUNT_ID}:job-queue/gpu",
    ),
)

ACCOUNT_ID_PROBE_IDS = [
    f"{model_type.__name__}.{field}" for model_type, _builder, field, _value in ACCOUNT_ID_PROBES
]

CREDENTIAL_PROBES: tuple[tuple[type[ContractModel], PayloadBuilder, str], ...] = (
    (ImageScanEvidence, image_scan_payload, "scan_status_description"),
    (OidcSessionEvidence, oidc_session_payload, "oidc_subject"),
    (DenialEvidence, denial_payload, "error_message"),
)

CREDENTIAL_PROBE_IDS = [
    f"{model_type.__name__}.{field}" for model_type, _builder, field in CREDENTIAL_PROBES
]


@pytest.mark.parametrize(("model_type", "build_payload"), EVIDENCE_MODELS, ids=EVIDENCE_MODEL_IDS)
def test_every_model_round_trips_through_canonical_json(
    model_type: type[ContractModel],
    build_payload: PayloadBuilder,
) -> None:
    evidence = model_type.model_validate(build_payload())
    encoded = canonical_json_bytes(evidence)
    restored = model_type.model_validate(json.loads(encoded))
    assert restored == evidence
    assert canonical_json_bytes(restored) == encoded


@pytest.mark.parametrize(("model_type", "build_payload"), EVIDENCE_MODELS, ids=EVIDENCE_MODEL_IDS)
def test_every_model_refuses_an_unknown_field(
    model_type: type[ContractModel],
    build_payload: PayloadBuilder,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        model_type.model_validate(build_payload(captured_by="a helpful script"))
    assert_validation_error(
        exc_info.value,
        loc_suffix=("captured_by",),
        error_type="extra_forbidden",
    )


@pytest.mark.parametrize(("model_type", "build_payload"), EVIDENCE_MODELS, ids=EVIDENCE_MODEL_IDS)
def test_every_model_refuses_a_stale_observation(
    model_type: type[ContractModel],
    build_payload: PayloadBuilder,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        model_type.model_validate(build_payload(observed_at=stale_observed_at()))
    assert evidence_load_reason_code(exc_info.value) == EVIDENCE_STALE_CODE


@pytest.mark.parametrize(("model_type", "build_payload"), EVIDENCE_MODELS, ids=EVIDENCE_MODEL_IDS)
def test_every_model_refuses_a_missing_observation(
    model_type: type[ContractModel],
    build_payload: PayloadBuilder,
) -> None:
    payload = build_payload()
    del payload["observed_at"]
    with pytest.raises(ValidationError) as exc_info:
        model_type.model_validate(payload)
    assert_validation_error(exc_info.value, loc_suffix=("observed_at",), error_type="missing")


@pytest.mark.parametrize(
    ("model_type", "build_payload", "field", "value"),
    ACCOUNT_ID_PROBES,
    ids=ACCOUNT_ID_PROBE_IDS,
)
def test_a_field_that_could_hold_an_account_id_refuses_one(
    model_type: type[ContractModel],
    build_payload: PayloadBuilder,
    field: str,
    value: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        model_type.model_validate(build_payload(**{field: value}))
    assert_validation_error(
        exc_info.value,
        loc_suffix=(field,),
        error_type="value_error",
        message_fragment="must not contain credentials or raw AWS account IDs",
    )


@pytest.mark.parametrize(
    ("model_type", "build_payload", "field"),
    CREDENTIAL_PROBES,
    ids=CREDENTIAL_PROBE_IDS,
)
def test_a_free_text_field_refuses_an_unredacted_credential(
    model_type: type[ContractModel],
    build_payload: PayloadBuilder,
    field: str,
) -> None:
    payload = build_payload(**{field: f"failed for {AWS_EXAMPLE_ACCESS_KEY_ID}"})
    with pytest.raises(ValidationError) as exc_info:
        model_type.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=(field,),
        error_type="value_error",
        message_fragment="must not contain credentials or raw AWS account IDs",
    )


def test_build_provenance_joins_the_run_to_the_commit_it_built() -> None:
    evidence = BuildProvenanceEvidence.model_validate(build_provenance_payload())
    assert evidence.source_identity.commit_sha == COMMIT_SHA
    assert evidence.workflow_run.run_repository == "edu-llm/OLMo-core"
    assert evidence.image_digest == IMAGE_DIGEST


def test_build_provenance_refuses_a_build_from_a_dirty_tree() -> None:
    payload = build_provenance_payload(source_identity=source_identity_payload(clean=False))
    with pytest.raises(ValidationError) as exc_info:
        BuildProvenanceEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("source_identity", "clean"),
        error_type="literal_error",
    )


def test_build_provenance_refuses_an_unfinished_run() -> None:
    with pytest.raises(ValidationError) as exc_info:
        BuildProvenanceEvidence.model_validate(build_provenance_payload(run_conclusion="failure"))
    assert_validation_error(
        exc_info.value,
        loc_suffix=("run_conclusion",),
        error_type="literal_error",
    )


@pytest.mark.parametrize(
    ("nested", "field", "value"),
    [
        ("workflow_run", "run_repository", f"edu-llm/{AWS_EXAMPLE_ACCOUNT_ID}"),
        ("workflow_run", "workflow_repository", f"edu-llm/{AWS_EXAMPLE_ACCOUNT_ID}"),
        ("source_identity", "repository", AWS_EXAMPLE_ACCOUNT_ID),
    ],
)
def test_build_provenance_scans_the_contracts_it_reuses(
    nested: str,
    field: str,
    value: str,
) -> None:
    # GitHubWorkflowRunReference and SourceIdentity predate SecretFreeStr and constrain
    # these fields by pattern alone. Both patterns admit an account ID.
    builder = workflow_run_payload if nested == "workflow_run" else source_identity_payload
    payload = build_provenance_payload(**{nested: builder(**{field: value})})
    with pytest.raises(ValidationError) as exc_info:
        BuildProvenanceEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=(),
        error_type="value_error",
        message_fragment="must not contain credentials or raw AWS account IDs",
    )


def test_build_provenance_accepts_a_commit_ref_the_secret_scan_would_refuse() -> None:
    # A forty-character commit SHA matches AWS_SECRET_ACCESS_KEY_PATTERN, so the nested
    # scan has to mask content digests before it looks for a credential.
    payload = build_provenance_payload(workflow_run=workflow_run_payload(workflow_ref=COMMIT_SHA))
    evidence = BuildProvenanceEvidence.model_validate(payload)
    assert evidence.workflow_run.workflow_ref == COMMIT_SHA


def test_ecr_image_records_the_registry_answer() -> None:
    evidence = EcrImageEvidence.model_validate(ecr_image_payload())
    assert evidence.repository_name == ECR_REPOSITORY
    assert evidence.image_tag == IMAGE_TAG
    assert evidence.base_image_digest == BASE_IMAGE_DIGEST


def test_ecr_image_refuses_a_repository_arn_in_place_of_the_name() -> None:
    # Refused twice over: the ARN is not a repository name, and it carries the account
    # ID the name exists to keep out. The pattern is checked first, so that is the
    # error a reader sees.
    arn = f"arn:aws:ecr:us-east-1:{AWS_EXAMPLE_ACCOUNT_ID}:repository/{ECR_REPOSITORY}"
    with pytest.raises(ValidationError) as exc_info:
        EcrImageEvidence.model_validate(ecr_image_payload(repository_name=arn))
    assert_validation_error(
        exc_info.value,
        loc_suffix=("repository_name",),
        error_type="string_pattern_mismatch",
    )
    with pytest.raises(ValueError, match="must not contain credentials or raw AWS account IDs"):
        scan_for_secrets(arn)


@pytest.mark.parametrize("tag", ["latest", "main", IMAGE_TAG.upper(), COMMIT_SHA])
def test_ecr_image_refuses_a_tag_that_is_not_a_commit_prefix(tag: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        EcrImageEvidence.model_validate(ecr_image_payload(image_tag=tag))
    assert_validation_error(
        exc_info.value,
        loc_suffix=("image_tag",),
        error_type="string_pattern_mismatch",
    )


def test_ecr_image_refuses_an_image_that_is_its_own_base() -> None:
    payload = ecr_image_payload(base_image_digest=IMAGE_DIGEST)
    with pytest.raises(ValidationError) as exc_info:
        EcrImageEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=(),
        error_type="value_error",
        message_fragment="an image cannot be its own base image",
    )


def test_a_completed_scan_with_no_findings_is_not_a_scan_that_never_ran() -> None:
    clean = ImageScanEvidence.model_validate(
        image_scan_payload(
            finding_counts=finding_counts_payload(high=0, medium=0, low=0, informational=0)
        )
    )
    unfinished = ImageScanEvidence.model_validate(
        image_scan_payload(
            scan_status="IN_PROGRESS",
            scan_status_description="The scan is in progress.",
            scan_completed_at=None,
            finding_counts=None,
        )
    )
    assert clean.finding_counts is not None
    assert clean.finding_counts.total == 0
    assert unfinished.finding_counts is None
    assert clean != unfinished


def test_a_completed_scan_must_record_its_findings() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ImageScanEvidence.model_validate(image_scan_payload(finding_counts=None))
    assert_validation_error(
        exc_info.value,
        loc_suffix=(),
        error_type="value_error",
        message_fragment="a completed scan must record its finding counts",
    )


def test_a_scan_that_did_not_complete_may_not_record_findings() -> None:
    payload = image_scan_payload(
        scan_status="PENDING",
        scan_status_description="The scan is pending.",
        scan_completed_at=None,
    )
    with pytest.raises(ValidationError) as exc_info:
        ImageScanEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=(),
        error_type="value_error",
        message_fragment="only a completed scan may record finding counts",
    )


def test_a_scan_that_did_not_complete_must_say_why() -> None:
    payload = image_scan_payload(
        scan_status="UNSUPPORTED_IMAGE",
        scan_status_description=None,
        scan_completed_at=None,
        finding_counts=None,
    )
    with pytest.raises(ValidationError) as exc_info:
        ImageScanEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=(),
        error_type="value_error",
        message_fragment="a scan that did not complete must record why",
    )


def test_a_completed_scan_must_record_when_it_completed() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ImageScanEvidence.model_validate(image_scan_payload(scan_completed_at=None))
    assert_validation_error(
        exc_info.value,
        loc_suffix=(),
        error_type="value_error",
        message_fragment="a completed scan must record when it completed",
    )


def test_image_scan_refuses_a_status_ecr_does_not_report() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ImageScanEvidence.model_validate(image_scan_payload(scan_status="CLEAN"))
    assert_validation_error(
        exc_info.value,
        loc_suffix=("scan_status",),
        error_type="literal_error",
    )


@pytest.mark.parametrize(
    "severity",
    ["critical", "high", "medium", "low", "informational", "undefined"],
)
def test_finding_counts_require_every_severity(severity: str) -> None:
    counts = finding_counts_payload()
    del counts[severity]
    with pytest.raises(ValidationError) as exc_info:
        ImageScanEvidence.model_validate(image_scan_payload(finding_counts=counts))
    assert_validation_error(
        exc_info.value,
        loc_suffix=("finding_counts", severity),
        error_type="missing",
    )


def test_finding_counts_refuse_a_negative_count() -> None:
    payload = image_scan_payload(finding_counts=finding_counts_payload(critical=-1))
    with pytest.raises(ValidationError) as exc_info:
        ImageScanEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("finding_counts", "critical"),
        error_type="greater_than_equal",
    )


def test_the_session_records_the_role_by_name_and_the_window_it_held() -> None:
    evidence = OidcSessionEvidence.model_validate(oidc_session_payload())
    assert evidence.role_name == PUBLISHER_ROLE_NAME
    assert evidence.oidc_issuer == "token.actions.githubusercontent.com"
    assert evidence.session_duration == timedelta(hours=1)


def test_a_session_with_no_recorded_expiry_cannot_be_written_down() -> None:
    payload = oidc_session_payload()
    del payload["expires_at"]
    with pytest.raises(ValidationError) as exc_info:
        OidcSessionEvidence.model_validate(payload)
    assert_validation_error(exc_info.value, loc_suffix=("expires_at",), error_type="missing")


@pytest.mark.parametrize("offset", [0, -60])
def test_a_session_must_expire_after_it_was_assumed(offset: int) -> None:
    assumed = moments_ago(600)
    payload = oidc_session_payload(assumed_at=assumed, expires_at=moments_ago(600 - offset))
    with pytest.raises(ValidationError) as exc_info:
        OidcSessionEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=(),
        error_type="value_error",
        message_fragment="a session must expire after it was assumed",
    )


def test_a_session_timestamp_must_carry_an_offset() -> None:
    payload = oidc_session_payload(assumed_at="2026-07-25T03:24:36")
    with pytest.raises(ValidationError) as exc_info:
        OidcSessionEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("assumed_at",),
        error_type="value_error",
        message_fragment="timestamps must be timezone-aware",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("event_name", "AssumeRole"), ("event_source", "ecr.amazonaws.com")],
)
def test_the_session_record_refuses_an_event_that_is_not_the_web_identity_assumption(
    field: str,
    value: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        OidcSessionEvidence.model_validate(oidc_session_payload(**{field: value}))
    assert_validation_error(exc_info.value, loc_suffix=(field,), error_type="literal_error")


def test_the_session_record_refuses_an_event_id_that_is_not_a_cloudtrail_uuid() -> None:
    with pytest.raises(ValidationError) as exc_info:
        OidcSessionEvidence.model_validate(oidc_session_payload(event_id="not-a-uuid"))
    assert_validation_error(
        exc_info.value,
        loc_suffix=("event_id",),
        error_type="string_pattern_mismatch",
    )


def test_denial_records_the_action_attempted_and_the_refusal_that_came_back() -> None:
    evidence = DenialEvidence.model_validate(denial_payload())
    assert evidence.attempted_action == "batch:SubmitJob"
    assert evidence.outcome == "denied"
    assert evidence.error_code == "AccessDeniedException"
    assert "is not authorized to perform" in evidence.error_message


def test_denial_evidence_cannot_record_a_call_that_was_allowed() -> None:
    with pytest.raises(ValidationError) as exc_info:
        DenialEvidence.model_validate(denial_payload(outcome="allowed"))
    assert_validation_error(exc_info.value, loc_suffix=("outcome",), error_type="literal_error")


def test_denial_accepts_the_redacted_message_and_refuses_the_raw_one() -> None:
    evidence = DenialEvidence.model_validate(denial_payload())
    assert AWS_EXAMPLE_ACCOUNT_ID not in evidence.error_message
    assert f"assumed-role/{PUBLISHER_ROLE_NAME}" in evidence.error_message
    with pytest.raises(ValidationError):
        DenialEvidence.model_validate(denial_payload(error_message=RAW_DENIAL_MESSAGE))


@pytest.mark.parametrize("action", ["batch:*", "*", "SubmitJob", "batch:", "Batch:SubmitJob"])
def test_denial_refuses_an_action_that_is_not_one_concrete_api_call(action: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        DenialEvidence.model_validate(denial_payload(attempted_action=action))
    assert_validation_error(
        exc_info.value,
        loc_suffix=("attempted_action",),
        error_type="string_pattern_mismatch",
    )


def test_denial_requires_the_resource_key_even_when_the_call_has_no_resource() -> None:
    without_resource = DenialEvidence.model_validate(denial_payload(attempted_resource=None))
    assert without_resource.attempted_resource is None
    payload = denial_payload()
    del payload["attempted_resource"]
    with pytest.raises(ValidationError) as exc_info:
        DenialEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("attempted_resource",),
        error_type="missing",
    )
