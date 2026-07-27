import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from edullm_platform.canonical import canonical_json_bytes, sha256_digest
from edullm_platform.contracts.admission import (
    AdmissionReason,
    ApprovalEnvironment,
    DecisionRecord,
    IntentRecord,
)
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.policy import ApprovalClass
from edullm_platform.manifest_helpers import load_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADMISSION_ROLE_PATH = PROJECT_ROOT / "infra" / "iam" / "admission-role.yaml"

RUN_ID = "run_0198f0a1-2b3c-7d4e-8f01-23456789abcd"
RECORDED_AT = "2026-07-27T09:15:30.123456Z"

REQUIRED_INTENT_FIELDS = tuple(
    name for name, field in IntentRecord.model_fields.items() if field.is_required()
)
REQUIRED_DECISION_FIELDS = tuple(
    name for name, field in DecisionRecord.model_fields.items() if field.is_required()
)

REFUSING_REASONS = tuple(
    reason for reason in AdmissionReason if reason is not AdmissionReason.ACCEPTED
)


def routine_manifest() -> RunManifest:
    return load_manifest(PROJECT_ROOT / "fixtures" / "manifests" / "cpu-routine.yaml")


def manifest_payload() -> dict[str, object]:
    return dict(routine_manifest().model_dump(mode="json"))


def manifest_digest() -> str:
    return sha256_digest(routine_manifest())


def workflow_run_payload() -> dict[str, object]:
    return {
        "run_repository": "edu-llm/platform",
        "workflow_repository": "edu-llm/platform",
        "workflow_path": ".github/workflows/submit-run.yml",
        "workflow_ref": "refs/heads/main",
        "run_id": 1704,
        "run_attempt": 1,
    }


def authorization_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "submitter": "caiiris",
        "approver": "ericrcwu001",
        "granted": True,
        "approval_class": "routine",
        "approval_scope": "organization",
        "claimed_team": "data-prep",
        "team_verified": False,
        "reason": "routine_approved_by_lead_or_admin",
    }
    payload.update(overrides)
    return payload


def refused_authorization_payload(**overrides: object) -> dict[str, object]:
    return authorization_payload(
        granted=False,
        reason="approver_lacks_lead_or_admin_role",
        **overrides,
    )


def cost_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "hourly_rate_usd": "1.428",
        "nodes": 1,
        "maximum_runtime_hours": "2",
        "maximum_attempts": 1,
        "cells": 1,
    }
    payload.update(overrides)
    return payload


def intent_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "run_id": RUN_ID,
        "submitter": "caiiris",
        "manifest": manifest_payload(),
        "manifest_sha256": manifest_digest(),
        "approving_environment": "run-approval-lead",
        "workflow_run": workflow_run_payload(),
        "recorded_at": RECORDED_AT,
    }
    payload.update(overrides)
    return payload


def decision_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "run_id": RUN_ID,
        "manifest_sha256": manifest_digest(),
        "policy_version": "v1",
        "approval_class": "routine",
        "approving_environment": "run-approval-lead",
        "authorization": authorization_payload(),
        "cost": cost_payload(),
        "accepted": True,
        "reason": "accepted",
        "detail": "Admitted as routine under policy v1.",
        "recorded_at": RECORDED_AT,
    }
    payload.update(overrides)
    return payload


def refused_decision_payload(reason: AdmissionReason, **overrides: object) -> dict[str, object]:
    return decision_payload(
        **{
            "accepted": False,
            "reason": reason.value,
            "authorization": refused_authorization_payload(),
            "detail": f"Refused: {reason.value}.",
            **overrides,
        }
    )


def assert_validation_error(
    error: ValidationError,
    *,
    error_type: str,
    message_fragment: str | None = None,
) -> None:
    matching_errors = [item for item in error.errors() if item["type"] == error_type]
    assert matching_errors, f"expected error type {error_type!r}, got {error.errors()}"
    if message_fragment is not None:
        assert any(message_fragment in item["msg"] for item in matching_errors), (
            f"expected {message_fragment!r} in {error_type!r} messages, "
            f"got {[item['msg'] for item in matching_errors]}"
        )


