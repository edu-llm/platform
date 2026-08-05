"""One Batch job state change, read as the facts a message about it needs.

**No clock, no SDK import, and no I/O this module opens for itself.** Every value is a
function of the EventBridge envelope, of three reviewed configuration files, and of readers
the caller injects. Each reader defaults to ``None`` and answers an honest unknown without
one, which is what makes the whole module testable against a committed fixture with no AWS
account. It is the same seam ``lifecycle_projection.CheckpointLister`` established.

**This reads the event and never the substrate.** The substrate is a nightly aggregation,
and a notification that waits on a nightly file is not a notification. What it does read is
one record the platform sealed at submission, which is a different thing: it was true before
this event existed and cannot be later than it.

**What the event carries, measured rather than assumed.** Verified 2026-08-05 against a real
delivery archived in ``/aws/events/edullm-phase4-event-shape-probe``. The detail carries
``jobName``, which is the run id, ``jobQueue`` as a full ARN, ``status``, ``attempts`` with
each attempt's ``startedAt``, ``stoppedAt`` and container exit code,
``timeout.attemptDurationSeconds``, ``retryStrategy.attempts`` and ``container.environment``.

**What it does not carry is the submitter, and the run id is how that is answered.** The
detail carries a ``tags`` key whose entire content is a ``resourceArn``, so the
``edullm:submitter`` tag ``execution.py`` sets is not in it. The only person-shaped value in
the envelope is ``WANDB_USERNAME``, and ``config/organization.yaml`` records one for thirty
of thirty-five people.

``intent/{run_id}.json`` answers it for all thirty-five. ``IntentRecord.submitter`` is a
GitHub login and the roster carries a ``display_name`` against every login it holds. The key
is derived from the job name rather than searched for, so this costs one ``GetObject`` and no
listing, and the record is written by ``WriteIntent`` in the admission state machine before
``SubmitJob`` runs, so it cannot be racing this event.

**Reading it as JSON rather than as ``IntentRecord``, deliberately.** One field is wanted and
the contract carries a whole ``RunManifest``. Importing it would pull the submission contracts
into this function's zip, and would let a manifest field this message never reads fail
validation and dead-letter a message about a run that demonstrably happened.
``tests/test_notification_facts.py`` compares :data:`SUBMITTER_FIELD` against
``IntentRecord.model_fields`` so the spelling cannot drift, which is the same discipline
:data:`CANCELLATION_MARKERS` is held to below.

**Every failure of that read is the fallback and never an exception.** A refusal, an absent
record, a body that is not JSON: each falls back to ``WANDB_USERNAME`` and then to ``None``,
and the message says it could not name the person. Raising would dead-letter a delivery over
a name.

**What is deliberately not read here is ``attempt/{run_id}/``.** Those records carry the real
per-attempt windows and ``run_costs.py`` already prices runs from them, and this module cannot
use them, because the recorder writes them in answer to the same event that triggers this one.
Measured 2026-08-05 over forty terminal events: the attempt object lands 1 to 4 seconds after
the envelope's own ``time``, median 1 second. There is no moment at which this reader would
find the record for the attempt its event describes. The single-run spend does not need them,
because the event carries its own ``attempts``, and the fan-out spend is read from Batch in
:func:`_cells_spent` instead.

**A queue no execution target names is a fact, not a crash.** The rule matches sixteen queues
and ``config/execution-targets.yaml`` names fourteen. An unrecognised queue yields a profile
of ``None`` and a cost of ``None``, and the message says so. Raising would dead-letter the
delivery and say nothing at all about a run that happened.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Final, Literal, Protocol

from ..config import load_yaml
from ..contracts.bindings import normalize_github_login
from ..contracts.execution import ExecutionTargetCatalog
from ..contracts.identity import RUN_ID_REGEX
from ..contracts.inventory import OrganizationInventory
from ..contracts.workload import WorkloadCatalog
from ..lifecycle_projection import (
    EVENTBRIDGE_BATCH_DETAIL_TYPE,
    EVENTBRIDGE_BATCH_SOURCE,
    OUTPUT_PREFIX_VARIABLE,
    container_variable,
)

__all__ = [
    "CATALOG_FILENAME",
    "CENTS",
    "DEFAULT_LINEAGE_BUCKET",
    "LINEAGE_BUCKET_VARIABLE",
    "ORGANIZATION_FILENAME",
    "SUBMITTER_FIELD",
    "TARGETS_FILENAME",
    "TEAM_VARIABLE",
    "Catalogs",
    "IntentReader",
    "Outcome",
    "RunEndedFacts",
    "attempt_seconds",
    "intent_key",
    "queue_name_of",
    "read_run_ended",
    "submitter_of",
]

#: The three reviewed files this reader opens, named as literals because
#: ``tests/test_lambda_package_closure.py`` walks the packaged modules for exactly these
#: strings and holds the zip builder's list against what it finds.
ORGANIZATION_FILENAME: Final = "organization.yaml"
CATALOG_FILENAME: Final = "workload-catalog.yaml"
TARGETS_FILENAME: Final = "execution-targets.yaml"

#: Where the intent records are. Spelled the same way ``lifecycle_handler`` spells it, and
#: restated rather than imported for the reason that module restates ``MARKER_OBJECT``: the
#: zip builder measures an entry point's import closure, and dragging the recorder in to
#: share a string would put the whole projection into this function's package. A test
#: compares the two spellings.
LINEAGE_BUCKET_VARIABLE: Final = "EDULLM_LINEAGE_BUCKET"
DEFAULT_LINEAGE_BUCKET: Final = "sbsandbox-intern-edullm-lineage"

#: The one field of the intent record this reads. Named here rather than inlined so that
#: ``tests/test_notification_facts.py`` can hold it against ``IntentRecord.model_fields``;
#: the contract is not imported by this module, for the reason the docstring gives.
SUBMITTER_FIELD: Final = "submitter"

#: The container variable carrying the team, set by ``execution.py``.
TEAM_VARIABLE: Final = "EDULLM_TEAM"

#: W&B's own spellings, which ``execution.py`` sets because the client reads them unasked.
#: ``WANDB_RUN_GROUP`` carries the experiment, and it is set in the same branch as the
#: ``edullm:experiment`` tag, so the two never disagree.
WANDB_USERNAME_VARIABLE: Final = "WANDB_USERNAME"
WANDB_RUN_GROUP_VARIABLE: Final = "WANDB_RUN_GROUP"

#: Money is rendered to the cent everywhere in this repository, and two reports of one
#: figure at two precisions send somebody looking for a bug in the arithmetic.
CENTS: Final = Decimal("0.01")

SECONDS_AN_HOUR: Final = Decimal(3600)

#: The three endings a run has. Batch reports no cancelled status: a terminated job comes
#: back FAILED with whatever reason the caller of TerminateJob supplied, so cancellation is
#: detected by the marker this platform writes and by nothing else.
Outcome = Literal["succeeded", "failed", "cancelled"]

_TERMINAL: Final[Mapping[str, Outcome]] = {"SUCCEEDED": "succeeded", "FAILED": "failed"}

#: How this platform's cancellation path words the reason. Restated rather than imported to
#: keep this module's meaning readable on its own; a test compares it against
#: ``lifecycle_projection.CANCELLATION_REASON_MARKERS`` so the two cannot drift.
CANCELLATION_MARKERS: Final = ("edullm:cancelled",)


@dataclass(frozen=True)
class Catalogs:
    """The three reviewed files, loaded once and handed to every read.

    Loaded together because a reader holding two of the three can name a profile it cannot
    price, and a message that names a machine and no money is the contentless message the
    whole design was written against.
    """

    inventory: OrganizationInventory
    catalog: WorkloadCatalog
    targets: ExecutionTargetCatalog

    @classmethod
    def load(cls, directory: Path) -> Catalogs:
        return cls(
            inventory=load_yaml(directory / ORGANIZATION_FILENAME, OrganizationInventory),
            catalog=load_yaml(directory / CATALOG_FILENAME, WorkloadCatalog),
            targets=load_yaml(directory / TARGETS_FILENAME, ExecutionTargetCatalog),
        )


@dataclass(frozen=True)
class RunEndedFacts:
    """Everything a message about one ended run may say, and nothing worded yet.

    Frozen, because a renderer that can edit its inputs produces output that depends on
    which message was built first.

    Every optional field is optional for a reason the message has to carry rather than hide.
    ``person`` is None where neither the intent record nor ``WANDB_USERNAME`` named anybody,
    which is a reader that could not look rather than a submitter nobody recorded.
    ``experiment`` is None for a run admitted before the field existed. ``compute_profile``,
    ``hourly_rate_usd``, ``spent_usd`` and ``authorised_usd`` are None together whenever the
    queue is one no execution target names, and from Task 5 ``spent_usd`` is also None on its
    own, for an array parent whose cells were not read.
    """

    run_id: str
    outcome: Outcome
    person: str | None
    team: str | None
    experiment: str | None
    queue_name: str | None
    compute_profile: str | None
    hourly_rate_usd: Decimal | None
    seconds_spent: int
    spent_usd: Decimal | None
    authorised_usd: Decimal | None
    exit_code: int | None
    output_prefix: str | None
    cells_total: int | None
    cells_failed: int | None
    cells_succeeded: int | None


def queue_name_of(detail: Mapping[str, Any]) -> str | None:
    """The last segment of the job queue ARN, which is the key into the targets file."""
    arn = detail.get("jobQueue")
    if not isinstance(arn, str) or "/" not in arn:
        return None
    return arn.rsplit("/", 1)[1] or None


def _profile_named_by(queue: str | None, targets: ExecutionTargetCatalog) -> str | None:
    if queue is None:
        return None
    for target in targets.targets:
        if target.job_queue == queue:
            return target.compute_profile
    return None


def _rate_for(profile: str | None, catalog: WorkloadCatalog) -> Decimal | None:
    if profile is None:
        return None
    for entry in catalog.compute_profiles:
        if entry.name == profile:
            return entry.hourly_rate_usd
    return None


class IntentReader(Protocol):
    """The one S3 read this module makes, described so mypy has something to check.

    boto3 is absent at type-check time by design, so this is the seam, the same discipline
    ``lifecycle_handler.ObjectStore`` uses for the write. A test supplies its own and gets the
    same code path the deployed function takes, rather than a branch that only exists for
    tests.
    """

    def get_object(self, **arguments: Any) -> Any: ...


def intent_key(run_id: str) -> str:
    """Where admission recorded what this run asked for.

    Derived from the run id rather than searched for, which is why this costs one
    ``GetObject`` and the role needs no listing of the lineage bucket. Spelled the same way
    ``admission_handler`` spells it when it answers the key back to the state machine, and a
    test compares the two.
    """
    return f"intent/{run_id}.json"


def submitter_of(reader: IntentReader | None, *, run_id: str, bucket: str) -> str | None:
    """The GitHub login that submitted this run, or None because it could not be read.

    NEVER RAISES, AND THAT IS THE WHOLE OF THE ERROR HANDLING. A refused read, an absent
    record, a body that is not JSON, a record with no submitter: every one of them is None,
    the caller falls back to ``WANDB_USERNAME``, and the message says it could not name the
    person. An exception here would dead-letter a delivery over a name, on a path whose
    standing rule is that a message nobody got is never worth a job nobody ran.

    Broad on purpose, because botocore's exception classes cannot be imported here and the
    set of ways a read can fail is open. Narrowed by what it does rather than by what it
    catches, exactly as ``lifecycle_projection.checkpoints_under`` is.
    """
    if reader is None:
        return None
    try:
        answer = reader.get_object(Bucket=bucket, Key=intent_key(run_id))
        record = json.loads(answer["Body"].read())
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(record, Mapping):
        return None
    submitter = record.get(SUBMITTER_FIELD)
    return submitter if isinstance(submitter, str) and submitter else None


def _person_named_by_login(
    github_login: str | None, inventory: OrganizationInventory
) -> str | None:
    """The display name behind a GitHub login, or None because the roster does not hold it.

    THE ONE LOOKUP THAT COVERS EVERYBODY. All thirty-five roster members carry a
    ``github_login`` and a ``display_name``; thirty carry a ``wandb_username``. So this
    answers for the five the fallback below cannot, and answers the other thirty from what
    admission sealed rather than by reversing an attribution.

    Compared on the normalised login, because GitHub logins are case insensitive and the
    contract already normalises them to refuse duplicates. A record spelling a login one way
    and the roster the other is one the exact comparison would silently fail to name.
    """
    if github_login is None:
        return None
    wanted = normalize_github_login(github_login)
    for member in inventory.members:
        if member.normalized_github_login == wanted:
            return member.display_name or member.github_login
    return None


def _person_named_by_wandb(
    wandb_username: str | None, inventory: OrganizationInventory
) -> str | None:
    """The display name behind a W&B account, or None because nobody recorded one.

    The reverse of ``OrganizationInventory.wandb_username_for``. The contract already
    refuses two members claiming one W&B account, so this cannot answer two names.

    KEPT RATHER THAN REPLACED BY THE LOOKUP ABOVE, and the reason is the wording loop. A
    reader with no client still names thirty of thirty-five from the envelope alone, so
    ``tools/render_notification.py`` prints a real name with no credential and the deployed
    path is not the only one that produces one.
    """
    if wandb_username is None:
        return None
    for member in inventory.members:
        if member.wandb_username == wandb_username:
            return member.display_name or member.github_login
    return None


def attempt_seconds(detail: Mapping[str, Any]) -> int:
    """How long the container actually ran, summed over every attempt that has an end.

    Summed rather than taken from the last attempt. A job that was reclaimed at hour eleven
    and retried spent both windows, and a figure naming only the second understates what was
    burned by the whole of the first.

    Attempts with no ``stoppedAt`` contribute nothing. A window nobody measured is not a
    window of zero, and it is the direction to be wrong in for a figure somebody reads as
    money.
    """
    attempts = detail.get("attempts")
    if not isinstance(attempts, list):
        return 0
    total = 0
    for attempt in attempts:
        if not isinstance(attempt, Mapping):
            continue
        started, stopped = attempt.get("startedAt"), attempt.get("stoppedAt")
        if not isinstance(started, int) or not isinstance(stopped, int):
            continue
        if isinstance(started, bool) or isinstance(stopped, bool) or stopped < started:
            continue
        total += (stopped - started) // 1000
    return total


def _money(rate: Decimal | None, seconds: int) -> Decimal | None:
    if rate is None:
        return None
    return (rate * Decimal(seconds) / SECONDS_AN_HOUR).quantize(CENTS, rounding=ROUND_HALF_UP)


def _authorised(
    detail: Mapping[str, Any], rate: Decimal | None, cells: int | None
) -> Decimal | None:
    """The ceiling the approval bought, which is what Batch was told to enforce.

    Every factor is read off the event rather than off the manifest, because the event is
    what the job was actually submitted with. A run whose bound was edited between the
    approval and the submission would be described by the manifest and priced by this.
    """
    if rate is None:
        return None
    timeout = detail.get("timeout")
    seconds = timeout.get("attemptDurationSeconds") if isinstance(timeout, Mapping) else None
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
        return None
    strategy = detail.get("retryStrategy")
    attempts = strategy.get("attempts") if isinstance(strategy, Mapping) else None
    tries = attempts if isinstance(attempts, int) and not isinstance(attempts, bool) else 1
    total = rate * Decimal(seconds) / SECONDS_AN_HOUR * Decimal(max(tries, 1)) * Decimal(cells or 1)
    return total.quantize(CENTS, rounding=ROUND_HALF_UP)


def _outcome(detail: Mapping[str, Any]) -> Outcome | None:
    status = detail.get("status")
    if not isinstance(status, str):
        return None
    ending = _TERMINAL.get(status)
    if ending is None:
        return None
    if ending == "failed" and _was_cancelled(detail):
        return "cancelled"
    return ending


def _was_cancelled(detail: Mapping[str, Any]) -> bool:
    reasons = [detail.get("statusReason")]
    attempts = detail.get("attempts")
    if isinstance(attempts, list):
        reasons.extend(
            attempt.get("statusReason") for attempt in attempts if isinstance(attempt, Mapping)
        )
    return any(
        isinstance(reason, str) and reason.lstrip().startswith(marker)
        for reason in reasons
        for marker in CANCELLATION_MARKERS
    )


def _exit_code(detail: Mapping[str, Any]) -> int | None:
    """What the last attempt's container returned, or None because it never returned.

    Absent is a fact and stays None. A host reclaimed mid-run leaves no exit code because
    there was no exit, and defaulting to zero would record that as a clean finish.
    """
    attempts = detail.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        return None
    last = attempts[-1]
    container = last.get("container") if isinstance(last, Mapping) else None
    code = container.get("exitCode") if isinstance(container, Mapping) else None
    if isinstance(code, bool) or not isinstance(code, int):
        return None
    return code


def _person(
    detail: Mapping[str, Any],
    *,
    run_id: str,
    catalogs: Catalogs,
    intent_reader: IntentReader | None,
    lineage_bucket: str,
) -> str | None:
    """Who submitted this run, from the record first and the envelope second.

    THE RECORD WINS AND THE ORDER IS THE POINT. The intent record is what admission sealed
    at submission and it names all thirty-five people. ``WANDB_USERNAME`` is an attribution
    the submission path set, is absent for five of them, and can only be reversed rather than
    read. Where both answer they agree; where they disagree the sealed one is right.

    None is the third answer and it is said rather than filled in. Neither source naming
    somebody is a fact about this message, and substituting the team or the run id would put
    a wrong name on a message about somebody's money.
    """
    named = _person_named_by_login(
        submitter_of(intent_reader, run_id=run_id, bucket=lineage_bucket), catalogs.inventory
    )
    if named is not None:
        return named
    return _person_named_by_wandb(
        container_variable(detail, WANDB_USERNAME_VARIABLE), catalogs.inventory
    )


def read_run_ended(
    envelope: Mapping[str, Any],
    *,
    catalogs: Catalogs,
    intent_reader: IntentReader | None = None,
    lineage_bucket: str | None = None,
) -> RunEndedFacts | None:
    """The facts one ended run's message needs, or None because no message is owed.

    None rather than an exception for every uninteresting delivery. A non-terminal state, a
    foreign source, a job name that is not a run id: each is a delivery this reader has
    nothing to say about, and raising on any of them would send it round the retry loop and
    into the dead-letter queue where a person is meant to find real failures.

    ``intent_reader`` defaults to ``None``, and without it the person is whatever
    ``WANDB_USERNAME`` reverses to. Every test in this repository and
    ``tools/render_notification.py`` take that path, which is what keeps the wording loop
    free of a credential.
    """
    if envelope.get("source") != EVENTBRIDGE_BATCH_SOURCE:
        return None
    if envelope.get("detail-type") != EVENTBRIDGE_BATCH_DETAIL_TYPE:
        return None
    detail = envelope.get("detail")
    if not isinstance(detail, Mapping):
        return None
    run_id = detail.get("jobName")
    if not isinstance(run_id, str) or RUN_ID_REGEX.fullmatch(run_id) is None:
        return None
    outcome = _outcome(detail)
    if outcome is None:
        return None

    queue = queue_name_of(detail)
    profile = _profile_named_by(queue, catalogs.targets)
    rate = _rate_for(profile, catalogs.catalog)
    seconds = attempt_seconds(detail)

    return RunEndedFacts(
        run_id=run_id,
        outcome=outcome,
        person=_person(
            detail,
            run_id=run_id,
            catalogs=catalogs,
            intent_reader=intent_reader,
            lineage_bucket=lineage_bucket or DEFAULT_LINEAGE_BUCKET,
        ),
        team=container_variable(detail, TEAM_VARIABLE),
        experiment=container_variable(detail, WANDB_RUN_GROUP_VARIABLE),
        queue_name=queue,
        compute_profile=profile,
        hourly_rate_usd=rate,
        seconds_spent=seconds,
        spent_usd=_money(rate, seconds),
        authorised_usd=_authorised(detail, rate, None),
        exit_code=_exit_code(detail),
        output_prefix=container_variable(detail, OUTPUT_PREFIX_VARIABLE),
        cells_total=None,
        cells_failed=None,
        cells_succeeded=None,
    )
