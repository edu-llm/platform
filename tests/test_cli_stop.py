"""``edullm stop``: the verb that ends a lane machine, and the fence that keeps it to yours.

**THE GAP THIS CLOSED IS WORTH STATING ONCE.** ``edullm run`` and ``edullm shell`` started a
machine and nothing in the binary ended one. ``--hours 1`` is the smallest lifetime the flag
accepts, so the floor on a researcher's mistake -- the wrong shape, or the right shape they
immediately realised they did not want -- was about an hour of billing they could watch and
could not stop. An agent that hit this on 2026-08-06 correctly refused to reach past the binary
to the AWS CLI, which ``AGENTS.md`` forbids, and waited for the expiry janitor.

The cases below are in three groups: what the verb does, what it says, and what it will not
reach. The third is the one to read first.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from edullm_platform.cli.configuration import load_reviewed_configuration
from edullm_platform.cli.lane import SCRATCH_BUCKET, priced_as
from edullm_platform.cli.main import (
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_UNREACHABLE,
    EXIT_UNUSABLE,
    build_parser_and_verbs,
)
from tests.cli_support import (
    CONFIG_DIR,
    LANE_INSTANCE,
    LANE_MACHINE_TYPE,
    LANE_MACHINE_UPTIME,
    FakeRunner,
    a_machine_you_have,
    failed,
    git_answers,
    invoke,
    lane_answers,
)

#: The person the fixture's caller ARN names. Read here rather than spelled into every case,
#: because it is what the working prefix and the tag filter are both built from and a case
#: asserting on either is asserting about the same fact.
PERSON = "caiiris"


def flat(text: str) -> str:
    """One stream with its line breaks taken out, for asserting on a sentence.

    Every paragraph this verb prints goes through the CLI's wrapper at seventy-six columns, so
    a phrase worth pinning is as likely as not to have a newline in the middle of it -- "still
    running and still\\nbilling" is the one that found this. Asserting against the wrapped form
    would tie every case to a column count that is nobody's decision to keep, and asserting
    against a fragment short enough to never wrap would stop pinning the sentence.
    """
    return " ".join(text.split())


def a_laptop(tmp_path: Path, **overrides: object) -> FakeRunner:
    return FakeRunner({**git_answers(tmp_path), **lane_answers(**overrides)})


def stopping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    project: str = "mixlaw",
    runner: FakeRunner | None = None,
    **overrides: object,
) -> tuple[FakeRunner, int, str, str]:
    """One ``edullm stop`` against a laptop that already holds a session."""
    laptop = a_laptop(tmp_path, **overrides) if runner is None else runner
    code, out, err = invoke(
        ["stop", "--project", project], runner=laptop, cwd=tmp_path, monkeypatch=monkeypatch
    )
    return laptop, code, out, err


# ---------------------------------------------------------------------------------------
# what it will not reach
# ---------------------------------------------------------------------------------------


def test_the_machine_it_ends_is_the_one_the_caller_s_own_identity_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE WHOLE OF THE AUTHORIZATION, AND IT IS A PROPERTY OF THE ARGV RATHER THAN OF IAM.**
    Mutation: filter on the project alone, on the theory that a project names a machine.

    ``infra/iam/researcher-role.yaml`` does not fence this from underneath. Its
    ``AllowResearchWorkingSet`` statement is ``"*"`` on ``"*"`` and every deny in the policy
    names ``ec2:RunInstances``, ``ec2:CreateTags`` or a bucket, so a lane credential can
    terminate anything in the account. The refusal therefore has to be built in the verb, and
    the only way to build it that holds is for the id acted on to be one a describe filtered on
    the caller's own source identity returned.

    Two people share a project routinely -- that is why ``find_machine_argv`` carries both tags
    -- so a filter on the project alone would hand one researcher the other's machine, and the
    person typing the verb would have no way to know.
    """
    laptop, code, out, err = stopping(
        tmp_path, monkeypatch, stoppable=[a_machine_you_have()]
    )

    assert code == EXIT_OK, out + err
    described = " ".join(laptop.ran("aws", "ec2", "describe-instances")[0])

    assert f"Name=tag:edullm:lane,Values={PERSON}" in described
    assert laptop.ran("aws", "ec2", "terminate-instances")[0][-5] == LANE_INSTANCE


