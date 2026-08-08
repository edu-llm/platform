"""When the block ends, what is still only on a disk that goes with the machine, and the clock.

**THE DEADLINE IS NOT THE END OF THE WINDOW AND EVERY NUMBER HERE IS RELATIVE TO THE OTHER
ONE.** A capacity block is sold as a window with an end time, and AWS begins terminating the
instances half an hour before that time -- so on a block ending 11:30 UTC the machines start
going away at 11:00, and a countdown that reports "thirty minutes left" at 11:00 is reporting
against a moment nobody gets to use. :data:`RECLAIM_MARGIN_MINUTES` is that half hour and
:func:`countdown` subtracts it once, at the top, so that no caller anywhere is left holding
the wrong end of it.

**INSTANCE STORE IS THE THING THAT IS ACTUALLY LOST.** Checkpoints written by an OLMo-core run
already go straight to S3 -- ``EDULLM_CHECKPOINT_DIR`` is an ``s3://`` URI and the trainer
writes there rather than locally -- and the log sync carries the training log up every minute.
What is on the machine and nowhere else is everything around those: the resolved
``.edullm/run.yaml``, the commit that was cloned, whatever a researcher wrote beside their
code, and any run whose command did not follow the convention at all. None of that is
recoverable after the instance goes, because ``/scratch`` is a RAID0 over local NVMe and local
NVMe does not survive a stop, let alone a terminate.

**THE FLUSH RUNS ON THE NODES AND THE WARNING RUNS IN GITHUB, AND THAT SPLIT IS THE DESIGN.**
A scheduled GitHub Actions workflow is not delivered on time -- minutes of delay under load is
ordinary and there is no commitment to any bound -- and the deadline here is enforced by AWS
against a wall clock. So the thing that must happen at a particular minute happens on a systemd
timer laid down by ``infra/block-node-bootstrap.sh``, on the machine holding the data, needing
nothing from GitHub, from Systems Manager or from anybody being awake. What GitHub is for is the
half that tolerates being late and cannot be done on a node at all: telling roughly fifteen
people who hold no AWS credential which machines still have unflushed work on them and who is
holding each one. ``tools/block_drain.py`` is the same reading from a laptop, for the case where
GitHub is the thing that is broken.

**WHAT THIS MODULE IS FOR RATHER THAN THE SHELL.** The node-side drain is shell because it has
to run where the data is, and shell is where an arithmetic mistake is least visible. Everything
a mistake would be expensive in -- how much time is left, which warning horizon that crosses,
whether the object count in S3 accounts for the files that were on disk, and whether a
checkpoint directory is a checkpoint or the wreckage of one -- is here, where
``tests/test_block_drain.py`` can reach it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

__all__ = [
    "CHECKPOINT_PREFIX",
    "DRAIN_FROM_MINUTES",
    "DRAIN_TICK_MINUTES",
    "RECLAIM_MARGIN_MINUTES",
    "SCRATCH_PREFIX",
    "WARNING_HORIZONS_MINUTES",
    "Checkpoints",
    "Countdown",
    "DrainReading",
    "RunFlush",
    "countdown",
    "drain_markdown",
    "drain_rows",
    "horizon_for",
    "outstanding",
    "parse_drain_reading",
    "read_checkpoints",
    "remaining_as",
    "unflushed_instances",
]

#: How long before the end of a purchased window AWS starts taking the instances back. It is
#: AWS's number rather than a policy of this repository, and it is here so that the launch
#: workflow can write it into each node's settings from one place instead of each side of the
#: lane carrying its own copy of a figure that only AWS can change.
RECLAIM_MARGIN_MINUTES: Final = 30

#: How long before the reclaim the nodes stop being quiet and flush on every tick.
#:
#: Two and a half hours rather than something tighter, because the cost of being early is a
#: handful of incremental ``s3 sync`` calls over a prefix that has not changed, and the cost of
#: being late is that the first flush of a large scratch directory is also the last one and it
#: does not finish. Repeating it means the flush that runs at the deadline has almost nothing
#: left to copy.
DRAIN_FROM_MINUTES: Final = 150

#: How often the node's timer fires. Five minutes bounds how stale the report a person reads
#: can be, and bounds how much of the last flush is still in flight when AWS arrives.
DRAIN_TICK_MINUTES: Final = 5

#: The horizons a person is warned at, in minutes before the reclaim rather than before the end
#: of the window. Descending, which is the order :func:`horizon_for` reads them in.
WARNING_HORIZONS_MINUTES: Final = (120, 60, 30, 15)

#: Where the drain puts what it lifted off the machine, under a run's own prefix.
#:
#: A subtree of its own rather than merged into the run prefix, and the reason is that the
#: verification below is a count. Merged, the objects the trainer wrote directly to S3 and the
#: objects the drain copied off the disk would share a denominator, and "there are at least as
#: many objects as there were files" would stop being a statement about whether the sync worked.
SCRATCH_PREFIX: Final = "scratch"

#: Where an OLMo-core run's checkpoints land, which is the one part of a run's output that does
#: not come off the disk because it never went to the disk.
CHECKPOINT_PREFIX: Final = "checkpoints"

#: ``Checkpointer.CHECKPOINT_DIR`` in OLMo-core, as it appears in an object key.
_STEP_DIRECTORY: Final = re.compile(r"^step(\d+)$")

#: What ``Checkpointer.dir_is_checkpoint`` looks for, transcribed. A directory holding only
#: ``.metadata`` is a weights-only checkpoint and complete; anything else has to carry all three
#: of the others, and ``.metadata.json`` is written last, which is what makes its absence beside
#: the rest the signature of a write that was interrupted.
_WEIGHTS_ONLY_MARKER: Final = ".metadata"
_FULL_CHECKPOINT_MARKERS: Final = (
    "train/rank0.pt",
    "model_and_optim/.metadata",
    ".metadata.json",
)


@dataclass(frozen=True)
class Countdown:
    """How long is left, measured against the moment AWS starts taking the machines back.

    ``remaining`` is signed. A drain that runs after the reclaim has begun is a real thing --
    the tick that fired at 10:58 is still copying at 11:01 -- and reporting that as zero would
    tell somebody reading the summary that they are at the deadline rather than past it.
    """

    ends_at: datetime
    reclaim_at: datetime
    remaining: timedelta

    @property
    def past_reclaim(self) -> bool:
        return self.remaining <= timedelta(0)

    @property
    def horizon(self) -> int | None:
        return horizon_for(self.remaining)

    def describe(self) -> str:
        if self.past_reclaim:
            return (
                f"AWS began reclaiming this fleet {remaining_as(-self.remaining)} ago; "
                "anything still on a disk is already gone or going"
            )
        return f"{remaining_as(self.remaining)} until AWS starts terminating this fleet"


def countdown(
    *, ends_at: datetime, now: datetime, reclaim_minutes: int = RECLAIM_MARGIN_MINUTES
) -> Countdown:
    """The clock every other part of the drain reads.

    ``reclaim_minutes`` is a parameter rather than the constant read directly, because the node
    carries its own copy in ``/etc/edullm-block.env`` and a reading taken from a laptop has to
    be able to reproduce what the node believed rather than what this file believes today.
    """
    reclaim_at = ends_at - timedelta(minutes=reclaim_minutes)
    return Countdown(ends_at=ends_at, reclaim_at=reclaim_at, remaining=reclaim_at - now)


def horizon_for(remaining: timedelta) -> int | None:
    """Which warning horizon this much time left has crossed, or ``None`` for none of them.

    The *smallest* horizon that still contains the remaining time, so 73 minutes reads as the
    two-hour warning and 28 reads as the half-hour one. Taking the largest instead would leave
    a fleet twenty minutes from being reclaimed reporting the two-hour horizon, which is the
    wrong end of the same table and is the mistake a reader cannot see.
    """
    for horizon in sorted(WARNING_HORIZONS_MINUTES):
        if remaining <= timedelta(minutes=horizon):
            return horizon
    return None


def remaining_as(interval: timedelta) -> str:
    """A duration in the units the question is asked in, and never in days.

    The same rule as ``block_fleet.elapsed_as`` and for the same reason: the longest window this
    lane serves is seventy-two hours, and ``2d3h`` makes a reader do arithmetic at the moment
    they are least able to. Negative intervals are the caller's to phrase -- see
    :meth:`Countdown.describe` -- so this floors at zero rather than printing a minus sign into
    the middle of a sentence.
    """
    seconds = max(int(interval.total_seconds()), 0)
    hours, rest = divmod(seconds, 3600)
    return f"{hours}h{rest // 60:02d}m"


@dataclass(frozen=True)
class RunFlush:
    """One run directory, as the node reported flushing it.

    ``local`` is what ``find`` counted on the disk and ``remote`` is what S3 answered for the
    destination prefix afterwards. They are carried separately rather than reduced to a boolean
    on the node, because the interesting case is the one where they disagree by a little: a
    sync that copied most of a directory and was throttled out of the rest reads as a success
    to anything that only checked the exit status of ``aws s3 sync``.
    """

    run: str
    local: int
    remote: int
    status: str

    @property
    def shortfall(self) -> int:
        return max(self.local - self.remote, 0)

    @property
    def complete(self) -> bool:
        return self.status == "ok" and self.shortfall == 0


@dataclass(frozen=True)
class DrainReading:
    """What one node said when asked to flush itself.

    ``reachable`` is separate from everything else for the reason
    ``block_fleet.NodeReading`` gives: a node Systems Manager could not deliver to and a node
    with nothing left to flush produce the same empty answer, and during the last hour of a
    window the second reading is the one that makes somebody stop worrying about a machine.
    """

    node: int | None
    instance_id: str
    reachable: bool
    detail: str
    usable_seconds: int | None
    who: str | None
    run: str | None
    container: str | None
    runs: tuple[RunFlush, ...]
    stopped: tuple[str, ...]
    drained_at: datetime | None

    @property
    def truncated(self) -> bool:
        """Whether the record stopped before the node had finished writing it.

        **SYSTEMS MANAGER CUTS INVOCATION OUTPUT OFF AND DOES NOT SAY SO.** A tag-targeted
        fan-out is read back through ``list-command-invocations``, which returns at most a
        couple of thousand characters per node, and a machine that accumulated a run directory
        per person per day over a long weekend can write more than that. What a cut costs is
        the worst possible thing: the records that survive are the early ones, they all parse,
        and the runs whose lines were dropped read as runs that were not there.

        ``drained_at`` is printed last by ``edullm-node drain`` and printed unconditionally, so
        its absence from an invocation that otherwise succeeded means the answer is a prefix of
        the answer. That is not a fact about the node and must never be reported as one.
        """
        return self.reachable and self.drained_at is None

    @property
    def flushed(self) -> bool:
        """Whether everything this node holds is now in S3.

        A node that answered and had nothing to flush is flushed. A node that did not answer is
        not, and must not read as one -- it is the only state in which somebody would look at a
        summary at 10:55 and decide there was nothing to do. Neither is a node whose answer was
        cut short: see :attr:`truncated`.
        """
        return (
            self.reachable
            and not self.truncated
            and all(found.complete for found in self.runs)
        )


def _fields(output: str) -> list[list[str]]:
    return [line.split("\t") for line in output.splitlines() if line.strip()]


def _timestamp(value: str) -> datetime | None:
    """A timestamp the node printed, or nothing.

    Tolerated rather than raised on, for the reason ``block_fleet.parse_reading`` tolerates a
    malformed claim timestamp: these records are written by ``printf`` on a machine rather than
    by a serializer, and losing a whole drain report over one unparsable field would report a
    node that answered as a node that did not.
    """
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_drain_reading(
    *, node: int | None, instance_id: str, status: str, output: str
) -> DrainReading:
    """One node's drain record, or the reason there is not one.

    Tab-separated records rather than JSON, which is the convention
    ``block_fleet.REMOTE_READING_SCRIPT`` already set and for its reason: emitting JSON from
    shell means quoting by hand or depending on ``jq``, and ``jq`` is not on every image this
    AMI family has shipped. A run name is validated against ``SAFE_NAME`` on the machine before
    it can become a directory, so no field here can contain a tab.

    The invocation status is read before the output, for the same reason ``parse_reading``
    reads it first: an empty output parses perfectly into a node with nothing left to save.
    """
    if status != "Success":
        return DrainReading(
            node=node,
            instance_id=instance_id,
            reachable=False,
            detail=status or "no answer",
            usable_seconds=None,
            who=None,
            run=None,
            container=None,
            runs=(),
            stopped=(),
            drained_at=None,
        )

    usable: int | None = None
    who: str | None = None
    held: str | None = None
    container: str | None = None
    drained: datetime | None = None
    runs: list[RunFlush] = []
    stopped: list[str] = []

    for record in _fields(output):
        key, values = record[0].strip(), [part.strip() for part in record[1:]]
        if key == "usable_seconds" and values and _signed_integer(values[0]) is not None:
            usable = _signed_integer(values[0])
        elif key == "claim" and len(values) >= 2:
            who, held = values[0] or None, values[1] or None
        elif key == "container" and values:
            container = values[0] or None
        elif key == "run" and len(values) >= 4:
            runs.append(
                RunFlush(
                    run=values[0],
                    local=_signed_integer(values[1]) or 0,
                    remote=_signed_integer(values[2]) or 0,
                    status=values[3],
                )
            )
        elif key == "stopped" and values and values[0]:
            stopped.append(values[0])
        elif key == "drained_at" and values:
            drained = _timestamp(values[0])

    return DrainReading(
        node=node,
        instance_id=instance_id,
        reachable=True,
        detail="",
        usable_seconds=usable,
        who=who,
        run=held,
        container=container,
        runs=tuple(runs),
        stopped=tuple(stopped),
        drained_at=drained,
    )


def _signed_integer(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class Checkpoints:
    """What is under a run's checkpoint prefix in S3, sorted into the two kinds it can hold."""

    complete: tuple[int, ...]
    torn: tuple[int, ...]
    latest_written: datetime | None
    truncated: bool

    @property
    def latest_step(self) -> int | None:
        steps = self.complete + self.torn
        return max(steps) if steps else None


