"""A second attempt sold on a declaration that nobody had ever watched come true.

Two checks used to stand between a submission and a retry bound. ``require_checkpoint_for_
retries`` asks whether the workload profile carries a checkpoint contract, and
``require_a_save_folder_a_retry_can_find`` asks whether the command expands
``$EDULLM_CHECKPOINT_DIR``. Neither reads the codebase, and the thing that decides whether a
retry resumes is in the codebase.

**BOTH PASS FOR BOTH REPOSITORIES THAT WERE MEASURED NOT RESUMING, WHICH IS WHAT MAKES THEM
A PAIR OF CHECKS THAT CANNOT FAIL.** ``edullm-p1`` carries the contract, expands the
variable, honours it on save, and starts a second attempt from step 0 for three independent
reasons in its own source. ``open-instruct-scored-rewards`` does the same and gates its load
on ``os.path.exists`` against an ``s3://`` URI, which is the only kind of path this platform
hands out. ``test_the_two_older_checks_pass_for_a_repository_measured_restarting_from_zero``
below is that stated as a test rather than as a paragraph, so that anybody weakening the new
guard has to delete a failing assertion rather than reason their way past a docstring.

**WHAT REPLACES THEM IS AN OBSERVATION AND NOT A THIRD INFERENCE.** A repository is granted
more than one attempt when ``config/reports/resume-demonstrations.yaml`` cites a run of it in
which a second process reported resuming from a step above zero and went on past it. The
entry is a run id and two integers, which a reviewer can check by opening the run; it is not
a field asserting that a trainer resumes, which is the shape ``checkpoint_commands``'s module
docstring rejected and which this is deliberately not.
"""

from __future__ import annotations

import shlex
from datetime import UTC, datetime

import pytest
from test_phase2_submission import load_resume_demonstrations

from edullm_platform.checkpoint_commands import (
    CHECKPOINT_DIRECTORY_VARIABLE,
    RESUME_CHECK_WAIVER,
    expands_the_checkpoint_directory,
    require_a_demonstrated_resume_for_retries,
    require_a_save_folder_a_retry_can_find,
    resume_note,
)
from edullm_platform.contracts.resume_evidence import (
    NO_RESUME_DEMONSTRATIONS,
    ResumeDemonstration,
    ResumeDemonstrations,
)
from edullm_platform.contracts.validation import require_checkpoint_for_retries
from edullm_platform.contracts.workload import CheckpointContract
from edullm_platform.errors import ResumeNotDemonstratedError, SubmissionRefusedError

#: The repository the shipped file records a demonstration for, and the two that were
#: measured restarting from nothing. Named rather than read off the registry, because the
#: point of each is what was measured about it and not that it is registered.
DEMONSTRATED = "OLMo-core"
MEASURED_RESTARTING = ("edullm-p1", "open-instruct-scored-rewards")

#: A command shaped like the ones those two repositories actually submit: it expands the
#: variable, under a shell, exactly as the older guard requires.
SAVES_WHERE_A_RETRY_LOOKS = tuple(
    shlex.split(f"bash -lc 'python train.py --save-folder \"${CHECKPOINT_DIRECTORY_VARIABLE}\"'")
)


def contract() -> CheckpointContract:
    return CheckpointContract(
        interval_minutes=30,
        destination_prefix="s3://sbsandbox-intern-edullm-outputs/teams/",
        resume_required=True,
    )


def demonstration(repository: str = DEMONSTRATED) -> ResumeDemonstrations:
    return ResumeDemonstrations(
        schema_version=1,
        demonstrations=(
            ResumeDemonstration(
                repository=repository,
                run_id="run_019fdd8f-ad71-70f6-bc19-e091cf3b22a7",
                commit_sha="08df5aa0142465c80b4ea48e84faa46117275d61",
                workload_profile="olmo-core-train",
                compute_profile="gpu-1xa10g",
                resumed_from_step=120,
                reached_step=700,
                observed="the second process reported resuming from step 120 and went on",
                recorded_by="philote-dev",
                recorded_at=datetime(2026, 8, 7, 19, 0, tzinfo=UTC),
            ),
        ),
    )


