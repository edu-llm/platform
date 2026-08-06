"""The expiry a machine carries and the expiry a researcher is told, held to one instant.

THE DEFECT THIS FILE IS THE FENCE FOR. ``edullm run`` computed a fresh expiry on every
invocation and printed it, while the ``ExpiresAt`` tag kept whatever the launch had written.
A second run against a machine that already existed was therefore shown a time later than the
janitor would honour, and the machine was stopped while the researcher believed they had time
left. The janitor was doing what the tag said; the tag was stale.

WHY EVERY CASE HERE READS BOTH SIDES RATHER THAN EITHER. A test asserting the printed line
carries a timestamp passed throughout the defect, and so would one asserting the tag carries
one. What was never asserted is that the two are the same instant, which is the only property
that was ever false. So each case below pulls the tag out of a launch argv or out of a
describe-instances answer, pulls every timestamp out of the sentence, and compares them.

WHY THE TAG IS NOT REWRITTEN ON REUSE. The other repair was to write the fresh time onto the
machine so the printed line became true, and ``infra/iam/researcher-role.yaml`` is where that
stops being an API call and becomes a policy change:
``DenyStrippingGovernanceTagsAfterLaunch`` denies ``ec2:CreateTags`` on ``ExpiresAt`` for
everything but the launch itself, and the template records beside it that IAM cannot compare
a tag against the clock. A grant to rewrite the tag is therefore a grant to write any value
into it, so the expiry stops being a bound in the same edit that makes the line true.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from edullm_platform.cli.configuration import load_reviewed_configuration
from edullm_platform.cli.lane import (
    LaneExpiry,
    LaneRequest,
    expires_at,
    expiry_for_a_new_machine,
    instance_type_for,
    load_working_tier_settings,
    machine_already_running,
    run_instances_argv,
)
from edullm_platform.researcher_lane import EXPIRES_AT_TAG_KEY

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
SETTINGS = load_working_tier_settings(CONFIG_DIR)
REQUEST = LaneRequest(project="mixlaw", person="caiiris", compute_profile="gpu-1xt4")

MACHINE = "i-00000000000000abc"

#: An instant far enough in the past that no fresh computation could produce it, which is what
#: makes the named mutation go red rather than pass by coincidence on a slow test run.
ALREADY_TAGGED = "2026-08-06T06:00:00Z"

#: Every absolute instant a sentence quotes, in the one spelling ``expires_at`` writes. The
#: assertions below compare the whole list rather than testing membership, because a line that
#: printed the tag *and* a fresh computation would satisfy "the tag is in there" and is exactly
#: the half-repair worth failing.
TIMESTAMPS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

#: The tag as ``run_instances_argv`` renders it into ``--tag-specifications``.
TAGGED = re.compile(rf"\{{Key={EXPIRES_AT_TAG_KEY},Value=([^}}]+)\}}")


def described(*, expires: str | None) -> str:
    """One machine as ``find_machine_argv``'s query returns it."""
    tags = [{"Key": "Project", "Value": "mixlaw"}]
    if expires is not None:
        tags.append({"Key": EXPIRES_AT_TAG_KEY, "Value": expires})
    return json.dumps([{"machine": MACHINE, "tags": tags}])


def launched_with(value: str) -> str:
    """The ``ExpiresAt`` value the account would be handed for this expiry."""
    argv = run_instances_argv(
        request=REQUEST,
        instance_type=instance_type_for(load_reviewed_configuration(CONFIG_DIR), "gpu-1xt4") or "",
        image_id="ami-000000000000000aa",
        subnet_id="subnet-000000000000000bb",
        security_group_id="sg-000000000000000cc",
        expires_at_value=value,
        settings=SETTINGS,
        spot=False,
    )
    found = TAGGED.search(" ".join(argv))
    assert found is not None, "the launch carries no ExpiresAt tag at all"
    return found.group(1)


def test_a_reused_machine_is_told_the_expiry_it_carries_and_not_a_fresh_one() -> None:
    """**THE DEFECT, AS ONE EQUALITY.**
    Mutation: compute a fresh expiry for a machine that already exists, which is what shipped.

    The machine below was tagged at launch and the janitor will stop it then. A verb that
    computed against this invocation's clock would print a later instant, the researcher would
    plan against it, and the stop would arrive early with nothing having warned them. Compared
    as the whole list of instants in the line, so printing both times fails too.
    """
    found = machine_already_running(described(expires=ALREADY_TAGGED))

    assert found is not None
    machine, expiry = found
    assert expiry.value == ALREADY_TAGGED
    assert TIMESTAMPS.findall(expiry.said(machine)) == [ALREADY_TAGGED]


def test_the_instant_the_launch_is_tagged_with_is_the_instant_the_line_quotes() -> None:
    """Mutation: tag one computation and print another.

    Two calls to ``expires_at`` a few milliseconds apart round to the same second nearly
    always, so a verb that computed twice looked correct in every test and in every hand run.
    One string reaches both the argv and the sentence, and this is what says so.
    """
    expiry = expiry_for_a_new_machine(datetime(2026, 8, 5, 22, 0, 0, tzinfo=UTC), 8)

    assert TIMESTAMPS.findall(expiry.said(MACHINE)) == [launched_with(expiry.value)]


