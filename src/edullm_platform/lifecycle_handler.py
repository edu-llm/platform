"""The Lambda that writes what Batch said into the lineage store.

A thin shell over :mod:`edullm_platform.lifecycle_projection`. Everything worth testing is
in that module, which takes no I/O and no clock; this one exists to unwrap a delivery, put
four kinds of object under four keys, and decide what to do when one of them will not go.

**It records, and it cannot act.** The role holds ``s3:PutObject`` on the four lineage
prefixes below and deliberately no ``batch:SubmitJob`` and no ``batch:TerminateJob``: the
component that reads what happened must not be able to make something happen, and the only
principal in this account that may start compute is the state machine's role, reachable
only through an execution admission started.

**Nothing here calls ``batch:DescribeJobs``, the plan expected it to, and the role no
longer grants it.** The grant was argued for on the basis that the recorder needs to fetch
the job's attempt detail. It does not: the ``Batch Job State Change`` detail already
carries the ``attempts`` array with each attempt's ``startedAt``, ``stoppedAt``,
``statusReason`` and container exit code, and
:mod:`edullm_platform.lifecycle_projection` reads what it needs out of the event. The exit
code is the one field of those four nothing reads, because no Phase 0 contract has a place
to put one -- it is captured evidence rather than a projected field, which is a reason
fewer to call a describe rather than a reason to.

Reading it from a describe would be worse than unnecessary. The event is a fixed statement
about the instant the state changed; a describe returns the job as it is when the recorder
gets round to asking. So a redelivered event would project from different inputs than its
first delivery, produce different bytes under the same derived key, and be refused by the
conditional write -- leaving whichever projection arrived first, chosen by timing. The
whole write-once design rests on a replay recomputing the same record, and that grant is
the way to lose it.

``infra/iam/lifecycle-lambda-role.yaml`` therefore grants no ``batch:`` action at all, and
``tests/test_phase3_infrastructure.py`` fails if one reappears.

**The delivery arrives through SQS, not from EventBridge directly.** Decision D6 settled
this: an EventBridge rule targeting a Lambda needs ``AWS::Lambda::Permission``, which needs
``lambda:AddPermission``, which the Phase 2 deployer policy excludes on purpose. Targeting a
queue and attaching this function with an event source mapping adds a capability instead of
reversing a written decision, and buys real retry and dead-letter semantics as a side
effect. The shape of the event follows from it: ``{"Records": [{"body": "<envelope>"}]}``,
with the EventBridge envelope as a JSON string inside each record.

**A failure fails one message where it can, and the whole invocation where it cannot.**
This handler raises whenever *every* record in the batch failed, and reports a partial list
under :data:`BATCH_ITEM_FAILURES_KEY` only when something beside it succeeded. A record the
queue did not give a message id also raises, because there is no way to name it in a partial
response and losing it silently is the one outcome worth an invocation failure.

A partial list is only honoured by an event source mapping that declares
:data:`BATCH_ITEM_FAILURES_RESPONSE_TYPE`; without it, a returned list is an ordinary
successful return, every message in the batch is deleted, and the failed ones are lost with
no retry and no dead-letter. ``infra/batch-events.yaml`` declares it. It is worth being
clear that at the ``BatchSize: 1`` that template also sets, declaring it changes nothing:
"some failed" and "all failed" are the same event at size one, so the handler always
raises. It is declared because the two files were designed apart and would otherwise agree
only by coincidence -- the next person to raise the batch size would change one number and
turn a lossless path into a lossy one, silently. ``tests/test_phase3_infrastructure.py``
reads the key this handler actually answers under and the response type that template
actually declares, and compares them.

Redelivering a record that got part way through costs nothing: the keys are derived, so the
writes that already landed are refused by their own conditional write and the rest proceed.

**A 412 is success.** The lineage bucket refuses a write whose key already exists, and this
handler writes keys derived from the EventBridge event id. A redelivered event therefore
recomputes the same key and is refused, which is the mechanism rather than a problem with
it: "event duplicates do not create conflicting terminal state" is a property of the store,
and a handler that treated the refusal as a failure would turn the mechanism into a
dead-letter.

**boto3 and botocore are not project dependencies.** Both are in the Lambda runtime, and
adding them to ``pyproject.toml`` would put the whole SDK into the admission validator's zip
as well. The consequence is that the client cannot be imported at type-check time, so the
one call this module makes is described by a Protocol and the one error it must recognise is
recognised by the shape of its response rather than by its class -- the same discipline
``parse_aws_cli_error`` uses for the CLI's stderr.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from typing import Any, Final, Protocol, cast

from edullm_platform.canonical import canonical_json_bytes
from edullm_platform.contracts.base import ContractModel
from edullm_platform.lifecycle_projection import (
    OUTPUTS_BUCKET,
    CheckpointLister,
    LifecycleProjection,
    project_batch_event,
)

__all__ = [
    "BATCH_ITEM_FAILURES_KEY",
    "BATCH_ITEM_FAILURES_RESPONSE_TYPE",
    "CHECKSUM_ALGORITHM",
    "CONFLICT_ERROR_CODES",
    "LINEAGE_BUCKET_VARIABLE",
    "OUTPUTS_BUCKET_VARIABLE",
    "LifecycleEventError",
    "ObjectStore",
    "attempt_key",
    "binding_key",
    "event_key",
    "handler",
    "lineage_writes",
    "result_key",
]

#: Where the records go. The bucket is deployment configuration and the prefixes are not:
#: the state machine writes ``binding/`` and this function writes the other three, the
#: lifecycle-lambda role is scoped to exactly these four, and the Phase 2 records live
#: under ``intent/``, ``decision/`` and ``conflicts/`` beside them. Renaming one here
#: detaches it from a grant that still permits the old name.
LINEAGE_BUCKET_VARIABLE: Final = "EDULLM_LINEAGE_BUCKET"
OUTPUTS_BUCKET_VARIABLE: Final = "EDULLM_OUTPUTS_BUCKET"
DEFAULT_LINEAGE_BUCKET: Final = "sbsandbox-intern-edullm-lineage"

#: What S3 answers when a conditional write met an object that is already there. Both
#: spellings, because the REST error is ``PreconditionFailed`` and the SDK has been seen to
#: surface the status on its own; recognising one and not the other would file a duplicate
#: as a failure and dead-letter a record that was already safely stored.
CONFLICT_ERROR_CODES: Final = frozenset({"PreconditionFailed", "412"})

#: The key Lambda reads a per-message verdict out of, and the event source mapping property
#: that makes it read one at all. Named here, beside the code that emits the key, so that
#: the seam test comparing this handler with ``infra/batch-events.yaml`` has one side to
#: read rather than a literal repeated in a test.
BATCH_ITEM_FAILURES_KEY: Final = "batchItemFailures"
BATCH_ITEM_FAILURES_RESPONSE_TYPE: Final = "ReportBatchItemFailures"

#: What the lineage bucket's policy requires of every write, and what makes a replay inert.
IF_NONE_MATCH: Final = "*"

JSON_CONTENT_TYPE: Final = "application/json"

#: Asks S3 to compute and store a SHA-256 over the bytes it received, which HeadObject then
#: returns under ``ChecksumSHA256``. It is what makes a lineage record verifiable by a
#: reader who was not there when it was written: the store attests the digest rather than
#: the writer asserting it.
#:
#: Sending it is the writer's job and there is no bucket setting that supplies it. Omitting
#: it costs nothing at write time, is invisible in every response, and produces an object
#: that reads exactly like an attested one until somebody asks for the checksum and finds
#: no field. That is how the first run through this path shipped: the five state machine
#: writes each set ChecksumAlgorithm in the ASL, this handler did not, and the events, the
#: attempt and the result came back from HeadObject carrying a VersionId and no digest.
CHECKSUM_ALGORITHM: Final = "SHA256"


class LifecycleEventError(ValueError):
    """The queue delivered something this handler cannot interpret as a Batch event."""


class ObjectStore(Protocol):
    """The one S3 call this handler makes, described so mypy has something to check.

    boto3 is absent at type-check time by design, so this is the seam. A test supplies its
    own implementation and gets the same code path the deployed function takes, rather than
    a branch that only exists for tests.
    """

    def put_object(self, **arguments: Any) -> Any: ...


def binding_key(run_id: str) -> str:
    return f"binding/{run_id}.json"


def event_key(run_id: str, event_id: str) -> str:
    return f"events/{run_id}/{event_id}.json"


def attempt_key(run_id: str, attempt_id: str) -> str:
    return f"attempt/{run_id}/{attempt_id}.json"


def result_key(run_id: str) -> str:
    return f"result/{run_id}.json"


def lineage_writes(projection: LifecycleProjection) -> tuple[tuple[str, ContractModel], ...]:
    """Which keys this projection puts, in the order they must be written.

    The event first, then the attempt, then the result. That ordering is what makes a
    partial write readable: a result whose ``attempt_id`` names an attempt with no record
    beside it would be an outcome attributed to an attempt nobody wrote down, where an
    attempt with no result yet is just a write that has not finished.

    A result whose ``attempt_id`` is ``None`` is the other case and is not a partial write.
    The run never got an attempt, so there is no attempt record to be missing, and the
    result is the only thing the store will ever hold about why it stopped.
    """
    writes: list[tuple[str, ContractModel]] = [
        (event_key(projection.event.run_id, projection.event.event_id), projection.event)
    ]
    if projection.attempt is not None:
        writes.append(
            (
                attempt_key(projection.attempt.run_id, projection.attempt.attempt_id),
                projection.attempt,
            )
        )
    if projection.result is not None:
        writes.append((result_key(projection.result.run_id), projection.result))
    return tuple(writes)


def _is_conflict(error: BaseException) -> bool:
    """Whether S3 refused this write because the object was already there.

    Read off the response rather than caught by class, because botocore cannot be imported
    here. The shape is botocore's and is stable: ``error.response["Error"]["Code"]``. An
    error that does not carry one is not a conflict, which is the direction to be wrong in
    -- an unrecognised failure is retried, and the retry meets the conditional write.
    """
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return False
    detail = response.get("Error")
    code = detail.get("Code") if isinstance(detail, Mapping) else None
    if isinstance(code, str) and code in CONFLICT_ERROR_CODES:
        return True
    metadata = response.get("ResponseMetadata")
    status = metadata.get("HTTPStatusCode") if isinstance(metadata, Mapping) else None
    return status == 412


def _put(store: ObjectStore, *, bucket: str, key: str, record: ContractModel) -> None:
    try:
        store.put_object(
            Bucket=bucket,
            Key=key,
            # The canonical bytes, not a re-encoding of them. What the store holds is then
            # byte-identical to what any reader would hash, so a record can be verified
            # without knowing how it was written.
            Body=canonical_json_bytes(record),
            ContentType=JSON_CONTENT_TYPE,
            ChecksumAlgorithm=CHECKSUM_ALGORITHM,
            IfNoneMatch=IF_NONE_MATCH,
        )
    except Exception as error:
        # Broad because botocore's exception classes are not importable here. It is
        # narrowed immediately: anything that is not S3's conflict is re-raised unchanged.
        if _is_conflict(error):
            return
        raise


def _default_object_store() -> ObjectStore:
    import boto3  # type: ignore[import-not-found]  # in the runtime, not in pyproject

    return cast(ObjectStore, boto3.client("s3"))


class _MetricsReader:
    """One ``s3://`` uri read as JSON, over the client the writes already use.

    An adapter rather than a second protocol on the client, because
    :class:`~edullm_platform.lifecycle_projection.MetricsReader` asks a question in the
    platform's vocabulary -- one uri, one document or nothing -- and a boto3 client answers in
    S3's. Keeping the translation here is what lets the projection stay a pure function that a
    test can drive without an SDK.

    EVERY FAILURE IS None AND THAT IS THE SAME SILENT DEGRADATION THE LISTING ABOVE ACCEPTS,
    for the same reason and with the same cost. An exception raised here dead-letters the
    delivery, which loses the event, the attempt and the result for a run that demonstrably
    happened -- so a missing key, a refused read and a document that is not JSON all come back
    as "this run scored nothing", and only the first of the three is true. The consequence is
    that ``eval_metrics`` stays null on every record until
    ``sbsandbox-intern-edullm-phase3-lifecycle-iam`` is applied, and nothing goes red to say
    so. ``infra/iam/lifecycle-lambda-role.yaml`` records the grant that closes it.

    What is deliberately NOT swallowed is a document that reads and does not parse. That
    raises out of ``read_olmo_eval_metrics`` in the projection, because bytes being present and
    unreadable is the one case where an empty field would be a lie this handler could have
    avoided telling.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def read_json(self, uri: str) -> Mapping[str, Any] | None:
        bucket, _, key = uri.removeprefix("s3://").partition("/")
        if not bucket or not key:
            return None
        try:
            body = self._client.get_object(Bucket=bucket, Key=key)["Body"].read()
            document = json.loads(body)
        except Exception:  # noqa: BLE001
            return None
        return cast(Mapping[str, Any], document) if isinstance(document, Mapping) else None


