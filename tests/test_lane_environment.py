"""What a lane machine actually is, and whether the two verbs hand a person the same one.

MEASURED AGAINST THIS ACCOUNT ON 2026-08-06, on ami-0326665395a428ccf out of
``GPU_AMI_PARAMETER``, from a g4dn.xlarge started through ``edullm run``. Everything asserted
below is a reading off that machine rather than a reading of Amazon's documentation.

  * ``/opt`` holds ``amazon aws cni containerd dlami nvidia`` and no ``conda`` and no
    ``pytorch``. ``/opt/dlami`` holds one directory and it is an NVMe helper.
  * ``find / -xdev -maxdepth 6 -name activate -path '*/bin/activate'`` matched nothing, and
    ``find / -xdev -maxdepth 8 -name torch -type d`` matched nothing. There is no environment
    holding torch, because there is no environment.
  * The only interpreter is ``/usr/bin/python3``, 3.10.12. There is no ``python``, and
    ``python-is-python3`` is not installed.
  * ``/etc/profile.d/dlami.sh`` is where the image says how it is meant to be used: four CUDA
    ``bin`` directories on ``PATH`` and the matching ``lib``, ``lib64``, CUPTI and OpenMPI
    directories on ``LD_LIBRARY_PATH``. A non-login shell reads none of it.
  * The account's default session preference -- what ``edullm shell`` opened until this file
    existed -- runs ``sh``, not a login shell, in ``/var/snap/amazon-ssm-agent/13349``.

So the failure the first ``edullm run`` hit was two failures wearing one message, and this
module is about the second: not that ``python`` is spelled ``python3``, but that both verbs ran
in an environment poorer than the one the machine was built to offer, and ran in two different
poor environments at that.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from edullm_platform.cli.lane import (
    GPU_AMI_FAMILY,
    GPU_AMI_PARAMETER,
    carry_back_script,
    command_not_found_said,
    interactive_script,
    remote_script,
    shell_session_argv,
    under_a_shell,
    what_the_machine_carries,
    work_directory,
)
from edullm_platform.cli.main import EXIT_OK, EXIT_REFUSED
from tests.cli_support import FakeRunner, git_answers, invoke, lane_answers

URI = "s3://edullm-scratch/caiiris/mixlaw/"
PROJECT = "mixlaw"


def a_laptop(tmp_path: Path, **overrides: object) -> FakeRunner:
    return FakeRunner({**git_answers(tmp_path), **lane_answers(**overrides)})


def unwrapped(text: str) -> str:
    """A paragraph with the terminal wrapping taken back out.

    Every sentence this module asserts on is written for a person and wrapped to a width, so a
    raw substring match is really a match against where the line breaks happen to fall. One
    already went red on a sentence that had not changed, only moved.
    """
    return " ".join(text.split())


# ---------------------------------------------------------------------------------------
# what the image is, which is the fact every sentence below rests on
# ---------------------------------------------------------------------------------------


def test_the_lane_launches_a_base_image_and_the_sentence_about_it_depends_on_that() -> None:
    """**THE ONE ASSERTION HOLDING A CLAIM MADE TO EVERY RESEARCHER TO A FACT ABOUT THE IMAGE.**
    Mutation: repoint the lane at ``oss-nvidia-driver-gpu-pytorch-2.12-ubuntu-24.04``, which was
    in this account's parameter store on 2026-08-06 and is a two-word edit away.

    ``what_the_machine_carries`` tells everybody who starts a machine that there is no torch on
    it. That is true of the ``base-`` families and false of the framework ones, and the failure
    if it drifts is silent and pointed the wrong way: a researcher told there is no torch, on a
    machine that has one, installs a second and gets whichever the path finds.

    The parameter is built from the family rather than written out beside it, so the two cannot
    disagree, and this pins the word the sentence depends on.
    """
    assert GPU_AMI_FAMILY.startswith("base-")
    assert GPU_AMI_PARAMETER.endswith(f"/{GPU_AMI_FAMILY}/latest/ami-id")
    assert GPU_AMI_FAMILY in what_the_machine_carries()


def test_the_machine_is_described_as_carrying_no_framework_rather_than_left_to_be_found() -> None:
    """**THE FIRST edullm run ANYBODY EVER MADE FAILED HERE AND WAS TOLD NOTHING ABOUT IT.**
    Mutation: drop the sentence and let people find out by importing.

    It asked for ``torch.cuda.get_device_name(0)`` on a healthy T4 and got a shell error. The
    reasonable inference from a deep-learning AMI, a working driver and a failing import is that
    torch is in an environment nothing activated -- and there is no environment. Nothing in the
    output said so, so the next person would have spent the same half hour.

    The three facts are the image, the absence, and that nothing here fills it. The last is the
    one that keeps somebody from waiting for the platform to install torch for them.
    """
    said = what_the_machine_carries()

    assert "no torch" in said
    assert "python3" in said
    assert "installs one" in said


# ---------------------------------------------------------------------------------------
# the environment, which is the same for both verbs or it is worth nothing
# ---------------------------------------------------------------------------------------


def test_a_command_runs_in_the_environment_the_image_configures_rather_than_a_bare_one() -> None:
    """**THE DEFECT: A GPU MACHINE HANDED OVER WITH ITS OWN TOOLS OFF THE PATH.**
    Mutation: go back to ``bash -c``, which is what shipped.

    Measured on one instance one second apart on 2026-08-06. Without ``-l``: ``PATH`` is
    ``/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:...``, ``LD_LIBRARY_PATH`` is
    unset, ``nvcc`` is not found. With it: four CUDA ``bin`` directories, ``/opt/amazon/efa/bin``
    and ``/opt/amazon/openmpi/bin`` on ``PATH``, the matching library directories on
    ``LD_LIBRARY_PATH``, and ``nvcc`` at ``/usr/local/cuda-13.2/bin/nvcc``.

    A login shell is not an environment chosen for the researcher. It is the machine as its
    builders configured it, stated in ``/etc/profile.d``, and it is what a person already got by
    sitting down at a prompt.
    """
    assert under_a_shell("nvcc --version") == "bash -lc 'nvcc --version'"
    assert shlex.split(under_a_shell("true"))[:2] == ["bash", "-lc"]


def test_both_verbs_reach_the_machine_through_the_same_wrapper() -> None:
    """**THE PROPERTY THE BRIEF ASKED FOR, ASSERTED AS ONE FUNCTION RATHER THAN TWO SPELLINGS.**
    Mutation: give the shell its own wrapper, or its own flags.

    A person who debugs at a prompt and then scripts the same thing with ``run`` must not be
    handed two environments. Two call sites spelling ``bash -lc`` separately would agree today
    and drift the first time one of them is edited, so the assertion is that the shell's own
    command is the same wrapper applied to the shell's own script.
    """
    opened = shell_session_argv("i-0000000000000aaaa", uri=URI, project=PROJECT)
    command = json.loads(opened[-1])["command"][0]

    assert command == under_a_shell(interactive_script(uri=URI, project=PROJECT))


def test_both_verbs_stand_the_researcher_in_the_same_directory() -> None:
    """**THEY STOOD IN DIFFERENT ONES, AND ONE OF THEM WAS THE SSM AGENT'S.**
    Mutation: let the shell open wherever the session lands, which is what shipped.

    ``run`` puts the tree in ``/work/<project>`` and runs there. The default session preference
    put the researcher in ``/var/snap/amazon-ssm-agent/13349``, measured on 2026-08-06, which
    holds none of their files and is not a directory anybody would guess. So "run it, then go
    and look at what it left" ended with somebody typing ``ls`` and seeing the agent's snap.

    Asserted through ``work_directory`` on both sides rather than against the literal path,
    because the property is that they agree and not what they agree on.
    """
    directory = work_directory(PROJECT)

    assert f"cd {directory}" in remote_script(uri=URI, project=PROJECT, command="true")
    assert f"cd {directory}" in interactive_script(uri=URI, project=PROJECT)
    assert directory in carry_back_script(uri=URI, project=PROJECT)


def test_the_shell_the_researcher_types_into_is_the_process_the_session_is_attached_to() -> None:
    """Mutation: call bash instead of exec-ing it, or exec ``bash -l`` rather than ``bash -i``.

    ``exec`` is what makes leaving with Ctrl-D end the session rather than return to a wrapper
    that then exits anyway. ``-i`` rather than ``-l`` is because the wrapper is already a login
    shell and the child inherits its environment: a second login sources
    ``/etc/profile.d/dlami.sh`` twice and puts every CUDA directory on ``PATH`` twice, which is
    harmless and reads as a bug to whoever prints it first.
    """
    script = interactive_script(uri=URI, project=PROJECT)

    assert script.rstrip().endswith("exec bash -i")
    assert "bash -l" not in script


# ---------------------------------------------------------------------------------------
# the tree, which is part of the environment and was in one verb only
# ---------------------------------------------------------------------------------------


def test_a_shell_ships_the_tree_and_carries_it_back_the_way_run_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE SHELL SHIPPED NOTHING, WHICH IS THE DIFFERENCE THAT ACTUALLY BITES.**
    Mutation: drop either sync.

    ``run``'s own help says it ships this working tree. ``shell`` said nothing about a tree and
    shipped none, so the two verbs disagreed about which files exist -- a bigger difference than
    the ``PATH`` and a harder one to notice, because an empty directory looks like a directory.

    Both directions are asserted and in order. The upload is what makes the prompt see what the
    laptop has; the carry-back is what makes the paragraph printed on the way in true, and
    without it standing somebody in ``/work/<project>`` would be a trap rather than a fix.
    """
    runner = a_laptop(tmp_path)

    code, out, err = invoke(
        ["shell", "--project", PROJECT, "--compute", "gpu-1xt4"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )
    uploaded = runner.ran("aws", "s3", "sync")
    sessions = runner.ran("aws", "ssm", "start-session")

    assert code == EXIT_OK, out + err
    assert [argv[3] for argv in uploaded] == [str(tmp_path)]
    assert uploaded[0][4].startswith("s3://")
    assert len(sessions) == 2
    assert carry_back_script(uri=uploaded[0][4], project=PROJECT) in sessions[1][-1]


def test_a_shell_uploads_with_the_same_exclusion_run_uses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: exclude something different, or nothing, on one of the two verbs.

    The two uploads are the same upload of the same directory to the same prefix, and a verb
    that excluded a different set would make the tree on the machine depend on which verb
    happened to touch it last. What is excluded is a separate question and the answer is
    presently ``.git/*`` on both.
    """
    for verb, extra in (("shell", []), ("run", ["--", "true"])):
        runner = a_laptop(tmp_path)
        invoke(
            [verb, "--project", PROJECT, "--compute", "gpu-1xt4", *extra],
            runner=runner,
            cwd=tmp_path,
            monkeypatch=monkeypatch,
        )
        uploaded = runner.ran("aws", "s3", "sync")[0]

        assert uploaded[-3:] == ("--exclude", ".git/*", "--only-show-errors"), verb


def test_a_forwarded_notebook_ships_nothing_and_does_not_claim_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: sync for the forward too, for symmetry with the shell.

    A port forward runs nothing on the machine and opens no directory. The Jupyter it reaches is
    one the researcher started themselves, from a shell, which is the branch that ships. Syncing
    here would mean a second session on the way to a tunnel, fetching a tree for a process that
    is already running -- and the paragraph would have to promise a carry-back that nothing
    performs, because a forward has no moment at which the person is finished with a directory.
    """
    runner = a_laptop(tmp_path)

    _, out, _ = invoke(
        ["shell", "--project", PROJECT, "--compute", "gpu-1xt4", "--notebook"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert runner.ran("aws", "s3", "sync") == []
    assert work_directory(PROJECT) not in out
    assert "carried back" not in unwrapped(out)


# ---------------------------------------------------------------------------------------
# python, and the decision to leave the machine alone
# ---------------------------------------------------------------------------------------


def test_a_command_the_machine_cannot_find_is_told_apart_from_a_command_that_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**WHAT THE FIRST edullm run GOT WAS "the command exited 127" AND NOTHING ELSE.**
    Mutation: print the number for every status, which is what shipped.

    127 is the shell saying it could not find the command at all, so it is a fact about what is
    installed on the machine rather than a verdict on the researcher's program -- and on this
    image the overwhelmingly likely cause is ``python``. Every other non-zero status is the
    researcher's own program and gets the number, because a paragraph about the platform under a
    failing test would be noise on the run where it is wrong.
    """
    runner = a_laptop(tmp_path, remote_exit=127)

    code, _, err = invoke(
        ["run", "--project", PROJECT, "--compute", "gpu-1xt4", "--", "python", "-V"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert "the command exited 127" in err
    assert "python3" in unwrapped(err)


def test_an_ordinary_failure_gets_the_number_and_no_lecture_about_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print the 127 paragraph on every non-zero status.

    A failing test suite exits 1 and a killed process exits 137, and neither is about anything
    this platform installed. Telling somebody about ``python3`` under their own failing assertion
    is the kind of always-on advice people learn to read past, which costs the message its
    effect on the one run where it is the answer.
    """
    runner = a_laptop(tmp_path, remote_exit=1)

    code, _, err = invoke(
        ["run", "--project", PROJECT, "--compute", "gpu-1xt4", "--", "pytest"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert "the command exited 1" in err
    assert "python3" not in err


def test_nothing_in_the_lane_creates_an_unversioned_python_on_the_machine() -> None:
    """**THE DECISION, ASSERTED WHERE SOMEBODY WOULD OTHERWISE QUIETLY REVERSE IT.**
    Mutation: add ``python-is-python3``, a symlink into ``/usr/local/bin``, or an alias, to
    either script.

    Ubuntu 22.04 ships no unversioned ``python`` on purpose. Three reasons the lane does not add
    one. It is the same act as activating an environment on somebody's behalf: a silent change
    to the machine, on every launch, that a researcher shipping their own interpreter would not
    expect. It cannot be made true where it matters, because the recorded path runs in an image
    built from the researcher's own repository and ``config/repositories.yaml`` has both
    ``docker.io/library/python``, which has a ``python``, and ``docker.io/nvidia/cuda``, which
    has neither until a Dockerfile installs one -- so a lane that always had ``python`` teaches a
    habit half the registered repositories break, and the submission is the expensive one to
    break. And what actually failed the first researcher was a message that said nothing about
    where they were, which is fixed by a message.

    Asserted on both scripts, because the two verbs must agree about this as about everything
    else here: a ``python`` that exists in the shell and not under ``run`` is worse than neither.
    """
    both = (
        remote_script(uri=URI, project=PROJECT, command="true"),
        interactive_script(uri=URI, project=PROJECT),
    )

    for script in both:
        assert "python-is-python3" not in script
        assert "alias python" not in script
        assert "ln -s" not in script
    assert "no unversioned python" in command_not_found_said()