def read_checkpoints(listing: Mapping[str, Any], *, prefix: str) -> Checkpoints:
    """Which step directories under a checkpoint prefix are checkpoints and which are wreckage.

    **WHY THIS IS WORTH A READING AT ALL.** A capacity block ends by termination rather than by
    the trainer finishing, so a save that was in flight at the moment the host went is left
    half written: ``train/rank0.pt`` is written before the first ``model_and_optim`` shard and
    ``.metadata.json`` is written last, so the shape of an interrupted write is a ``stepN``
    directory holding some of the three and not all of them. OLMo-core's own
    ``remove_torn_checkpoints`` clears exactly those on the way into a *resume*, which is why
    this does not delete anything: the repair already exists and belongs to the run that picks
    the work up. What does not exist is anybody knowing it is there, on a block whose outputs
    nobody resumes from because the next run is on the platform in another region.

    The completeness test is ``Checkpointer.dir_is_checkpoint`` transcribed rather than
    approximated. A directory carrying only ``.metadata`` is a weights-only checkpoint and is
    complete -- treating that as torn would report every published set of weights in the bucket
    as damage.

    ``truncated`` is carried rather than swallowed. A listing that stopped early is a statement
    about the page size and not about the bucket, and reporting "no torn checkpoints" off a
    partial read is the one answer this must never give.
    """
    root = f"{prefix.rstrip('/')}/"
    inside: dict[int, set[str]] = {}
    latest: datetime | None = None

    for entry in listing.get("Contents") or []:
        if not isinstance(entry, Mapping):
            continue
        key = str(entry.get("Key") or "")
        if not key.startswith(root):
            continue
        head, _, tail = key[len(root) :].partition("/")
        matched = _STEP_DIRECTORY.match(head)
        if matched is None or not tail:
            continue
        inside.setdefault(int(matched.group(1)), set()).add(tail)
        written = _timestamp(str(entry.get("LastModified") or ""))
        if written is not None and (latest is None or written > latest):
            latest = written

    complete: list[int] = []
    torn: list[int] = []
    for step, members in inside.items():
        whole = _WEIGHTS_ONLY_MARKER in members or all(
            marker in members for marker in _FULL_CHECKPOINT_MARKERS
        )
        (complete if whole else torn).append(step)

    return Checkpoints(
        complete=tuple(sorted(complete)),
        torn=tuple(sorted(torn)),
        latest_written=latest,
        truncated=bool(listing.get("NextToken") or listing.get("IsTruncated")),
    )


