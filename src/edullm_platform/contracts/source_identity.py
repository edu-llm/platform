from __future__ import annotations

import re
import subprocess
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from .base import ContractModel
from .manifest import COMMIT_SHA_PATTERN
from .repository_registry import RepositoryRegistry, UnknownRepositoryError

BRANCH_REF_PATTERN = r"^refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]*$"
REMOTE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
GIT_TIMEOUT_SECONDS = 10


class SourceIdentityReason(StrEnum):
    UNREGISTERED_REPOSITORY = "unregistered_repository"
    REPOSITORY_ID_MISMATCH = "repository_id_mismatch"
    INVALID_REF = "invalid_ref"
    INVALID_COMMIT_SHA = "invalid_commit_sha"
    INVALID_REMOTE = "invalid_remote"
    NOT_GIT_REPOSITORY = "not_git_repository"
    DIRTY_TREE = "dirty_tree"
    HEAD_MISMATCH = "head_mismatch"
    REMOTE_REF_MISSING = "remote_ref_missing"
    REMOTE_REF_MISMATCH = "remote_ref_mismatch"
    GIT_COMMAND_FAILURE = "git_command_failure"


class SourceIdentityError(RuntimeError):
    def __init__(self, reason: SourceIdentityReason, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason.value}: {detail}")


def _is_valid_branch_ref(ref: str) -> bool:
    if re.fullmatch(BRANCH_REF_PATTERN, ref) is None:
        return False
    branch_name = ref.removeprefix("refs/heads/")
    components = branch_name.split("/")
    return not (
        ".." in branch_name
        or "@{" in branch_name
        or branch_name.endswith((".", ".lock"))
        or "" in components
        or any(component.startswith(".") or component.endswith(".lock") for component in components)
    )


class SourceIdentity(ContractModel):
    schema_version: Literal[1]
    repository: str = Field(min_length=1, pattern=r".*\S.*")
    github_repository_id: int = Field(gt=0)
    ref: str = Field(min_length=len("refs/heads/x"), pattern=BRANCH_REF_PATTERN)
    commit_sha: str = Field(pattern=COMMIT_SHA_PATTERN)
    clean: Literal[True]
    verified: Literal[True]

    @field_validator("ref")
    @classmethod
    def validate_branch_ref(cls, value: str) -> str:
        if not _is_valid_branch_ref(value):
            raise ValueError("ref must be a well-formed refs/heads branch ref")
        return value


def _run_git(repository_root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(repository_root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SourceIdentityError(
            SourceIdentityReason.GIT_COMMAND_FAILURE,
            "git command exceeded the bounded timeout",
        ) from exc
    except OSError as exc:
        raise SourceIdentityError(
            SourceIdentityReason.GIT_COMMAND_FAILURE,
            "git command could not be executed",
        ) from exc


def _require_success(
    result: subprocess.CompletedProcess[str],
    operation: str,
) -> str:
    if result.returncode != 0:
        raise SourceIdentityError(
            SourceIdentityReason.GIT_COMMAND_FAILURE,
            f"{operation} failed with exit code {result.returncode}",
        )
    return result.stdout.strip()


def verify_source_identity(
    *,
    repository: str,
    github_repository_id: int,
    ref: str,
    commit_sha: str,
    repository_root: Path,
    registry: RepositoryRegistry,
    remote_name: str = "origin",
) -> SourceIdentity:
    try:
        registered_repository = registry.repository_by_name(repository)
    except UnknownRepositoryError as exc:
        raise SourceIdentityError(
            SourceIdentityReason.UNREGISTERED_REPOSITORY,
            f"repository is not registered: {repository!r}",
        ) from exc

    if registered_repository.github_repository_id != github_repository_id:
        raise SourceIdentityError(
            SourceIdentityReason.REPOSITORY_ID_MISMATCH,
            "GitHub repository ID does not match the registered repository",
        )
    if not _is_valid_branch_ref(ref):
        raise SourceIdentityError(
            SourceIdentityReason.INVALID_REF,
            "ref must be a well-formed refs/heads branch ref",
        )
    if re.fullmatch(COMMIT_SHA_PATTERN, commit_sha) is None:
        raise SourceIdentityError(
            SourceIdentityReason.INVALID_COMMIT_SHA,
            "commit SHA must contain exactly 40 lowercase hexadecimal characters",
        )
    if re.fullmatch(REMOTE_NAME_PATTERN, remote_name) is None:
        raise SourceIdentityError(
            SourceIdentityReason.INVALID_REMOTE,
            "remote name must use only letters, digits, dots, underscores, and hyphens",
        )

    work_tree = _run_git(repository_root, "rev-parse", "--is-inside-work-tree")
    if work_tree.returncode != 0:
        if "not a git repository" not in work_tree.stderr.lower():
            _require_success(work_tree, "checking Git work tree")
        raise SourceIdentityError(
            SourceIdentityReason.NOT_GIT_REPOSITORY,
            "repository root is not a Git work tree",
        )
    if work_tree.stdout.strip() != "true":
        raise SourceIdentityError(
            SourceIdentityReason.NOT_GIT_REPOSITORY,
            "repository root is not a Git work tree",
        )

    status = _require_success(
        _run_git(repository_root, "status", "--porcelain", "--untracked-files=all"),
        "git status",
    )
    if status:
        raise SourceIdentityError(
            SourceIdentityReason.DIRTY_TREE,
            "repository work tree contains tracked or untracked changes",
        )

    head = _require_success(
        _run_git(repository_root, "rev-parse", "HEAD"),
        "reading checkout HEAD",
    )
    if head != commit_sha:
        raise SourceIdentityError(
            SourceIdentityReason.HEAD_MISMATCH,
            "checkout HEAD does not match the requested commit SHA",
        )

    remote = _run_git(
        repository_root,
        "ls-remote",
        "--exit-code",
        "--",
        remote_name,
        ref,
    )
    if remote.returncode == 2 and not remote.stdout.strip():
        raise SourceIdentityError(
            SourceIdentityReason.REMOTE_REF_MISSING,
            f"remote {remote_name!r} does not contain the requested branch ref",
        )
    remote_output = _require_success(
        remote,
        f"reading remote {remote_name!r} branch ref",
    )
    lines = remote_output.splitlines()
    if len(lines) != 1:
        raise SourceIdentityError(
            SourceIdentityReason.REMOTE_REF_MISMATCH,
            "remote branch lookup did not return exactly one identity",
        )
    fields = lines[0].split()
    if len(fields) != 2 or fields[0] != commit_sha or fields[1] != ref:
        raise SourceIdentityError(
            SourceIdentityReason.REMOTE_REF_MISMATCH,
            "remote branch head does not match the requested commit SHA",
        )

    return SourceIdentity(
        schema_version=1,
        repository=repository,
        github_repository_id=github_repository_id,
        ref=ref,
        commit_sha=commit_sha,
        clean=True,
        verified=True,
    )
