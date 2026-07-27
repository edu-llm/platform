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
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, model_validator

from .base import ContractModel, require_ordered_sequence
from .dataset import DatasetReleaseId

__all__ = [
    "DatasetRegistry",
    "RegisteredDatasetRelease",
]


class RegisteredDatasetRelease(ContractModel):
    release_id: DatasetReleaseId


class DatasetRegistry(ContractModel):
    schema_version: Literal[1]
    releases: Annotated[
        tuple[RegisteredDatasetRelease, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)

    @model_validator(mode="after")
    def validate_releases(self) -> Self:
        release_ids = [entry.release_id for entry in self.releases]
        if release_ids != sorted(set(release_ids)):
            raise ValueError(
                "registered dataset releases must be listed once each in ascending order"
            )
        return self

    @property
    def release_ids(self) -> frozenset[str]:
        return frozenset(entry.release_id for entry in self.releases)

    def is_registered(self, release_id: str) -> bool:
        return release_id in self.release_ids
