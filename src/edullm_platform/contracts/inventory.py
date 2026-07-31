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
from .results import WANDB_NAME_PATTERN

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
    #: The W&B account this person's runs should be attributed to, or ``None`` when nobody
    #: has recorded one. Absent by default and absent for most of the roster: W&B only
    #: honours an attribution when the named account belongs to the service account's parent
    #: team, and reports nothing at all when it does not -- so a login recorded on a hunch
    #: produces a run that logs as the platform and looks exactly like one that was never
    #: attributed. The pattern is W&B's own, shared with ``contracts/results.py``.
    wandb_username: str | None = Field(default=None, pattern=WANDB_NAME_PATTERN)

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
        # ATTRIBUTING ONE PERSON'S RUN TO ANOTHER IS WORSE THAN ATTRIBUTING IT TO NOBODY. An
        # unattributed run is visibly unattributed; a misattributed one is indistinguishable
        # from a correct one, and the only reader placed to catch it is the person who did
        # not run it.
        claimed = [
            member.wandb_username for member in self.members if member.wandb_username is not None
        ]
        if len(set(claimed)) != len(claimed):
            raise ValueError("a wandb username must not be claimed by two members")
        return self

    def is_admin(self, github_login: str) -> bool:
        return normalize_github_login(github_login) in normalize_github_logins(self.admins)

    def is_team_lead(self, github_login: str) -> bool:
        return normalize_github_login(github_login) in normalize_github_logins(self.team_leads)

    def wandb_username_for(self, github_login: str) -> str | None:
        """The W&B account this login's runs belong to, or ``None`` if nobody recorded one.

        ``None`` rather than an empty string, because the caller has to tell the difference.
        An unrecorded person's run must carry no ``WANDB_USERNAME`` at all: W&B reads an
        empty one as an attribution that failed rather than as an attribution not attempted.
        """
        wanted = normalize_github_login(github_login)
        for member in self.members:
            if member.normalized_github_login == wanted:
                return member.wandb_username
        return None

    def teams_led_by(self, github_login: str) -> tuple[TeamBinding, ...]:
        return self.team_bindings.teams_led_by(github_login)

    def teams_for_member(self, github_login: str) -> tuple[TeamBinding, ...]:
        return self.team_bindings.teams_for_member(github_login)
