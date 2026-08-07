"""The gate that lets a repository refuse its own image before anybody is billed for it.

Every test names in its docstring or its comment the mutation it was written against, so
that a change which weakens the gate turns one of them red rather than passing quietly.

The differential half of this file -- the two passes and their disagreement -- is held to a
higher standard than the rest, because the check it defends against is the one a reasonable
person writes first and the one OLMo-core has been running green.
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
    PRETENDED_DEVICE_LIMIT,
    REJECTION_GUIDANCE,
    SELF_CHECK_PATH,
    SENTINEL,
    Pass,
    SelfCheckError,
    build_parser,
    main,
    probe_command,
    probe_source,
    read_report,
    reconcile_the_two_passes,
    self_check_directory,
    verdict_of,
)

IMAGE = (
    "123456789012.dkr.ecr.us-east-1.amazonaws.com/sbsandbox-intern-edullm-olmo-core:abcabcabcabc"
)


def sentinel_line(**fields: object) -> str:
    return f"{SENTINEL} {json.dumps(fields)}"


def a_pass(*, outcome: str, pretended: bool = False, said: str = "") -> Pass:
    return Pass(
        report={"outcome": outcome, "pretended": pretended},
        stdout=said,
        stderr="",
    )


def a_repository_with_a_check(root: Path, body: str = "pass\n") -> Path:
    script = root / SELF_CHECK_PATH
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(body, encoding="utf-8")
    return root


def docker_stub(directory: Path, body: str) -> Path:
    """A ``docker`` on PATH that answers the probe without a daemon."""
    return write_stub(directory, "docker", body).parent


def a_docker_that_answers(directory: Path, *, honest: str, pretended: str) -> Path:
    """A docker telling the two passes apart the way the probe source does.

    The pretend pass is the only one whose program mentions the patched function, so the
    stub can key on that without this test knowing anything else about the probe.
    """
    return docker_stub(
        directory,
        f'if [[ "$*" == *"{PRETENDED_DEVICE_LIMIT} = lambda"* ]]; then\n'
        f"  {pretended}\n"
        "else\n"
        f"  {honest}\n"
        "fi\n",
    )


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
# One pass, judged on its own.
# --------------------------------------------------------------------------------------


def test_a_pass_that_passed_has_no_verdict_against_it() -> None:
    assert verdict_of({"outcome": "passed"}) is None


def test_a_non_zero_exit_is_a_repository_refusing_its_own_image() -> None:
    assert verdict_of({"outcome": "refused", "exit": 1}) == "self_check_refused"


def test_a_raising_check_is_reported_apart_from_a_deliberate_refusal() -> None:
    """An uncaught exception is the shape a real finding has.

    `assert_supported()` raises rather than exiting, so a check that asserts a backend never
    reaches an exit statement at all. Mutation: fold this into `self_check_refused`, which
    loses the difference between a check that decided something and one that hit what it was
    looking for.
    """
    assert verdict_of({"outcome": "raised", "error": "ImportError"}) == "self_check_raised"


def test_an_outcome_this_tool_does_not_recognise_fails() -> None:
    # Mutation: treat an unknown outcome as a pass. The probe writes one of three words, so a
    # fourth means the sentinel came from something else in the image -- which establishes
    # nothing, and establishing nothing is what this gate refuses.
    assert verdict_of({"outcome": "probably fine"}) == "self_check_unanswered"
    assert verdict_of({}) == "self_check_unanswered"


# --------------------------------------------------------------------------------------
# The differential, which is the whole of what this gate learned the hard way.
# --------------------------------------------------------------------------------------


def test_a_check_that_answers_the_same_either_way_is_accepted() -> None:
    accepted, _ = reconcile_the_two_passes(
        a_pass(outcome="passed"), a_pass(outcome="passed", pretended=True)
    )

    assert accepted == "device_independent"


def test_a_check_that_passes_only_without_a_card_is_refused() -> None:
    """THE ONE THIS REWRITE EXISTS FOR, AND THE FAILURE THAT WAS ALREADY GREEN IN THE ACCOUNT.

    Constructing a model config per registered size looks like the obvious check and asserts
    nothing: `Attention.__init__` warns and swaps the configured attention backend for the
    torch one whenever no card is present, before it ever calls `assert_supported()`. So on
    a builder every olmo3 config constructs happily on an image that cannot train one.

    Under the second pass the downgrade branch is skipped, the assertion is reached, and
    flash-attn is absent, so the two passes disagree -- and the platform reports the check as
    device-conditional having been told nothing about attention backends by anybody.

    Mutation: run one pass. That is the whole of the previous version of this gate, and it
    would have published the image this finding came from with a green line in the log.
    """
    with pytest.raises(SelfCheckError) as raised:
        reconcile_the_two_passes(
            a_pass(outcome="passed"), a_pass(outcome="raised", pretended=True)
        )

    assert raised.value.reason == "self_check_is_device_conditional"


def test_a_check_that_passes_only_with_a_card_is_refused_as_its_own_thing() -> None:
    """The other direction, which is a different problem and needs a different sentence.

    Mutation: report this as `self_check_refused`. It is not a repository refusing its image;
    it is a check that no builder can ever run, and it would redden every build of a
    perfectly good one until somebody worked out why.
    """
    with pytest.raises(SelfCheckError) as raised:
        reconcile_the_two_passes(
            a_pass(outcome="raised"), a_pass(outcome="passed", pretended=True)
        )

    assert raised.value.reason == "self_check_needs_a_device"


def test_a_check_that_fails_both_ways_is_the_ordinary_refusal() -> None:
    # Mutation: report agreement on two failures as a pass. Agreeing is not the property
    # being tested for; agreeing on a pass is.
    with pytest.raises(SelfCheckError) as raised:
        reconcile_the_two_passes(
            a_pass(outcome="refused"), a_pass(outcome="refused", pretended=True)
        )

    assert raised.value.reason == "self_check_refused"


def test_an_image_with_no_torch_to_patch_is_judged_on_the_one_honest_pass(
) -> None:
    """Mutation: refuse when the second pass patched nothing.

    An image with no importable torch cannot be carrying the degradation this looks for, and
    edullm-data publishes exactly that image. Refusing it would redden a repository for a
    hazard it cannot have. Mutation the other way: treat the unpatched second pass as a real
    comparison, which would compare a run against an identical run and always agree, quietly
    turning the differential off for every image.
    """
    accepted, _ = reconcile_the_two_passes(
        a_pass(outcome="passed"), a_pass(outcome="passed", pretended=False)
    )

    assert accepted == "no_device_switch_to_pretend_with"


def test_the_two_accepted_states_are_told_apart_in_what_is_printed() -> None:
    # Mutation: print one word for both. "Both passes agreed" and "there was nothing to
    # compare against" are different amounts of evidence and the log should say which.
    agreed, _ = reconcile_the_two_passes(
        a_pass(outcome="passed"), a_pass(outcome="passed", pretended=True)
    )
    uncompared, _ = reconcile_the_two_passes(a_pass(outcome="passed"), None)

    assert agreed != uncompared


def test_the_disagreement_is_reported_from_the_pass_that_disagreed() -> None:
    """Mutation: always show the first pass.

    For a device-conditional check the first pass is the green one, so a reader gets a
    transcript of everything going well under a heading saying it did not.
    """
    pretended = a_pass(outcome="raised", pretended=True, said="ImportError: no flash_attn")
    with pytest.raises(SelfCheckError):
        reconcile_the_two_passes(a_pass(outcome="passed", said="all fourteen built"), pretended)

    assert "ImportError: no flash_attn" in pretended.said()


def test_every_refusal_carries_guidance_that_names_what_to_do() -> None:
    # Mutation: drop the guidance. A reason token alone sends an author to reproduce a
    # container build in order to learn which of four quite different things happened.
    for reason in (
        "self_check_refused",
        "self_check_raised",
        "self_check_is_device_conditional",
        "self_check_needs_a_device",
        "self_check_unanswered",
    ):
        assert SelfCheckError(reason).guidance == REJECTION_GUIDANCE[reason]


def test_the_device_conditional_guidance_teaches_the_repair_rather_than_the_symptom() -> None:
    """The single highest-value string in this tool.

    Whoever reads it has just written the obvious check and been refused, and what they need
    is not the observation that two runs disagreed but the reason a constructor cannot answer
    this question and what to call instead. Mutation: say only that the passes disagreed.
    """
    guidance = REJECTION_GUIDANCE["self_check_is_device_conditional"]

    assert "assert_supported" in guidance
    assert "construction" in guidance.lower()
    assert "directly" in guidance
    assert PRETENDED_DEVICE_LIMIT in guidance


def test_the_needs_a_device_guidance_names_where_such_a_check_does_belong() -> None:
    # Mutation: refuse without an alternative. There is a right place for a check that reads
    # the device, and it is a profile this platform already has.
    assert "*-check" in REJECTION_GUIDANCE["self_check_needs_a_device"]


# --------------------------------------------------------------------------------------
# The probe itself, executed rather than read.
# --------------------------------------------------------------------------------------


def _run_probe(
    tmp_path: Path, body: str, *, pretend_a_device: bool = False
) -> subprocess.CompletedProcess[str]:
    """The probe, against a check on this interpreter rather than in a container."""
    a_repository_with_a_check(tmp_path, body)
    # The probe reads a fixed mount point, which does not exist here, so the source is
    # retargeted at the checkout. Everything the probe decides is downstream of this line.
    source = probe_source(pretend_a_device=pretend_a_device).replace(
        repr(MOUNT_POINT), repr(str(tmp_path / ".edullm"))
    )
    return subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True, check=True, timeout=120
    )


def test_the_probe_reports_a_check_that_returned_normally_as_passed(tmp_path: Path) -> None:
    completed = _run_probe(tmp_path, "value = 1 + 1\n")

    assert read_report(completed.stdout) == {
        "probe": "self_check",
        "version": 2,
        "pretended": False,
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
        "version": 2,
        "pretended": False,
        "outcome": "passed",
    }


def test_the_probe_reports_a_non_zero_exit_as_a_refusal_and_keeps_the_code(
    tmp_path: Path,
) -> None:
    completed = _run_probe(tmp_path, "import sys\nsys.exit(3)\n")

    assert read_report(completed.stdout) == {
        "probe": "self_check",
        "version": 2,
        "pretended": False,
        "outcome": "refused",
        "exit": 3,
    }


def test_the_probe_reports_a_raising_check_as_raised_and_prints_the_traceback(
    tmp_path: Path,
) -> None:
    """Mutation: let the exception escape the probe.

    Then no sentinel is written at all and the tool reports that no interpreter answered,
    which is a true statement about the wrong thing.
    """
    completed = _run_probe(tmp_path, "raise ImportError('flash_attn is not installed')\n")

    assert read_report(completed.stdout) == {
        "probe": "self_check",
        "version": 2,
        "pretended": False,
        "outcome": "raised",
        "error": "ImportError",
    }
    assert "flash_attn is not installed" in completed.stderr


def test_the_probe_puts_the_checks_own_directory_on_the_import_path(tmp_path: Path) -> None:
    """Mutation: drop the sys.path insert.

    A check split into `.edullm/verify_image.py` and a helper beside it imports its sibling
    when it is run from a checkout, and would stop doing so here.
    """
    a_repository_with_a_check(tmp_path, "import backends\nassert backends.NAMES\n")
    (tmp_path / ".edullm" / "backends.py").write_text("NAMES = ('flash_2',)\n", encoding="utf-8")
    source = probe_source(pretend_a_device=False).replace(
        repr(MOUNT_POINT), repr(str(tmp_path / ".edullm"))
    )

    completed = subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True, check=True, timeout=120
    )

    assert read_report(completed.stdout)["outcome"] == "passed"  # type: ignore[index]


def test_the_pretending_probe_says_it_pretended_nothing_where_there_is_no_torch(
    tmp_path: Path,
) -> None:
    """This interpreter carries no torch, which is the honest case to test against.

    Mutation: report `pretended: True` unconditionally. Then an image with no torch would
    have its two identical passes compared as though one had been patched, they would always
    agree, and the differential would be silently off for every such image.
    """
    completed = _run_probe(tmp_path, "value = 1\n", pretend_a_device=True)

    assert read_report(completed.stdout) == {
        "probe": "self_check",
        "version": 2,
        "pretended": False,
        "outcome": "passed",
    }


def test_the_pretending_probe_makes_the_switch_answer_true_before_the_check_runs(
    tmp_path: Path,
) -> None:
    """The patch is executed against a stand-in torch, not merely read out of the source.

    A fake `torch` on the path rather than the real one, because the platform environment
    carries no torch and installing one to test a two-line patch is not a trade worth making.
    The check asserts the switch from inside itself, which is exactly what
    `Attention.__init__` does.

    Mutation: patch after running the check, or patch a copy of the module. Either leaves the
    degradation branch reading False and the second pass is a repeat of the first.
    """
    a_repository_with_a_check(
        tmp_path,
        "import torch\nassert torch.cuda.is_available(), 'the switch was not patched'\n",
    )
    fake = tmp_path / "site" / "torch"
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text("from . import cuda\n", encoding="utf-8")
    (fake / "cuda.py").write_text("def is_available():\n    return False\n", encoding="utf-8")
    source = probe_source(pretend_a_device=True).replace(
        repr(MOUNT_POINT), repr(str(tmp_path / ".edullm"))
    )

    completed = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(tmp_path / "site")},
    )

    assert read_report(completed.stdout) == {
        "probe": "self_check",
        "version": 2,
        "pretended": True,
        "outcome": "passed",
    }


def test_the_honest_probe_leaves_the_switch_alone(tmp_path: Path) -> None:
    """Mutation: patch on both passes. The two would then always agree and never disagree,
    which is the differential turned off in the way that still looks like it is running."""
    a_repository_with_a_check(
        tmp_path,
        "import torch\nassert not torch.cuda.is_available(), 'the switch was patched'\n",
    )
    fake = tmp_path / "site" / "torch"
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text("from . import cuda\n", encoding="utf-8")
    (fake / "cuda.py").write_text("def is_available():\n    return False\n", encoding="utf-8")
    source = probe_source(pretend_a_device=False).replace(
        repr(MOUNT_POINT), repr(str(tmp_path / ".edullm"))
    )

    completed = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(tmp_path / "site")},
    )

    assert read_report(completed.stdout) == {
        "probe": "self_check",
        "version": 2,
        "pretended": False,
        "outcome": "passed",
    }


def test_only_the_one_switch_is_patched(tmp_path: Path) -> None:
    """Mutation: patch device_count, current_device and the rest of the cluster too.

    Each one widens the set of correct checks this refuses -- a check that asks how many
    cards there are and is told one will go looking for card zero -- without covering
    meaningfully more of the pattern that has cost money. The bound is deliberate and is
    argued in PRETENDED_DEVICE_LIMIT.
    """
    source = probe_source(pretend_a_device=True)

    assert f"{PRETENDED_DEVICE_LIMIT} = lambda" in source
    for untouched in ("device_count", "current_device", "get_device_capability"):
        assert untouched not in source


# --------------------------------------------------------------------------------------
# The container this gate runs.
# --------------------------------------------------------------------------------------


def test_the_container_holds_no_network_no_card_and_is_removed(tmp_path: Path) -> None:
    """Mutation: drop --network none, or add --gpus.

    The network one is its sibling's argument. The device one is this whole tool: if a card
    were available the differential would have nothing to say, and the runner has none.
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


