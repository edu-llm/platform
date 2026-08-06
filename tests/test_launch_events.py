"""Turning what CloudTrail answers into what the join takes.

**THE EVENT BODY IS A STRING HOLDING JSON, WHICH IS THE PART THAT BITES.** ``lookup-events``
returns ``CloudTrailEvent`` as text, so a reader that treated the outer record as the event
finds no ``userIdentity`` at all and every launch resolves to no role -- which is a mismatch
list of length zero on a busy morning. The fixture beside these tests is a real answer from
this account, sanitized, so the shape is measured rather than assumed.

Nothing here reaches AWS. Where a call is unavoidable to the shape of the test, ``aws`` is
replaced and the replacement records what it was asked for.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import read_launch_events
from read_launch_events import (
    LAUNCH_EVENT_NAMES,
    NO_SESSION_ISSUER,
    LaunchEventParseError,
    parse_event,
)
from visibility_board import RUN_ID_TAG

from edullm_platform.capture_tooling import CaptureFailedError

FIXTURE = PROJECT_ROOT / "fixtures/evidence/instruments/run-instances.sanitized.json"

A_RUN = "run_019fa73d-be37-7066-984b-a4bacf194f49"


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "{}", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_the_role_name_comes_out_of_the_session_issuer() -> None:
    """Mutation: read `userIdentity.arn` or `Username` instead of the session issuer.

    `Username` on the outer record is the session name, which is a broker session id for a
    person and the service's own name for a service role, and neither joins to anything. The
    fixture is the ordinary case: this account bringing its own Batch capacity up, under
    `AWSServiceRoleForBatch`, with `aws-batch` in the envelope.
    """
    answer = json.loads(FIXTURE.read_text(encoding="utf-8"))
    found = [parse_event(record) for record in answer["Events"]]

    assert found, "the fixture holds no event this parses"
    assert all(event.role_name for event in found)
    assert all("/" not in event.role_name for event in found)
    assert {event.role_name for event in found} == {"AWSServiceRoleForBatch"}
    assert {record.get("Username") for record in answer["Events"]} == {"aws-batch"}


def test_a_run_id_tag_on_the_launch_is_carried_through() -> None:
    """Mutation: ignore tagSpecificationSet, which makes every platform launch a mismatch."""
    record = {
        "EventId": "e1",
        "EventName": "RunInstances",
        "EventTime": "2026-08-04T14:16:17-05:00",
        "CloudTrailEvent": json.dumps(
            {
                "userIdentity": {
                    "sessionContext": {"sessionIssuer": {"userName": "Intern-amy.lin-sbsandbox"}}
                },
                "requestParameters": {
                    "tagSpecificationSet": {
                        "items": [
                            {
                                "resourceType": "instance",
                                "tags": [{"key": "edullm:run-id", "value": A_RUN}],
                            }
                        ]
                    }
                },
            }
        ),
    }
    event = parse_event(record)
    assert event.role_name == "Intern-amy.lin-sbsandbox"
    assert event.run_id == A_RUN


def test_a_batch_submission_carries_its_tags_under_a_different_key() -> None:
    """Mutation: handle only the EC2 tag shape.

    `SubmitJob` puts tags in `requestParameters.tags` as an object, and EC2 puts them in
    `tagSpecificationSet` as a list of key/value pairs. A reader that knows one shape reports
    every job submitted directly to Batch as untagged, which makes every one of them a
    mismatch.
    """
    record = {
        "EventId": "e2",
        "EventName": "SubmitJob",
        "EventTime": "2026-08-04T09:00:00Z",
        "CloudTrailEvent": json.dumps(
            {
                "userIdentity": {
                    "sessionContext": {
                        "sessionIssuer": {"userName": "sbsandbox-intern-edullm-run-preview"}
                    }
                },
                "requestParameters": {"tags": {"edullm:run-id": A_RUN}},
            }
        ),
    }
    event = parse_event(record)
    assert event.role_name == "sbsandbox-intern-edullm-run-preview"
    assert event.run_id == A_RUN


def test_a_launch_carrying_no_run_id_tag_reports_none_rather_than_an_empty_string() -> None:
    """Mutation: return the tag's value whatever it is, or default it to "".

    An empty string is a run id nothing will ever match, which is what a mismatch is, so the
    two happen to agree today. They stop agreeing the moment anything asks whether the launch
    was tagged at all, and `None` is the answer that survives that question.
    """
    record = {
        "EventId": "e3",
        "EventName": "RunInstances",
        "EventTime": "2026-08-04T09:00:00Z",
        "CloudTrailEvent": json.dumps(
            {
                "userIdentity": {
                    "sessionContext": {"sessionIssuer": {"userName": "Intern-amy.lin-sbsandbox"}}
                },
                "requestParameters": {"tags": {"edullm:run-id": ""}},
            }
        ),
    }
    assert parse_event(record).run_id is None


def test_an_event_with_no_session_issuer_is_returned_rather_than_dropped() -> None:
    """Mutation: return None for a root or IAM-user call.

    A launch by an identity that is not an assumed role is exactly the kind of thing worth
    seeing, and dropping it would remove it from the denominator as well as from the list.
    """
    record = {
        "EventId": "e4",
        "EventName": "RunInstances",
        "EventTime": "2026-08-04T09:00:00Z",
        "CloudTrailEvent": json.dumps(
            {"userIdentity": {"type": "IAMUser", "userName": "someuser"}}
        ),
    }
    assert parse_event(record).role_name == NO_SESSION_ISSUER


def test_an_unparseable_event_body_is_refused_rather_than_read_as_empty() -> None:
    """Mutation: swallow the JSON error and return None.

    An event body this cannot parse is a launch nobody looked at, and returning None puts it
    outside the denominator, which is the one thing the denominator exists to prevent.
    """
    with pytest.raises(LaunchEventParseError):
        parse_event({"EventId": "e5", "EventName": "RunInstances", "CloudTrailEvent": "{oops"})


def test_an_event_with_no_readable_time_is_refused_too() -> None:
    """Mutation: substitute the collection time when the event time will not parse.

    An event stamped with the moment it was read is an event on the wrong day, and the daily
    aggregation downstream keys on exactly that. Refusing is the honest answer, and the
    denominator is what makes refusing safe: nothing is silently lost.
    """
    with pytest.raises(LaunchEventParseError):
        parse_event(
            {
                "EventId": "e6",
                "EventName": "RunInstances",
                "EventTime": "the other day",
                "CloudTrailEvent": json.dumps({"userIdentity": {}}),
            }
        )


def test_the_four_event_names_are_the_ones_that_start_compute() -> None:
    """Mutation: add or drop an event name without deciding what a launch is.

    `PurchaseCapacityBlock` is deliberately not here: the mismatch list staying blind to
    capacity purchases is a recorded decision, and adding it silently would change a decision
    rather than implement one. A capacity purchase is an API call with a price and no
    instance behind it, so there is no launch for this to join.

    `CreateApp` is here because Studio became the exploration surface, and every entry carries
    the service that has to have issued it -- see the case below, which is the whole reason the
    mapping is a mapping.
    """
    assert LAUNCH_EVENT_NAMES == {
        "RunInstances": "ec2.amazonaws.com",
        "CreateFleet": "ec2.amazonaws.com",
        "SubmitJob": "batch.amazonaws.com",
        "CreateApp": "sagemaker.amazonaws.com",
    }
    assert "PurchaseCapacityBlock" not in LAUNCH_EVENT_NAMES


def test_an_amplify_create_app_is_not_a_launch_and_a_sagemaker_one_is() -> None:
    """THE ONE THAT MATTERS HERE. Mutation: match on the event name and not the source.

    Both of these are real shapes out of this account's own trail on the same
    ``lookup-events`` call: Studio starting a JupyterLab app on 2026-08-03, and Amplify
    creating a static site on 2026-08-06. An event name is not unique across AWS, and the two
    services that share this one are a service that starts an instance and a service that does
    not -- so a reader keyed on the name alone reports a web deploy as compute and inflates
    the denominator with events no run can ever reconcile.
    """
    studio = {
        "EventId": "e1",
        "EventName": "CreateApp",
        "EventSource": "sagemaker.amazonaws.com",
        "CloudTrailEvent": json.dumps(
            {
                "eventSource": "sagemaker.amazonaws.com",
                "eventTime": "2026-08-03T22:22:31Z",
                "requestParameters": {"domainId": "d-example", "spaceName": "frank"},
            }
        ),
    }
    amplify = {
        "EventId": "e2",
        "EventName": "CreateApp",
        "EventSource": "amplify.amazonaws.com",
        "CloudTrailEvent": json.dumps(
            {
                "eventSource": "amplify.amazonaws.com",
                "eventTime": "2026-08-06T16:05:55Z",
                "requestParameters": {"name": "calc-readiness-demo"},
            }
        ),
    }

    assert read_launch_events.is_a_launch(studio)
    assert not read_launch_events.is_a_launch(amplify)
    # The envelope is absent on some fixtures and the body always carries it, so both are read
    # and a record with only the inner spelling is still understood.
    assert (
        read_launch_events.event_source_of({k: v for k, v in studio.items() if k != "EventSource"})
        == "sagemaker.amazonaws.com"
    )
    # Neither spelling present is not a launch, which is the safe direction: an event nobody
    # can identify counted as compute is a person reported for something they did not do.
    assert not read_launch_events.is_a_launch({"EventName": "CreateApp"})


def test_a_sagemaker_tag_list_is_read_as_tags() -> None:
    """Mutation: keep only the Batch object shape and the EC2 tagSpecificationSet shape.

    SageMaker writes ``requestParameters.tags`` as a list of ``key``/``value`` objects, which
    is neither of the other two. A Studio app carries no run id whatever the shape, so this
    changes no verdict today; what it changes is that a launch this reads reports the project
    it was tagged with rather than no tags at all.
    """
    event = read_launch_events.parse_event(
        {
            "EventId": "e3",
            "EventName": "CreateApp",
            "EventSource": "sagemaker.amazonaws.com",
            "CloudTrailEvent": json.dumps(
                {
                    "eventTime": "2026-08-06T12:00:00Z",
                    "userIdentity": {
                        "sessionContext": {
                            "sessionIssuer": {"userName": "Intern-amy.lin-sbsandbox"}
                        }
                    },
                    "requestParameters": {
                        "tags": [{"key": RUN_ID_TAG, "value": "run_019fcf3c-9878"}]
                    },
                }
            ),
        }
    )

    assert event.run_id == "run_019fcf3c-9878"
    assert event.role_name == "Intern-amy.lin-sbsandbox"


def test_every_event_name_is_asked_for_and_every_page_of_each_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: stop at the first page, or ask for one event name and filter.

    `lookup-events` takes a single lookup attribute, so four names is four calls. And a
    reader that stopped at the first page would under-report the denominator on exactly the
    busy morning somebody is reading it for -- which is worse than under-reporting the list,
    because the denominator is what says the list is short.

    Each page answers with an event of the name that was asked for, issued by the service
    :data:`LAUNCH_EVENT_NAMES` pairs with it, so every one of them is a launch and the count
    below measures paging rather than filtering.
    """
    calls: list[list[str]] = []

    def answering(arguments: Any, *, profile: Any = None, region: Any = None) -> Any:
        recorded = [str(argument) for argument in arguments]
        calls.append(recorded)
        asked_for = recorded[recorded.index("--lookup-attributes") + 1].split("AttributeValue=")[1]
        body = json.dumps(
            {
                "eventSource": LAUNCH_EVENT_NAMES[asked_for],
                "userIdentity": {
                    "sessionContext": {"sessionIssuer": {"userName": "Intern-amy.lin-sbsandbox"}}
                },
            }
        )
        page = {
            "Events": [
                {
                    "EventId": f"e{len(calls)}",
                    "EventName": asked_for,
                    "EventSource": LAUNCH_EVENT_NAMES[asked_for],
                    "EventTime": "2026-08-04T09:00:00Z",
                    "CloudTrailEvent": body,
                }
            ]
        }
        if "--next-token" not in recorded:
            page["NextToken"] = "more"
        return _Completed(stdout=json.dumps(page))

    monkeypatch.setattr(read_launch_events, "aws", answering)
    found = read_launch_events.read_launch_events(
        since=date(2026, 8, 4), until=date(2026, 8, 5), profile=None, region="us-east-1"
    )

    asked = [
        call[call.index("--lookup-attributes") + 1].split("AttributeValue=")[1] for call in calls
    ]
    assert sorted(set(asked)) == sorted(LAUNCH_EVENT_NAMES)
    assert len(calls) == 2 * len(LAUNCH_EVENT_NAMES), "each name is read to its second page"
    assert len(found) == len(calls)


