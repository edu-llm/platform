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
    ACCESS_REQUEST_COMMAND,
    ARM_MACHINES,
    AWS_BROKER,
    AWS_LOGIN_COMMAND,
    AWS_PROFILE_COMMAND,
    AWS_PROFILE_VARIABLE,
    PLUGIN_DOWNLOADS,
    SESSION_PLUGIN,
    agent_online_argv,
    aws_config_path,
    broker_profiles,
    command_line,
    interactive_script,
    load_working_tier_settings,
    missing_broker_refusal,
    missing_plugin_refusal,
    notebook_forward_argv,
    plugin_install_commands,
    read_aws_config,
    remote_command_argv,
    remote_script,
    resolve_aws_profile,
    shell_session_argv,
    ssh_proxy_command,
    under_a_shell,
)
from edullm_platform.cli.workspace import SubprocessRunner
from tests.cli_support import FAKE_ACCOUNT

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS = load_working_tier_settings(PROJECT_ROOT / "config")
INSTANCE = "i-0000000000000aaaa"


def test_a_shell_stands_in_the_work_directory_with_the_machine_s_own_environment() -> None:
    """**THE ACCOUNT'S DEFAULT SESSION PREFERENCE IS NOT A SHELL ANYBODY WOULD CHOOSE.**
    Mutation: go back to no document, which is what shipped.

    Measured through ``edullm shell`` against this account on 2026-08-06. The preference runs
    ``sh``; ``shopt`` is not a command in it; it is not a login shell, so ``PATH`` carries no
    CUDA and ``LD_LIBRARY_PATH`` is unset; and it stands the researcher in
    ``/var/snap/amazon-ssm-agent/13349``, which is the agent's own directory and holds none of
    their files. ``edullm run`` puts the tree in ``/work/<project>`` and runs there.

    The reason recorded against naming a document -- that it "would run one command and exit,
    which is the other verb" -- is answered by what the command is. It ends in ``exec bash -i``,
    so the one command is the shell, and the session is attached to it rather than to a wrapper.
    """
    argv = shell_session_argv(INSTANCE, uri="s3://a/b/", project="p")
    command = json.loads(argv[-1])["command"][0]

    assert argv[:5] == ("aws", "ssm", "start-session", "--target", INSTANCE)
    assert "AWS-StartInteractiveCommand" in argv
    assert shlex.split(command)[:2] == ["bash", "-lc"]
    assert shlex.split(command)[2] == interactive_script(uri="s3://a/b/", project="p")


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
    assert json.loads(argv[-1]) == {"command": ["bash -lc 'echo hello'"]}


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
    assert under_a_shell("echo a; echo b") == "bash -lc 'echo a; echo b'"

    wrapped = under_a_shell("""echo "it's" quoted""")
    assert shlex.split(wrapped)[:2] == ["bash", "-lc"]
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


# ---------------------------------------------------------------------------------------
# the broker, which precedes the plugin, and the profile it writes
# ---------------------------------------------------------------------------------------

#: The three lines per profile ``sb-aws-creds install-profiles`` writes, copied off a laptop it
#: had been run on. No role ARN and no account among them, which is the whole reason
#: :func:`broker_profiles` keys on the command rather than on an ARN.
MANAGED_BLOCK = """\
# >>> sb-aws-creds (managed) >>>
# Generated by sb-aws-creds. Do not edit by hand.

[profile sbsandbox]
credential_process = sb-aws-creds credential_process --profile sbsandbox
region = us-east-1

# <<< sb-aws-creds (managed) <<<
"""

#: What somebody's other work looks like in the same file, and the trap an ARN-matching resolver
#: would fall into. The ``role_arn`` names an ``Intern-*`` role, so it matches the pattern the
#: lane's trust policy admits -- and nothing in the file says whose account it is in.
#:
#: The account is interpolated rather than written. ``tests/test_evidence.py`` scans the tracked
#: tree for anything shaped like an AWS account ID and fails on one, which is the same rule that
#: makes the real account unavailable to the resolver as a literal to compare against.
SOMEBODY_ELSES_WORK = f"""\
[default]
region = eu-west-2

[profile dayjob]
region = eu-west-2
role_arn = arn:aws:iam::{FAKE_ACCOUNT}:role/Intern-frank.gonzalez-sbsandbox
source_profile = default

[profile dayjob-sso]
sso_session = acme
sso_account_id = {FAKE_ACCOUNT}
sso_role_name = Developer
"""


