from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from pydantic import ValidationError

from edullm_platform.canonical import canonical_json_bytes, sha256_digest
from edullm_platform.contracts.image import (
    GitHubWorkflowRunReference,
    ImageProvenance,
    resolve_image_reference,
)

IMAGE_DIGEST = "sha256:" + "b" * 64
BASE_IMAGE_DIGEST = "sha256:" + "c" * 64
COMMIT_SHA = "a" * 40
ECR_REPOSITORY = "sbsandbox-intern-edullm-olmo-core"


def synthetic_account_id() -> str:
    return "".join(str(digit) for digit in range(1, 10)) + "123"


def source_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "repository": "OLMo-core",
        "github_repository_id": 1306868157,
        "ref": "refs/heads/main",
        "commit_sha": COMMIT_SHA,
        "clean": True,
        "verified": True,
    }
    payload.update(overrides)
    return payload


def workflow_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "run_repository": "edu-llm/OLMo-core",
        "workflow_repository": "edu-llm/platform",
        "workflow_path": ".github/workflows/build-research-image.yml",
        "workflow_ref": "refs/heads/main",
        "run_id": 987654321,
        "run_attempt": 2,
    }
    payload.update(overrides)
    return payload


def image_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "ecr_repository": ECR_REPOSITORY,
        "image_digest": IMAGE_DIGEST,
        "base_image_digest": BASE_IMAGE_DIGEST,
        "source": source_payload(),
        "workflow_run": workflow_payload(),
        "built_at": "2026-07-25T18:30:00.123456Z",
    }
    payload.update(overrides)
    return payload


def test_image_provenance_has_exact_durable_dump_and_run_url() -> None:
    provenance = ImageProvenance.model_validate(image_payload())

    assert provenance.model_dump(mode="json") == image_payload()
    assert provenance.workflow_run.url == (
        "https://github.com/edu-llm/OLMo-core/actions/runs/987654321/attempts/2"
    )
    assert provenance.workflow_run.job_workflow_ref == (
        "edu-llm/platform/.github/workflows/"
        "build-research-image.yml@refs/heads/main"
    )


def test_image_provenance_is_frozen_strict_and_forbids_extras() -> None:
    provenance = ImageProvenance.model_validate(image_payload())
    with pytest.raises(ValidationError):
        provenance.image_digest = "sha256:" + "d" * 64

    for payload in (
        image_payload(schema_version=2),
        image_payload(schema_version="1"),
        image_payload(unexpected=True),
    ):
        with pytest.raises(ValidationError):
            ImageProvenance.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("image_digest", "latest"),
        ("image_digest", "olmo:latest"),
        ("image_digest", "b" * 64),
        ("image_digest", "sha512:" + "b" * 64),
        ("image_digest", "sha256:" + "B" * 64),
        ("base_image_digest", "python:3.12"),
        ("base_image_digest", "sha256:short"),
        ("base_image_digest", "sha256:" + "C" * 64),
    ],
)
def test_image_provenance_rejects_tags_and_invalid_digests(
    field: str,
    value: str,
) -> None:
    with pytest.raises(ValidationError):
        ImageProvenance.model_validate(image_payload(**{field: value}))


@pytest.mark.parametrize(
    "ecr_repository",
    [
        "edullm-olmo-core",
        "sbsandbox-intern-",
        "sbsandbox-intern-Uppercase",
        "sbsandbox-intern-edullm:latest",
        "sbsandbox-intern-/olmo",
    ],
)
def test_image_provenance_rejects_invalid_ecr_repository(
    ecr_repository: str,
) -> None:
    with pytest.raises(ValidationError):
        ImageProvenance.model_validate(
            image_payload(ecr_repository=ecr_repository)
        )


