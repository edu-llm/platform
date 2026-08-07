"""What a published training image was seen to contain, as a measurement rather than a claim.

**THIS EXISTS BECAUSE TWO SEPARATE THINGS THE PLATFORM SAYS ABOUT A RUN WERE ANSWERED OUT OF
FILES THAT DO NOT KNOW WHAT AN IMAGE CONTAINS.** They failed the same way five days apart, and
the shape is one shape:

``edullm data`` answers a column a chooser cannot get anywhere else -- whether the corpus they
pick will start -- and it answered it out of :data:`~edullm_platform.tokenizers.TOKENIZERS`,
this platform's map of published tokenizer ids to the OLMo-core expression that reproduces each
one. That map says what this platform knows how to *express*. What decides whether a run starts
is what the image knows how to *build*. While the two agreed the verdict was right; the moment
they did not, the verb marked three corpora runnable that no image can train, and
``run_019fdd88-3ac4`` was admitted, allocated a GPU, and exited 69.

``guides/olmo-core.md`` tells a researcher to write ``--model-factory olmo2_1B``, and nothing
checked whether any image has that factory. It does, so nobody has been hurt by it yet. That
is luck and not a check: the container resolves the name with ``getattr(TransformerConfig,
...)`` and exits 70 on a miss, having already been priced, released by a lead, admitted and
given a machine.

**SO THE RULE IS ONE RULE, AND IT IS WHY THIS FILE IS NAMED FOR CONTENTS RATHER THAN FOR
EITHER OF THEM.** A fact about what an image contains cannot be answered by a file describing
what the platform can express, and the only honest source is a reading of the image. The first
instance of that was fixed with a record about tokenizers; the second arrived four hours later.
A second bespoke record would have been the same mistake in a new file, so the record holds
*named vocabularies* and each instance of this class costs one member of
:class:`VocabularyName`, one pattern in :data:`NAME_PATTERNS`, and one reader in
``tools/probe_image_contents.py``.

**ABSENT AND EMPTY ARE DIFFERENT FINDINGS, AND CONFLATING THEM IS THE ONE WAY THIS TURNS INTO A
BLANKET REFUSAL.** A vocabulary nobody has read is not a vocabulary an image lacks.
:meth:`ImageContentsReading.names` returns ``None`` for the first and ``()`` for the second, and
the two callers deliberately treat absence differently, because what each of them costs when it
is wrong is different:

* The corpus verdict in :mod:`edullm_platform.corpora` reads absence as "cannot train it". It
  under-promises: a researcher is told a corpus will not run when it might, and the sentence
  names which images were asked so the gap is visible to the person it affects. Nothing is
  blocked.
* The submission gate in :mod:`edullm_platform.model_factory` reads absence as *no opinion* and
  lets the submission through. Refusing on absence there would stop every run naming any
  factory on any repository nobody has probed, which is work stopped by a file nobody wrote.

**A MEASUREMENT, SO IT CARRIES ITS PROVENANCE AND ITS DATE.** Each reading names the image it
was taken from by commit and digest, when it was taken, and what took it. Without those a reader
cannot tell a reading off a published image from a list somebody maintained by hand, which is
the state this replaces.

**UNDER ``config/`` AND NOT ``config/reports/``, WHICH IS THE OPPOSITE FILING TO ``corpora.json``
AND IS THE SAME ARGUMENT.** That file's header says a re-measurement must not cut a release,
because nothing in it can lose anybody a machine. It then names the exception in as many words:
"the field that *could* lose somebody a machine is the runnability verdict, and that one is not
in this file at all." This is that field's input, and now also a submission refusal's. An
install answering out of a stale copy is an install that says a corpus runs when the image it
would reach cannot train it, so this belongs on the release trigger with the reviewed rules.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Final, Literal, Self

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
    "NAME_PATTERNS",
    "ImageContentsReading",
    "ImageContentsRecord",
    "ImageVocabulary",
    "ReadingMethod",
    "VocabularyName",
]


class ReadingMethod(StrEnum):
    """How a reading was taken, because the two are not equally strong.

    ``tools/verify_image_accelerator.py`` makes this argument at length for a different fact
    about an image and every word of it applies here: an assertion about a repository's source
    protects exactly what that source produces, and an image is not that. A ``COPY`` that
    misses the file, a Dockerfile whose entrypoint pins an older checkout, a BuildKit cache
    hit that reuses the layer without re-running the step -- each leaves an image whose
    contents are not the tree's contents. Only a probe of the assembled image rules those out.

    Both are recorded rather than only the strong one, because the weak one is available today
    and refusing to write it down would leave the verdict deriving from a hand-kept list,
    which is worse than deriving from a source reading that says it is one.
    """

    #: Read out of the assembled image, which is the answer that cannot be wrong about what an
    #: image contains. Carries the digest of the image that was opened.
    IMAGE_PROBE = "image_probe"
    #: Read out of the research repository's source at a named commit. Establishes what an
    #: image built from that commit *should* hold, and nothing about any particular build.
    SOURCE_AT_COMMIT = "source_at_commit"


class VocabularyName(StrEnum):
    """The kinds of name a container resolves at startup and refuses on a miss.

    **THE TEST FOR MEMBERSHIP IS NOT "IS THIS A FACT ABOUT THE IMAGE" BUT "DOES THE PLATFORM
    ASSERT IT".** Plenty is true of an image that nothing here claims, and recording those
    would be a manifest nobody reads. What belongs here is a set of names the image holds,
    which some file on this side offers a researcher, and whose absence is a refusal after the
    machine has been paid for. ``tests/test_image_contents.py`` holds each member to the file
    that offers it, in both directions.

    Two members and both are that: exit 69 is a tokenizer id the image cannot build a config
    for, exit 70 is a factory name the image's ``TransformerConfig`` does not have.
    ``config/accelerators.yaml`` covers the third of the three known cases, exit 73, and stays
    where it is -- it is a reading of the *account's hardware*, not of an image, and folding a
    fact about silicon in here would make this file about two subjects.
    """

    TOKENIZERS = "tokenizers"
    MODEL_FACTORIES = "model_factories"


ReadingMethodValue = Annotated[ReadingMethod, BeforeValidator(parse_str_enum(ReadingMethod))]
VocabularyNameValue = Annotated[VocabularyName, BeforeValidator(parse_str_enum(VocabularyName))]

#: What a name of each kind has to look like, so a reader that read the wrong thing is refused
#: here rather than recorded.
#:
#: A pattern per kind rather than one shared rule, because the two are spelled by different
#: authorities: a tokenizer id is a published dataset id and is prefixed as one, and a factory
#: name is a Python attribute that ``getattr`` will be asked for. Keyed on the enum and checked
#: exhaustively by :meth:`ImageVocabulary.validate_every_name_is_spelled_like_its_kind`, so a
#: third member of :class:`VocabularyName` cannot be added without deciding this for it.
NAME_PATTERNS: Final[dict[VocabularyName, re.Pattern[str]]] = {
    VocabularyName.TOKENIZERS: re.compile(r"tokenizer/[a-z0-9]+(?:-[a-z0-9]+)*"),
    VocabularyName.MODEL_FACTORIES: re.compile(r"[A-Za-z_][A-Za-z0-9_]*"),
}


class ImageVocabulary(ContractModel):
    """One set of names an image was seen to hold, and the file it was read out of.

    ``names`` may be empty and an empty list is a finding rather than a gap: a repository whose
    trainer holds a map with nothing in it has been read, and what that means for a caller is
    the same as a map without the name in question. It is *recorded* emptiness, which is what
    separates it from a vocabulary this reading does not carry at all.
    """

    kind: VocabularyNameValue
    #: The path inside the image the names were read out of, so a reader who wants to check
    #: this by hand knows where to look and a probe that read the wrong file is visible in
    #: review. Per vocabulary rather than per reading, because the two live in different
    #: files: the tokenizer map is the research repository's own, and the factory surface is
    #: OLMo-core's library code.
    read_from: str = Field(min_length=1, max_length=256)
    #: Sorted and unique, because two orderings of one reading would produce two diffs for one
    #: fact and a re-reading should be a no-op in review when nothing moved.
    names: Annotated[tuple[str, ...], BeforeValidator(require_ordered_sequence)] = Field(
        default=(), strict=False
    )

    @model_validator(mode="after")
    def validate_names_are_distinct_and_sorted(self) -> Self:
        if len(set(self.names)) != len(self.names):
            raise ValueError(f"an image must not be recorded as carrying one {self.kind} twice")
        if list(self.names) != sorted(self.names):
            raise ValueError(
                f"recorded {self.kind} must be sorted, so a re-reading diffs cleanly"
            )
        return self

    @model_validator(mode="after")
    def validate_every_name_is_spelled_like_its_kind(self) -> Self:
        pattern = NAME_PATTERNS[self.kind]
        for name in self.names:
            if not pattern.fullmatch(name):
                raise ValueError(
                    f"{name!r} is not how a {self.kind} is spelled, so nothing that resolves "
                    f"one would find it. Expected {pattern.pattern}"
                )
        return self


class ImageContentsReading(ContractModel):
    """One published image, and every vocabulary it was seen to hold.

    ``vocabularies`` carries at least one entry. A reading that established nothing is not a
    reading, and recording one would put a repository in this file whose presence says "asked
    and answered" while holding no answer -- which is exactly the ambiguity between absent and
    empty that the module header is about.
    """

    #: The registry key of the research repository whose image this is, as
    #: ``config/repositories.yaml`` spells it. Held to that file by
    #: ``tests/test_image_contents.py``, so a record cannot describe an image nothing
    #: publishes.
    repository: str = Field(min_length=1, pattern=r".*\S.*")
    #: The commit the image was built from, which is the thing a reader compares against the
    #: commit their submission names. A digest identifies the image and says nothing a person
    #: can act on; a commit is what they can go and read.
    commit_sha: str = Field(pattern=COMMIT_SHA_PATTERN)
    read_by: ReadingMethodValue
    #: The image that was opened, and ``None`` for a reading taken off the source. Required for
    #: a probe and refused for a source reading by the validator below: a digest beside a
    #: reading that never opened an image would claim the strong form while holding the weak
    #: one.
    image_digest: Sha256Digest | None = None
    read_at: UtcTimestamp
    vocabularies: Annotated[
        tuple[ImageVocabulary, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)

    @model_validator(mode="after")
    def validate_a_digest_is_present_exactly_when_an_image_was_opened(self) -> Self:
        opened = self.read_by is ReadingMethod.IMAGE_PROBE
        if opened and self.image_digest is None:
            raise ValueError("a reading taken from an image must name the digest it was taken from")
        if not opened and self.image_digest is not None:
            raise ValueError(
                "a reading taken from source must not name an image digest; it establishes "
                "what an image built from that commit should hold and nothing about a build"
            )
        return self

    @model_validator(mode="after")
    def validate_one_entry_per_vocabulary_in_a_fixed_order(self) -> Self:
        kinds = [entry.kind for entry in self.vocabularies]
        if len(set(kinds)) != len(kinds):
            raise ValueError("a reading must not record one vocabulary twice")
        if kinds != sorted(kinds):
            raise ValueError(
                "recorded vocabularies must be sorted by kind, so re-reading one of them "
                "diffs as one block rather than as a reshuffle"
            )
        return self

    def names(self, kind: VocabularyName) -> tuple[str, ...] | None:
        """The names of this kind this image holds, or ``None`` if none were ever read.

        The two are different answers and the header says why at length. Returning ``()`` for
        an unread vocabulary would make "nobody looked" indistinguishable from "the image has
        none", and one of those is a reason to refuse a submission while the other is a reason
        to say nothing.
        """
        for entry in self.vocabularies:
            if entry.kind is kind:
                return entry.names
        return None

    def carries(self, kind: VocabularyName, name: str | None) -> bool:
        """Whether this image holds this name, which is false when nothing was read.

        ``None`` is false and is not a special case. A corpus that declares no tokenizer holds
        pre-tokenization text, and there is no vocabulary for the offered path to build a model
        over -- the same answer, reached honestly, as a name no image carries.
        """
        read = self.names(kind)
        return read is not None and name is not None and name in read


class ImageContentsRecord(ContractModel):
    """What every published training image was last seen to contain.

    One reading per repository rather than per digest, and that is a deliberate loss of
    precision worth naming. A per-digest record would be exact and would also go stale on every
    rebuild, so a caller would get "no reading for this image" for the ordinary case of somebody
    publishing a commit this morning. What is being asked is whether a corpus is trainable and
    whether a factory exists at all, and neither moves when somebody rebuilds -- they move when
    somebody edits. So the useful unit is the repository, with the commit recorded so a reader
    can see how far behind the reading is.
    """

    schema_version: Literal[1]
    images: Annotated[
        tuple[ImageContentsReading, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)

    @model_validator(mode="after")
    def validate_one_reading_per_repository(self) -> Self:
        repositories = [reading.repository for reading in self.images]
        if len(set(repositories)) != len(repositories):
            raise ValueError("a repository must not carry more than one recorded reading")
        return self

    def images_carrying(self, kind: VocabularyName, name: str | None) -> tuple[str, ...]:
        """Which published images hold this name, so a refusal can name them.

        A tuple rather than a boolean for the reason
        :func:`~edullm_platform.contracts.image_scan.unreviewed_blocking_findings` is a list
        rather than a count: a decision wants the emptiness and a message wants the names. "No
        image has it" is a sentence somebody can act on only once they know which were asked.
        """
        return tuple(
            reading.repository for reading in self.images if reading.carries(kind, name)
        )

    def images_that_read(self, kind: VocabularyName) -> tuple[str, ...]:
        """Which readings established this vocabulary at all, which is the absence question.

        A caller that refuses has to ask this first. An empty answer means nobody has looked,
        and a refusal on that would be this platform asserting something about an image on the
        strength of a file that was never written -- which is the failure the whole record
        exists to end, arriving from the other direction.
        """
        return tuple(
            reading.repository for reading in self.images if reading.names(kind) is not None
        )

    def names_some_image_carries(self, kind: VocabularyName) -> frozenset[str]:
        """Every name of this kind any published image holds.

        The union rather than the intersection. A corpus is trainable if there is an image that
        can train it, and asking for agreement across every registered repository would refuse
        everything the moment a dataset validator with no map was recorded.
        """
        return frozenset(
            name
            for reading in self.images
            for name in (reading.names(kind) or ())
        )

    def reading_for(self, repository: str) -> ImageContentsReading | None:
        for reading in self.images:
            if reading.repository == repository:
                return reading
        return None