def test_a_missing_broker_routes_to_the_ask_queue_and_prints_no_install_command() -> None:
    """**THE ONE ASSERTION THIS WHOLE REFUSAL EXISTS FOR.**
    Mutation: name an install command -- any of them.

    ``sb-aws-creds`` cannot be self-served. Its ``package.json`` marks it ``private``, so it has
    never been on npm and ``npm view`` answers 404; it is built out of a private repository in
    an organization this roster is not in, so ``git clone`` answers 404 too. Every working copy
    in this organization was handed over as a tarball by somebody who already had one.

    So an install line here would send fifteen blocked people to a 404 and cost each of them the
    afternoon the refusal exists to save -- which is the exact failure being corrected, since
    what they get today is a shell "command not found" that also has nothing to offer. The
    tarball's own README demonstrates the hazard rather than avoiding it: it names ``pipx
    install git+...`` for a package that is Node and TypeScript.

    ``brew`` is in the list because the working install on the laptop this was traced from is
    under ``/opt/homebrew/bin``, which invites exactly the wrong conclusion. It is a symlink
    ``npm install -g`` left there, and no formula exists.
    """
    refusal = missing_broker_refusal()

    assert refusal.code == "aws_broker_missing"
    assert AWS_BROKER in refusal.detail
    assert ACCESS_REQUEST_COMMAND in refusal.detail
    for invented in (
        "npm install",
        "npm i ",
        "pipx install",
        "brew install",
        "git clone",
        "pip install",
    ):
        assert invented not in refusal.detail


def test_the_profile_is_found_by_the_broker_on_it_rather_than_by_a_role_arn() -> None:
    """**THE DESIGN DECISION THAT LOOKS WRONG UNTIL YOU READ THE FILE.**
    Mutation: select on ``role/Intern-*`` in a ``role_arn`` instead.

    The lane's trust policy admits ``ArnLike .../role/Intern-*`` and nothing else, so an
    Intern role is indeed the only profile that could work -- but the ARN is not in
    ``~/.aws/config`` to be read. ``install-profiles`` prints ``[sbsandbox] -> arn:...:role/
    Intern-<person>-sbsandbox`` to its own stdout and writes a section header, a
    ``credential_process`` and a region. A resolver keying on the ARN matches nothing the
    broker has ever written.

    The fixture above is what makes the mutation fail rather than merely be unsupported: it
    carries a ``role_arn`` naming an ``Intern-*`` role, which is the pattern an ARN-matching
    resolver would select on, in a profile belonging to somebody's other work. Narrowing that to
    one account is not available either -- a twelve-digit account ID is in
    ``evidence.SECRET_PATTERNS`` and ``tests/test_evidence.py`` fails on one anywhere in the
    tracked tree, so there is no literal here to compare against, and the real account arrives
    only from the ``sts:GetCallerIdentity`` this resolution has to precede.
    """
    assert broker_profiles(MANAGED_BLOCK + "\n" + SOMEBODY_ELSES_WORK) == ("sbsandbox",)


def test_a_bare_default_profile_is_never_the_one_this_picks() -> None:
    """Mutation: treat ``[default]`` as a profile named "default".

    The AWS CLI spells the default profile without the word, so a resolver splitting every
    section on whitespace and taking the second word either crashes on it or invents a name.
    Either way it is a profile the person did not pick, and picking it is how this would reach
    credentials belonging to somebody's other work.
    """
    with_a_broker_default = "[default]\ncredential_process = sb-aws-creds credential_process\n"

    assert broker_profiles(with_a_broker_default) == ()
    assert broker_profiles(SOMEBODY_ELSES_WORK) == ()


