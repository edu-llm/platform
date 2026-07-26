from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
        "--image-created": "2026-07-26T12:00:00.000000Z",
        "--output": str(tmp_path / "image-provenance.json"),
    }
    arguments.update(overrides)
    return [token for pair in arguments.items() for token in pair]


def written_provenance(tmp_path: Path) -> dict[str, Any]:
    payload = json.loads((tmp_path / "image-provenance.json").read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


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


def test_built_at_is_the_images_own_creation_time_not_the_moment_of_this_run(
    tmp_path: Path,
) -> None:
    # The resumed-run case: the image was published months ago and this run only reads it
    # back. A record that dated the build to now would state a time nothing happened at.
    write_identity(tmp_path)
    created = "2026-01-04T03:02:01.000000Z"

    assert main(argv(tmp_path, **{"--image-created": created})) == 0

    assert written_provenance(tmp_path)["built_at"] == created


@pytest.mark.parametrize(
    ("created", "recorded"),
    [
        ("2026-07-26T12:00:00.026260339Z", "2026-07-26T12:00:00.026260Z"),
        ("2026-07-26T12:00:00.999999999Z", "2026-07-26T12:00:00.999999Z"),
        ("2026-07-26T12:00:00Z", "2026-07-26T12:00:00.000000Z"),
        ("2026-07-26T07:00:00.000000-05:00", "2026-07-26T12:00:00.000000Z"),
    ],
    ids=["nanoseconds", "nanoseconds that would round up", "no fraction", "an offset"],
)
def test_a_nanosecond_creation_time_is_truncated_rather_than_rounded(
    tmp_path: Path,
    created: str,
    recorded: str,
) -> None:
    # An image configuration's `created` is RFC 3339 with nanoseconds and the contract's
    # timestamp type carries microseconds. Truncating keeps built_at at or before the
    # moment the image was created; rounding could place it after.
    write_identity(tmp_path)

    assert main(argv(tmp_path, **{"--image-created": created})) == 0

    assert written_provenance(tmp_path)["built_at"] == recorded


def test_the_image_creation_time_cannot_be_left_to_a_clock(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # There is deliberately no default. A tool that can invent a build time will, and the
    # invented one is wrong on exactly the run where nobody is watching.
    write_identity(tmp_path)
    without = [token for token in argv(tmp_path) if token != "--image-created"]
    without.remove("2026-07-26T12:00:00.000000Z")

    with pytest.raises(SystemExit) as raised:
        main(without)

    assert raised.value.code == 2
    assert "--image-created" in capsys.readouterr().err
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
        {"--image-created": "2026-07-26T12:00:00"},
        {"--image-created": ""},
        {"--image-created": "not a timestamp"},
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