def _records(event: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    records = event.get("Records")
    if not isinstance(records, list):
        raise LifecycleEventError(
            "the event source mapping delivers a batch under 'Records'; this event has none"
        )
    return [record for record in records if isinstance(record, Mapping)]


def _envelope(record: Mapping[str, Any]) -> Mapping[str, Any]:
    body = record.get("body")
    if not isinstance(body, str):
        raise LifecycleEventError("an SQS record must carry its EventBridge envelope as a body")
    parsed = json.loads(body)
    if not isinstance(parsed, Mapping):
        raise LifecycleEventError("an SQS record's body must be a JSON object")
    return cast(Mapping[str, Any], parsed)


def handler(
    event: Mapping[str, Any],
    context: object = None,
    *,
    store: ObjectStore | None = None,
) -> dict[str, Any]:
    """Write every delivery in the batch, and name the ones that could not be written.

    ``context`` is unused: unlike the admission validator, this function needs no account
    id -- every ARN it would build is already in the detail Batch sent, and the bucket it
    writes to is deployment configuration rather than something derived.
    """
    del context

    lineage_bucket = os.environ.get(LINEAGE_BUCKET_VARIABLE) or DEFAULT_LINEAGE_BUCKET
    output_bucket = os.environ.get(OUTPUTS_BUCKET_VARIABLE) or OUTPUTS_BUCKET
    writer = store if store is not None else _default_object_store()
    # THE SAME CLIENT, WHICH IS WHY THIS IS A LINE AND NOT A PARAMETER. A boto3 S3 client
    # answers both calls, so listing a run's checkpoint prefix costs no second client and no
    # second argument to this handler.
    #
    # Asked for rather than assumed, because ``store`` is a seam a test fills. A store that
    # implements only the write answers None here, the projection records no checkpoints,
    # and that is the same honest emptiness a refused listing produces -- so a test that
    # cares about the lineage writes does not have to grow an S3 listing to keep passing.
    lister = writer if callable(getattr(writer, "list_objects_v2", None)) else None
    # The same client again, and asked for the same way. A store implementing only the write
    # answers None here and the projection records no metrics, which is what every caller
    # outside the deployed function gets and what the deployed function itself got until the
    # GetObject grant in infra/iam/lifecycle-lambda-role.yaml was applied.
    reader = (
        _MetricsReader(writer) if callable(getattr(writer, "get_object", None)) else None
    )

    records = _records(event)
    failures: list[tuple[str | None, Exception]] = []
    for record in records:
        try:
            projection = project_batch_event(
                _envelope(record),
                output_bucket=output_bucket,
                checkpoint_lister=cast(CheckpointLister | None, lister),
                metrics_reader=reader,
            )
            for key, written in lineage_writes(projection):
                _put(writer, bucket=lineage_bucket, key=key, record=written)
        except Exception as error:  # noqa: BLE001
            # Broad on purpose: one delivery that cannot be projected or written must not
            # stop the ones beside it, and every way it can fail is handled the same way --
            # kept rather than re-raised, so the decision below is taken over the whole
            # batch instead of by whichever record happened to fail first.
            identifier = record.get("messageId")
            failures.append((identifier if isinstance(identifier, str) else None, error))

    unnameable = any(identifier is None for identifier, _ in failures)
    if failures and (len(failures) == len(records) or unnameable):
        # Nothing survived, or something cannot be named in a partial response. Either way
        # the only way to have the delivery retried under an event source mapping that does
        # not report per-message verdicts is to fail the invocation, and the original error
        # is raised rather than a summary so the reason reaches CloudWatch intact.
        raise failures[0][1]
    return {
        BATCH_ITEM_FAILURES_KEY: [
            {"itemIdentifier": identifier} for identifier, _ in failures if identifier is not None
        ]
    }
