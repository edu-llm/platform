"""What Phase 2 captures from GitHub, and what a captured record is allowed to say.

Three of Phase 2's criteria are about GitHub's own configuration rather than about code:
the reviewer lists on the two approval environments, the branch policy those environments
enforce, and the absence of repository-level secrets. Nothing in this repository could read
any of them, so all three were gaps with no citation at all -- the configuration existed,
was set deliberately, and was believed rather than checked.

These models are what a capture of that configuration must be, and each carries the reason
its shape is stricter than the API's.

**Secret names, never values, and the model cannot hold a value.** :class:`SecretInventory`
records names only. There is no field for a value to go in, so a capture tool cannot
accidentally write one, and the ``SecretFreeStr`` scan refuses anything that looks like a
credential even in a name. The criterion this serves is that no repository-level secret
exists at all, which is a statement about names, and a capture that carried values to prove
it would be the worst possible way to keep that true.

**The branch policy is recorded in its two-flag form rather than as a summary.** GitHub
offers two ways to restrict deployments and they are not equivalent: ``protected_branches``
follows whatever branch protection happens to cover, so it silently widens the moment a
second branch is protected, while ``custom_branch_policies`` matches names that were
written down. Recording "restricted to main" would lose exactly the distinction the
criterion exists to check, so both flags and the named branches are kept separately.

**A reviewer is a type and a name, and the type matters.** ``run-approval-lead`` lists one
reviewer, the ``team-leads`` team, because eight leads exceed the six-slot cap and a team
counts as one slot. A capture that flattened teams into their members would report eight
reviewers and agree with the roster for the wrong reason -- and would go on agreeing after
somebody replaced the team with six named users, which is a different control.

Everything here is a :class:`~edullm_platform.evidence.FreshEvidenceModel`, so a record
older than the freshness window refuses to load rather than reading as current. A GitHub
setting can be changed in a browser in ten seconds, which is precisely why a statement about
one expires.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from edullm_platform.contracts.base import require_ordered_sequence
from edullm_platform.evidence import (
    EvidenceEnvironment,
    FreshEvidenceModel,
    SecretFreeStr,
)

__all__ = [
    "APPROVAL_ENVIRONMENT_NAMES",
    "EnvironmentInventory",
    "EnvironmentReviewer",
    "ProtectedEnvironment",
    "SecretInventory",
]

#: The two names the admission role's trust policy enumerates. A capture naming anything
#: else is capturing the wrong environments, and a capture missing one of these is
#: capturing an incomplete gate.
APPROVAL_ENVIRONMENT_NAMES: tuple[str, ...] = ("run-approval-lead", "run-approval-admin")

OrderedStrings = Annotated[tuple[str, ...], BeforeValidator(require_ordered_sequence)]


def _ordered_by_environment(value: object) -> object:
    """Apply the ordered-sequence rule to each value of a mapping.

    ``strict`` does not reach inside a dict's value type, so a JSON array loaded back for
    ``environment_secret_names`` arrives as a list and is refused where the same array at
    the top level would have been converted. Doing it here keeps the rule -- names are an
    ordered sequence, never a set -- rather than relaxing the field to accept anything.
    """
    if not isinstance(value, dict):
        return value
    ordered: dict[object, object] = {}
    for key, item in value.items():
        checked = require_ordered_sequence(item)
        ordered[key] = tuple(checked) if isinstance(checked, (list, tuple)) else checked
    return ordered


class EnvironmentReviewer(FreshEvidenceModel):
    """One reviewer on one environment, as GitHub reports it.

    ``kind`` is kept because a team and a user are different controls wearing the same
    slot. Flattening a team into its members would make a reviewer list agree with the
    roster after somebody had replaced the team with a fixed set of names.
    """

    kind: Literal["User", "Team"]
    name: SecretFreeStr = Field(min_length=1)


class ProtectedEnvironment(FreshEvidenceModel):
    """One approval environment's protection, as configured rather than as intended."""

    source: Literal["github"]
    environment: EvidenceEnvironment
    organization: SecretFreeStr = Field(min_length=1)
    repository: SecretFreeStr = Field(min_length=1)
    name: SecretFreeStr = Field(min_length=1)

    #: One-of-N semantics: any listed reviewer may release a deployment.
    reviewers: Annotated[
        tuple[EnvironmentReviewer, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(strict=False)

    #: Deliberately false on both environments. Leads self-authorizing routine runs and
    #: admins approving their own exceptions are intended, per the global constraints, so
    #: turning this on would break the behaviour rather than tighten it. The prohibition
    #: that does apply -- a member cannot approve their own submission -- is enforced by
    #: members not being reviewers, and independently by evaluate_authorization.
    prevent_self_review: bool

    #: Whether a repository admin may release a deployment without a reviewer, through
    #: "Start all waiting jobs". Left on, this produces no approval record at all, which
    #: removes the attribution the whole design leans on rather than merely widening who
    #: may approve.
    can_admins_bypass: bool

    #: The two forms, kept apart. See the module docstring.
    protected_branches: bool
    custom_branch_policies: bool
    branch_policy_names: OrderedStrings = Field(strict=False)

    wait_timer_minutes: int = Field(ge=0)


class EnvironmentInventory(FreshEvidenceModel):
    """Every environment on the repository, not only the ones this phase expects.

    Capturing all of them is the point. An environment is auto-created, with no protection
    rules whatsoever, by anyone who can name one in a workflow file -- which is everybody
    who holds write, which is everybody who can submit. A capture that read only the two
    expected names would report a healthy gate while a third, unprotected environment sat
    beside it. The trust policy enumerates two subjects and refuses a third, so such an
    environment could not reach AWS; it could still mislead a reader of this evidence.
    """

    source: Literal["github"]
    environment: EvidenceEnvironment
    organization: SecretFreeStr = Field(min_length=1)
    repository: SecretFreeStr = Field(min_length=1)
    environments: Annotated[
        tuple[ProtectedEnvironment, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(strict=False)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(environment.name for environment in self.environments)


class SecretInventory(FreshEvidenceModel):
    """Secret and variable names at every level that can reach a workflow.

    Names only. There is no field a value could be written into, which is a stronger
    guarantee than a tool that is careful. Variables are recorded beside secrets because
    the criterion is about what a workflow can read, and the distinction between the two
    is whether GitHub masks it rather than whether it matters.
    """

    source: Literal["github"]
    environment: EvidenceEnvironment
    organization: SecretFreeStr = Field(min_length=1)
    repository: SecretFreeStr = Field(min_length=1)

    #: The one that must stay empty. A repository secret is readable by a workflow on any
    #: branch, so a credential here is reachable from a branch nobody reviewed.
    repository_secret_names: OrderedStrings = Field(strict=False)
    organization_secret_names: OrderedStrings = Field(strict=False)
    dependabot_secret_names: OrderedStrings = Field(strict=False)

    #: Environment secrets are the permitted home for a credential, because an environment
    #: carries a deployment branch policy and a reviewer list. Recorded per environment so
    #: that "it is an environment secret" can be checked against which environment.
    environment_secret_names: Annotated[
        dict[str, tuple[str, ...]], BeforeValidator(_ordered_by_environment)
    ] = Field(default_factory=dict, strict=False)

    repository_variable_names: OrderedStrings = Field(strict=False)
