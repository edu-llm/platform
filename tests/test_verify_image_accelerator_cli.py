"""The gate that keeps a CPU-only torch out of the registry.

Every test names in its docstring or its comment the mutation it was written against, so
that a change which weakens the gate turns one of them red rather than passing quietly.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from workflow_support import write_stub

from tools.verify_image_accelerator import (
    INTERPRETERS,
    PROBE,
    REJECTION_GUIDANCE,
    SENTINEL,
    AcceleratorError,
    main,
    probe_command,
    read_report,
    require_reachable_accelerator,
)

IMAGE = (
    "123456789012.dkr.ecr.us-east-1.amazonaws.com/sbsandbox-intern-edullm-olmo-core:abcabcabcabc"
)


def sentinel_line(**fields: object) -> str:
    return f"{SENTINEL} {json.dumps(fields)}"


def docker_stub(directory: Path, body: str) -> Path:
    """A ``docker`` on PATH that answers the probe without a daemon."""
    return write_stub(directory, "docker", body).parent


def run_main(stub_bin: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setenv("PATH", f"{stub_bin}{os.pathsep}{os.defpath}")
    return main(["--image-reference", IMAGE])


# --------------------------------------------------------------------------------------
# The judgement, which is the whole decision this tool makes.
# --------------------------------------------------------------------------------------


def test_a_cuda_build_is_accepted_and_says_which_state_it_accepted() -> None:
    # Mutation: return a bare True, so "there was no torch to check" and "the torch here
    # can reach a card" become the same answer in the log.
    accepted = require_reachable_accelerator({"torch": "2.9.0+cu128", "cuda": "12.8", "hip": None})

    assert accepted == "cuda"


def test_a_rocm_build_is_accepted_because_cuda_is_not_the_only_gpu() -> None:
    # Mutation: test only torch.version.cuda. A ROCm wheel leaves cuda None and sets hip,
    # so that mutation refuses a working GPU image for being the wrong vendor.
    accepted = require_reachable_accelerator({"torch": "2.9.0+rocm6.4", "cuda": None, "hip": "6.4"})

    assert accepted == "rocm"


def test_an_image_with_no_torch_is_accepted_untouched() -> None:
    # edullm-data publishes exactly this: boto3 and numpy, no torch, and twenty images in
    # the registry. Mutation: demand torch of every image, which reddens that repository
    # and every future one like it for a hazard it cannot have.
    accepted = require_reachable_accelerator({"torch": None})

    assert accepted == "torch_absent"


def test_a_cpu_only_torch_is_refused() -> None:
    """The one this file exists for.

    Mutation: accept when torch merely imports. That is what makes the failure silent --
    the CPU wheel imports perfectly.
    """
    with pytest.raises(AcceleratorError) as raised:
        require_reachable_accelerator({"torch": "2.9.0+cpu", "cuda": None, "hip": None})

    assert raised.value.reason == "cpu_only_torch"


def test_the_cpu_refusal_carries_the_guidance_that_names_the_causes() -> None:
    # Mutation: drop the guidance. The reason token alone sends somebody to read a
    # Dockerfile that is correct; the causes are an index URL, a lockfile, a later
    # install, or an arm64 runner.
    error = AcceleratorError("cpu_only_torch")

    assert error.guidance == REJECTION_GUIDANCE["cpu_only_torch"]
    assert "PEP 440" in error.guidance
    assert "arm64" in error.guidance


def test_an_empty_cuda_string_is_a_cpu_build() -> None:
    # Mutation: test `is not None`. An empty string is not None and is not a CUDA runtime.
    with pytest.raises(AcceleratorError) as raised:
        require_reachable_accelerator({"torch": "2.9.0", "cuda": "", "hip": ""})

    assert raised.value.reason == "cpu_only_torch"


def test_a_torch_that_will_not_import_is_refused_rather_than_read_as_absent() -> None:
    # Mutation: treat every failed import as "no torch here". An image whose torch does
    # not load would then publish, and fail on a paid instance instead of on this runner.
    with pytest.raises(AcceleratorError) as raised:
        require_reachable_accelerator({"torch": "unimportable", "error": "ImportError"})

    assert raised.value.reason == "torch_unimportable"


def test_a_report_whose_torch_field_is_not_a_string_is_refused() -> None:
    # Mutation: trust the payload's shape. It comes out of somebody else's image.
    with pytest.raises(AcceleratorError) as raised:
        require_reachable_accelerator({"torch": 29, "cuda": "12.8"})

    assert raised.value.reason == "image_probe_unanswered"


# --------------------------------------------------------------------------------------
# Reading the answer back out of an image that is free to print whatever it likes.
# --------------------------------------------------------------------------------------


def test_the_sentinel_is_found_among_whatever_else_the_image_prints() -> None:
    # Mutation: parse the whole of stdout as JSON, or take the first line. A base image is
    # free to print a banner, and a warning routed to stdout is ordinary.
    report = read_report(
        "\n".join(
            [
                "Warning: something the base image wanted to say",
                sentinel_line(torch="2.9.0", cuda="12.8"),
                "and something after it",
            ]
        )
    )

    assert report == {"torch": "2.9.0", "cuda": "12.8"}


def test_the_last_sentinel_wins_so_an_echoed_one_cannot_answer_first() -> None:
    # Mutation: take the first sentinel. The probe writes its line after every import has
    # run, so anything earlier came from the image.
    report = read_report(
        "\n".join([sentinel_line(torch=None), sentinel_line(torch="2.9.0", cuda="12.8")])
    )

    assert report == {"torch": "2.9.0", "cuda": "12.8"}


def test_stdout_with_no_sentinel_is_no_answer_rather_than_an_empty_one() -> None:
    # Mutation: return {} when nothing matched, which reads downstream as "no torch".
    assert read_report("Traceback (most recent call last):\n  ...\n") is None


def test_a_sentinel_line_that_is_not_json_is_not_an_answer() -> None:
    assert read_report(f"{SENTINEL} not json at all") is None


def test_a_sentinel_line_that_is_json_but_not_an_object_is_not_an_answer() -> None:
    assert read_report(f"{SENTINEL} [1, 2, 3]") is None


# --------------------------------------------------------------------------------------
# The probe itself, executed rather than read.
# --------------------------------------------------------------------------------------


def test_the_probe_runs_and_reports_this_interpreter_which_has_no_torch() -> None:
    """The probe program is run, not just parsed.

    This interpreter is the platform's own environment, which carries no torch, so the
    honest answer is the absent one -- and getting it proves the source string executes,
    the sentinel is written, and the ModuleNotFoundError branch is reachable. Mutation:
    any syntax error, or a probe that writes nothing when torch is missing.
    """
    completed = subprocess.run(
        [sys.executable, "-c", PROBE], capture_output=True, text=True, check=True, timeout=120
    )

    assert read_report(completed.stdout) == {
        "probe": "accelerator",
        "version": 1,
        "torch": None,
    }


def test_the_probe_never_puts_an_exception_message_in_the_report() -> None:
    # Mutation: report str(exc). An exception out of somebody else's image can carry a
    # path or a token, and this log is world readable.
    assert "str(exc)" not in PROBE
    assert "type(exc).__name__" in PROBE


def test_a_torch_whose_extension_modules_are_missing_is_not_read_as_absent(
    tmp_path: Path,
) -> None:
    """Mutation: catch ModuleNotFoundError without checking which module was not found.

    A torch whose compiled extension did not unpack raises ModuleNotFoundError naming
    ``torch._C``, not ``torch``. Reading that as "this image has no torch" publishes it.
    An importable package on the path rather than a meta-path hook, because it is the
    shape a real broken install has.
    """
    package = tmp_path / "torch"
    package.mkdir()
    (package / "__init__.py").write_text("from torch._C import *  # noqa\n", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
    )

    assert read_report(completed.stdout) == {
        "probe": "accelerator",
        "version": 1,
        "torch": "unimportable",
        "error": "ModuleNotFoundError",
    }


# --------------------------------------------------------------------------------------
# The container this gate runs.
# --------------------------------------------------------------------------------------


def test_the_probe_container_holds_no_network_and_is_removed() -> None:
    # Mutation: drop --network none, so a probe in somebody else's image can reach out;
    # or drop --rm, so a build leaves containers behind on a shared runner.
    command = probe_command(IMAGE, "python")

    assert command[:2] == ["docker", "run"]
    assert "--rm" in command
    assert command[command.index("--network") + 1] == "none"


def test_the_interpreter_is_an_entrypoint_override_not_a_command() -> None:
    """Mutation: pass the interpreter as the container command.

    These Dockerfiles inherit the registered base's entrypoint deliberately, and an
    inherited entrypoint would receive the probe as arguments instead of running it.
    """
    command = probe_command(IMAGE, "python3")

    assert command[command.index("--entrypoint") + 1] == "python3"
    assert command[-3:] == [IMAGE, "-c", PROBE]


def test_no_shell_stands_between_this_tool_and_the_probe() -> None:
    # Mutation: build the command as a string for a shell. The probe is multi-line Python
    # and every quoting rule between here and it is a way to run something else.
    assert all(isinstance(token, str) for token in probe_command(IMAGE, "python"))


# --------------------------------------------------------------------------------------
# End to end, with a docker that answers without a daemon.
# --------------------------------------------------------------------------------------


@pytest.mark.slow
def test_an_image_with_a_cuda_torch_passes_and_the_build_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stub_bin = docker_stub(
        tmp_path / "bin", f"echo '{sentinel_line(torch='2.9.0', cuda='12.8')}'\n"
    )

    assert run_main(stub_bin, monkeypatch) == 0
    assert capsys.readouterr().out.strip() == "accelerator_verified:cuda"


@pytest.mark.slow
def test_an_image_with_a_cpu_torch_stops_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The end-to-end form of the only refusal that saves money.

    Mutation: exit 0 on a refusal. The tool would print the reason and publish anyway.
    """
    stub_bin = docker_stub(
        tmp_path / "bin", f"echo '{sentinel_line(torch='2.9.0+cpu', cuda=None, hip=None)}'\n"
    )

    assert run_main(stub_bin, monkeypatch) == 1
    captured = capsys.readouterr()
    assert captured.err.splitlines()[0] == "cpu_only_torch"


