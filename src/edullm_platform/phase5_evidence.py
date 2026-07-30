"""What a pilot run left behind, in the shapes Phase 5's checks are written against.

Phase 5's claim is about people rather than about mechanism, and it is worth stating
exactly: **somebody who did not build this platform submitted a run, somebody else released
it, and both facts can be read out of what they left behind rather than out of anybody's
memory of watching it happen.** These models are the "what they left behind" half. The
criteria read them; ``tools/capture_phase5_evidence.py`` writes them.

**One record spans two sources, and that is the point rather than a shortcut.** Every other
phase's run record describes one system. An admitted run has to describe two, because the
central Phase 5 claim -- that the digest written immutably into lineage is the digest the
container was actually given -- is a comparison *between* the lineage store and the
scheduler. Splitting it across two files would put the two halves of one assertion in two
records that nothing requires to be about the same run, which is the class of defect this
phase's own findings are mostly about.

**The division between what expires and what does not is the one Phase 4 established.** A
run happened, so :class:`AdmittedRunEvidence` extends
:class:`~edullm_platform.evidence.RecordedEventModel` and never expires. How a branch is
protected and what a registry currently holds are statements about now, are one browser
click and one push from being false, and extend
:class:`~edullm_platform.evidence.FreshEvidenceModel`.

**Every model here can only hold what a reader may see.** The one field that could
plausibly carry something else is the container's own image reference, and it is typed as a
digest rather than as free text for exactly that reason.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal, Self

from pydantic import BeforeValidator, Field, model_validator

from edullm_platform.contracts.base import (
    ContractModel,
    Sha256Digest,
    UtcTimestamp,
    require_ordered_sequence,
)
from edullm_platform.evidence import (
    DigestBearingStr,
    EvidenceEnvironment,
    FreshEvidenceModel,
    RecordedEventModel,
    SecretFreeStr,
)

__all__ = [
    "LEAD_APPROVAL_REASON",
    "SELF_AUTHORIZED_REASONS",
    "AdmittedRunEvidence",
    "BranchProtectionEvidence",
    "PublishedImageEvidence",
    "RunAuthorizationEvidence",
]

#: The reason code the entire two-person approval design exists to produce, and which had
#: never been written in twenty-five dispatches. Named here so the check for it has one
#: spelling rather than a string literal in each test that looks for it.
LEAD_APPROVAL_REASON: Final = "routine_approved_by_lead_or_admin"

#: What every accepted decision record said before a second person used the platform. Kept
#: beside the reason above because the distinction between them is the whole of criterion 2:
#: all three are granted authorizations, and only the first is evidence about the path a
#: member takes.
SELF_AUTHORIZED_REASONS: Final = (
    "routine_self_authorized",
    "exception_self_approved_by_admin",
)

OrderedText = Annotated[tuple[str, ...], BeforeValidator(require_ordered_sequence)]


class RunAuthorizationEvidence(ContractModel):
    """The authorization block a decision record carries, copied field for field.

    Copied rather than summarised into a verdict. A boolean saying "released by somebody
    else" would be this module deciding the criterion, and the record would then be
    unreadable as evidence for anything the criterion did not anticipate -- which is most of
    what a reader of a proof bundle is doing.

    ``team_verified`` is carried even though it is false on every record so far, because a
    field that is recorded and false is a statement about a control that does not exist,
    and a field that is absent is a statement about nothing.
    """

    approval_class: SecretFreeStr = Field(min_length=1)
    approval_scope: SecretFreeStr = Field(min_length=1)
    approver: SecretFreeStr = Field(min_length=1)
    claimed_team: SecretFreeStr = Field(min_length=1)
    granted: bool
    reason: SecretFreeStr = Field(min_length=1)
    submitter: SecretFreeStr = Field(min_length=1)
    team_verified: bool

    @property
    def released_by_another_person(self) -> bool:
        return self.approver != self.submitter


class AdmittedRunEvidence(RecordedEventModel):
    """One pilot run, as the lineage store and the scheduler together describe it.

    ``declared_image_digest`` is what the manifest was admitted on and what lineage records
    immutably. ``container_image_digest`` is what the scheduler says the container was
    given. Criterion 4 is the assertion that they are the same, and it is only worth making
    because they are read from different systems -- a record that derived one from the other
    could not fail.

    ``container_image_digest`` is optional because a run can be admitted, submitted to Batch
    and never start: the first pilot run reached an instance and died resolving its own
    command line against ``$PATH``. That run has no result record and no exit code, and it
    is committed rather than dropped, because a phase whose evidence is only its successes
    is a phase that has not been tested.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    region: SecretFreeStr = Field(min_length=1)

    run_id: SecretFreeStr = Field(min_length=1)
    submitter: SecretFreeStr = Field(min_length=1)
    workflow_run_id: int = Field(gt=0)
    workflow_path: SecretFreeStr = Field(min_length=1)
    workflow_ref: SecretFreeStr = Field(min_length=1)

    manifest_sha256: Sha256Digest
    declared_commit_sha: DigestBearingStr
    declared_image_digest: Sha256Digest
    repository: SecretFreeStr = Field(min_length=1)
    team: SecretFreeStr = Field(min_length=1)
    compute_profile: SecretFreeStr = Field(min_length=1)
    workload_profile: SecretFreeStr = Field(min_length=1)

    authorization: RunAuthorizationEvidence

    batch_job_id: SecretFreeStr | None
    job_definition_name: SecretFreeStr | None
    container_image_digest: Sha256Digest | None
    scheduler_status: SecretFreeStr | None
    exit_code: int | None
    #: Every lifecycle state the store recorded for this run, oldest first. The failed run's
    #: reads ``runnable, runnable, failed``, which is what a container that never started
    #: looks like from outside.
    recorded_states: OrderedText = Field(strict=False)

    result_outcome: SecretFreeStr | None
    output_prefixes: OrderedText = Field(strict=False)
    wandb_run: SecretFreeStr | None

    @model_validator(mode="after")
    def a_run_that_produced_no_result_may_not_claim_one(self) -> Self:
        if self.result_outcome is None and self.output_prefixes:
            raise ValueError(
                "a run with no result record cannot name an output prefix; the prefix is "
                "read out of the result manifest and there is none"
            )
        return self

    @property
    def image_that_ran_is_the_image_admitted(self) -> bool:
        """Whether the container was given the digest the manifest was admitted on.

        False rather than true when nothing ran. An unstarted run pulled no image, and a
        property that answered "yes, trivially" would let criterion 4 be closed by a run
        that never reached a container.
        """
        return (
            self.container_image_digest is not None
            and self.container_image_digest == self.declared_image_digest
        )

    @property
    def released_by_another_person(self) -> bool:
        return self.authorization.released_by_another_person


