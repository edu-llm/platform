import pytest
from pydantic import ValidationError

from edullm_platform.contracts.bindings import (
    SANDBOX_BUCKET_PREFIX,
    RepositoryBinding,
    TeamBinding,
    TeamBindingCatalog,
    normalize_github_login,
    normalize_github_logins,
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


def memory_split_payload() -> dict[str, object]:
    return {
        "team_id": "memory-split",
        "github_team_slug": "memory-split",
        "lead_logins": ["ericrcwu001"],
        "member_logins": ["caiiris"],
        "s3_namespace": "sbsandbox-intern-memory-split",
        "wandb_entity": "edu-llm-memory-split",
        "allowed_compute_profiles": ["gpu-4xa10g"],
        "attribution_tags": [{"key": "team", "value": "memory-split"}],
    }


def curriculum_payload() -> dict[str, object]:
    return {
        "team_id": "curriculum",
        "github_team_slug": "curriculum",
        "lead_logins": ["alsy7009", "meric233"],
        "s3_namespace": "sbsandbox-intern-curriculum",
        "wandb_entity": "edu-llm-curriculum",
        "allowed_compute_profiles": ["cpu-32vcpu", "gpu-4xa10g"],
    }


def catalog_payload() -> dict[str, object]:
    return {
        "teams": [memory_split_payload(), curriculum_payload()],
        "repositories": [
            {
                "repository": "OLMo-core",
                "permitted_team_ids": ["memory-split", "curriculum"],
            },
            {"repository": "dolma", "permitted_team_ids": ["curriculum"]},
        ],
    }


def test_normalize_github_login_casefolds_authored_spelling() -> None:
    assert normalize_github_login("Adarsh-Rajesh-Github") == "adarsh-rajesh-github"
    assert normalize_github_login("Adarsh-Rajesh-gitHub") == "adarsh-rajesh-github"
    assert normalize_github_logins(("Ericrcwu001", "ALSY7009")) == ("ericrcwu001", "alsy7009")


def test_empty_team_bindings_are_valid() -> None:
    catalog = TeamBindingCatalog()
    assert catalog.teams == ()
    assert catalog.repositories == ()
    assert TeamBindingCatalog.model_validate({}) == catalog


def test_catalog_accepts_team_and_repository_bindings() -> None:
    catalog = TeamBindingCatalog.model_validate(catalog_payload())
    assert tuple(team.team_id for team in catalog.teams) == ("memory-split", "curriculum")
    assert catalog.repositories[0].permitted_team_ids == ("memory-split", "curriculum")


def test_repository_binding_permits_many_teams() -> None:
    binding = RepositoryBinding.model_validate(
        {
            "repository": "OLMo-core",
            "permitted_team_ids": ["memory-split", "curriculum", "learning-science"],
        }
    )
    assert len(binding.permitted_team_ids) == 3
    assert binding.permits("learning-science") is True
    assert binding.permits("unbound-team") is False


def test_repository_binding_carries_no_owning_team() -> None:
    assert set(RepositoryBinding.model_fields) == {"repository", "permitted_team_ids"}
    with pytest.raises(ValidationError) as exc_info:
        RepositoryBinding.model_validate(
            {
                "repository": "OLMo-core",
                "permitted_team_ids": ["memory-split"],
                "team": "memory-split",
            }
        )
    assert_validation_error(exc_info.value, error_type="extra_forbidden")


def test_catalog_maps_a_repository_to_every_permitted_team() -> None:
    catalog = TeamBindingCatalog.model_validate(catalog_payload())
    permitted = catalog.teams_permitted_for_repository("OLMo-core")
    assert tuple(team.team_id for team in permitted) == ("memory-split", "curriculum")
    assert catalog.teams_permitted_for_repository("unbound-repository") == ()


def test_catalog_rejects_repository_binding_with_unknown_team_id() -> None:
    payload = catalog_payload()
    repositories = list(payload["repositories"])  # type: ignore[arg-type]
    repositories[0] = {
        **repositories[0],
        "permitted_team_ids": ["memory-split", "learning-science"],
    }
    payload["repositories"] = repositories
    with pytest.raises(ValidationError) as exc_info:
        TeamBindingCatalog.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="permits unknown team ids",
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_team_id",
        "duplicate_team_slug",
        "duplicate_repository",
        "duplicate_lead",
        "case_insensitive_duplicate_lead",
    ],
)
def test_catalog_rejects_invalid_binding_rules(mutation: str) -> None:
    payload = catalog_payload()
    teams = list(payload["teams"])  # type: ignore[arg-type]
    repositories = list(payload["repositories"])  # type: ignore[arg-type]
    if mutation == "duplicate_team_id":
        teams[1] = {**teams[1], "team_id": "memory-split"}
        expected_message = "team ids must be unique"
    elif mutation == "duplicate_team_slug":
        teams[1] = {**teams[1], "github_team_slug": "memory-split"}
        expected_message = "github team slugs must be unique"
    elif mutation == "duplicate_repository":
        repositories[1] = {**repositories[1], "repository": "OLMo-core"}
        expected_message = "repository binding names must be unique"
    elif mutation == "duplicate_lead":
        teams[1] = {**teams[1], "lead_logins": ["alsy7009", "alsy7009"]}
        expected_message = "team lead logins must be unique within a team"
    else:
        teams[1] = {**teams[1], "lead_logins": ["alsy7009", "ALSY7009"]}
        expected_message = "team lead logins must be unique within a team"
    payload["teams"] = teams
    payload["repositories"] = repositories
    with pytest.raises(ValidationError) as exc_info:
        TeamBindingCatalog.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment=expected_message,
    )