def refuse(**overrides: object) -> None:
    arguments: dict[str, object] = {
        "command": SAVES_WHERE_A_RETRY_LOOKS,
        "maximum_attempts": 2,
        "repository": DEMONSTRATED,
        "workload_profile": "olmo-core-train",
        "demonstrations": NO_RESUME_DEMONSTRATIONS,
    }
    arguments.update(overrides)
    require_a_demonstrated_resume_for_retries(**arguments)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------
# The guard this replaces, held as a test so that weakening it costs a deletion
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("repository", MEASURED_RESTARTING)
def test_the_two_older_checks_pass_for_a_repository_measured_restarting_from_zero(
    repository: str,
) -> None:
    """Both of them, on a command of the shape that repository submits.

    This is the finding the whole module exists for and it is asserted rather than described
    because a description is what it was. The repository name is unused by either check,
    which is precisely the problem: neither can tell these two apart from one that resumes.
    """
    require_checkpoint_for_retries(maximum_attempts=2, checkpoint=contract())
    require_a_save_folder_a_retry_can_find(
        command=SAVES_WHERE_A_RETRY_LOOKS,
        workload_profile=f"{repository}-train",
        checkpoint=contract(),
    )

    assert expands_the_checkpoint_directory(SAVES_WHERE_A_RETRY_LOOKS)


# ---------------------------------------------------------------------------------------
# The guard that can fail
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("repository", MEASURED_RESTARTING)
def test_a_second_attempt_is_refused_for_a_repository_nobody_has_watched_resume(
    repository: str,
) -> None:
    with pytest.raises(ResumeNotDemonstratedError) as refusal:
        refuse(repository=repository, workload_profile=f"{repository}-train")

    assert repository in str(refusal.value)
    # The way out is named, because a refusal a submitter cannot act on is a wall.
    assert RESUME_CHECK_WAIVER in str(refusal.value)
    assert "one attempt" in str(refusal.value)


def test_a_second_attempt_stands_for_a_repository_that_has_been_watched_resuming() -> None:
    refuse(demonstrations=demonstration())


def test_the_demonstration_has_to_be_for_this_repository_and_not_for_any_repository() -> None:
    """Mutation: ask whether the file holds any demonstration at all.

    A trainer is a property of a codebase. ``edullm-p1`` does not resume because OLMo-core
    does, and a guard reading the file as a single flag would clear the two repositories it
    was written for on the strength of the one it was not.
    """
    with pytest.raises(ResumeNotDemonstratedError):
        refuse(repository="edullm-p1", demonstrations=demonstration(DEMONSTRATED))


def test_a_single_attempt_run_is_not_asked_the_question() -> None:
    """One attempt buys nothing on a retry, so there is nothing to justify.

    A refusal here would stop every run of every repository nobody has demonstrated, which
    is every run this platform has ever accepted, over a second attempt it did not ask for.
    """
    refuse(maximum_attempts=1)


def test_the_waiver_buys_the_second_attempt_and_is_spelled_like_the_other_two() -> None:
    """Mutation: make the refusal absolute.

    A refusal with no way past it makes a lost host cost the whole run for anybody whose
    repository has no demonstration yet, which is worse than what is being refused. The
    token travels in the command, so it is inside the hashed manifest and the immutable
    lineage record, and the note below puts it in front of the lead.
    """
    waived = tuple(
        shlex.split(
            f"bash -lc '{RESUME_CHECK_WAIVER} python train.py "
            f'--save-folder "${CHECKPOINT_DIRECTORY_VARIABLE}"\''
        )
    )

    refuse(command=waived)


# ---------------------------------------------------------------------------------------
# What the approver and the caller are told
# ---------------------------------------------------------------------------------------


def note(**overrides: object) -> str | None:
    arguments: dict[str, object] = {
        "command": SAVES_WHERE_A_RETRY_LOOKS,
        "maximum_attempts": 2,
        "repository": DEMONSTRATED,
        "workload_profile": "olmo-core-train",
        "checkpoint": contract(),
        "demonstrations": NO_RESUME_DEMONSTRATIONS,
    }
    arguments.update(overrides)
    return resume_note(**arguments)  # type: ignore[arg-type]


