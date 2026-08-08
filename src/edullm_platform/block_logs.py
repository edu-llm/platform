"""A training log, read by somebody who holds no AWS credential and never will.

**THE POPULATION THIS EXISTS FOR IS THE SAME ONE ``block-run.yml`` EXISTS FOR.** Roughly
fifteen of the thirty-five people on this team hold no AWS role at all: they cannot assume
anything, cannot open a Systems Manager session and cannot read the outputs bucket. The node's
log sync already carries every run's ``train.log`` to S3 once a minute, which solved the
durability half of the problem and none of the access half -- ``s3://`` needs a credential, so
what those fifteen people actually have is Weights and Biases and nothing else. W&B is a good
surface for a loss curve and is no surface at all for a stack trace.

So the log is fetched by a workflow, which holds the credential, and printed into the job
summary, which anybody with repository access can read in a browser. That is the whole idea and
everything below is arrangement around it.

**IT RESOLVES OUT OF S3 RATHER THAN OFF THE NODE, AND THAT IS THE LOAD-BEARING CHOICE.** The
obvious implementation asks the machine which run it is holding, through the same Systems
Manager probe ``block-run.yml`` uses. It would work for three days and then stop, at the exact
moment the logs become the only thing left: after the reclaim there are no machines, no claim
files and no Systems Manager targets, and the question "what did that run print" is asked most
often *after* a window rather than during one. Every lookup here is a listing of the bucket.

**THE TAIL IS A RANGE READ AND NOT A DOWNLOAD.** A seventy-two hour run's log is not small, and
``aws s3 cp s3://... -`` pulls all of it to print the last two hundred lines of it. The size is
read first and the last few hundred kilobytes are fetched by byte range, which is the
difference between a workflow that answers in seconds and one that answers in minutes for the
same two hundred lines.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

__all__ = [
    "LOG_FILE",
    "MAXIMUM_TAIL_BYTES",
    "MINIMUM_TAIL_BYTES",
    "AmbiguousRunError",
    "Progress",
    "RunLog",
    "choose_run",
    "common_prefixes",
    "log_key",
    "logs_markdown",
    "progress",
    "read_candidates",
    "tail",
    "tail_range",
]

#: What the node's log sync writes and therefore the one object this reads. Named here rather
#: than spelled into each caller, because it is the same string in
#: ``infra/block-node-bootstrap.sh`` and a disagreement between the two is a workflow that
#: reports every run as having no log.
LOG_FILE: Final = "log/train.log"

#: How much of the tail to ask for per line requested. Generous against an OLMo-core console
#: line, which is well under a hundred characters, because the cost of overshooting is a few
#: kilobytes of range read and the cost of undershooting is a summary that prints forty lines
#: when somebody asked for two hundred.
BYTES_PER_LINE: Final = 512

#: Floors and ceilings on the range read. The floor keeps a small request from fetching so
#: little that a single wrapped traceback fills it; the ceiling is what stops a request for ten
#: thousand lines from pulling a gigabyte through a runner to print into a page that cannot
#: hold it. GitHub truncates a job summary at one mebibyte.
MINIMUM_TAIL_BYTES: Final = 16 * 1024
MAXIMUM_TAIL_BYTES: Final = 512 * 1024

#: The progress marker ``ConsoleLoggerCallback`` prints, matched loosely on purpose. Whatever
#: logging prefix the image is configured with sits to the left of it and none of it is worth
#: depending on, so the anchor is the bracket rather than the start of the line.
_STEP_MARKER: Final = re.compile(r"\[step=(\d+)/(\d+|\?+),epoch=(\d+)")

#: The metric a person means when they ask how a run is going. OLMo-core records it under this
#: exact name and ``.edullm/train_on_corpus.py`` reads the same key out of its own callback, so
#: the two agree about what "the loss" is.
_LOSS: Final = re.compile(r"train/CE loss=([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)")

#: Both spellings the trainer uses. The first is printed when a save starts and the second when
#: an asynchronous one lands, and during the last hour of a window the gap between them is
#: exactly the thing somebody is trying to see.
_CHECKPOINT_STARTED: Final = re.compile(r"Saving checkpoint for step (\d+)")
_CHECKPOINT_LANDED: Final = re.compile(r"Checkpoint for step (\d+) saved successfully")

#: What a run dying looks like in its own output. Deliberately not an exhaustive taxonomy --
#: this is the line a summary quotes so that somebody does not have to read two hundred lines
#: to find out whether there is a problem, and being approximately right about that is worth
#: far more than being exactly right about which exception classes exist.
_TROUBLE: Final = re.compile(
    r"Traceback \(most recent call last\)|CUDA out of memory|OutOfMemoryError|"
    r"\bERROR\b|\bCRITICAL\b"
)


class AmbiguousRunError(RuntimeError):
    """More than one run answered, or none did, and picking for the caller would be a guess.

    ``reason`` is a colon-delimited token followed by a sentence, which is the shape
    ``CaptureFailedError`` uses and the shape the block workflows already print.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class RunLog:
    """One run's log object, as the bucket describes it."""

    node: int | None
    run: str
    key: str
    size: int
    modified: datetime | None


