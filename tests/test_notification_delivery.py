"""How a message travels, tested without a network.

The transport knows nothing about runs, money or Batch, which is the other half of the seam:
wording changes cannot break delivery and delivery changes cannot alter wording.
"""

from __future__ import annotations

import json
from typing import Self

import pytest

from edullm_platform.notifications.delivery import (
    WebhookDeliveryError,
    WebhookTransport,
    webhook_payload,
)
from edullm_platform.notifications.messages import RUNS_CHANNEL, Message

MESSAGE = Message(channel=RUNS_CHANNEL, text="Amy Lin · onboarding · $1.20 spent.")


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return b""


class FakeOpener:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[object] = []

    def open(self, request: object, timeout: float) -> FakeResponse:
        self.requests.append(request)
        return FakeResponse(self.status)


def test_the_body_is_the_message_text_under_a_key_slack_and_google_chat_both_read() -> None:
    """One shape rather than a format per destination.

    Both Slack incoming webhooks and Google Chat read `text`, and there is no chat
    integration in this organization to be specific to, so the payload is the one both take.
    """
    assert json.loads(webhook_payload(MESSAGE)) == {"text": MESSAGE.text}


def test_delivering_posts_json_to_the_endpoint() -> None:
    opener = FakeOpener()

    WebhookTransport(endpoint="https://example.invalid/hook", opener=opener).deliver(MESSAGE)

    assert len(opener.requests) == 1
    request = opener.requests[0]
    assert request.full_url == "https://example.invalid/hook"
    assert request.get_method() == "POST"
    assert request.headers["Content-type"] == "application/json"
    assert json.loads(request.data) == {"text": MESSAGE.text}


def test_a_refused_post_raises_and_names_the_status_without_naming_the_endpoint() -> None:
    """Mutation: put the endpoint in the error.

    The endpoint is the secret. A Slack incoming webhook carries its whole credential in the
    URL path, so an error naming it puts the credential into CloudWatch, where the function's
    log group is readable by anybody with the console.
    """
    opener = FakeOpener(status=403)

    with pytest.raises(WebhookDeliveryError) as raised:
        WebhookTransport(
            endpoint="https://example.invalid/T000/B000/xoxb-refused", opener=opener
        ).deliver(MESSAGE)

    # The path is where a Slack webhook keeps its secret, so it is the part named here. A
    # bare `"hook" not in` would be the check that cannot fail in reverse: the message says
    # "the webhook answered", so it can never pass however much of the endpoint leaks.
    assert "403" in str(raised.value)
    assert "example.invalid" not in str(raised.value)
    assert "xoxb-refused" not in str(raised.value)


def test_an_unreachable_endpoint_raises_and_names_neither_the_host_nor_the_reason() -> None:
    """The production path, where build_opener raises rather than returning a status.

    HTTPError carries the URL it was raised against and URLError's reason can carry it too,
    so both are re-raised with the chain suppressed. A chained traceback in CloudWatch would
    print the whole webhook, which is the credential.
    """
    import urllib.error

    class UnreachableOpener:
        def open(self, request: object, timeout: float) -> object:
            raise urllib.error.URLError("Name or service not known: hooks.example.invalid")

    with pytest.raises(WebhookDeliveryError) as raised:
        WebhookTransport(
            endpoint="https://hooks.example.invalid/T000/B000/xoxb", opener=UnreachableOpener()
        ).deliver(MESSAGE)

    assert "xoxb" not in str(raised.value)
    assert "hooks.example.invalid" not in str(raised.value)
    assert raised.value.__cause__ is None


def test_the_transport_holds_no_opinion_about_what_a_message_says() -> None:
    """Mutation: format anything in the transport.

    Every string a person reads is in messages.py. This module may name a status code and a
    content type and nothing else.
    """
    from pathlib import Path

    source = Path("src/edullm_platform/notifications/delivery.py").read_text(encoding="utf-8")

    assert "facts" not in source
    assert "$" not in source
