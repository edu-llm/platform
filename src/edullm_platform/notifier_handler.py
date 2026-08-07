"""The Lambda that turns what the platform did into something somebody reads.

A thin shell over ``edullm_platform.notifications``. Everything worth testing is in those
modules; this one unwraps a delivery, decides whether a message is owed, and says which
records could not be posted.

**Three things it says, on one queue.** A run ended, which comes from Batch. A run is waiting
on a lead, which comes from the platform describing its own approval gate. And what happened
overnight, which comes from a schedule. :func:`message_for` picks between them on the
envelope's ``source`` and ``detail-type``, and each reader answers ``None`` for an envelope
that is not its own, so a delivery none of them claims is a success rather than a retry.
One queue rather than three, because the queue is where a message that could not be posted
goes to be found and all three failures are the same incident.

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
import time
from collections.abc import Callable, Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Any, Final, Protocol, cast

from edullm_platform.accelerators import AcceleratorRecord
from edullm_platform.contracts.policy import ApprovalPolicy
from edullm_platform.lifecycle_projection import CheckpointLister
from edullm_platform.notifications.approval import (
    APPROVAL_DETAIL_TYPE,
    PLATFORM_EVENT_SOURCE,
    ApprovalRequestedFacts,
    load_accelerators,
    load_policy,
    read_approval_requested,
)
from edullm_platform.notifications.delivery import Transport, WebhookTransport
from edullm_platform.notifications.facts import (
    DEFAULT_LINEAGE_BUCKET,
    LINEAGE_BUCKET_VARIABLE,
    Catalogs,
    CellLister,
    IntentReader,
    read_run_ended,
)
from edullm_platform.notifications.messages import (
    Message,
    render_approval_requested,
    render_morning_page,
    render_run_ended,
)
from edullm_platform.notifications.overnight import read_overnight
from edullm_platform.run_history import RunHistory, load_run_history

__all__ = [
    "BATCH_ITEM_FAILURES_KEY",
    "BATCH_ITEM_FAILURES_RESPONSE_TYPE",
    "CONFIG_DIRECTORY_VARIABLE",
    "WEBHOOK_SECRET_VARIABLE",
    "NotifierEventError",
    "SecretReader",
    "handler",
    "message_for",
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

#: Where the packaged reviewed configuration is.
#:
#: RESOLVED FROM THIS MODULE'S OWN LOCATION RATHER THAN WRITTEN AS AN ABSOLUTE PATH, WHICH
#: IS THE WHOLE OF THE 2026-08-06 OUTAGE. The builder copies the three reviewed files to
#: ``build_admission_lambda.PACKAGED_CONFIG_PREFIX``, which is ``edullm_platform/config``
#: inside the zip and therefore ``/var/task/edullm_platform/config`` once Lambda unpacks it.
#: This constant said ``/var/task/config`` and the template repeated the same wrong string,
#: so every invocation raised ``FileNotFoundError`` on ``organization.yaml`` while the file
#: sat in the package one directory across. Reading it off ``__file__`` makes the handler
#: and the builder agree by construction: whatever directory the module was unpacked into is
#: the directory its configuration was unpacked beside. It is also how
#: :func:`edullm_platform.admission_handler.config_directory` has always resolved it, which
#: is why the validator carries the same three files and has never once failed to find them.
CONFIG_DIRECTORY_VARIABLE: Final = "EDULLM_CONFIG_DIRECTORY"
DEFAULT_CONFIG_DIRECTORY: Final = Path(__file__).resolve().parent / "config"

MILLISECONDS_A_SECOND: Final = 1000


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


def _is_an_approval(envelope: Mapping[str, Any]) -> bool:
    """Whether this delivery is worth opening the policy for.

    The same two keys ``read_approval_requested`` checks first, asked here so the two files
    that message needs are read only when one has arrived. Duplicated rather than restructured
    because the alternative is a reader that reports why it declined, and every other reader
    in this package answers ``None`` and says nothing.
    """
    return (
        envelope.get("source") == PLATFORM_EVENT_SOURCE
        and envelope.get("detail-type") == APPROVAL_DETAIL_TYPE
    )


def _sent_at(record: Mapping[str, Any], envelope: Mapping[str, Any]) -> int:
    """When this delivery was put on the queue, in milliseconds, out of the delivery itself.

    THE ONE CLOCK THIS FUNCTION HAS, AND IT IS READ RATHER THAN TAKEN. The morning page needs
    a moment to measure a window back from, and everything under ``notifications/`` is
    written with no clock so that every answer is reproducible from a committed envelope.
    SQS stamps ``SentTimestamp`` on every record, which is the instant the schedule fired
    rather than the instant this container got round to it, so a retry after a cold start
    measures the same window the first attempt would have.

    ``time.time`` is the fallback and it is only reached by a caller that built the record by
    hand. Falling back rather than refusing, because a morning page measured from a second
    later is right and a morning page nobody got is not.
    """
    attributes = record.get("attributes")
    stamp = attributes.get("SentTimestamp") if isinstance(attributes, Mapping) else None
    if isinstance(stamp, str) and stamp.isdigit():
        return int(stamp)
    if isinstance(stamp, int) and not isinstance(stamp, bool):
        return stamp
    del envelope
    return int(time.time() * MILLISECONDS_A_SECOND)


def message_for(
    envelope: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    catalogs: Catalogs,
    policy: Callable[[], ApprovalPolicy],
    history: Callable[[], RunHistory | None],
    accelerators: Callable[[], tuple[AcceleratorRecord, ...]],
    intent_reader: IntentReader | None,
    lineage_bucket: str,
    cell_lister: CellLister | None,
    checkpoint_lister: CheckpointLister | None,
) -> Message | None:
    """The one thing to say about this delivery, or ``None`` because it is owed nothing.

    THREE SHAPES ON ONE QUEUE, AND THE SOURCE IS WHAT TELLS THEM APART. Batch's own state
    changes arrive from ``aws.batch``; the approval request and the morning trigger arrive
    from ``edullm.platform``, because nothing in AWS produces either and a reader that could
    not tell them from a job would try to price a submission as one.

    A second queue per shape was the alternative and it buys nothing. The queue is where a
    message that could not be posted goes to be found, and a message nobody got is the same
    incident whichever of the three it was. Three queues would be three dead-letter queues
    and three alarms watching for one thing.

    Every reader is asked in turn and each answers ``None`` for an envelope that is not its
    own, which is the same shape ``read_run_ended`` already had for a non-terminal event.

    ``policy``, ``history`` and ``accelerators`` arrive as callables rather than as values,
    and the laziness is the point rather than a style. Nine deliveries in ten are Batch state
    changes owing a run-ended line, and that line reads none of the three. Loading them
    eagerly would make every one of those invocations pay for reads it does not use, and
    would make a run-ended message fail on a deployment whose policy file was missing, which
    is a message about a run that demonstrably happened lost to a file it never opens.
    """
    ended = read_run_ended(
        envelope,
        catalogs=catalogs,
        intent_reader=intent_reader,
        lineage_bucket=lineage_bucket,
        cell_lister=cell_lister,
        checkpoint_lister=checkpoint_lister,
    )
    if ended is not None:
        return render_run_ended(ended)

    asked: ApprovalRequestedFacts | None = (
        read_approval_requested(
            envelope,
            catalogs=catalogs,
            policy=policy(),
            history=history(),
            accelerators=accelerators(),
        )
        if _is_an_approval(envelope)
        else None
    )
    if asked is not None:
        return render_approval_requested(asked)

    overnight = read_overnight(
        envelope,
        catalogs=catalogs,
        cell_lister=cell_lister,
        now_ms=_sent_at(record, envelope),
    )
    return None if overnight is None else render_morning_page(overnight)


def handler(
    event: Mapping[str, Any],
    context: object = None,
    *,
    transport: Transport | None = None,
    catalogs: Catalogs | None = None,
    policy: ApprovalPolicy | None = None,
    history: RunHistory | None = None,
    accelerators: Sequence[AcceleratorRecord] | None = None,
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

    ``policy``, ``history`` and ``accelerators`` come from the same packaged directory the
    catalogs come from, and each is read at most once per invocation and only when an
    approval request arrives. ``history`` is ``None`` where no reading is packaged, which the
    approval message says rather than treating as a shape nothing has run; ``accelerators``
    is empty there, and the message drops the clause naming the machine's memory.
    """
    del context

    sender = transport if transport is not None else WebhookTransport(endpoint=_webhook_endpoint())
    directory = Path(os.environ.get(CONFIG_DIRECTORY_VARIABLE) or DEFAULT_CONFIG_DIRECTORY)
    loaded = catalogs if catalogs is not None else Catalogs.load(directory)
    rules = cache(lambda: policy if policy is not None else load_policy(directory))
    reading = cache(lambda: history if history is not None else load_run_history(directory))
    cards = cache(
        lambda: tuple(accelerators) if accelerators is not None else load_accelerators(directory)
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
            message = message_for(
                _envelope(record),
                record,
                catalogs=loaded,
                policy=rules,
                history=reading,
                accelerators=cards,
                intent_reader=reader,
                lineage_bucket=bucket,
                cell_lister=cells,
                checkpoint_lister=lister,
            )
            if message is not None:
                sender.deliver(message)
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
