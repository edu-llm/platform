import json
import re
from enum import StrEnum

import pytest
from pydantic import ValidationError

from edullm_platform.canonical import canonical_json_bytes
from edullm_platform.contracts import policy
from edullm_platform.contracts.base import ContractModel
from edullm_platform.contracts.vocabulary import (
    ApprovalClass,
    DataClassification,
    JobType,
    RetentionClass,
)

VOCABULARY_VALUE_PATTERN = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")

JOB_TYPE_MEMBERS = {
    "CORPUS_PREPROCESSING": "corpus_preprocessing",
    "TOKENIZER_TRAINING": "tokenizer_training",
    "MODEL_PRETRAINING": "model_pretraining",
    "MODEL_FINE_TUNING": "model_fine_tuning",
    "MODEL_EVALUATION": "model_evaluation",
    "BATCH_INFERENCE": "batch_inference",
}

RETENTION_CLASS_MEMBERS = {
    "TRANSIENT": "transient",
    "STANDARD": "standard",
    "LONG_LIVED": "long_lived",
    "PERMANENT": "permanent",
}

DATA_CLASSIFICATION_MEMBERS = {
    "PUBLIC": "public",
    "INTERNAL": "internal",
    "RESTRICTED": "restricted",
}

APPROVAL_CLASS_MEMBERS = {
    "AUTOMATIC": "automatic",
    "ROUTINE": "routine",
    "EXCEPTION": "exception",
}

VOCABULARIES: tuple[tuple[type[StrEnum], dict[str, str]], ...] = (
    (JobType, JOB_TYPE_MEMBERS),
    (RetentionClass, RETENTION_CLASS_MEMBERS),
    (DataClassification, DATA_CLASSIFICATION_MEMBERS),
    (ApprovalClass, APPROVAL_CLASS_MEMBERS),
)

VOCABULARY_TYPES = tuple(vocabulary for vocabulary, _ in VOCABULARIES)

VOCABULARY_IDS = ("job-type", "retention-class", "data-classification", "approval-class")

VOCABULARY_FIELDS = ("job_type", "retention_class", "data_classification", "approval_class")


class WorkRecord(ContractModel):
    job_type: JobType
    retention_class: RetentionClass
    data_classification: DataClassification
    approval_class: ApprovalClass


def work_record_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "job_type": JobType.MODEL_PRETRAINING,
        "retention_class": RetentionClass.STANDARD,
        "data_classification": DataClassification.INTERNAL,
        "approval_class": ApprovalClass.ROUTINE,
    }
    payload.update(overrides)
    return payload


def work_record_json(**overrides: object) -> str:
    payload: dict[str, object] = {
        "job_type": "model_pretraining",
        "retention_class": "standard",
        "data_classification": "internal",
        "approval_class": "routine",
    }
    payload.update(overrides)
    return json.dumps(payload)


def assert_validation_error(
    error: ValidationError,
    *,
    error_type: str,
    loc: tuple[str | int, ...],
) -> None:
    matching_errors = [
        item for item in error.errors() if item["type"] == error_type and item["loc"] == loc
    ]
    assert matching_errors, (
        f"expected error type {error_type!r} at loc {loc!r}, got {error.errors()}"
    )


@pytest.mark.parametrize(("vocabulary", "expected_members"), VOCABULARIES, ids=VOCABULARY_IDS)
def test_vocabulary_members_are_stable(
    vocabulary: type[StrEnum],
    expected_members: dict[str, str],
) -> None:
    assert {member.name: member.value for member in vocabulary} == expected_members


@pytest.mark.parametrize(("vocabulary", "expected_members"), VOCABULARIES, ids=VOCABULARY_IDS)
def test_vocabulary_is_a_string_enumeration_without_aliases(
    vocabulary: type[StrEnum],
    expected_members: dict[str, str],
) -> None:
    assert issubclass(vocabulary, StrEnum)
    assert len(vocabulary.__members__) == len(expected_members)
    assert len({member.value for member in vocabulary}) == len(expected_members)


@pytest.mark.parametrize("vocabulary", VOCABULARY_TYPES, ids=VOCABULARY_IDS)
def test_vocabulary_values_are_lowercase_snake_case(vocabulary: type[StrEnum]) -> None:
    for member in vocabulary:
        assert VOCABULARY_VALUE_PATTERN.fullmatch(member.value) is not None, member.value


def test_authorization_vocabulary_reuses_the_policy_approval_class() -> None:
    assert ApprovalClass is policy.ApprovalClass


def test_work_record_accepts_every_vocabulary_member() -> None:
    for job_type in JobType:
        for retention_class in RetentionClass:
            record = WorkRecord.model_validate(
                work_record_payload(job_type=job_type, retention_class=retention_class)
            )
            assert record.job_type is job_type
            assert record.retention_class is retention_class
    for data_classification in DataClassification:
        for approval_class in ApprovalClass:
            record = WorkRecord.model_validate(
                work_record_payload(
                    data_classification=data_classification,
                    approval_class=approval_class,
                )
            )
            assert record.data_classification is data_classification
            assert record.approval_class is approval_class


def test_work_record_accepts_known_values_from_json() -> None:
    record = WorkRecord.model_validate_json(work_record_json())
    assert record.job_type is JobType.MODEL_PRETRAINING
    assert record.retention_class is RetentionClass.STANDARD
    assert record.data_classification is DataClassification.INTERNAL
    assert record.approval_class is ApprovalClass.ROUTINE


@pytest.mark.parametrize("field", VOCABULARY_FIELDS)
@pytest.mark.parametrize(
    "value",
    ["unknown_value", "", "ROUTINE", "Standard", "model-pretraining"],
    ids=["unknown", "empty", "uppercase", "capitalized", "kebab-case"],
)
def test_vocabulary_field_rejects_unknown_json_value(field: str, value: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        WorkRecord.model_validate_json(work_record_json(**{field: value}))
    assert_validation_error(exc_info.value, error_type="enum", loc=(field,))


@pytest.mark.parametrize("field", VOCABULARY_FIELDS)
@pytest.mark.parametrize(
    "value",
    ["unknown_value", "model_pretraining", "routine", 1, None],
    ids=["unknown", "known-job-value", "known-approval-value", "integer", "none"],
)
def test_vocabulary_field_requires_members_in_python_mode(field: str, value: object) -> None:
    with pytest.raises(ValidationError) as exc_info:
        WorkRecord.model_validate(work_record_payload(**{field: value}))
    assert_validation_error(exc_info.value, error_type="is_instance_of", loc=(field,))


@pytest.mark.parametrize("field", VOCABULARY_FIELDS)
def test_vocabulary_field_rejects_members_from_another_vocabulary(field: str) -> None:
    foreign_members: dict[str, StrEnum] = {
        "job_type": RetentionClass.STANDARD,
        "retention_class": JobType.MODEL_PRETRAINING,
        "data_classification": ApprovalClass.ROUTINE,
        "approval_class": DataClassification.INTERNAL,
    }
    with pytest.raises(ValidationError) as exc_info:
        WorkRecord.model_validate(work_record_payload(**{field: foreign_members[field]}))
    assert_validation_error(exc_info.value, error_type="is_instance_of", loc=(field,))


def test_work_record_serializes_vocabulary_values_as_plain_strings() -> None:
    record = WorkRecord.model_validate(work_record_payload())
    assert canonical_json_bytes(record) == (
        b'{"approval_class":"routine","data_classification":"internal",'
        b'"job_type":"model_pretraining","retention_class":"standard"}'
    )
