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
    redact_content_digests,
    scan_for_secrets,
)
from edullm_platform.phase1_evidence import (
    BuildProvenanceEvidence,
    DenialEvidence,
    DeployedRoleEvidence,
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
#: A commit whose first twelve characters are decimal digits, and not merely digits: they
#: are the example account ID. One commit in 281 has a prefix like this, and the tag it
#: produces is indistinguishable from an account ID except by the commit it came from.
ACCOUNT_ID_SHAPED_COMMIT_SHA = AWS_EXAMPLE_ACCOUNT_ID + "b3f0" * 7
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

#: A policy resource is an ARN and has no name that identifies it on its own, so this
#: is the one place an ARN is recorded. Redacted, it lines up with the template's
#: ${AWS::AccountId} without either side naming the account.
RAW_REPOSITORY_ARN = f"arn:aws:ecr:us-east-1:{AWS_EXAMPLE_ACCOUNT_ID}:repository/{ECR_REPOSITORY}"
REDACTED_REPOSITORY_ARN = redact_aws_account_ids(RAW_REPOSITORY_ARN)

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
        "source_commit_sha": COMMIT_SHA,
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


def condition_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "operator": "StringEquals",
        "condition_key": "token.actions.githubusercontent.com:aud",
        "values": ["sts.amazonaws.com"],
    }
    payload.update(overrides)
    return payload


def action_match_payload(actions: list[str], **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {"element": "Action", "actions": actions}
    payload.update(overrides)
    return payload


def resource_match_payload(resources: list[str], **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {"element": "Resource", "resources": resources}
    payload.update(overrides)
    return payload


def principal_match_payload(
    principals: list[dict[str, object]],
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {"element": "Principal", "principals": principals}
    payload.update(overrides)
    return payload


FEDERATED_GITHUB_PRINCIPAL: dict[str, object] = {
    "principal_type": "Federated",
    "identifier": "token.actions.githubusercontent.com",
}


def trust_statement_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "sid": None,
        "effect": "Allow",
        "action_match": action_match_payload(["sts:AssumeRoleWithWebIdentity"]),
        "principal_match": principal_match_payload([FEDERATED_GITHUB_PRINCIPAL]),
        "conditions": [
            condition_payload(),
            condition_payload(
                condition_key="token.actions.githubusercontent.com:repository_id",
                values=["1306868157"],
            ),
            condition_payload(
                operator="StringLike",
                condition_key="token.actions.githubusercontent.com:sub",
                values=["repo:edu-llm@306859726/OLMo-core@1306868157:ref:refs/heads/*"],
            ),
        ],
    }
    payload.update(overrides)
    return payload


def permission_statement_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "sid": None,
        "effect": "Allow",
        "action_match": action_match_payload(["ecr:PutImage", "ecr:UploadLayerPart"]),
        "resource_match": resource_match_payload([REDACTED_REPOSITORY_ARN]),
        "conditions": [],
    }
    payload.update(overrides)
    return payload


def inline_policy_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "policy_name": "publish-olmo-core-images",
        "policy_version": "2012-10-17",
        "statements": [
            permission_statement_payload(
                action_match=action_match_payload(["ecr:GetAuthorizationToken"]),
                resource_match=resource_match_payload(["*"]),
            ),
            permission_statement_payload(),
        ],
    }
    payload.update(overrides)
    return payload


def deployed_role_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "source": "aws",
        "environment": "sandbox",
        "observed_at": recent_observed_at(),
        "status": "ok",
        "role_name": PUBLISHER_ROLE_NAME,
        "permissions_boundary_policy_name": "InternSandboxBoundary",
        "max_session_duration_seconds": 3600,
        "trust_policy_version": "2012-10-17",
        "trust_statements": [trust_statement_payload()],
        "inline_policies": [inline_policy_payload()],
        "attached_managed_policy_names": [],
    }
    payload.update(overrides)
    return payload


EVIDENCE_MODELS: tuple[tuple[type[ContractModel], PayloadBuilder], ...] = (
    (BuildProvenanceEvidence, build_provenance_payload),
    (EcrImageEvidence, ecr_image_payload),
    (ImageScanEvidence, image_scan_payload),
    (OidcSessionEvidence, oidc_session_payload),
    (DenialEvidence, denial_payload),
    (DeployedRoleEvidence, deployed_role_payload),
)