@pytest.mark.parametrize(
    "s3_namespace",
    [
        "sbsandbox-intern-memory-split",
        "sbsandbox-intern-shared/memory-split",
        "sbsandbox-intern-shared/memory-split/checkpoints",
        "sbsandbox-intern-a1",
    ],
)
def test_team_binding_accepts_sandbox_s3_namespaces(s3_namespace: str) -> None:
    payload = {**memory_split_payload(), "s3_namespace": s3_namespace}
    assert TeamBinding.model_validate(payload).s3_namespace == s3_namespace


@pytest.mark.parametrize(
    "s3_namespace",
    [
        "",
        "sbsandbox-intern-",
        "memory-split",
        "sbsandbox-memory-split",
        "sandbox-intern-memory-split",
        "SBSANDBOX-INTERN-memory-split",
        "s3://sbsandbox-intern-memory-split",
        " sbsandbox-intern-memory-split",
    ],
)
def test_team_binding_rejects_s3_namespaces_outside_the_sandbox(s3_namespace: str) -> None:
    payload = {**memory_split_payload(), "s3_namespace": s3_namespace}
    with pytest.raises(ValidationError) as exc_info:
        TeamBinding.model_validate(payload)
    assert_validation_error(exc_info.value, error_type="string_pattern_mismatch")
    assert SANDBOX_BUCKET_PREFIX == "sbsandbox-intern-"


def test_a_team_may_record_no_lead_because_nobody_has_recorded_one() -> None:
    """THIS TEST USED TO ASSERT THE OPPOSITE. Mutation: require a lead again.

    Requiring one login here read as a safety rule and worked as a forcing function: a group
    could not be declared at all until somebody put a name against it, so the four groups
    whose lead is written down nowhere could only be declared by inventing one. An invented
    lead is worse than none, because ``_routing_note`` prints it to the reviewer as the
    person this run would normally go to.

    Nothing was protecting anything. ``config/policy.yaml`` sets ``approval_scope`` to
    organization, so a lead recorded here carries no authorization weight at all, and under
    team scope a group with no lead routes to an admin, who may always release. The branch
    for a group with no recorded lead already existed in ``submission._routing_note`` and
    called itself the ordinary path; this constraint was what made it unreachable.
    """
    binding = TeamBinding.model_validate({**memory_split_payload(), "lead_logins": []})

    assert binding.lead_logins == ()
    assert binding.is_led_by("ericrcwu001") is False
    assert binding.includes("ericrcwu001") is False


