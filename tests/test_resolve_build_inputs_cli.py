from __future__ import annotations

from pathlib import Path

import pytest

from tools.resolve_build_inputs import (
    UnsafeStepOutputError,
    main,
    require_digest_pinned_reference,
    require_output_safe,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "repositories.yaml"
BASE_DIGEST = "sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"


def argv(tmp_path: Path, **overrides: str) -> list[str]:
    arguments: dict[str, str] = {
        "--registry": str(REGISTRY_PATH),
        "--repository": "OLMo-core",
        "--github-output": str(tmp_path / "step-output.txt"),
    }
    arguments.update(overrides)
    return [token for pair in arguments.items() for token in pair]


def test_registered_repository_emits_exactly_the_build_inputs_the_workflow_consumes(
    tmp_path: Path,
) -> None:
    # The ECR repository name is not among them. The publish job takes it from the gate
    # job's output, so a copy emitted here was only ever a second way to be wrong.
    assert main(argv(tmp_path)) == 0

    assert (tmp_path / "step-output.txt").read_text(encoding="utf-8") == (
        f"base_reference=docker.io/library/python@{BASE_DIGEST}\n"
        "dockerfile_path=.edullm/Dockerfile\n"
        "build_context=.\n"
    )


def test_a_registry_file_that_is_not_utf_8_fails_closed_without_a_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # UnicodeDecodeError is a ValueError, so it slipped past the ValidationError branch
    # and reached the runner log as a traceback naming the full path.
    registry = tmp_path / "repositories.yaml"
    registry.write_bytes(b"repositories:\n  - repository: \xff\xfe\n")

    exit_code = main(argv(tmp_path, **{"--registry": str(registry)}))
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err.strip() == "registry_unreadable"
    assert captured.out == ""
    assert str(tmp_path) not in captured.err


def test_step_outputs_are_appended_rather_than_truncated(tmp_path: Path) -> None:
    step_output = tmp_path / "step-output.txt"
    step_output.write_text("previous=kept\n", encoding="utf-8")

    assert main(argv(tmp_path)) == 0
    assert step_output.read_text(encoding="utf-8").startswith("previous=kept\n")


def test_an_unregistered_repository_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(argv(tmp_path, **{"--repository": "not-registered"}))

    assert exit_code == 1
    assert capsys.readouterr().err.strip() == "unregistered_repository"
    assert not (tmp_path / "step-output.txt").exists()


def test_a_missing_registry_file_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(argv(tmp_path, **{"--registry": str(tmp_path / "absent.yaml")}))

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "registry_unreadable"


@pytest.mark.parametrize(
    "reference",
    [
        "docker.io/library/python",
        "docker.io/library/python:3.12",
        "docker.io/library/python@sha512:" + "a" * 128,
        "docker.io/library/python@sha256:" + "A" * 64,
        "docker.io/library/python@sha256:" + "a" * 63,
        "docker.io/library/python@sha256:" + "a" * 65,
        "@sha256:" + "a" * 64,
        "docker.io/library/python:3.12@sha256:" + "a" * 64,
    ],
)
def test_only_digest_pinned_base_references_are_accepted(reference: str) -> None:
    with pytest.raises(ValueError):
        require_digest_pinned_reference(reference)


def test_a_digest_pinned_base_reference_round_trips() -> None:
    reference = f"docker.io/library/python@{BASE_DIGEST}"

    assert require_digest_pinned_reference(reference) == reference


@pytest.mark.parametrize(
    "value",
    ["one\ntwo", "one\rtwo", "trailing\n", "form\x0cfeed", "null\x00byte"],
)
def test_step_output_values_may_not_smuggle_extra_lines(value: str) -> None:
    with pytest.raises(UnsafeStepOutputError):
        require_output_safe("dockerfile_path", value)


def test_ordinary_step_output_values_are_returned_unchanged() -> None:
    assert require_output_safe("build_context", ".") == "."