EVIDENCE_MODEL_IDS = [model_type.__name__ for model_type, _builder in EVIDENCE_MODELS]

#: Fields whose legitimate values are close enough to a twelve-digit account ID that a
#: reader could not tell one from the other, so each is proved to refuse one. The image
#: tag is not among them: it is licensed by the commit SHA beside it rather than by the
#: scan, which the tests around ``ACCOUNT_ID_SHAPED_COMMIT_SHA`` cover instead.
ACCOUNT_ID_PROBES: tuple[tuple[type[ContractModel], PayloadBuilder, str, str], ...] = (
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
    (DeployedRoleEvidence, deployed_role_payload, "role_name", AWS_EXAMPLE_ACCOUNT_ID),
    (
        DeployedRoleEvidence,
        deployed_role_payload,
        "permissions_boundary_policy_name",
        AWS_EXAMPLE_ACCOUNT_ID,
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
    assert evidence.source_commit_sha == COMMIT_SHA
    assert evidence.base_image_digest == BASE_IMAGE_DIGEST


def test_a_tag_of_twelve_digits_is_recordable_because_the_commit_licenses_it() -> None:
    # The tag here is character for character the example account ID, and it is accepted
    # only because the commit it was built from begins with those digits. That pairing is
    # the proof: the digits are a commit prefix, and a reader can check that they are.
    evidence = EcrImageEvidence.model_validate(
        ecr_image_payload(
            image_tag=AWS_EXAMPLE_ACCOUNT_ID,
            source_commit_sha=ACCOUNT_ID_SHAPED_COMMIT_SHA,
        )
    )
    assert evidence.image_tag == AWS_EXAMPLE_ACCOUNT_ID
    assert evidence.source_commit_sha.startswith(evidence.image_tag)


def test_the_same_twelve_digits_are_refused_when_no_commit_licenses_them() -> None:
    # Same tag, ordinary commit. Nothing licenses the digits, so this is how an account
    # ID pasted into the tag by a capture bug fails rather than being recorded.
    with pytest.raises(ValidationError) as exc_info:
        EcrImageEvidence.model_validate(ecr_image_payload(image_tag=AWS_EXAMPLE_ACCOUNT_ID))
    assert_validation_error(
        exc_info.value,
        loc_suffix=(),
        error_type="value_error",
        message_fragment="the image tag must be the first 12 characters of the commit SHA",
    )


def test_a_tag_that_is_not_this_commit_prefix_cannot_validate() -> None:
    other_commit = "0" * 8 + COMMIT_SHA[8:]
    with pytest.raises(ValidationError) as exc_info:
        EcrImageEvidence.model_validate(ecr_image_payload(source_commit_sha=other_commit))
    assert_validation_error(
        exc_info.value,
        loc_suffix=(),
        error_type="value_error",
        message_fragment="the image tag must be the first 12 characters of the commit SHA",
    )


def test_a_tag_without_the_commit_that_licenses_it_cannot_validate() -> None:
    payload = ecr_image_payload()
    del payload["source_commit_sha"]
    with pytest.raises(ValidationError) as exc_info:
        EcrImageEvidence.model_validate(payload)
    assert_validation_error(exc_info.value, loc_suffix=("source_commit_sha",), error_type="missing")


@pytest.mark.parametrize("sha", [IMAGE_TAG, COMMIT_SHA.upper(), COMMIT_SHA + "0", IMAGE_DIGEST])
def test_the_licensing_commit_must_itself_be_a_full_commit_sha(sha: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        EcrImageEvidence.model_validate(ecr_image_payload(source_commit_sha=sha))
    assert_validation_error(
        exc_info.value,
        loc_suffix=("source_commit_sha",),
        error_type="string_pattern_mismatch",
    )


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


def test_free_text_holding_a_digest_needs_both_masks_before_a_field_takes_it() -> None:
    # redact_aws_account_ids keeps a sha256 digest deliberately, and scan_for_secrets
    # refuses one just as deliberately, because sixty-four hexadecimal characters are
    # the shape of a long credential. A captured message carrying both an account ID
    # and a digest therefore needs both masks, in this order.
    message = f"image {IMAGE_DIGEST} already exists in {RAW_REPOSITORY_ARN}"
    once = redact_aws_account_ids(message)
    assert AWS_EXAMPLE_ACCOUNT_ID not in once
    assert IMAGE_DIGEST in once
    with pytest.raises(ValidationError) as exc_info:
        DenialEvidence.model_validate(denial_payload(error_message=once))
    assert_validation_error(
        exc_info.value,
        loc_suffix=("error_message",),
        error_type="value_error",
        message_fragment="must not contain credentials or raw AWS account IDs",
    )
    twice = redact_content_digests(once)
    accepted = DenialEvidence.model_validate(denial_payload(error_message=twice))
    assert accepted.error_message == twice
    assert "already exists in" in accepted.error_message


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


def test_the_deployed_role_carries_what_a_template_comparison_needs() -> None:
    evidence = DeployedRoleEvidence.model_validate(deployed_role_payload())
    assert evidence.role_name == PUBLISHER_ROLE_NAME
    assert evidence.permissions_boundary_policy_name == "InternSandboxBoundary"
    assert evidence.max_session_duration_seconds == 3600
    assert evidence.attached_managed_policy_names == ()
    trust = evidence.trust_statements[0]
    assert trust.principal_match.element == "Principal"
    assert trust.principal_match.principals[0].identifier == "token.actions.githubusercontent.com"
    assert [condition.condition_key for condition in trust.conditions] == [
        "token.actions.githubusercontent.com:aud",
        "token.actions.githubusercontent.com:repository_id",
        "token.actions.githubusercontent.com:sub",
    ]
    policy = evidence.inline_policies[0]
    assert policy.policy_name == "publish-olmo-core-images"
    assert policy.statements[1].resource_match.element == "Resource"
    assert policy.statements[1].resource_match.resources == (REDACTED_REPOSITORY_ARN,)


def test_the_deployed_role_records_names_rather_than_its_own_arn() -> None:
    fields = set(DeployedRoleEvidence.model_fields)
    assert "role_arn" not in fields
    assert "account_id" not in fields
    # IAM is global. A region on this record would be a fact about the API call rather
    # than about the role, and would invite a comparison that means nothing.
    assert "region" not in fields


@pytest.mark.parametrize("field", sorted(deployed_role_payload()))
def test_a_partially_captured_role_cannot_validate(field: str) -> None:
    payload = deployed_role_payload()
    del payload[field]
    with pytest.raises(ValidationError) as exc_info:
        DeployedRoleEvidence.model_validate(payload)
    assert_validation_error(exc_info.value, loc_suffix=(field,), error_type="missing")


def test_a_detached_permissions_boundary_is_recordable_and_not_the_same_as_uncaptured() -> None:
    detached = DeployedRoleEvidence.model_validate(
        deployed_role_payload(permissions_boundary_policy_name=None)
    )
    assert detached.permissions_boundary_policy_name is None


def test_an_attached_managed_policy_is_recorded_because_no_template_would_show_it() -> None:
    # The committed template attaches nothing, so a managed policy attached in the
    # console is drift the template cannot express. A record that could not hold it
    # would be blind to the easiest way to widen this role.
    widened = DeployedRoleEvidence.model_validate(
        deployed_role_payload(attached_managed_policy_names=["AdministratorAccess"])
    )
    assert widened.attached_managed_policy_names == ("AdministratorAccess",)


def test_the_role_record_holds_a_policy_widened_in_the_console() -> None:
    widened = DeployedRoleEvidence.model_validate(
        deployed_role_payload(
            max_session_duration_seconds=43200,
            inline_policies=[
                inline_policy_payload(
                    policy_name="added-by-hand",
                    statements=[
                        permission_statement_payload(
                            action_match=action_match_payload(["*"]),
                            resource_match=resource_match_payload(["*"]),
                        )
                    ],
                )
            ],
            trust_statements=[trust_statement_payload(conditions=[])],
        )
    )
    assert widened.inline_policies[0].statements[0].action_match.actions == ("*",)
    assert widened.trust_statements[0].conditions == ()


@pytest.mark.parametrize("duration", [1800, 43201])
def test_the_role_record_refuses_a_session_duration_iam_cannot_return(duration: int) -> None:
    with pytest.raises(ValidationError) as exc_info:
        DeployedRoleEvidence.model_validate(
            deployed_role_payload(max_session_duration_seconds=duration)
        )
    error_type = "greater_than_equal" if duration < 3600 else "less_than_equal"
    assert_validation_error(
        exc_info.value,
        loc_suffix=("max_session_duration_seconds",),
        error_type=error_type,
    )


def test_the_role_record_refuses_a_duplicate_inline_policy_name() -> None:
    payload = deployed_role_payload(
        inline_policies=[inline_policy_payload(), inline_policy_payload()]
    )
    with pytest.raises(ValidationError) as exc_info:
        DeployedRoleEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=(),
        error_type="value_error",
        message_fragment="inline policy names must be unique",
    )


def test_the_role_record_refuses_a_duplicate_attached_policy_name() -> None:
    payload = deployed_role_payload(
        attached_managed_policy_names=["ReadOnlyAccess", "ReadOnlyAccess"]
    )
    with pytest.raises(ValidationError) as exc_info:
        DeployedRoleEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=(),
        error_type="value_error",
        message_fragment="attached managed policy names must be unique",
    )


def role_with_one_statement(**statement_overrides: object) -> dict[str, object]:
    """A role whose single inline policy holds one statement, built from overrides."""
    return deployed_role_payload(
        inline_policies=[
            inline_policy_payload(statements=[permission_statement_payload(**statement_overrides)])
        ]
    )


@pytest.mark.parametrize(
    ("statement_field", "values_field"),
    [("action_match", "actions"), ("resource_match", "resources")],
)
def test_a_permission_statement_that_selects_nothing_cannot_validate(
    statement_field: str,
    values_field: str,
) -> None:
    # IAM's grammar requires an action element and a resource element in every statement,
    # each naming at least one value. An empty list is a capture that dropped something.
    builder = action_match_payload if values_field == "actions" else resource_match_payload
    payload = role_with_one_statement(**{statement_field: builder([])})
    with pytest.raises(ValidationError) as exc_info:
        DeployedRoleEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=(statement_field, values_field),
        error_type="too_short",
    )


