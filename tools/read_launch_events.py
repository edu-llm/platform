"""What CloudTrail says launched, reduced to what the mismatch join takes.

**THE EVENT IS A STRING HOLDING JSON INSIDE THE RECORD THAT DESCRIBES IT.** ``lookup-events``
answers with a thin envelope -- ``EventId``, ``EventName``, ``EventTime``, ``Username`` -- and
the whole event as text under ``CloudTrailEvent``. Everything the join needs is inside that
string: the role name is ``userIdentity.sessionContext.sessionIssuer.userName``, and the
launch's tags are in ``requestParameters``. A reader that stopped at the envelope would find no
role on any event and report a mismatch list of length zero every morning.

**``Username`` IS NOT THE ROLE.** On the envelope it is the session name, which is
``broker-<first>.<last>-<digits>`` for a person federating through the dashboard and
``aws-batch`` for the service role that brings this account's own capacity up. The session
issuer is the role, and it is the only field that joins to ``config/organization.yaml``.

**TAGS ARE IN TWO SHAPES BECAUSE TWO SERVICES WRITE THEM.** EC2 puts them under
``requestParameters.tagSpecificationSet.items[].tags[]`` as key/value objects; Batch puts them
under ``requestParameters.tags`` as one object. Both are read, because a reader that knew one
shape would report every job submitted straight to Batch as carrying no run id, and a run id is
the only thing that clears a launch.

**NOTHING IS DROPPED, AND AN UNREADABLE EVENT RAISES.** An event with no session issuer is
returned under a placeholder role name rather than skipped, so it lands in the report's
unresolved bucket and is counted. An event whose body will not parse raises, because a launch
this could not read is a launch nobody looked at, and the whole point of the denominator is
that no launch leaves without being counted.

Three event names, listed literally. ``PurchaseCapacityBlock`` is deliberately absent: the
mismatch list staying blind to capacity purchases is a decision rather than an oversight, and
adding the event here would reverse it quietly. A capacity purchase is an API call with a price
and no instance behind it, so it has no launch for this to join.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

from visibility_board import RUN_ID_TAG

from edullm_platform.capture_tooling import CaptureFailedError, aws
from edullm_platform.substrate import LaunchEvent

__all__ = [
    "LAUNCH_EVENT_NAMES",
    "NO_SESSION_ISSUER",
    "LaunchEventParseError",
    "parse_event",
    "read_launch_events",
]

#: What counts as starting compute in this account. Literal and short on purpose: each name
#: here is a decision about what the mismatch list covers, and the list is extended by an
#: argument rather than by a guess.
LAUNCH_EVENT_NAMES: Final = ("RunInstances", "CreateFleet", "SubmitJob")

#: The role name given to a launch made by something that is not an assumed role. Written as
#: a name rather than as ``None`` so the event still lands in a bucket and still counts toward
#: the denominator; it will never match a binding, so it reports as unresolved, which is
#: exactly what it is.
NO_SESSION_ISSUER: Final = "<no session issuer>"

#: How many events one call answers with. CloudTrail caps this at fifty and the loop below
#: pages, so this is the page size rather than a limit on what is read.
PAGE_SIZE: Final = 50

#: How many pages of one event name this will read before giving up.
#:
#: THE CEILING EXISTS BECAUSE THE FEED IS BIGGER THAN IT LOOKS AND IT IS SLOW. Measured
#: against this account on 2026-08-05, a three-day window had not finished after six minutes:
#: Batch brings capacity up and takes it down all day, so `RunInstances` alone is most of a
#: management-event history that `lookup-events` hands back fifty at a time with no way to
#: filter by role. A scheduled job that pages until it finishes is one that can take the whole
#: morning.
#:
#: HITTING IT RAISES RATHER THAN RETURNING WHAT WAS READ, WHICH IS THE WHOLE POINT. A
#: truncated feed is a denominator that is wrong, and a wrong denominator is worse than an
#: absent one -- the report would say it examined nine hundred launches and found no
#: mismatches, on a morning it stopped reading before it reached the afternoon. The collector
#: turns the refusal into "the launch feed was not read", which is true.
MAXIMUM_PAGES: Final = 120


class LaunchEventParseError(RuntimeError):
    """One event could not be read, which is never the same as there being none."""


def _tags(request: Mapping[str, Any]) -> dict[str, str]:
    """Both tag shapes, flattened into one mapping.

    The Batch shape is read first and the EC2 shape second, which is arbitrary and safe: an
    event carries one of the two, never both, because one service made the call.
    """
    tags: dict[str, str] = {}
    plain = request.get("tags")
    if isinstance(plain, Mapping):
        tags.update({str(key): str(value) for key, value in plain.items()})
    specification = request.get("tagSpecificationSet")
    if isinstance(specification, Mapping):
        for item in specification.get("items") or []:
            if not isinstance(item, Mapping):
                continue
            for tag in item.get("tags") or []:
                if isinstance(tag, Mapping) and "key" in tag:
                    tags[str(tag["key"])] = str(tag.get("value", ""))
    return tags


def parse_event(record: Mapping[str, Any]) -> LaunchEvent:
    """One ``lookup-events`` entry, as a :class:`LaunchEvent`."""
    body_text = record.get("CloudTrailEvent")
    try:
        body = json.loads(str(body_text))
    except ValueError as error:
        raise LaunchEventParseError(
            f"the body of event {record.get('EventId')!r} is not JSON"
        ) from error
    if not isinstance(body, Mapping):
        raise LaunchEventParseError(
            f"the body of event {record.get('EventId')!r} is not an object"
        )

    identity = body.get("userIdentity")
    issuer: Mapping[str, Any] = {}
    if isinstance(identity, Mapping):
        session = identity.get("sessionContext")
        if isinstance(session, Mapping) and isinstance(session.get("sessionIssuer"), Mapping):
            issuer = session["sessionIssuer"]
    role_name = str(issuer.get("userName") or NO_SESSION_ISSUER)

    request = body.get("requestParameters")
    run_id = _tags(request).get(RUN_ID_TAG) if isinstance(request, Mapping) else None

    when = body.get("eventTime") or record.get("EventTime")
    try:
        # CloudTrail writes the trailing Z form; fromisoformat has read it since 3.11, so the
        # `.replace("Z", "+00:00")` this used to carry was doing nothing but hiding the fact.
        occurred_at = datetime.fromisoformat(str(when))
    except ValueError as error:
        raise LaunchEventParseError(
            f"event {record.get('EventId')!r} carries {when!r} where a time belongs"
        ) from error

    return LaunchEvent(
        event_id=str(record.get("EventId") or ""),
        event_name=str(record.get("EventName") or body.get("eventName") or ""),
        occurred_at=occurred_at,
        role_name=role_name,
        run_id=run_id or None,
    )


def read_launch_events(
    *, since: date, until: date, profile: str | None, region: str
) -> tuple[LaunchEvent, ...]:
    """Every launch in the window, one call per event name and every page of each.

    One call per name because ``lookup-events`` accepts a single lookup attribute, so three
    names is three calls rather than one filter. The pagination loop is not defensive
    programming: measured against this account, a three-day window had not finished after six
    minutes, because Batch brings capacity up and down all day and the feed is handed back
    fifty at a time.

    Bounded by :data:`MAXIMUM_PAGES`, and hitting the bound raises. A reader that stopped at a
    page and returned what it had would under-report the denominator on exactly the busy
    morning somebody is reading it for -- and a denominator that is wrong is worse than one
    that is missing, because the report would say it examined nine hundred launches and found
    nothing on a morning it stopped before the afternoon.
    """
    found: list[LaunchEvent] = []
    for event_name in LAUNCH_EVENT_NAMES:
        token: str | None = None
        for page in range(MAXIMUM_PAGES + 1):
            if page == MAXIMUM_PAGES:
                raise CaptureFailedError(
                    f"{event_name} has more than {MAXIMUM_PAGES * PAGE_SIZE} events between "
                    f"{since} and {until}, which is more than one reading can take. The feed "
                    "is refused rather than truncated: a short feed is a denominator that is "
                    "wrong, and a wrong denominator reads as a morning somebody examined."
                )
            call: list[str] = [
                "cloudtrail",
                "lookup-events",
                "--lookup-attributes",
                f"AttributeKey=EventName,AttributeValue={event_name}",
                "--start-time",
                since.isoformat(),
                "--end-time",
                until.isoformat(),
                "--max-results",
                str(PAGE_SIZE),
            ]
            if token:
                call += ["--next-token", token]
            completed = aws(call, profile=profile, region=region)
            if completed.returncode != 0:
                raise CaptureFailedError(
                    f"aws cloudtrail lookup-events was refused for {event_name}: "
                    f"{(completed.stderr or completed.stdout or '').strip()}"
                )
            try:
                answer = json.loads(completed.stdout or "{}")
            except ValueError as error:
                raise CaptureFailedError(
                    "CloudTrail answered with something that is not JSON"
                ) from error
            for record in answer.get("Events") or []:
                if isinstance(record, Mapping):
                    found.append(parse_event(record))
            token = answer.get("NextToken")
            if not token:
                break
    return tuple(found)
