"""What a person choosing a corpus needs, joined out of a registry and one measurement.

**THE COLUMN THIS MODULE EXISTS FOR IS "WILL IT RUN", AND IT IS COMPUTED RATHER THAN
STORED.** Twenty-nine corpora are registered. Sixteen of them a run can name and train on.
Five more are registered, current, in a trainable family, at a corpus payload profile, and
refused by nothing this platform checks -- and what a run naming one reaches is a container
that cannot construct a model for the tokens it just resolved, exiting 69 with
:data:`~edullm_platform.tokenizers.THE_CONTAINERS_REFUSAL`. Until this verb the only way to
find that out was to submit one.

That verdict is a join over three things the wheel already carries, ``config/datasets.yaml``,
:data:`~edullm_platform.tokenizers.TOKENIZERS` and ``config/image-tokenizers.yaml``, so it
cannot go stale: all three are on the release trigger, a change to any of them cuts a
release, and an install that answers the old way is an install that says so when ``edullm
submit`` probes for a newer one. Nothing about runnability is written into the committed
measurement, deliberately. A stored verdict would be a claim made on the day somebody ran a
tool, and the day OLMo-core grows a byte tokenizer it would be a wrong claim nobody had a
reason to re-run anything about.

**IT WAS A JOIN OVER TWO AND THE MISSING THIRD IS WHY IT LIED.** The tokenizer map says what
this platform knows how to express and the verdict presented that as what an image knows how
to build. ``tokenizers.py`` records the hazard in capitals and states the ordering that
prevents it -- the OLMo-core change lands first, this ships after -- and the ordering was
not held, twice. So ``fineweb-edu-750m-v2``, ``fineweb-edu-1b-v6`` and
``formal-proof-premises-500m-v3`` were verdicted ``runs``, offered on the submission form,
and ``run_019fdd88-3ac4`` named the first, was admitted, allocated a GPU and exited 69. A
rule written in a docstring is a rule the next author has to have read; what
``config/image-tokenizers.yaml`` does is make the ordering the shape of the data, so a
tokenizer this platform can express is offered only once an image has been *seen* carrying
it. ``contracts/image_tokenizers.py`` carries that argument in full.

**WHAT IS MEASURED IS THE PART THAT LIVES IN SOMEBODY ELSE'S BUCKET.** Train tokens, shard
dtype, licence, size and the one line saying what a corpus is are in the sealed
``dataset.json`` under ``s3://edullm-data/``, which ``edullm`` holds no credential to read
and is not going to grow one. So the measurement travels with the tool, in
``config/reports/corpora.json``, exactly as ``config/run-history.json`` does and for the
argument ``tools/build_run_history.py`` sets out at length. ``tools/build_corpora_snapshot.py``
is the only thing that writes it.

**UNDER ``config/reports/`` RATHER THAN ``config/``, WHICH IS A DECISION AND NOT A
FILING.** A file added to ``config/`` is a file on ``.github/workflows/release-tag.yml``'s
path list, so every re-measurement would cut a release and tell thirty-five installs they
are behind. ``config/reports/working-tier.yaml`` made the same choice and records the cost:
a change here reaches an installed CLI only on its next re-install. That lag is acceptable
for exactly the fields below, because none of them can lose anybody a machine -- a token
count a week out of date is a number to sort by, and every printing of it carries the date
it was measured. The field that *could* lose somebody a machine is the runnability verdict,
and that one is not in this file at all.

**EVERY MEASURED FIELD IS NULLABLE AND ``None`` MEANS "THE READING DID NOT ESTABLISH THIS".**
Not "zero", not "none of it", not "unknown to anybody". A reading that covered a corpus
partially is the ordinary state of a bucket whose thirty-two seals come in three different
shapes, and a snapshot that filled a gap with a plausible number would be inventing the one
kind of fact this platform refuses to invent. The verb prints a dash.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from edullm_platform.contracts.dataset_registry import (
    DatasetRegistry,
    PublishedDatasetReference,
)
from edullm_platform.contracts.image_tokenizers import ImageTokenizerRecord
from edullm_platform.reviewed_configuration import ConfigFile
from edullm_platform.tokenizers import THE_CONTAINERS_REFUSAL, TOKENIZERS

__all__ = [
    "CORPORA_FILENAME",
    "NOTHING_MEASURED",
    "NO_SNAPSHOT_PACKAGED",
    "SNAPSHOT_FORMAT_VERSION",
    "CorporaSnapshot",
    "CorporaSnapshotFormatError",
    "Corpus",
    "CorpusMeasurement",
    "CorpusUnknownError",
    "Runnability",
    "as_document",
    "corpora",
    "from_document",
    "load_corpora_snapshot",
    "one_corpus",
    "tokens_said",
]

#: Bumped when a field changes meaning or goes away. Adding one does not move it, which is
#: the promise a reader of the document needs.
SNAPSHOT_FORMAT_VERSION: Final = 1

#: Where the reading lives, relative to the reviewed configuration directory, read off the
#: vocabulary rather than written out. ``tests/test_config_resolution.py`` fails a module
#: that spells a reviewed file's location, and this is one, ``reports/`` and all.
#:
#: JSON among the YAML for ``run-history.json``'s reason: nobody edits this by hand and
#: nobody should, so a comment in it would be a claim nothing regenerated.
CORPORA_FILENAME: Final = ConfigFile.CORPORA.value

#: What the verb says when the install carries no reading at all. Distinct from a reading
#: that covered a corpus and established nothing about it, which prints as dashes on the row.
#: One is a finding about this install and the other is a finding about the bucket.
NO_SNAPSHOT_PACKAGED: Final = (
    "no corpus measurement is packaged with this install, so the columns read out of the "
    "sealed bucket are blank. tools/build_corpora_snapshot.py is what writes it."
)

#: What the detail view says about a corpus the reading did not cover at all.
NOTHING_MEASURED: Final = (
    "the packaged measurement does not cover this corpus, so nothing here can say how many "
    "tokens it holds, what its shards are or what it is licensed under"
)


class CorporaSnapshotFormatError(ValueError):
    """A reading this tree cannot parse, which is never a reading that found nothing."""


class CorpusUnknownError(LookupError):
    """A name the registry does not carry, raised so the verb can say what it does carry."""


@dataclass(frozen=True)
class Runnability:
    """Whether a run naming this corpus would get anywhere, and the sentence saying why not.

    THREE STATES AND NOT TWO, WHICH IS THE WHOLE REASON THIS IS NOT A BOOLEAN. A corpus can
    be refused before it costs anything, admitted and then refused by a container, or fine.
    The middle one is the state this verb was built for and it is the one a boolean would
    have folded into "no" beside the harmless case of a tokenizer entry nobody could ever
    have trained on.
    """

    #: One of ``runs``, ``exits_69`` or ``refused``. A closed vocabulary a caller may branch
    #: on, where :attr:`said` is prose this repository will reword.
    verdict: str
    said: str

    @property
    def will_run(self) -> bool:
        return self.verdict == "runs"

    @property
    def costs_a_machine(self) -> bool:
        """Admitted by everything here and refused by the container. The expensive state."""
        return self.verdict == "exits_69"


@dataclass(frozen=True)
class CorpusMeasurement:
    """The facts about one corpus that live in the sealed bucket and nowhere in the wheel.

    Every field bar the reference id is optional, for the reason the module header gives.
    """

    reference_id: str
    train_tokens: int | None = None
    #: Whether the source of :attr:`train_tokens` stated every digit of it.
    #:
    #: **A FLAG RATHER THAN A ROUNDED NUMBER PRETENDING TO BE AN EXACT ONE, AND IT EXISTS
    #: BECAUSE THE FIRST READING IS PARTLY A TRANSCRIPTION.** Seven of the figures below come
    #: out of ``config/datasets.yaml``, where whoever registered the corpus wrote the count
    #: off its sealed ``dataset.json`` to the token. The rest come out of the table in
    #: ``guides/the-platform.md``, which states one decimal place in billions, so
    #: ``10_100_000_000`` there is a figure somebody rounded and not one anybody read. Both
    #: sort the same and only one of them may be printed as a fact, which is what this
    #: separates. The verb prints a rounded one with a ``~`` in front of it.
    #:
    #: Every row a real bucket reading produces is exact, so this flag is the visible measure
    #: of how much of the file is still a transcription, and it goes away on its own.
    train_tokens_exact: bool = True
    #: ``uint16`` or ``uint32``, as the corpus's own ``tokens/manifest.json`` declares it per
    #: entry. Never inferred from a vocabulary size: OLMo-core's reader takes the dtype from
    #: the manifest, and a corpus read at the wrong width produces a loss curve rather than
    #: an exception, which is why this is worth a column at all.
    shard_dtype: str | None = None
    size_bytes: int | None = None
    #: The dataset-level licence id, or ``None`` where the corpus declares
    #: ``{basis: unknown, id: null}`` or declares ``mixed`` without naming the constituents.
    licence: str | None = None
    #: Whether the corpus is, in whole or in part, under a share-alike licence. A separate
    #: field from :attr:`licence` because it is a condition on redistributing a model rather
    #: than a name, and because the corpus this matters most for declares its licence id as
    #: null while its own notes record CC-BY-SA-4.0 over 7.13 per cent of its train tokens.
    #: A reader sorting by size would never meet that fact; a column does.
    share_alike: bool = False
    #: What the corpus's own ``purpose`` field says, in one line, or ``None``.
    purpose: str | None = None
    #: Anything the reading established that no column can hold, printed on the detail view
    #: and nowhere else.
    note: str | None = None


@dataclass(frozen=True)
class CorporaSnapshot:
    """One reading of the sealed bucket, and when and from what it was taken."""

    measured_at: datetime
    #: What was read. Printed, because a reader deciding whether to believe a token count
    #: wants to know whether somebody opened the bucket or transcribed a table.
    measured_from: str
    measurements: tuple[CorpusMeasurement, ...]

    def measurement_for(self, reference_id: str) -> CorpusMeasurement | None:
        for entry in self.measurements:
            if entry.reference_id == reference_id:
                return entry
        return None

    def said(self) -> str:
        """The provenance line every printing of this reading carries."""
        return (
            f"Measured on {self.measured_at.date().isoformat()} over "
            f"{len(self.measurements)} corpora, from {self.measured_from}."
        )


@dataclass(frozen=True)
class Corpus:
    """One registered corpus as the verb prints it: the registry, the join, the reading."""

    reference: PublishedDatasetReference
    runnability: Runnability
    #: ``None`` where the packaged reading does not cover this corpus, or where there is no
    #: packaged reading at all.
    measurement: CorpusMeasurement | None
    #: The un-retired reference ids registered against the same corpus, which is what a
    #: retired row is told to name instead. Carried on the row rather than looked up at
    #: print time, so the renderer needs no registry and cannot answer out of a second one.
    current_versions: tuple[str, ...] = ()

    @property
    def reference_id(self) -> str:
        return self.reference.reference_id

    @property
    def retired(self) -> bool:
        return self.reference.retired

    @property
    def tokenizer(self) -> str:
        """What the corpus was built with, spelled for a column rather than as a dataset id.

        ``-`` for a corpus that declares none, which is honest rather than missing: three
        registered sft corpora hold pre-tokenization conversation text and the run's
        tokenizer comes from the model.
        """
        if self.reference.tokenizer is None:
            return "-"
        return self.reference.tokenizer.removeprefix("tokenizer/")

    @property
    def train_tokens_said(self) -> str:
        """The size column, with a ``~`` on a figure nobody read to the token."""
        if self.measurement is None or self.measurement.train_tokens is None:
            return "-"
        said = tokens_said(self.measurement.train_tokens)
        return said if self.measurement.train_tokens_exact else f"~{said}"

    @property
    def dtype_said(self) -> str:
        if self.measurement is None or self.measurement.shard_dtype is None:
            return "-"
        return self.measurement.shard_dtype.replace("uint", "u")

    @property
    def licence_said(self) -> str:
        """The licence as a column, with share-alike said out loud where it applies.

        SHARE-ALIKE BEATS THE ID WHEN BOTH ARE PRESENT, AND THAT IS THE POINT OF THE COLUMN.
        A researcher scanning this is deciding what they may publish, and "unknown" beside a
        corpus that is seven per cent CC-BY-SA-4.0 tells them the wrong thing more clearly
        than a blank would.
        """
        if self.measurement is None:
            return "-"
        if self.measurement.share_alike and self.measurement.licence is None:
            return "share-alike"
        if self.measurement.share_alike:
            return f"{self.measurement.licence}, share-alike"
        return self.measurement.licence or "-"


def tokens_said(count: int) -> str:
    """A token count as a person says one, because nobody reads 250,242,924,544.

    One decimal place and never more. These are sorted by and compared, not added up, and a
    figure printed to the token would claim a precision the choice does not need. The exact
    integer is on the detail view and in ``--json``, which is where somebody who wants it is.
    """
    if count >= 1_000_000_000:
        return f"{count / 1_000_000_000:.1f}B"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    return str(count)


def _runnability(
    reference: PublishedDatasetReference, images: ImageTokenizerRecord
) -> Runnability:
    """The join, in the order a submission meets the refusals.

    EACH BRANCH IS A REFUSAL THAT ALREADY EXISTS SOMEWHERE ELSE, ASKED HERE RATHER THAN
    INVENTED HERE. ``is_a_corpus_a_run_may_read`` is what ``dataset_is_not_a_corpus`` reads,
    ``retired`` is what ``retired_dataset_release`` reads, and the tokenizer lookup is the
    one that nothing reads -- which is exactly why the verb exists.

    The order matters and is the order the submission path applies them in. A retired
    tokenizer entry would be reported as retired if retirement came first, and what a
    submitter actually meets is ``dataset_is_not_a_corpus``, because policy denies that
    outright and never gets as far as the current-version check.

    **THE LAST BRANCH IS TWO QUESTIONS AND IT USED TO BE ONE, WHICH IS THE DEFECT.** It
    asked whether the tokenizer was a key in :data:`~edullm_platform.tokenizers.TOKENIZERS`,
    which is this platform's map of what it can *express*, and answered with a claim about
    what an image can *build*. Those agreed until two entries were added ahead of the
    matching lines in OLMo-core -- the ordering ``tokenizers.py`` insists on in capitals and
    which nothing enforced -- and then three corpora were verdicted ``runs`` that no image
    can train. ``run_019fdd88-3ac4`` named one, was admitted, allocated a GPU and exited 69.

    So the two questions are asked separately, of the two things that can answer them. Both
    end in ``exits_69`` because both produce exactly that, down to the same refusal string;
    what differs is the sentence, because they ask opposite things of whoever reads it. A
    tokenizer this platform cannot express is a corpus nobody can run until somebody writes
    a config for it. One the platform expresses and no image carries is a corpus that is one
    published image away, and the reader can be told which images were asked.
    """
    if not reference.is_a_corpus_a_run_may_read:
        return Runnability(
            verdict="refused",
            said=(
                f"Refused before it costs anything. {reference.dataset_id} at profile "
                f"{reference.payload_profile} is an input to a corpus rather than a corpus, "
                "so a submission naming it is denied outright with dataset_is_not_a_corpus."
            ),
        )
    if reference.retired:
        return Runnability(
            verdict="refused",
            said=(
                "Refused before it costs anything. Its owner has stopped naming this version "
                "as the current one, so a submission naming it is refused with "
                "retired_dataset_release, on the laptop and again in the compile job."
            ),
        )
    if reference.tokenizer not in TOKENIZERS:
        declared = (
            "declares no tokenizer, because its payload is pre-tokenization text"
            if reference.tokenizer is None
            else f"depends on {reference.tokenizer}, which no OLMo-core TokenizerConfig builds"
        )
        return Runnability(
            verdict="exits_69",
            said=(
                f"Nothing refuses this and it does not run. It {declared}, so a submission "
                "naming it compiles clean, classifies routine, spends an approval, allocates "
                f"the machine, and the container exits 69 with {THE_CONTAINERS_REFUSAL}."
            ),
        )
    if not images.images_carrying(reference.tokenizer):
        return Runnability(
            verdict="exits_69",
            said=(
                f"Nothing refuses this and it does not run. This platform can build a config "
                f"for {reference.tokenizer} and no published image carries one: "
                f"{_images_said(images)}. So a submission naming it compiles clean, "
                "classifies routine, spends an approval, allocates the machine, and the "
                f"container exits 69 with {THE_CONTAINERS_REFUSAL}. It becomes runnable when "
                "the tokenizer lands in a research repository's own map and that image is "
                "read again, and not before."
            ),
        )
    return Runnability(verdict="runs", said="A run may name this and it will start.")


def _images_said(images: ImageTokenizerRecord) -> str:
    """Which images were asked and what each holds, so a refusal names its evidence.

    A refusal reading "no image carries it" sends somebody to look at every image there is.
    One naming the images and their maps sends them to the one line that has to change,
    which is the same argument ``unreviewed_blocking_findings`` makes for listing findings
    rather than counting them.
    """
    return "; ".join(
        f"{reading.repository} carries "
        + (", ".join(reading.tokenizers) if reading.tokenizers else "none")
        for reading in images.images
    )


def corpora(
    registry: DatasetRegistry,
    *,
    images: ImageTokenizerRecord,
    snapshot: CorporaSnapshot | None = None,
) -> tuple[Corpus, ...]:
    """Every registered published corpus, joined and sorted by what a chooser sorts by.

    BY TRAIN TOKENS AND NOT BY NAME, WHICH IS A CLAIM ABOUT WHO IS READING. Somebody
    choosing a corpus is choosing a size first and everything else second, and an
    alphabetical list puts ``fineweb2-unimax-superbpe-20b-v1`` above ``math-frontload-100m-v1``
    for no reason a reader can use. A corpus the reading did not measure sorts last rather
    than first, because an unmeasured row is not a small one.

    Everything registered is here, retired and unrunnable included. What the verb does with
    them is the verb's decision; hiding one at this level would make the two halves of that
    decision live in different files.
    """
    rows = [
        Corpus(
            reference=reference,
            runnability=_runnability(reference, images),
            measurement=(
                None
                if snapshot is None
                else snapshot.measurement_for(reference.reference_id)
            ),
            current_versions=(
                registry.current_versions_of(reference.reference_id) if reference.retired else ()
            ),
        )
        for reference in registry.published
    ]
    return tuple(sorted(rows, key=_by_size))


def _by_size(row: Corpus) -> tuple[int, int, str]:
    measured = row.measurement.train_tokens if row.measurement is not None else None
    return (1, 0, row.reference_id) if measured is None else (0, measured, row.reference_id)


def one_corpus(
    reference_id: str,
    registry: DatasetRegistry,
    *,
    images: ImageTokenizerRecord,
    snapshot: CorporaSnapshot | None = None,
) -> Corpus:
    """One corpus by the name a submission would use, or a refusal naming what exists.

    Raises :class:`CorpusUnknownError` rather than returning ``None``, so that the verb has
    one place to compose the list of names and cannot report "no such corpus" as an empty
    table.
    """
    for row in corpora(registry, images=images, snapshot=snapshot):
        if row.reference_id == reference_id:
            return row
    raise CorpusUnknownError(reference_id)


def load_corpora_snapshot(directory: Path) -> CorporaSnapshot | None:
    """The packaged reading, or ``None`` where this install carries none.

    ``None`` for an absent file and an exception for an unreadable one, which is
    :func:`~edullm_platform.run_history.load_run_history`'s split and is the same argument: a
    missing measurement is an ordinary install, and a measurement this tree cannot parse is a
    broken one.
    """
    path = directory / CORPORA_FILENAME
    if not path.is_file():
        return None
    try:
        return from_document(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise CorporaSnapshotFormatError(f"{path} could not be read: {exc}") from exc


def as_document(snapshot: CorporaSnapshot) -> dict[str, Any]:
    """One reading, as the JSON-shaped mapping the tool commits.

    A measured field that is ``None`` is written out as ``null`` rather than omitted. The
    two mean the same thing to :func:`from_document`, and only one of them is visible to
    somebody reading the file to find out what the reading missed.
    """
    return {
        "format_version": SNAPSHOT_FORMAT_VERSION,
        "measured_at": snapshot.measured_at.isoformat(),
        "measured_from": snapshot.measured_from,
        "corpora": [
            {
                "reference_id": entry.reference_id,
                "train_tokens": entry.train_tokens,
                "train_tokens_exact": entry.train_tokens_exact,
                "shard_dtype": entry.shard_dtype,
                "size_bytes": entry.size_bytes,
                "licence": entry.licence,
                "share_alike": entry.share_alike,
                "purpose": entry.purpose,
                "note": entry.note,
            }
            for entry in sorted(snapshot.measurements, key=lambda entry: entry.reference_id)
        ],
    }


def from_document(document: Mapping[str, Any]) -> CorporaSnapshot:
    """Read one back, refusing a version this tree does not know how to read.

    A newer document would have fields this reader drops, and a dropped measurement reads as
    a measurement that was not taken, which is the one thing a reading may not invent.
    """
    declared = int(document.get("format_version", 0))
    if declared != SNAPSHOT_FORMAT_VERSION:
        raise CorporaSnapshotFormatError(
            f"this edullm reads corpus measurements at format_version "
            f"{SNAPSHOT_FORMAT_VERSION} and the packaged one declares {declared}"
        )
    return CorporaSnapshot(
        measured_at=_read_timestamp(str(document["measured_at"])),
        measured_from=str(document["measured_from"]),
        measurements=tuple(_measurements(document.get("corpora") or ())),
    )


def _measurements(entries: Any) -> Iterator[CorpusMeasurement]:
    for entry in entries:
        yield CorpusMeasurement(
            reference_id=str(entry["reference_id"]),
            train_tokens=_optional_int(entry.get("train_tokens")),
            train_tokens_exact=bool(entry.get("train_tokens_exact", True)),
            shard_dtype=_optional_text(entry.get("shard_dtype")),
            size_bytes=_optional_int(entry.get("size_bytes")),
            licence=_optional_text(entry.get("licence")),
            share_alike=bool(entry.get("share_alike", False)),
            purpose=_optional_text(entry.get("purpose")),
            note=_optional_text(entry.get("note")),
        )


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _read_timestamp(text: str) -> datetime:
    """A timestamp that always carries a zone, because a naive one cannot be compared.

    The tool writes UTC. A document written by hand without an offset is read as UTC rather
    than refused, which is the reading that makes a date printed beside a table right.
    """
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