@pytest.mark.parametrize("action", ["ecr:*", "*", "ecr:PutImage"])
def test_a_policy_action_may_be_a_wildcard_because_a_widened_role_has_one(action: str) -> None:
    evidence = DeployedRoleEvidence.model_validate(
        role_with_one_statement(action_match=action_match_payload([action]))
    )
    assert evidence.inline_policies[0].statements[0].action_match.actions == (action,)


@pytest.mark.parametrize("action", ["submit a job", "Ecr:PutImage", "ecr:", ":PutImage"])
def test_a_policy_action_that_is_not_an_action_cannot_validate(action: str) -> None:
    payload = role_with_one_statement(action_match=action_match_payload([action]))
    with pytest.raises(ValidationError) as exc_info:
        DeployedRoleEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("actions", 0),
        error_type="value_error",
        message_fragment="a policy action must be a service action or a wildcard",
    )


def test_an_inline_policy_with_no_statements_cannot_validate() -> None:
    payload = deployed_role_payload(inline_policies=[inline_policy_payload(statements=[])])
    with pytest.raises(ValidationError) as exc_info:
        DeployedRoleEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("inline_policies", 0, "statements"),
        error_type="too_short",
    )


def test_a_trust_statement_with_no_principals_cannot_validate() -> None:
    payload = deployed_role_payload(
        trust_statements=[trust_statement_payload(principal_match=principal_match_payload([]))]
    )
    with pytest.raises(ValidationError) as exc_info:
        DeployedRoleEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("principal_match", "principals"),
        error_type="too_short",
    )


