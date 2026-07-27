from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from edullm_platform.canonical import sha256_digest
from edullm_platform.config import load_yaml
from edullm_platform.contracts.authorization import AuthorizationReason
from edullm_platform.contracts.decision_matrix import AuthorizationScenario
from edullm_platform.contracts.policy import ApprovalClass, ApprovalScope
from edullm_platform.manifest_helpers import compute_manifest_maximum_cost
from edullm_platform.phase0_gate import request_facts_from_manifest
from tests.test_lifecycle import reverse_mapping_order
from tests.test_manifest import (
    PROJECT_ROOT,
    load_representative_manifest,
    load_workload_catalog,
)
from tests.test_policy import (
    load_approval_policy,
    load_dataset_registry,
    load_organization_inventory,
)

AUTHORIZATION_FIXTURES_DIR = PROJECT_ROOT / "fixtures" / "authorization"

AUTHORIZATION_FIXTURE_FILENAMES = tuple(
    sorted(path.name for path in AUTHORIZATION_FIXTURES_DIR.glob("*.yaml"))
)

REVIEWED_SCENARIO_REASONS = {
    "admin-exception.yaml": AuthorizationReason.EXCEPTION_APPROVED_BY_ADMIN,
    "lead-self-authorization.yaml": AuthorizationReason.ROUTINE_SELF_AUTHORIZED,
    "member-approval.yaml": AuthorizationReason.ROUTINE_APPROVED_BY_LEAD_OR_ADMIN,
}

SCENARIO_SOURCE_MANIFESTS = {
    "admin-exception.yaml": "gpu-exception.yaml",
    "lead-self-authorization.yaml": "multiseed-routine.yaml",
    "member-approval.yaml": "olmo-branch-routine.yaml",
}

SCENARIO_IDS = list(REVIEWED_SCENARIO_REASONS)

REQUIRED_SCENARIO_FIELDS = tuple(
    name for name, field in AuthorizationScenario.model_fields.items() if field.is_required()
)


def load_scenario(filename: str) -> AuthorizationScenario:
    return load_yaml(AUTHORIZATION_FIXTURES_DIR / filename, AuthorizationScenario)


def load_scenario_document(filename: str) -> dict[str, object]:
    document = yaml.safe_load(
        (AUTHORIZATION_FIXTURES_DIR / filename).read_text(encoding="utf-8")
    )
    assert isinstance(document, dict)
    return document


def scenario_payload(**overrides: object) -> dict[str, object]:
    payload = load_scenario_document("member-approval.yaml")
    payload.update(overrides)
    return payload


def test_every_authorization_fixture_is_a_reviewed_scenario() -> None:
    assert set(AUTHORIZATION_FIXTURE_FILENAMES) == set(REVIEWED_SCENARIO_REASONS)


@pytest.mark.parametrize("filename", AUTHORIZATION_FIXTURE_FILENAMES)
def test_authorization_fixture_validates_against_the_scenario_contract(filename: str) -> None:
    scenario = load_scenario(filename)
    assert scenario.schema_version == 1
    assert scenario.scenario == Path(filename).stem


@pytest.mark.parametrize("filename", SCENARIO_IDS, ids=SCENARIO_IDS)
def test_authorization_fixture_produces_exactly_its_expected_reason(filename: str) -> None:
    scenario = load_scenario(filename)
    decision = scenario.decide(load_approval_policy(), load_organization_inventory())
    assert decision.reason is REVIEWED_SCENARIO_REASONS[filename]
    assert decision.reason is scenario.expected.reason
    assert decision.granted is scenario.expected.granted
    assert decision.approval_class is scenario.expected.approval_class
    assert decision.approval_scope is scenario.expected.approval_scope
    assert scenario.expected.matches(decision)


@pytest.mark.parametrize("filename", AUTHORIZATION_FIXTURE_FILENAMES)
def test_authorization_fixture_records_the_actor_roles_the_shipped_roster_grants(
    filename: str,
) -> None:
    scenario = load_scenario(filename)
    inventory = load_organization_inventory()
    assert scenario.submitter.matches_roster(inventory)
    if scenario.approver is not None:
        assert scenario.approver.matches_roster(inventory)


@pytest.mark.parametrize(
    ("filename", "manifest_filename"),
    sorted(SCENARIO_SOURCE_MANIFESTS.items()),
    ids=sorted(SCENARIO_SOURCE_MANIFESTS),
)
def test_authorization_fixture_facts_come_from_a_reviewed_manifest(
    filename: str,
    manifest_filename: str,
) -> None:
    manifest = load_representative_manifest(manifest_filename)
    catalog = load_workload_catalog()
    facts = request_facts_from_manifest(
        manifest,
        inventory=load_organization_inventory(),
        catalog=catalog,
        dataset_registry=load_dataset_registry(),
        estimated_cost_usd=compute_manifest_maximum_cost(manifest, catalog),
    )
    assert load_scenario(filename).request == facts


def test_the_reviewed_scenarios_cover_three_distinct_decisions() -> None:
    reasons = {
        load_scenario(filename).expected.reason
        for filename in AUTHORIZATION_FIXTURE_FILENAMES
    }
    assert len(reasons) == len(AUTHORIZATION_FIXTURE_FILENAMES)