def test_no_flag_names_an_instance_so_none_can_be_smuggled_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE FLAG THIS VERB WILL NOT HAVE, PINNED SO THAT ADDING IT IS A RED TEST.**
    Mutation: take ``--instance`` for the case where somebody knows the id they want gone.

    It is the obvious next request and it is the one way past the fence above. An id typed on
    a command line is an id the person-tagged describe never returned, and since IAM permits
    this credential to terminate anything, the flag would be the whole of the difference
    between a verb that can end your machine and a verb that can end anybody's.

    ``edullm stop`` finding the wrong machine is not the hazard being guarded here; the hazard
    is a verb that can be *told* which machine, by somebody who read an id off a colleague's
    terminal.
    """
    laptop = a_laptop(tmp_path, stoppable=[a_machine_you_have()])

    code, _out, err = invoke(
        ["stop", "--project", "mixlaw", "--instance", "i-0000000000000beef"],
        runner=laptop,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_UNUSABLE
    assert "--instance" in err
    assert laptop.ran("aws", "ec2", "terminate-instances") == [], (
        "nothing may be ended by a command line that named an id"
    )
    assert "--instance" not in build_parser_and_verbs()[1]["stop"].format_help()


def test_a_machine_of_another_project_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: end the first machine the describe returned.

    A person has one machine per project, so the answer to this describe carries every project
    they are working on. Taking the first would end whichever one EC2 happened to list first,
    which is a coin toss between the machine they asked about and one they are still using.
    """
    laptop, code, out, err = stopping(
        tmp_path,
        monkeypatch,
        project="mixlaw",
        stoppable=[
            a_machine_you_have(machine="i-0000000000000f00d", project="tokenizer-sweep"),
            a_machine_you_have(machine=LANE_INSTANCE, project="mixlaw"),
        ],
    )

    assert code == EXIT_OK, out + err
    ended = [argv[argv.index("--instance-ids") + 1] for argv in laptop.ran("aws", "ec2", "terminate-instances")]

    assert ended == [LANE_INSTANCE]


def test_stopping_never_starts_a_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE PROPERTY THAT KEEPS THIS VERB OFF ``_lane_session``.**
    Mutation: reuse ``_lane_session`` to resolve the machine, which is what it is for.

    That function hands back a machine and starts one where it finds none. A verb whose whole
    purpose is that nothing is billing must not be able to buy an instance, and a mistyped
    ``--project`` is exactly the input that would make it: no machine for ``mixlow``, so start
    one. The two share the identity call and the lane entry and nothing after them.
    """
    laptop, code, _out, _err = stopping(tmp_path, monkeypatch, project="mixlow", stoppable=[])

    assert code == EXIT_OK
    assert laptop.ran("aws", "ec2", "run-instances") == []
    assert laptop.ran("aws", "ssm", "get-parameter") == [], "not even an AMI was looked up"


def test_it_needs_no_session_manager_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**A PROPERTY RATHER THAN AN OVERSIGHT, AND THE LAPTOP THAT NEEDS IT MOST IS THIS ONE.**
    Mutation: check for the plugin the way the other two lane verbs do.

    ``run`` and ``shell`` refuse ``session_plugin_missing`` because both open a Systems Manager
    session and neither can do anything without it. This verb makes one EC2 call and opens
    nothing. Somebody whose plugin has broken, or who is on a machine they can no longer
    connect to, is precisely the person with an instance they need to end, and a refusal here
    would leave them where the verb was written to stop them being: watching it bill.
    """
    laptop = a_laptop(tmp_path, stoppable=[a_machine_you_have()])

    code, out, err = invoke(
        ["stop", "--project", "mixlaw"],
        runner=laptop,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
        plugin=False,
    )

    assert code == EXIT_OK, out + err
    assert laptop.ran("aws", "ec2", "terminate-instances")