def test_the_broker_is_recognised_by_absolute_path_and_a_lookalike_is_not() -> None:
    """Mutation: test the whole command with ``in``, or compare it whole against the name.

    ``npm install -g`` leaves the broker as a symlink under a bin directory, and somebody who
    hit a PATH problem once may well have spelled that path out in their config; a resolution
    that only recognised the bare word would refuse a laptop that works. The other direction
    matters as much: a substring test would match a wrapper merely mentioning the broker in an
    argument, and select a profile whose credentials come from somewhere else entirely.
    """
    absolute = "[profile sbsandbox]\ncredential_process = /opt/homebrew/bin/sb-aws-creds cp\n"
    mentions_it = "[profile other]\ncredential_process = some-wrapper --like sb-aws-creds\n"

    assert broker_profiles(absolute) == ("sbsandbox",)
    assert broker_profiles(mentions_it) == ()


def test_a_credential_process_belongs_to_the_section_above_it_and_not_to_the_file() -> None:
    """Mutation: scan the file for the broker's name and take every profile header in it.

    One file holds both, which is the ordinary case for anybody who has this job and another
    one. Attributing the broker's line to the wrong section is how a resolution reports that it
    chose the platform's profile while handing the AWS CLI somebody else's.
    """
    both = SOMEBODY_ELSES_WORK + "\n" + MANAGED_BLOCK

    assert broker_profiles(both) == ("sbsandbox",)


def test_a_profile_the_person_set_is_never_overridden_and_never_reported() -> None:
    """Mutation: resolve anyway and prefer the broker's, or warn that theirs disagrees.

    Somebody who exported ``AWS_PROFILE`` has said which credential they want more explicitly
    than anything this could infer. Overruling that is worse than the silent selection this
    whole resolution exists to replace, and second-guessing it in a warning is noise printed at
    the one person who already knows.
    """
    resolved = resolve_aws_profile(MANAGED_BLOCK, declared="dayjob", path=Path("/c"))

    assert resolved.profile is None
    assert resolved.said is None
    assert resolved.refusal is None


def test_a_blank_aws_profile_is_not_a_choice_because_the_cli_does_not_read_it_as_one() -> None:
    """Mutation: treat any non-``None`` value as a declaration.

    ``AWS_PROFILE=`` is indistinguishable from unset to the AWS CLI, which falls back to
    ``default``. Honouring the empty string as a choice would leave this resolving nothing while
    the CLI resolved something else, and the machine would start under whichever credential
    ``default`` happens to be.
    """
    resolved = resolve_aws_profile(MANAGED_BLOCK, declared="   ", path=Path("/c"))

    assert resolved.profile == "sbsandbox"
    assert resolved.said is not None


def test_the_one_profile_is_chosen_and_the_choice_is_said_out_loud() -> None:
    """**CHOOSING A CREDENTIAL IS FINE; CHOOSING ONE SILENTLY IS THE THING TO AVOID.**
    Mutation: resolve it and print nothing.

    A person with an AWS profile for other work has to be able to see, on the line above the
    machine that starts billing, that this reached for the platform's profile and not theirs.
    The line names the file as well as the profile for the reason ``check`` names its
    configuration source: a resolution nobody can locate is one nobody can correct.
    """
    resolved = resolve_aws_profile(MANAGED_BLOCK, declared=None, path=Path("/home/x/.aws/config"))

    assert resolved.profile == "sbsandbox"
    assert resolved.refusal is None
    assert resolved.said is not None
    assert "sbsandbox" in resolved.said
    assert "/home/x/.aws/config" in resolved.said


def test_no_profile_at_all_names_the_second_step_rather_than_the_first() -> None:
    """Mutation: send them back to ``sb-aws-creds login``.

    ``login`` puts a refresh token in the keychain and writes nothing to ``~/.aws/config``, so
    somebody who ran it and stopped has a working credential no AWS client can find -- and
    telling them to run it again is telling them to repeat the step that already worked.
    ``install-profiles`` is what writes the profile and it is the one they are missing.

    The override is named too, because not everybody who holds an AWS session got it from the
    broker and a refusal with no way past it would break a setup this does not understand.
    """
    refusal = resolve_aws_profile("", declared=None, path=Path("/c")).refusal

    assert refusal is not None
    assert refusal.code == "no_broker_profile"
    assert AWS_PROFILE_COMMAND in refusal.detail
    assert AWS_PROFILE_VARIABLE in refusal.detail


