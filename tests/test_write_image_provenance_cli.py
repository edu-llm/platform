from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.write_image_provenance import main

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "repositories.yaml"
COMMIT_SHA = "a" * 40
IMAGE_DIGEST = "sha256:" + "b" * 64
BASE_DIGEST = "sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"
JOB_WORKFLOW_SHA = "c" * 40
ECR_REPOSITORY = "sbsandbox-intern-edullm-olmo-core"
WORKFLOW_PATH_INPUT = ".github/workflows/build-research-image.yml"


def write_identity(tmp_path: Path, **overrides: object) -> Path:
    payload: dict[str, object] = {
        "schema_version": 1,
        "repository": "OLMo-core",
        "github_repository_id": 1306868157,
        "ref": "refs/heads/main",
        "commit_sha": COMMIT_SHA,
        "clean": True,
        "verified": True,
    }
    payload.update(overrides)
    path = tmp_path / "source-identity.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def argv(tmp_path: Path, **overrides: str) -> list[str]:
    arguments: dict[str, str] = {
        "--registry": str(REGISTRY_PATH),
        "--repository": "OLMo-core",
        "--source-identity": str(tmp_path / "source-identity.json"),
        "--image-digest": IMAGE_DIGEST,
        "--run-repository": "edu-llm/OLMo-core",
        "--workflow-repository": "edu-llm/platform",
        "--workflow-path": WORKFLOW_PATH_INPUT,
        "--workflow-ref": JOB_WORKFLOW_SHA,
        "--run-id": "987654321",
        "--run-attempt": "2",
        "--built-at": "2026-07-26T12:00:00.000000Z",
        "--output": str(tmp_path / "image-provenance.json"),
    }
    arguments.update(overrides)
    return [token for pair in arguments.items() for token in pair]


def test_provenance_is_canonical_and_derives_registry_facts_from_the_registry(
    tmp_path: Path,
) -> None:
    write_identity(tmp_path)

    assert main(argv(tmp_path)) == 0

    written = (tmp_path / "image-provenance.json").read_text(encoding="utf-8")
    assert json.loads(written) == {
        "schema_version": 1,
        "ecr_repository": ECR_REPOSITORY,
        "image_digest": IMAGE_DIGEST,
        "base_image_digest": BASE_DIGEST,
        "source": {
            "schema_version": 1,
            "repository": "OLMo-core",
            "github_repository_id": 1306868157,
            "ref": "refs/heads/main",
            "commit_sha": COMMIT_SHA,
            "clean": True,
            "verified": True,
        },
        "workflow_run": {
            "run_repository": "edu-llm/OLMo-core",
            "workflow_repository": "edu-llm/platform",
            "workflow_path": WORKFLOW_PATH_INPUT,
            "workflow_ref": JOB_WORKFLOW_SHA,
            "run_id": 987654321,
            "run_attempt": 2,
        },
        "built_at": "2026-07-26T12:00:00.000000Z",
    }
    assert written.endswith("\n")
    assert ", " not in written and '": ' not in written


def test_provenance_never_persists_a_registry_host_or_account_identifier(
    tmp_path: Path,
) -> None:
    write_identity(tmp_path)
    assert main(argv(tmp_path)) == 0

    written = (tmp_path / "image-provenance.json").read_text(encoding="utf-8")
    assert "dkr.ecr" not in written
    assert "amazonaws.com" not in written
    assert "repositoryUri" not in written


def test_the_caller_is_the_run_repository_and_platform_owns_the_workflow(
    tmp_path: Path,
) -> None:
    write_identity(tmp_path)
    assert main(argv(tmp_path)) == 0

    payload = json.loads((tmp_path / "image-provenance.json").read_text(encoding="utf-8"))
    workflow_run = payload["workflow_run"]

    assert workflow_run["run_repository"] != workflow_run["workflow_repository"]
    assert workflow_run["run_repository"] == "edu-llm/OLMo-core"
    assert workflow_run["workflow_repository"] == "edu-llm/platform"


def test_an_identity_naming_a_different_repository_is_rejected(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_identity(tmp_path, repository="Other-repo")

    exit_code = main(argv(tmp_path))

    assert exit_code == 1
    assert capsys.readouterr().err.strip() == "repository_mismatch"
    assert not (tmp_path / "image-provenance.json").exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"--image-digest": "sha256:" + "z" * 64},
        {"--image-digest": "b" * 64},
        {"--run-id": "0"},
        {"--run-attempt": "0"},
        {"--workflow-path": "build-research-image.yml"},
        {"--run-repository": "edu-llm"},
        {"--built-at": "2026-07-26T12:00:00"},
    ],
)
def test_malformed_run_context_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    overrides: dict[str, str],
) -> None:
    write_identity(tmp_path)

    exit_code = main(argv(tmp_path, **overrides))

    assert exit_code == 1
    assert capsys.readouterr().err.strip() == "invalid_provenance"
    assert not (tmp_path / "image-provenance.json").exists()


def test_an_unverified_source_identity_is_rejected(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_identity(tmp_path, verified=False)

    exit_code = main(argv(tmp_path))

    assert exit_code == 1
    assert capsys.readouterr().err.strip() == "invalid_source_identity"


def test_an_unregistered_repository_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_identity(tmp_path, repository="not-registered")

    exit_code = main(argv(tmp_path, **{"--repository": "not-registered"}))

    assert exit_code == 1
    assert capsys.readouterr().err.strip() == "unregistered_repository"