# ---------------------------------------------------------------------------------------
# what it does
# ---------------------------------------------------------------------------------------


def test_it_terminates_rather_than_stopping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE DECISION THIS VERB IS.**
    Mutation: call ``stop-instances``, which is what the expiry janitor calls.

    The janitor is right to stop. It acts on a machine nobody asked it to touch, on a clock
    rather than on a person's word, so it takes the expensive half off the bill and leaves
    every recovery open. This verb is the person saying they are finished, and stopping would
    leave them worse off three ways at once: ``find_machine_argv`` looks for ``pending`` and
    ``running``, so the next ``edullm run`` would not find the machine and would start a second
    one; ``expiry_janitor`` answers ``already_stopped`` for anything not running, so nothing
    would ever reclaim it; and its two hundred gibibytes would go on billing while it sat
    there. Ordinary use would leave one of those behind every time.
    """
    laptop, code, out, err = stopping(tmp_path, monkeypatch, stoppable=[a_machine_you_have()])

    assert code == EXIT_OK, out + err
    assert laptop.ran("aws", "ec2", "terminate-instances")
    assert laptop.ran("aws", "ec2", "stop-instances") == []


def test_a_machine_the_janitor_already_stopped_can_still_be_ended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE ONLY ROUTE OUT OF THE STATE THE SWEEP LEAVES BEHIND.**
    Mutation: look for ``pending`` and ``running``, as every other lane verb does.

    A machine the janitor has stopped is invisible to the whole binary: ``run`` and ``shell``
    do not find it and start another, and the sweep decides ``already_stopped`` and leaves it
    for ever, with its volume billing the entire time. This verb is what removes it, which
    means it has to be able to see a state no other verb has any use for.
    """
    laptop, code, out, err = stopping(
        tmp_path, monkeypatch, stoppable=[a_machine_you_have(state="stopped")]
    )

    assert code == EXIT_OK, out + err
    described = " ".join(laptop.ran("aws", "ec2", "describe-instances")[0])

    assert "Values=pending,running,stopping,stopped" in described
    assert laptop.ran("aws", "ec2", "terminate-instances")
    assert "stopped until this ran" in flat(out)


