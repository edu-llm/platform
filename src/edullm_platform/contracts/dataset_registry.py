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
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, model_validator

from .base import ContractModel, require_ordered_sequence
from .dataset import (
    PUBLISHED_DATASET_BUCKET,
    BareSha256Digest,
    DatasetReleaseId,
    PublishedDatasetPrefix,
)

__all__ = [
    "DatasetRegistry",
    "PublishedDatasetReference",
    "RegisteredDatasetRelease",
]


class RegisteredDatasetRelease(ContractModel):
    release_id: DatasetReleaseId


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
    tokenizer: str = Field(min_length=1, pattern=r"^tokenizer/[a-z0-9]+(?:-[a-z0-9.]+)*$")

    # Deliberately no per-release source snapshot (a corpus's constituent names and their token
    # counts) here, though a compile-time mixture check would need exactly one and this model
    # would be its natural home. Absent because nothing reads it. The absence is safe rather
    # than merely deferred: adding it later is purely additive -- a defaulted tuple field, the
    # same shape WorkloadRoleScopeEvidence uses -- so no committed registry entry has to be
    # rewritten when the mixture fields ship.

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
        return frozenset(entry.release_id for entry in self.releases)

    def is_registered(self, release_id: str) -> bool:
        return release_id in self.release_ids

    def reference_for(self, reference_id: str) -> PublishedDatasetReference | None:
        for entry in self.published:
            if entry.reference_id == reference_id:
                return entry
        return None
