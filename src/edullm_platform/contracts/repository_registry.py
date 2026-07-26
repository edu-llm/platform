from typing import Annotated, Self

from pydantic import BeforeValidator, Field, field_validator, model_validator

from .base import (
    SANDBOX_BUCKET_PREFIX,
    ContractModel,
    Sha256Digest,
    require_ordered_sequence,
)

ECR_REPOSITORY_PATTERN = (
    rf"^{SANDBOX_BUCKET_PREFIX}[a-z0-9]+(?:[._/-][a-z0-9]+)*$"
)
BASE_IMAGE_REPOSITORY_SCHEMA_PATTERN = r"^[^:@]*[^:@\s][^:@]*$"
SAFE_PATH_COMPONENT_SCHEMA_PATTERN = (
    r"(?:\.|[^./\\][^/\\]*|\.[^./\\][^/\\]*|\.\.[^/\\]+)"
)
NON_DOT_PATH_COMPONENT_SCHEMA_PATTERN = (
    r"(?:[^./\\][^/\\]*|\.[^./\\][^/\\]*|\.\.[^/\\]+)"
)
BUILD_CONTEXT_SCHEMA_PATTERN = (
    rf"^{SAFE_PATH_COMPONENT_SCHEMA_PATTERN}"
    rf"(?:/{SAFE_PATH_COMPONENT_SCHEMA_PATTERN})*$"
)
DOCKERFILE_PATH_SCHEMA_PATTERN = (
    rf"^(?:{NON_DOT_PATH_COMPONENT_SCHEMA_PATTERN}|"
    rf"{SAFE_PATH_COMPONENT_SCHEMA_PATTERN}/{SAFE_PATH_COMPONENT_SCHEMA_PATTERN}"
    rf"(?:/{SAFE_PATH_COMPONENT_SCHEMA_PATTERN})*)$"
)


class UnknownRepositoryError(ValueError):
    """Raised when a repository is absent from the registry."""


class RegisteredRepository(ContractModel):
    repository: str = Field(min_length=1, pattern=r".*\S.*")
    github_repository_id: int = Field(gt=0)
    default_branch: str = Field(min_length=1, pattern=r".*\S.*")
    ecr_repository: str = Field(
        min_length=len(SANDBOX_BUCKET_PREFIX) + 1,
        max_length=256,
        pattern=ECR_REPOSITORY_PATTERN,
    )
    base_image_repository: str = Field(
        min_length=1,
        json_schema_extra={"pattern": BASE_IMAGE_REPOSITORY_SCHEMA_PATTERN},
    )
    base_image_digest: Sha256Digest
    dockerfile_path: str = Field(
        min_length=1,
        json_schema_extra={"pattern": DOCKERFILE_PATH_SCHEMA_PATTERN},
    )
    build_context: str = Field(
        min_length=1,
        json_schema_extra={"pattern": BUILD_CONTEXT_SCHEMA_PATTERN},
    )

    @field_validator("base_image_repository")
    @classmethod
    def validate_base_image_repository(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("base image repository must be non-empty")
        if ":" in value or "@" in value:
            raise ValueError("base image repository must not include a tag or digest")
        return value

    @field_validator("dockerfile_path", "build_context")
    @classmethod
    def validate_repository_relative_path(cls, value: str) -> str:
        if value.startswith("/") or "\\" in value:
            raise ValueError("path must be a repository-relative POSIX path")
        components = value.split("/")
        if "" in components or ".." in components:
            raise ValueError("path must be a safe repository-relative POSIX path")
        return value

    @field_validator("dockerfile_path")
    @classmethod
    def validate_dockerfile_path(cls, value: str) -> str:
        if value == ".":
            raise ValueError("Dockerfile path must identify a file")
        return value

    @property
    def immutable_base_reference(self) -> str:
        return f"{self.base_image_repository}@{self.base_image_digest}"


class RepositoryRegistry(ContractModel):
    repositories: Annotated[
        tuple[RegisteredRepository, ...],
        BeforeValidator(require_ordered_sequence),
    ] = Field(min_length=1, strict=False)

    @model_validator(mode="after")
    def validate_unique_identifiers(self) -> Self:
        repository_names = [item.repository for item in self.repositories]
        if len(set(repository_names)) != len(repository_names):
            raise ValueError("repository names must be unique")
        github_repository_ids = [
            item.github_repository_id for item in self.repositories
        ]
        if len(set(github_repository_ids)) != len(github_repository_ids):
            raise ValueError("GitHub repository IDs must be unique")
        ecr_repository_names = [item.ecr_repository for item in self.repositories]
        if len(set(ecr_repository_names)) != len(ecr_repository_names):
            raise ValueError("ECR repository names must be unique")
        return self

    def repository_by_name(self, repository_name: str) -> RegisteredRepository:
        for repository in self.repositories:
            if repository.repository == repository_name:
                return repository
        raise UnknownRepositoryError(
            f"repository name is not registered: {repository_name!r}"
        )

    def repository_by_id(self, github_repository_id: int) -> RegisteredRepository:
        for repository in self.repositories:
            if repository.github_repository_id == github_repository_id:
                return repository
        raise UnknownRepositoryError(
            f"GitHub repository ID is not registered: {github_repository_id}"
        )
