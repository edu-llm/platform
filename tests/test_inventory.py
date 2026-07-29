from pathlib import Path

import pytest
from pydantic import ValidationError

from edullm_platform.config import load_yaml
from edullm_platform.contracts.inventory import OrganizationInventory, normalize_github_login


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


def team_bindings_payload() -> dict[str, object]:
    return {
        "teams": [
            {
                "team_id": "memory-split",
                "github_team_slug": "memory-split",
                "lead_logins": ["ericrcwu001"],
                "member_logins": ["alsy7009"],
                "s3_namespace": "sbsandbox-intern-memory-split",
                "wandb_entity": "edu-llm-memory-split",
                "allowed_compute_profiles": ["gpu-4xa10g"],
            },
            {
                "team_id": "curriculum",
                "github_team_slug": "curriculum",
                "lead_logins": ["meric233"],
                "s3_namespace": "sbsandbox-intern-curriculum",
                "wandb_entity": "edu-llm-curriculum",
                "allowed_compute_profiles": ["cpu-32vcpu"],
            },
        ],
        "repositories": [
            {
                "repository": "OLMo-core",
                "permitted_team_ids": ["memory-split", "curriculum"],
            }
        ],
    }


def test_inventory_has_real_admins_leads_and_two_pilots() -> None:
    inventory = OrganizationInventory.model_validate(inventory_payload())
    assert inventory.admins == ("philote-dev", "BritishAmericqn")
    assert len(inventory.team_leads) == 8
    assert inventory.pilot_repositories == ("OLMo-core", "dolma")


def test_inventory_accepts_a_ninth_team_lead() -> None:
    payload = inventory_payload()
    payload["team_leads"] = [*list(payload["team_leads"]), "katiehehe"]  # type: ignore[arg-type]
    payload["members"] = [*list(payload["members"]), {"github_login": "katiehehe"}]  # type: ignore[arg-type]
    inventory = OrganizationInventory.model_validate(payload)
    assert len(inventory.team_leads) == 9
    assert inventory.is_team_lead("katiehehe") is True


def test_inventory_accepts_removing_a_team_lead() -> None:
    payload = inventory_payload()
    payload["team_leads"] = list(payload["team_leads"])[:-1]  # type: ignore[arg-type]
    inventory = OrganizationInventory.model_validate(payload)
    assert len(inventory.team_leads) == 7
    assert inventory.is_team_lead("pianomaster99") is False


def test_inventory_accepts_a_single_admin() -> None:
    payload = inventory_payload()
    payload["admins"] = ["philote-dev"]
    inventory = OrganizationInventory.model_validate(payload)
    assert inventory.admins == ("philote-dev",)
    assert inventory.is_admin("BritishAmericqn") is False


def test_inventory_accepts_a_single_pilot_repository() -> None:
    payload = inventory_payload()
    payload["pilot_repositories"] = ["OLMo-core"]
    assert OrganizationInventory.model_validate(payload).pilot_repositories == ("OLMo-core",)


@pytest.mark.parametrize("field", ["admins", "team_leads", "pilot_repositories"])
def test_inventory_rejects_empty_required_collections(field: str) -> None:
    payload = inventory_payload()
    payload[field] = []
    with pytest.raises(ValidationError) as exc_info:
        OrganizationInventory.model_validate(payload)
    assert any(item["type"] == "too_short" for item in exc_info.value.errors())


@pytest.mark.parametrize(
    ("mutation", "expected_message"),
    [
        ("duplicate_admin", "platform admin logins must be unique"),
        ("duplicate_lead", "team lead logins must be unique"),
        ("duplicate_member", "member GitHub logins must be unique"),
        ("duplicate_pilot", "pilot repository names must be unique"),
        ("unknown_admin", "every admin and team lead must be an organization member"),
        ("unknown_lead", "every admin and team lead must be an organization member"),
    ],
)
def test_inventory_rejects_invalid_role_structure(mutation: str, expected_message: str) -> None:
    payload = inventory_payload()
    if mutation == "duplicate_admin":
        payload["admins"] = ["philote-dev", "philote-dev"]
    elif mutation == "duplicate_lead":
        payload["team_leads"] = [*list(payload["team_leads"])[:-1], "philote-dev"]  # type: ignore[arg-type]
    elif mutation == "duplicate_member":
        members = list(payload["members"])  # type: ignore[arg-type]
        members.append(members[0])
        payload["members"] = members
    elif mutation == "duplicate_pilot":
        payload["pilot_repositories"] = ["OLMo-core", "OLMo-core"]
    elif mutation == "unknown_admin":
        payload["admins"] = ["philote-dev", "not-a-member"]
    else:
        payload["team_leads"] = [*list(payload["team_leads"])[:-1], "not-a-member"]  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as exc_info:
        OrganizationInventory.model_validate(payload)
    assert any(
        expected_message in item["msg"]
        for item in exc_info.value.errors()
        if item["type"] == "value_error"
    ), f"expected {expected_message!r}, got {exc_info.value.errors()}"


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


@pytest.mark.parametrize("field", ["admins", "team_leads"])
def test_inventory_treats_case_variants_of_one_login_as_duplicates(field: str) -> None:
    payload = inventory_payload()
    values = list(payload[field])  # type: ignore[arg-type]
    values[1] = values[0].upper()
    payload[field] = values
    with pytest.raises(ValidationError):
        OrganizationInventory.model_validate(payload)


