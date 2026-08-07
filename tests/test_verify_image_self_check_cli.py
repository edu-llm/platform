"""The gate that lets a repository refuse its own image before anybody is billed for it.

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

from tools.verify_image_self_check import (
    FAILURE_OUTPUT_LINES,
    INTERPRETERS,
    MOUNT_POINT,
    PROBE,
    REJECTION_GUIDANCE,
    SELF_CHECK_PATH,
    SENTINEL,
    SelfCheckError,
    build_parser,
    main,
    probe_command,
    read_report,
    require_the_check_passed,
    self_check_directory,
)

IMAGE = (
    "123456789012.dkr.ecr.us-east-1.amazonaws.com/sbsandbox-intern-edullm-olmo-core:abcabcabcabc"
)


def sentinel_line(**fields: object) -> str:
    return f"{SENTINEL} {json.dumps(fields)}"


def a_repository_with_a_check(root: Path, body: str = "pass\n") -> Path:
    script = root / SELF_CHECK_PATH
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(body, encoding="utf-8")
    return root


def docker_stub(directory: Path, body: str) -> Path:
    """A ``docker`` on PATH that answers the probe without a daemon."""
    return write_stub(directory, "docker", body).parent


def run_main(stub_bin: Path, root: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setenv("PATH", f"{stub_bin}{os.pathsep}{os.defpath}")
    return main(["--image-reference", IMAGE, "--repository-root", str(root)])


# --------------------------------------------------------------------------------------
# Whether this repository asks anything at all.
# --------------------------------------------------------------------------------------


def test_a_repository_that_wrote_a_check_is_read_from_its_own_directory(tmp_path: Path) -> None:
    # Mutation: mount the repository root. The check would then see the whole checkout,
    # which is not what runs in the image and is a much larger thing to reason about.
    a_repository_with_a_check(tmp_path)

    assert self_check_directory(tmp_path) == tmp_path / ".edullm"


def test_a_repository_with_an_edullm_directory_and_no_check_asserts_nothing(
    tmp_path: Path,
) -> None:
    """Mutation: test for the directory rather than the file.

    Every registered repository has `.edullm/`, because that is where its Dockerfile lives.
    Mounting on the strength of the directory runs a check that is not there.
    """
    (tmp_path / ".edullm").mkdir()

    assert self_check_directory(tmp_path) is None


def test_a_check_that_is_a_directory_is_not_a_check(tmp_path: Path) -> None:
    # Mutation: `.exists()`. A directory at that path is a repository having done something
    # else, and running it would report a failure nobody caused.
    (tmp_path / SELF_CHECK_PATH).mkdir(parents=True)

    assert self_check_directory(tmp_path) is None


# --------------------------------------------------------------------------------------
# The verdict, which is the whole decision this tool makes.
# --------------------------------------------------------------------------------------


def test_a_check_that_passed_is_accepted() -> None:
    require_the_check_passed({"probe": "self_check", "version": 1, "outcome": "passed"})


def test_a_check_that_exited_non_zero_stops_the_build() -> None:
    """The one this file exists for, in the shape a deliberate refusal has.

    Mutation: accept any exit the check managed to reach. A check whose whole job is to
    exit 1 would then never stop anything.
    """
    with pytest.raises(SelfCheckError) as raised:
        require_the_check_passed({"outcome": "refused", "exit": 1})

    assert raised.value.reason == "self_check_refused"


def test_a_check_that_raised_stops_the_build_and_is_reported_apart() -> None:
    """An uncaught exception is the shape the OLMo-core finding actually has.

    `Attention.__init__` calls `assert_supported()`, which raises rather than exiting, so a
    check that merely constructs a model never reaches an exit statement at all. Mutation:
    fold this into `self_check_refused`, which loses the distinction between a check that
    decided something and a check that hit what it was looking for.
    """
    with pytest.raises(SelfCheckError) as raised:
        require_the_check_passed({"outcome": "raised", "error": "ImportError"})

    assert raised.value.reason == "self_check_raised"


def test_an_outcome_this_tool_does_not_recognise_is_refused_with_the_failures() -> None:
    # Mutation: treat an unknown outcome as a pass. The probe writes one of three words, so
    # a fourth means the sentinel came from something else in the image -- which establishes
    # nothing, and establishing nothing is what this gate refuses.
    with pytest.raises(SelfCheckError) as raised:
        require_the_check_passed({"outcome": "probably fine"})

    assert raised.value.reason == "self_check_unanswered"


def test_a_report_with_no_outcome_at_all_is_refused() -> None:
    with pytest.raises(SelfCheckError) as raised:
        require_the_check_passed({})

    assert raised.value.reason == "self_check_unanswered"


def test_every_refusal_carries_guidance_that_names_what_to_do() -> None:
    # Mutation: drop the guidance. A reason token alone sends an author to reproduce a
    # container build in order to learn which of three quite different things happened.
    for reason in ("self_check_refused", "self_check_raised", "self_check_unanswered"):
        assert SelfCheckError(reason).guidance == REJECTION_GUIDANCE[reason]
    assert SELF_CHECK_PATH in REJECTION_GUIDANCE["self_check_refused"]
    # The two constraints an author will otherwise write a check against and find out on a
    # runner: no device, and only their own directory mounted.
    assert "no GPU" in REJECTION_GUIDANCE["self_check_raised"]
    assert "no network" in REJECTION_GUIDANCE["self_check_raised"]


# --------------------------------------------------------------------------------------
# Reading the verdict back out of an image that is free to print whatever it likes.
# --------------------------------------------------------------------------------------


def test_the_sentinel_is_found_among_whatever_else_the_check_printed() -> None:
    # Mutation: parse the whole of stdout as JSON. A check that reports what it constructed
    # is a good check, and this has to survive it.
    report = read_report(
        "\n".join(
            [
                "constructing olmo3_1B",
                "constructing olmo3_7B",
                sentinel_line(outcome="passed"),
            ]
        )
    )

    assert report == {"outcome": "passed"}


def test_the_last_sentinel_wins_so_a_check_that_echoed_one_cannot_answer_first() -> None:
    # Mutation: take the first sentinel. The probe writes its line after the check has
    # finished, so anything earlier came from the check.
    report = read_report("\n".join([sentinel_line(outcome="passed"), sentinel_line(outcome="raised")]))

    assert report == {"outcome": "raised"}


def test_stdout_with_no_sentinel_is_no_answer_rather_than_an_empty_one() -> None:
    # Mutation: return {} when nothing matched, which reads downstream as an outcome.
    assert read_report("Traceback (most recent call last):\n  ...\n") is None


# --------------------------------------------------------------------------------------
# The probe itself, executed rather than read.
# --------------------------------------------------------------------------------------


def _run_probe(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    """The probe, against a check on this interpreter rather than in a container.

    The probe is the platform's own program and its whole job is to classify how somebody
    else's file ended, so it is worth running rather than reading.
    """
    a_repository_with_a_check(tmp_path, body)
    # The probe reads a fixed mount point, which does not exist here, so the source is
    # retargeted at the checkout. Everything the probe decides is downstream of this line.
    source = PROBE.replace(repr(MOUNT_POINT), repr(str(tmp_path / ".edullm")))
    return subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True, check=True, timeout=120
    )


def test_the_probe_reports_a_check_that_returned_normally_as_passed(tmp_path: Path) -> None:
    completed = _run_probe(tmp_path, "value = 1 + 1\n")

    assert read_report(completed.stdout) == {
        "probe": "self_check",
        "version": 1,
        "outcome": "passed",
    }


def test_the_probe_reports_a_zero_exit_as_passed_rather_than_as_a_refusal(
    tmp_path: Path,
) -> None:
    # Mutation: treat SystemExit as a failure whatever its code. `sys.exit(0)` at the end of
    # a `main()` is the ordinary way to write one of these, and would fail every build.
    completed = _run_probe(tmp_path, "import sys\nsys.exit(0)\n")

    assert read_report(completed.stdout) == {
        "probe": "self_check",
        "version": 1,
        "outcome": "passed",
    }


def test_the_probe_reports_a_non_zero_exit_as_a_refusal_and_keeps_the_code(
    tmp_path: Path,
) -> None:
    completed = _run_probe(tmp_path, "import sys\nsys.exit(3)\n")

    assert read_report(completed.stdout) == {
        "probe": "self_check",
        "version": 1,
        "outcome": "refused",
        "exit": 3,
    }


def test_the_probe_reports_a_raising_check_as_raised_and_prints_the_traceback(
    tmp_path: Path,
) -> None:
    """The OLMo-core shape: `assert_supported()` raises during construction.

    Mutation: let the exception escape the probe. Then no sentinel is written at all and
    the tool reports that no interpreter answered, which is a true statement about the
    wrong thing.
    """
    completed = _run_probe(tmp_path, "raise ImportError('flash_attn is not installed')\n")

    assert read_report(completed.stdout) == {
        "probe": "self_check",
        "version": 1,
        "outcome": "raised",
        "error": "ImportError",
    }
    assert "flash_attn is not installed" in completed.stderr


def test_the_probe_puts_the_checks_own_directory_on_the_import_path(tmp_path: Path) -> None:
    """Mutation: drop the sys.path insert.

    A check split into `.edullm/verify_image.py` and a helper beside it imports its sibling
    when it is run from a checkout, and would stop doing so here.
    """
    a_repository_with_a_check(tmp_path, "import rungs\nassert rungs.NAMES\n")
    (tmp_path / ".edullm" / "rungs.py").write_text("NAMES = ('olmo3_1B',)\n", encoding="utf-8")
    source = PROBE.replace(repr(MOUNT_POINT), repr(str(tmp_path / ".edullm")))

    completed = subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True, check=True, timeout=120
    )

    assert read_report(completed.stdout) == {
        "probe": "self_check",
        "version": 1,
        "outcome": "passed",
    }


# --------------------------------------------------------------------------------------
# The container this gate runs.
# --------------------------------------------------------------------------------------


def test_the_container_holds_no_network_no_card_and_is_removed(tmp_path: Path) -> None:
    """Mutation: drop --network none, or add --gpus.

    The network one is its sibling's argument. The device one is this tool's whole
    affordability claim: the runner has no card, so a gate that asked for one would fail
    every build, and a check that needs one belongs on a `*-check` workload profile.
    """
    command = probe_command(IMAGE, "python", tmp_path)

    assert command[:2] == ["docker", "run"]
    assert "--rm" in command
    assert command[command.index("--network") + 1] == "none"
    assert not any(token.startswith("--gpus") for token in command)


def test_the_check_is_mounted_read_only_from_the_checkout(tmp_path: Path) -> None:
    """Mutation: run a path inside the image instead of mounting the checkout.

    A Dockerfile is not obliged to COPY `.edullm/` into the image at all, so a gate reading
    an in-image path runs on some repositories and silently stops running on the rest.
    Mutation: drop `:ro`, and a check can rewrite itself into one that passes.
    """
    command = probe_command(IMAGE, "python", tmp_path)

    assert command[command.index("--volume") + 1] == f"{tmp_path}:{MOUNT_POINT}:ro"


def test_the_mount_point_cannot_shadow_a_directory_a_research_image_would_have() -> None:
    # Mutation: mount at /edullm. A research image is free to have one, and shadowing it
    # would change what the image is while claiming to inspect it.
    assert MOUNT_POINT != "/edullm"
    assert MOUNT_POINT.startswith("/edullm")


def test_the_interpreter_is_an_entrypoint_override_not_a_command(tmp_path: Path) -> None:
    # Mutation: pass the interpreter as the container command. These Dockerfiles inherit the
    # registered base's entrypoint deliberately, and it would swallow the probe.
    command = probe_command(IMAGE, "python3", tmp_path)

    assert command[command.index("--entrypoint") + 1] == "python3"
    assert command[-3:] == [IMAGE, "-c", PROBE]


def test_no_shell_stands_between_this_tool_and_the_probe(tmp_path: Path) -> None:
    # Mutation: build the command as a string for a shell. The probe is multi-line Python
    # and every quoting rule between here and it is a way to run something else.
    assert all(isinstance(token, str) for token in probe_command(IMAGE, "python", tmp_path))


def test_the_repository_root_is_required_rather_than_guessed(tmp_path: Path) -> None:
    # Mutation: default it to the working directory. This tool runs from a subshell cd'd
    # into the platform tooling checkout, where `.edullm/` is somebody else's.
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--image-reference", IMAGE])


# --------------------------------------------------------------------------------------
# End to end, with a docker that answers without a daemon.
# --------------------------------------------------------------------------------------


@pytest.mark.slow
def test_a_repository_that_asserts_nothing_passes_and_the_state_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: refuse a repository with no check.

    Five repositories are registered today and none of them has one, so that mutation
    reddens every build on the platform. Mutation the other way: print nothing, and "this
    repository asserts nothing about its image" becomes indistinguishable from a pass.
    """
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()

    assert run_main(empty_bin, tmp_path, monkeypatch) == 0
    assert capsys.readouterr().out.strip() == f"self_check_verified:absent:{SELF_CHECK_PATH}"


