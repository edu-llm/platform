"""How a laptop reaches a lane machine, and the one thing on the laptop that has to exist.

MEASURED RATHER THAN CHOSEN. The mechanism is Systems Manager Session Manager, and the reason is
in the plan this file comes from: on 2026-08-05 an instance in the platform's own VPC answered
Online with agent 3.3.4793.0, all six subnets in that VPC route to an internet gateway, the
security group there has zero ingress rules, and Session Manager is free on EC2. Nothing had to
be built for any of that.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from edullm_platform.cli.lane import (
    ARM_MACHINES,
    AWS_LOGIN_COMMAND,
    PLUGIN_DOWNLOADS,
    SESSION_PLUGIN,
    agent_online_argv,
    command_line,
    load_working_tier_settings,
    missing_plugin_refusal,
    notebook_forward_argv,
    plugin_install_commands,
    remote_command_argv,
    remote_script,
    shell_session_argv,
    ssh_proxy_command,
    under_a_shell,
)
from edullm_platform.cli.workspace import SubprocessRunner

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS = load_working_tier_settings(PROJECT_ROOT / "config")
INSTANCE = "i-0000000000000aaaa"


def test_a_shell_is_a_session_and_carries_no_document() -> None:
    """Mutation: name a document.

    The default session document is the account's shell preference, which is what a person
    expects from "give me a shell". Naming AWS-StartInteractiveCommand instead would run one
    command and exit, which is the other verb.
    """
    assert shell_session_argv(INSTANCE) == (
        "aws",
        "ssm",
        "start-session",
        "--target",
        INSTANCE,
    )


def test_a_notebook_is_a_port_forward_and_opens_nothing_on_the_instance() -> None:
    """THE PROPERTY THAT KEEPS A NOTEBOOK OFF THE INTERNET.
    Mutation: open the port in a security group instead.

    Jupyter binds loopback on the instance and the forward carries the bytes through the agent's
    own outbound connection, so nothing is listening anywhere a scanner can reach. The security
    group the launch uses has zero ingress rules, and this is why that is enough rather than a
    thing to work around.
    """
    argv = notebook_forward_argv(INSTANCE, settings=SETTINGS, local_port=8890)

    assert "--document-name" in argv
    assert "AWS-StartPortForwardingSession" in argv
    assert f'"portNumber":["{SETTINGS.notebook_port}"]' in " ".join(argv)
    assert '"localPortNumber":["8890"]' in " ".join(argv)


def test_a_remote_command_streams_rather_than_returning_at_the_end() -> None:
    """Mutation: use ssm send-command.

    SendCommand is fire-and-collect: its output is truncated at 24,000 characters unless an S3
    bucket is configured, and it arrives when the command is done. The verb's own sentence in the
    CLI is "stream the output back", and AWS-StartNonInteractiveCommand is the document that
    does that.
    """
    argv = remote_command_argv(INSTANCE, command="echo hello")

    assert "AWS-StartNonInteractiveCommand" in argv
    assert json.loads(argv[-1]) == {"command": ["bash -c 'echo hello'"]}


def test_the_parameters_document_is_serialised_rather_than_interpolated() -> None:
    """**THE DEFECT THAT MADE edullm run FAIL ON EVERY COMMAND IT WAS EVER GIVEN.**
    Mutation: build the document with an f-string again.

    remote_script ends in ``echo "edullm-exit:$status"``, so the document always carries a double
    quote, and interpolating it into ``{"command":["..."]}`` produced something that is not JSON.
    The AWS CLI refused it, the verb reported that the session had ended without saying what the
    command did, and that sentence names neither the quote nor this file.

    A researcher's own command carries the same hazard and carries it further, because
    ``python -c "print(1)"`` is a thing people type. Parsed rather than matched, because a
    substring assertion is exactly what missed this.
    """
    script = remote_script(uri="s3://a/b/", project="p", command='python -c "print(1)"')
    argv = remote_command_argv(INSTANCE, command=script)

    assert json.loads(argv[-1])["command"] == [under_a_shell(script)]


def test_the_tokens_after_the_dashes_keep_the_quoting_the_researcher_typed() -> None:
    """**THE FOURTH WAY A RUN CAME BACK SAYING NOTHING, AND THE LIKELIEST TO BE HIT.**
    Mutation: join the tokens with a space.

    ``edullm run -- python -c 'print(1+1)'`` arrives as three tokens with the quotes already
    taken off by the researcher's own shell, so a space join sends ``python -c print(1+1)`` to
    the machine, where the parentheses are shell syntax. bash refused the entire remote script
    before running any of it, printed a parse error naming the whole line, and the verb reported
    only that the session had ended without saying what the command did. Measured on 2026-08-06.

    Round-tripped through shlex.split rather than compared to a string, because the property is
    that the machine sees the same words the researcher typed, and there is more than one
    correct quoting of most of them.
    """
    tokens = ["python", "-c", "print(1+1)"]

    assert shlex.split(command_line(tokens)) == tokens
    assert command_line(["echo", "one two"]) != "echo one two"
    assert shlex.split(command_line(["sh", "-c", "echo a; exit 7"])) == [
        "sh",
        "-c",
        "echo a; exit 7",
    ]


def test_a_command_is_wrapped_for_a_shell_because_the_document_runs_none() -> None:
    """**THE SECOND REASON edullm run NEVER WORKED, AND IT IS INVISIBLE FROM THE ARGV.**
    Mutation: pass the script straight through.

    AWS-StartNonInteractiveCommand splits the command the way a shell splits a line and then
    executes the first token with the rest as its arguments. Nothing interprets ``;``, ``$?`` or
    ``(``. Handed remote_script directly it ran ``echo`` with the whole of the rest of the
    pipeline as arguments, printed them back as one line and exited 0, so the machine did none of
    the work and the sentinel never appeared. Measured against the account on 2026-08-06.

    Mutation: wrap with ``f"bash -c '{script}'"``. A single quote in the researcher's own command
    closes the wrapper early and the tail of their command is then read as shell of its own,
    which ``git commit -m 'don't'`` produces.
    """
    assert under_a_shell("echo a; echo b") == "bash -c 'echo a; echo b'"

    wrapped = under_a_shell("""echo "it's" quoted""")
    assert shlex.split(wrapped)[:2] == ["bash", "-c"]
    assert shlex.split(wrapped)[2] == """echo "it's" quoted"""


def test_the_work_directory_is_made_with_privilege_and_handed_to_the_session() -> None:
    """**THE THIRD REASON A RUN CAME BACK SAYING NOTHING USEFUL.**
    Mutation: go back to a plain ``mkdir -p``.

    A Session Manager session runs as ``ssm-user``, who cannot create a directory at the
    filesystem root. Without privilege the first act failed Permission denied, the sync down had
    nowhere to land, the ``cd`` failed and the sync back reported a path that does not exist --
    and the researcher's command ran anyway, in whatever directory the session started in, and
    returned 0. A run that half-works and reports success is worse than one that refuses.

    Mutation: create it as root and leave it owned by root, which is what ``sudo mkdir -p``
    alone does. The sync back afterwards runs as the session and cannot write into it.
    """
    script = remote_script(uri="s3://a/b/", project="p", command="true")

    assert "sudo install -d" in script
    assert '-o "$(id -u)" -g "$(id -g)" /work/p' in script
    assert script.index("install -d") < script.index("aws s3 sync s3://a/b/")


def test_the_remote_script_syncs_the_tree_down_before_it_runs_anything() -> None:
    """Mutation: run the command without the sync.

    The tree was uploaded to the working tier by the verb; without the sync the command runs
    against whatever the machine happened to have, which on a reused machine is the previous
    run's tree and on a new one is nothing.
    """
    script = remote_script(
        uri="s3://edullm-scratch/caiiris/mixlaw/", project="mixlaw", command="pytest"
    )

    assert "aws s3 sync s3://edullm-scratch/caiiris/mixlaw/" in script
    assert script.index("s3 sync") < script.index("pytest")


def test_the_remote_script_prints_the_exit_status_where_the_verb_can_read_it() -> None:
    """THE ONLY WAY THE VERB LEARNS WHETHER THE COMMAND WORKED.
    Mutation: drop the sentinel.

    start-session exits with the plugin's status and not with the remote command's, so without a
    sentinel on the last line the verb would report success for a command that failed. That is
    the failure that makes a script built on this verb useless.
    """
    script = remote_script(uri="s3://a/b/", project="p", command="false")

    assert "edullm-exit:" in script
    assert script.rstrip().endswith('"edullm-exit:$status"')


def test_the_remote_script_syncs_the_work_directory_back_up_after_the_command() -> None:
    """Mutation: sync down and never up.

    The machine goes away on a schedule. Anything the command wrote and nothing carried back is
    gone, and the working tier is the whole answer to "what happens to my work".

    Ordered rather than counted. A version of this that asserted two syncs and their order by
    index arithmetic passed on a script that synced down twice, because two calls and a later
    call are both true of that. The direction of each one is the property.
    """
    script = remote_script(uri="s3://a/b/", project="p", command="true")
    down = script.index("aws s3 sync s3://a/b/ /work/p")
    up = script.index("aws s3 sync /work/p s3://a/b/")

    assert script.count("aws s3 sync") == 2
    assert down < script.index("(true)") < up


def test_the_status_is_captured_before_the_upload_so_a_failed_command_keeps_its_output() -> None:
    """Mutation: capture the status after the sync back up.

    ``$?`` is the last command's, so a status read after the upload is the upload's rather than
    the researcher's. A failing program on a machine whose sync succeeded would then be reported
    as having worked, which is the exact reversal the sentinel exists to prevent.
    """
    script = remote_script(uri="s3://a/b/", project="p", command="false")

    assert script.index("status=$?") < script.index("aws s3 sync /work/p s3://a/b/")


def test_whether_the_agent_has_answered_is_asked_of_systems_manager() -> None:
    """Mutation: poll ec2 describe-instance-status instead.

    An instance reaches "running" and passes its status checks a minute or two before the agent
    registers, so an EC2 status check says the machine is ready and the session then fails with
    "target not connected". The agent's own ping status is the fact the session depends on.
    """
    argv = agent_online_argv(INSTANCE)

    assert "describe-instance-information" in argv
    assert f"Key=InstanceIds,Values={INSTANCE}" in " ".join(argv)


def test_the_ssh_line_is_printed_rather_than_written_into_anybody_s_config() -> None:
    """Mutation: edit ~/.ssh/config.

    An editor over SSH is what several people will want, and the way there is a ProxyCommand.
    Writing into somebody's ssh config is a change to a file the CLI does not own, on a machine
    where it may be managed by something else, to save one paste.
    """
    line = ssh_proxy_command(INSTANCE, system="Darwin")

    assert line.startswith("ProxyCommand sh -c ")
    assert "AWS-StartSSHSession" in line
    assert INSTANCE in line


def test_the_ssh_line_a_windows_researcher_is_given_names_no_program_windows_lacks() -> None:
    """**A LINE THAT CANNOT RUN IS WORSE THAN NO LINE, AND THIS ONE COULD NOT RUN.**
    Mutation: print the ``sh -c`` form on every platform, which is what shipped.

    Native Windows has no ``sh``. Not in ``System32``, not from the OpenSSH client Windows ships,
    and not from anything installing the AWS CLI puts there. So the one line this verb offers a
    Windows researcher failed inside whatever editor read their ssh config, saying that ``sh``
    was not found -- which sends somebody to debug an SSH configuration for a program nothing
    ever told them they needed.

    Both spellings are AWS's own, from *Step 8: Allow and control permissions for SSH connections
    through Session Manager* in the Systems Manager user guide. Asserted on the absence of ``sh``
    rather than only on the presence of the interpreter, because the interpreter could be added
    in front of the old line and the old line is the part that breaks.
    """
    line = ssh_proxy_command(INSTANCE, system="Windows")

    assert "sh -c" not in line
    assert "powershell.exe" in line
    assert "AWS-StartSSHSession" in line
    assert INSTANCE in line


def test_wsl_is_a_linux_laptop_and_gets_the_line_its_own_ssh_can_run() -> None:
    """Mutation: key the Windows form on anything that smells of Windows.

    A researcher under WSL is a Linux process writing a Linux ``~/.ssh/config`` that a Linux
    ``ssh`` will read, and that ``ssh`` has ``sh``. ``platform.system`` answers ``Linux`` for
    them, and handing them ``powershell.exe`` because the machine underneath is a Windows one
    would break the case that currently works. The separate hazard of a Windows ``gh`` on a WSL
    PATH is diagnosed by ``github_interop_diagnostic`` and is not this line's business.
    """
    assert ssh_proxy_command(INSTANCE, system="Linux") == ssh_proxy_command(
        INSTANCE, system="Darwin"
    )
    assert "sh -c" in ssh_proxy_command(INSTANCE, system="Linux")


#: Every laptop this platform is used from, and the two AWS publishes a package for beyond
#: them. Windows carries no architecture because AWS ships one 64-bit installer for it.
EVERY_LAPTOP = (
    ("Windows", "AMD64", False),
    ("Darwin", "arm64", False),
    ("Darwin", "x86_64", False),
    ("Linux", "x86_64", True),
    ("Linux", "aarch64", True),
    ("Linux", "x86_64", False),
    ("Linux", "aarch64", False),
)


def test_a_missing_plugin_is_an_installation_and_says_so() -> None:
    """Mutation: let the aws CLI's own message through.

    Without the plugin, `aws ssm start-session` prints "SessionManagerPlugin is not found" and a
    documentation URL, which is a good message about a tool nobody told the researcher they
    needed. The refusal names the tool, says why the lane needs it, and does not read as a
    verdict on anything they asked for.
    """
    refusal = missing_plugin_refusal(system="Darwin", machine="arm64")

    assert refusal.code == "session_plugin_missing"
    assert SESSION_PLUGIN in refusal.detail
    assert len(refusal.detail.split()) > 20


@pytest.mark.parametrize(("system", "machine", "has_dpkg"), EVERY_LAPTOP)
def test_every_laptop_is_given_a_command_and_not_a_documentation_page(
    system: str, machine: str, has_dpkg: bool
) -> None:
    """**THE DEFECT THIS WHOLE CHANGE IS ABOUT.**
    Mutation: put "install it from the AWS documentation for the Session Manager plugin" back.

    Both refusals a first `edullm run` can produce were well written in every respect except
    that they ended by sending somebody to a search engine on their first morning. The
    process knows its operating system and its architecture, so there is always a command it
    could have printed, and this asserts one exists for every laptop anybody here is on.

    Asserted as an AWS download plus a verb rather than against a fixed string, because the
    URLs move when AWS reorganises and the wording is the writer's business.
    """
    commands = plugin_install_commands(system=system, machine=machine, has_dpkg=has_dpkg)

    assert commands, "no command at all, which is the documentation page by another name"
    assert PLUGIN_DOWNLOADS in " ".join(commands)
    assert any(
        command.startswith(("curl ", "sudo ", PLUGIN_DOWNLOADS)) for command in commands
    )


@pytest.mark.parametrize(("system", "machine", "has_dpkg"), EVERY_LAPTOP)
def test_no_install_command_arrives_wrapped_and_therefore_unpasteable(
    system: str, machine: str, has_dpkg: bool
) -> None:
    """**WHY THE COMMANDS ARE NOT IN THE REFUSAL DETAIL, HELD AS A PROPERTY.**
    Mutation: interpolate the commands into `missing_plugin_refusal`'s detail again.

    `presentation.render_refusals` wraps a detail at 76 columns with `textwrap.wrap`, which
    replaces every newline with a space. A `curl "..." -o "..."` carried inside one therefore
    arrives split across four indented lines and has to be reassembled by hand before it
    runs, which is most of the work this change exists to remove -- and it is not a
    hypothetical, because the first version of this repair did exactly that. A URL survives
    wrapping because it is one token and `break_long_words` is off; a command with spaces in
    it does not.

    Held on the detail rather than on the printed block, because the detail is the thing a
    tidying edit would put them back into.
    """
    detail = missing_plugin_refusal(system=system, machine=machine, has_dpkg=has_dpkg).detail

    for command in plugin_install_commands(system=system, machine=machine, has_dpkg=has_dpkg):
        if " " in command:
            assert command not in detail, (
                f"{command!r} is in the wrapped paragraph, so it reaches the terminal broken "
                "across lines. Print it under the block instead"
            )


@pytest.mark.parametrize(("system", "machine", "has_dpkg"), EVERY_LAPTOP)
def test_the_first_refusal_names_the_second_prerequisite(
    system: str, machine: str, has_dpkg: bool
) -> None:
    """**THE TWO PREREQUISITES ARE ORDERED AND THE FIRST ONE HAS TO SAY SO.**
    Mutation: drop the sentence naming the AWS session.

    `cli/main.py`'s `_lane_session` checks the plugin before it calls
    `sts:GetCallerIdentity`, so this is the first wall a newcomer meets and the credentials
    message is the second. Somebody who installs the plugin, believes they are finished, and
    then meets a second refusal has been made to discover the shape of the setup one wall at
    a time. Naming the next one costs a sentence here and saves an attempt there.
    """
    detail = missing_plugin_refusal(system=system, machine=machine, has_dpkg=has_dpkg).detail

    assert AWS_LOGIN_COMMAND in detail
    assert "first" in detail, "the ordering is the fact, not merely that a second thing exists"


def test_windows_is_told_the_two_ways_a_working_install_looks_broken() -> None:
    """**BOTH ARE AWS'S OWN WARNINGS AND BOTH PRODUCE THE SAME SYMPTOM.**
    Mutation: drop either clause, or drop the Administrator note.

    AWS documents that the installer needs Administrator rights, that Windows usually does
    not give the new PATH entry to the shell that ran it, and that the plugin supports
    PowerShell and the Command shell only. The middle one is the likeliest way a successful
    install goes on printing this same refusal, and the third produces an identical symptom
    from a different cause in a population sitting in Git Bash -- this binary drives `git`
    and `gh`, so a Git Bash window is where somebody already is. Naming one of two causes
    for one symptom sends half the readers to the wrong repair.

    Nowhere else gets a caveat, and the second half of this asserts that: prose nobody needs
    is prose that pushes the command off the screen.
    """
    windows = missing_plugin_refusal(system="Windows", machine="AMD64").detail

    assert "Administrator" in windows
    assert "PATH" in windows and "new PowerShell or Command Prompt window" in windows
    assert "Git Bash" in windows
    for system, machine, has_dpkg in EVERY_LAPTOP:
        if system == "Windows":
            continue
        elsewhere = missing_plugin_refusal(
            system=system, machine=machine, has_dpkg=has_dpkg
        ).detail
        assert "Administrator" not in elsewhere
        assert "PowerShell" not in elsewhere


def test_the_package_matches_the_silicon_under_both_names_for_it() -> None:
    """Mutation: test `arm64` only, or `aarch64` only.

    Darwin says `arm64` and Linux says `aarch64` for the same processor. Checking one
    spelling hands every laptop using the other an x86 package, which installs and then will
    not run -- a failure that happens after the person believes they are finished and does
    not name a cause.
    """
    for machine in ARM_MACHINES:
        for system, has_dpkg in (("Darwin", False), ("Linux", True), ("Linux", False)):
            joined = " ".join(
                plugin_install_commands(system=system, machine=machine, has_dpkg=has_dpkg)
            )
            assert "arm64" in joined, f"{system} on {machine} is offered an x86 package"

    for system, machine, has_dpkg in (
        ("Darwin", "x86_64", False),
        ("Linux", "x86_64", True),
        ("Linux", "x86_64", False),
    ):
        joined = " ".join(
            plugin_install_commands(system=system, machine=machine, has_dpkg=has_dpkg)
        )
        assert "arm64" not in joined


def test_no_package_manager_aws_does_not_document_is_named() -> None:
    """**Mutation: name a Homebrew formula for macOS.**

    It is the first thing anybody reaches for on a Mac and AWS documents none -- their macOS
    page gives the signed `.pkg` and the bundled `.zip` and nothing else. A formula name
    guessed here would be printed to somebody with no way to check it, at the moment they
    are least able to absorb being sent somewhere that does not exist. Everything printed on
    every platform comes off an AWS page.
    """
    for system, machine, has_dpkg in EVERY_LAPTOP:
        joined = " ".join(
            plugin_install_commands(system=system, machine=machine, has_dpkg=has_dpkg)
        )
        assert "brew" not in joined
        assert "apt-get" not in joined and "apt install" not in joined
        assert PLUGIN_DOWNLOADS in joined


def test_nothing_the_lane_runs_needs_a_key_or_an_open_port() -> None:
    """**THE PROPERTY THAT MAKES A ZERO-INGRESS SECURITY GROUP SUFFICIENT.**
    Mutation: add an ssh call, or a key pair, to any of the four.

    Every route to a lane machine goes through the agent's outbound connection. The moment one
    of them shells out to ssh directly, the security group has to open a port and the launch has
    to distribute a key, and both are decisions this design made once. The ProxyCommand is the
    exception that proves it: it is a line printed for somebody else's ssh to drive, and it too
    goes through start-session.
    """
    every = (
        shell_session_argv(INSTANCE),
        notebook_forward_argv(INSTANCE, settings=SETTINGS, local_port=8890),
        remote_command_argv(INSTANCE, command="true"),
        agent_online_argv(INSTANCE),
    )

    for argv in every:
        assert argv[0] == "aws"
        assert "--key-name" not in argv
    for system in ("Darwin", "Linux", "Windows"):
        assert "aws ssm start-session" in ssh_proxy_command(INSTANCE, system=system)


# ---------------------------------------------------------------------------------------
# what the plugin reads from its own stdin, which is not a detail
# ---------------------------------------------------------------------------------------

#: A child that answers the one question the session plugin asks of its standard input before
#: it will carry anything: is there a reader on the other end, or is it already at end of file.
#: ``select`` rather than a read, because an open pipe with nothing in it never returns from a
#: read and the plugin does not block on one either -- it goes on relaying the session.
_WHAT_STDIN_IS = (
    "import sys, select;"
    "ready, _, _ = select.select([sys.stdin], [], [], 1.0);"
    "print('eof' if ready and sys.stdin.read(1) == '' else 'open')"
)


@contextmanager
def _stdin_at_end_of_file() -> Iterator[None]:
    """This process's own file descriptor 0, pointed at nothing, for as long as the block runs.

    Descriptor 0 and not ``sys.stdin``, because a child inherits the descriptor and never sees
    the object. Restored through a duplicate rather than reopened, so a runner that was handed
    a terminal, a pipe or a captured stream gets back exactly what it had.
    """
    saved = os.dup(0)
    with open(os.devnull, "rb") as nothing:
        os.dup2(nothing.fileno(), 0)
    try:
        yield
    finally:
        os.dup2(saved, 0)
        os.close(saved)


def test_a_session_the_researcher_types_nothing_into_still_gets_a_stdin_that_stays_open() -> None:
    """**THE DEFECT THAT MADE ``edullm run`` FAIL FOR EVERY CALLER WITHOUT A KEYBOARD.**
    Mutation: drop ``stdin_stays_open`` at the call, or let it pass ``DEVNULL`` instead.

    ``session-manager-plugin`` relays standard input into the session and treats end of file on
    it as the researcher hanging up, so it prints ``Cannot perform start session: EOF`` and
    exits 0 -- before any of the remote command's output comes back. Inheriting the caller's
    descriptor is right for a terminal and wrong everywhere else: under ``nohup``, in CI, from
    an editor task, behind ``< /dev/null`` or under an agent, descriptor 0 is already at end of
    file and the session dies the instant it opens. ``edullm run`` then reports that the session
    ended without saying what the command did, which names neither the descriptor nor the cause.
    Measured against this account on 2026-08-06: the same instance, the same command and the
    same laptop one minute apart answered ``NVIDIA L4`` with an open descriptor and
    ``Cannot perform start session: EOF`` with ``/dev/null``.

    A pipe the parent holds rather than a terminal, because a pseudo-terminal is a per-platform
    dependency for a session that reads no keystrokes.
    """
    argv = (sys.executable, "-c", _WHAT_STDIN_IS)

    with _stdin_at_end_of_file():
        inherited = SubprocessRunner()(argv)
        held = SubprocessRunner()(argv, stdin_stays_open=True)

    assert inherited.text == "eof", "the fixture has to reproduce the failure it is about"
    assert held.text == "open"


# ---------------------------------------------------------------------------------------
# who owns descriptors 1 and 2, which is the difference between a terminal and a transcript
# ---------------------------------------------------------------------------------------


@contextmanager
def _stdout_going_to(path: Path) -> Iterator[None]:
    """This process's own file descriptor 1, pointed at a file, for as long as the block runs.

    The counterpart to :func:`_stdin_at_end_of_file` and descriptor-level for the same reason: a
    child inherits the descriptor and never sees :data:`sys.stdout`, so a fixture that replaced
    the object would prove nothing about what the child writes to.
    """
    saved = os.dup(1)
    with open(path, "wb") as destination:
        os.dup2(destination.fileno(), 1)
    try:
        yield
    finally:
        os.dup2(saved, 1)
        os.close(saved)


def test_a_session_the_researcher_watches_writes_to_their_terminal_as_it_goes(
    tmp_path: Path,
) -> None:
    """**THE DEFECT THAT MADE ``edullm shell`` LOOK HUNG TO EVERY PERSON WHO EVER RAN IT.**
    Mutation: drop ``hands_over_the_terminal`` at the call, which is what shipped.

    The runner captures both streams for every other call it makes, and rightly: those are
    questions this binary asks and reads the answers to. A shell is not one. Capture puts a pipe
    on descriptors 1 and 2 that nothing drains until the child exits, so the researcher saw an
    empty screen for as long as they sat there, typed into it blind, and met the whole session
    played back at them at the end -- by which time they had reasonably concluded the verb hung.

    Asserted from the far side of the descriptor rather than on the keyword, because the keyword
    is what a caller passes and this is what a person sees. The captured half is asserted in the
    same breath so that a runner which wrote to both would fail: printing the result *as well*
    would put every line on the screen twice.
    """
    argv = (sys.executable, "-c", "print('the machine said this')")
    landed = tmp_path / "what-the-terminal-got"

    with _stdout_going_to(landed):
        handed = SubprocessRunner()(argv, hands_over_the_terminal=True)
    captured = SubprocessRunner()(argv)

    assert landed.read_text(encoding="utf-8").strip() == "the machine said this"
    assert handed.stdout == ""
    assert handed.ok
    assert captured.text == "the machine said this", "the other calls must still be captured"


def test_a_session_cannot_be_both_typed_into_and_handed_a_pipe() -> None:
    """Mutation: let the two keywords compose, with either one winning.

    They are two answers to one question -- whether a person is at the other end of descriptor 0
    -- so a caller asking for both has not decided which session it is opening. Resolved by
    precedence in either direction, one of them silently becomes the bug the other exists to
    prevent: a pipe swallowing every keystroke typed into a shell, or a session hanging up on end
    of file before a byte comes back.
    """
    with pytest.raises(ValueError, match="cannot both"):
        SubprocessRunner()(("true",), stdin_stays_open=True, hands_over_the_terminal=True)
