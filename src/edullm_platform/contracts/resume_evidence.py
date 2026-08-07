"""Which repositories have been watched resuming, and what was watched.

**AN OBSERVATION RATHER THAN A DECLARATION, WHICH IS THE WHOLE OF WHY THIS FILE IS NOT THE
THING ITS OWN MODULE DOCSTRING ONCE REFUSED TO BUILD.**
:mod:`edullm_platform.checkpoint_commands` argued against "a per-repository field asserting
that one resumes", on the ground that it would be "the same easy question standing in for
the same hard one, one level further up". That argument is right about an assertion and does
not reach an entry like this one, because nothing here may be written from reading a
trainer. An entry names a platform run, the commit whose image that run executed, the step a
second process reported resuming from and the step it went on to reach. Every one of those
is a number somebody read off a run that happened, and a reviewer of the pull request adding
one can open the run and see the same numbers.

**WHY THE STEP PAIR IS TWO FIELDS AND NOT A BOOLEAN.** A trainer that loads a checkpoint and
then trains nothing has resumed in the sense a boolean would record and in no sense anybody
cares about; so has one that loads the weights, reports step zero and starts the learning
rate schedule again. :meth:`ResumeDemonstration.continued` is what the entry has to satisfy,
and it is the only claim this file makes: a second process reported a step above zero and
finished above where it started. What that leaves out -- whether the loss met across the
boundary, whether the data loader skipped the batches it had already seen -- goes in
``observed``, in prose, because those are curves rather than integers and the reviewer
reading the run is who judges them.

**A REPOSITORY RATHER THAN A WORKLOAD PROFILE OR A COMMAND.** What resumes or does not is a
trainer, and a trainer belongs to a codebase. Two profiles over one repository are the same
program with different bounds, and a demonstration under either is a demonstration of the
same load path. The profile and the machine are recorded anyway, because they are what a
reader needs to judge whether the demonstration resembles the run they are about to submit.

**AN ENTRY GOES STALE AND NOTHING HERE PRETENDS OTHERWISE.** ``commit_sha`` is the commit the
demonstration ran, and a repository whose trainer changed the day after has an entry
describing code nobody runs any more. The deliberate choice is that staleness does not
refuse: :func:`ResumeDemonstrations.for_repository` answers with the entry and the caller
prints its date and its commit, so a submitter is told how old the evidence is and decides.
Expiring entries automatically was the alternative and it fails in the expensive direction --
it would refuse a twenty-four-hour run at two in the morning over a demonstration that
lapsed while nothing about the trainer changed.
"""

from typing import Annotated, Self

from pydantic import BeforeValidator, Field, model_validator

from .base import ContractModel, UtcTimestamp, require_ordered_sequence
from .bindings import GitHubLogin, RepositoryName
from .identity import RunId
from .manifest import COMMIT_SHA_PATTERN


class ResumeDemonstration(ContractModel):
    """One run on which a second process picked up a first one's checkpoint and went on.

    ``observed`` carries the length floor ``CheckpointAcknowledgement.reason`` carries and
    for the same reason: "it resumed" is not an observation, and the value of the entry is
    that a later reader can tell what was actually watched. Say how the first process was
    stopped, what the second one said about loading, and whether the loss and the learning
    rate met across the boundary or jumped.
    """

    repository: RepositoryName
    run_id: RunId
    commit_sha: str = Field(pattern=COMMIT_SHA_PATTERN)
    workload_profile: str = Field(min_length=1, pattern=r"^\S+$")
    compute_profile: str = Field(min_length=1, pattern=r"^\S+$")
    #: The step the resuming process reported starting from. Above zero, because zero is
    #: precisely the outcome this file exists to distinguish a demonstration from.
    resumed_from_step: int = Field(gt=0)
    #: The step it reached. Above the one above, because a process that loads a checkpoint
    #: and trains nothing has demonstrated a read rather than a resume.
    reached_step: int = Field(gt=0)
    observed: str = Field(min_length=40)
    recorded_by: GitHubLogin
    recorded_at: UtcTimestamp

    @model_validator(mode="after")
    def validate_the_run_continued(self) -> Self:
        if self.reached_step <= self.resumed_from_step:
            raise ValueError(
                "a demonstration must reach a step above the one it resumed from, "
                f"and this one resumed from {self.resumed_from_step} and reached "
                f"{self.reached_step}"
            )
        return self


class ResumeDemonstrations(ContractModel):
    """The demonstrations, as a file this repository reviews like any other change.

    Under ``contracts/`` rather than in the tool that writes it, which is the opposite of
    where ``CheckpointAcknowledgement`` sits and for the reason recorded there. That list
    scopes a report and changes no run's outcome. This one is read on the decision path:
    :func:`~edullm_platform.checkpoint_commands.require_a_demonstrated_resume_for_retries`
    refuses a second attempt over it, so the schema belongs where the other contracts a
    submission is judged against are.
    """

    schema_version: int = Field(ge=1, le=1)
    demonstrations: Annotated[
        tuple[ResumeDemonstration, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(default=(), strict=False)

    @model_validator(mode="after")
    def validate_one_demonstration_per_run(self) -> Self:
        named = [entry.run_id for entry in self.demonstrations]
        if len(set(named)) != len(named):
            raise ValueError("a run must not carry more than one demonstration")
        return self

    def for_repository(self, repository: str) -> ResumeDemonstration | None:
        """The most recent demonstration for this repository, or ``None`` if there is none.

        The most recent rather than the first, because a repository that has demonstrated
        resume twice has done so at two commits and the later one is the one a submitter is
        closer to running. Ties are impossible in practice and are broken by file order,
        which is the order a reviewer sees.
        """
        found = [entry for entry in self.demonstrations if entry.repository == repository]
        if not found:
            return None
        return max(found, key=lambda entry: entry.recorded_at)


#: What a caller that was handed no demonstrations has, and the fail-closed direction on
#: purpose. A compile step that forgot to pass the file refuses every second attempt, which
#: is a loud and cheap mistake; the other default would grant them all silently, which is the
#: state this whole mechanism exists to end.
NO_RESUME_DEMONSTRATIONS = ResumeDemonstrations(schema_version=1)
