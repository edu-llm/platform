from pathlib import Path

import pytest
from pydantic import ValidationError

from edullm_platform.config import load_yaml
from edullm_platform.contracts.inventory import OrganizationInventory


def inventory_payload() -> dict[str, object]:
    return {
        "admins": ["philote-dev", "BritishAmericqn"],
        "team_leads": [
            "philote-dev",
            "ericrcwu001",
            "alsy7009",
            "meric233",
            "syz2026",
            "gorpyshortlegs",
            "hiyasvyas",
            "pianomaster99",
        ],
        "members": [
            {"github_login": "philote-dev", "display_name": "Example Admin"},
            {"github_login": "BritishAmericqn"},
            {"github_login": "ericrcwu001"},
            {"github_login": "alsy7009"},
            {"github_login": "meric233"},
            {"github_login": "syz2026"},
            {"github_login": "gorpyshortlegs"},
            {"github_login": "hiyasvyas"},
            {"github_login": "pianomaster99"},
        ],
        "pilot_repositories": ["OLMo-core", "dolma"],
    }


def test_inventory_has_real_admins_leads_and_two_pilots() -> None:
    inventory = OrganizationInventory.model_validate(inventory_payload())
    assert inventory.admins == ("philote-dev", "BritishAmericqn")
    assert len(inventory.team_leads) == 8
    assert inventory.pilot_repositories == ("OLMo-core", "dolma")


@pytest.mark.parametrize(
    "mutation",
    ["duplicate_admin", "seven_leads", "unknown_lead", "one_pilot", "duplicate_member"],
)
def test_inventory_rejects_invalid_role_structure(mutation: str) -> None:
    payload = inventory_payload()
    if mutation == "duplicate_admin":
        payload["admins"] = ["philote-dev", "philote-dev"]
    elif mutation == "seven_leads":
        payload["team_leads"] = list(payload["team_leads"])[:-1]
    elif mutation == "unknown_lead":
        payload["team_leads"] = [*list(payload["team_leads"])[:-1], "not-a-member"]
    elif mutation == "one_pilot":
        payload["pilot_repositories"] = ["OLMo-core"]
    else:
        members = list(payload["members"])
        members.append(members[0])
        payload["members"] = members
    with pytest.raises(ValidationError):
        OrganizationInventory.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("pilot_repositories", ""),
        ("pilot_repositories", " "),
        ("admins", ""),
        ("admins", " "),
        ("team_leads", ""),
        ("team_leads", " "),
    ],
)
def test_inventory_rejects_empty_or_whitespace_item_values(
    field: str,
    invalid_value: str,
) -> None:
    payload = inventory_payload()
    values = list(payload[field])  # type: ignore[arg-type]
    values[0] = invalid_value
    payload[field] = values
    with pytest.raises(ValidationError):
        OrganizationInventory.model_validate(payload)


@pytest.mark.parametrize("field", ["admins", "team_leads"])
def test_inventory_rejects_role_values_not_in_roster(field: str) -> None:
    payload = inventory_payload()
    values = list(payload[field])  # type: ignore[arg-type]
    values[0] = "not-a-member"
    payload[field] = values
    with pytest.raises(ValidationError):
        OrganizationInventory.model_validate(payload)


def test_organization_yaml_validates_against_inventory_contract() -> None:
    project_root = Path(__file__).resolve().parents[1]
    config_path = project_root / "config" / "organization.yaml"
    inventory = load_yaml(config_path, OrganizationInventory)
    assert inventory.admins == ("philote-dev", "BritishAmericqn")
    assert len(inventory.team_leads) == 8
    assert inventory.pilot_repositories == ("OLMo-core", "dolma")