def trust_policy_subjects() -> list[str]:
    template: Any = yaml.safe_load(ADMISSION_ROLE_PATH.read_text(encoding="utf-8"))
    statements = template["Resources"]["AdmissionRole"]["Properties"][
        "AssumeRolePolicyDocument"
    ]["Statement"]
    assert len(statements) == 1, f"expected one trust statement, got {statements}"
    subjects = statements[0]["Condition"]["StringEquals"][
        "token.actions.githubusercontent.com:sub"
    ]
    assert isinstance(subjects, list), (
        "the trust policy must enumerate subjects; a single string or a wildcard would "
        "accept a subject minted for an auto-created, ungated environment"
    )
    return subjects


def trust_policy_environments() -> set[str]:
    return {subject.rsplit(":environment:", maxsplit=1)[1] for subject in trust_policy_subjects()}


@pytest.mark.parametrize(
    ("approval_class", "expected"),
    [
        (ApprovalClass.ROUTINE, ApprovalEnvironment.LEAD),
        (ApprovalClass.EXCEPTION, ApprovalEnvironment.ADMIN),
    ],
)
def test_policy_picks_the_gate_a_classification_must_pass_through(
    approval_class: ApprovalClass,
    expected: ApprovalEnvironment,
) -> None:
    assert ApprovalEnvironment.for_approval_class(approval_class) is expected


def test_the_two_gate_names_are_the_literals_the_trust_policy_pins() -> None:
    assert ApprovalEnvironment.LEAD.value == "run-approval-lead"
    assert ApprovalEnvironment.ADMIN.value == "run-approval-admin"
    assert {environment.value for environment in ApprovalEnvironment} == {
        "run-approval-lead",
        "run-approval-admin",
    }


def test_every_gate_name_is_enumerated_in_the_admission_role_trust_policy() -> None:
    assert trust_policy_environments() == {
        environment.value for environment in ApprovalEnvironment
    }, (
        "the trust policy enumerates these names rather than matching them, so renaming a "
        "gate here without renaming it there revokes AWS trust and surfaces as an "
        "AssumeRole denial that reads like a broken role ARN"
    )


@pytest.mark.parametrize("environment", list(ApprovalEnvironment))
def test_each_gate_is_trusted_under_exactly_one_subject(
    environment: ApprovalEnvironment,
) -> None:
    matching = [
        subject
        for subject in trust_policy_subjects()
        if subject.endswith(f":environment:{environment.value}")
    ]
    assert len(matching) == 1, f"expected one subject for {environment.value!r}, got {matching}"


def test_no_approval_class_routes_to_a_gate_aws_does_not_trust() -> None:
    routed = {
        ApprovalEnvironment.for_approval_class(approval_class).value
        for approval_class in ApprovalClass
    }
    assert routed == trust_policy_environments()


@pytest.mark.parametrize("environment", list(ApprovalEnvironment))
def test_a_gate_name_parses_back_from_the_string_a_subject_claim_carries(
    environment: ApprovalEnvironment,
) -> None:
    payload = intent_payload(approving_environment=environment.value)
    assert IntentRecord.model_validate(payload).approving_environment is environment


