"""The record of what each published image contains, and what keeps it honest.

**THIS IS THE FILE THAT GOES RED WHEN A PLATFORM-SIDE FILE AND A READING OF AN IMAGE
DISAGREE, WHICH IS WHAT NOTHING DID.** Two of the platform's claims about a run turned out to
be answered from files that cannot know what an image holds, and both failed the same way:

``edullm_platform.tokenizers.TOKENIZERS`` is what this platform knows how to *express* and the
``tokenizers`` vocabulary in ``config/image-contents.yaml`` is what an image was measured to
*build*. The runnability verdict is a join over both, so a disagreement is either a corpus
offered that cannot run or a corpus withheld that can. Until the record existed there was one
map, the verdict read it as though it answered both questions, and the disagreement was
invisible until a container exited 69 -- which ``run_019fdd88-3ac4`` did, on a GPU, after
spending an approval.

``guides/olmo-core.md`` tells a researcher to write ``--model-factory olmo2_1B`` and nothing
checked that any image has that factory. It does, which is luck; the same sentence naming a
factory that had been renamed would be exit 70 on the same path, at the same cost.

So every check below runs in *both directions*, and the ones that can be written once for every
vocabulary are. A name the platform offers and no image holds is a promise nothing can keep. A
name an image holds and the platform cannot express or does not offer is quieter and just as
broken. Neither had anything watching it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from edullm_platform.config import load_yaml
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.image_contents import (
    NAME_PATTERNS,
    ImageContentsReading,
    ImageContentsRecord,
    ImageVocabulary,
    ReadingMethod,
    VocabularyName,
)
from edullm_platform.contracts.repository_registry import RepositoryRegistry
from edullm_platform.model_factory import model_factory_request_in
from edullm_platform.reviewed_configuration import ConfigFile
from edullm_platform.tokenizers import TOKENIZERS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT_ROOT / "config"

A_DIGEST = "sha256:" + "a" * 64
A_COMMIT = "0" * 40


def a_reading(**overrides: object) -> ImageContentsReading:
    """A valid reading, so each case below writes only the field it is about."""
    fields: dict[str, object] = {
        "repository": "OLMo-core",
        "commit_sha": A_COMMIT,
        "read_by": ReadingMethod.SOURCE_AT_COMMIT,
        "read_at": "2026-08-07T00:00:00Z",
        "vocabularies": (
            ImageVocabulary(
                kind=VocabularyName.TOKENIZERS,
                read_from=".edullm/train_on_corpus.py",
                names=("tokenizer/dolma2-bpe",),
            ),
        ),
    }
    fields.update(overrides)
    return ImageContentsReading(**fields)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def record() -> ImageContentsRecord:
    return load_yaml(CONFIG / ConfigFile.IMAGE_CONTENTS.value, ImageContentsRecord)


# ---------------------------------------------------------------------------------------
# The record against the files that decide what exists
# ---------------------------------------------------------------------------------------


def test_every_recorded_image_is_a_repository_this_platform_registers(
    record: ImageContentsRecord,
) -> None:
    """Mutation: record a reading against a name nothing publishes.

    A reading keyed on an unregistered repository describes an image no submission can name,
    and its tokenizers would widen what the verb says is runnable on the strength of an image
    nobody can run. A typo does the same thing in the other direction: the real image's reading
    stops being found, its corpora quietly stop being offered, and every factory check on it
    silently stops applying.
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
    record: ImageContentsRecord,
) -> None:
    """**THE DIRECTION NOBODY WAS WATCHING.** Mutation: record a tokenizer not in ``TOKENIZERS``.

    The failure everybody has in mind is the platform running ahead of the image, which is what
    happened and what the verdict now reads this record to prevent. The reverse is just as
    broken and quieter. ``batch_submit_request`` sends the corpus's tokenizer id and the
    container looks it up in its own map; what turns that id into a config on this side is
    ``TOKENIZERS``, so a tokenizer an image carries that this platform cannot express is a
    corpus offered on the strength of the image and refused by the platform's own submission
    path.
    """
    for reading in record.images:
        carried = reading.names(VocabularyName.TOKENIZERS) or ()
        unexpressible = sorted(set(carried) - set(TOKENIZERS))
        assert not unexpressible, (
            f"the {reading.repository} image carries {unexpressible}, which "
            "edullm_platform.tokenizers cannot build a config for. A corpus on one of those "
            "would be offered because the image can train it and refused because this platform "
            "cannot describe it to the image. Add the entry there, in the same commit."
        )