def test_repository_binding_requires_at_least_one_permitted_team() -> None:
    with pytest.raises(ValidationError) as exc_info:
        RepositoryBinding.model_validate({"repository": "OLMo-core", "permitted_team_ids": []})
    assert_validation_error(exc_info.value, error_type="too_short")


def test_team_binding_defaults_optional_collections_to_empty() -> None:
    payload = {
        "team_id": "learning-science",
        "github_team_slug": "learning-science",
        "lead_logins": ["hiyasvyas"],
        "s3_namespace": "sbsandbox-intern-learning-science",
        "wandb_entity": "edu-llm-learning-science",
    }
    binding = TeamBinding.model_validate(payload)
    assert binding.member_logins == ()
    assert binding.allowed_compute_profiles == ()
    assert binding.attribution_tags == ()


def test_team_binding_rejects_duplicate_attribution_tag_keys() -> None:
    payload = {
        **memory_split_payload(),
        "attribution_tags": [
            {"key": "team", "value": "memory-split"},
            {"key": "team", "value": "curriculum"},
        ],
    }
    with pytest.raises(ValidationError) as exc_info:
        TeamBinding.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="attribution tag keys must be unique within a team",
    )


def test_team_binding_preserves_attribution_tag_order() -> None:
    payload = {
        **memory_split_payload(),
        "attribution_tags": [
            {"key": "team", "value": "memory-split"},
            {"key": "cost-center", "value": "research"},
        ],
    }
    binding = TeamBinding.model_validate(payload)
    assert tuple(tag.key for tag in binding.attribution_tags) == ("team", "cost-center")


def test_lookups_return_empty_for_a_login_without_a_team() -> None:
    empty = TeamBindingCatalog()
    assert empty.teams_led_by("philote-dev") == ()
    assert empty.teams_for_member("philote-dev") == ()
    populated = TeamBindingCatalog.model_validate(catalog_payload())
    assert populated.teams_led_by("blackbird-alt") == ()
    assert populated.teams_for_member("blackbird-alt") == ()


def test_lead_and_membership_lookups_are_case_insensitive() -> None:
    catalog = TeamBindingCatalog.model_validate(catalog_payload())
    assert tuple(team.team_id for team in catalog.teams_led_by("ERICRCWU001")) == ("memory-split",)
    assert tuple(team.team_id for team in catalog.teams_for_member("CaIiRiS")) == ("memory-split",)


def test_team_membership_includes_leads() -> None:
    catalog = TeamBindingCatalog.model_validate(catalog_payload())
    assert tuple(team.team_id for team in catalog.teams_for_member("alsy7009")) == ("curriculum",)


def test_a_login_may_lead_several_teams() -> None:
    payload = catalog_payload()
    teams = list(payload["teams"])  # type: ignore[arg-type]
    teams[1] = {**teams[1], "lead_logins": ["ericrcwu001", "meric233"]}
    payload["teams"] = teams
    catalog = TeamBindingCatalog.model_validate(payload)
    assert tuple(team.team_id for team in catalog.teams_led_by("ericrcwu001")) == (
        "memory-split",
        "curriculum",
    )


def test_team_binding_is_frozen_and_forbids_unknown_fields() -> None:
    binding = TeamBinding.model_validate(memory_split_payload())
    with pytest.raises(ValidationError):
        TeamBinding.model_validate({**memory_split_payload(), "owner": "philote-dev"})
    with pytest.raises(ValidationError):
        binding.__setattr__("team_id", "curriculum")