@pytest.mark.parametrize(
    "environment", ["run-approval", "RUN-APPROVAL-LEAD", "run_approval_lead", "", "production"]
)
def test_a_gate_this_system_never_configured_is_not_an_approving_environment(
    environment: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        IntentRecord.model_validate(intent_payload(approving_environment=environment))
    assert exc_info.value.errors()[0]["loc"] == ("approving_environment",)


def test_an_intent_record_says_what_was_asked_for_without_saying_it_was_allowed() -> None:
    record = IntentRecord.model_validate(intent_payload())
    document = record.model_dump(mode="json")

    assert "accepted" not in document
    assert not any(isinstance(value, bool) for value in document.values()), (
        "an intent record is written before the judgement it precedes, so no field of it "
        "may be readable as approval"
    )
    assert record.manifest == routine_manifest()
    assert record.manifest_sha256 == sha256_digest(record.manifest)


def test_an_intent_record_keys_the_same_run_as_the_decision_that_follows_it() -> None:
    intent = IntentRecord.model_validate(intent_payload())
    decision = DecisionRecord.model_validate(decision_payload())

    assert intent.run_id == decision.run_id
    assert intent.manifest_sha256 == decision.manifest_sha256


@pytest.mark.parametrize("field", REQUIRED_INTENT_FIELDS)
def test_intent_record_rejects_a_payload_that_omits_a_required_field(field: str) -> None:
    payload = intent_payload()
    del payload[field]
    with pytest.raises(ValidationError) as exc_info:
        IntentRecord.model_validate(payload)
    assert any(
        item["type"] == "missing" and item["loc"] == (field,)
        for item in exc_info.value.errors()
    ), f"expected a missing-field error naming {field!r}, got {exc_info.value.errors()}"


def test_intent_record_unknown_schema_version_fails_closed() -> None:
    with pytest.raises(ValidationError) as exc_info:
        IntentRecord.model_validate(intent_payload(schema_version=2))
    assert_validation_error(exc_info.value, error_type="literal_error")


def test_intent_record_round_trips_through_canonical_json() -> None:
    record = IntentRecord.model_validate(intent_payload())
    restored = IntentRecord.model_validate(json.loads(canonical_json_bytes(record)))

    assert restored == record


def test_a_decision_that_admits_a_run_records_the_whole_of_why() -> None:
    decision = DecisionRecord.model_validate(decision_payload())

    assert decision.accepted is True
    assert decision.reason is AdmissionReason.ACCEPTED
    assert decision.authorization is not None
    assert decision.authorization.granted is True
    assert decision.cost is not None
    assert decision.approving_environment is ApprovalEnvironment.for_approval_class(
        decision.approval_class
    )


@pytest.mark.parametrize("reason", REFUSING_REASONS)
def test_a_decision_claiming_acceptance_must_name_the_accepted_reason(
    reason: AdmissionReason,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        DecisionRecord.model_validate(decision_payload(accepted=True, reason=reason.value))
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="admission outcome must match the recorded reason",
    )


def test_a_decision_that_names_the_accepted_reason_must_claim_acceptance() -> None:
    with pytest.raises(ValidationError) as exc_info:
        DecisionRecord.model_validate(decision_payload(accepted=False))
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="admission outcome must match the recorded reason",
    )


@pytest.mark.parametrize(
    "authorization",
    [None, refused_authorization_payload()],
    ids=["no-authorization", "refused-authorization"],
)
def test_an_accepted_decision_must_carry_a_granted_authorization(
    authorization: dict[str, object] | None,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        DecisionRecord.model_validate(decision_payload(authorization=authorization))
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="an accepted decision must record a granted authorization",
    )


def test_an_accepted_decision_must_say_what_the_run_is_expected_to_cost() -> None:
    with pytest.raises(ValidationError) as exc_info:
        DecisionRecord.model_validate(decision_payload(cost=None))
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="an accepted decision must record what it is expected to cost",
    )


@pytest.mark.parametrize(
    ("approval_class", "approving_environment"),
    [
        (ApprovalClass.ROUTINE, ApprovalEnvironment.ADMIN),
        (ApprovalClass.EXCEPTION, ApprovalEnvironment.LEAD),
    ],
)
def test_an_accepted_decision_must_have_passed_the_gate_its_class_demands(
    approval_class: ApprovalClass,
    approving_environment: ApprovalEnvironment,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        DecisionRecord.model_validate(
            decision_payload(
                approval_class=approval_class.value,
                approving_environment=approving_environment.value,
            )
        )
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="an accepted decision must have passed the gate its classification demands",
    )