def test_every_recorded_tokenizer_is_a_published_dataset_the_registry_carries(
    record: ImageContentsRecord,
) -> None:
    """Mutation: record ``tokenizer/smollm2`` for ``tokenizer/smollm2-bpe``.

    The ids here are joined against the ``tokenizer`` a corpus declares in
    ``config/datasets.yaml``, so a near-miss spelling matches nothing and silently takes every
    corpus on that tokenizer off the form. Nothing else would notice: the verdict would read
    ``exits_69``, which is a sentence about a real state, and it would be false.
    """
    published = {
        entry.dataset_id
        for entry in load_yaml(CONFIG / "datasets.yaml", DatasetRegistry).published
    }

    for reading in record.images:
        carried = reading.names(VocabularyName.TOKENIZERS) or ()
        unknown = sorted(set(carried) - published)
        assert not unknown, (
            f"the {reading.repository} reading names {unknown}, which config/datasets.yaml does "
            "not carry as a published dataset. A corpus joins to this record on the exact id it "
            "declares, so a spelling nothing registers withholds every corpus on that tokenizer "
            "and says they exit 69"
        )


def test_the_two_tokenizer_maps_disagree_only_in_the_direction_that_withholds(
    record: ImageContentsRecord,
) -> None:
    """The state of the disagreement today, recorded so that closing it is deliberate.

    Mutation: none -- this is a tripwire rather than a rule, and it is here because the two sets
    being equal is the end state and being unequal is a decision somebody should have to look
    at. The gap costs nothing dangerous while it is in this direction: a tokenizer this platform
    can express and no image carries withholds corpora, which under-promises.

    Asserted as the exact set rather than as a property, because "the gap has changed" is the
    thing worth a reader's attention. It shrinks when the matching lines land in OLMo-core and
    the image is read again, which is the work this leaves open.
    """
    expressible = frozenset(TOKENIZERS)
    carried = record.names_some_image_carries(VocabularyName.TOKENIZERS)

    assert carried <= expressible
    assert sorted(expressible - carried) == [
        "tokenizer/qwen25-vendored",
        "tokenizer/smollm2-bpe",
    ], (
        "the set of tokenizers this platform can express and no published image carries has "
        "moved. If it shrank, an image was read carrying one and the corpora on it are offered "
        "again -- check that config/datasets.yaml and the submission form agree. If it grew, an "
        "entry was added to edullm_platform.tokenizers ahead of the image again, which is the "
        "ordering that module's header refuses and the defect this record was built for."
    )


# ---------------------------------------------------------------------------------------
# The model factories, which is the same rule arriving at a different reader
# ---------------------------------------------------------------------------------------

#: Anything the guides write between backticks. Read out of the prose rather than out of fenced
#: blocks alone, because the one place a factory name is offered today is a table cell, and a
#: check that looked only where the last instance happened to live is the shape of gap this
#: whole change is about.
_QUOTED = re.compile(r"`([^`\n]+)`")


def factories_offered_in(text: str) -> set[str]:
    """Every factory name a document tells a reader to write.

    Found by running the guard's own detector over each quoted span rather than by matching a
    word, which is ``test_agent_layer``'s rule for the bfloat16 line and is the point: a
    narrowed detector fails here instead of leaving a worked example that quietly stopped being
    checked.
    """
    found = set()
    for quoted in _QUOTED.findall(text):
        request = model_factory_request_in(("bash", "-lc", quoted))
        if request is not None:
            found.add(request)
    return found


