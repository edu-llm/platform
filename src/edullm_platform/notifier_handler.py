"""The Lambda that turns a Batch state change into something somebody reads.

A thin shell over ``edullm_platform.notifications``. Everything worth testing is in those
three modules; this one unwraps a delivery, decides whether a message is owed, and says which
records could not be posted.

**It reads and it cannot act.** The role holds a queue read, one secret read, one object read
under the lineage store's ``intent/`` prefix, one listing of the outputs bucket,
``batch:ListJobs`` under a region condition, and its own log group. Every one of them is a
read in the strictest sense. ``batch:SubmitJob``, ``batch:CancelJob`` and
``batch:TerminateJob`` are absent and named as absent in the role, because the component that
says what happened must not be able to make something happen and an event-driven component is
the worst place to put that ability.

**It reads the event and never the substrate.** The substrate is a nightly aggregation and a
notification that waits on a nightly file is not a notification. What a message carries comes
from the envelope Batch sent, from three reviewed configuration files packaged into this zip,
from one record admission sealed before the job existed, and from Batch's own account of an
array's cells. None of the four is an aggregation and none of them is behind a schedule.

**It does not read the attempt records, and that is a race rather than a policy.** They carry
the same windows and the recorder writes them in answer to the same events that drive this
function. Measured 2026-08-05: an attempt record lands 1 to 4 seconds after the envelope's own
time, and on both array runs in the store the final cell's record lands at or after the
parent's terminal event.

**The delivery arrives through SQS, and that is not a workaround.** An EventBridge rule
targeting a Lambda directly needs ``AWS::Lambda::Permission``, which needs
``lambda:AddPermission``, which the deployer policy excludes on purpose. A queue also gives
the "messages stopped being sent" failure a depth metric an alarm can watch, which is the
whole of how anybody would find out that this function had stopped.

**A failure fails one message where it can and the invocation where it cannot.** Same shape as
``lifecycle_handler.handler`` and for the same reason: a partial list is only honoured by an
event source mapping declaring ``ReportBatchItemFailures``, and ``infra/notifications.yaml``
declares it.

**A message nobody is owed is a success.** Most deliveries on this queue are not endings.
Reporting them as failures would send every RUNNABLE and RUNNING event round the retry loop
into the dead-letter queue, and the alarm there would then fire on the platform working.

**A duplicate message is the accepted cost of a redelivery.** Unlike the lineage recorder,
this writes nothing immutable, so there is no conditional write to make a replay inert. A
redelivered event posts the line twice. That is a worse outcome than posting once and a much
better one than posting nothing, and closing it would need a store this function does not
have.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final, Protocol, cast

from edullm_platform.lifecycle_projection import CheckpointLister
from edullm_platform.notifications.delivery import Transport, WebhookTransport
from edullm_platform.notifications.facts import (
    DEFAULT_LINEAGE_BUCKET,
    LINEAGE_BUCKET_VARIABLE,
    Catalogs,
    CellLister,
    IntentReader,
    read_run_ended,
)
from edullm_platform.notifications.messages import render_run_ended

__all__ = [
    "BATCH_ITEM_FAILURES_KEY",
    "BATCH_ITEM_FAILURES_RESPONSE_TYPE",
    "CONFIG_DIRECTORY_VARIABLE",
    "WEBHOOK_SECRET_VARIABLE",
    "NotifierEventError",
    "SecretReader",
    "handler",
]

#: The key Lambda reads a per-message verdict out of, and the event source mapping property
#: that makes it read one. Named here, beside the code that emits the key, so the seam test
#: comparing this handler with ``infra/notifications.yaml`` has one side to read.
BATCH_ITEM_FAILURES_KEY: Final = "batchItemFailures"
BATCH_ITEM_FAILURES_RESPONSE_TYPE: Final = "ReportBatchItemFailures"

#: Which Secrets Manager secret holds the webhook URL. A name rather than a value, so the URL
#: is never in a template, in this repository, or in an Actions variable. The value is created
#: by hand; infra/README.md carries the command.
WEBHOOK_SECRET_VARIABLE: Final = "EDULLM_WEBHOOK_SECRET_ID"

#: Where the packaged reviewed configuration is. Lambda unpacks the zip at /var/task.
CONFIG_DIRECTORY_VARIABLE: Final = "EDULLM_CONFIG_DIRECTORY"
DEFAULT_CONFIG_DIRECTORY: Final = "/var/task/config"


class NotifierEventError(ValueError):
    """The queue delivered something this handler cannot read as an EventBridge envelope."""


class SecretReader(Protocol):
    """The one Secrets Manager call this handler makes, described so mypy has something.

    boto3 is absent at type-check time by design, so this is the seam, the same discipline
    ``lifecycle_handler.ObjectStore`` uses for the write.
    """

    def get_secret_value(self, **arguments: Any) -> Any: ...


def _records(event: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    records = event.get("Records")
    if not isinstance(records, list):
        raise NotifierEventError(
            "the event source mapping delivers a batch under 'Records'; this event has none"
        )
    return [record for record in records if isinstance(record, Mapping)]


def _envelope(record: Mapping[str, Any]) -> Mapping[str, Any]:
    body = record.get("body")
    if not isinstance(body, str):
        raise NotifierEventError("an SQS record must carry its EventBridge envelope as a body")
    parsed = json.loads(body)
    if not isinstance(parsed, Mapping):
        raise NotifierEventError("an SQS record's body must be a JSON object")
    return cast(Mapping[str, Any], parsed)


def _webhook_endpoint() -> str:
    """The URL, read from the secret the environment names, on every invocation.

    Read here rather than passed in a template variable, because a Slack incoming webhook
    carries its whole credential in the URL and a template variable is plaintext in
    CloudFormation, in the console and in `get-function-configuration`.

    EVERY INVOCATION AND NOT ONCE PER CONTAINER, WHICH IS THE ONE THING TO PRESERVE HERE.
    Caching it in a module-level global is the obvious optimisation and it is what makes a
    rotation silently ineffective: `put-secret-value` keeps the ARN, so nothing about the
    deployed function changes, and a warm container would go on posting to the URL that was
    just revoked until Lambda happened to recycle it. That is a notifier that has stopped
    reaching anybody while every alarm reads normal, because Slack answers a retired webhook
    with a 404 rather than a timeout and the retries dead-letter quietly at three.

    The cost is one GetSecretValue per run ending, which is a handful a day.

    Nothing here ever puts the value in an exception. The message below says the value is
    withheld and means it: the endpoint is the credential, so a message quoting the thing it
    is complaining about would put the webhook in CloudWatch.
    """
    import boto3  # type: ignore[import-not-found]  # in the runtime, not in pyproject

    secret_id = os.environ[WEBHOOK_SECRET_VARIABLE]
    reader = cast(SecretReader, boto3.client("secretsmanager"))
    answer = reader.get_secret_value(SecretId=secret_id)
    value = answer.get("SecretString")
    if not isinstance(value, str) or not value.startswith("https://"):
        raise NotifierEventError(
            "the webhook secret does not hold an https URL, so nothing can be posted. "
            "The value is withheld because it carries the credential."
        )
    return value


def _default_s3_client() -> Any:
    """One client for both S3 reads, which is why this is one function and not two.

    ``intent_reader`` calls ``get_object`` and ``checkpoint_lister`` calls
    ``list_objects_v2``, and a boto3 S3 client answers both. Two clients would be two
    connection pools for one service, and the same line in ``lifecycle_handler`` makes the
    same point about the recorder's writer and its lister.
    """
    # No `type: ignore` here or below. mypy reports the missing stub once per module, so a
    # second one is an unused-ignore error rather than belt and braces.
    import boto3

    return boto3.client("s3")


def _default_cell_lister() -> CellLister:
    import boto3

    return cast(CellLister, boto3.client("batch"))


def handler(
    event: Mapping[str, Any],
    context: object = None,
    *,
    transport: Transport | None = None,
    catalogs: Catalogs | None = None,
    intent_reader: IntentReader | None = None,
    cell_lister: CellLister | None = None,
    checkpoint_lister: CheckpointLister | None = None,
) -> dict[str, Any]:
    """Post a message for every delivery that is owed one, and name the ones that failed.

    ``context`` is unused. Every value on a message is in the envelope, in the packaged
    configuration, or under a key derived from the run id, so there is no account id to
    derive.

    THE THREE READERS ARE BUILT ONLY WHEN NOTHING WAS HANDED IN, AND THE CONDITION IS
    ``transport is None`` RATHER THAN A FLAG. A caller that supplied a transport is a test,
    and a test that got a real boto3 client behind its fake transport would reach the account
    from the suite. A caller that supplied none is the deployed function.
    """
    del context

    sender = transport if transport is not None else WebhookTransport(endpoint=_webhook_endpoint())
    loaded = (
        catalogs
        if catalogs is not None
        else Catalogs.load(
            Path(os.environ.get(CONFIG_DIRECTORY_VARIABLE) or DEFAULT_CONFIG_DIRECTORY)
        )
    )
    reader, lister, cells = intent_reader, checkpoint_lister, cell_lister
    if transport is None and reader is None and lister is None and cells is None:
        storage = _default_s3_client()
        reader = cast(IntentReader, storage)
        lister = cast(CheckpointLister, storage)
        cells = _default_cell_lister()
    bucket = os.environ.get(LINEAGE_BUCKET_VARIABLE) or DEFAULT_LINEAGE_BUCKET

    records = _records(event)
    failures: list[tuple[str | None, Exception]] = []
    for record in records:
        try:
            facts = read_run_ended(
                _envelope(record),
                catalogs=loaded,
                intent_reader=reader,
                lineage_bucket=bucket,
                cell_lister=cells,
                checkpoint_lister=lister,
            )
            if facts is not None:
                sender.deliver(render_run_ended(facts))
        except Exception as error:  # noqa: BLE001
            # Broad on purpose: one delivery that cannot be read or posted must not stop the
            # ones beside it, and every way it can fail is handled the same way.
            identifier = record.get("messageId")
            failures.append((identifier if isinstance(identifier, str) else None, error))

    unnameable = any(identifier is None for identifier, _ in failures)
    if failures and (len(failures) == len(records) or unnameable):
        raise failures[0][1]
    return {
        BATCH_ITEM_FAILURES_KEY: [
            {"itemIdentifier": identifier} for identifier, _ in failures if identifier is not None
        ]
    }
