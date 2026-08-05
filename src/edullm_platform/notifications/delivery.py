"""How a message travels, and nothing about what it says.

**One POST of one JSON body, over ``urllib.request``.** No new dependency, because
``uv sync --locked`` is a gate and a notifier is not worth a lockfile change. The body is
``{"text": ...}``, which Slack incoming webhooks and Google Chat both read, and which was
chosen because there is no chat integration in this organization to be specific to. Verified
2026-08-05: ``aws chatbot describe-slack-workspaces`` answers with an empty list and the only
GitHub App installed on the organization is ``cursor``.

**The endpoint is the credential and never reaches a log.** A Slack incoming webhook carries
its whole secret in the URL path, so an error naming the endpoint puts that secret into the
function's CloudWatch group. Every error here names a status code and nothing else.

**A refusal raises and the caller decides.** This module has no opinion about whether a
message nobody received should retry, dead-letter or be dropped, because that depends on what
the message was about.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Final, Protocol, cast

from .messages import Message

__all__ = [
    "CONTENT_TYPE",
    "DEFAULT_TIMEOUT_SECONDS",
    "Opener",
    "Transport",
    "WebhookDeliveryError",
    "WebhookTransport",
    "webhook_payload",
]

CONTENT_TYPE: Final = "application/json"

#: Five seconds. A webhook that has not answered in five is one this invocation should stop
#: waiting on: the Lambda has a timeout of its own, and a delivery that ties it up delays
#: every message behind it in the queue.
DEFAULT_TIMEOUT_SECONDS: Final = 5.0

_SUCCESSFUL = range(200, 300)


class WebhookDeliveryError(RuntimeError):
    """The endpoint did not accept the message.

    Carries the status and never the endpoint, because the endpoint is the secret.
    """


class Transport(Protocol):
    """The whole interface between a message and the world.

    One method, so the handler can be tested against a list and the wording can be rendered
    with nothing attached at all.
    """

    def deliver(self, message: Message) -> None: ...


class Opener(Protocol):
    """What an opener offers, described so mypy has something and a test can fill it.

    A seam rather than a mock library, matching the ``ObjectStore`` protocol in
    ``lifecycle_handler.py``: a test supplies its own and gets the same code path the
    deployed function takes, rather than a branch that only exists for tests.

    The default is ``urllib.request.build_opener()`` and not the ``urllib.request`` module.
    The module has ``urlopen`` and no ``open``, so naming the module here type-checks against
    nothing and fails at run time on the first delivery.
    """

    def open(self, request: Any, timeout: float) -> Any: ...


def webhook_payload(message: Message) -> bytes:
    """The body, as compact bytes.

    Separators without spaces, so what is measured against a size limit is what is sent.
    """
    return json.dumps({"text": message.text}, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class WebhookTransport:
    """One endpoint, one POST.

    The channel on the message is not used to choose an endpoint, and that is a statement
    about what exists rather than a limitation to fix later: this slice posts to one channel,
    because the approvals channel and the direct messages the design describes need a chat
    integration and an identity map that this organization does not have.
    """

    endpoint: str
    opener: Opener | None = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def deliver(self, message: Message) -> None:
        request = urllib.request.Request(  # https, and the scheme is ours
            self.endpoint,
            data=webhook_payload(message),
            headers={"Content-Type": CONTENT_TYPE},
            method="POST",
        )
        opener: Opener = (
            self.opener if self.opener is not None else cast(Opener, urllib.request.build_opener())
        )
        # TWO WAYS A REFUSAL ARRIVES, AND BOTH HAVE TO BE CAUGHT.
        #
        # build_opener installs HTTPErrorProcessor, which raises HTTPError on any non-2xx
        # rather than returning it, so the status check below never sees a real 403. It sees
        # the status a test's opener returns, which is why both are here: the check is what a
        # test exercises and the except is what production takes.
        #
        # `from None` on both, deliberately. HTTPError carries the URL it was raised against
        # and URLError's reason can carry it too, and a chained traceback in CloudWatch would
        # print the whole webhook, which is the credential. The status survives, which is the
        # part worth having.
        try:
            with opener.open(request, timeout=self.timeout_seconds) as response:
                status = getattr(response, "status", None)
                if status not in _SUCCESSFUL:
                    raise WebhookDeliveryError(
                        f"the webhook answered {status}, so the message was not delivered. "
                        "The endpoint is withheld because it carries the credential."
                    )
        except urllib.error.HTTPError as error:
            raise WebhookDeliveryError(
                f"the webhook answered {error.code}, so the message was not delivered. "
                "The endpoint is withheld because it carries the credential."
            ) from None
        except urllib.error.URLError:
            raise WebhookDeliveryError(
                "the webhook could not be reached, so the message was not delivered. "
                "The endpoint and the reason are both withheld because either can carry "
                "the credential."
            ) from None
