"""Which tokenizers a published training image can actually build, as a measurement.

**THIS EXISTS BECAUSE THE VERDICT WAS DERIVED FROM SOMETHING THAT DOES NOT KNOW WHAT AN
IMAGE CONTAINS.** ``edullm data`` answers one column a chooser cannot get anywhere else --
whether the corpus they pick will start -- and it answered it out of
:data:`~edullm_platform.tokenizers.TOKENIZERS`, which is this platform's map of published
tokenizer ids to the OLMo-core expression that reproduces each one. That map says what this
platform knows how to *express*. What decides whether a run starts is what the image knows
how to *build*, and those are two facts. While they happened to agree the verdict was
right; the moment they did not, the verb marked three corpora runnable that no image can
train, and ``run_019fdd88-3ac4`` was admitted, allocated a GPU and exited 69 with
:data:`~edullm_platform.tokenizers.THE_CONTAINERS_REFUSAL` -- precisely the outcome the
verdict field exists to prevent.

**THE ORDERING WAS WRITTEN DOWN AND WRITING IT DOWN WAS NOT ENOUGH.** ``tokenizers.py``
carries the rule in capitals: a key added there before the matching line lands in OLMo-core
offers a corpus every image refuses, and "the ordering is not optional: the OLMo-core change
lands first and this ships after". Two keys were added ahead of the image anyway. A rule
that lives only in a docstring is a rule somebody has to have read, and the person adding a
tokenizer entry is usually the person who has just proved to themselves that the tokenizer
works. So the ordering stops being a convention here and becomes the shape of the data: the
verdict reads a record of the image, and a tokenizer this platform can express is offered
only once an image has been seen carrying it.

**WHY A RECORD AND NOT AN INTROSPECTION, WHICH WOULD BE BETTER.** The truth lives inside the
image, in the research repository's own ``.edullm/train_on_corpus.py``. Nothing on the read
side can reach it. ``edullm`` holds no AWS credential by design, a laptop has no Docker
daemon and no copy of another repository, and a ``GITHUB_TOKEN`` scoped to ``edu-llm/platform``
is refused by every other repository's API -- the same wall ``build-research-image.yml``
records for the publisher role variable. Reading the map at verdict time is therefore not
merely expensive, it is not available on any machine that runs the verb.

What is available is a probe at the one moment the image exists and something trusted is
holding it: on the build runner, after the last layer. ``tools/verify_image_accelerator.py``
already does exactly that for a different fact about the image and sets the pattern down to
the sentinel. ``tools/probe_image_tokenizers.py`` runs the same shape and writes what it
read into ``config/image-tokenizers.yaml``, so what reaches a laptop is a measurement
somebody took rather than a claim somebody typed.

**A MEASUREMENT, SO IT CARRIES ITS PROVENANCE AND ITS DATE.** Each record names the image it
was read from by commit and digest, when it was read, and what read it. Without those a
reader has no way to tell a record taken off a published image from a list somebody
maintained by hand, which is the state this replaces.

**UNDER ``config/`` AND NOT ``config/reports/``, WHICH IS THE OPPOSITE FILING TO
``corpora.json`` AND IS THE SAME ARGUMENT.** That file's module header says a re-measurement
must not cut a release, and gives the reason: nothing in it can lose anybody a machine, so
an install reading a week-old token count is fine. It then names the exception in as many
words -- "the field that *could* lose somebody a machine is the runnability verdict, and
that one is not in this file at all." This is that field's input. An install answering out
of a stale copy of it is an install that says a corpus runs when the image it would reach
cannot train it, so this belongs on the release trigger with the six rules.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, model_validator

from .base import (
    ContractModel,
    Sha256Digest,
    UtcTimestamp,
    parse_str_enum,
    require_ordered_sequence,
)
from .manifest import COMMIT_SHA_PATTERN

__all__ = [
    "ImageTokenizerReading",
    "ImageTokenizerRecord",
    "ReadingMethod",
]


class ReadingMethod(StrEnum):
    """How a reading was taken, because the two are not equally strong.

    ``tools/verify_image_accelerator.py`` makes this argument at length for a different fact
    and every word of it applies here: an assertion about a repository's source protects
    exactly what that source produces, and an image is not that. A ``COPY`` that misses the
    file, a Dockerfile whose entrypoint pins an older checkout, a BuildKit cache hit that
    reuses the layer without re-running the step -- each leaves an image whose map is not the
    map in the tree. Only a probe of the assembled image rules those out.

    Both are recorded rather than only the strong one, because the weak one is available
    today and refusing to write it down would leave the verdict deriving from a hand-kept
    list, which is worse than deriving from a source reading that says it is one.
    """

    #: Read out of the assembled image, which is the answer that cannot be wrong about what
    #: an image contains. Carries the digest of the image that was opened.
    IMAGE_PROBE = "image_probe"
    #: Read out of the research repository's source at a named commit. Establishes what the
    #: image built from that commit *should* hold, and nothing about any particular build.
    SOURCE_AT_COMMIT = "source_at_commit"


ReadingMethodValue = Annotated[ReadingMethod, BeforeValidator(parse_str_enum(ReadingMethod))]


class ImageTokenizerReading(ContractModel):
    """One published image, and the tokenizer ids it was seen to hold.

    ``tokenizers`` may be empty and an empty list is a finding rather than a gap. Four of
    the six registered repositories publish an image that trains no corpus at all -- a
    dataset validator, an evaluation image that calls a hosted API -- and a reading that
    found no map in one of those is not a reading that failed. What it means for the verdict
    is the same as a map without the key in question: no run on this image can train a
    corpus needing one.
    """

    #: The registry key of the research repository whose image this is, as
    #: ``config/repositories.yaml`` spells it. Held to that file by
    #: ``tests/test_image_tokenizers.py``, so a record cannot describe an image nothing
    #: publishes.
    repository: str = Field(min_length=1, pattern=r".*\S.*")
    #: The commit the image was built from, which is the thing a reader compares against
    #: the commit their submission names. A digest identifies the image and says nothing a
    #: person can act on; a commit is what they can go and read.
    commit_sha: str = Field(pattern=COMMIT_SHA_PATTERN)
    read_by: ReadingMethodValue
    #: The image that was opened, and ``None`` for a reading taken off the source. Required
    #: for a probe and refused for a source reading by the validator below: a digest beside
    #: a reading that never opened an image would claim the strong form of this record while
    #: holding the weak one.
    image_digest: Sha256Digest | None = None
    #: The file the map was read out of, so a reader who wants to check this by hand knows
    #: where to look and a probe that read the wrong thing is visible in review.
    read_from: str = Field(min_length=1, max_length=256)
    read_at: UtcTimestamp
    #: The dataset ids of the published tokenizers the image can build a config for, exactly
    #: as the corpus's own ``depends_on`` entry spells them. Sorted and unique, because two
    #: orderings of one reading would produce two diffs for one fact.
    tokenizers: Annotated[
        tuple[str, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(default=(), strict=False)

    @model_validator(mode="after")
    def validate_a_digest_is_present_exactly_when_an_image_was_opened(self) -> Self:
        opened = self.read_by is ReadingMethod.IMAGE_PROBE
        if opened and self.image_digest is None:
            raise ValueError(
                "a reading taken from an image must name the digest it was taken from"
            )
        if not opened and self.image_digest is not None:
            raise ValueError(
                "a reading taken from source must not name an image digest; it establishes "
                "what an image built from that commit should hold and nothing about a build"
            )
        return self

    @model_validator(mode="after")
    def validate_tokenizers_are_distinct_and_sorted(self) -> Self:
        if len(set(self.tokenizers)) != len(self.tokenizers):
            raise ValueError("an image must not be recorded as carrying a tokenizer twice")
        if list(self.tokenizers) != sorted(self.tokenizers):
            raise ValueError("recorded tokenizers must be sorted, so a re-reading diffs cleanly")
        for tokenizer in self.tokenizers:
            if not tokenizer.startswith("tokenizer/"):
                raise ValueError(
                    f"{tokenizer!r} is not a published tokenizer id; the record holds the "
                    "dataset ids a corpus names in its own depends_on entry"
                )
        return self

    def carries(self, tokenizer: str | None) -> bool:
        """Whether a corpus declaring this tokenizer could be built by this image.

        ``None`` is false and is not a special case. A corpus that declares no tokenizer
        holds pre-tokenization text, and there is no vocabulary for the offered path to
        build a model over -- which is the same answer, reached honestly, as a tokenizer no
        image carries.
        """
        return tokenizer is not None and tokenizer in self.tokenizers


class ImageTokenizerRecord(ContractModel):
    """What every published training image was last seen able to build.

    One record per repository rather than per digest, and that is a deliberate loss of
    precision worth naming. A per-digest record would be exact and would also go stale on
    every rebuild, so the verdict would answer "no reading for this image" for the ordinary
    case of somebody publishing a commit this morning. What a chooser is asking is whether
    the corpus is trainable at all, and the tokenizer map moves when somebody edits it
    rather than when somebody rebuilds -- so the useful unit is the repository, with the
    commit recorded so a reader can see how far behind the reading is.
    """

    schema_version: Literal[1]
    images: Annotated[
        tuple[ImageTokenizerReading, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)

    @model_validator(mode="after")
    def validate_one_reading_per_repository(self) -> Self:
        repositories = [reading.repository for reading in self.images]
        if len(set(repositories)) != len(repositories):
            raise ValueError("a repository must not carry more than one recorded reading")
        return self

    def images_carrying(self, tokenizer: str | None) -> tuple[str, ...]:
        """Which published images can build this tokenizer, so a refusal can name them.

        A tuple rather than a boolean for the reason
        :func:`~edullm_platform.contracts.image_scan.unreviewed_blocking_findings` is a list
        rather than a count: a decision wants the emptiness and a message wants the names.
        "No image carries it" is a sentence somebody can act on only once they know which
        images were asked.
        """
        return tuple(
            reading.repository for reading in self.images if reading.carries(tokenizer)
        )

    def tokenizers_some_image_carries(self) -> frozenset[str]:
        """Every tokenizer any published image can build, which is what decides the verdict.

        The union rather than the intersection. A corpus is trainable if there is an image
        that can train it, and asking for agreement across every registered repository would
        refuse every corpus the moment a dataset validator with no map was recorded.
        """
        return frozenset(
            tokenizer for reading in self.images for tokenizer in reading.tokenizers
        )

    def reading_for(self, repository: str) -> ImageTokenizerReading | None:
        for reading in self.images:
            if reading.repository == repository:
                return reading
        return None
