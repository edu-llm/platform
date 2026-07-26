from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator

from .base import SANDBOX_BUCKET_PREFIX, ContractModel, Sha256Digest, UtcTimestamp
from .repository_registry import ECR_REPOSITORY_PATTERN
from .source_identity import SourceIdentity

GITHUB_REPOSITORY_PATTERN = (
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?"
    r"/[A-Za-z0-9_](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9_-])?$"
)
GITHUB_WORKFLOW_PATH_PATTERN = (
    r"^\.github/workflows/[A-Za-z0-9_]"
    r"(?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?\.ya?ml$"
)
GITHUB_REF_PATTERN = (
    r"^(?:refs/(?:heads|tags)/[A-Za-z0-9][A-Za-z0-9._/-]*|[0-9a-f]{40})$"
)
AWS_ACCOUNT_ID_PATTERN = r"^[0-9]{12}$"
SANDBOX_REGIONS = frozenset({"us-east-1", "us-east-2"})


def _is_well_formed_git_ref(value: str) -> bool:
    if re.fullmatch(GITHUB_REF_PATTERN, value) is None:
        return False
    if re.fullmatch(r"[0-9a-f]{40}", value) is not None:
        return True
    ref_name = value.split("/", maxsplit=2)[2]
    components = ref_name.split("/")
    return not (
        ".." in ref_name
        or "@{" in ref_name
        or ref_name.endswith((".", ".lock"))
        or "" in components
        or any(
            component.startswith(".") or component.endswith(".lock")
            for component in components
        )
    )


class GitHubWorkflowRunReference(ContractModel):
    run_repository: str = Field(pattern=GITHUB_REPOSITORY_PATTERN)
    workflow_repository: str = Field(pattern=GITHUB_REPOSITORY_PATTERN)
    workflow_path: str = Field(pattern=GITHUB_WORKFLOW_PATH_PATTERN)
    workflow_ref: str = Field(pattern=GITHUB_REF_PATTERN)
    run_id: int = Field(gt=0)
    run_attempt: int = Field(gt=0)

    @field_validator("workflow_ref")
    @classmethod
    def validate_workflow_ref(cls, value: str) -> str:
        if not _is_well_formed_git_ref(value):
            raise ValueError("workflow ref must be a well-formed branch, tag, or commit ref")
        return value

    @property
    def url(self) -> str:
        return (
            f"https://github.com/{self.run_repository}/actions/runs/"
            f"{self.run_id}/attempts/{self.run_attempt}"
        )

    @property
    def job_workflow_ref(self) -> str:
        return (
            f"{self.workflow_repository}/{self.workflow_path}"
            f"@{self.workflow_ref}"
        )


class ImageProvenance(ContractModel):
    """What one published image is, and where it came from.

    ``built_at`` is the ``created`` field of the image's own configuration blob, not the
    moment this record was written. The two coincide on a fresh build and diverge by
    however long has passed on a resumed run, which reads back an image someone else's
    run published; a record that dated that build to now would state a time at which
    nothing happened. Taking it from the image also makes the field checkable: anyone
    holding ``image_digest`` can inspect the image and compare.

    Two consequences follow from that choice and are deliberate. The configuration's
    ``created`` is RFC 3339 with nanoseconds while this field carries microseconds, so the
    remainder is truncated rather than rounded and ``built_at`` is never later than the
    moment the image records. And a build whose stages add no layer of their own carries
    its base image's ``created`` forward, so ``built_at`` then describes the base rather
    than the run — which is what the image itself claims, and is the fact worth recording.
    """

    schema_version: Literal[1]
    ecr_repository: str = Field(
        min_length=len(SANDBOX_BUCKET_PREFIX) + 1,
        max_length=256,
        pattern=ECR_REPOSITORY_PATTERN,
    )
    image_digest: Sha256Digest
    base_image_digest: Sha256Digest
    source: SourceIdentity
    workflow_run: GitHubWorkflowRunReference
    built_at: UtcTimestamp


def resolve_image_reference(
    provenance: ImageProvenance,
    *,
    aws_account_id: str,
    region: str,
) -> str:
    if not isinstance(aws_account_id, str):
        raise TypeError("AWS account ID must be a string")
    if re.fullmatch(AWS_ACCOUNT_ID_PATTERN, aws_account_id) is None:
        raise ValueError("AWS account ID must contain exactly 12 decimal digits")
    if not isinstance(region, str):
        raise TypeError("AWS region must be a string")
    if region not in SANDBOX_REGIONS:
        raise ValueError("AWS region must be an allowed sandbox region")
    if (
        not isinstance(provenance.ecr_repository, str)
        or len(provenance.ecr_repository) > 256
        or re.fullmatch(ECR_REPOSITORY_PATTERN, provenance.ecr_repository) is None
    ):
        raise ValueError("provenance contains an invalid ECR repository name")
    if (
        not isinstance(provenance.image_digest, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", provenance.image_digest) is None
    ):
        raise ValueError("provenance contains an invalid image digest")
    return (
        f"{aws_account_id}.dkr.ecr.{region}.amazonaws.com/"
        f"{provenance.ecr_repository}@{provenance.image_digest}"
    )