@pytest.mark.slow
def test_a_check_that_passes_lets_the_build_go_on_quietly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    a_repository_with_a_check(tmp_path)
    stub_bin = docker_stub(tmp_path / "bin", f"echo '{sentinel_line(outcome='passed')}'\n")

    assert run_main(stub_bin, tmp_path, monkeypatch) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == f"self_check_verified:passed:{SELF_CHECK_PATH}"
    assert captured.err == ""


@pytest.mark.slow
def test_a_check_that_refuses_stops_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The end-to-end form of the refusal that saves an allocation.

    Mutation: exit 0 on a refusal. The tool would print the reason and publish anyway.
    """
    a_repository_with_a_check(tmp_path)
    stub_bin = docker_stub(
        tmp_path / "bin",
        f"echo '{sentinel_line(outcome='raised', error='ImportError')}'\n",
    )

    assert run_main(stub_bin, tmp_path, monkeypatch) == 1
    assert capsys.readouterr().err.splitlines()[0] == "self_check_raised"


@pytest.mark.slow
def test_what_a_failing_check_said_is_reproduced_so_nobody_rebuilds_to_read_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """This is where the gate diverges from its accelerator sibling, deliberately.

    That one prints nothing the image said, because it runs the platform's program in
    somebody else's image. This runs the caller's own committed file, and its output is the
    entire product of the check. Mutation: suppress it, and an author has to rebuild a
    container to read a traceback that already exists.
    """
    a_repository_with_a_check(tmp_path)
    stub_bin = docker_stub(
        tmp_path / "bin",
        "echo 'constructing olmo3_1B'\n"
        "echo \"ImportError: flash_attn is required for attn_backend=flash_2\" >&2\n"
        f"echo '{sentinel_line(outcome='raised', error='ImportError')}'\n",
    )

    assert run_main(stub_bin, tmp_path, monkeypatch) == 1
    errors = capsys.readouterr().err
    assert "constructing olmo3_1B" in errors
    assert "flash_attn is required" in errors
    # The verdict line is this tool talking to itself and does not belong in the middle of
    # what the check said.
    assert SENTINEL not in errors


@pytest.mark.slow
def test_a_flood_of_output_is_bounded_at_the_tail_where_the_cause_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Mutation: keep the head. A traceback ends with the line that raised, so a head-bounded
    # log reproduces the least useful part of it.
    a_repository_with_a_check(tmp_path)
    stub_bin = docker_stub(
        tmp_path / "bin",
        f"seq 1 {FAILURE_OUTPUT_LINES * 2}\n"
        f"echo '{sentinel_line(outcome='refused', exit=1)}'\n",
    )

    assert run_main(stub_bin, tmp_path, monkeypatch) == 1
    errors = capsys.readouterr().err
    assert str(FAILURE_OUTPUT_LINES * 2) in errors
    assert "\n1\n" not in errors


@pytest.mark.slow
def test_a_second_interpreter_is_tried_when_the_first_is_not_in_the_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: try only ``python``.

    An image whose interpreter is only on PATH as ``python3`` would then be refused for
    having no answer, which is a red build on a correct image.
    """
    a_repository_with_a_check(tmp_path)
    stub_bin = docker_stub(
        tmp_path / "bin",
        'if [[ " $* " == *" --entrypoint python3 "* ]]; then\n'
        f"  echo '{sentinel_line(outcome='passed')}'\n"
        "else\n"
        '  echo "docker: executable file not found in $PATH" >&2\n'
        "  exit 127\n"
        "fi\n",
    )

    assert run_main(stub_bin, tmp_path, monkeypatch) == 0
    assert INTERPRETERS.index("python") < INTERPRETERS.index("python3")


@pytest.mark.slow
def test_an_image_that_answers_nothing_is_refused_with_the_ones_that_answer_wrongly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: pass when no interpreter answered.

    That is the fail-open spelling, and it is reachable from any image: an entrypoint that
    swallows the probe produces exactly this.
    """
    a_repository_with_a_check(tmp_path)
    stub_bin = docker_stub(tmp_path / "bin", "exit 127\n")

    assert run_main(stub_bin, tmp_path, monkeypatch) == 1
    assert capsys.readouterr().err.splitlines()[0] == "self_check_unanswered"


@pytest.mark.slow
def test_a_docker_that_is_not_installed_is_a_broken_runner_not_a_bad_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Exit 2 is this repository's code for tooling that could not be driven, and it matters:
    # 1 would read as "this repository refused its image", which is a claim nothing made.
    a_repository_with_a_check(tmp_path)
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    assert main(["--image-reference", IMAGE, "--repository-root", str(tmp_path)]) == 2
    assert capsys.readouterr().err.splitlines()[0] == "docker_unavailable"