class PublishedImageEvidence(FreshEvidenceModel):
    """What the registry holds for the commit the pilot runs declared.

    A ``FreshEvidenceModel`` rather than a recorded event, and the distinction is real
    here: the push happened and is settled, but what this record is *used* for is the claim
    that the digest is reachable from the commit today, and an image can be deleted from a
    repository. The commit and the tag are what tie the two together -- the build workflow
    tags with the first twelve characters of the commit SHA, so the tag is not decoration.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    region: SecretFreeStr = Field(min_length=1)
    repository_name: SecretFreeStr = Field(min_length=1)

    commit_sha: DigestBearingStr
    image_tag: SecretFreeStr = Field(min_length=1)
    image_digest: Sha256Digest
    pushed_at: UtcTimestamp
    #: Every tag the registry holds for this repository, so a reader can see for themselves
    #: that one commit produced one image rather than taking the claim on the tag alone.
    published_tags: OrderedText = Field(strict=False)

    @model_validator(mode="after")
    def the_tag_has_to_be_the_commits_own_prefix(self) -> Self:
        if self.image_tag != self.commit_sha[: len(self.image_tag)]:
            raise ValueError(
                f"tag {self.image_tag!r} is not a prefix of commit {self.commit_sha!r}, so "
                "this record does not tie the digest to the commit it claims to"
            )
        return self


class BranchProtectionEvidence(FreshEvidenceModel):
    """How the default branch is protected, and who may go round it.

    ``enforce_admins`` is recorded rather than asserted away. It is false by decision, and
    the consequence is that the criterion resting on this record is about what a *member*
    may do -- so the field that makes the criterion narrower than it sounds has to be in the
    evidence, where a reader will see it, rather than only in the prose beside it.
    """

    source: Literal["github"]
    environment: EvidenceEnvironment
    organization: SecretFreeStr = Field(min_length=1)
    repository: SecretFreeStr = Field(min_length=1)
    branch: SecretFreeStr = Field(min_length=1)

    required_approving_review_count: int = Field(ge=0)
    require_code_owner_reviews: bool
    dismiss_stale_reviews: bool
    enforce_admins: bool
    allow_force_pushes: bool
    allow_deletions: bool
    required_conversation_resolution: bool
    required_status_checks: OrderedText = Field(strict=False)
