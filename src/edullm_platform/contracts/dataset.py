from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, model_validator

from .base import ContractModel, Sha256Digest, UtcTimestamp, require_ordered_sequence
from .bindings import TeamId
from .identity import RunId
from .lifecycle import SandboxS3Prefix
from .vocabulary import DataClassificationValue

DATASET_RELEASE_ID_PATTERN = r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$"
DATASET_OBJECT_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9!._*'()/-]{0,1023}$"
S3_VERSION_ID_PATTERN = r"^[A-Za-z0-9._-]{1,1024}$"
LICENCE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9.+-]{0,63}$"

DatasetReleaseId = Annotated[str, Field(pattern=DATASET_RELEASE_ID_PATTERN)]


class DatasetObject(ContractModel):
    key: str = Field(pattern=DATASET_OBJECT_KEY_PATTERN)
    checksum: Sha256Digest
    s3_version_id: str = Field(pattern=S3_VERSION_ID_PATTERN)


class DatasetSchemaRef(ContractModel):
    name: str = Field(min_length=1)
    digest: Sha256Digest


class DatasetAccessPolicy(ContractModel):
    readable_by_team_ids: Annotated[
        tuple[TeamId, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)

    @model_validator(mode="after")
    def validate_reader_roster(self) -> Self:
        team_ids = list(self.readable_by_team_ids)
        if team_ids != sorted(set(team_ids)):
            raise ValueError("readable team ids must be unique and sorted")
        return self

    def permits(self, team_id: str) -> bool:
        return team_id in self.readable_by_team_ids


class DatasetRelease(ContractModel):
    schema_version: Literal[1]
    release_id: DatasetReleaseId
    uri: SandboxS3Prefix
    created_at: UtcTimestamp
    objects: Annotated[
        tuple[DatasetObject, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)
    schema_ref: DatasetSchemaRef
    derived_from: Annotated[
        tuple[DatasetReleaseId, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(default=(), strict=False)
    produced_by_run_id: RunId | None
    licence: str = Field(pattern=LICENCE_ID_PATTERN)
    data_classification: DataClassificationValue
    access_policy: DatasetAccessPolicy

    @model_validator(mode="after")
    def validate_release(self) -> Self:
        keys = [entry.key for entry in self.objects]
        if keys != sorted(set(keys)):
            raise ValueError("dataset objects must be listed once each in ascending key order")
        parents = list(self.derived_from)
        if parents != sorted(set(parents)):
            raise ValueError("derived-from releases must be unique and sorted")
        if self.release_id in parents:
            raise ValueError("a dataset release must not be derived from itself")
        if not parents and self.produced_by_run_id is None:
            raise ValueError(
                "a dataset release must record either a parent release "
                "or the run that produced it"
            )
        return self

    def is_readable_by(self, team_id: str) -> bool:
        return self.access_policy.permits(team_id)