def test_a_role_with_no_trust_policy_cannot_validate() -> None:
    with pytest.raises(ValidationError) as exc_info:
        DeployedRoleEvidence.model_validate(deployed_role_payload(trust_statements=[]))
    assert_validation_error(
        exc_info.value,
        loc_suffix=("trust_statements",),
        error_type="too_short",
    )


def role_with_one_condition(**condition_overrides: object) -> dict[str, object]:
    """A role whose single trust statement carries one condition, built from overrides."""
    return deployed_role_payload(
        trust_statements=[
            trust_statement_payload(conditions=[condition_payload(**condition_overrides)])
        ]
    )


@pytest.mark.parametrize(
    ("captured", "recorded"),
    [(True, "true"), (False, "false"), (10, "10"), (1.5, "1.5"), ("true", "true")],
)
def test_a_condition_value_iam_returned_unquoted_is_recorded_quoted(
    captured: object,
    recorded: str,
) -> None:
    # IAM's grammar makes quotation marks optional around numbers and booleans, so a
    # policy can hold aws:SecureTransport as true rather than "true" and the API returns
    # what was stored. Both spellings mean the same thing, and refusing the unquoted one
    # would have failed capture on a policy IAM accepted.
    evidence = DeployedRoleEvidence.model_validate(
        role_with_one_condition(
            operator="Bool",
            condition_key="aws:SecureTransport",
            values=[captured],
        )
    )
    assert evidence.trust_statements[0].conditions[0].values == (recorded,)