def test_the_note_cites_the_run_and_the_two_steps_rather_than_asserting_that_it_resumes() -> None:
    """Mutation: say "this repository resumes".

    The sentence is what an approver decides on, and the difference between a citation and
    an assertion is the whole of what this mechanism adds. A reader who is given a run id
    and two step numbers can check them; a reader who is given a verdict cannot.
    """
    said = note(demonstrations=demonstration())

    assert said is not None
    assert "run_019fdd8f-ad71-70f6-bc19-e091cf3b22a7" in said
    assert "step 120" in said
    assert "step 700" in said
    # The commit, so that a reader can see how old the evidence is against what they submit.
    assert "08df5aa01424" in said


def test_the_note_says_the_run_is_refused_when_it_is_rather_than_inventing_a_waiver() -> None:
    """Mutation: assume the only way past the guard was taken.

    Past the refusal a waiver is the only way to reach this sentence, so the first version
    was written as though one had been used. ``edullm check`` composes the note beside the
    refusal rather than instead of it, so every refused submission was told it carried a
    token it had not written, and a submitter reading that goes looking for it in their own
    command.
    """
    said = note()

    assert said is not None
    assert RESUME_CHECK_WAIVER not in said
    assert "refused more than one attempt" in said


def test_the_note_reports_the_waiver_when_the_command_carries_one() -> None:
    waived = (*SAVES_WHERE_A_RETRY_LOOKS, RESUME_CHECK_WAIVER)

    said = note(command=waived)

    assert said is not None
    assert RESUME_CHECK_WAIVER in said


def test_a_waiver_over_a_demonstration_reports_the_demonstration() -> None:
    """A waiver on a repository that has been watched resuming is waiving nothing.

    Reporting it over the measurement would put the weaker of two true things in front of
    the approver, which is the same argument ``waived_checkpoint_check_note`` makes for
    returning ``None`` on a command that saves where a retry looks and carries the token too.
    """
    said = note(
        command=(*SAVES_WHERE_A_RETRY_LOOKS, RESUME_CHECK_WAIVER),
        demonstrations=demonstration(),
    )

    assert said is not None
    assert RESUME_CHECK_WAIVER not in said
    assert "has been watched resuming" in said


def test_the_note_reads_the_same_in_a_terminal_a_document_and_a_markdown_page() -> None:
    """One string, three renderings, so emphasis in one is punctuation in the other two.

    ``placement_said`` carries the same constraint for the same reason, and the underscores
    in the variable name are not emphasis: GitHub-flavoured markdown does not open an
    emphasis run inside a word.
    """
    for said in (note(), note(demonstrations=demonstration())):
        assert said is not None
        assert "*" not in said
        assert "`" not in said


# ---------------------------------------------------------------------------------------
# The shipped file
# ---------------------------------------------------------------------------------------


def test_the_shipped_file_records_the_run_that_bought_olmo_core_its_second_attempt() -> None:
    """The committed evidence, held so that deleting it fails rather than quietly re-opens.

    ``config/workload-catalog.yaml`` grants ``olmo-core-train`` two attempts. Without an
    entry here that profile is refused, so this assertion and that catalog line are two
    halves of one fact and the suite should say so if they come apart.
    """
    entry = load_resume_demonstrations().for_repository(DEMONSTRATED)

    assert entry is not None
    assert entry.resumed_from_step > 0
    assert entry.reached_step > entry.resumed_from_step


@pytest.mark.parametrize("repository", MEASURED_RESTARTING)
def test_the_shipped_file_records_nothing_for_the_repositories_measured_restarting(
    repository: str,
) -> None:
    """Neither has been watched resuming, and neither may buy a second attempt on a claim.

    Both declare ``resume_required: true`` in the catalog today. That field is a declaration
    no code branches on, and this is what stands in its place.
    """
    assert load_resume_demonstrations().for_repository(repository) is None

    with pytest.raises(SubmissionRefusedError):
        refuse(repository=repository, demonstrations=load_resume_demonstrations())