@pytest.mark.slow
def test_a_second_interpreter_is_tried_when_the_first_is_not_in_the_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: try only ``python``.

    An image whose interpreter is only on PATH as ``python3`` would then be refused for
    having no answer, which is a red build on a correct image.
    """
    stub_bin = docker_stub(
        tmp_path / "bin",
        'if [[ " $* " == *" --entrypoint python3 "* ]]; then\n'
        f"  echo '{sentinel_line(torch='2.9.0', cuda='12.8')}'\n"
        "else\n"
        '  echo "docker: executable file not found in $PATH" >&2\n'
        "  exit 127\n"
        "fi\n",
    )

    assert run_main(stub_bin, monkeypatch) == 0
    assert INTERPRETERS.index("python") < INTERPRETERS.index("python3")


@pytest.mark.slow
def test_an_image_that_answers_nothing_is_refused_with_the_ones_that_answer_wrongly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: pass when no interpreter answered.

    That is the fail-open spelling, and it is reachable from any image: an entrypoint that
    swallows the probe produces exactly this.
    """
    stub_bin = docker_stub(tmp_path / "bin", "exit 127\n")

    assert run_main(stub_bin, monkeypatch) == 1
    assert capsys.readouterr().err.splitlines()[0] == "image_probe_unanswered"


@pytest.mark.slow
def test_neither_the_image_reference_nor_the_container_output_reaches_the_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: print the subprocess streams to help somebody debug.

    The runner log is world readable for any public caller repository, the reference
    carries the account id, and the streams are somebody else's image talking.
    """
    stub_bin = docker_stub(
        tmp_path / "bin",
        'echo "a secret the image printed"\n'
        f"echo '{sentinel_line(torch='2.9.0+cpu', cuda=None)}'\n"
        'echo "a secret on stderr" >&2\n',
    )

    run_main(stub_bin, monkeypatch)
    captured = capsys.readouterr()
    everything = captured.out + captured.err

    assert IMAGE not in everything
    assert "a secret" not in everything


@pytest.mark.slow
def test_a_docker_that_is_not_installed_is_a_broken_runner_not_a_bad_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Exit 2 is this repository's code for tooling that could not be driven, and it
    # matters: 1 would read as "this image is wrong", which is a claim nothing made.
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    assert main(["--image-reference", IMAGE]) == 2
    assert capsys.readouterr().err.splitlines()[0] == "docker_unavailable"
