"""The three walls a lane verb puts up before it makes a call, and the order they stand in.

WHY THERE ARE THREE AND WHY THE ORDER IS NOT ARBITRARY. ``edullm run`` and ``edullm shell``
need a credential broker, a Session Manager plugin and a profile to run under, and the first
of those produces the credential the third one selects. So the broker is not one prerequisite
beside two others -- it is upstream of both, and a laptop without it fails every later step
for a reason none of them names.

WHAT THIS FILE IS ACTUALLY GUARDING, WHICH IS A POPULATION RATHER THAN A CODE PATH. Twenty
people in this organization have the broker working and fifteen do not, and
``infra/iam/researcher-role.yaml`` draws the same split from the other side: the lane's trust
policy admits ``role/Intern-*``, twenty hold such a role and fifteen hold none. Until
2026-08-06 nothing checked for the broker at all, so those fifteen reached ``credential_process``
and got a shell "command not found" -- not a refusal, no code to search for, and no route to
the queue that could help. The cases below are what stop that returning.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from edullm_platform.cli.lane import ACCESS_REQUEST_COMMAND, AWS_BROKER, AWS_PROFILE_VARIABLE
from edullm_platform.cli.main import EXIT_OK, EXIT_UNUSABLE
from tests.cli_support import (
    ONE_BROKER_PROFILE,
    FakeRunner,
    git_answers,
    invoke,
    lane_answers,
)

#: Both lane verbs, because both go through ``_lane_session`` and a wall that went up in one
#: of them and not the other would leave half the population exactly where they started.
LANE_VERBS = (
    ["run", "--project", "mixlaw", "--compute", "gpu-1xt4", "--", "python", "-V"],
    ["shell", "--project", "mixlaw", "--compute", "gpu-1xt4"],
)


def a_laptop(tmp_path: Path, **overrides: object) -> FakeRunner:
    return FakeRunner({**git_answers(tmp_path), **lane_answers(**overrides)})


@pytest.mark.parametrize("argv", LANE_VERBS, ids=["run", "shell"])
def test_a_laptop_without_the_broker_is_refused_before_anything_is_asked(
    argv: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE FIFTEEN. Mutation: drop the check, or move it below the plugin.**

    What these people get today is ``sb-aws-creds: command not found`` on standard error from
    inside ``credential_process``, wrapped in an AWS CLI message about credentials. It names no
    refusal code, offers nothing to do, and reads as a broken laptop rather than as missing
    access -- which is why fifteen of them have been stuck on it separately instead of once.

    Nothing is asked of AWS, which is the second half of the property: the refusal is free and
    local, so it arrives immediately rather than after a call that was never going to work.
    """
    runner = a_laptop(tmp_path)

    code, out, err = invoke(
        argv, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch, broker=False
    )

    assert code == EXIT_UNUSABLE, out + err
    assert "aws_broker_missing" in err
    assert runner.ran("aws") == []


def test_the_broker_is_the_first_wall_and_not_the_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE ORDERING, ASSERTED WHERE BOTH ARE MISSING BECAUSE THAT IS THE ONLY PLACE IT SHOWS.**
    Mutation: check the plugin first.

    A newcomer has neither. Told about the plugin first they install it -- a real download, a
    package, a ``sudo`` -- clear that wall, and then meet the broker, which they cannot install
    at all. The two refusals are not interchangeable: one names a thing they can do and the
    other names a thing only somebody else can grant, so the order decides whether the
    afternoon is spent before or after learning the afternoon was unnecessary.
    """
    runner = a_laptop(tmp_path)

    code, out, err = invoke(
        LANE_VERBS[0],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
        broker=False,
        plugin=False,
    )

    assert code == EXIT_UNUSABLE, out + err
    assert "aws_broker_missing" in err
    assert "session_plugin_missing" not in err


def test_the_refusal_a_blocked_person_gets_offers_the_queue_and_no_install_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print an install command in the rendered refusal.

    The unit case in ``tests/test_lane_session.py`` holds the refusal's own text. This one holds
    what actually reaches the terminal, because a caller that helpfully appended a suggestion
    beneath the wrapped detail -- which is exactly how the Session Manager plugin's install
    lines are printed -- would put a 404 in front of the reader with the refusal still clean.
    """
    runner = a_laptop(tmp_path)

    _, out, err = invoke(
        LANE_VERBS[0], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch, broker=False
    )

    assert ACCESS_REQUEST_COMMAND in err
    for invented in ("npm install", "pipx install", "brew install", "git clone"):
        assert invented not in out + err