def test_a_machine_already_gone_is_not_reported_as_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THIS VERB'S HALF OF NOT FIGHTING THE JANITOR.**
    Mutation: report AWS's refusal verbatim and exit 3.

    The sweep runs every five minutes and the window between this verb's describe and its
    terminate is open to it. A machine EC2 no longer has is a machine that is not billing,
    which is what was asked for, and reporting it as unreachable would send somebody looking
    for a charge that had already stopped.
    """
    laptop = a_laptop(tmp_path, stoppable=[a_machine_you_have()])
    laptop._answers[("aws", "ec2", "terminate-instances")] = failed(
        "An error occurred (InvalidInstanceID.NotFound) when calling the TerminateInstances "
        f"operation: The instance ID '{LANE_INSTANCE}' does not exist",
        returncode=254,
    )

    code, out, err = invoke(
        ["stop", "--project", "mixlaw"], runner=laptop, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_OK, out + err
    assert "already gone" in flat(out)
    assert "nothing of yours is billing" in flat(out).lower()


def test_a_terminate_that_genuinely_fails_names_the_machine_that_is_still_billing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: report the failure without the instance id in it.

    Somebody typed this verb to stop paying for something. A refusal that does not name what
    is still running leaves them with the charge and nothing to look it up by, which is the
    rule ``_machine_never_answered`` already follows for the one other message in the lane
    that leaves a machine behind. The expiry is what bounds it, so it is said.
    """
    laptop = a_laptop(tmp_path, stoppable=[a_machine_you_have()])
    laptop._answers[("aws", "ec2", "terminate-instances")] = failed(
        "An error occurred (UnauthorizedOperation) when calling the TerminateInstances "
        "operation: You are not authorized to perform this operation",
        returncode=254,
    )

    code, _out, err = invoke(
        ["stop", "--project", "mixlaw"], runner=laptop, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_UNREACHABLE
    assert LANE_INSTANCE in err
    assert "still billing" in flat(err)
    assert "expiry tag still holds" in flat(err)


# ---------------------------------------------------------------------------------------
# what it says
# ---------------------------------------------------------------------------------------


def test_it_says_what_the_machine_ran_up_at_the_catalog_s_own_rate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE FIGURE IS READ OUT OF REVIEWED CONFIGURATION AT THE MOMENT OF ASKING.**
    Mutation: print the duration and leave the money to whoever reads the bill.

    A researcher stopping a machine is stopping a charge, and a verb that would not say how
    big it had got is asking them to go to Cost Explorer to find out what the tool already
    knew. ``AGENTS.md`` forbids quoting a price from memory or from a document; this reads the
    rate out of ``config/workload-catalog.yaml`` and names the file, which is that discipline
    pointed at a figure rather than at a refusal.

    The expected number is computed here from the same catalog rather than written down, so
    this case follows a repricing instead of going red on one.
    """
    configuration = load_reviewed_configuration(CONFIG_DIR)
    profile = priced_as(configuration, LANE_MACHINE_TYPE)
    assert profile is not None, f"{LANE_MACHINE_TYPE} is no longer priced, so pick another"
    hours = Decimal(int(LANE_MACHINE_UPTIME.total_seconds())) / Decimal(3600)
    expected = (profile.hourly_rate_usd * hours).quantize(Decimal("0.01"))

    _laptop, code, out, err = stopping(tmp_path, monkeypatch, stoppable=[a_machine_you_have()])

    assert code == EXIT_OK, out + err
    assert "2 hours 15 minutes" in flat(out)
    assert f"${expected}" in flat(out)
    assert profile.name in flat(out)
    assert "config/workload-catalog.yaml" in flat(out)
    assert "not its disk or its traffic" in flat(out)


def test_it_says_where_the_files_are_because_the_disk_has_just_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE FACT A PERSON CANNOT RECOVER IF IT IS NOT SAID HERE.**
    Mutation: say the machine is terminated and stop there.

    The scratch prefix outliving the machine is the whole point of the layout -- ``run`` syncs
    it down before every command and back up after -- and somebody who has just watched an
    instance disappear has no way to know that unless it is said at the moment it matters. A
    verb that ended a machine and went quiet would teach people to keep machines alive as
    storage, which is the expensive habit this platform is arranged to avoid.
    """
    _laptop, code, out, err = stopping(tmp_path, monkeypatch, stoppable=[a_machine_you_have()])

    assert code == EXIT_OK, out + err
    assert f"s3://{SCRATCH_BUCKET}/{PERSON}/mixlaw/" in flat(out)
    assert "disk went with the machine" in flat(out)


def test_a_spot_machine_is_said_to_have_cost_less_than_the_figure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: quote the on-demand rate for a Spot machine without saying which it is.

    ``--spot`` buys the persistent stop form and it bills well under the catalog's number, so
    the figure is a ceiling rather than a reading. Printed bare it is a number a researcher
    could take to somebody as what their work cost, and be wrong by most of it.
    """
    _laptop, code, out, err = stopping(
        tmp_path, monkeypatch, stoppable=[a_machine_you_have(spot=True)]
    )

    assert code == EXIT_OK, out + err
    assert "Spot" in flat(out)
    assert "ceiling" in flat(out)


def test_a_machine_with_no_launch_time_is_still_ended_and_the_cost_is_not_invented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: fall back to the current time, which makes the duration zero.

    A duration of nothing prints as ``less than a minute`` and ``$0.00``, which is a measured
    figure for a machine that may have been up for a day. Saying the clock could not be read
    is the honest answer and it costs the verb nothing: ending the machine is what was asked
    for and it still happens.
    """
    _laptop, code, out, err = stopping(
        tmp_path, monkeypatch, stoppable=[a_machine_you_have(up_for=None)]
    )

    assert code == EXIT_OK, out + err
    assert "did not say when it started" in flat(out)
    assert "$" not in flat(out).split("Your files")[0], "no figure may be quoted from no clock"


def test_a_project_with_no_machine_names_the_projects_that_have_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**WHY THE FINDER DOES NOT FILTER ON THE PROJECT.**
    Mutation: filter the describe on the project and answer a miss with "nothing found".

    A person types this verb because they believe something is billing. Answering a mistyped
    project with "nothing found" tells them the opposite of the truth in the exact words that
    sound like reassurance, and they stop looking while the machine runs to its expiry. One
    unfiltered call answers both questions, so naming what is actually running costs nothing.
    """
    _laptop, code, out, err = stopping(
        tmp_path,
        monkeypatch,
        project="mixlow",
        stoppable=[a_machine_you_have(project="mixlaw")],
    )

    assert code == EXIT_OK, out + err
    assert "no machine for 'mixlow'" in flat(out)
    assert "mixlaw" in flat(out)


def test_nothing_at_all_is_an_ordinary_answer_rather_than_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: refuse, on the grounds that the verb did not do anything.

    The state this verb exists to produce is already the state. A cleanup verb that exited
    non-zero on its second call is one nobody can put in a script, and this is the verb an
    anxious person runs twice.
    """
    _laptop, code, out, err = stopping(tmp_path, monkeypatch, stoppable=[])

    assert code == EXIT_OK, out + err
    assert "no machine in the lane" in flat(out)
    assert "nothing of yours is billing" in flat(out)


def test_a_session_already_inside_the_lane_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: fall back to the project alone when the person cannot be read.

    ``sts:GetCallerIdentity`` does not return the source identity, so a session this lane
    created carries no person. Without one the tag filter would be unbounded and the verb
    would be choosing a machine out of the whole account's, which is the one outcome the
    fence exists to prevent. It shares ``cannot_tell_who_you_are`` with the other lane verbs
    rather than restating it, because it is the same fact about the same session.
    """
    answers = lane_answers(stoppable=[a_machine_you_have()])
    answers[("aws", "sts", "get-caller-identity")] = _inside_the_lane()
    laptop = FakeRunner({**git_answers(tmp_path), **answers})

    code, _out, err = invoke(
        ["stop", "--project", "mixlaw"], runner=laptop, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_REFUSED
    assert "cannot_tell_who_you_are" in err
    assert laptop.ran("aws", "ec2", "terminate-instances") == []


def _inside_the_lane():  # type: ignore[no-untyped-def]
    """A caller identity for a session the lane itself minted, which names no person."""
    import json

    from tests.cli_support import FAKE_ACCOUNT, ok

    return ok(
        json.dumps(
            {
                "Account": FAKE_ACCOUNT,
                "Arn": (
                    f"arn:aws:sts::{FAKE_ACCOUNT}:assumed-role/edullm-researcher/lane-mixlaw"
                ),
            }
        )
    )


def test_a_long_lived_machine_reads_in_days_rather_than_in_minutes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print every unit, so a three-day machine reads ``3 days 4 hours 12 minutes``.

    The money beside it is approximate by construction -- it excludes the volume and the
    traffic and it is a ceiling on Spot -- so a duration precise to the minute across three
    days claims an accuracy the figure it decorates does not have.
    """
    _laptop, code, out, err = stopping(
        tmp_path,
        monkeypatch,
        stoppable=[a_machine_you_have(up_for=timedelta(days=3, hours=4, minutes=12))],
    )

    assert code == EXIT_OK, out + err
    assert "3 days 4 hours" in flat(out)
    assert "12 minutes" not in flat(out)