def drain_rows(readings: Sequence[DrainReading]) -> tuple[str, ...]:
    """One line per node, in the shape somebody scans at 10:40 on the last morning.

    A node with nothing on it says so on its own line rather than as a row of zeroes, for the
    reason ``block_fleet.status_rows`` prints ``IDLE`` that way: the question being asked is
    which machines still need something done to them, and that wants to be answerable down a
    column.
    """
    lines: list[str] = []
    for reading in readings:
        label = f"node {reading.node if reading.node is not None else '?'}"
        head = f"{label:<8}{reading.instance_id:<21}"
        if not reading.reachable:
            lines.append(f"{head}UNREACHABLE  {reading.detail}")
            continue
        if reading.truncated:
            lines.append(f"{head}ANSWER CUT SHORT  read the drain log on the machine")
            continue
        if not reading.runs:
            lines.append(f"{head}NOTHING TO SAVE")
            continue
        short = [found for found in reading.runs if not found.complete]
        verdict = "IN FLIGHT" if short else "SAVED"
        detail = ", ".join(f"{found.run} {found.remote}/{found.local}" for found in reading.runs)
        lines.append(f"{head}{verdict:<13}{reading.who or '-':<9} {detail}")
        for found in short:
            lines.append(
                f"{'':<29}{found.run}: {found.shortfall} of {found.local} files are not in S3"
            )
    return tuple(lines)