@pytest.mark.parametrize("value", [None, ["nested"], {"key": "value"}])
def test_a_condition_value_that_is_not_a_json_scalar_cannot_validate(value: object) -> None:
    with pytest.raises(ValidationError) as exc_info:
        DeployedRoleEvidence.model_validate(role_with_one_condition(values=[value]))
    assert_validation_error(exc_info.value, loc_suffix=("values", 0), error_type="string_type")


def test_a_condition_with_no_values_cannot_validate() -> None:
    payload = deployed_role_payload(
        trust_statements=[trust_statement_payload(conditions=[condition_payload(values=[])])]
    )
    with pytest.raises(ValidationError) as exc_info:
        DeployedRoleEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("conditions", 0, "values"),
        error_type="too_short",
    )


def test_a_policy_resource_arn_must_arrive_with_its_account_id_redacted() -> None:
    payload = role_with_one_statement(resource_match=resource_match_payload([RAW_REPOSITORY_ARN]))
    with pytest.raises(ValidationError) as exc_info:
        DeployedRoleEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("resources", 0),
        error_type="value_error",
        message_fragment="must not contain credentials or raw AWS account IDs",
    )
    accepted = DeployedRoleEvidence.model_validate(deployed_role_payload())
    recorded = accepted.inline_policies[0].statements[1].resource_match.resources[0]
    assert AWS_EXAMPLE_ACCOUNT_ID not in recorded
    assert ECR_REPOSITORY in recorded


def test_a_trust_principal_refuses_an_unredacted_credential() -> None:
    payload = deployed_role_payload(
        trust_statements=[
            trust_statement_payload(
                principal_match=principal_match_payload(
                    [{"principal_type": "AWS", "identifier": AWS_EXAMPLE_ACCESS_KEY_ID}]
                )
            )
        ]
    )
    with pytest.raises(ValidationError) as exc_info:
        DeployedRoleEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("principals", 0, "identifier"),
        error_type="value_error",
        message_fragment="must not contain credentials or raw AWS account IDs",
    )


def test_a_trust_statement_can_record_a_deny_that_the_template_does_not_have() -> None:
    denying = DeployedRoleEvidence.model_validate(
        deployed_role_payload(
            trust_statements=[
                trust_statement_payload(),
                trust_statement_payload(sid="AddedByHand", effect="Deny"),
            ]
        )
    )
    assert [statement.effect for statement in denying.trust_statements] == ["Allow", "Deny"]
    assert denying.trust_statements[1].sid == "AddedByHand"


def test_a_statement_records_that_its_actions_are_negated() -> None:
    # AWS's own example of the shape: allow every action in every service except IAM's.
    # Read as an Action list this statement grants two things; read correctly it grants
    # nearly everything. A record that could not tell them apart would be worse than one
    # that refused the statement, and refusing it would miss the widening outright.
    widened = DeployedRoleEvidence.model_validate(
        role_with_one_statement(
            action_match=action_match_payload(["iam:*"], element="NotAction"),
            resource_match=resource_match_payload(["*"]),
        )
    )
    statement = widened.inline_policies[0].statements[0]
    assert statement.action_match.element == "NotAction"
    assert statement.action_match.actions == ("iam:*",)


