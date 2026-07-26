from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from tools.verify_source_identity import main

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "repositories.yaml"
REPOSITORY = "OLMo-core"
GITHUB_REPOSITORY_ID = "1306868157"
ECR_REPOSITORY = "sbsandbox-intern-edullm-olmo-core"
BRANCH_REF = "refs/heads/main"


def run_git(repository_root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository_root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


@dataclass(frozen=True)
class Checkout:
    root: Path
    commit_sha: str


@pytest.fixture
def checkout(tmp_path: Path) -> Checkout:
    origin = tmp_path / "origin.git"
    root = tmp_path / "source"
    subprocess.run(
        ["git", "init", "--bare", str(origin)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(root)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    run_git(root, "config", "user.name", "Build Tooling Test")
    run_git(root, "config", "user.email", "build-tooling@example.invalid")
    run_git(root, "remote", "add", "origin", str(origin))
    (root / "tracked.txt").write_text("initial\n", encoding="utf-8")
    run_git(root, "add", "tracked.txt")
    run_git(root, "commit", "-m", "initial")
    run_git(root, "push", "--set-upstream", "origin", "main")
    return Checkout(root=root, commit_sha=run_git(root, "rev-parse", "HEAD"))


def argv(
    checkout: Checkout,
    tmp_path: Path,
    **overrides: str,
) -> list[str]:
    arguments: dict[str, str] = {
        "--registry": str(REGISTRY_PATH),
        "--repository": REPOSITORY,
        "--github-repository-id": GITHUB_REPOSITORY_ID,
        "--ref": BRANCH_REF,
        "--commit-sha": checkout.commit_sha,
        "--repository-root": str(checkout.root),
        "--output": str(tmp_path / "source-identity.json"),
        "--github-output": str(tmp_path / "step-output.txt"),
    }
    arguments.update(overrides)
    return [token for pair in arguments.items() for token in pair]


def test_clean_pushed_branch_writes_canonical_identity_and_step_outputs(
    checkout: Checkout,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(argv(checkout, tmp_path))

    assert exit_code == 0
    assert capsys.readouterr().err == ""
    written = (tmp_path / "source-identity.json").read_text(encoding="utf-8")
    assert json.loads(written) == {
        "schema_version": 1,
        "repository": REPOSITORY,
        "github_repository_id": 1306868157,
        "ref": BRANCH_REF,
        "commit_sha": checkout.commit_sha,
        "clean": True,
        "verified": True,
    }
    assert written.endswith("\n")
    assert ", " not in written and '": ' not in written
    assert (tmp_path / "step-output.txt").read_text(encoding="utf-8") == (
        f"commit_sha={checkout.commit_sha}\necr_repository={ECR_REPOSITORY}\n"
    )


def test_the_identity_document_is_optional_so_the_gate_need_not_write_one(
    checkout: Checkout,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The gate job runs on its own runner and the publish job re-derives the document
    # there, so the file the gate used to write was never read by anything.
    arguments = argv(checkout, tmp_path)
    output_index = arguments.index("--output")
    del arguments[output_index : output_index + 2]

    assert main(arguments) == 0
    assert capsys.readouterr().err == ""
    assert not (tmp_path / "source-identity.json").exists()
    assert (tmp_path / "step-output.txt").read_text(encoding="utf-8") == (
        f"commit_sha={checkout.commit_sha}\necr_repository={ECR_REPOSITORY}\n"
    )


def test_step_outputs_are_appended_so_earlier_outputs_survive(
    checkout: Checkout,
    tmp_path: Path,
) -> None:
    step_output = tmp_path / "step-output.txt"
    step_output.write_text("previous=kept\n", encoding="utf-8")

    assert main(argv(checkout, tmp_path)) == 0
    assert step_output.read_text(encoding="utf-8").startswith("previous=kept\n")


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"--repository": "not-registered"}, "unregistered_repository"),
        ({"--github-repository-id": "42"}, "repository_id_mismatch"),
        ({"--ref": "main"}, "invalid_ref"),
        ({"--commit-sha": "abc123"}, "invalid_commit_sha"),
        ({"--ref": "refs/heads/absent"}, "remote_ref_missing"),
    ],
)
def test_rejected_identities_exit_non_zero_with_only_a_machine_readable_reason(
    checkout: Checkout,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    overrides: dict[str, str],
    reason: str,
) -> None:
    exit_code = main(argv(checkout, tmp_path, **overrides))
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.err == f"{reason}\n"
    assert captured.out == ""
    assert not (tmp_path / "source-identity.json").exists()
    assert not (tmp_path / "step-output.txt").exists()


def test_a_dirty_tree_is_rejected_without_leaking_paths_or_environment(
    checkout: Checkout,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leak-canary-value")
    (checkout.root / "untracked.txt").write_text("scratch\n", encoding="utf-8")

    exit_code = main(argv(checkout, tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.err == "dirty_tree\n"
    assert "leak-canary-value" not in captured.err
    assert str(checkout.root) not in captured.err
    assert "git" not in captured.err


def test_a_missing_registry_file_fails_closed_without_a_traceback(
    checkout: Checkout,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(argv(checkout, tmp_path, **{"--registry": str(tmp_path / "absent.yaml")}))
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err.strip() == "registry_unreadable"
    assert not (tmp_path / "source-identity.json").exists()
