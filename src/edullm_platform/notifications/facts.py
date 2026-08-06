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
from ..contracts.results import CheckpointListingOutcome
from ..contracts.workload import (
    ComputeProfile,
    WorkloadCatalog,
    compute_maximum_compute_cost_usd,
)
from ..lifecycle_projection import (
    CHECKPOINT_DIR_VARIABLE,
    EVENTBRIDGE_BATCH_DETAIL_TYPE,
    EVENTBRIDGE_BATCH_SOURCE,
    OUTPUT_PREFIX_VARIABLE,
    CheckpointLister,
    checkpoints_under,
    container_variable,
)

__all__ = [
    "CATALOG_FILENAME",
    "CENTS",
    "DEFAULT_LINEAGE_BUCKET",
    "LINEAGE_BUCKET_VARIABLE",
    "MAXIMUM_CELL_PAGES",
    "ORGANIZATION_FILENAME",
    "SUBMITTER_FIELD",
    "TARGETS_FILENAME",
    "TEAM_VARIABLE",
    "TERMINAL_CELL_STATUSES",
    "Catalogs",
    "CellLister",
    "CheckpointState",
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

#: Whether a run's checkpoint prefix was read, and what was under it. Three values rather
#: than a boolean, because `nobody looked` and `nothing was there` are different sentences
#: and only one of them is about the run.
CheckpointState = Literal["written", "none", "unknown"]

_TERMINAL: Final[Mapping[str, Outcome]] = {"SUCCEEDED": "succeeded", "FAILED": "failed"}

#: How this platform's cancellation path words the reason. Restated rather than imported to
#: keep this module's meaning readable on its own; a test compares it against
#: ``lifecycle_projection.CANCELLATION_REASON_MARKERS`` so the two cannot drift.
CANCELLATION_MARKERS: Final = ("edullm:cancelled",)

#: The two statuses an array's cells are in once their parent is terminal. Both are asked
#: for because ``ListJobs`` answers about ``RUNNING`` when no status is given, which is the
#: one status a finished sweep has none of. Asking for the other five as well would be five
#: more requests for five empty answers.
TERMINAL_CELL_STATUSES: Final = ("SUCCEEDED", "FAILED")

#: How many pages of cells this will follow per status before giving up. A ceiling rather
#: than an unbounded loop, for the reason ``lifecycle_projection.MAXIMUM_LISTING_PAGES``
#: carries one: this runs inside an event handler with a timeout, and a store that kept
#: handing back a token would spend the whole of it. Reaching it abandons the read rather
#: than reporting what was seen so far, so the message says the spend was not read instead
#: of naming a figure that is missing an arbitrary set of cells.
MAXIMUM_CELL_PAGES: Final = 20


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
    #: How many of an array's cells the Batch listing accounted for, and None where no
    #: listing happened. Held apart from ``cells_total`` because they answer different
    #: questions: the total is what the event says was submitted, and this is what was read.
    #: Equal is the normal case and the message says a plain figure; short means the spend is
    #: a floor and the message says so; None means nobody looked and the message says that
    #: instead of showing a ceiling in the slot a measurement belongs in.
    cells_measured: int | None
    #: Which cells failed, taken off the child job ids in the same answer that carried the
    #: windows. None where the listing did not happen, which is a different sentence from an
    #: empty tuple: empty means every cell finished.
    failed_cell_indexes: tuple[int, ...] | None
    #: Whether anything survived under this run's checkpoint prefix. ``unknown`` where no
    #: lister was supplied and where the listing did not work, which is the direction to be
    #: wrong in: telling somebody nothing was saved when the bytes may be in S3 is the one
    #: wrong answer a failure message must not give.
    checkpoint_state: CheckpointState


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


def _priced_by(profile: str | None, catalog: WorkloadCatalog) -> ComputeProfile | None:
    """The whole catalog entry, because the money needs two fields off it and not one.

    This returned a bare rate until 2026-08-06 and the node count was dropped on the floor.
    Every profile in ``config/workload-catalog.yaml`` is one machine today, so the two
    products agreed with the rest of the platform by arithmetic accident.
    """
    if profile is None:
        return None
    for entry in catalog.compute_profiles:
        if entry.name == profile:
            return entry
    return None


class IntentReader(Protocol):
    """The one S3 read this module makes, described so mypy has something to check.

    boto3 is absent at type-check time by design, so this is the seam, the same discipline
    ``lifecycle_handler.ObjectStore`` uses for the write. A test supplies its own and gets the
    same code path the deployed function takes, rather than a branch that only exists for
    tests.
    """

    def get_object(self, **arguments: Any) -> Any: ...


class CellLister(Protocol):
    """The one Batch call this module makes, described so mypy has something to check.

    Reading the cells rather than the ``attempt/`` records under the same run id, and the
    reason is a race rather than a preference. The recorder writes those records in answer
    to the same events that drive this function, and the parent's terminal event is the last
    to arrive: measured 2026-08-05, the final cell's record lands in the same second as the
    parent's event or a second after it. Batch has no such race, because it moves an array
    parent to a terminal status only once every child is already terminal.
    """

    def list_jobs(self, **arguments: Any) -> Any: ...


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


def _money(priced: ComputeProfile | None, seconds: int) -> Decimal | None:
    """What a window actually cost, priced the way ``run_costs.py`` prices one.

    ``rate x nodes x duration``, which is that module's own sentence for it. A two-node
    profile burns two machines for the whole window and a per-machine rate is half of what
    was spent.
    """
    if priced is None:
        return None
    product = priced.hourly_rate_usd * priced.nodes * Decimal(seconds) / SECONDS_AN_HOUR
    return product.quantize(CENTS, rounding=ROUND_HALF_UP)


def _authorised(
    detail: Mapping[str, Any], priced: ComputeProfile | None, cells: int | None
) -> Decimal | None:
    """The ceiling the approval bought, which is what Batch was told to enforce.

    Every factor is read off the event rather than off the manifest, because the event is
    what the job was actually submitted with. A run whose bound was edited between the
    approval and the submission would be described by the manifest and priced by this.

    THE PRODUCT ITSELF IS NOT WRITTEN HERE. ``compute_maximum_compute_cost_usd`` is what
    ``CostInputs`` validates every decision record against, so it is what a lead reconciling
    a message against the record it names will be comparing with. A second spelling of the
    same five factors is a second thing to keep right, and the one that goes stale is the one
    with no record beside it to disagree with. It carried four of the five until 2026-08-06.

    It raises rather than returning on a product too wide to represent, and a notifier that
    raises is a message nobody gets, so an unrepresentable ceiling is reported as no ceiling.
    """
    if priced is None:
        return None
    timeout = detail.get("timeout")
    seconds = timeout.get("attemptDurationSeconds") if isinstance(timeout, Mapping) else None
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
        return None
    strategy = detail.get("retryStrategy")
    attempts = strategy.get("attempts") if isinstance(strategy, Mapping) else None
    tries = attempts if isinstance(attempts, int) and not isinstance(attempts, bool) else 1
    try:
        return compute_maximum_compute_cost_usd(
            priced.hourly_rate_usd,
            priced.nodes,
            Decimal(seconds) / SECONDS_AN_HOUR,
            max(tries, 1),
            max(cells or 1, 1),
        )
    except (ArithmeticError, ValueError):
        return None


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


def _cells(detail: Mapping[str, Any]) -> tuple[int, int, int] | None:
    """Size, succeeded and failed for an array parent, or None because this is not one.

    Batch distinguishes the two by which key it fills. A parent carries ``size`` and a
    summary over its children; a child carries ``index`` and an empty summary. Reading the
    presence of ``size`` rather than the absence of ``index`` is the direction that fails
    safe: an array shape Batch adds later without an index would be treated as a child and
    say nothing, rather than as a parent and say something wrong.
    """
    properties = detail.get("arrayProperties")
    if not isinstance(properties, Mapping):
        return None
    size = properties.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        return None
    summary = properties.get("statusSummary")
    summary = summary if isinstance(summary, Mapping) else {}
    succeeded = summary.get("SUCCEEDED")
    failed = summary.get("FAILED")
    return (
        size,
        succeeded if isinstance(succeeded, int) and not isinstance(succeeded, bool) else 0,
        failed if isinstance(failed, int) and not isinstance(failed, bool) else 0,
    )


def _is_array_child(detail: Mapping[str, Any]) -> bool:
    properties = detail.get("arrayProperties")
    if not isinstance(properties, Mapping):
        return False
    index = properties.get("index")
    return isinstance(index, int) and not isinstance(index, bool)


def _cell_index(job_id: object) -> int | None:
    """The cell's index, which is the suffix of its Batch job id.

    ``<parent>:13`` is how Batch names the fourteenth child, and it is the only place the
    index appears in a ``ListJobs`` summary. None for anything that does not parse, so a
    shape Batch changes later costs the indexes and not the spend.
    """
    if not isinstance(job_id, str) or ":" not in job_id:
        return None
    suffix = job_id.rsplit(":", 1)[1]
    return int(suffix) if suffix.isdigit() else None


def _cells_spent(
    lister: CellLister | None, *, array_job_id: object
) -> tuple[int, int, tuple[int, ...]] | None:
    """What an array's cells actually ran for, how many were read, and which ones failed.

    None where no listing happened, and None rather than a zero for every way it can fail.
    A sweep whose cells nobody read is not a sweep that cost nothing, and ``$0.00 spent`` is
    the cheapest-looking wrong answer in the field's range.

    Never raises, for the reason ``submitter_of`` never does. The cell counts are in the
    event and are worth posting on their own, so a refused listing costs the message a figure
    and never the message.
    """
    if lister is None or not isinstance(array_job_id, str) or not array_job_id:
        return None
    seconds = 0
    measured = 0
    failed: list[int] = []
    try:
        for status in TERMINAL_CELL_STATUSES:
            arguments: dict[str, Any] = {"arrayJobId": array_job_id, "jobStatus": status}
            for _page in range(MAXIMUM_CELL_PAGES):
                answer = lister.list_jobs(**arguments)
                for cell in answer.get("jobSummaryList") or []:
                    if not isinstance(cell, Mapping):
                        continue
                    if status == "FAILED":
                        index = _cell_index(cell.get("jobId"))
                        if index is not None:
                            failed.append(index)
                    started, stopped = cell.get("startedAt"), cell.get("stoppedAt")
                    if not isinstance(started, int) or not isinstance(stopped, int):
                        continue
                    if isinstance(started, bool) or isinstance(stopped, bool):
                        continue
                    if stopped < started:
                        continue
                    # COUNTED HERE AND NOT WHERE THE CELL WAS SEEN, so that `measured` means
                    # priced rather than returned. A cell whose window Batch did not report
                    # contributes nothing to the sum, and counting it would make the total
                    # look complete while it was short by that cell's hours.
                    measured += 1
                    seconds += (stopped - started) // 1000
                token = answer.get("nextToken")
                if not isinstance(token, str) or not token:
                    break
                arguments["nextToken"] = token
            else:
                # The page ceiling, reached rather than exhausted. Abandoning the whole read
                # is deliberate: a partial sum rendered as a spend is a figure missing an
                # arbitrary set of cells and reads exactly like a complete one.
                return None
    except Exception:  # noqa: BLE001
        # Broad because botocore's exception classes cannot be imported here and the set of
        # ways a listing can fail is open. Narrowed by what it does rather than by what it
        # catches, exactly as `checkpoints_under` is.
        return None
    return seconds, measured, tuple(sorted(failed))


def _checkpoint_state(
    detail: Mapping[str, Any], *, lister: CheckpointLister | None
) -> CheckpointState:
    """What is under this run's checkpoint prefix, or that nobody looked.

    Every failure is `unknown` and never `none`. `no checkpoint written` is a claim about the
    run; `unknown` is a claim about the reader. Reporting the first when the second is true
    tells somebody their eleven hours are gone when the bytes may be sitting in S3, and that
    is the one wrong answer this message must not give.

    Never raises, for the reason `checkpoints_under` never does: this runs while a message is
    being built, and an exception here loses the whole message for a run that demonstrably
    happened.
    """
    if lister is None:
        return "unknown"
    prefix = container_variable(detail, CHECKPOINT_DIR_VARIABLE)
    if prefix is None:
        return "unknown"
    manifests, survey = checkpoints_under(lister, prefix=prefix)
    if survey.outcome is not CheckpointListingOutcome.LISTED:
        # The other five members each name a way the listing did not happen: nothing to list
        # with, no prefix declared, a prefix in somebody else's bucket, a refusal, or more
        # pages than the reader will follow. None of them is evidence that nothing was
        # written, and only LISTED is the statement that the prefix was read and was bare.
        return "unknown"
    return "written" if manifests or survey.objects_seen > 0 else "none"


def read_run_ended(
    envelope: Mapping[str, Any],
    *,
    catalogs: Catalogs,
    intent_reader: IntentReader | None = None,
    lineage_bucket: str | None = None,
    cell_lister: CellLister | None = None,
    checkpoint_lister: CheckpointLister | None = None,
) -> RunEndedFacts | None:
    """The facts one ended run's message needs, or None because no message is owed.

    None rather than an exception for every uninteresting delivery. A non-terminal state, a
    foreign source, a job name that is not a run id: each is a delivery this reader has
    nothing to say about, and raising on any of them would send it round the retry loop and
    into the dead-letter queue where a person is meant to find real failures.

    ``intent_reader`` defaults to ``None``, and without it the person is whatever
    ``WANDB_USERNAME`` reverses to. ``cell_lister`` defaults to ``None`` as well, and without
    it an array parent's spend is unknown rather than zero, because the parent event carries
    no attempts and a sweep nobody read is not a sweep that cost nothing. Every test in this
    repository and ``tools/render_notification.py`` take both defaults, which is what keeps
    the wording loop free of a credential.
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
    if _is_array_child(detail):
        # ONE MESSAGE PER ARRAY, AT COMPLETION, AND NEVER ONE PER CELL. A twenty-checkpoint
        # sweep is one event with one result, and the result somebody acts on is how many
        # cells failed. The parent's own terminal event arrives after every child's, so
        # suppressing children here loses nothing and costs latency: the sweep says nothing
        # until its last cell lands.
        return None
    cells = _cells(detail)
    # ASKED ONLY FOR AN ARRAY PARENT, WHICH IS WHY THIS SITS BEHIND THE CELL CHECK. A run
    # that is not an array carries its own attempts in the event, so its spend is already
    # exact and a Batch call would buy nothing on every one of the nine messages a day.
    spent = None if cells is None else _cells_spent(cell_lister, array_job_id=detail.get("jobId"))

    queue = queue_name_of(detail)
    profile = _profile_named_by(queue, catalogs.targets)
    priced = _priced_by(profile, catalogs.catalog)
    seconds = attempt_seconds(detail)

    # WHICH SECONDS THE MONEY IS COMPUTED FROM, WHICH IS THE WHOLE OF THIS TASK. A single
    # run's window is in its own event. An array parent's is not in its event at all, so it
    # comes off the cells or it does not come at all: `spent_usd` is None where they were not
    # read, and a message that cannot say what a sweep cost says so rather than showing the
    # ceiling in the slot a measurement belongs in.
    if cells is not None:
        seconds = 0 if spent is None else spent[0]

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
        hourly_rate_usd=None if priced is None else priced.hourly_rate_usd,
        seconds_spent=seconds,
        spent_usd=None if (cells is not None and spent is None) else _money(priced, seconds),
        authorised_usd=_authorised(detail, priced, None if cells is None else cells[0]),
        exit_code=_exit_code(detail),
        output_prefix=container_variable(detail, OUTPUT_PREFIX_VARIABLE),
        cells_total=None if cells is None else cells[0],
        cells_failed=None if cells is None else cells[2],
        cells_succeeded=None if cells is None else cells[1],
        cells_measured=None if spent is None else spent[1],
        failed_cell_indexes=None if spent is None else spent[2],
        checkpoint_state=_checkpoint_state(detail, lister=checkpoint_lister),
    )
