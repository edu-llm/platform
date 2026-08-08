"""The refusal that happens instead of exit 70, and the three silences it keeps.

``.edullm/train_on_corpus.py`` resolves ``--model-factory`` with ``getattr`` and exits 70 on a
miss, in the container's first seconds, with the run already priced, released by a lead,
admitted and given an instance. Nothing before the container could see it, because the name is
a string until something looks it up -- and the only thing that can look it up honestly is a
reading of the image, which is what ``config/image-contents.yaml`` now is.

**MOST OF THIS MODULE IS ABOUT WHAT THE GUARD DOES NOT REFUSE**, which is the harder half. A
rule that reads a recorded measurement can be wrong in a way the bfloat16 rule beside it cannot:
Turing has no bfloat16 whatever anybody believes, but a reading is dated, and a researcher on a
branch that adds a factory is right while the record is behind them. So the guard is silent on a
repository nobody has read, silent on a command that names no factory, and waivable in as many
words -- and each of those silences is a case where refusing would stop work that would have
succeeded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from edullm_platform.config import load_yaml
from edullm_platform.contracts.image_contents import (
    ImageContentsReading,
    ImageContentsRecord,
    ImageVocabulary,
    ReadingMethod,
    VocabularyName,
)
from edullm_platform.errors import ModelFactoryNotInTheImageError, SubmissionRefusedError
from edullm_platform.model_factory import (
    MODEL_FACTORY_CHECK_WAIVER,
    model_factory_request_in,
    require_a_model_factory_the_image_has,
    waived_model_factory_note,
)
from edullm_platform.reviewed_configuration import ConfigFile

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: The shipped record, so these cases run against the reading a researcher's install would use
#: rather than against a fixture that could drift from it.
RECORD = load_yaml(
    PROJECT_ROOT / "config" / ConfigFile.IMAGE_CONTENTS.value, ImageContentsRecord
)

#: The repository the record has a reading for, read off the record rather than written here so
#: that a second repository being probed does not silently take these cases out of the covered
#: set.
READ_REPOSITORY = RECORD.images_that_read(VocabularyName.MODEL_FACTORIES)[0]

#: A factory the reading holds and one it does not. Derived, for the reason above.
A_REAL_FACTORY = min(RECORD.names_some_image_carries(VocabularyName.MODEL_FACTORIES))
NOT_A_FACTORY = "olmo2_900Q"


def shell(inner: str) -> tuple[str, ...]:
    """The wrapper every real submission carries.

    ``ContainerOverrides.Command`` is exec form, so ``$EDULLM_RUN_ID`` has to be expanded by a
    shell the submitter supplies, and a guard that could only read a bare argv would read
    nothing about any real training command.
    """
    return ("bash", "-lc", inner)


def refuse(command: tuple[str, ...], *, repository: str = READ_REPOSITORY) -> str:
    with pytest.raises(ModelFactoryNotInTheImageError) as caught:
        require_a_model_factory_the_image_has(
            command=command, repository=repository, images=RECORD
        )
    return str(caught.value)


def allow(command: tuple[str, ...], *, repository: str = READ_REPOSITORY) -> None:
    require_a_model_factory_the_image_has(command=command, repository=repository, images=RECORD)


# ---------------------------------------------------------------------------------------
# Reading the name out of the command
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "inner",
    [
        f"python .edullm/train_on_corpus.py --model-factory {NOT_A_FACTORY}",
        f"python .edullm/train_on_corpus.py --model-factory={NOT_A_FACTORY}",
        f"python .edullm/train_on_corpus.py --model_factory {NOT_A_FACTORY}",
        (
            f"python .edullm/train_on_corpus.py $EDULLM_RUN_ID "
            f"--model-factory {NOT_A_FACTORY} optim.lr=3e-4"
        ),
    ],
)
def test_the_spellings_a_submitter_actually_writes_are_all_read(inner: str) -> None:
    """Mutation: match only ``--model-factory`` with a space after it.

    argparse accepts the equals form and the underscore form, so a submitter who writes either
    is writing a command that runs. A detector that missed one would be a guard that looked
    present and applied to some commands, which is worse than one nobody relies on.
    """
    assert model_factory_request_in(shell(inner)) == NOT_A_FACTORY


def test_a_repeated_flag_is_read_as_argparse_reads_it() -> None:
    """Mutation: return the first match.

    argparse keeps the last occurrence, so predicting the first is predicting a resolution the
    container will not perform -- refusing a command that works, or clearing one that does not.
    """
    assert (
        model_factory_request_in(
            shell(f"python train.py --model-factory {NOT_A_FACTORY} --model-factory {A_REAL_FACTORY}")
        )
        == A_REAL_FACTORY
    )


def test_a_shell_variable_of_a_similar_name_is_not_the_request() -> None:
    """Mutation: treat any ``NAME=value`` whose key normalises to the flag as the request.

    ``MODEL_FACTORY=x python train.py`` sets an environment variable the trainer never reads;
    the trainer takes ``--model-factory`` and nothing else. Reading the assignment would refuse
    a command on the strength of a variable that changes nothing.
    """
    assert model_factory_request_in(shell(f"MODEL_FACTORY={NOT_A_FACTORY} python train.py")) is None


def test_a_command_naming_no_factory_is_read_as_naming_none() -> None:
    assert model_factory_request_in(shell("python .edullm/train_on_corpus.py $EDULLM_RUN_ID")) is None


# ---------------------------------------------------------------------------------------
# What it refuses
# ---------------------------------------------------------------------------------------


def test_a_factory_no_read_image_has_is_refused_before_anything_is_spent() -> None:
    """Mutation: none. This is the defect, closed.

    The message has to do three things a bare refusal does not: say which image was read and at
    what commit, so a submitter can tell whether the reading is simply behind their branch; name
    the escape, because the reading being behind them is a real and ordinary state; and say what
    it would otherwise have cost, because a submitter who does not know exit 70 is expensive
    will read this as pedantry.
    """
    said = refuse(shell(f"python .edullm/train_on_corpus.py --model-factory {NOT_A_FACTORY}"))

    assert NOT_A_FACTORY in said
    assert READ_REPOSITORY in said
    assert MODEL_FACTORY_CHECK_WAIVER in said
    assert "exits 70" in said


def test_the_refusal_points_at_the_family_rather_than_listing_sixty_three_names() -> None:
    """Mutation: print every factory the image has.

    Sixty-three names is not a suggestion, it is a wall of text a reader skips. The mistakes
    this has to help with are a size that does not exist and a generation that does not, and for
    both the useful answer is the rest of the family the submitter was already reaching for.
    """
    said = refuse(shell("python train.py --model-factory olmo2_900Q"))

    assert "olmo2_1B" in said
    assert "llama3_8B" not in said, "the refusal is listing names from an unrelated family"


def test_the_refusal_says_it_read_the_command_and_not_the_running_container() -> None:
    """Mutation: drop the sentence that bounds the claim.

    ``precision.py`` makes this argument in full and it applies unchanged: a submitter who meets
    this once will reasonably believe the platform knows which runs name a bad factory. It knows
    which commands say so, against a reading with a date on it. A command that names no factory
    gets the trainer's own default and is not checked at all.
    """
    said = refuse(shell(f"python train.py --model-factory {NOT_A_FACTORY}"))

    assert "default" in said
    assert "recorded reading" in said or "read at" in said


# ---------------------------------------------------------------------------------------
# The three silences, which are the half that must not become a refusal
# ---------------------------------------------------------------------------------------


def test_a_factory_the_image_was_seen_to_have_passes() -> None:
    allow(shell(f"python .edullm/train_on_corpus.py --model-factory {A_REAL_FACTORY}"))


def test_a_repository_nobody_has_read_is_not_refused() -> None:
    """**THE SILENCE THAT MATTERS MOST.** Mutation: refuse when there is no reading.

    Five of the six registered repositories have no reading at all. Refusing on absence would
    stop every run naming any factory on any of them, which is work stopped by a file nobody has
    written -- this platform asserting something about an image it has not read, which is the
    exact failure the record exists to end, arriving from the other direction.
    """
    allow(
        shell(f"python train.py --model-factory {NOT_A_FACTORY}"),
        repository="a-repository-nobody-has-probed",
    )


def test_a_repository_read_for_tokenizers_only_is_not_refused_on_factories() -> None:
    """Mutation: read an unrecorded vocabulary as an empty one.

    The finer-grained half of the silence above, and the one a future vocabulary will meet
    first: a reading that established the tokenizers of an image says nothing about its
    factories, and treating the missing entry as "it has none" would refuse every factory on an
    image somebody had partly measured.
    """
    partly_read = ImageContentsRecord(
        schema_version=1,
        images=(
            ImageContentsReading(
                repository="OLMo-core",
                commit_sha="0" * 40,
                read_by=ReadingMethod.SOURCE_AT_COMMIT,
                read_at="2026-08-07T00:00:00Z",
                vocabularies=(
                    ImageVocabulary(
                        kind=VocabularyName.TOKENIZERS,
                        read_from=".edullm/train_on_corpus.py",
                        names=("tokenizer/dolma2-bpe",),
                    ),
                ),
            ),
        ),
    )

    require_a_model_factory_the_image_has(
        command=shell(f"python train.py --model-factory {NOT_A_FACTORY}"),
        repository="OLMo-core",
        images=partly_read,
    )


def test_the_waiver_lets_through_the_researcher_whose_branch_adds_a_factory() -> None:
    """Mutation: no waiver, on the argument that precision.py has none.

    That module says why it has none, and the reason does not transfer: a bfloat16 run on Turing
    does not work however sure the submitter is, and a run naming a factory their own commit
    adds works fine. This is the case ``EDULLM_LAUNCH_CHECK=waived`` exists for -- the waived run
    still works and the platform is only declining to assert something about it.
    """
    allow(
        shell(
            f"{MODEL_FACTORY_CHECK_WAIVER} python train.py --model-factory {NOT_A_FACTORY}"
        )
    )


def test_the_waiver_puts_a_sentence_in_front_of_the_lead_who_releases_the_run() -> None:
    """Mutation: waive silently.

    The command is not on the approver page, so a waiver nobody surfaces is a submitter's
    assertion that the approver never sees. ``waived_launch_check_note`` sets this pattern,
    including the part where a note is returned only when the waiver did something.
    """
    waived = shell(f"{MODEL_FACTORY_CHECK_WAIVER} python train.py --model-factory {NOT_A_FACTORY}")
    note = waived_model_factory_note(
        command=waived, repository=READ_REPOSITORY, images=RECORD
    )
    assert note is not None
    assert NOT_A_FACTORY in note
    assert "exits 70" in note

    carried_for_nothing = shell(
        f"{MODEL_FACTORY_CHECK_WAIVER} python train.py --model-factory {A_REAL_FACTORY}"
    )
    assert (
        waived_model_factory_note(
            command=carried_for_nothing, repository=READ_REPOSITORY, images=RECORD
        )
        is None
    )


def test_the_refusal_is_the_kind_every_other_command_rule_raises() -> None:
    """Mutation: raise something of its own.

    ``preflight._check_command`` and ``compile_submission`` both catch
    ``SubmissionRefusedError`` and read ``type(exc).reason_code`` off it. A rule raising anything
    else escapes both, which surfaces as a traceback in the compile job rather than as a
    refusal a submitter can read.
    """
    with pytest.raises(SubmissionRefusedError):
        require_a_model_factory_the_image_has(
            command=shell(f"python train.py --model-factory {NOT_A_FACTORY}"),
            repository=READ_REPOSITORY,
            images=RECORD,
        )
    assert ModelFactoryNotInTheImageError.reason_code == "model_factory_not_in_the_image"