def test_the_two_passes_differ_only_in_the_patch(tmp_path: Path) -> None:
    # Mutation: change the mount, the network or the interpreter between passes. Then a
    # disagreement no longer isolates the one variable it is supposed to isolate.
    honest = probe_command(IMAGE, "python", tmp_path, pretend_a_device=False)
    pretended = probe_command(IMAGE, "python", tmp_path, pretend_a_device=True)

    assert honest[:-1] == pretended[:-1]
    assert honest[-1] != pretended[-1]


def test_the_interpreter_is_an_entrypoint_override_not_a_command(tmp_path: Path) -> None:
    # Mutation: pass the interpreter as the container command. These Dockerfiles inherit the
    # registered base's entrypoint deliberately, and it would swallow the probe.
    command = probe_command(IMAGE, "python3", tmp_path)

    assert command[command.index("--entrypoint") + 1] == "python3"
    assert command[-3] == IMAGE
    assert command[-2] == "-c"


def test_no_shell_stands_between_this_tool_and_the_probe(tmp_path: Path) -> None:
    # Mutation: build the command as a string for a shell. The probe is multi-line Python
    # and every quoting rule between here and it is a way to run something else.
    assert all(isinstance(token, str) for token in probe_command(IMAGE, "python", tmp_path))


def test_the_repository_root_is_required_rather_than_guessed() -> None:
    # Mutation: default it to the working directory. This tool runs from a subshell cd'd
    # into the platform tooling checkout, where `.edullm/` is somebody else's.
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--image-reference", IMAGE])


