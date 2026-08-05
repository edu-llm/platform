"""What the janitor decides, and the four ways deciding it wrong would matter.

THE ONE PROPERTY EVERYTHING HERE PROTECTS: nothing is stopped that was not warned first.
docs-frank/reference/system-overview.md, "How money gets spent, and what stops a mistake",
draws the janitor as warning before it stops anything. A janitor that stops a machine somebody
is sitting at, with no warning, teaches thirty-five people that the platform destroys work --
which is a more expensive outcome than the machine.

The second property is the filter. This is a shared account: MCAT, LSAT, a personal site and
nineteen administrators the roster has never heard of all run here, and a sweeper acting on
anything but our own two tags would stop somebody else's machine.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from edullm_platform.expiry_janitor import (
    ExpiryAction,
    TaggedInstance,
    decide_expiry_actions,
    instance_from_tags,
)
from edullm_platform.researcher_lane import LaneSettings

NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=UTC)
SETTINGS = LaneSettings(
    schema_version=1, default_lifetime_hours=8, warning_lead_minutes=30, sweep_minutes=5
)


def machine(
    *,
    instance_id: str = "i-0000000000000aaaa",
    state: str = "running",
    project: str | None = "mixlaw",
    expires_in_minutes: float | None = 60,
    warned_minutes_ago: float | None = None,
) -> TaggedInstance:
    return TaggedInstance(
        instance_id=instance_id,
        state=state,
        project=project,
        expires_at=(
            None if expires_in_minutes is None else NOW + timedelta(minutes=expires_in_minutes)
        ),
        warned_at=(
            None if warned_minutes_ago is None else NOW - timedelta(minutes=warned_minutes_ago)
        ),
    )


def only(instances: list[TaggedInstance]) -> tuple[ExpiryAction, str]:
    decisions = decide_expiry_actions(instances, now=NOW, settings=SETTINGS)
    assert len(decisions) == 1
    return decisions[0].action, decisions[0].reason


def test_a_machine_with_no_project_tag_is_not_ours_to_touch() -> None:
    """Mutation: act on ExpiresAt alone.

    The account is shared. A machine carrying an expiry and no project tag was not launched
    through the lane, and stopping it is stopping somebody else's work in an account where we
    cannot even see who they are.
    """
    assert only([machine(project=None)]) == (
        ExpiryAction.SKIP,
        "not_launched_through_the_lane",
    )


def test_a_machine_with_no_expiry_tag_is_not_ours_to_touch_either() -> None:
    """Mutation: treat a missing ExpiresAt as expired.

    The lane refuses a launch that carries no expiry, so a machine with a project tag and no
    expiry was tagged by something else -- and the failure of guessing here is terminating on
    a tag collision.
    """
    assert only([machine(expires_in_minutes=None)]) == (
        ExpiryAction.SKIP,
        "not_launched_through_the_lane",
    )


def test_a_machine_that_is_already_stopped_is_left_alone() -> None:
    """Mutation: stop anything expired regardless of state.

    StopInstances on a stopped instance is a no-op that still costs a call and still writes a
    CloudTrail event, so a sweep would report work it did not do every five minutes for as long
    as the instance existed.
    """
    assert only([machine(state="stopped", expires_in_minutes=-120)]) == (
        ExpiryAction.LEAVE,
        "already_stopped",
    )


def test_a_machine_well_inside_its_lifetime_is_left_alone() -> None:
    """Mutation: warn on every sweep.

    A warning on every sweep is a warning nobody reads, and it would rewrite the warned-at tag
    continuously -- which would make the stop condition below unreachable.
    """
    assert only([machine(expires_in_minutes=600)]) == (ExpiryAction.LEAVE, "inside_its_lifetime")


def test_a_machine_inside_the_warning_lead_is_warned_once() -> None:
    """Mutation: compare against the sweep interval instead of the warning lead.

    The lead comes from config/reports/researcher-lane.yaml. Twenty-nine minutes out is inside
    a thirty-minute lead and is the case that decides whether the boundary is read from the
    file or from a number somebody typed.
    """
    assert only([machine(expires_in_minutes=29)]) == (ExpiryAction.WARN, "expiry_is_near")


def test_a_machine_already_warned_is_not_warned_again() -> None:
    """Mutation: drop the warned_at check.

    Re-warning rewrites the tag, and the stop condition requires a warning that already
    happened -- so a janitor that warns every sweep never stops anything, which is the failure
    that looks like the janitor working.
    """
    assert only([machine(expires_in_minutes=10, warned_minutes_ago=15)]) == (
        ExpiryAction.LEAVE,
        "already_warned",
    )


def test_an_expired_machine_that_was_warned_is_stopped() -> None:
    """THE ONE DECISION THIS WHOLE COMPONENT EXISTS FOR.
    Mutation: require the expiry to be some margin in the past.

    Expiry is a promise the researcher made, so stopping at it needs no further judgement --
    system-overview.md says exactly that. A margin here would be the janitor second-guessing
    the promise, and it would make the expiry mean something other than what it says.
    """
    assert only([machine(expires_in_minutes=-1, warned_minutes_ago=40)]) == (
        ExpiryAction.STOP,
        "expired_after_a_warning",
    )


def test_an_expired_machine_that_was_never_warned_is_warned_rather_than_stopped() -> None:
    """THE ASYMMETRY THAT MAKES "WARNS BEFORE IT STOPS ANYTHING" TRUE UNCONDITIONALLY.
    Mutation: stop it, on the grounds that it is already expired.

    A machine can reach expiry unwarned -- the janitor was down, or the lifetime was shorter
    than the warning lead. Stopping it then is exactly the unannounced destruction the warning
    exists to prevent, and the fact that it is technically overdue does not change what it
    feels like to the person at the keyboard. It gets its warning and is stopped on the next
    sweep.
    """
    assert only([machine(expires_in_minutes=-90)]) == (
        ExpiryAction.WARN,
        "expired_without_a_warning",
    )


def test_every_instance_gets_exactly_one_decision_and_they_keep_their_order() -> None:
    """Mutation: return only the actionable decisions.

    A sweep that reports only what it acted on cannot distinguish "nothing needed doing" from
    "the filter matched nothing", and those are the two states an operator most needs told
    apart -- the second is the janitor being silently broken.
    """
    decisions = decide_expiry_actions(
        [
            machine(instance_id="i-0000000000000aaaa", expires_in_minutes=600),
            machine(instance_id="i-0000000000000bbbb", project=None),
            machine(
                instance_id="i-0000000000000cccc", expires_in_minutes=-1, warned_minutes_ago=40
            ),
        ],
        now=NOW,
        settings=SETTINGS,
    )

    assert [one.instance_id for one in decisions] == [
        "i-0000000000000aaaa",
        "i-0000000000000bbbb",
        "i-0000000000000cccc",
    ]
    assert [one.action for one in decisions] == [
        ExpiryAction.LEAVE,
        ExpiryAction.SKIP,
        ExpiryAction.STOP,
    ]


def test_an_unparseable_expiry_tag_is_skipped_rather_than_guessed() -> None:
    """Mutation: fall back to "now" when the timestamp does not parse.

    A tag somebody edited by hand into "tomorrow" is not a time. Treating it as now stops the
    machine; treating it as never leaves it for ever. Skipping is the honest third answer, and
    the reason code is what makes the machine findable in the sweep's output.
    """
    instance = instance_from_tags(
        "i-0000000000000dddd",
        "running",
        {"Project": "mixlaw", "ExpiresAt": "tomorrow"},
    )

    assert only([instance]) == (ExpiryAction.SKIP, "not_launched_through_the_lane")


def test_an_expiry_tag_with_no_zone_is_read_as_utc_rather_than_as_the_lambda_s_clock() -> None:
    """Mutation: return the naive datetime datetime.fromisoformat gives back.

    A naive datetime compared against an aware `now` raises TypeError, which in the handler is
    an unhandled exception on a scheduled invocation: the sweep dies, every machine that sweep
    would have stopped keeps running, and the only trace is a CloudWatch metric. The tag the
    lane writes always carries a Z, so this is about a tag a person set by hand -- which is
    exactly the case that must not take the sweep down with it.
    """
    instance = instance_from_tags(
        "i-0000000000000ffff",
        "running",
        {"Project": "mixlaw", "ExpiresAt": "2026-08-04T11:00:00"},
    )

    assert instance.expires_at is not None
    assert instance.expires_at.tzinfo is not None
    assert only([instance]) == (ExpiryAction.WARN, "expired_without_a_warning")


def test_tags_are_read_by_the_exact_keys_the_policy_conditions_use() -> None:
    """Mutation: read a lowercase project tag.

    aws:RequestTag is case-sensitive, so the policy's condition and the janitor's read have to
    agree exactly. A lowercase read finds the session tag key rather than the launch tag key
    and matches nothing the lane produced.
    """
    lowercased = instance_from_tags(
        "i-0000000000000eeee",
        "running",
        {"project": "mixlaw", "expiresat": "2026-08-04T18:00:00Z"},
    )

    assert lowercased.project is None
    assert lowercased.expires_at is None
