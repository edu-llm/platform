import hashlib
import json
from decimal import Decimal

import pytest
from pydantic import Field

from edullm_platform.canonical import canonical_json_bytes, sha256_digest
from edullm_platform.contracts.base import ContractModel
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.policy import RequestFacts


def manifest_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "repository": "OLMo-core",
        "commit_sha": "a" * 40,
        "image_digest": "sha256:" + "b" * 64,
        "dataset_release": "dolma-2026-07",
        "command": ["python", "-m", "train"],
        "team": "modeling",
        "wandb_project": "olmo",
        "workload_profile": "gpu-training-smoke",
        "compute_profile": "gpu-single-node",
        "maximum_runtime_hours": "6",
        "maximum_attempts": 2,
        "checkpoint": {
            "interval_minutes": 30,
            "destination_prefix": "s3://sbsandbox-intern-edullm-checkpoints/runs/",
            "resume_required": True,
        },
    }


def test_canonical_json_bytes_sorts_hand_built_payload() -> None:
    manifest = RunManifest.model_validate(manifest_payload())
    payload = manifest.model_dump(mode="json", by_alias=True, exclude_none=False)
    reversed_payload = dict(reversed(list(payload.items())))
    expected = json.dumps(
        reversed_payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert canonical_json_bytes(manifest) == expected


def test_canonical_json_bytes_sorts_keys() -> None:
    manifest = RunManifest.model_validate(manifest_payload())
    encoded = canonical_json_bytes(manifest)
    payload = json.loads(encoded)
    assert list(payload.keys()) == sorted(payload.keys())


def test_canonical_json_bytes_uses_compact_separators() -> None:
    manifest = RunManifest.model_validate(manifest_payload())
    encoded = canonical_json_bytes(manifest)
    text = encoded.decode("utf-8")
    assert ": " not in text
    assert ", " not in text


def test_canonical_json_bytes_returns_utf8() -> None:
    payload = manifest_payload()
    payload["team"] = "équipe"
    manifest = RunManifest.model_validate(payload)
    encoded = canonical_json_bytes(manifest)
    assert isinstance(encoded, bytes)
    text = encoded.decode("utf-8")
    assert "équipe" in text
    assert "\\u00e9" not in text


def test_canonical_json_bytes_rejects_non_finite_floats() -> None:
    class FloatPayload(ContractModel):
        value: float = Field(strict=False)

    model = FloatPayload.model_construct(value=float("nan"))
    with pytest.raises(ValueError, match="nan"):
        canonical_json_bytes(model)


def test_canonical_json_bytes_includes_null_fields() -> None:
    payload = manifest_payload()
    payload["maximum_attempts"] = 1
    payload["checkpoint"] = None
    manifest = RunManifest.model_validate(payload)
    encoded = canonical_json_bytes(manifest)
    assert b'"checkpoint":null' in encoded


def test_canonical_json_bytes_uses_field_aliases() -> None:
    class AliasPayload(ContractModel):
        plain_field: str = Field(alias="aliased_field")

    model = AliasPayload.model_validate({"aliased_field": "value"})
    encoded = canonical_json_bytes(model)
    assert b'"aliased_field":"value"' in encoded
    assert b'"plain_field"' not in encoded


@pytest.mark.parametrize("runtime_hours", ["6", "6.0", "6.00"])
def test_manifest_decimal_spellings_hash_identically(runtime_hours: str) -> None:
    baseline = RunManifest.model_validate(manifest_payload())
    payload = manifest_payload()
    payload["maximum_runtime_hours"] = runtime_hours
    manifest = RunManifest.model_validate(payload)
    assert sha256_digest(baseline) == sha256_digest(manifest)
    encoded = canonical_json_bytes(manifest).decode("utf-8")
    assert '"maximum_runtime_hours":"6"' in encoded


def test_manifest_decimal_scientific_notation_serializes_as_integer() -> None:
    payload = manifest_payload()
    payload["maximum_runtime_hours"] = Decimal("1E+9")
    manifest = RunManifest.model_validate(payload)
    encoded = canonical_json_bytes(manifest).decode("utf-8")
    assert '"maximum_runtime_hours":"1000000000"' in encoded


def request_facts_payload(**overrides: object) -> dict[str, object]:
    payload = {
        "claimed_team": "modeling",
        "repository_registered": True,
        "dataset_registered": True,
        "dataset_is_a_corpus": True,
        "compute_profile_registered": True,
        "immutable_revision": True,
        "immutable_image": True,
        "image_scan_reviewed": True,
        "estimated_cost_usd": "0",
        "maximum_runtime_hours": "1",
        "maximum_attempts": 1,
    }
    payload.update(overrides)
    return payload


def test_negative_zero_and_zero_hash_identically() -> None:
    zero = RequestFacts.model_validate(request_facts_payload(estimated_cost_usd="0"))
    negative_zero = RequestFacts.model_validate(
        request_facts_payload(estimated_cost_usd=Decimal("-0"))
    )
    assert sha256_digest(zero) == sha256_digest(negative_zero)
    encoded = canonical_json_bytes(negative_zero).decode("utf-8")
    assert '"estimated_cost_usd":"0"' in encoded


@pytest.mark.parametrize("runtime_hours", ["0.0000001", "0.00000010"])
def test_sub_micro_decimal_spellings_hash_identically(runtime_hours: str) -> None:
    baseline = RunManifest.model_validate(manifest_payload())
    payload = manifest_payload()
    payload["maximum_runtime_hours"] = runtime_hours
    manifest = RunManifest.model_validate(payload)
    assert sha256_digest(baseline) != sha256_digest(manifest)
    exponent_form = RunManifest.model_validate(
        {**manifest_payload(), "maximum_runtime_hours": Decimal("1E-7")}
    )
    assert sha256_digest(manifest) == sha256_digest(exponent_form)
    encoded = canonical_json_bytes(exponent_form).decode("utf-8")
    assert '"maximum_runtime_hours":"0.0000001"' in encoded
    assert "E" not in encoded.split('"maximum_runtime_hours"')[1].split(",")[0]


@pytest.mark.parametrize(
    "field",
    [
        "repository",
        "commit_sha",
        "image_digest",
        "dataset_release",
        "command",
        "team",
        "wandb_project",
        "workload_profile",
        "compute_profile",
        "maximum_runtime_hours",
        "maximum_attempts",
        "checkpoint",
    ],
)
def test_manifest_digest_changes_when_single_field_differs(field: str) -> None:
    baseline = RunManifest.model_validate(manifest_payload())
    mutated_payload = manifest_payload()
    if field == "repository":
        mutated_payload[field] = "dolma"
    elif field == "commit_sha":
        mutated_payload[field] = "c" * 40
    elif field == "image_digest":
        mutated_payload[field] = "sha256:" + "d" * 64
    elif field == "dataset_release":
        mutated_payload[field] = "dolma-2026-08"
    elif field == "command":
        mutated_payload[field] = ["python", "-m", "eval"]
    elif field == "team":
        mutated_payload[field] = "infra"
    elif field == "wandb_project":
        mutated_payload[field] = "dolma"
    elif field == "workload_profile":
        mutated_payload[field] = "cpu-smoke"
    elif field == "compute_profile":
        mutated_payload[field] = "cpu-single-node"
    elif field == "maximum_runtime_hours":
        mutated_payload[field] = "7"
    elif field == "maximum_attempts":
        mutated_payload[field] = 3
    elif field == "checkpoint":
        mutated_payload[field] = {
            "interval_minutes": 45,
            "destination_prefix": "s3://sbsandbox-intern-edullm-checkpoints/runs/",
            "resume_required": True,
        }
    else:
        raise AssertionError(f"unhandled field: {field}")
    mutated = RunManifest.model_validate(mutated_payload)
    assert sha256_digest(baseline) != sha256_digest(mutated)


def test_sha256_digest_format() -> None:
    manifest = RunManifest.model_validate(manifest_payload())
    digest = sha256_digest(manifest)
    expected = hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()
    assert digest == f"sha256:{expected}"