# --------------------------------------------------------------------------------------
# Reading the verdict back out of an image that is free to print whatever it likes.
# --------------------------------------------------------------------------------------


def test_the_sentinel_is_found_among_whatever_else_the_check_printed() -> None:
    # Mutation: parse the whole of stdout as JSON. A check that reports what it asserted is
    # a good check, and this has to survive it.
    report = read_report(
        "\n".join(["asserting flash_2", "asserting torch", sentinel_line(outcome="passed")])
    )

    assert report == {"outcome": "passed"}


def test_the_last_sentinel_wins_so_a_check_that_echoed_one_cannot_answer_first() -> None:
    # Mutation: take the first sentinel. The probe writes its line after the check has
    # finished, so anything earlier came from the check.
    report = read_report(
        "\n".join([sentinel_line(outcome="passed"), sentinel_line(outcome="raised")])
    )

    assert report == {"outcome": "raised"}


def test_stdout_with_no_sentinel_is_no_answer_rather_than_an_empty_one() -> None:
    # Mutation: return {} when nothing matched, which reads downstream as an outcome.
    assert read_report("Traceback (most recent call last):\n  ...\n") is None


def test_what_a_pass_said_drops_the_verdict_line_and_keeps_the_tail() -> None:
    # Mutation: keep the head. A traceback ends with the line that raised, so a head-bounded
    # log reproduces the least useful part of it.
    numbered = "\n".join(str(number) for number in range(FAILURE_OUTPUT_LINES * 2))
    said = Pass(report={}, stdout=f"{numbered}\n{sentinel_line(outcome='refused')}").said()

    assert SENTINEL not in said
    assert str(FAILURE_OUTPUT_LINES * 2 - 1) in said
    assert said.splitlines()[0] != "0"


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
def test_a_device_independent_check_lets_the_build_go_on_quietly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    a_repository_with_a_check(tmp_path)
    passed = f"echo '{sentinel_line(outcome='passed', pretended=True)}'"
    stub_bin = a_docker_that_answers(
        tmp_path / "bin",
        honest=f"echo '{sentinel_line(outcome='passed', pretended=False)}'",
        pretended=passed,
    )

    assert run_main(stub_bin, tmp_path, monkeypatch) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "self_check_verified:passed:device_independent"
    assert captured.err == ""


