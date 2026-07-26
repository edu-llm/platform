from __future__ import annotations

from pathlib import Path

import pytest

from tools.verify_dockerfile_base import (
    DockerfileBaseError,
    main,
    require_base_image_contract,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "repositories.yaml"
DOCKERFILE_PATH = ".edullm/Dockerfile"


def write_dockerfile(tmp_path: Path, body: str) -> Path:
    source = tmp_path / "source"
    dockerfile = source / DOCKERFILE_PATH
    dockerfile.parent.mkdir(parents=True, exist_ok=True)
    dockerfile.write_text(body, encoding="utf-8")
    return source


def argv(tmp_path: Path, **overrides: str) -> list[str]:
    arguments: dict[str, str] = {
        "--registry": str(REGISTRY_PATH),
        "--repository": "OLMo-core",
        "--repository-root": str(tmp_path / "source"),
    }
    arguments.update(overrides)
    return [token for pair in arguments.items() for token in pair]


ACCEPTED = {
    "the smallest possible contract": "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n",
    "an unbraced expansion": "ARG BASE_IMAGE\nFROM $BASE_IMAGE\n",
    "lowercase keywords": "arg BASE_IMAGE\nfrom ${BASE_IMAGE}\n",
    "mixed case keywords": "Arg BASE_IMAGE\nFrom ${BASE_IMAGE}\n",
    "comments and blank lines": (
        "# syntax=docker/dockerfile:1\n"
        "\n"
        "# The platform passes the registered digest.\n"
        "ARG BASE_IMAGE\n"
        "\n"
        "FROM ${BASE_IMAGE}\n"
        "RUN echo hello\n"
    ),
    "a platform flag": "ARG BASE_IMAGE\nFROM --platform=$BUILDPLATFORM ${BASE_IMAGE} AS build\n",
    "a line continuation": "ARG BASE_IMAGE\nFROM \\\n  ${BASE_IMAGE} \\\n  AS build\n",
    "a comment inside a continuation": (
        "ARG BASE_IMAGE\nFROM \\\n  # chosen by the platform\n  ${BASE_IMAGE}\n"
    ),
    "every stage on the registered base": (
        "ARG BASE_IMAGE\n"
        "FROM ${BASE_IMAGE} AS build\n"
        "RUN make wheel\n"
        "FROM ${BASE_IMAGE} AS runtime\n"
        "COPY --from=build /wheel /wheel\n"
    ),
    "a later stage extending an earlier one by name": (
        "ARG BASE_IMAGE\n"
        "FROM ${BASE_IMAGE} AS build\n"
        "RUN make wheel\n"
        "FROM build AS test\n"
        "RUN pytest\n"
        "FROM ${BASE_IMAGE} AS runtime\n"
        "COPY --from=test /wheel /wheel\n"
    ),
    "a stage name referenced in another case": (
        "ARG BASE_IMAGE\nFROM ${BASE_IMAGE} AS Build\nFROM bUiLd AS final\n"
    ),
    "several names on one global ARG": "ARG TARGET BASE_IMAGE\nFROM ${BASE_IMAGE}\n",
}

REJECTED = {
    "no ARG at all": ("FROM ${BASE_IMAGE}\n", "missing_base_image_arg"),
    "an ARG that arrives too late": (
        "FROM ${BASE_IMAGE}\nARG BASE_IMAGE\n",
        "missing_base_image_arg",
    ),
    "an ARG declared inside a stage": (
        "ARG OTHER\nFROM ${BASE_IMAGE} AS build\nARG BASE_IMAGE\n",
        "missing_base_image_arg",
    ),
    "a differently spelled ARG": ("ARG base_image\nFROM ${BASE_IMAGE}\n", "missing_base_image_arg"),
    "a default that could stand in for the registered base": (
        "ARG BASE_IMAGE=docker.io/library/python@sha256:" + "a" * 64 + "\nFROM ${BASE_IMAGE}\n",
        "base_image_arg_has_default",
    ),
    "an empty default": ("ARG BASE_IMAGE=\nFROM ${BASE_IMAGE}\n", "base_image_arg_has_default"),
    "a literal tag": ("ARG BASE_IMAGE\nFROM python:3.12\n", "unregistered_base_image"),
    "a literal digest": (
        "ARG BASE_IMAGE\nFROM docker.io/library/python@sha256:" + "a" * 64 + "\n",
        "unregistered_base_image",
    ),
    "scratch": ("ARG BASE_IMAGE\nFROM scratch\n", "unregistered_base_image"),
    "a literal first stage that a later stage papers over": (
        "ARG BASE_IMAGE\nFROM python:3.12 AS build\nFROM ${BASE_IMAGE} AS runtime\n",
        "unregistered_base_image",
    ),
    "an unregistered builder stage": (
        "ARG BASE_IMAGE\nFROM ${BASE_IMAGE} AS runtime\nFROM golang:1.24 AS tools\n",
        "unregistered_base_image",
    ),
    "a different variable": ("ARG BASE_IMAGE\nFROM ${OTHER_IMAGE}\n", "unregistered_base_image"),
    "a stage referenced before it exists": (
        "ARG BASE_IMAGE\nFROM runtime AS build\nFROM ${BASE_IMAGE} AS runtime\n",
        "unregistered_base_image",
    ),
    "a base wrapped in a larger reference": (
        "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}-slim\n",
        "unregistered_base_image",
    ),
    "nothing to build": ("ARG BASE_IMAGE\nRUN echo hello\n", "no_build_stage"),
    "an empty file": ("", "no_build_stage"),
    "a FROM with no image": ("ARG BASE_IMAGE\nFROM\n", "malformed_from_instruction"),
    "a FROM that is only flags": (
        "ARG BASE_IMAGE\nFROM --platform=linux/amd64\n",
        "malformed_from_instruction",
    ),
    "a dangling AS": ("ARG BASE_IMAGE\nFROM ${BASE_IMAGE} AS\n", "malformed_from_instruction"),
    "junk after the stage name": (
        "ARG BASE_IMAGE\nFROM ${BASE_IMAGE} AS build extra\n",
        "malformed_from_instruction",
    ),
    "a continuation that never ends": (
        "ARG BASE_IMAGE\nFROM ${BASE_IMAGE} \\\n",
        "unterminated_line_continuation",
    ),
}


@pytest.mark.parametrize("body", ACCEPTED.values(), ids=list(ACCEPTED))
def test_a_dockerfile_that_derives_every_stage_from_the_registered_base_is_accepted(
    body: str,
) -> None:
    require_base_image_contract(body)


@pytest.mark.parametrize(
    ("body", "reason"),
    REJECTED.values(),
    ids=list(REJECTED),
)
def test_a_dockerfile_that_could_build_from_an_unregistered_base_is_rejected(
    body: str,
    reason: str,
) -> None:
    with pytest.raises(DockerfileBaseError) as raised:
        require_base_image_contract(body)

    assert raised.value.reason == reason


def test_the_registered_dockerfile_is_read_from_the_checkout(tmp_path: Path) -> None:
    write_dockerfile(tmp_path, "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n")

    assert main(argv(tmp_path)) == 0


def test_a_rejected_dockerfile_stops_the_run_with_only_a_machine_readable_reason(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_dockerfile(tmp_path, "ARG BASE_IMAGE\nFROM python:3.12\n")

    exit_code = main(argv(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.err.strip() == "unregistered_base_image"
    assert captured.out == ""
    assert str(tmp_path) not in captured.err


def test_a_missing_dockerfile_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "source").mkdir()

    exit_code = main(argv(tmp_path))

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "dockerfile_unreadable"


def test_a_dockerfile_that_is_not_utf_8_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = write_dockerfile(tmp_path, "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n")
    (source / DOCKERFILE_PATH).write_bytes(b"ARG BASE_IMAGE\nFROM \xff\xfe\n")

    exit_code = main(argv(tmp_path))

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "dockerfile_undecodable"


def test_a_dockerfile_symlinked_out_of_the_checkout_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outside = tmp_path / "outside.Dockerfile"
    outside.write_text("ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n", encoding="utf-8")
    source = write_dockerfile(tmp_path, "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n")
    dockerfile = source / DOCKERFILE_PATH
    dockerfile.unlink()
    dockerfile.symlink_to(outside)

    exit_code = main(argv(tmp_path))

    assert exit_code == 1
    assert capsys.readouterr().err.strip() == "dockerfile_outside_repository"


def test_an_unregistered_repository_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_dockerfile(tmp_path, "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n")

    exit_code = main(argv(tmp_path, **{"--repository": "not-registered"}))

    assert exit_code == 1
    assert capsys.readouterr().err.strip() == "unregistered_repository"


def test_a_missing_registry_file_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_dockerfile(tmp_path, "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n")

    exit_code = main(argv(tmp_path, **{"--registry": str(tmp_path / "absent.yaml")}))

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "registry_unreadable"