def outstanding(reading: DrainReading) -> str:
    """Why one node is not finished, in the words a person would use about it."""
    if not reading.reachable:
        return f"did not answer ({reading.detail})"
    if reading.truncated:
        return (
            "its report was cut off part way through, so what it did not mention is unknown "
            "rather than absent -- read /var/lib/edullm/drain.log on the machine"
        )
    short = [found for found in reading.runs if not found.complete]
    return "; ".join(
        f"{found.run} is {found.shortfall} files short of the {found.local} on disk"
        if found.shortfall
        else f"{found.run} reported {found.status}"
        for found in short
    )


def drain_markdown(
    readings: Sequence[DrainReading],
    *,
    clock: Countdown,
    checkpoints: Mapping[str, Checkpoints] | None = None,
) -> str:
    """The same reading as a job summary, for the people who have no other way to see it.

    Roughly fifteen of the thirty-five people on this team hold no AWS role, so a drain report
    that only exists as a table on a maintainer's terminal is a report addressed to the half of
    the team that was already fine. Markdown into ``GITHUB_STEP_SUMMARY`` is the one surface a
    repository login is enough to read.
    """
    torn_by_run = {
        run: found.torn for run, found in (checkpoints or {}).items() if found.torn
    }
    unflushed = [reading for reading in readings if not reading.flushed]

    lines = [
        f"### Capacity block drain — {clock.describe()}",
        "",
        f"| window ends | `{clock.ends_at.isoformat()}` |",
        "| --- | --- |",
        f"| AWS starts terminating | `{clock.reclaim_at.isoformat()}` |",
        f"| nodes read | {len(readings)} |",
        f"| nodes with work still in flight | {len(unflushed)} |",
        "",
    ]
    if not readings:
        lines.append("No running instance carries a block tag, so there is nothing to drain.")
        return "\n".join(lines)

    lines += ["```", *drain_rows(readings), "```", ""]

    if unflushed:
        lines += [
            (
                "**These machines still hold something that is not in S3.** Everything under "
                "`/scratch` on them is on instance store and goes with the instance."
            ),
            "",
        ]
        for reading in unflushed:
            who = f"held by {reading.who}" if reading.who else "unclaimed"
            lines.append(
                f"- node {reading.node} (`{reading.instance_id}`), {who}: {outstanding(reading)}"
            )
        lines.append("")

    if torn_by_run:
        lines += [
            (
                "**Interrupted checkpoint writes.** A `stepN` directory missing "
                "`.metadata.json` is the wreckage of a save that did not finish. OLMo-core "
                "clears these on the way into a resume through `remove_torn_checkpoints`, so "
                "nothing here needs deleting by hand -- but nothing resumes these prefixes "
                "after the window either, so read the step below the torn one as the last "
                "usable state."
            ),
            "",
        ]
        for run, steps in sorted(torn_by_run.items()):
            lines.append(f"- `{run}`: {', '.join(f'step{step}' for step in steps)}")
        lines.append("")

    return "\n".join(lines)


def unflushed_instances(readings: Iterable[DrainReading]) -> tuple[str, ...]:
    """Every instance that did not report everything safely in S3, for an exit code to read."""
    return tuple(reading.instance_id for reading in readings if not reading.flushed)
