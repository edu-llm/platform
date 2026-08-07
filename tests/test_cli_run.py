"""``edullm run``: a machine, a command, and nothing checked on the way.

THE CASE THIS FILE EXISTS FOR IS THE FIRST ONE. A researcher standing in a directory that is not
a registered repository, with uncommitted changes, on a commit nobody pushed, gets a machine. The
spec calls that out by name: check refuses unregistered_repository, the lookup sits in
run_preflight, and a second verb calling run_preflight would pick it up for free.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from edullm_platform.cli.main import EXIT_OK, EXIT_REFUSED, EXIT_UNREACHABLE, EXIT_UNUSABLE
from tests.cli_support import (
    LANE_EXISTING_EXPIRY,
    LANE_INSTANCE,
    FakeRunner,
    git_answers,
    invoke,
    lane_answers,
)


def a_laptop(tmp_path: Path, **overrides: object) -> FakeRunner:
    repository = str(overrides.pop("repository", "OLMo-core"))
    return FakeRunner({**git_answers(tmp_path, repository=repository), **lane_answers(**overrides)})


def test_a_machine_is_started_in_a_directory_nothing_registers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE ONE WIRE THE SPEC NAMES, ASSERTED AS AN OUTCOME.**

    Mutation: call run_preflight from _run. The repository here is not one of the five
    config/repositories.yaml carries, and `edullm check` in this directory refuses
    unregistered_repository. `edullm run` starts a machine, because the lane is ungated: you get
    a machine, you do what you like, nothing is checked and nothing is recorded as citable.
    """
    runner = a_laptop(tmp_path, repository="somebodys-personal-scratchpad")

    code, out, err = invoke(
        ["run", "--project", "mixlaw", "--compute", "gpu-1xt4", "--", "python", "-V"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert runner.ran("aws", "ec2", "run-instances")
    assert "unregistered_repository" not in out + err


def test_an_uncommitted_dirty_tree_is_exactly_what_this_verb_is_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: reuse working_tree_refusals.

    Exploring is what somebody does with uncommitted changes, and the refusal that exists so a
    submission names the commit it built from has nothing to say here: nothing is built, nothing
    is recorded, and what runs is the bytes on the laptop rather than a commit.
    """
    runner = FakeRunner(
        {**git_answers(tmp_path, dirty=["train.py"], pushed=False), **lane_answers()}
    )

    code, out, err = invoke(
        ["run", "--project", "mixlaw", "--compute", "gpu-1xt4", "--", "python", "-V"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert "uncommitted_changes" not in out + err
    assert "commit_not_pushed" not in out + err


def test_the_tree_goes_up_before_the_command_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: run the command and never upload.

    "Ship this working tree to a machine" is the verb's own sentence. Without the upload the
    remote sync pulls down whatever was there before, which is the previous run on a reused
    machine and nothing at all on a new one.
    """
    runner = a_laptop(tmp_path)

    invoke(
        ["run", "--project", "mixlaw", "--compute", "gpu-1xt4", "--", "python", "-V"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    synced = runner.ran("aws", "s3", "sync")
    started = runner.ran("aws", "ssm", "start-session")

    assert synced, runner.calls
    assert "s3://edullm-scratch/caiiris/mixlaw/" in " ".join(synced[0])
    assert runner.calls.index(synced[0]) < runner.calls.index(started[0])


def test_the_upload_carries_the_lane_credential_and_not_the_laptop_s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: run the sync with no env.

    The researcher's own session cannot write to the working tier; the assumed lane role can, and
    only under its own source identity. A sync that went out on the ambient credential would fail
    with AccessDenied naming nothing, on the one call carrying the researcher's actual work.
    """
    runner = a_laptop(tmp_path)

    invoke(
        ["run", "--project", "mixlaw", "--compute", "gpu-1xt4", "--", "python", "-V"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    synced = runner.ran("aws", "s3", "sync")[0]
    carried = runner.environments[runner.calls.index(synced)]

    assert carried["AWS_SESSION_TOKEN"] == "token"


def test_an_existing_machine_for_this_project_is_reused_rather_than_doubled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: launch unconditionally.

    A second run five minutes after the first would otherwise start a second machine, and both
    would bill until the janitor reached them. One machine per person per project is the whole of
    the scheduling this route has.
    """
    runner = a_laptop(tmp_path, existing="i-0000000000000aaaa")

    code, out, err = invoke(
        ["run", "--project", "mixlaw", "--compute", "gpu-1xt4", "--", "python", "-V"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert runner.ran("aws", "ec2", "run-instances") == []


def test_nothing_claims_to_start_a_shape_when_a_machine_was_found_instead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print ``DefaultedCompute.said`` where the profile is resolved, above the
    branch that looks for an existing machine, which is where it went in first.

    That ordering had the verb say "this starts gpu-1xl4: g6.xlarge at $0.8048/hour" and then
    "found that machine rather than starting one", three lines apart. Both cannot be true, and
    the rate was the worse half: reuse does not check that the machine it found is the shape
    anybody asked for, so the figure quoted belonged to a machine the person was not getting.

    Asserted on the reuse path with no ``--compute`` -- the only combination that produced it --
    and paired with the launch path below, because a fix that simply stopped printing the
    sentence would pass this and take the announcement with it.
    """
    runner = a_laptop(tmp_path, existing=LANE_INSTANCE)

    code, out, err = invoke(
        ["run", "--project", "mixlaw", "--", "python", "-V"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert runner.ran("aws", "ec2", "run-instances") == []
    assert "starts" not in out + err, out + err


def test_a_defaulted_shape_is_still_named_with_its_rate_when_one_does_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: delete the announcement rather than moving it past the reuse branch.

    The pair to the test above, and the reason it is a pair. The objection to answering
    ``--compute`` at all is that it spends money nobody named, and the answer is that the shape
    and its hourly rate are printed before the call that spends it. A fix for the reuse case
    that dropped the sentence would satisfy the other test and give up the whole defence.
    """
    runner = a_laptop(tmp_path)

    code, out, err = invoke(
        ["run", "--project", "mixlaw", "--", "python", "-V"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert runner.ran("aws", "ec2", "run-instances") != []
    assert "/hour" in out + err, out + err


def test_a_reused_machine_is_told_the_expiry_its_tag_carries_and_not_a_fresh_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE STALE-EXPIRY DEFECT, AT THE VERB RATHER THAN AT THE FUNCTION.
    Mutation: compute the expiry from this invocation's clock, which is what shipped.

    The janitor stops the machine seconds after the instant on its ``ExpiresAt`` tag, and that
    tag keeps the value launch wrote because the researcher role is denied ``ec2:CreateTags`` on
    it after ``RunInstances``. A verb that printed a fresh eight hours told somebody they had
    until this evening when the sweep was coming for them within the hour.

    Asserted as the tag's value appearing and every other instant being absent, rather than as a
    substring of one sentence, because the sentence is prose and will be reworded. The fixture's
    tag is a round instant no arithmetic against the test clock reaches, so this cannot pass by
    coincidence.
    """
    runner = a_laptop(tmp_path, existing=LANE_INSTANCE)

    code, out, err = invoke(
        ["run", "--project", "mixlaw", "--compute", "gpu-1xt4", "--", "python", "-V"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    printed = set(re.findall(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", out))
    assert printed == {LANE_EXISTING_EXPIRY}, out


def test_the_session_is_given_a_stdin_the_caller_s_own_cannot_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE VERB SIDE OF THE DEFECT THAT KILLED EVERY RUN WITHOUT A KEYBOARD.**
    Mutation: drop the flag, or set it on the sync instead.

    ``session-manager-plugin`` treats end of file on its standard input as the person hanging up
    and exits before the command's output comes back, so a ``run`` that inherited descriptor 0
    worked in a terminal and failed under ``nohup``, in CI, behind ``< /dev/null`` and under
    every agent -- reporting only that the session ended without saying what the command did.
    Measured against this account on 2026-08-06.

    The sync is checked too, and it is checked for the opposite: it is an ``aws s3`` call that
    reads nothing, and a pipe there would be a descriptor opened for no reason on every run.
    """
    runner = a_laptop(tmp_path)

    invoke(
        ["run", "--project", "mixlaw", "--compute", "gpu-1xt4", "--", "python", "-V"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert runner.held_stdin_open_for("aws", "ssm", "start-session") == [True]
    assert not any(runner.held_stdin_open_for("aws", "s3", "sync"))


def test_a_remote_command_that_failed_is_reported_as_refused_with_its_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: exit 0 whatever the sentinel says.

    start-session exits with the plugin's status rather than the remote command's, so a verb that
    passed that through would report success for every failed command. 1 rather than the raw
    status, because 2 and 3 are already spoken for and a script that could not tell a failed
    program from an unreachable platform is a script that retries the wrong one.
    """
    runner = a_laptop(tmp_path, remote_exit=7)

    code, out, err = invoke(
        ["run", "--project", "mixlaw", "--compute", "gpu-1xt4", "--", "false"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert "exited 7" in out + err


def test_a_session_that_died_without_a_verdict_is_the_platform_s_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: read a missing sentinel as success.

    A session that dropped judged nothing. Reporting that as a pass is the mapping that told a
    caller a command succeeded when the machine went away in the middle of it, which is a Spot
    interruption on any day.
    """
    runner = a_laptop(tmp_path, remote_exit=None)

    code, _, _ = invoke(
        ["run", "--project", "mixlaw", "--compute", "gpu-1xt4", "--", "python", "-V"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_UNREACHABLE


def test_the_remote_output_reaches_the_researcher_whatever_the_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print the stream only on success.

    A command that failed is the one whose output somebody needs. Swallowing it on the failing
    branch would leave "the command exited 7" and nothing to act on, which is worse than the raw
    aws error this verb replaces.
    """
    runner = a_laptop(tmp_path, remote_exit=7)

    _, out, _ = invoke(
        ["run", "--project", "mixlaw", "--compute", "gpu-1xt4", "--", "false"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert "hello from the machine" in out


def test_the_expiry_is_printed_before_anything_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE HALF OF THE DONE-CONDITION THAT IS ABOUT BEING WARNED.**
    Mutation: tag the machine and say nothing.

    The slice is done when a researcher "loses it on a schedule they were warned about". The tag
    is what the janitor reads and the line is the warning. A machine that expires silently
    teaches thirty-five people that the platform destroys work.

    Read with the wrapping taken back out, because these are paragraphs written for a terminal
    and the line breaks fall wherever the width puts them. This asserted against the raw stream
    and went red on a sentence that had not changed, only moved: a clause added above it pushed
    the wrap into the middle of the phrase.
    """
    runner = a_laptop(tmp_path)

    _, out, _ = invoke(
        ["run", "--project", "mixlaw", "--compute", "gpu-1xt4", "--", "python", "-V"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert "expires" in out
    assert "Nothing here is recorded as citable" in " ".join(out.split())


def test_nothing_this_verb_prints_carries_an_ansi_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: colour the expiry line.

    A piped run and a terminal run are the same bytes everywhere else in this binary, which is
    what makes a pasted transcript what the next person sees.
    """
    runner = a_laptop(tmp_path)

    _, out, err = invoke(
        ["run", "--project", "mixlaw", "--compute", "gpu-1xt4", "--", "python", "-V"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert "\x1b" not in out + err


def test_a_machine_nothing_prices_is_refused_before_a_credential_is_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: refuse after assuming the role.

    The refusal costs nothing and the assumption costs a call and a session. Refusing first is
    also what keeps a typo from producing a lane session nobody uses.
    """
    runner = a_laptop(tmp_path)

    code, _, err = invoke(
        ["run", "--project", "mixlaw", "--compute", "gpu-9000", "--", "python", "-V"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert "unknown_machine" in err
    assert runner.ran("aws", "sts", "assume-role") == []


def test_run_with_no_command_says_so_rather_than_starting_a_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: default the command to a shell.

    A machine started for no command is a machine billing until the janitor reaches it, and the
    verb that gives you a machine to sit at is the other one. Exit 2, because nothing was judged.
    """
    runner = a_laptop(tmp_path)

    code, _, _ = invoke(
        ["run", "--project", "mixlaw", "--compute", "gpu-1xt4"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_UNUSABLE
    assert runner.ran("aws") == []


def test_the_command_s_own_flags_reach_it_rather_than_this_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: parse the command with anything but REMAINDER.

    `edullm run ... -- python train.py --compute-budget 4` has a flag this binary also defines.
    Without REMAINDER argparse claims it, and the researcher's program runs without the argument
    it was given, which is a wrong answer rather than an error.
    """
    runner = a_laptop(tmp_path)

    code, out, err = invoke(
        [
            "run",
            "--project",
            "mixlaw",
            "--compute",
            "gpu-1xt4",
            "--",
            "python",
            "train.py",
            "--compute",
            "4",
        ],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    started = runner.ran("aws", "ssm", "start-session")[0]
    assert "python train.py --compute 4" in " ".join(started)
    assert "--instance-type" in runner.ran("aws", "ec2", "run-instances")[0]


def test_on_demand_is_what_a_plain_run_buys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: pass spot=True from the verb whatever the flag said.

    The argv builder defaults to On-Demand and tests/test_lane_launch.py holds it there. This is
    the other half: that the verb passes the flag through rather than deciding for itself.
    """
    runner = a_laptop(tmp_path)
    invoke(
        ["run", "--project", "mixlaw", "--compute", "gpu-1xt4", "--", "true"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )
    plain = " ".join(runner.ran("aws", "ec2", "run-instances")[0])

    runner = a_laptop(tmp_path)
    invoke(
        ["run", "--project", "mixlaw", "--compute", "gpu-1xt4", "--spot", "--", "true"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )
    asked = " ".join(runner.ran("aws", "ec2", "run-instances")[0])

    assert "MarketType=spot" not in plain
    assert "SpotInstanceType=persistent" in asked
