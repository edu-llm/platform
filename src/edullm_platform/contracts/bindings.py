from typing import Annotated, Self

from pydantic import BeforeValidator, Field, model_validator

from .base import SANDBOX_BUCKET_PREFIX, ContractModel, require_ordered_sequence

S3_NAMESPACE_PATTERN = (
    rf"^{SANDBOX_BUCKET_PREFIX}[a-z0-9][a-z0-9-]*(?:/[a-z0-9][a-z0-9._-]*)*$"
)
SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"

GitHubLogin = Annotated[str, Field(min_length=1, pattern=r"^[A-Za-z0-9-]+$")]
RepositoryName = Annotated[str, Field(min_length=1, pattern=r"^\S+$")]
TeamId = Annotated[str, Field(min_length=1, pattern=SLUG_PATTERN)]
GitHubTeamSlug = Annotated[str, Field(min_length=1, pattern=SLUG_PATTERN)]
S3Namespace = Annotated[str, Field(pattern=S3_NAMESPACE_PATTERN)]
WandbEntity = Annotated[str, Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
ComputeProfileName = Annotated[str, Field(min_length=1)]


def normalize_github_login(github_login: str) -> str:
    return github_login.casefold()


def normalize_github_logins(github_logins: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(normalize_github_login(login) for login in github_logins)


class AttributionTag(ContractModel):
    key: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=256)


class TeamBinding(ContractModel):
    team_id: TeamId
    github_team_slug: GitHubTeamSlug
    #: Who leads this group, where anybody has recorded it. Empty is permitted and means
    #: exactly that nobody has, which is a different state from a group that has no lead.
    #: This field required at least one login until 2026-08-01, and the constraint was
    #: unreachable in one direction and wrong in the other: ``submission._routing_note``
    #: already carries a branch for a group with no recorded lead and calls it the ordinary
    #: path, and requiring a name here is what forced that name to be invented before a
    #: group could be declared at all. Under ``approval_scope: organization`` a lead carries
    #: no authorization weight, so an empty list withholds nothing; under team scope the
    #: group routes to an admin, who may always release.
    lead_logins: Annotated[
        tuple[GitHubLogin, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(default=(), strict=False)
    s3_namespace: S3Namespace
    wandb_entity: WandbEntity
    allowed_compute_profiles: Annotated[
        tuple[ComputeProfileName, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(default=(), strict=False)
    member_logins: Annotated[
        tuple[GitHubLogin, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(default=(), strict=False)
    attribution_tags: Annotated[
        tuple[AttributionTag, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(default=(), strict=False)

    @model_validator(mode="after")
    def validate_team_binding(self) -> Self:
        normalized_leads = normalize_github_logins(self.lead_logins)
        if len(set(normalized_leads)) != len(normalized_leads):
            raise ValueError("team lead logins must be unique within a team")
        normalized_members = normalize_github_logins(self.member_logins)
        if len(set(normalized_members)) != len(normalized_members):
            raise ValueError("team member logins must be unique within a team")
        if len(set(self.allowed_compute_profiles)) != len(self.allowed_compute_profiles):
            raise ValueError("allowed compute profile names must be unique within a team")
        tag_keys = [tag.key for tag in self.attribution_tags]
        if len(set(tag_keys)) != len(tag_keys):
            raise ValueError("attribution tag keys must be unique within a team")
        return self

    def is_led_by(self, github_login: str) -> bool:
        return normalize_github_login(github_login) in normalize_github_logins(self.lead_logins)

    def includes(self, github_login: str) -> bool:
        roster = normalize_github_logins(self.lead_logins + self.member_logins)
        return normalize_github_login(github_login) in roster


class RepositoryBinding(ContractModel):
    repository: RepositoryName
    permitted_team_ids: Annotated[
        tuple[TeamId, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)

    @model_validator(mode="after")
    def validate_repository_binding(self) -> Self:
        if len(set(self.permitted_team_ids)) != len(self.permitted_team_ids):
            raise ValueError("permitted team ids must be unique within a repository binding")
        return self

    def permits(self, team_id: str) -> bool:
        return team_id in self.permitted_team_ids


class TeamBindingCatalog(ContractModel):
    teams: Annotated[tuple[TeamBinding, ...], BeforeValidator(require_ordered_sequence)] = Field(
        default=(), strict=False
    )
    repositories: Annotated[
        tuple[RepositoryBinding, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(default=(), strict=False)

    @model_validator(mode="after")
    def validate_binding_catalog(self) -> Self:
        team_ids = [team.team_id for team in self.teams]
        if len(set(team_ids)) != len(team_ids):
            raise ValueError("team ids must be unique")
        team_slugs = [team.github_team_slug for team in self.teams]
        if len(set(team_slugs)) != len(team_slugs):
            raise ValueError("github team slugs must be unique")
        repository_names = [binding.repository for binding in self.repositories]
        if len(set(repository_names)) != len(repository_names):
            raise ValueError("repository binding names must be unique")
        known_team_ids = set(team_ids)
        for binding in self.repositories:
            unknown = [
                team_id for team_id in binding.permitted_team_ids if team_id not in known_team_ids
            ]
            if unknown:
                raise ValueError(
                    f"repository binding {binding.repository!r} permits unknown team ids: "
                    f"{unknown!r}"
                )
        return self

    def teams_led_by(self, github_login: str) -> tuple[TeamBinding, ...]:
        return tuple(team for team in self.teams if team.is_led_by(github_login))

    def teams_for_member(self, github_login: str) -> tuple[TeamBinding, ...]:
        return tuple(team for team in self.teams if team.includes(github_login))

    def teams_permitted_for_repository(self, repository: str) -> tuple[TeamBinding, ...]:
        binding = next(
            (item for item in self.repositories if item.repository == repository),
            None,
        )
        if binding is None:
            return ()
        return tuple(team for team in self.teams if binding.permits(team.team_id))