def test_every_factory_the_guides_offer_is_one_a_published_image_was_seen_to_have(
    record: ImageContentsRecord,
) -> None:
    """**THE EXIT 70 THE PLATFORM WAS ASSERTING BY LUCK.** Mutation: rename a factory upstream.

    ``guides/olmo-core.md`` writes ``--model-factory olmo2_1B`` into a line a researcher copies,
    and until this test nothing anywhere connected that name to a reading of any image. It is a
    real name today, which is why nobody has been hurt; the container resolves it with
    ``getattr(TransformerConfig, ...)`` and exits 70 on a miss, after the run has been priced,
    released by a lead, admitted and given a machine.

    This is the same shape as the tokenizer join and is checked in the same two directions. A
    guide offering a name no image holds is a documented way to burn an allocation. The reverse
    -- an image holding factories no guide mentions -- is not an error and is not asserted: 63
    factories exist and a guide naming three of them is a guide, not a gap.
    """
    guides = sorted((PROJECT_ROOT / "guides").glob("*.md"))
    assert guides, "no guides to check, which means this test stopped covering anything"

    known = record.names_some_image_carries(VocabularyName.MODEL_FACTORIES)
    assert known, (
        "no published image has a recorded reading of its model factories, so the submission "
        "gate is silent and this check covers nothing. Run tools/probe_image_contents.py."
    )

    offered: dict[str, str] = {}
    for guide in guides:
        for name in factories_offered_in(guide.read_text(encoding="utf-8")):
            offered.setdefault(name, guide.name)
    assert offered, (
        "no guide offers a model factory any more. If the flag stopped being documented this "
        "test is dead weight; if the detector stopped finding it, every guide is now unchecked."
    )

    missing = sorted(name for name in offered if name not in known)
    assert not missing, (
        f"{missing} are written into {sorted({offered[name] for name in missing})} and no "
        "published image was seen to have them. A researcher copying that line spends an "
        "approval and a GPU allocation to reach exit 70. Either the guide names a factory that "
        "has been renamed upstream, or the reading in config/image-contents.yaml predates the "
        "commit that added it -- re-run tools/probe_image_contents.py before editing the guide."
    )


def test_the_factory_the_trainer_defaults_to_is_one_the_image_has(
    record: ImageContentsRecord,
) -> None:
    """Mutation: none. A tripwire on the name a command that says nothing gets.

    ``require_a_model_factory_the_image_has`` reads the command and deliberately does not read
    the trainer's argparse default, because a second copy of somebody else's default is a fact
    that moves without telling anybody. That leaves one name reachable by writing no flag at
    all, and it is worth knowing it is real: a run naming no factory is the most ordinary run
    there is, and the guard is silent about it by design.
    """
    known = record.names_some_image_carries(VocabularyName.MODEL_FACTORIES)
    assert "olmo2_190M" in known, (
        "olmo2_190M is what .edullm/train_on_corpus.py falls back to when a command names no "
        "--model-factory, and no image was seen to have it. Every run that does not name one "
        "would exit 70, and the guard reads the command rather than the default so it would "
        "refuse none of them."
    )


# ---------------------------------------------------------------------------------------
# The shape of a reading, which is what stops a hand edit reading as a measurement
# ---------------------------------------------------------------------------------------


def test_every_vocabulary_this_record_can_hold_has_a_spelling_rule() -> None:
    """Mutation: add a third ``VocabularyName`` and leave :data:`NAME_PATTERNS` alone.

    The pattern is what refuses a reader that read the wrong file -- a probe pointed at the
    wrong class writing method names into the tokenizer vocabulary, say. A member with no
    pattern would raise ``KeyError`` inside a validator, which surfaces as an unreadable
    configuration file rather than as the missing decision it is.
    """
    assert set(NAME_PATTERNS) == set(VocabularyName), (
        f"{sorted(set(VocabularyName) - set(NAME_PATTERNS))} can be recorded and has no rule "
        "for how its names are spelled. Decide it in NAME_PATTERNS in the same commit that adds "
        "the member."
    )


