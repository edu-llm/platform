from typing import Annotated, Self

from pydantic import BeforeValidator, Field, model_validator

from .base import ContractModel, require_ordered_sequence

GitHubLogin = Annotated[str, Field(min_length=1, pattern=r"^[A-Za-z0-9-]+$")]
RepositoryName = Annotated[str, Field(min_length=1, pattern=r"^\S+$")]


class PersonRef(ContractModel):
    github_login: GitHubLogin
    display_name: str | None = Field(default=None, min_length=1)


class OrganizationInventory(ContractModel):
    admins: Annotated[tuple[GitHubLogin, ...], BeforeValidator(require_ordered_sequence)] = Field(
        strict=False
    )
    team_leads: Annotated[
        tuple[GitHubLogin, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(strict=False)
    members: Annotated[tuple[PersonRef, ...], BeforeValidator(require_ordered_sequence)] = Field(
        min_length=1, strict=False
    )
    pilot_repositories: Annotated[
        tuple[RepositoryName, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(strict=False)

    @model_validator(mode="after")
    def validate_inventory(self) -> Self:
        if len(self.admins) != 2 or len(set(self.admins)) != 2:
            raise ValueError("exactly two distinct platform admins are required")
        if len(self.team_leads) != 8 or len(set(self.team_leads)) != 8:
            raise ValueError("exactly eight distinct team leads are required")
        member_logins = [member.github_login for member in self.members]
        if len(member_logins) != len(set(member_logins)):
            raise ValueError("member GitHub logins must be unique")
        unknown_roles = (set(self.admins) | set(self.team_leads)) - set(member_logins)
        if unknown_roles:
            raise ValueError("every admin and team lead must be an organization member")
        if (
            len(self.pilot_repositories) != 2
            or len(set(self.pilot_repositories)) != 2
        ):
            raise ValueError("exactly two distinct pilot repositories are required")
        return self