def log_key(*, reservation: str, node: int, run: str) -> str:
    """Where one run's log lives, assembled once.

    The layout is ``block/<reservation>/node-<n>/<run>/log/train.log`` and it is
    ``infra/block-node-bootstrap.sh`` that decides it. The reservation is in the path because
    two blocks in one month is two fleets and node numbers repeat across them.
    """
    return f"block/{reservation}/node-{node}/{run}/{LOG_FILE}"


def _leaf(prefix: str) -> str:
    return prefix.rstrip("/").rsplit("/", 1)[-1]


def common_prefixes(listing: Mapping[str, Any]) -> tuple[str, ...]:
    """The directory names one level down, out of a delimited ``list-objects-v2`` answer.

    Delimited rather than a recursive listing, and that is not a micro-optimisation. A run
    prefix holds every checkpoint shard the trainer wrote, so a recursive listing to find out
    which runs exist reads thousands of keys to answer a question about a handful of names.
    """
    return tuple(
        _leaf(str(entry.get("Prefix") or ""))
        for entry in listing.get("CommonPrefixes") or []
        if isinstance(entry, Mapping) and entry.get("Prefix")
    )


def read_candidates(
    described: Sequence[tuple[int, str, str, Mapping[str, Any] | None]],
) -> tuple[RunLog, ...]:
    """Every run whose log object actually exists, out of what ``head-object`` answered.

    Each element is a node number, a run name, that run's log key, and the ``head-object``
    answer for it -- or ``None`` where the object is not there, which is an ordinary state
    rather than an error: a run that was claimed and never started has a scratch prefix in S3
    and no log under it, and so does a run whose container died before printing a line.

    Ordered newest first, because the question this is asked is almost always about the run
    somebody started a minute ago. Sorted on the epoch seconds rather than on the timestamps
    themselves, since an object with no readable ``LastModified`` has to sort against ones that
    have a timezone-aware datetime and Python refuses to order those two kinds against each
    other.
    """
    found: list[RunLog] = []
    for node, run, key, head in described:
        if head is None:
            continue
        found.append(
            RunLog(
                node=node,
                run=run,
                key=key,
                size=int(head.get("ContentLength") or 0),
                modified=_timestamp(str(head.get("LastModified") or "")),
            )
        )
    return tuple(
        sorted(
            found,
            key=lambda item: (
                item.modified is not None,
                item.modified.timestamp() if item.modified is not None else 0.0,
            ),
            reverse=True,
        )
    )


def choose_run(candidates: Sequence[RunLog], *, run: str | None = None) -> RunLog:
    """The one run this request is about, or a refusal that names the alternatives.

    **A NAMED RUN THAT MATCHES TWO NODES IS A REFUSAL AND NOT A COIN FLIP.** Run names are
    chosen by people on a shared sheet and nothing enforces uniqueness across the fleet, so two
    people naming a run ``baseline`` on different nodes is reachable. Printing one of the two
    logs under a heading that names the run would be indistinguishable, to the reader, from
    printing theirs.

    With no name, the most recently written log wins, which is the question somebody is
    actually asking when they pass only a node number.
    """
    if not candidates:
        raise AmbiguousRunError(
            "no_run_has_a_log_here: nothing under this prefix carries a log/train.log. Either "
            "no run has started on it, or the node number and the block do not go together."
        )
    if run is None:
        return candidates[0]
    matching = [found for found in candidates if found.run == run]
    if not matching:
        # Distinct names rather than one per candidate. A search that spans the fleet has one
        # candidate per node, so a run on two machines is listed twice -- and a reader looking
        # for their typo reads that as two different runs sharing a name, which is the thing
        # the refusal below this one exists to tell them about and is not what happened.
        raise AmbiguousRunError(
            f"no_such_run:{run}. The runs with a log here are "
            f"{', '.join(sorted({found.run for found in candidates}))}."
        )
    if len(matching) > 1:
        # In node order rather than in the newest-first order the candidates arrive in, because
        # what the reader does with this line is go and ask the two people who are on those
        # machines, and they refer to them by number.
        nodes = ", ".join(str(found.node) for found in sorted(matching, key=lambda f: f.node or 0))
        raise AmbiguousRunError(
            f"run_is_on_more_than_one_node:{run} has a log on nodes {nodes}. Pass the node as "
            "well -- nothing stops two people choosing the same run name."
        )
    return matching[0]


def tail_range(*, size: int, lines: int) -> tuple[int, int]:
    """The byte range that holds at least ``lines`` lines of the end of an object.

    Returns a half-open ``(start, length)`` in bytes. ``start`` is zero for an object smaller
    than the window, which is what makes the first fetch of a run that has just begun return
    the whole log rather than a slice of it that happens to begin mid-word.
    """
    window = min(max(lines * BYTES_PER_LINE, MINIMUM_TAIL_BYTES), MAXIMUM_TAIL_BYTES)
    start = max(size - window, 0)
    return start, size - start


