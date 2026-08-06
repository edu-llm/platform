"""``edullm shell``: the same machine, handed over rather than driven.

WHAT SEPARATES THIS FROM ``run`` IS WHO HOLDS THE TERMINAL. ``run`` sends one command and reads
the answer, so it can report a status. ``shell`` gives the terminal to a child process and gets
it back when the person is finished, so there is no answer to report and no document to print.
That difference is why neither verb takes ``--json``, and the case at the bottom pins it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from edullm_platform.cli.main import (
    EXIT_OK,
    EXIT_UNUSABLE,
    build_parser_and_verbs,
)
from tests.cli_support import (
    LANE_EXISTING_EXPIRY,
    LANE_INSTANCE,
    FakeRunner,
    git_answers,
    invoke,
    lane_answers,
)


def a_laptop(tmp_path: Path, **overrides: object) -> FakeRunner:
    return FakeRunner({**git_answers(tmp_path), **lane_answers(**overrides)})


def usage_of(verb: str) -> str:
    return build_parser_and_verbs()[1][verb].format_usage()


def test_a_shell_hands_over_a_terminal_on_a_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: run one command instead.

    A session with no document is the account's own shell preference, which is what somebody
    asking for a shell means. Naming a document would run something and exit.
    """
    runner = a_laptop(tmp_path)

    code, out, err = invoke(
        ["shell", "--project", "mixlaw", "--compute", "gpu-1xt4"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    started = runner.ran("aws", "ssm", "start-session")

    assert started
    assert "--document-name" not in started[0]


def test_a_shell_keeps_the_researcher_s_own_stdin_and_is_not_handed_a_pipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE BOUNDARY ON THE FIX THAT REPAIRED ``edullm run``.**
    Mutation: set ``stdin_stays_open`` here too, on the theory that what helped one verb helps
    both.

    ``run`` is given a standard input its caller cannot close, because that session reads no
    keystrokes and the plugin would otherwise hang up the instant descriptor 0 is at end of
    file. This verb is the opposite case: it is a person at a keyboard, and a pipe in place of
    their terminal would swallow every character they type into a shell that then does nothing.
    The line is whether the session asks the researcher for anything.
    """
    runner = a_laptop(tmp_path)

    invoke(
        ["shell", "--project", "mixlaw", "--compute", "gpu-1xt4"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert runner.held_stdin_open_for("aws", "ssm", "start-session") == [False]


def test_the_notebook_flag_forwards_a_port_and_prints_the_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: start a shell and tell the person to run jupyter themselves.

    decisions.md records `notebook` being folded into `shell --notebook` rather than kept as a
    verb. What makes the fold honest is that the flag does the whole thing: the forward, and the
    address to open. A shell plus instructions is the verb that was retired.
    """
    runner = a_laptop(tmp_path)

    code, out, err = invoke(
        ["shell", "--project", "mixlaw", "--compute", "gpu-1xt4", "--notebook"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    started = " ".join(runner.ran("aws", "ssm", "start-session")[0])

    assert "AWS-StartPortForwardingSession" in started
    assert "http://localhost:" in out


def test_a_notebook_opens_no_port_on_the_machine_and_needs_no_ingress_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE PROPERTY THAT KEEPS SOMEBODY'S NOTEBOOK OFF THE PUBLIC INTERNET.**
    Mutation: authorize an ingress rule for the notebook port.

    A Jupyter server with a guessable token on an instance with an open port is the failure that
    ends this route. The forward carries the bytes through the agent's own outbound connection,
    so the launch's security group keeps its zero ingress rules and there is nothing to reach.
    """
    runner = a_laptop(tmp_path)

    invoke(
        ["shell", "--project", "mixlaw", "--compute", "gpu-1xt4", "--notebook"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert runner.ran("aws", "ec2", "authorize-security-group-ingress") == []
    assert "--key-name" not in " ".join(runner.ran("aws", "ec2", "run-instances")[0])


def test_the_ssh_line_is_offered_for_an_editor_and_written_nowhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: write it into ~/.ssh/config.

    An editor over SSH is what several people will want, and a ProxyCommand is the way there.
    Writing into a file this binary does not own, on a machine where something else may manage
    it, to save one paste, is a change nobody asked for.
    """
    runner = a_laptop(tmp_path)

    _, out, _ = invoke(
        ["shell", "--project", "mixlaw", "--compute", "gpu-1xt4"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert "ProxyCommand" in out
    assert not (tmp_path / ".ssh" / "config").exists()


def test_the_expiry_reaches_the_person_who_is_about_to_sit_at_the_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print it only on run.

    A shell is where somebody spends an afternoon, so it is the verb where an unannounced expiry
    does the most damage. The line is the same one run prints, for the same reason.
    """
    runner = a_laptop(tmp_path)

    _, out, _ = invoke(
        ["shell", "--project", "mixlaw", "--compute", "gpu-1xt4"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert "expires" in out


def test_a_shell_reuses_the_machine_run_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: launch from shell unconditionally.

    "Run something, then go and look at what it left" is the ordinary sequence, and it only works
    if both verbs find the same machine. Two verbs each starting their own would double the bill
    and put the output on the machine the person is not sitting at.
    """
    runner = a_laptop(tmp_path, existing="i-0000000000000aaaa")

    invoke(
        ["shell", "--project", "mixlaw", "--compute", "gpu-1xt4"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert runner.ran("aws", "ec2", "run-instances") == []
    assert "i-0000000000000aaaa" in " ".join(runner.ran("aws", "ssm", "start-session")[0])


def test_a_shell_on_a_reused_machine_quotes_the_tag_and_not_a_fresh_afternoon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: compute the expiry from this invocation's clock, which is what shipped.

    The companion to the same check on run, and the verb where the stale number cost most: a
    shell is where somebody settles in for an afternoon on the strength of the expiry this line
    gave them. Both verbs print through one object precisely so that fixing one cannot leave the
    other saying something else.
    """
    runner = a_laptop(tmp_path, existing=LANE_INSTANCE)

    _, out, _ = invoke(
        ["shell", "--project", "mixlaw", "--compute", "gpu-1xt4"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert set(re.findall(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", out)) == {LANE_EXISTING_EXPIRY}, out


def test_a_shell_takes_no_command_and_says_so_rather_than_ignoring_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: accept a command and drop it.

    `edullm shell --project p --compute c -- python train.py` is a reasonable thing to type and
    means `edullm run`. Silently opening a shell would leave somebody watching a prompt, sure
    they had started a job.
    """
    runner = a_laptop(tmp_path)

    code, _, err = invoke(
        ["shell", "--project", "mixlaw", "--compute", "gpu-1xt4", "--", "python", "train.py"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_UNUSABLE
    assert "edullm run" in err
    assert runner.ran("aws", "ec2", "run-instances") == []


def test_neither_lane_verb_takes_json_and_that_is_a_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE ONE PLACE THE MACHINE-READABLE CONVENTION IS DELIBERATELY NOT FOLLOWED.**
    Mutation: add --json to either verb.

    check, status, add and ask all take it, and the shape is settled: one document on stdout
    whatever the outcome. Neither lane verb has a structure to publish. run's stdout is the
    researcher's own program's output, streamed as it arrives, and shell's is a terminal handed
    to a child process. A --json that emptied stdout of that would be worse than no flag, and one
    that printed a document beside it would break the one-document promise for every caller.

    Asserted through SystemExit and capsys, because argparse writes an invalid-choice message to
    the real stderr and exits rather than returning, so an assertion reading a captured stream
    here would never run at all.
    """
    for verb in ("run", "shell"):
        assert "--json" not in usage_of(verb)

    with pytest.raises(SystemExit) as raised:
        build_parser_and_verbs()[1]["shell"].parse_args(
            ["--project", "p", "--compute", "gpu-1xt4", "--json"]
        )

    assert raised.value.code == 2


def test_both_lane_verbs_take_the_same_four_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: give one of them --hours and not the other.

    A person who learns the flags on run and types them on shell is the ordinary case. Equality
    rather than containment: a subset assertion gets easier to satisfy every time a flag leaves
    one of the two, which is the shape that has now been found seven times in this repository.
    """

    def flags(verb: str) -> set[str]:
        parser = build_parser_and_verbs()[1][verb]
        return {
            option
            for action in parser._actions
            for option in action.option_strings
            if option.startswith("--")
        } - {"--help", "--config-dir", "--notebook"}

    assert flags("run") == flags("shell")
    assert "--spot" in flags("run")


def test_the_notebook_flag_is_the_one_thing_only_shell_has(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: put --notebook on run too.

    A forwarded port outlives nothing on run: that verb runs one command and returns. The flag
    belongs to the verb that stays open, and having it on both would be a flag that did nothing
    on one of them.
    """
    assert "--notebook" in usage_of("shell")
    assert "--notebook" not in usage_of("run")


def test_the_address_printed_is_the_port_the_forward_actually_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print a fixed 8888 while forwarding something else.

    The two numbers are different things: the remote one comes from
    config/reports/working-tier.yaml and is where Jupyter listens on the machine, the local one
    is where it appears on the laptop. Printing the remote one is the mistake that sends somebody
    to a page that is not there, and it looks right in every transcript where the two agree.
    """
    from edullm_platform.cli.lane import load_working_tier_settings

    settings = load_working_tier_settings()
    runner = a_laptop(tmp_path)

    _, out, _ = invoke(
        ["shell", "--project", "mixlaw", "--compute", "gpu-1xt4", "--notebook"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )
    forwarded = " ".join(runner.ran("aws", "ssm", "start-session")[0])
    parameters = json.loads(forwarded.split("--parameters ")[1])
    local = parameters["localPortNumber"][0]

    assert parameters["portNumber"] == [str(settings.notebook_port)]
    assert f"http://localhost:{local}/" in out