def test_two_broker_profiles_are_named_and_asked_about_rather_than_chosen_between() -> None:
    """Mutation: pick the first, or prefer the one spelled ``sbsandbox``.

    Both are the platform's own credentials, so there is no unsafe answer here -- only an
    expensive one. Starting a machine on the production account for somebody who meant the
    sandbox spends real money and reports success. Nothing in the file says which account each
    label reaches, so there is nothing here that could break the tie, and a precedence rule
    invented from the spelling would be a guess that reads like a fact.
    """
    two = MANAGED_BLOCK + (
        "[profile sbproduction]\n"
        "credential_process = sb-aws-creds credential_process --profile sbproduction\n"
    )

    resolved = resolve_aws_profile(two, declared=None, path=Path("/c"))

    assert resolved.profile is None
    assert resolved.refusal is not None
    assert resolved.refusal.code == "aws_profile_is_ambiguous"
    assert "sbsandbox" in resolved.refusal.detail
    assert "sbproduction" in resolved.refusal.detail


def test_a_config_file_that_is_not_there_reads_as_no_profiles_rather_than_raising() -> None:
    """Mutation: let the OSError out.

    No file is the ordinary state of a laptop that has never run the broker's second step,
    which is precisely the laptop this refusal is written for. A traceback there would replace
    the one sentence that says which command to run.
    """
    assert read_aws_config(Path("/does/not/exist/config")) == ""
    assert broker_profiles("this is not an ini file at all") == ()


def test_the_config_file_is_read_from_where_the_aws_cli_would_read_it() -> None:
    """Mutation: always use ``~/.aws/config``.

    ``AWS_CONFIG_FILE`` moves the file for the AWS CLI, for the SDKs and for the broker itself
    -- ``install-profiles`` writes wherever that variable points. Reading a different file from
    the one the CLI is about to read would refuse over a profile that is present, or resolve one
    the CLI cannot see.
    """
    home = Path("/home/x")

    assert aws_config_path({}, home=home) == home / ".aws" / "config"
    assert aws_config_path({"AWS_CONFIG_FILE": "/tmp/elsewhere"}, home=home) == Path(
        "/tmp/elsewhere"
    )


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
def test_the_plugin_refusal_names_the_wall_after_it_and_its_own_place(
    system: str, machine: str, has_dpkg: bool
) -> None:
    """**THE THREE PREREQUISITES ARE ORDERED AND EACH ONE HAS TO SAY WHERE IT SITS.**
    Mutation: put back "there are two prerequisites and this is the first of them".

    `cli/main.py`'s `_lane_session` checks the broker, then the plugin, then a profile, all
    before it calls `sts:GetCallerIdentity`. So this is the second wall a newcomer meets and
    the profile is the third. Somebody who installs the plugin, believes they are finished,
    and then meets another refusal has been made to discover the shape of the setup one wall
    at a time. Naming the next one costs a sentence here and saves an attempt there.

    The count is asserted and not just the ordering word, because "first of two" was true
    until the broker check landed in front of this one and is the exact claim that goes
    stale when a wall is added. A test that only looked for an ordering word would have gone
    on passing over it.
    """
    detail = missing_plugin_refusal(system=system, machine=machine, has_dpkg=has_dpkg).detail

    assert AWS_LOGIN_COMMAND in detail
    assert "second" in detail, "the ordering is the fact, not merely that a next thing exists"
    assert "three prerequisites" in detail, "the count moved when the broker went in front"
    assert "first" not in detail, "the plugin stopped being the first wall on 2026-08-06"


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
        shell_session_argv(INSTANCE, uri="s3://a/b/", project="p"),
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
