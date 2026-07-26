from collections.abc import Callable

import pytest
from pydantic import ValidationError

from edullm_platform.canonical import canonical_json_bytes, sha256_digest
from edullm_platform.contracts.base import ContractModel
from edullm_platform.contracts.dataset import (
    DatasetAccessPolicy,
    DatasetObject,
    DatasetRelease,
    DatasetSchemaRef,
)
from edullm_platform.contracts.vocabulary import DataClassification

STABLE_RUN_ID = "run_01994f2a-1c00-7c3b-8f4d-2a5b6c7d8e9f"
STABLE_ATTEMPT_ID = "att_01994f2a-1c00-7c3b-9a1b-2c3d4e5f6a7b"

RELEASE_ID = "dolma-2026-07"
PARENT_RELEASE_ID = "dolma-2026-06"
DATASET_URI = "s3://sbsandbox-intern-edullm-datasets/dolma/2026-07/"
SCHEMA_DIGEST = "sha256:" + "d" * 64
FIRST_OBJECT_DIGEST = "sha256:" + "e" * 64
SECOND_OBJECT_DIGEST = "sha256:" + "f" * 64

DATASET_RELEASE_DIGEST = "sha256:9d135bc5652f76e407311acc1227cf5059b6ac9a1c242f94bd11e9e1a2b9ae63"

OUTSIDE_SANDBOX_PREFIXES = (
    "s3://edullm-datasets/dolma/2026-07/",
    "s3://sbsandbox-intern/dolma/2026-07/",
    "s3://not-sbsandbox-intern-datasets/dolma/2026-07/",
    "s3://SBSANDBOX-INTERN-datasets/dolma/2026-07/",
    "s3://sbsandbox-intern-edullm-datasets/dolma/2026-07",
    "s3://sbsandbox-intern-edullm-datasets/",
    "s3://sbsandbox-intern-/dolma/2026-07/",
    "https://sbsandbox-intern-edullm-datasets.s3.amazonaws.com/dolma/2026-07/",
)


def dataset_object_payloads() -> list[dict[str, object]]:
    return [
        {
            "key": "documents/part-00000.jsonl.zst",
            "checksum": FIRST_OBJECT_DIGEST,
            "s3_version_id": "3sL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY",
        },
        {
            "key": "documents/part-00001.jsonl.zst",
            "checksum": SECOND_OBJECT_DIGEST,
            "s3_version_id": "PHtexPGjH2y.zBgT8LmB7wwLI2mpbz.k",
        },
    ]


def dataset_release_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "release_id": RELEASE_ID,
        "uri": DATASET_URI,
        "created_at": "2026-07-25T12:00:00Z",
        "objects": dataset_object_payloads(),
        "schema_ref": {"name": "dolma-document", "digest": SCHEMA_DIGEST},
        "derived_from": [PARENT_RELEASE_ID],
        "produced_by_run_id": STABLE_RUN_ID,
        "licence": "ODC-By-1.0",
        "data_classification": "internal",
        "access_policy": {"readable_by_team_ids": ["evaluation", "modeling"]},
    }
    payload.update(overrides)
    return payload


def reverse_mapping_order(value: object) -> object:
    if isinstance(value, dict):
        items = reversed(list(value.items()))
        return {key: reverse_mapping_order(item) for key, item in items}
    if isinstance(value, list):
        return [reverse_mapping_order(item) for item in value]
    return value


def assert_validation_error(
    error: ValidationError,
    *,
    error_type: str,
    loc: tuple[str | int, ...],
    message_fragment: str | None = None,
) -> None:
    matching_errors = [
        item for item in error.errors() if item["type"] == error_type and item["loc"] == loc
    ]
    assert matching_errors, (
        f"expected error type {error_type!r} at loc {loc!r}, got {error.errors()}"
    )
    if message_fragment is not None:
        assert any(message_fragment in item["msg"] for item in matching_errors), (
            f"expected {message_fragment!r} in {error_type!r} messages at {loc!r}, "
            f"got {[item['msg'] for item in matching_errors]}"
        )


def test_dataset_release_validates_a_complete_payload() -> None:
    release = DatasetRelease.model_validate(dataset_release_payload())
    assert release.release_id == RELEASE_ID
    assert release.uri == DATASET_URI
    assert release.data_classification is DataClassification.INTERNAL
    assert release.licence == "ODC-By-1.0"
    assert release.derived_from == (PARENT_RELEASE_ID,)
    assert release.produced_by_run_id == STABLE_RUN_ID
    assert release.schema_ref == DatasetSchemaRef(name="dolma-document", digest=SCHEMA_DIGEST)
    assert len(release.objects) == 2


def test_every_object_carries_a_checksum_and_an_s3_version_id() -> None:
    release = DatasetRelease.model_validate(dataset_release_payload())
    first, second = release.objects
    assert isinstance(first, DatasetObject)
    assert first.key == "documents/part-00000.jsonl.zst"
    assert first.checksum == FIRST_OBJECT_DIGEST
    assert first.s3_version_id == "3sL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY"
    assert second.s3_version_id != first.s3_version_id