def test_a_negated_statement_is_never_equal_to_the_positive_one_it_inverts() -> None:
    def role_selecting(element: str) -> DeployedRoleEvidence:
        return DeployedRoleEvidence.model_validate(
            role_with_one_statement(
                action_match=action_match_payload(["iam:*"], element=element),
                resource_match=resource_match_payload(["*"]),
            )
        )

    positive = role_selecting("Action")
    negated = role_selecting("NotAction")
    assert positive != negated
    encoded = canonical_json_bytes(negated)
    assert b'"NotAction"' in encoded
    assert canonical_json_bytes(positive) != encoded


def test_a_statement_records_that_its_resources_are_negated() -> None:
    widened = DeployedRoleEvidence.model_validate(
        role_with_one_statement(
            resource_match=resource_match_payload([REDACTED_REPOSITORY_ARN], element="NotResource")
        )
    )
    statement = widened.inline_policies[0].statements[0]
    assert statement.resource_match.element == "NotResource"
    assert statement.resource_match.resources == (REDACTED_REPOSITORY_ARN,)


def test_a_trust_statement_records_that_its_principals_are_negated() -> None:
    # IAM Access Analyzer raises ALLOW_WITH_NOT_PRINCIPAL against this shape because it
    # can admit anonymous principals. It is exactly what a drift record must be able to
    # say about a trust policy someone edited by hand.
    widened = DeployedRoleEvidence.model_validate(
        deployed_role_payload(
            trust_statements=[
                trust_statement_payload(
                    principal_match=principal_match_payload(
                        [FEDERATED_GITHUB_PRINCIPAL], element="NotPrincipal"
                    )
                )
            ]
        )
    )
    statement = widened.trust_statements[0]
    assert statement.principal_match.element == "NotPrincipal"
    assert statement.principal_match.principals[0].identifier == (
        "token.actions.githubusercontent.com"
    )


@pytest.mark.parametrize(
    ("match_payload", "loc_suffix"),
    [
        (
            {"element": "Action", "actions": ["ecr:PutImage"], "not_actions": ["iam:*"]},
            ("action_match", "not_actions"),
        ),
        (
            {"element": "Resource", "resources": ["*"], "not_resources": ["*"]},
            ("resource_match", "not_resources"),
        ),
    ],
)
def test_a_statement_cannot_claim_a_form_and_its_negation_at_once(
    match_payload: dict[str, object],
    loc_suffix: tuple[str, ...],
) -> None:
    # There is one list and one element name, so naming both forms takes an extra key,
    # and there is no room for one.
    payload = role_with_one_statement(**{loc_suffix[0]: match_payload})
    with pytest.raises(ValidationError) as exc_info:
        DeployedRoleEvidence.model_validate(payload)
    assert_validation_error(exc_info.value, loc_suffix=loc_suffix, error_type="extra_forbidden")


@pytest.mark.parametrize("statement_field", ["action_match", "resource_match"])
def test_a_statement_cannot_leave_the_form_of_its_selection_unsaid(statement_field: str) -> None:
    builder = action_match_payload if statement_field == "action_match" else resource_match_payload
    match_payload = builder(["*"])
    del match_payload["element"]
    payload = role_with_one_statement(**{statement_field: match_payload})
    with pytest.raises(ValidationError) as exc_info:
        DeployedRoleEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=(statement_field, "element"),
        error_type="missing",
    )


@pytest.mark.parametrize("element", ["Actions", "notAction", "NotActions", "Deny", ""])
def test_an_action_element_iam_does_not_have_cannot_validate(element: str) -> None:
    payload = role_with_one_statement(action_match=action_match_payload(["*"], element=element))
    with pytest.raises(ValidationError) as exc_info:
        DeployedRoleEvidence.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        loc_suffix=("action_match", "element"),
        error_type="literal_error",
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
