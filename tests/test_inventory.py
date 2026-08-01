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


def test_a_member_may_record_the_wandb_account_their_runs_belong_to() -> None:
    payload = inventory_payload()
    members = payload["members"]
    assert isinstance(members, list)
    members[0] = {
        "github_login": "philote-dev",
        "display_name": "Example Admin",
        "wandb_username": "philote",
    }
    inventory = OrganizationInventory.model_validate(payload)
    assert inventory.wandb_username_for("philote-dev") == "philote"


def test_the_wandb_account_is_found_however_the_login_was_cased() -> None:
    # The submitter reaches admission as GitHub spelled it in the event, and this file
    # spells logins as their owners authored them. Every other lookup on this model already
    # normalises, and an attribution that worked or not depending on casing would be the
    # worst of both: right most of the time.
    payload = inventory_payload()
    members = payload["members"]
    assert isinstance(members, list)
    members[0] = {"github_login": "philote-dev", "wandb_username": "philote"}
    inventory = OrganizationInventory.model_validate(payload)
    assert inventory.wandb_username_for("PHILOTE-DEV") == "philote"


def test_a_member_with_no_recorded_wandb_account_answers_nothing() -> None:
    # Nothing rather than an empty string, because the caller has to tell the difference:
    # a run for somebody with no W&B account must be submitted with no attribution at all,
    # and W&B reads an empty WANDB_USERNAME as an attribution attempt that fails.
    inventory = OrganizationInventory.model_validate(inventory_payload())
    assert inventory.wandb_username_for("pianomaster99") is None


def test_a_login_nobody_recognises_answers_nothing_rather_than_raising() -> None:
    inventory = OrganizationInventory.model_validate(inventory_payload())
    assert inventory.wandb_username_for("somebody-who-left") is None


def test_two_people_may_not_claim_the_same_wandb_account() -> None:
    """ATTRIBUTING ONE PERSON'S RUN TO ANOTHER IS WORSE THAN ATTRIBUTING IT TO NOBODY.

    An unattributed run is visibly unattributed. A run attributed to the wrong person looks
    exactly like a correct one, and the only reader who could catch it is the person who did
    not run it.
    """
    payload = inventory_payload()
    members = payload["members"]
    assert isinstance(members, list)
    members[0] = {"github_login": "philote-dev", "wandb_username": "philote"}
    members[1] = {"github_login": "BritishAmericqn", "wandb_username": "philote"}
    with pytest.raises(ValidationError) as exc_info:
        OrganizationInventory.model_validate(payload)
    assert "wandb" in str(exc_info.value).casefold()


def test_the_shipped_roster_attributes_the_people_who_have_run_something() -> None:
    """The pilot's three named people, and the two runs that made this necessary.

    `run_019fb4f6` was submitted by `aryanjverma`, released by `pianomaster99`, and logged
    nothing to W&B; the run after it logged as the platform's own account, because a
    personal API key was in the container and nothing told W&B who had asked for the work.
    All three are recorded now. Aryan was the one this file called unattributable, and the
    claim had stopped being true rather than never having been: the `eduLLM` entity holds
    `aryan-jaden-verma` under the display name `Aryan Verma`, an exact match, so the gap was
    in the roster and not in the W&B team.
    """
    project_root = Path(__file__).resolve().parents[1]
    inventory = load_yaml(project_root / "config" / "organization.yaml", OrganizationInventory)
    assert inventory.wandb_username_for("philote-dev") == "philote"
    assert inventory.wandb_username_for("pianomaster99") == "liumaizi"
    assert inventory.wandb_username_for("aryanjverma") == "aryan-jaden-verma"


def test_the_roster_records_who_cannot_be_attributed_yet() -> None:
    """W&B ATTRIBUTION IS SILENT WHEN THE PERSON IS NOT ON THE TEAM, WHICH IS WHY THIS EXISTS.

    `WANDB_USERNAME` only names a run's author when that account belongs to the service
    account's parent team, and W&B reports nothing when it does not -- the run simply logs
    as the service account, which is indistinguishable from having sent no attribution.

    So a login is recorded here only when its owner was read out of the `eduLLM` team's own
    member list. Six people are not in that list under any spelling, and a blank is the only
    true answer for them: a plausible guess produces exactly the silent failure this contract
    exists to avoid, and it is harder to notice than the blank because the run looks
    attributed.

    Named rather than counted, so that recording one is an edit here as well. Adding somebody
    to the W&B team is an owner action in W&B and nothing in this repository can do it.
    """
    project_root = Path(__file__).resolve().parents[1]
    inventory = load_yaml(project_root / "config" / "organization.yaml", OrganizationInventory)
    unattributable = {
        member.github_login
        for member in inventory.members
        if member.wandb_username is None
    }

    assert unattributable == {
        "BritishAmericqn",
        "arteexu",
        "caiiris",
        "yuen-kai",
        "Adarsh-Rajesh-gitHub",
        "NotAnAlgorithm",
    }


def test_every_recorded_wandb_account_belongs_to_somebody_on_the_roster() -> None:
    project_root = Path(__file__).resolve().parents[1]
    inventory = load_yaml(project_root / "config" / "organization.yaml", OrganizationInventory)
    attributed = [member for member in inventory.members if member.wandb_username is not None]
    assert len(attributed) > 20, "the roster stopped carrying the mapping it was built for"
    assert len({member.wandb_username for member in attributed}) == len(attributed)


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
    assert inventory.team_bindings.teams != ()
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