@pytest.mark.parametrize("field", ["checksum", "s3_version_id"])
def test_object_provenance_fields_are_mandatory(field: str) -> None:
    objects = dataset_object_payloads()
    del objects[0][field]
    with pytest.raises(ValidationError) as exc_info:
        DatasetRelease.model_validate(dataset_release_payload(objects=objects))
    assert_validation_error(exc_info.value, error_type="missing", loc=("objects", 0, field))


@pytest.mark.parametrize(
    "checksum",
    ["", "e" * 64, "sha256:" + "E" * 64, "md5:" + "e" * 32],
    ids=["empty", "unprefixed", "uppercase", "md5"],
)
def test_object_checksum_must_be_a_sha256_digest(checksum: str) -> None:
    objects = dataset_object_payloads()
    objects[0]["checksum"] = checksum
    with pytest.raises(ValidationError) as exc_info:
        DatasetRelease.model_validate(dataset_release_payload(objects=objects))
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("objects", 0, "checksum"),
    )


@pytest.mark.parametrize(
    "s3_version_id",
    ["", "has spaces", "has/slash"],
    ids=["empty", "spaces", "slash"],
)
def test_object_s3_version_id_must_be_well_formed(s3_version_id: str) -> None:
    objects = dataset_object_payloads()
    objects[0]["s3_version_id"] = s3_version_id
    with pytest.raises(ValidationError) as exc_info:
        DatasetRelease.model_validate(dataset_release_payload(objects=objects))
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("objects", 0, "s3_version_id"),
    )


def test_dataset_release_requires_at_least_one_object() -> None:
    with pytest.raises(ValidationError) as exc_info:
        DatasetRelease.model_validate(dataset_release_payload(objects=[]))
    assert_validation_error(exc_info.value, error_type="too_short", loc=("objects",))


def test_dataset_release_rejects_duplicate_object_keys() -> None:
    objects = dataset_object_payloads()
    objects[1]["key"] = objects[0]["key"]
    with pytest.raises(ValidationError) as exc_info:
        DatasetRelease.model_validate(dataset_release_payload(objects=objects))
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=(),
        message_fragment="ascending key order",
    )


def test_dataset_release_rejects_objects_listed_out_of_key_order() -> None:
    objects = list(reversed(dataset_object_payloads()))
    with pytest.raises(ValidationError) as exc_info:
        DatasetRelease.model_validate(dataset_release_payload(objects=objects))
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=(),
        message_fragment="ascending key order",
    )


def test_dataset_release_rejects_an_unordered_object_collection() -> None:
    with pytest.raises(ValidationError) as exc_info:
        DatasetRelease.model_validate(dataset_release_payload(objects="documents/"))
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=("objects",),
        message_fragment="ordered sequences must be provided as a list or tuple",
    )


@pytest.mark.parametrize("uri", OUTSIDE_SANDBOX_PREFIXES)
def test_dataset_location_outside_the_sandbox_bucket_namespace_is_rejected(uri: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        DatasetRelease.model_validate(dataset_release_payload(uri=uri))
    assert_validation_error(exc_info.value, error_type="string_pattern_mismatch", loc=("uri",))


@pytest.mark.parametrize(
    "data_classification",
    ["top_secret", "", "INTERNAL", "Internal", "public-ish"],
    ids=["unknown", "empty", "uppercase", "capitalized", "kebab-case"],
)
def test_unknown_data_classification_fails_closed(data_classification: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        DatasetRelease.model_validate(
            dataset_release_payload(data_classification=data_classification)
        )
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=("data_classification",),
    )


@pytest.mark.parametrize("data_classification", list(DataClassification))
def test_every_known_data_classification_is_accepted(
    data_classification: DataClassification,
) -> None:
    release = DatasetRelease.model_validate(
        dataset_release_payload(data_classification=data_classification.value)
    )
    assert release.data_classification is data_classification


def test_access_policy_names_the_teams_that_may_read_the_release() -> None:
    release = DatasetRelease.model_validate(dataset_release_payload())
    assert release.access_policy.readable_by_team_ids == ("evaluation", "modeling")
    assert release.is_readable_by("modeling") is True
    assert release.is_readable_by("evaluation") is True
    assert release.is_readable_by("infrastructure") is False


def test_access_policy_must_name_at_least_one_team() -> None:
    with pytest.raises(ValidationError) as exc_info:
        DatasetRelease.model_validate(
            dataset_release_payload(access_policy={"readable_by_team_ids": []})
        )
    assert_validation_error(
        exc_info.value,
        error_type="too_short",
        loc=("access_policy", "readable_by_team_ids"),
    )


def test_access_policy_rejects_duplicate_team_ids() -> None:
    with pytest.raises(ValidationError) as exc_info:
        DatasetRelease.model_validate(
            dataset_release_payload(
                access_policy={"readable_by_team_ids": ["modeling", "modeling"]}
            )
        )
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=("access_policy",),
        message_fragment="unique and sorted",
    )