def test_the_expiry_survives_the_round_trip_from_launch_to_the_machine_and_back() -> None:
    """**THE PROPERTY THE WHOLE REPAIR IS, END TO END.**
    Mutation: read the tag but recompute the sentence, or vice versa.

    A machine is launched with an expiry, the account hands it back on the next invocation as
    a tag, and what the second invocation prints has to be the instant the first one wrote.
    Every step is real here except the account: the tag goes in through the launch argv and
    comes back out through the finder's own parse.
    """
    started = expiry_for_a_new_machine(datetime(2026, 8, 5, 22, 0, 0, tzinfo=UTC), 8)
    tagged = launched_with(started.value)

    found = machine_already_running(described(expires=tagged))

    assert found is not None
    machine, reused = found
    assert TIMESTAMPS.findall(reused.said(machine)) == TIMESTAMPS.findall(started.said(machine))


def test_a_machine_found_and_a_machine_started_do_not_print_the_same_sentence() -> None:
    """Mutation: give both causes the one line, which is what the verb did.

    Every one of the five defects that made this lane unusable produced the same uninformative
    sentence, and that is the rule this carries forward. The honest reading of a reused machine
    costs a surprise -- somebody who expected a second run to buy them more time -- and the line
    is the only place that surprise can be answered, so the two cases have to read differently.
    """
    started = LaneExpiry(value=ALREADY_TAGGED, found_running=False)
    found = LaneExpiry(value=ALREADY_TAGGED, found_running=True)

    assert started.said(MACHINE) != found.said(MACHINE)
    assert "did not move its expiry" in found.said(MACHINE)


def test_a_machine_carrying_no_expiry_says_so_rather_than_printing_the_word_and_nothing() -> None:
    """Mutation: fall through to the ordinary line for a machine with no tag.

    The researcher role refuses a launch with no ``ExpiresAt``, so a lane machine reaching this
    was tagged by hand or launched around the role -- and it is precisely the machine nothing
    will ever reclaim. The shared sentence would announce it as ``expires`` followed by nothing,
    which reads as a broken tool rather than as a machine that is going to bill until somebody
    notices.
    """
    found = machine_already_running(described(expires=None))

    assert found is not None
    machine, expiry = found
    assert expiry.value == ""
    assert TIMESTAMPS.findall(expiry.said(machine)) == []
    assert EXPIRES_AT_TAG_KEY in expiry.said(machine)


def test_the_tag_is_matched_on_the_key_the_role_and_the_janitor_spell() -> None:
    """Mutation: match the key case-insensitively, or spell it in the JMESPath query.

    ``aws:RequestTag`` is case-sensitive and the role's condition, the janitor's reader and the
    launch all take their spelling from ``researcher_lane.EXPIRES_AT_TAG_KEY``. A near miss has
    to read as a machine with no expiry -- which is a sentence somebody acts on -- rather than
    quietly resolving to a value the janitor is not looking at.
    """
    near_miss = json.dumps(
        [{"machine": MACHINE, "tags": [{"Key": EXPIRES_AT_TAG_KEY.lower(), "Value": "whenever"}]}]
    )

    found = machine_already_running(near_miss)

    assert found is not None
    assert found[1].value == ""


def test_a_person_with_no_machine_is_told_about_none() -> None:
    """Mutation: return a machine for an empty answer, or raise on one.

    The ordinary first invocation. ``--output json`` over a filter that matched nothing prints
    an empty list, and an installation whose AWS CLI printed nothing at all is the other shape
    this has to survive: a JSONDecodeError here is a traceback in front of a researcher whose
    real situation is that they have no machine yet.
    """
    assert machine_already_running("[]") is None
    assert machine_already_running("") is None
    assert machine_already_running("  \n") is None


def test_the_new_machine_line_is_the_absolute_instant_and_not_a_duration() -> None:
    """Mutation: print the lifetime the researcher asked for instead of the instant it becomes.

    ``expires_at``'s own reasoning: a duration has to be joined to LaunchTime, and a stopped and
    restarted instance keeps its original one. The line a researcher plans against has to be the
    same absolute instant the janitor compares against, in the same spelling.
    """
    now = datetime(2026, 8, 5, 22, 0, 0, tzinfo=UTC)
    expiry = expiry_for_a_new_machine(now, 8)

    assert expiry.value == expires_at(now, 8)
    assert TIMESTAMPS.findall(expiry.said(MACHINE)) == [expires_at(now, 8)]


def test_the_line_names_the_machine_it_is_about() -> None:
    """Mutation: print the expiry with no instance id.

    Somebody running two projects has two machines and two expiries, and a line naming neither
    is a line they cannot act on. It is also the id they need to look the charge up by.
    """
    for expiry in (
        expiry_for_a_new_machine(datetime(2026, 8, 5, 22, 0, 0, tzinfo=UTC), 8),
        LaneExpiry(value=ALREADY_TAGGED, found_running=True),
        LaneExpiry(value="", found_running=True),
    ):
        assert MACHINE in expiry.said(MACHINE)