@pytest.mark.parametrize("argv", LANE_VERBS, ids=["run", "shell"])
def test_the_profile_is_resolved_carried_and_said_out_loud(
    argv: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE THIRD STEP, WHICH USED TO BE A PERSON'S AND WAS PER-TERMINAL.**
    Mutation: resolve it and print nothing, or print it and carry nothing.

    ``install-profiles`` writes a profile and makes it nothing's default, so reaching the lane
    took ``AWS_PROFILE=sbsandbox`` in front of every command. That is per-terminal, so somebody
    got through it once and met an identical refusal in the next tab.

    Both halves are asserted together on purpose. Carrying the profile without saying so is a
    silent credential selection, which is the thing to avoid; saying so without carrying it is a
    line that claims something untrue and a call that still fails.
    """
    runner = a_laptop(tmp_path)

    code, out, err = invoke(argv, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK, out + err
    assert "sbsandbox" in err
    assert runner.environment_for("aws", "sts", "get-caller-identity") == [
        {AWS_PROFILE_VARIABLE: "sbsandbox"}
    ]


def test_the_assume_role_call_runs_under_the_same_profile_as_the_identity_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: carry the profile on ``get-caller-identity`` only.

    Those are the two calls a lane verb makes as the person rather than as the lane, and the
    second is the one that turns their credential into the lane's. Proving an identity under one
    profile and then assuming from a different one -- or from none -- is the failure this
    resolution exists to remove, arriving one call later and much harder to read.

    Everything after ``assume-role`` carries the lane's own keys instead, which the last
    assertion pins: ``AWS_PROFILE`` beside them would be a second answer to a settled question.
    """
    runner = a_laptop(tmp_path)

    invoke(LANE_VERBS[0], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert runner.environment_for("aws", "sts", "assume-role") == [
        {AWS_PROFILE_VARIABLE: "sbsandbox"}
    ]
    for carried in runner.environment_for("aws", "s3", "sync"):
        assert AWS_PROFILE_VARIABLE not in carried
        assert "AWS_ACCESS_KEY_ID" in carried


def test_a_profile_the_person_exported_is_left_exactly_as_they_set_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: resolve anyway, or overlay the broker's profile on top of theirs.

    Somebody who exported ``AWS_PROFILE`` has answered this question more explicitly than
    anything here could infer, and the whole population this feature is for has been told for
    weeks to do exactly that. Overriding them would break the workaround on the day the
    workaround stopped being necessary.

    The empty mapping is the assertion that matters: nothing is added, so the call is the one
    this made before any of this existed and the person's own environment reaches ``aws``
    untouched.
    """
    runner = a_laptop(tmp_path)

    code, out, err = invoke(
        LANE_VERBS[0],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
        aws_profile="something-of-my-own",
    )

    assert code == EXIT_OK, out + err
    assert runner.environment_for("aws", "sts", "get-caller-identity") == [{}]
    assert "sbsandbox" not in out + err


def test_a_broker_that_has_written_no_profile_yet_is_refused_before_any_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: fall through and let ``get-caller-identity`` fail instead.

    This is the gap between the broker's two steps, and it is a common place to stop: ``login``
    opens a browser, prints success and writes nothing to ``~/.aws/config``. Falling through
    reaches ``_no_aws_session``, which tells them to run ``sb-aws-creds login`` -- the step they
    just completed -- and says nothing about the one they missed.
    """
    runner = a_laptop(tmp_path)

    code, out, err = invoke(
        LANE_VERBS[0], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch, aws_config=None
    )

    assert code == EXIT_UNUSABLE, out + err
    assert "no_broker_profile" in err
    assert runner.ran("aws") == []


def test_two_broker_profiles_stop_the_verb_and_name_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: take the first one.

    Both reach the platform, so neither is a safety problem -- but they are different accounts
    and one of them is production. Choosing wrong starts a machine on the wrong bill and reports
    success, and the config file says nothing about which account either label reaches.
    """
    runner = a_laptop(tmp_path)
    two = ONE_BROKER_PROFILE + (
        "\n[profile sbproduction]\n"
        f"credential_process = {AWS_BROKER} credential_process --profile sbproduction\n"
    )

    code, out, err = invoke(
        LANE_VERBS[0], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch, aws_config=two
    )

    assert code == EXIT_UNUSABLE, out + err
    assert "aws_profile_is_ambiguous" in err
    assert "sbproduction" in err
    assert runner.ran("aws") == []


def test_a_profile_belonging_to_other_work_is_never_the_one_this_reaches_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE PROFILE THIS MUST NOT TOUCH, END TO END.**
    Mutation: fall back to the only profile in the file when the broker manages none.

    People here have AWS profiles for other jobs, and one of them being the only entry in the
    file is not consent to spend it. The refusal is the right answer even though a profile is
    sitting right there, and ``AWS_PROFILE`` remains the way to say otherwise deliberately.
    """
    runner = a_laptop(tmp_path)
    elsewhere = "[default]\nregion = eu-west-2\n\n[profile dayjob]\nregion = eu-west-2\n"

    code, out, err = invoke(
        LANE_VERBS[0],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
        aws_config=elsewhere,
    )

    assert code == EXIT_UNUSABLE, out + err
    assert "no_broker_profile" in err
    assert "dayjob" not in out + err
    assert runner.ran("aws") == []