def test_inventory_treats_case_variants_of_one_member_as_duplicates() -> None:
    payload = inventory_payload()
    members = list(payload["members"])  # type: ignore[arg-type]
    members.append({"github_login": "Philote-Dev"})
    payload["members"] = members
    with pytest.raises(ValidationError) as exc_info:
        OrganizationInventory.model_validate(payload)
    assert any(
        "member GitHub logins must be unique" in item["msg"] for item in exc_info.value.errors()
    )


def test_inventory_matches_roles_to_members_case_insensitively() -> None:
    payload = inventory_payload()
    payload["admins"] = ["PHILOTE-DEV", "BritishAmericqn"]
    inventory = OrganizationInventory.model_validate(payload)
    assert inventory.is_admin("philote-dev") is True
    assert inventory.is_admin("Philote-Dev") is True
    assert inventory.is_team_lead("ERICRCWU001") is True
    assert inventory.is_admin("ericrcwu001") is False


def test_inventory_preserves_authored_login_casing_through_serialization() -> None:
    payload = inventory_payload()
    payload["admins"] = ["Philote-Dev", "BritishAmericqn"]
    inventory = OrganizationInventory.model_validate(payload)
    dumped = inventory.model_dump()
    assert dumped["admins"] == ("Philote-Dev", "BritishAmericqn")
    assert dumped["members"][0]["github_login"] == "philote-dev"
    assert '"Philote-Dev"' in inventory.model_dump_json()


def test_inventory_defaults_to_empty_team_bindings() -> None:
    inventory = OrganizationInventory.model_validate(inventory_payload())
    assert inventory.team_bindings.teams == ()
    assert inventory.team_bindings.repositories == ()


def test_inventory_lookups_return_empty_without_team_bindings() -> None:
    inventory = OrganizationInventory.model_validate(inventory_payload())
    assert inventory.teams_led_by("philote-dev") == ()
    assert inventory.teams_for_member("philote-dev") == ()
    assert inventory.teams_led_by("not-a-member") == ()
    assert inventory.teams_for_member("not-a-member") == ()


def test_inventory_answers_lead_and_membership_lookups_from_team_bindings() -> None:
    payload = inventory_payload()
    payload["team_bindings"] = team_bindings_payload()
    inventory = OrganizationInventory.model_validate(payload)
    assert tuple(team.team_id for team in inventory.teams_led_by("ericrcwu001")) == (
        "memory-split",
    )
    assert tuple(team.team_id for team in inventory.teams_for_member("ALSY7009")) == (
        "memory-split",
    )
    assert inventory.teams_led_by("alsy7009") == ()
    assert inventory.teams_for_member("gorpyshortlegs") == ()


def test_inventory_rejects_team_bindings_referencing_an_unknown_team() -> None:
    payload = inventory_payload()
    bindings = team_bindings_payload()
    bindings["repositories"] = [
        {"repository": "OLMo-core", "permitted_team_ids": ["learning-science"]}
    ]
    payload["team_bindings"] = bindings
    with pytest.raises(ValidationError):
        OrganizationInventory.model_validate(payload)


def test_organization_yaml_validates_against_inventory_contract() -> None:
    project_root = Path(__file__).resolve().parents[1]
    config_path = project_root / "config" / "organization.yaml"
    inventory = load_yaml(config_path, OrganizationInventory)
    member_logins = {normalize_github_login(member.github_login) for member in inventory.members}
    assert len(member_logins) == len(inventory.members)
    assert inventory.admins
    assert inventory.team_leads
    assert inventory.pilot_repositories
    assert {normalize_github_login(login) for login in inventory.admins} <= member_logins
    assert {normalize_github_login(login) for login in inventory.team_leads} <= member_logins
    assert inventory.team_bindings.teams == ()
    assert inventory.teams_led_by(inventory.team_leads[0]) == ()


def test_the_shipped_roster_names_the_person_who_leads_the_memory_group() -> None:
    """A ROSTER THAT HAS NOT KEPT UP IS WRONG IN TWO DIRECTIONS AT ONCE. Mutation: revert
    the roster and leave the person who stopped leading on it.

    ``is_team_lead`` reads ``team_leads`` and nothing else, and ``config/policy.yaml`` sets
    ``approval_scope`` to ``organization``, so a name on this list can release any team's
    routine submission and a name off it can release none.

    Both halves were live. ``syz2026`` no longer leads a group and could still release
    anybody's routine run, which is authority nobody granted. ``VS-code-cloud`` leads the
    Memory group and was carried only as a member, so a Memory submission routed to its own
    lead was refused at admission with ``approver_lacks_lead_or_admin_role`` -- after the
    lead had already released the gate, because GitHub's ``team-leads`` team and this file
    are two different lists.

    That second list is the one this test cannot reach. The GitHub team gating the
    ``run-approval-lead`` environment lives in organization settings, and changing it is an
    owner action; until it is changed the two disagree in the other direction.
    """
    inventory = load_yaml(
        Path(__file__).resolve().parents[1] / "config" / "organization.yaml",
        OrganizationInventory,
    )

    assert inventory.is_team_lead("VS-code-cloud")
    assert not inventory.is_team_lead("syz2026")
    # Still a member, because leaving a group and leaving the organization are different
    # facts and only the first of them has been recorded.
    assert any(member.github_login == "syz2026" for member in inventory.members)
    assert len(inventory.team_leads) == 8
