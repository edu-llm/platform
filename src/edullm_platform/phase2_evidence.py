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

**Who is in that team is therefore a second record, and until 2026-07-31 there was none.**
Keeping the reviewer as a team is right and it leaves a question the environment capture
cannot answer: one slot with eight people behind it, held in organization settings that no
file in this repository follows and that an owner can edit without leaving an artifact
anywhere. A member added to the team becomes a reviewer on the lead gate, and every test
reading the environment capture goes on passing. :class:`LeadTeamMembership` is the answer,
and it carries the team's slug beside its members so a capture of some other team cannot be
mistaken for this one.

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
    "LEAD_APPROVAL_TEAM_SLUG",
    "ROLE_TEAM_SLUGS",
    "EnvironmentInventory",
    "EnvironmentReviewer",
    "LeadTeamMembership",
    "ProtectedEnvironment",
    "ResearchTeamInventory",
    "ResearchTeamMembership",
    "SecretInventory",
]

#: The two names the admission role's trust policy enumerates. A capture naming anything
#: else is capturing the wrong environments, and a capture missing one of these is
#: capturing an incomplete gate.
APPROVAL_ENVIRONMENT_NAMES: tuple[str, ...] = ("run-approval-lead", "run-approval-admin")

#: The one team that reviews ``run-approval-lead``. Written down here rather than read off
#: the environment capture, because a capture is the thing being checked: a tool that
#: asked GitHub which team reviews the gate and then captured that team's members could
#: never disagree with the gate, and disagreeing is the whole job.
LEAD_APPROVAL_TEAM_SLUG: str = "team-leads"

#: The GitHub teams that are roles rather than research groups. ``team-leads`` reviews the
#: lead approval gate and ``team-members`` is how write access on this repository is
#: granted. Neither is a group a run can be attributed to, so both are held out of every
#: comparison against the groups ``config/organization.yaml`` declares.
ROLE_TEAM_SLUGS: tuple[str, ...] = (LEAD_APPROVAL_TEAM_SLUG, "team-members")

#: The three roles Phase 2 creates, and the committed templates that declare them. The
#: Phase 1 list is separate on purpose: these roles belong to a different phase's evidence
#: and a different phase's freshness window, and folding them into COMMITTED_ROLE_TEMPLATES
#: would make a Phase 1 capture fail because a Phase 2 role drifted.
PHASE2_ROLE_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("sbsandbox-intern-edullm-admission", "infra/iam/admission-role.yaml"),
    ("sbsandbox-intern-edullm-admission-states", "infra/iam/admission-service-roles.yaml"),
    ("sbsandbox-intern-edullm-admission-lambda", "infra/iam/admission-service-roles.yaml"),
)

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


class LeadTeamMembership(FreshEvidenceModel):
    """Everyone in the GitHub team that reviews ``run-approval-lead``, as GitHub lists them.

    The record :class:`ProtectedEnvironment` structurally cannot hold, and the reason it
    cannot is deliberate. The gate names one reviewer of kind ``Team``; who stands behind
    that slot is organization state, not repository state, and the platform's own list of
    leads is ``team_leads`` in ``config/organization.yaml``. The two are edited in different
    places by different people and have already disagreed in both directions at once.

    **Logins are recorded exactly as GitHub returned them, including any this repository
    does not declare, and there is no placeholder for one.** ``phase1_evidence``'s
    ``UNDECLARED_IDENTITY_PLACEHOLDER`` exists for the opposite situation -- a per-person
    IAM role in a sandbox account shared with teams this project has nothing to do with --
    and folding an unrecognized login into a placeholder here would delete the single fact
    this record was built to surface. ``tools/capture_phase2_evidence.py`` argues that in
    full at the point where the redaction would have gone.

    ``team_slug`` is captured rather than assumed so that a capture of some other team
    cannot be read as this one, and ``tests/test_phase2_github_evidence.py`` pins it
    against the reviewer the lead gate actually names. ``repository`` is recorded even
    though a team belongs to an organization: the claim being made is about this
    repository's gate, and the same team can review an environment on a repository this
    platform does not own.

    ``member_logins`` may be empty and that is not refused. A team with nobody in it is a
    lead gate no routine run can ever pass, which is a real state worth writing down
    rather than a capture failure -- and the roster comparison reports it by naming all
    eight leads as missing, which is louder than any refusal here would be.
    """

    source: Literal["github"]
    environment: EvidenceEnvironment
    organization: SecretFreeStr = Field(min_length=1)
    repository: SecretFreeStr = Field(min_length=1)
    team_slug: SecretFreeStr = Field(min_length=1)

    #: Plain ``str`` where every other field here is ``SecretFreeStr``, and the exception
    #: is a decision rather than an oversight. That scan refuses any bare run of twelve
    #: digits, because in free text that is an AWS account id -- and a GitHub login of
    #: twelve digits is a legal login, so the scan would refuse a real member and take the
    #: whole record down with him, failing the criteria that read it for a reason having
    #: nothing to do with the gate. What the scan would be protecting is already had by
    #: shape: the endpoint behind this field returns logins, nothing but the login is
    #: written, and there is no field here a credential could arrive in.
    #:
    #: The inconsistency that leaves is real and is the lesser risk. ``EnvironmentReviewer
    #: .name`` carries logins too and keeps the scan, so it would refuse the same login --
    #: but that field holds reviewers this repository chose and would recognize, while
    #: this one holds everyone an owner has added to a team, which is precisely where a
    #: login nobody here chose turns up.
    member_logins: OrderedStrings = Field(strict=False)