def test_access_policy_is_usable_on_its_own() -> None:
    policy = DatasetAccessPolicy.model_validate({"readable_by_team_ids": ["modeling"]})
    assert policy.permits("modeling") is True
    assert policy.permits("evaluation") is False


def test_dataset_release_records_lineage_back_to_its_parent_release() -> None:
    release = DatasetRelease.model_validate(
        dataset_release_payload(produced_by_run_id=None)
    )
    assert release.derived_from == (PARENT_RELEASE_ID,)
    assert release.produced_by_run_id is None


def test_a_root_release_may_name_only_the_run_that_produced_it() -> None:
    release = DatasetRelease.model_validate(dataset_release_payload(derived_from=[]))
    assert release.derived_from == ()
    assert release.produced_by_run_id == STABLE_RUN_ID


def test_a_release_without_any_lineage_fails_closed() -> None:
    with pytest.raises(ValidationError) as exc_info:
        DatasetRelease.model_validate(
            dataset_release_payload(derived_from=[], produced_by_run_id=None)
        )
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=(),
        message_fragment="parent release or the run that produced it",
    )


def test_a_release_cannot_be_derived_from_itself() -> None:
    with pytest.raises(ValidationError) as exc_info:
        DatasetRelease.model_validate(dataset_release_payload(derived_from=[RELEASE_ID]))
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=(),
        message_fragment="must not be derived from itself",
    )


def test_lineage_run_reference_must_be_a_run_id() -> None:
    with pytest.raises(ValidationError) as exc_info:
        DatasetRelease.model_validate(dataset_release_payload(produced_by_run_id=STABLE_ATTEMPT_ID))
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("produced_by_run_id",),
    )


@pytest.mark.parametrize(
    "release_id",
    ["", "Dolma-2026-07", "dolma 2026 07", "dolma/2026-07", "-dolma"],
    ids=["empty", "uppercase", "spaces", "slash", "leading-dash"],
)
def test_release_identifier_must_be_a_stable_slug(release_id: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        DatasetRelease.model_validate(dataset_release_payload(release_id=release_id))
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("release_id",),
    )


def test_schema_reference_is_pinned_by_content_digest() -> None:
    with pytest.raises(ValidationError) as exc_info:
        DatasetRelease.model_validate(
            dataset_release_payload(schema_ref={"name": "dolma-document", "digest": "v1"})
        )
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("schema_ref", "digest"),
    )


@pytest.mark.parametrize(
    "licence",
    ["", "ODC By 1.0", "see LICENSE file"],
    ids=["empty", "spaces", "prose"],
)
def test_licence_must_be_an_identifier_not_prose(licence: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        DatasetRelease.model_validate(dataset_release_payload(licence=licence))
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("licence",),
    )


def test_unknown_schema_version_fails_closed() -> None:
    for schema_version in (0, 2, "1", None):
        with pytest.raises(ValidationError) as exc_info:
            DatasetRelease.model_validate(
                dataset_release_payload(schema_version=schema_version)
            )
        assert_validation_error(
            exc_info.value,
            error_type="literal_error",
            loc=("schema_version",),
        )


@pytest.mark.parametrize(
    ("model_type", "payload_factory"),
    [(DatasetRelease, dataset_release_payload)],
    ids=["dataset-release"],
)
def test_reordering_input_fields_does_not_change_the_digest(
    model_type: type[ContractModel],
    payload_factory: Callable[..., dict[str, object]],
) -> None:
    payload = payload_factory()
    reordered = reverse_mapping_order(payload)
    assert list(reordered) != list(payload)
    baseline = model_type.model_validate(payload)
    shuffled = model_type.model_validate(reordered)
    assert canonical_json_bytes(baseline) == canonical_json_bytes(shuffled)
    assert sha256_digest(baseline) == sha256_digest(shuffled)


def test_digest_is_stable_across_repeated_validation() -> None:
    first = DatasetRelease.model_validate(dataset_release_payload())
    second = DatasetRelease.model_validate(dataset_release_payload())
    assert sha256_digest(first) == sha256_digest(second)
    assert sha256_digest(first) == DATASET_RELEASE_DIGEST


def test_dataset_timestamps_serialize_as_utc() -> None:
    release = DatasetRelease.model_validate(
        dataset_release_payload(created_at="2026-07-25T07:00:00-05:00")
    )
    assert b'"created_at":"2026-07-25T12:00:00.000000Z"' in canonical_json_bytes(release)
    assert sha256_digest(release) == DATASET_RELEASE_DIGEST


def test_dataset_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError) as exc_info:
        DatasetRelease.model_validate(dataset_release_payload(row_count=1000))
    assert_validation_error(exc_info.value, error_type="extra_forbidden", loc=("row_count",))


def test_dataset_models_are_frozen() -> None:
    release = DatasetRelease.model_validate(dataset_release_payload())
    with pytest.raises(ValidationError) as exc_info:
        release.licence = "CC-BY-4.0"
    assert_validation_error(exc_info.value, error_type="frozen_instance", loc=("licence",))