def test_a_reading_from_source_may_not_claim_to_have_opened_an_image() -> None:
    """Mutation: let a source reading carry a digest, since one is easy to look up.

    The digest is what makes a reading a statement about a particular image. Putting one beside
    a reading taken off a checkout claims the strong form while holding the weak one, and the
    difference is exactly what ``verify_image_accelerator.py`` exists for: a ``COPY`` that
    misses the file, an entrypoint pinning an older checkout, or a cache hit reusing a layer all
    leave an image whose contents are not the tree's.
    """
    with pytest.raises(ValidationError):
        a_reading(read_by=ReadingMethod.SOURCE_AT_COMMIT, image_digest=A_DIGEST)
    with pytest.raises(ValidationError):
        a_reading(read_by=ReadingMethod.IMAGE_PROBE)


def test_a_reading_that_established_nothing_is_refused() -> None:
    """Mutation: allow an empty ``vocabularies``, since a probe might find nothing.

    A repository present in the record with no vocabulary says "asked and answered" while
    holding no answer, which is precisely the ambiguity between absent and empty that the two
    callers read in opposite directions. An image the probe found nothing in needs no entry.
    """
    with pytest.raises(ValidationError):
        a_reading(vocabularies=())


def test_a_record_that_names_one_image_twice_is_refused() -> None:
    """Mutation: take the first match, or the last.

    Two readings for one repository is two answers to "what does this image hold", and a lookup
    would take whichever it reached first. Both spellings of that are a silent choice between a
    stale reading and a current one.
    """
    reading = a_reading()
    with pytest.raises(ValidationError):
        ImageContentsRecord(schema_version=1, images=(reading, reading))


def test_one_reading_may_not_record_a_vocabulary_twice_or_out_of_order() -> None:
    """Mutation: append each vocabulary as the probe reads it.

    The same argument as the sorted name lists one step up. Two entries of one kind is two
    answers, and :meth:`ImageContentsReading.names` would return whichever came first; an
    unsorted list turns a re-reading of one vocabulary into a diff that reshuffles the block.
    """
    tokenizers = ImageVocabulary(
        kind=VocabularyName.TOKENIZERS, read_from="a.py", names=("tokenizer/dolma2-bpe",)
    )
    factories = ImageVocabulary(
        kind=VocabularyName.MODEL_FACTORIES, read_from="b.py", names=("olmo2_1B",)
    )
    with pytest.raises(ValidationError):
        a_reading(vocabularies=(tokenizers, tokenizers))
    with pytest.raises(ValidationError):
        a_reading(vocabularies=(tokenizers, factories))

    a_reading(vocabularies=(factories, tokenizers))


def test_an_unsorted_repeated_or_misspelled_name_is_refused() -> None:
    """Mutation: accept whatever order the probe emitted, and whatever it read.

    The tool sorts, so an unsorted list in the file is a hand edit -- the thing the header asks
    nobody to make -- and sorting keeps a re-reading to a diff of what moved. The spelling rule
    is the other half: a probe pointed at the wrong file would write plausible strings, and a
    tokenizer id that is not one joins to no corpus while looking like it should.
    """
    for names in (
        ("tokenizer/gigatoken-bpe", "tokenizer/dolma2-bpe"),
        ("tokenizer/dolma2-bpe", "tokenizer/dolma2-bpe"),
        ("dolma2-bpe",),
    ):
        with pytest.raises(ValidationError):
            ImageVocabulary(
                kind=VocabularyName.TOKENIZERS, read_from="a.py", names=names
            )

    for factories in (("olmo2_7B", "olmo2_1B"), ("olmo2_1B", "olmo2_1B"), ("olmo2 1B",)):
        with pytest.raises(ValidationError):
            ImageVocabulary(
                kind=VocabularyName.MODEL_FACTORIES, read_from="b.py", names=factories
            )


def test_the_committed_record_is_what_the_probe_would_write(record: ImageContentsRecord) -> None:
    """Mutation: hand-edit an entry, which is what the header asks nobody to do.

    Held on the round trip rather than on the bytes, because the file carries a header of prose
    the tool cannot regenerate and should not. What this catches is an entry whose shape has
    drifted from what ``tools/probe_image_contents.py`` produces -- a field dropped, a digest
    added by hand, an order reversed -- any of which means the next re-reading produces a diff
    nobody can review.
    """
    from tools.probe_image_contents import as_document

    reloaded = ImageContentsRecord.model_validate(yaml.safe_load(as_document(record)))

    assert reloaded == record