class ResearchTeamMembership(FreshEvidenceModel):
    """One research group's GitHub team, its repository permission, and who is in it.

    ``config/organization.yaml`` names a ``github_team_slug`` for each group it declares,
    and that name is a claim about another system. Nothing in this repository could check
    it: a slug naming a team that was never created, or created and later renamed, reads
    exactly like a slug naming one that exists, and the failure it produces arrives much
    later as a person who cannot see the Run button.

    ``repository_permission`` is captured beside the members because the two answer
    different halves of one question. Membership decides who the team contains;
    the permission decides whether containing them grants anything, and a team with
    ``pull`` on this repository leaves every member unable to see the submission workflow
    at all, since GitHub shows a manual workflow only to people who can write.

    ``member_logins`` may be empty and that is not refused. Every research team is empty
    today, which is the state the roster records as well, and a capture that treated it as
    a failure could not record the thing that is actually true.
    """

    source: Literal["github"]
    environment: EvidenceEnvironment
    organization: SecretFreeStr = Field(min_length=1)
    repository: SecretFreeStr = Field(min_length=1)
    team_slug: SecretFreeStr = Field(min_length=1)
    repository_permission: SecretFreeStr = Field(min_length=1)

    #: Plain ``str`` for the reason :class:`LeadTeamMembership` gives at length: a login of
    #: twelve digits is legal on GitHub and the secret scan reads a bare run of twelve
    #: digits as an AWS account id, so scanning here would refuse a real member and take
    #: the record down with him.
    member_logins: OrderedStrings = Field(strict=False)


class ResearchTeamInventory(FreshEvidenceModel):
    """Every GitHub team in the organization that is not one of the two role teams.

    Captured as the whole set rather than as the teams the roster happens to name, because
    the interesting failure is in the direction a roster-driven capture cannot see. A team
    created on GitHub and bound to nothing is a group whose runs nothing can attribute and
    whose members were granted access nobody wrote down, and a capture that asked only
    about declared slugs would report a clean result while it sat there.

    ``team-leads`` and ``team-members`` are excluded by name rather than filtered by shape.
    They are role teams, not research groups: one is the reviewer on the lead approval gate
    and the other is how everybody gets write access, and folding either into a comparison
    against the research groups would report both as unbound forever.
    """

    source: Literal["github"]
    environment: EvidenceEnvironment
    organization: SecretFreeStr = Field(min_length=1)
    repository: SecretFreeStr = Field(min_length=1)
    teams: Annotated[
        tuple[ResearchTeamMembership, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(strict=False)

    @property
    def slugs(self) -> tuple[str, ...]:
        return tuple(team.team_slug for team in self.teams)


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


class LineageObject(FreshEvidenceModel):
    """One object in the lineage store, as S3 describes it rather than as we wrote it.

    The two digests are separate fields and must stay separate. ``checksum_sha256`` is what
    S3 computed over the bytes it holds; ``manifest_sha256`` is what the platform computed
    over the manifest's canonical serialization and is the value an approval was taken
    against. They answer different questions -- did the object arrive intact, and is this
    the manifest that was approved -- and a record that conflated them would be a lineage
    error rather than a wording slip.
    """

    key: SecretFreeStr = Field(min_length=1)
    version_id: SecretFreeStr = Field(min_length=1)
    checksum_sha256: SecretFreeStr = Field(min_length=1)
    content_length: int = Field(ge=0)

    #: Whether the stored bytes are exactly canonical_json_bytes of the record they hold.
    #: False for objects written before the encoding fix, which were stored as a JSON
    #: string rather than an object. Recorded rather than filtered out: a capture that
    #: omitted the older shape would make the store look more uniform than it is.
    canonical: bool


class LineageInventory(FreshEvidenceModel):
    """Every object in the lineage store, with what S3 attests about each."""

    source: Literal["aws"]
    environment: EvidenceEnvironment
    bucket: SecretFreeStr = Field(min_length=1)
    objects: Annotated[
        tuple[LineageObject, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(strict=False)


class AdmissionExecution(FreshEvidenceModel):
    """One admission execution and where it ended.

    The name is the run id, which is what makes the duplicate-name refusal meaningful: a
    second StartExecution under a name that has closed is answered
    ``ExecutionAlreadyExists`` for ninety days, so the name is the deduplication key rather
    than a label.
    """

    name: SecretFreeStr = Field(min_length=1)
    status: Literal["SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED", "RUNNING"]

    #: Present only on a failure. AdmissionRejected is the state machine refusing a
    #: submission the validator judged; anything else is the machine itself failing, and
    #: the two mean very different things about whether admission worked.
    error: SecretFreeStr | None = None


class AdmissionExecutionInventory(FreshEvidenceModel):
    """Every execution the admission state machine has run."""

    source: Literal["aws"]
    environment: EvidenceEnvironment
    state_machine_name: SecretFreeStr = Field(min_length=1)
    executions: Annotated[
        tuple[AdmissionExecution, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(strict=False)