@pytest.mark.parametrize("approval_class", list(ApprovalClass))
def test_an_accepted_decision_through_the_demanded_gate_validates(
    approval_class: ApprovalClass,
) -> None:
    decision = DecisionRecord.model_validate(
        decision_payload(
            approval_class=approval_class.value,
            approving_environment=ApprovalEnvironment.for_approval_class(approval_class).value,
        )
    )
    assert decision.accepted is True
    assert decision.approval_class is approval_class


def test_a_manifest_hash_mismatch_may_record_no_authorization_at_all() -> None:
    decision = DecisionRecord.model_validate(
        refused_decision_payload(AdmissionReason.MANIFEST_HASH_MISMATCH, authorization=None)
    )

    assert decision.authorization is None
    assert decision.accepted is False


@pytest.mark.parametrize(
    "reason",
    [reason for reason in REFUSING_REASONS if reason is not AdmissionReason.MANIFEST_HASH_MISMATCH],
)
def test_only_a_manifest_hash_mismatch_may_omit_the_authorization_evaluation(
    reason: AdmissionReason,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        DecisionRecord.model_validate(refused_decision_payload(reason, authorization=None))
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="only a manifest-hash mismatch may omit the authorization evaluation",
    )


@pytest.mark.parametrize("reason", REFUSING_REASONS)
def test_a_refused_decision_may_leave_the_cost_unpriceable(reason: AdmissionReason) -> None:
    decision = DecisionRecord.model_validate(refused_decision_payload(reason, cost=None))

    assert decision.cost is None, (
        "a zero here would read as a free run rather than an unpriceable one, and it is the "
        "cheapest-looking value in the field's range"
    )
    assert decision.accepted is False


@pytest.mark.parametrize("reason", REFUSING_REASONS)
def test_a_refused_decision_records_the_reason_it_was_refused_for(
    reason: AdmissionReason,
) -> None:
    decision = DecisionRecord.model_validate(refused_decision_payload(reason))

    assert decision.reason is reason
    assert decision.accepted is False
    assert reason.value in decision.detail


@pytest.mark.parametrize("policy_version", ["", "1", "v0", "v01", "V1", "v1.1", "latest"])
def test_decision_rejects_a_policy_version_that_is_not_orderable(policy_version: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        DecisionRecord.model_validate(decision_payload(policy_version=policy_version))
    assert exc_info.value.errors()[0]["loc"] == ("policy_version",)


def test_decision_rejects_an_empty_detail() -> None:
    with pytest.raises(ValidationError) as exc_info:
        DecisionRecord.model_validate(decision_payload(detail=""))
    assert_validation_error(exc_info.value, error_type="string_too_short")
    assert exc_info.value.errors()[0]["loc"] == ("detail",)


@pytest.mark.parametrize("field", REQUIRED_DECISION_FIELDS)
def test_decision_rejects_a_payload_that_omits_a_required_field(field: str) -> None:
    payload = decision_payload()
    del payload[field]
    with pytest.raises(ValidationError) as exc_info:
        DecisionRecord.model_validate(payload)
    assert any(
        item["type"] == "missing" and item["loc"] == (field,)
        for item in exc_info.value.errors()
    ), f"expected a missing-field error naming {field!r}, got {exc_info.value.errors()}"


def test_decision_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError) as exc_info:
        DecisionRecord.model_validate(decision_payload(approved_by="philote-dev"))
    assert_validation_error(exc_info.value, error_type="extra_forbidden")


def test_decision_unknown_schema_version_fails_closed() -> None:
    with pytest.raises(ValidationError) as exc_info:
        DecisionRecord.model_validate(decision_payload(schema_version=2))
    assert_validation_error(exc_info.value, error_type="literal_error")


def test_decision_round_trips_through_canonical_json() -> None:
    decision = DecisionRecord.model_validate(decision_payload())
    document = json.loads(canonical_json_bytes(decision))
    cost = document["cost"]
    assert isinstance(cost, dict)
    total = cost.pop("maximum_compute_cost_usd")
    restored = DecisionRecord.model_validate(document)

    assert restored == decision
    assert total == "2.86", (
        "the record carries the factors as well as the total, because a total alone cannot "
        "tell an underestimate from a policy change"
    )