def test_the_member_scenario_needs_someone_else_to_approve_it() -> None:
    scenario = load_scenario("member-approval.yaml")
    assert scenario.submitter.admin is False
    assert scenario.submitter.team_lead is False
    assert scenario.approver is not None
    assert scenario.approver.team_lead is True
    assert scenario.expected.approval_class is ApprovalClass.ROUTINE
    assert scenario.expected.granted is True


def test_the_lead_scenario_records_no_second_approver() -> None:
    scenario = load_scenario("lead-self-authorization.yaml")
    assert scenario.submitter.team_lead is True
    assert scenario.submitter.admin is False
    assert scenario.approver is None
    assert scenario.expected.reason is AuthorizationReason.ROUTINE_SELF_AUTHORIZED


def test_the_exception_scenario_is_approved_by_an_admin_who_leads_no_team() -> None:
    scenario = load_scenario("admin-exception.yaml")
    assert scenario.approver is not None
    assert scenario.approver.admin is True
    assert scenario.approver.team_lead is False
    assert scenario.expected.approval_class is ApprovalClass.EXCEPTION


@pytest.mark.parametrize("filename", AUTHORIZATION_FIXTURE_FILENAMES)
def test_authorization_fixture_states_the_scope_it_was_reviewed_under(filename: str) -> None:
    assert load_scenario(filename).expected.approval_scope is ApprovalScope.ORGANIZATION


@pytest.mark.parametrize("filename", AUTHORIZATION_FIXTURE_FILENAMES)
def test_authorization_fixture_is_reviewable_without_reading_python(filename: str) -> None:
    document = load_scenario_document(filename)
    assert set(document) == {
        "schema_version",
        "scenario",
        "submitter",
        "approver",
        "request",
        "expected",
    }
    expected = document["expected"]
    assert isinstance(expected, dict)
    assert set(expected) == {"granted", "approval_class", "approval_scope", "reason"}


def test_scenario_rejects_an_expectation_that_contradicts_its_reason() -> None:
    payload = scenario_payload()
    expected = dict(load_scenario_document("member-approval.yaml")["expected"])  # type: ignore[arg-type]
    expected["granted"] = False
    payload["expected"] = expected
    with pytest.raises(ValidationError) as exc_info:
        AuthorizationScenario.model_validate(payload)
    assert any(
        "expected authorization outcome must match the expected reason" in item["msg"]
        for item in exc_info.value.errors()
    )


def test_scenario_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError):
        AuthorizationScenario.model_validate(scenario_payload(reviewed_by="philote-dev"))


def test_scenario_rejects_an_unknown_reason() -> None:
    payload = scenario_payload()
    expected = dict(load_scenario_document("member-approval.yaml")["expected"])  # type: ignore[arg-type]
    expected["reason"] = "vibes"
    payload["expected"] = expected
    with pytest.raises(ValidationError):
        AuthorizationScenario.model_validate(payload)


def test_scenario_is_frozen() -> None:
    scenario = load_scenario("member-approval.yaml")
    with pytest.raises(ValidationError):
        scenario.__setattr__("scenario", "something-else")


def test_the_scenario_payload_this_module_uses_supplies_every_required_field() -> None:
    assert set(REQUIRED_SCENARIO_FIELDS) <= set(scenario_payload())


@pytest.mark.parametrize("field", REQUIRED_SCENARIO_FIELDS)
def test_scenario_rejects_a_payload_that_omits_a_required_field(field: str) -> None:
    payload = scenario_payload()
    del payload[field]
    with pytest.raises(ValidationError) as exc_info:
        AuthorizationScenario.model_validate(payload)
    assert any(
        item["type"] == "missing" and item["loc"] == (field,)
        for item in exc_info.value.errors()
    ), f"expected a missing-field error naming {field!r}, got {exc_info.value.errors()}"


def test_scenario_unknown_schema_version_fails_closed() -> None:
    with pytest.raises(ValidationError) as exc_info:
        AuthorizationScenario.model_validate(scenario_payload(schema_version=2))
    assert any(
        item["type"] == "literal_error" and item["loc"] == ("schema_version",)
        for item in exc_info.value.errors()
    ), f"expected a literal error on schema_version, got {exc_info.value.errors()}"


@pytest.mark.parametrize("filename", AUTHORIZATION_FIXTURE_FILENAMES)
def test_source_field_order_does_not_change_a_scenario_digest(filename: str) -> None:
    document = load_scenario_document(filename)
    reordered = reverse_mapping_order(document)
    assert isinstance(reordered, dict)
    assert list(reordered) != list(document)
    nested = [key for key, value in document.items() if isinstance(value, dict) and len(value) > 1]
    assert nested, f"{filename} has no nested block, so reordering would prove little"
    for key in nested:
        assert list(reordered[key]) != list(document[key])
    assert sha256_digest(AuthorizationScenario.model_validate(reordered)) == sha256_digest(
        load_scenario(filename)
    )
