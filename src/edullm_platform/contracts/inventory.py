from typing import Annotated, Self

from pydantic import BeforeValidator, Field, model_validator

from .base import ContractModel, require_ordered_sequence
from .bindings import (
    GitHubLogin,
    RepositoryName,
    TeamBinding,
    TeamBindingCatalog,
    normalize_github_login,
    normalize_github_logins,
)

__all__ = [
    "GitHubLogin",
    "OrganizationInventory",
    "PersonRef",
    "RepositoryName",
    "normalize_github_login",
    "normalize_github_logins",
]


class PersonRef(ContractModel):
    github_login: GitHubLogin
    display_name: str | None = Field(default=None, min_length=1)

    @property
    def normalized_github_login(self) -> str:
        return normalize_github_login(self.github_login)


class OrganizationInventory(ContractModel):
    admins: Annotated[tuple[GitHubLogin, ...], BeforeValidator(require_ordered_sequence)] = Field(
        min_length=1, strict=False
    )
    team_leads: Annotated[
        tuple[GitHubLogin, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)
    members: Annotated[tuple[PersonRef, ...], BeforeValidator(require_ordered_sequence)] = Field(
        min_length=1, strict=False
    )
    pilot_repositories: Annotated[
        tuple[RepositoryName, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)
    team_bindings: TeamBindingCatalog = TeamBindingCatalog()

    @model_validator(mode="after")
    def validate_inventory(self) -> Self:
        normalized_admins = normalize_github_logins(self.admins)
        if len(set(normalized_admins)) != len(normalized_admins):
            raise ValueError("platform admin logins must be unique")
        normalized_leads = normalize_github_logins(self.team_leads)
        if len(set(normalized_leads)) != len(normalized_leads):
            raise ValueError("team lead logins must be unique")
        normalized_members = tuple(member.normalized_github_login for member in self.members)
        if len(set(normalized_members)) != len(normalized_members):
            raise ValueError("member GitHub logins must be unique")
        unknown_roles = (set(normalized_admins) | set(normalized_leads)) - set(normalized_members)
        if unknown_roles:
            raise ValueError("every admin and team lead must be an organization member")
        if len(set(self.pilot_repositories)) != len(self.pilot_repositories):
            raise ValueError("pilot repository names must be unique")
        return self

    def is_admin(self, github_login: str) -> bool:
        return normalize_github_login(github_login) in normalize_github_logins(self.admins)

    def is_team_lead(self, github_login: str) -> bool:
        return normalize_github_login(github_login) in normalize_github_logins(self.team_leads)

    def teams_led_by(self, github_login: str) -> tuple[TeamBinding, ...]:
        return self.team_bindings.teams_led_by(github_login)

    def teams_for_member(self, github_login: str) -> tuple[TeamBinding, ...]:
        return self.team_bindings.teams_for_member(github_login)