def tail(text: str, lines: int, *, partial_first_line: bool) -> str:
    """The last ``lines`` lines, with the first one dropped where the range cut it in half.

    ``partial_first_line`` is the caller's knowledge rather than something this can infer: a
    range read that started at a non-zero offset almost certainly began in the middle of a
    line, and a line beginning mid-word reads as corruption to somebody who does not know a
    byte range was involved.
    """
    body = text.splitlines()
    if partial_first_line and len(body) > 1:
        body = body[1:]
    return "\n".join(body[-lines:]) if lines > 0 else ""


@dataclass(frozen=True)
class Progress:
    """What the tail says about where the run had got to, for the header of the summary."""

    step: int | None
    max_steps: str | None
    epoch: int | None
    loss: float | None
    checkpoint_started: int | None
    checkpoint_landed: int | None
    trouble: str | None

    @property
    def checkpoint_in_flight(self) -> bool:
        """A save that started and has not been reported as landed.

        Worth its own name because it is the state the drain cares about: a run interrupted
        here is the one that leaves a torn ``stepN`` directory behind in the bucket.
        """
        if self.checkpoint_started is None:
            return False
        return self.checkpoint_landed is None or self.checkpoint_landed < self.checkpoint_started


def progress(text: str) -> Progress:
    """Read the last of each thing worth putting above the log rather than inside it.

    **THE LOSS IS TAKEN FROM THE LAST LINE THAT CARRIES ONE AND NOT FROM THE LAST STEP MARKER.**
    The console callback prints the marker and then the metrics under it as separate lines of
    one record, and a tail can land between the two -- so pairing them positionally would drop
    the loss of the most recent step about as often as it reported it. They are read
    independently and the header says both, which is honest about the one case where the tail
    cut the record in half.

    Everything here is a last-match scan over a few hundred lines of text. It is cheap enough
    that being asked for it costs nothing, which is the argument for surfacing it at all: it
    turns a page somebody has to read into a page they can glance at.
    """
    step: int | None = None
    max_steps: str | None = None
    epoch: int | None = None
    loss: float | None = None
    started: int | None = None
    landed: int | None = None
    trouble: str | None = None

    for line in text.splitlines():
        marker = _STEP_MARKER.search(line)
        if marker is not None:
            step, max_steps, epoch = int(marker.group(1)), marker.group(2), int(marker.group(3))
        measured = _LOSS.search(line)
        if measured is not None:
            loss = float(measured.group(1))
        saving = _CHECKPOINT_STARTED.search(line)
        if saving is not None:
            started = int(saving.group(1))
        saved = _CHECKPOINT_LANDED.search(line)
        if saved is not None:
            landed = int(saved.group(1))
        if _TROUBLE.search(line):
            trouble = line.strip()

    return Progress(
        step=step,
        max_steps=max_steps,
        epoch=epoch,
        loss=loss,
        checkpoint_started=started,
        checkpoint_landed=landed,
        trouble=trouble,
    )


def _timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def logs_markdown(
    record: RunLog,
    *,
    reservation: str,
    body: str,
    measured: Progress,
    lines: int,
    bucket: str,
) -> str:
    """The page a person with no AWS access reads, which is the whole point of this module."""
    reached = "not yet reported" if measured.step is None else str(measured.step)
    if measured.max_steps:
        reached = f"{reached} of {measured.max_steps}"
    loss = "none in this tail" if measured.loss is None else str(measured.loss)
    written = record.modified.isoformat() if record.modified is not None else "unknown"
    header = [
        f"### `{record.run}` on node {record.node}",
        "",
        f"| step | {reached} |",
        "| --- | --- |",
        f"| last logged loss | {loss} |",
        f"| last checkpoint | {_checkpoint_phrase(measured)} |",
        f"| log size | {record.size:,} bytes |",
        f"| last written | {written} |",
        f"| block | `{reservation}` |",
        "",
    ]
    if measured.trouble is not None:
        header += [
            "**The tail carries something that reads as trouble:**",
            "",
            "```",
            measured.trouble,
            "```",
            "",
        ]
    return "\n".join(
        [
            *header,
            f"Last {lines} lines of `s3://{bucket}/{record.key}`:",
            "",
            "```text",
            body,
            "```",
        ]
    )


def _checkpoint_phrase(measured: Progress) -> str:
    """What the tail says about the last save, allowing for it having caught only half of one.

    A save prints twice -- once when it starts and once when it lands -- and a tail can begin
    between the two, so a landing with no start in view is an ordinary reading rather than a
    contradiction. Answering "none in this tail" for it, which is what checking the start
    first does, hides the most recent checkpoint the run actually wrote.
    """
    if measured.checkpoint_in_flight:
        return f"step{measured.checkpoint_started} started and not yet reported saved"
    if measured.checkpoint_landed is not None:
        return f"step{measured.checkpoint_landed} saved"
    return "none in this tail"