@pytest.mark.parametrize(
    "source",
    [
        source_payload(verified=False),
        source_payload(clean=False),
        source_payload(commit_sha="A" * 40),
        source_payload(ref="main"),
        source_payload(unexpected=True),
    ],
)
def test_image_provenance_requires_valid_verified_clean_source(
    source: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ImageProvenance.model_validate(image_payload(source=source))


@pytest.mark.parametrize(
    "workflow",
    [
        workflow_payload(run_repository="edu-llm"),
        workflow_payload(run_repository="/OLMo-core"),
        workflow_payload(run_repository="edu-llm/OLMo-core/extra"),
        workflow_payload(run_repository="edu llm/OLMo-core"),
        workflow_payload(workflow_repository="edu-llm"),
        workflow_payload(workflow_repository="/platform"),
        workflow_payload(workflow_repository="edu-llm/platform/extra"),
        workflow_payload(workflow_repository="edu llm/platform"),
        workflow_payload(workflow_path="build-research-image.yml"),
        workflow_payload(workflow_path=".github/workflows/../build.yml"),
        workflow_payload(workflow_path=".github/workflows/build.txt"),
        workflow_payload(workflow_ref="main"),
        workflow_payload(workflow_ref="refs/pull/1/head"),
        workflow_payload(workflow_ref="refs/heads/main lock"),
        workflow_payload(run_id=0),
        workflow_payload(run_attempt=0),
        workflow_payload(run_id="987654321"),
        workflow_payload(run_attempt="2"),
        workflow_payload(extra=True),
    ],
)
def test_workflow_run_reference_is_strict_and_well_formed(
    workflow: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ImageProvenance.model_validate(image_payload(workflow_run=workflow))


def test_workflow_run_reference_is_frozen() -> None:
    workflow = GitHubWorkflowRunReference.model_validate(workflow_payload())
    with pytest.raises(ValidationError):
        workflow.run_attempt = 3


@pytest.mark.parametrize(
    "built_at",
    [
        "2026-07-25T18:30:00",
        "not-a-timestamp",
        123,
    ],
)
def test_image_provenance_rejects_invalid_timestamp(built_at: object) -> None:
    with pytest.raises(ValidationError):
        ImageProvenance.model_validate(image_payload(built_at=built_at))


def test_image_provenance_normalizes_offset_timestamp_to_utc() -> None:
    provenance = ImageProvenance.model_validate(
        image_payload(built_at="2026-07-25T13:30:00.123456-05:00")
    )
    assert provenance.model_dump(mode="json")["built_at"] == (
        "2026-07-25T18:30:00.123456Z"
    )


def test_resolve_image_reference_composes_exact_full_ecr_reference() -> None:
    provenance = ImageProvenance.model_validate(image_payload())
    account_id = synthetic_account_id()

    reference = resolve_image_reference(
        provenance,
        aws_account_id=account_id,
        region="us-east-2",
    )

    assert reference == (
        f"{account_id}.dkr.ecr.us-east-2.amazonaws.com/"
        f"{ECR_REPOSITORY}@{IMAGE_DIGEST}"
    )


@pytest.mark.parametrize(
    "aws_account_id",
    [
        "",
        "1" * 11,
        "1" * 13,
        "12345678901x",
        int(synthetic_account_id()),
    ],
)
def test_resolve_image_reference_rejects_invalid_account_ids(
    aws_account_id: Any,
) -> None:
    provenance = ImageProvenance.model_validate(image_payload())
    with pytest.raises((TypeError, ValueError)):
        resolve_image_reference(
            provenance,
            aws_account_id=aws_account_id,
            region="us-east-1",
        )


@pytest.mark.parametrize("region", ["us-west-2", "us-east-3", "", 1])
def test_resolve_image_reference_rejects_non_sandbox_region(region: Any) -> None:
    provenance = ImageProvenance.model_validate(image_payload())
    with pytest.raises((TypeError, ValueError)):
        resolve_image_reference(
            provenance,
            aws_account_id=synthetic_account_id(),
            region=region,
        )


def test_resolver_revalidates_constructed_provenance_fields() -> None:
    provenance = ImageProvenance.model_construct(
        **{
            **image_payload(),
            "ecr_repository": "unsafe",
            "image_digest": "latest",
        }
    )
    with pytest.raises(ValueError):
        resolve_image_reference(
            provenance,
            aws_account_id=synthetic_account_id(),
            region="us-east-1",
        )


def test_account_and_registry_are_never_persisted_or_represented() -> None:
    provenance = ImageProvenance.model_validate(image_payload())
    account_id = synthetic_account_id()
    registry_host = f"{account_id}.dkr.ecr.us-east-1.amazonaws.com"

    dumped = repr(provenance.model_dump(mode="json"))
    represented = repr(provenance)

    assert account_id not in dumped
    assert registry_host not in dumped
    assert account_id not in represented
    assert registry_host not in represented
    assert "aws_account_id" not in dumped
    assert "registry" not in dumped


def test_image_provenance_canonical_digest_is_stable_under_input_reordering() -> None:
    payload = image_payload()
    reordered = dict(reversed(list(payload.items())))
    source = payload["source"]
    workflow = payload["workflow_run"]
    assert isinstance(source, dict)
    assert isinstance(workflow, dict)
    reordered["source"] = dict(reversed(list(source.items())))
    reordered["workflow_run"] = dict(reversed(list(workflow.items())))

    baseline = ImageProvenance.model_validate(payload)
    reordered_model = ImageProvenance.model_validate(reordered)

    assert canonical_json_bytes(baseline) == canonical_json_bytes(reordered_model)
    assert sha256_digest(baseline) == sha256_digest(reordered_model)


def test_image_provenance_built_at_is_a_datetime_in_python() -> None:
    provenance = ImageProvenance.model_validate(image_payload())
    assert isinstance(provenance.built_at, datetime)
