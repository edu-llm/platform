"""What the janitor does about a machine, decided from tags and a clock and nothing else.

WHY THIS COMPONENT EXISTS AND WHY IT SHIPS WITH THE ROLE. edullm-researcher refuses a launch
that carries no ExpiresAt tag. Without something that acts on that tag, the requirement is a
comment: it produces a tag on every machine, tells thirty-five people their machines expire,
and stops nothing -- which is worse than having no expiry at all, because people plan around a
control that does not exist. docs-frank/reference/system-overview.md, "How money gets spent,
and what stops a mistake", is the specification.

TWO PROPERTIES, AND EVERY RULE BELOW IS ONE OR THE OTHER.

Nothing is stopped that was not warned. The warning is a tag the janitor writes, so it survives
the function being redeployed and is visible to anybody looking at the machine. An expired
machine that was never warned is warned rather than stopped, which looks like leniency and is
not: a machine reaches that state because the janitor was down or because the lifetime was
shorter than the warning lead, and neither is the researcher's doing.

Nothing outside the lane is touched. Both readers of this account read a shared account -- the
overview says so -- so the filter is the two tags the lane writes and a machine missing either
is skipped. This is what keeps a sweeper from stopping MCAT's instance.

Delivery of the warning is not here. A direct message is the chat integration's, which is the
code host's service and which the instruments slice carries as a task of its own. What this
returns is the structured decision that would be delivered.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from edullm_platform.contracts.base import ContractModel
from edullm_platform.researcher_lane import (
    EXPIRES_AT_TAG_KEY,
    PROJECT_TAG_KEY,
    WARNING_TAG_KEY,
    LaneSettings,
)

__all__ = [
    "ExpiryAction",
    "ExpiryDecision",
    "TaggedInstance",
    "decide_expiry_actions",
    "instance_from_tags",
]


class ExpiryAction(StrEnum):
    #: Ours, and nothing to do this sweep.
    LEAVE = "leave"
    #: Ours, near its expiry, and not yet warned.
    WARN = "warn"
    #: Ours, past its expiry, and warned.
    STOP = "stop"
    #: Not ours. Reported rather than dropped, so a sweep that matched nothing is
    #: distinguishable from a sweep that ran against nothing.
    SKIP = "skip"


class TaggedInstance(ContractModel):
    instance_id: str
    state: str
    project: str | None
    expires_at: datetime | None
    warned_at: datetime | None


class ExpiryDecision(ContractModel):
    instance_id: str
    action: ExpiryAction
    #: A code rather than a sentence, so a sweep's output is groupable and a test can assert
    #: on it. The sentence belongs to whoever renders this.
    reason: str


def _parse(value: str | None) -> datetime | None:
    """An ISO-8601 tag value, or None where it is not one.

    None rather than an exception, and rather than a guess. A tag somebody hand-edited into
    "tomorrow" is not a time; reading it as now stops the machine and reading it as never
    leaves it for ever, so the honest answer is that this machine cannot be judged, which
    ``decide_expiry_actions`` turns into a SKIP that names the instance.

    A value carrying no zone is read as UTC rather than returned naive. ``datetime`` refuses to
    compare a naive value with an aware one, so a naive return would raise ``TypeError`` inside
    a scheduled sweep -- the whole sweep dies, every machine it would have stopped keeps
    running, and the only trace is a metric. The lane always writes a Z; this is about the tag
    a person set by hand, which is exactly the input that must not take the sweep down.
    """
    if value is None:
        return None
    try:
        # No `.replace("Z", "+00:00")`. fromisoformat has read a trailing Z since 3.11 and this
        # project needs 3.12, so the substitution is dead weight that also rewrites a Z
        # anywhere else in the string.
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def instance_from_tags(instance_id: str, state: str, tags: Mapping[str, str]) -> TaggedInstance:
    """One instance as the janitor sees it, out of the tag map describe-instances returns.

    The keys are read exactly as the policy's conditions spell them. ``aws:RequestTag`` is
    case-sensitive, so a lowercase read would find the session tag key rather than the launch
    tag key and match nothing the lane produced.
    """
    return TaggedInstance(
        instance_id=instance_id,
        state=state,
        project=tags.get(PROJECT_TAG_KEY),
        expires_at=_parse(tags.get(EXPIRES_AT_TAG_KEY)),
        warned_at=_parse(tags.get(WARNING_TAG_KEY)),
    )


def _decide(instance: TaggedInstance, *, now: datetime, settings: LaneSettings) -> ExpiryDecision:
    def decision(action: ExpiryAction, reason: str) -> ExpiryDecision:
        return ExpiryDecision(instance_id=instance.instance_id, action=action, reason=reason)

    if instance.project is None or instance.expires_at is None:
        return decision(ExpiryAction.SKIP, "not_launched_through_the_lane")
    if instance.state != "running":
        return decision(ExpiryAction.LEAVE, "already_stopped")
    if now >= instance.expires_at:
        if instance.warned_at is None:
            return decision(ExpiryAction.WARN, "expired_without_a_warning")
        return decision(ExpiryAction.STOP, "expired_after_a_warning")
    if instance.warned_at is not None:
        return decision(ExpiryAction.LEAVE, "already_warned")
    if instance.expires_at - now <= timedelta(minutes=settings.warning_lead_minutes):
        return decision(ExpiryAction.WARN, "expiry_is_near")
    return decision(ExpiryAction.LEAVE, "inside_its_lifetime")


def decide_expiry_actions(
    instances: Sequence[TaggedInstance],
    *,
    now: datetime,
    settings: LaneSettings,
) -> tuple[ExpiryDecision, ...]:
    """One decision per instance, in the order they arrived.

    Every instance gets a decision, including the ones that are not ours. A sweep reporting
    only what it acted on cannot tell "nothing needed doing" from "the filter matched nothing",
    and the second of those is the janitor being silently broken -- which is the failure mode a
    scheduled component has and an invoked one does not.
    """
    return tuple(_decide(one, now=now, settings=settings) for one in instances)
