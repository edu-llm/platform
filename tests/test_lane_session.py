"""How a laptop reaches a lane machine, and the one thing on the laptop that has to exist.

MEASURED RATHER THAN CHOSEN. The mechanism is Systems Manager Session Manager, and the reason is
in the plan this file comes from: on 2026-08-05 an instance in the platform's own VPC answered
Online with agent 3.3.4793.0, all six subnets in that VPC route to an internet gateway, the
security group there has zero ingress rules, and Session Manager is free on EC2. Nothing had to
be built for any of that.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from edullm_platform.cli.lane import (
    SESSION_PLUGIN,
    agent_online_argv,
    command_line,
    load_working_tier_settings,
    missing_plugin_refusal,
    notebook_forward_argv,
    remote_command_argv,
    remote_script,
    shell_session_argv,
    ssh_proxy_command,
    under_a_shell,
)

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
    line = ssh_proxy_command(INSTANCE)

    assert line.startswith("ProxyCommand sh -c ")
    assert "AWS-StartSSHSession" in line
    assert INSTANCE in line


def test_a_missing_plugin_is_an_installation_and_says_so() -> None:
    """Mutation: let the aws CLI's own message through.

    Without the plugin, `aws ssm start-session` prints "SessionManagerPlugin is not found" and a
    documentation URL, which is a good message about a tool nobody told the researcher they
    needed. The refusal names the tool, says why the lane needs it, and does not read as a
    verdict on anything they asked for.
    """
    refusal = missing_plugin_refusal()

    assert refusal.code == "session_plugin_missing"
    assert SESSION_PLUGIN in refusal.detail
    assert len(refusal.detail.split()) > 20


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
    assert "aws ssm start-session" in ssh_proxy_command(INSTANCE)
