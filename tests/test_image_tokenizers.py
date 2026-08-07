"""The record of what each published image holds, and what keeps it honest.

**THIS IS THE FILE THAT GOES RED WHEN THE TWO MAPS DISAGREE, WHICH IS WHAT NOTHING DID.**
``edullm_platform.tokenizers.TOKENIZERS`` is what this platform knows how to express and
``config/image-tokenizers.yaml`` is what an image was measured to hold. The runnability
verdict is a join over both, so a disagreement between them is either a corpus offered that
cannot run or a corpus withheld that can. Until this record existed there was one map, the
verdict read it as though it answered both questions, and the disagreement was invisible
until a container exited 69 -- which ``run_019fdd88-3ac4`` did, on a GPU, after spending an
approval.

The ordering ``tokenizers.py`` sets out in capitals -- the OLMo-core change lands first and
the platform's ships after -- is now enforced by the data rather than asserted in prose, and
what this module adds is the other direction: an image carrying a tokenizer this platform
cannot express is equally broken, and nothing would have noticed that either.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from edullm_platform.config import load_yaml
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.image_tokenizers import (
    ImageTokenizerReading,
    ImageTokenizerRecord,
    ReadingMethod,
)
from edullm_platform.contracts.repository_registry import RepositoryRegistry
from edullm_platform.reviewed_configuration import ConfigFile
from edullm_platform.tokenizers import TOKENIZERS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT_ROOT / "config"

A_DIGEST = "sha256:" + "a" * 64
A_COMMIT = "0" * 40


@pytest.fixture(scope="module")
def record() -> ImageTokenizerRecord:
    return load_yaml(CONFIG / ConfigFile.IMAGE_TOKENIZERS.value, ImageTokenizerRecord)


def test_every_recorded_image_is_a_repository_this_platform_registers(
    record: ImageTokenizerRecord,
) -> None:
    """Mutation: record a reading against a name nothing publishes.

    A reading keyed on a repository that is not registered describes an image no submission
    can name, and its tokenizers would widen what the verb says is runnable on the strength
    of an image nobody can run. A typo in the key does the same thing in the other direction:
    the real image's reading stops being found and its corpora quietly stop being offered.
    """
    registered = {
        entry.repository
        for entry in load_yaml(CONFIG / "repositories.yaml", RepositoryRegistry).repositories
    }
    recorded = {reading.repository for reading in record.images}

    assert recorded <= registered, (
        f"{sorted(recorded - registered)} have a recorded image reading and are not in "
        "config/repositories.yaml, so the reading describes an image no submission can name"
    )
    assert recorded, "no image is recorded, so every corpus reads as unrunnable"


def test_every_tokenizer_an_image_carries_is_one_this_platform_can_build_a_config_for(
    record: ImageTokenizerRecord,
) -> None:
    """**THE DIRECTION NOBODY WAS WATCHING.** Mutation: record a tokenizer that is not in
    ``TOKENIZERS``.

    The failure everybody has in mind is the platform running ahead of the image, which is
    what happened and what the verdict now reads this record to prevent. The reverse is just
    as broken and quieter. ``batch_submit_request`` sends the corpus's tokenizer id and the
    container looks it up in its own map; what turns that id into a config on this side is
    ``TOKENIZERS``, and a tokenizer an image carries that this platform cannot express is a
    corpus that would be offered on the strength of the image and refused by the platform's
    own submission path.

    So the record may never lead the platform's map either. Both maps have to hold a
    tokenizer before a corpus depending on it is offered, and this is the half of that which
    the verdict itself cannot check -- the verdict only ever asks whether both are true.
    """
    for reading in record.images:
        unexpressible = sorted(set(reading.tokenizers) - set(TOKENIZERS))
        assert not unexpressible, (
            f"the {reading.repository} image carries {unexpressible}, which "
            "edullm_platform.tokenizers cannot build a config for. A corpus on one of those "
            "would be offered because the image can train it and refused because this "
            "platform cannot describe it to the image. Add the entry there, in the same "
            "commit."
        )


def test_every_recorded_tokenizer_is_a_published_dataset_the_registry_carries(
    record: ImageTokenizerRecord,
) -> None:
    """Mutation: record ``tokenizer/smollm2`` for ``tokenizer/smollm2-bpe``.

    The ids here are joined against the ``tokenizer`` a corpus declares in
    ``config/datasets.yaml``, so a near-miss spelling matches nothing and silently takes
    every corpus on that tokenizer off the form. Nothing else would notice: the verdict would
    read ``exits_69``, which is a sentence about a real state, and it would be false.
    """
    published = {entry.dataset_id for entry in load_yaml(
        CONFIG / "datasets.yaml", DatasetRegistry
    ).published}

    for reading in record.images:
        unknown = sorted(set(reading.tokenizers) - published)
        assert not unknown, (
            f"the {reading.repository} reading names {unknown}, which config/datasets.yaml "
            "does not carry as a published dataset. A corpus joins to this record on the "
            "exact id it declares, so a spelling nothing registers withholds every corpus on "
            "that tokenizer and says they exit 69"
        )


def test_the_two_maps_disagree_only_in_the_direction_that_withholds(
    record: ImageTokenizerRecord,
) -> None:
    """The state of the disagreement today, recorded so that closing it is deliberate.

    Mutation: none -- this is a tripwire rather than a rule, and it is here because the two
    sets being equal is the end state and being unequal is a decision somebody should have to
    look at. The gap costs nothing dangerous while it is in this direction: a tokenizer this
    platform can express and no image carries withholds corpora, which under-promises.

    It is asserted as the exact set rather than as a property, because "the gap has changed"
    is the thing worth a reader's attention. It shrinks when the matching lines land in
    OLMo-core and the image is read again, which is the work this leaves open.
    """
    expressible = frozenset(TOKENIZERS)
    carried = record.tokenizers_some_image_carries()

    assert carried <= expressible
    assert sorted(expressible - carried) == [
        "tokenizer/qwen25-vendored",
        "tokenizer/smollm2-bpe",
    ], (
        "the set of tokenizers this platform can express and no published image carries has "
        "moved. If it shrank, an image was read carrying one and the corpora on it are "
        "offered again -- check that config/datasets.yaml and the submission form agree. If "
        "it grew, an entry was added to edullm_platform.tokenizers ahead of the image again, "
        "which is the ordering that module's header refuses and the defect this record was "
        "built for."
    )


def test_a_reading_from_source_may_not_claim_to_have_opened_an_image() -> None:
    """Mutation: let a source reading carry a digest, since one is easy to look up.

    The digest is what makes a reading a statement about a particular image. Putting one
    beside a reading taken off a checkout claims the strong form while holding the weak one,
    and the difference is exactly what ``verify_image_accelerator.py`` exists for: a ``COPY``
    that misses the file, an entrypoint pinning an older checkout, or a cache hit reusing a
    layer all leave an image whose map is not the map in the tree.
    """
    with pytest.raises(ValidationError):
        ImageTokenizerReading(
            repository="OLMo-core",
            commit_sha=A_COMMIT,
            read_by=ReadingMethod.SOURCE_AT_COMMIT,
            image_digest=A_DIGEST,
            read_from=".edullm/train_on_corpus.py",
            read_at="2026-08-07T00:00:00Z",
            tokenizers=(),
        )
    with pytest.raises(ValidationError):
        ImageTokenizerReading(
            repository="OLMo-core",
            commit_sha=A_COMMIT,
            read_by=ReadingMethod.IMAGE_PROBE,
            read_from=".edullm/train_on_corpus.py",
            read_at="2026-08-07T00:00:00Z",
            tokenizers=(),
        )


def test_a_record_that_names_one_image_twice_is_refused() -> None:
    """Mutation: take the first match, or the last.

    Two readings for one repository is two answers to "what does this image hold", and the
    verdict would take whichever the lookup reached first. Both spellings of that are a
    silent choice between a stale reading and a current one.
    """
    reading = ImageTokenizerReading(
        repository="OLMo-core",
        commit_sha=A_COMMIT,
        read_by=ReadingMethod.SOURCE_AT_COMMIT,
        read_from=".edullm/train_on_corpus.py",
        read_at="2026-08-07T00:00:00Z",
        tokenizers=("tokenizer/dolma2-bpe",),
    )

    with pytest.raises(ValidationError):
        ImageTokenizerRecord(schema_version=1, images=(reading, reading))


def test_an_unsorted_or_repeated_tokenizer_list_is_refused() -> None:
    """Mutation: accept whatever order the probe emitted.

    The tool sorts, so an unsorted list in the file is a hand edit -- which is the thing the
    header asks nobody to make. It also keeps a re-reading to a diff of what moved rather
    than a reshuffle nobody can review.
    """
    for tokenizers in (
        ("tokenizer/gigatoken-bpe", "tokenizer/dolma2-bpe"),
        ("tokenizer/dolma2-bpe", "tokenizer/dolma2-bpe"),
        ("dolma2-bpe",),
    ):
        with pytest.raises(ValidationError):
            ImageTokenizerReading(
                repository="OLMo-core",
                commit_sha=A_COMMIT,
                read_by=ReadingMethod.SOURCE_AT_COMMIT,
                read_from=".edullm/train_on_corpus.py",
                read_at="2026-08-07T00:00:00Z",
                tokenizers=tokenizers,
            )


def test_the_committed_record_is_what_the_probe_would_write(record: ImageTokenizerRecord) -> None:
    """Mutation: hand-edit an entry, which is what the header asks nobody to do.

    Held on the round trip rather than on the bytes, because the file carries a header of
    prose the tool cannot regenerate and should not. What this catches is an entry whose
    shape has drifted from what ``tools/probe_image_tokenizers.py`` produces -- a field
    dropped, a digest added by hand, an order reversed -- any of which means the next
    re-reading produces a diff nobody can review.
    """
    from tools.probe_image_tokenizers import as_document

    reloaded = ImageTokenizerRecord.model_validate(
        yaml.safe_load(as_document(record))
    )

    assert reloaded == record
