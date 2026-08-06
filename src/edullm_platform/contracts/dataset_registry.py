"""Which dataset releases admission will accept.

This exists because the answer used to live in a ``frozenset`` literal inside
``phase0_gate``. That was serviceable while the only caller was the gate itself, and it
stops being serviceable the moment admission asks the same question: a validator running
inside AWS would have to import a gate module to learn which datasets are registered, and
the registered set would then be a property of the verification tooling rather than of the
reviewed configuration it is supposed to describe.

Deliberately thin. A registry entry carries a release identifier and nothing else, because
a release identifier is the whole of what admission asks. The rich description of a
release — checksums, S3 version ids, schema, lineage, licence, classification, access
policy — already exists as :class:`~edullm_platform.contracts.dataset.DatasetRelease` and
is what a later phase will bind these identifiers to. Adding those fields here now would
mean inventing values nothing reads.

Two kinds of dataset are registered here, described by different facts, and each keeps its
own list rather than sharing one. A :class:`RegisteredDatasetRelease` is checked by
identifier alone, because that is the whole of what admission asks about a release this
platform produced. A :class:`PublishedDatasetReference` names a corpus somebody else
published into a sealed bucket this account does not own; it carries a URI, a dataset id, a
version, a content digest and a tokenizer because those are the facts a later reader needs
to resolve and pin it, and none of them belong on the thin model — adding them there would
put a field with no admission-time reader next to the one field admission actually checks.

Two lists, and admission asks two questions of them. :meth:`DatasetRegistry.is_registered`
answers over both, because ``unregistered_dataset`` denies a submission outright and a corpus
that names its own uri and digest is not an unresolvable input. The lists stay separate
because they carry different facts, not because a submitter is being asked which kind they
picked.

:meth:`DatasetRegistry.is_a_trainable_corpus` is the second question and arrived with the
first non-corpus entries. This registry now carries a tokenizer, a verbatim vendor mirror and
two sft conversation corpora, because dependents pin them by digest and the registry's job is
to carry what exists. Being registered is what lets a submission resolve one; it is not
permission to train on one, and ``TRAINABLE_FAMILIES`` is where the difference is written
down.

:meth:`DatasetRegistry.is_retired` is the third, and it is the one ``retired`` never had. The
flag has existed on both models since the first entry needed it, and until now the only thing
that read it was the join computing the submission form's option list. So a retired corpus
was held off the form and admitted by every check this platform makes -- measured rather than
reasoned about, by running ``dolma-2026-07`` and ``fineweb-edu-1b-v2`` through ``edullm
check``, ``tools/compile_submission.py`` and ``admit`` on 2026-08-05: no refusals, compiled
routine, admitted. A dropdown is not an enforcement point, and a run that reached a green
finish that way leaves an immutable lineage record naming a corpus nobody publishes.

The predicate is here, in the contract both sides of the submission path already read, so
that it cannot be answered one way on a laptop and another way in CI.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal, Self

from pydantic import BeforeValidator, Field, model_validator

from .base import ContractModel, require_ordered_sequence
from .dataset import (
    PUBLISHED_DATASET_BUCKET,
    BareSha256Digest,
    DatasetReleaseId,
    PublishedDatasetPrefix,
)
from .vocabulary import InputRole

__all__ = [
    "TRAINABLE_FAMILIES",
    "WEIGHTS_FAMILIES",
    "DatasetRegistry",
    "PublishedDatasetReference",
    "RegisteredDatasetRelease",
]

#: Which dataset families a run may name as the corpus it trains on.
#:
#: THE ONE THING STANDING BETWEEN A TOKENIZER AND A TRAINING RUN, and it is written as an
#: allowlist because the failure it prevents is silent. A tokenizer declares no partitions, so
#: the reader hands back every object rather than a trainable split, and its group carries no
#: dtype, which a ``NumpyFSLDatasetConfig`` turns into OLMo-core's ``uint16`` default before
#: memmapping ``tokenizer.json`` as if it were tokens. The run trains, the loss moves, and
#: nothing anywhere reports a problem. A result produced that way is indistinguishable from a
#: real one until somebody asks what the model read.
#:
#: WHAT USED TO REFUSE IT WAS A SHAPE RATHER THAN A RULE. ``tokenizer`` was required and
#: non-null, and a tokenizer declares no tokenizer of its own, so the entry could not be
#: written at all. That was never a decision about families; it was an accident of a field
#: that happened to be mandatory, and it evaporated the moment the field learned to hold
#: ``None`` so that the sft and vendor corpora could be registered. This is the deliberate
#: replacement, added in the same commit that removed the accident.
#:
#: AN ALLOWLIST AND NOT A DENYLIST, WHICH IS THE WHOLE SAFETY ARGUMENT. A denylist naming
#: ``tokenizer`` and ``vendor`` is correct today and admits the next family somebody publishes
#: without anybody deciding it is trainable. The dataset standard fixes six families and calls
#: adding one "a deliberate change to this document", and the upstream reader already carries
#: a seventh, so families do arrive. Failing closed means a new one is refused until a person
#: puts it here, and the cost of that is a refusal naming exactly what to do.
#:
#: WHY ``sft`` IS IN IT AND ``vendor`` IS NOT. The P7 tutor work trains on
#: ``sft/pedagogy70-normal30``, so forbidding the family would refuse a real training input to
#: buy a guarantee about tokenizers that excluding tokenizers already buys.
#: ``vendor/openai-prm800k`` is left out on its own evidence rather than on a hunch: its
#: ``limitations`` block records that records are preserved verbatim and that "any train-ready
#: representation must be a separately validated derived artifact". Naming it directly as a
#: corpus asks a run to train on the thing that corpus says is not trainable yet.
TRAINABLE_FAMILIES: Final = frozenset({"pretrain", "sft"})

#: Which dataset families a run may name as the weights it starts from.
#:
#: A SECOND SET RATHER THAN A SECOND MEMBERSHIP IN THE FIRST, and the reason is the failure
#: TRAINABLE_FAMILIES exists to prevent, running the other way. A base model handed to a
#: training run as its corpus is memmapped as tokens and produces a loss curve; a corpus handed
#: to an evaluation as its weights fails loudly, because nothing can load it. Only one of the
#: two is silent, and folding the sets would make a single edit able to reintroduce it.
#:
#: ``model/`` HAS NO SEALED ENTRY YET AND THAT IS NOT A REASON TO WAIT. s3://edullm-data/ carried
#: no model/ prefix on 2026-08-04 and config/datasets.yaml registers nothing under it. Sealing a
#: base model is the dataset validator's act; being able to say what a model/ entry would be for
#: is this file's, and the order has to be this way round -- a validated model with no family to
#: land in is unreachable the day it is sealed.
WEIGHTS_FAMILIES: Final = frozenset({"model"})


class RegisteredDatasetRelease(ContractModel):
    release_id: DatasetReleaseId
    #: Still accepted by admission, no longer offered on the form. The two are separate
    #: questions and conflating them forces a bad choice.
    #:
    #: ``dolma-2026-07`` is the case that needed it. Every historical intent record names it,
    #: because the field was required before ``none`` existed, so de-registering it would
    #: make those records unresolvable against the registry that is supposed to explain them.
    #: But no dataset was ever bound to it and no run ever read one, so offering it on the
    #: form invites a submitter to record that their run read something it did not -- into a
    #: record that is immutable by design and cannot be corrected afterwards.
    #:
    #: Defaulted false so every existing entry means what it meant, and so retiring one is a
    #: deliberate line in config/datasets.yaml rather than an omission somewhere.
    retired: bool = False


class PublishedDatasetReference(ContractModel):
    """A corpus somebody else built, named so a submission can ask for it.

    NOT A DatasetRelease, AND THE REASON IS A VALIDATOR RATHER THAN A URI TYPE.
    ``DatasetRelease.validate_release`` requires either a parent release or the run that
    produced it, and a corpus published elsewhere has neither -- ``derived_from`` holds
    slash-free identifiers this platform registers and ``produced_by_run_id`` is a ``run_``
    uuid7 we mint. Past that, ``objects`` is ``min_length=1`` with a sha256 and an S3
    VersionId per entry, so the largest of these corpora would need 6,911 records about
    objects nobody here produced. ``DatasetRelease`` is a statement about provenance this
    platform can make; this is a statement about a dependency.

    ``reference_id`` is what a submitter picks on the form and what admission checks, because
    ``DATASET_RELEASE_ID_PATTERN`` forbids slashes and ``pretrain/olmo-150b-dolma2`` is
    therefore not expressible as an identifier. ``dataset_id`` and ``version`` are stored
    apart rather than split out of the URI, because the reader takes them as two arguments
    and a caller that split the string differently would read a version nobody registered.

    The field set is the dataset standard's own cross-dataset pin -- its section 7 shows one
    dataset depending on another by ``{dataset_id, version, uri, manifest_sha256}`` -- plus the
    tokenizer, which that standard puts on the group and the published corpora carry one hop
    away in ``groups[].depends_on[]`` with ``role: "tokenizer"``.
    """

    reference_id: DatasetReleaseId
    uri: PublishedDatasetPrefix
    #: Shape-only, deliberately not constrained to a family enum. The dataset standard fixes
    #: `<family>` as a six-value enum -- pretrain, curriculum, sft, eval, probe, vendor -- and
    #: calls adding a family "a deliberate change to this document"; the upstream reader code
    #: carries seven, adding tokenizer. A pattern pinned to the standard's six would refuse an
    #: address that exists -- s3://edullm-data/ holds pretrain/ and tokenizer/ as its family
    #: prefixes, alongside the _catalog/ and _inventory/ metadata prefixes, read live 2026-07-31
    #: -- and a pattern pinned to the code's seven would encode that drift as if it were a rule.
    dataset_id: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:[/-][a-z0-9.]+)*$")
    #: Deliberately stays ``^v[0-9]+$`` rather than widening: upstream auto-allocates this value
    #: and never types it by hand. Note that upstream's ``version`` in ``dataset.json`` is an
    #: OBJECT -- ``{"id": "v3", "relation": "supersedes", "of": "v2"}`` with ``relation`` one of
    #: ``supersedes``, ``extends``, ``sibling`` -- and this field is the ``id``, which is also
    #: the path segment. That relation is why this plan never calls the upstream
    #: ``resolve_latest``: under ``extends``, the highest version is not the right answer,
    #: because the extension is consumed alongside its base and reading the latest alone
    #: silently drops it.
    version: str = Field(min_length=1, pattern=r"^v[0-9]+$")
    #: The payload group's ``manifest_sha256``, NOT the seal's ``dataset_sha256``. Per group
    #: rather than per dataset, and present only because these corpora declare
    #: ``mutability: frozen`` -- the standard requires the digest for ``frozen`` alone, so a
    #: ``live`` or ``append-only`` dataset has nothing to pin here and is not registrable.
    #:
    #: BARE HEX, NOT ``Sha256Digest``. That type is ``^sha256:[0-9a-f]{64}$``, written for ECR
    #: image digests; the value published in ``dataset.json`` carries no prefix. Storing a
    #: re-encoded copy of somebody else's digest is the one thing a content pin must not do.
    manifest_sha256: BareSha256Digest
    #: The published tokenizer this corpus was built with, as ITS dataset id. Required rather
    #: than defaulted: the upstream family file turns its own family-wide tokenizer default OFF
    #: and records the reason -- a mismatched tokenizer's ids usually still fall in range, so
    #: the failure is a plausible loss curve rather than an exception.
    #:
    #: NULLABLE AND STILL REQUIRED, WHICH ARE DIFFERENT PROPERTIES AND BOTH ARE WANTED. The
    #: annotation carries no default, so pydantic still refuses an entry that omits the key;
    #: what it now accepts is an explicit ``null``. Four registered datasets declare no
    #: tokenizer dependency at all -- two sft corpora of pre-tokenization conversation text, a
    #: verbatim vendor mirror, and the tokenizers themselves -- and the value the bucket offers
    #: for them is nothing. Writing ``tokenizer/dolma2-bpe`` there to satisfy a mandatory field
    #: would be the exact invented fact the paragraph above refuses, so the honest answer has
    #: to be spellable and has to be spelled out.
    #:
    #: A NULL HERE IS NOT WHAT KEEPS A TOKENIZER OFF A TRAINING RUN. It happens to, because
    #: ``None`` is not a key in any tokenizer map, and relying on that would be the same
    #: accident this field used to be. ``TRAINABLE_FAMILIES`` is the rule.
    tokenizer: str | None = Field(pattern=r"^tokenizer/[a-z0-9]+(?:-[a-z0-9.]+)*$")
    #: Registered and no longer offered, the same two questions ``RegisteredDatasetRelease``
    #: separates and for a reason that turned out to be identical.
    #:
    #: ``pretrain/fineweb-edu-1b`` is published at v2 and at v6, both sealed and frozen, and
    #: v6 is the one its owner names as current. Nothing computable tells them apart. They
    #: share a family and a tokenizer, and they pin the same digest, because their
    #: ``tokens/manifest.json`` objects are byte identical. The bucket does not close the gap
    #: either: v6 declares ``supersedes`` of v5 and v2 declares ``supersedes`` of v1, and v3
    #: through v5 were never published, so no chain in the data reaches from one to the other.
    #:
    #: So this is a fact no computation can derive, which is precisely the test the release
    #: list already applies to ``retired``. Defaulted false so every existing entry means what
    #: it meant, and so retiring one is a deliberate line in config/datasets.yaml.
    retired: bool = False

    # Deliberately no per-release source snapshot (a corpus's constituent names and their token
    # counts) here, though a compile-time mixture check would need exactly one and this model
    # would be its natural home. Absent because nothing reads it. The absence is safe rather
    # than merely deferred: adding it later is purely additive -- a defaulted tuple field, the
    # same shape WorkloadRoleScopeEvidence uses -- so no committed registry entry has to be
    # rewritten when the mixture fields ship.

    @property
    def family(self) -> str:
        """The first segment of the dataset id, which is where the standard puts the family.

        Derived rather than stored, deliberately. A second field would be a second place for
        the same fact and the first place to find it disagreeing with the id a reader passes
        to ``dataset_paths``, and the two are one statement for the same reason ``uri``,
        ``dataset_id`` and ``version`` are held to reconstructing each other.
        """
        return self.dataset_id.split("/", maxsplit=1)[0]

    @property
    def is_a_corpus_a_run_may_read(self) -> bool:
        """Whether a run may name this as the corpus it trains on. See TRAINABLE_FAMILIES."""
        return self.family in TRAINABLE_FAMILIES

    @property
    def is_weights_a_run_may_start_from(self) -> bool:
        """Whether a run may name this as the weights it starts from. See WEIGHTS_FAMILIES."""
        return self.family in WEIGHTS_FAMILIES

    @model_validator(mode="after")
    def validate_reference(self) -> Self:
        # A suffix test is not enough here: with dataset_id="olmo-150b-dolma2" (missing its
        # "pretrain/" segment, and therefore not the corpus's real id) and
        # uri="s3://edullm-data/pretrain/olmo-150b-dolma2/v1/", the uri still ENDS WITH
        # "/olmo-150b-dolma2/v1/" and `endswith` would silently accept it -- storing a
        # dataset_id that is not this dataset's id and that a later reader passes straight
        # to the upstream reader. Reconstructing the full uri from its parts and comparing
        # for equality closes that gap: every uri a full match accepts, a suffix match would
        # also accept, but not the reverse.
        # The message says "must be", not "must end with", because the rule stopped being a
        # suffix test and the old wording was false about the one case the strengthening was
        # for: the headline rejection is a uri that DOES end with its dataset id and version
        # and is refused anyway.
        expected_uri = f"s3://{PUBLISHED_DATASET_BUCKET}/{self.dataset_id}/{self.version}/"
        if self.uri != expected_uri:
            raise ValueError(
                "a published reference's uri must be the one its dataset id and version "
                "name, so the two fields and the prefix cannot describe different objects"
            )
        return self


class DatasetRegistry(ContractModel):
    schema_version: Literal[1]
    releases: Annotated[
        tuple[RegisteredDatasetRelease, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)
    published: Annotated[
        tuple[PublishedDatasetReference, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(default=(), strict=False)

    @model_validator(mode="after")
    def validate_releases(self) -> Self:
        release_ids = [entry.release_id for entry in self.releases]
        if release_ids != sorted(set(release_ids)):
            raise ValueError(
                "registered dataset releases must be listed once each in ascending order"
            )
        reference_ids = [entry.reference_id for entry in self.published]
        if reference_ids != sorted(set(reference_ids)):
            raise ValueError(
                "published dataset references must be listed once each in ascending order"
            )
        return self

    @property
    def release_ids(self) -> frozenset[str]:
        """Only the releases this platform produces. Not what admission asks -- see below."""
        return frozenset(entry.release_id for entry in self.releases)

    @property
    def reference_ids(self) -> frozenset[str]:
        return frozenset(entry.reference_id for entry in self.published)

    def is_registered(self, dataset_id: str) -> bool:
        """Whether a submission may name this dataset at all. Both lists, deliberately.

        ``phase0_gate`` denies a manifest outright with ``unregistered_dataset`` when this is
        False, so the question here is "can this platform resolve what was asked for", and a
        published corpus is more resolvable than a release id, not less: it carries a uri, a
        content digest and a tokenizer, where a release id carries only itself.

        Answered from ``releases`` alone until the published corpora reached the submission
        form. Nothing could name one before that, so the narrower answer was untestable in
        the direction that mattered and read as the tidier split. What it would produce now is
        a dropdown option denied after a lead has approved it.

        The parameter is ``dataset_id`` rather than ``release_id`` because it stopped being
        one namespace's identifier, and a name promising otherwise is how the second list gets
        forgotten again.
        """
        return dataset_id in self.release_ids or dataset_id in self.reference_ids

    def is_a_trainable_corpus(self, dataset_id: str) -> bool:
        """Whether a run naming this dataset would be reading a corpus rather than an input.

        A SECOND QUESTION AND NOT A STRICTER VERSION OF :meth:`is_registered`, which is why
        it is a second method and a second denied-outright condition rather than a narrowing
        of the first. "Nothing registers this" and "this is registered and is not a corpus"
        send a submitter to two different places, and folding them together would answer the
        second with ``unregistered_dataset`` -- a refusal naming a dataset that is in the file
        the refusal points at.

        True for anything this cannot resolve to a published corpus, which covers ``none``,
        ``dolma-2026-07`` and every identifier nothing registers. That is not a fail-open
        default. A release id names no family, so there is no family question to ask about
        one, and an unregistered identifier is already denied by :meth:`is_registered`. Each
        condition answers exactly one thing and a submission trips whichever apply.
        """
        reference = self.reference_for(dataset_id)
        return reference is None or reference.is_a_corpus_a_run_may_read

    def is_retired(self, dataset_id: str) -> bool:
        """Whether this entry is registered and is no longer the one to name.

        THE THIRD QUESTION, AND THE ONE THE FLAG DID NOT USED TO BE ASKED. ``retired`` is set
        on two entries and was read by one caller, the join in
        ``tests/test_submission_form_options.py`` that computes the form's option list. So
        the flag removed a menu item and enforced nothing, which
        ``config/datasets.yaml`` says in as many words -- it "keeps admission's answer and
        removes the menu item". Measured on 2026-08-05 rather than inferred: both retired
        names clear ``edullm check``, compile routine and are admitted.

        ASKED OVER BOTH LISTS, BECAUSE BOTH CARRY THE FLAG AND FOR THE SAME REASON. The two
        entries that set it are one of each kind, and what they have in common is that
        nothing computable separates them from an entry that is current --
        ``dolma-2026-07`` has nothing published under it to look up, and
        ``pretrain/fineweb-edu-1b`` at v2 shares a family, a tokenizer and a pinned digest
        with the version that supersedes it.

        False for anything this registry does not carry, and that is not a fail-open
        default. An identifier nothing registers is already refused by :meth:`is_registered`
        under a condition policy denies outright, and answering "not retired" for a name
        that is not there is the honest answer to the question actually asked.

        WHAT READS THIS IS THE SUBMISSION PATH AND NOTHING THAT READS A RECORD. A stored run
        naming ``dolma-2026-07`` still resolves against this registry, because
        :meth:`is_registered` is what resolution asks and retirement does not touch it. That
        separation is the same one #214 and #228 landed for images and for verdicts:
        today's roster must not retroactively refuse yesterday's run.
        """
        reference = self.reference_for(dataset_id)
        if reference is not None:
            return reference.retired
        for entry in self.releases:
            if entry.release_id == dataset_id:
                return entry.retired
        return False

    def names_a_run_may_still_use(self) -> tuple[str, ...]:
        """Every registered identifier a submission could name and not be refused for, sorted.

        THE LIST A REFUSAL MAY SUGGEST, AND IT IS NARROWER THAN "REGISTERED". #232 made the
        same correction to the workload refusal and ``_resolve_workload`` records why: a name
        offered to somebody who has just been refused is a second refusal waiting, and
        listing the whole registry reproduces inside an error message the exact defect the
        dropdown tests exist to keep off a menu.

        Three conditions, and each one is a refusal that already exists rather than a
        judgement made here. Registered, or ``unregistered_dataset`` denies it outright. In a
        trainable family, or ``dataset_is_not_a_corpus`` does. Not retired, which is the
        refusal this change adds. Every registered name this omits is a name some check
        refuses, and every name it keeps is one no check refuses -- so a refusal built from
        it cannot suggest something that cannot be taken, and cannot go stale as the
        registry grows.

        Deliberately not narrowed to what the form offers. Five registered corpora are
        trainable, current and off the dropdown because no tokenizer here can build theirs,
        and nothing in this platform refuses one -- so naming them is honest about what a
        submission may do, and omitting them would be this list claiming an enforcement that
        does not exist. ``config/datasets.yaml`` carries what a run picking one meets
        instead, which is a container that exits 69.
        """
        return tuple(
            sorted(
                name
                for name in (*self.release_ids, *self.reference_ids)
                if self.is_a_trainable_corpus(name) and not self.is_retired(name)
            )
        )

    def current_versions_of(self, dataset_id: str) -> tuple[str, ...]:
        """The un-retired reference ids registered against the same corpus, sorted.

        What a retirement refusal can put in front of somebody instead of the name they
        typed, when the registry knows one. ``pretrain/fineweb-edu-1b`` is carried at v2 and
        v6, so a submission naming the retired v2 can be sent to v6 by name rather than to
        the file to read.

        Empty is an ordinary answer and not a lookup that failed. ``dolma-2026-07`` is a
        release rather than a published corpus, so it has no ``dataset_id`` to group on and
        nothing was ever published under it; the honest replacement for that one is ``none``,
        which the refusal says for itself rather than deriving from here.
        """
        retired = self.reference_for(dataset_id)
        if retired is None:
            return ()
        return tuple(
            sorted(
                entry.reference_id
                for entry in self.published
                if entry.dataset_id == retired.dataset_id and not entry.retired
            )
        )

    def reference_for(self, reference_id: str) -> PublishedDatasetReference | None:
        """Resolve a published corpus to the facts a reader needs. Published list only.

        Narrow on purpose, and not the same question as :meth:`is_registered`: there is no uri
        or digest to hand back for ``dolma-2026-07``, so returning ``None`` for a registered
        release is the honest answer rather than a gap.
        """
        for entry in self.published:
            if entry.reference_id == reference_id:
                return entry
        return None

    def may_fill(self, dataset_id: str, *, role: InputRole) -> bool:
        """Whether this dataset can be what a run named it as.

        FAILS CLOSED ON AN UNRESOLVABLE ID, WHICH INVERTS is_a_trainable_corpus DELIBERATELY.
        That method answers True for anything it cannot resolve, because a release id names no
        family and is_registered already denies an unknown one. Here the question is what the
        platform will hand to a container, and an id resolving to no address resolves to no
        bytes -- so True would be a run started from nothing, reported as started from
        something.
        """
        reference = self.reference_for(dataset_id)
        if reference is None:
            return False
        if role is InputRole.CORPUS:
            return reference.is_a_corpus_a_run_may_read
        return reference.is_weights_a_run_may_start_from