@pytest.mark.slow
def test_the_vacuous_check_is_caught_end_to_end_and_never_pushed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole finding, driven through the tool rather than through its parts.

    A check that constructs models: green on the builder, because the attention backend was
    swapped for the torch one on the way past; red under a pretended card, because the swap
    does not happen and the real assertion is reached. Mutation: any weakening that lets one
    green pass answer for both.
    """
    a_repository_with_a_check(tmp_path)
    stub_bin = a_docker_that_answers(
        tmp_path / "bin",
        honest=(
            "echo 'built all fourteen olmo3 configs'\n  "
            f"echo '{sentinel_line(outcome='passed', pretended=False)}'"
        ),
        pretended=(
            "echo 'ImportError: flash_attn is required for flash_2' >&2\n  "
            f"echo '{sentinel_line(outcome='raised', error='ImportError', pretended=True)}'"
        ),
    )

    assert run_main(stub_bin, tmp_path, monkeypatch) == 1
    errors = capsys.readouterr().err
    assert errors.splitlines()[0] == "self_check_is_device_conditional"
    # The failing pass is what a reader is shown, not the reassuring one.
    assert "flash_attn is required" in errors
    assert "built all fourteen" not in errors


@pytest.mark.slow
def test_a_check_that_refuses_both_ways_stops_the_build_as_a_plain_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    a_repository_with_a_check(tmp_path)
    stub_bin = a_docker_that_answers(
        tmp_path / "bin",
        honest=(
            "echo 'ImportError: flash_attn is required' >&2\n  "
            f"echo '{sentinel_line(outcome='raised', error='ImportError', pretended=False)}'"
        ),
        pretended=f"echo '{sentinel_line(outcome='raised', error='ImportError', pretended=True)}'",
    )

    assert run_main(stub_bin, tmp_path, monkeypatch) == 1
    errors = capsys.readouterr().err
    assert errors.splitlines()[0] == "self_check_raised"
    assert "flash_attn is required" in errors


@pytest.mark.slow
def test_an_image_with_no_torch_is_judged_on_one_pass_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # edullm-data publishes exactly this: boto3 and numpy, no torch. Mutation: refuse it, or
    # report it as though the differential had run.
    a_repository_with_a_check(tmp_path)
    stub_bin = docker_stub(
        tmp_path / "bin", f"echo '{sentinel_line(outcome='passed', pretended=False)}'\n"
    )

    assert run_main(stub_bin, tmp_path, monkeypatch) == 0
    assert (
        capsys.readouterr().out.strip()
        == "self_check_verified:passed:no_device_switch_to_pretend_with"
    )


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
        f"  echo '{sentinel_line(outcome='passed', pretended=False)}'\n"
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
