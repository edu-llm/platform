from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from edullm_platform.contracts.repository_registry import (
    RegisteredRepository,
    RepositoryRegistry,
)
from edullm_platform.contracts.source_identity import (
    SourceIdentity,
    SourceIdentityError,
    SourceIdentityReason,
    verify_source_identity,
)

REPOSITORY_NAME = "OLMo-core"
GITHUB_REPOSITORY_ID = 1306868157
BRANCH_REF = "refs/heads/main"
BASE_DIGEST = "sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"


def run_git(repository_root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository_root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def make_registry() -> RepositoryRegistry:
    return RepositoryRegistry(
        repositories=(
            RegisteredRepository(
                repository=REPOSITORY_NAME,
                github_repository_id=GITHUB_REPOSITORY_ID,
                default_branch="main",
                ecr_repository="sbsandbox-intern-edullm-olmo-core",
                base_image_repository="docker.io/library/python",
                base_image_digest=BASE_DIGEST,
                dockerfile_path=".edullm/Dockerfile",
                build_context=".",
            ),
        )
    )


@dataclass(frozen=True)
class GitFixture:
    checkout: Path
    origin: Path
    commit_sha: str
    registry: RepositoryRegistry

    def verify(self, **overrides: Any) -> SourceIdentity:
        arguments: dict[str, object] = {
            "repository": REPOSITORY_NAME,
            "github_repository_id": GITHUB_REPOSITORY_ID,
            "ref": BRANCH_REF,
            "commit_sha": self.commit_sha,
            "repository_root": self.checkout,
            "registry": self.registry,
        }
        arguments.update(overrides)
        return verify_source_identity(**arguments)  # type: ignore[arg-type]


def build_git_fixture(tmp_path: Path) -> GitFixture:
    origin = tmp_path / "origin.git"
    checkout = tmp_path / "checkout"
    subprocess.run(
        ["git", "init", "--bare", str(origin)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(checkout)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    run_git(checkout, "config", "user.name", "Source Identity Test")
    run_git(checkout, "config", "user.email", "source-identity@example.invalid")
    run_git(checkout, "remote", "add", "origin", str(origin))
    (checkout / "tracked.txt").write_text("initial\n")
    run_git(checkout, "add", "tracked.txt")
    run_git(checkout, "commit", "-m", "initial")
    run_git(checkout, "push", "--set-upstream", "origin", "main")
    return GitFixture(
        checkout=checkout,
        origin=origin,
        commit_sha=run_git(checkout, "rev-parse", "HEAD"),
        registry=make_registry(),
    )


@pytest.fixture
def git_fixture(tmp_path: Path) -> GitFixture:
    """A repository of this test's own, for a test that changes it.

    Building one costs nine git subprocesses. A test that dirties the worktree, commits,
    or pushes needs its own; take ``unchanging_repository`` instead if you only read.
    """
    return build_git_fixture(tmp_path)


@pytest.fixture(scope="module")
def unchanging_repository(tmp_path_factory: pytest.TempPathFactory) -> Iterator[GitFixture]:
    """One clean repository, shared by every test in this module that only reads it.

    Most of these cases verify a clean checkout and assert on the refusal they get for a
    bad argument, which leaves the tree exactly as it was found. Rebuilding a repository
    for each of them was twenty-two identical setups.

    A test that changes the tree may not use this. The teardown says so out loud rather
    than leaving the next test in the module to fail somewhere unrelated.
    """
    fixture = build_git_fixture(tmp_path_factory.mktemp("unchanging"))
    yield fixture
    assert run_git(fixture.checkout, "status", "--porcelain") == "", (
        "a test changed the shared repository. Every later test in this module read a "
        "tree that was not the one this fixture set up; use git_fixture instead."
    )
    assert run_git(fixture.checkout, "rev-parse", "HEAD") == fixture.commit_sha


def assert_reason(
    expected: SourceIdentityReason,
    fixture: GitFixture,
    **overrides: Any,
) -> SourceIdentityError:
    with pytest.raises(SourceIdentityError) as exc_info:
        fixture.verify(**overrides)
    assert exc_info.value.reason is expected
    assert str(exc_info.value)
    return exc_info.value


def source_identity_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "repository": REPOSITORY_NAME,
        "github_repository_id": GITHUB_REPOSITORY_ID,
        "ref": BRANCH_REF,
        "commit_sha": "a" * 40,
        "clean": True,
        "verified": True,
    }
    payload.update(overrides)
    return payload


def test_clean_pushed_branch_returns_frozen_verified_identity(
    unchanging_repository: GitFixture,
) -> None:
    identity = unchanging_repository.verify()

    assert identity.model_dump() == source_identity_payload(
        commit_sha=unchanging_repository.commit_sha
    )
    with pytest.raises(ValidationError):
        identity.commit_sha = "b" * 40


@pytest.mark.parametrize(
    "payload",
    [
        source_identity_payload(schema_version=2),
        source_identity_payload(schema_version="1"),
        source_identity_payload(github_repository_id="1306868157"),
        source_identity_payload(clean=False),
        source_identity_payload(verified=False),
        source_identity_payload(clean="true"),
        source_identity_payload(verified="true"),
        source_identity_payload(ref="a" * 40, commit_sha=BRANCH_REF),
        source_identity_payload(unexpected=True),
    ],
)
def test_source_identity_rejects_wrong_extra_swapped_or_non_strict_values(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        SourceIdentity.model_validate(payload)


@pytest.mark.parametrize(
    "ref",
    [
        "main",
        "refs/pull/1/head",
        "refs/tags/main",
        "refs/heads/",
        "refs/heads/feature..bad",
        "refs/heads/feature lock",
        "refs/heads/main.lock",
        "refs/heads//main",
        "refs/heads/main/",
        "refs/heads/main//x",
        "refs/heads/@{bad",
    ],
)
def test_source_identity_contract_rejects_non_branch_or_malformed_refs(
    ref: str,
) -> None:
    with pytest.raises(ValidationError):
        SourceIdentity.model_validate(source_identity_payload(ref=ref))


@pytest.mark.parametrize("commit_sha", ["a" * 7, "A" * 40])
def test_source_identity_contract_rejects_short_or_uppercase_sha(
    commit_sha: str,
) -> None:
    with pytest.raises(ValidationError):
        SourceIdentity.model_validate(source_identity_payload(commit_sha=commit_sha))


def test_dirty_tracked_worktree_fails(git_fixture: GitFixture) -> None:
    (git_fixture.checkout / "tracked.txt").write_text("dirty\n")

    assert_reason(SourceIdentityReason.DIRTY_TREE, git_fixture)


def test_dirty_untracked_worktree_fails(git_fixture: GitFixture) -> None:
    (git_fixture.checkout / "untracked.txt").write_text("dirty\n")

    assert_reason(SourceIdentityReason.DIRTY_TREE, git_fixture)


def test_unpushed_commit_fails_branch_head_verification(
    git_fixture: GitFixture,
) -> None:
    (git_fixture.checkout / "tracked.txt").write_text("unpushed\n")
    run_git(git_fixture.checkout, "add", "tracked.txt")
    run_git(git_fixture.checkout, "commit", "-m", "unpushed")
    unpushed_sha = run_git(git_fixture.checkout, "rev-parse", "HEAD")

    assert_reason(
        SourceIdentityReason.REMOTE_REF_MISMATCH,
        git_fixture,
        commit_sha=unpushed_sha,
    )


def test_wrong_repository_id_fails(unchanging_repository: GitFixture) -> None:
    assert_reason(
        SourceIdentityReason.REPOSITORY_ID_MISMATCH,
        unchanging_repository,
        github_repository_id=999,
    )


def test_unknown_repository_fails(unchanging_repository: GitFixture) -> None:
    assert_reason(
        SourceIdentityReason.UNREGISTERED_REPOSITORY,
        unchanging_repository,
        repository="missing",
    )


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("commit_sha", "a" * 7, SourceIdentityReason.INVALID_COMMIT_SHA),
        ("commit_sha", "A" * 40, SourceIdentityReason.INVALID_COMMIT_SHA),
        ("ref", "main", SourceIdentityReason.INVALID_REF),
        ("ref", "refs/pull/1/head", SourceIdentityReason.INVALID_REF),
        ("ref", "refs/tags/main", SourceIdentityReason.INVALID_REF),
        ("ref", "refs/heads/main/", SourceIdentityReason.INVALID_REF),
        ("ref", "refs/heads/main//x", SourceIdentityReason.INVALID_REF),
        ("ref", "refs/heads/main;echo-no", SourceIdentityReason.INVALID_REF),
    ],
)
def test_verifier_rejects_invalid_identity_inputs(
    unchanging_repository: GitFixture,
    field: str,
    value: str,
    reason: SourceIdentityReason,
) -> None:
    assert_reason(reason, unchanging_repository, **{field: value})


def test_checkout_head_mismatch_fails(git_fixture: GitFixture) -> None:
    (git_fixture.checkout / "tracked.txt").write_text("second\n")
    run_git(git_fixture.checkout, "add", "tracked.txt")
    run_git(git_fixture.checkout, "commit", "-m", "second")
    second_sha = run_git(git_fixture.checkout, "rev-parse", "HEAD")
    run_git(git_fixture.checkout, "push", "origin", "main")

    assert_reason(
        SourceIdentityReason.HEAD_MISMATCH,
        git_fixture,
        commit_sha=git_fixture.commit_sha,
    )
    assert second_sha != git_fixture.commit_sha


def test_missing_remote_ref_fails(unchanging_repository: GitFixture) -> None:
    assert_reason(
        SourceIdentityReason.REMOTE_REF_MISSING,
        unchanging_repository,
        ref="refs/heads/missing",
    )


def test_non_git_root_fails(unchanging_repository: GitFixture, tmp_path: Path) -> None:
    non_git_root = tmp_path / "not-git"
    non_git_root.mkdir()

    assert_reason(
        SourceIdentityReason.NOT_GIT_REPOSITORY,
        unchanging_repository,
        repository_root=non_git_root,
    )


def test_initial_git_probe_failure_is_git_command_failure(
    unchanging_repository: GitFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text("#!/bin/sh\nexit 42\n")
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))

    error = assert_reason(SourceIdentityReason.GIT_COMMAND_FAILURE, unchanging_repository)

    assert "exit code 42" in error.detail