def test_a_refused_call_raises_rather_than_answering_with_no_launches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE ONE THAT MATTERS HERE. Mutation: return the launches read so far on a denial.

    A refusal that came back as an empty feed is a mismatch list of length zero on a morning
    nobody could look, which is the exact collapse the collector turns into "the launch feed
    was not read". It can only do that if this raises.
    """

    def refusing(arguments: Any, *, profile: Any = None, region: Any = None) -> Any:
        return _Completed(returncode=255, stderr="AccessDenied: cloudtrail:LookupEvents")

    monkeypatch.setattr(read_launch_events, "aws", refusing)
    with pytest.raises(CaptureFailedError, match="LookupEvents"):
        read_launch_events.read_launch_events(
            since=date(2026, 8, 4), until=date(2026, 8, 5), profile=None, region="us-east-1"
        )


def test_the_collector_finds_this_module_now_that_it_exists() -> None:
    """Mutation: rename the module, or rename the function inside it.

    `tools/read_substrate.py` looks this up by name rather than importing it, so that its
    absence cost one column instead of making the whole collector unimportable. The price of
    that indirection is that a rename is silent -- the launch feed would go on reporting
    itself as not built, on a morning it was there all along.
    """
    import read_substrate

    reader = read_substrate._launch_reader()
    assert reader is read_launch_events.read_launch_events