def test_missing_remote_is_sanitized_git_command_failure(
    unchanging_repository: GitFixture,
) -> None:
    error = assert_reason(
        SourceIdentityReason.GIT_COMMAND_FAILURE,
        unchanging_repository,
        remote_name="missing",
    )

    assert "missing" in str(error)
    assert "CAAS_" not in str(error)
    assert "AWS_" not in str(error)


def test_upload_pack_option_is_rejected_without_executing_command(
    unchanging_repository: GitFixture,
    tmp_path: Path,
) -> None:
    marker = tmp_path / "option-injection-marker"
    unsafe_remote = f"--upload-pack=touch {marker}"

    error = assert_reason(
        SourceIdentityReason.INVALID_REMOTE,
        unchanging_repository,
        remote_name=unsafe_remote,
    )

    assert "remote name" in error.detail
    assert not marker.exists()


@pytest.mark.parametrize(
    "remote_name",
    [
        "",
        "-origin",
        "team/origin",
        "team origin",
        "https://example.invalid/repository.git",
        "ssh://example.invalid/repository.git",
    ],
)
def test_unsafe_remote_names_are_rejected(
    unchanging_repository: GitFixture,
    remote_name: str,
) -> None:
    assert_reason(
        SourceIdentityReason.INVALID_REMOTE,
        unchanging_repository,
        remote_name=remote_name,
    )


def test_ls_remote_uses_option_terminator(
    unchanging_repository: GitFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_git = shutil.which("git")
    assert real_git is not None
    wrapper_directory = tmp_path / "bin"
    wrapper_directory.mkdir()
    invocation_log = tmp_path / "git-invocations"
    wrapper = wrapper_directory / "git"
    wrapper.write_text(
        "#!/bin/sh\n"
        f'printf "<%s>" "$@" >> "{invocation_log}"\n'
        f'printf "\\n" >> "{invocation_log}"\n'
        f'exec "{real_git}" "$@"\n'
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", str(wrapper_directory))

    unchanging_repository.verify()

    assert (
        "<-C>"
        f"<{unchanging_repository.checkout}>"
        "<ls-remote><--exit-code><--><origin><refs/heads/main>"
    ) in invocation_log.read_text()
