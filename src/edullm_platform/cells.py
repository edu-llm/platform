"""What a run's cells did, and the one place that decides what that makes the run.

**A CELL IS A SCHEDULER JOB AND AN ORDINAL IS A RETRY, AND UNTIL THIS MODULE EXISTED THE
DIFFERENCE WAS NOT WRITTEN DOWN ANYWHERE.** ``lifecycle_projection._last_attempt``
enumerates ``detail["attempts"]``, which is Batch's retry list for one scheduler job, so
:attr:`~.contracts.lifecycle.SchedulerAttempt.attempt_ordinal` is scoped to
``(run_id, scheduler_job_id)`` and begins again at 1 inside every cell of an array. Batch
names an array's children ``<parent>:<index>``, and that suffix is the only place a cell's
index exists. Read from the lineage store on 2026-08-06: 523 attempt records, 514 at
ordinal 1 and 9 at ordinal 2, falling into 523 distinct ``(run_id, scheduler_job_id)``
groups with no ordinal repeated inside any one of them. The nine are single containers
Batch retried once. **The records are right. Nothing defaults the field and no repair of
them is owed.**

What was wrong was reading them. ``substrate._state`` grouped by ``run_id`` alone and took
``max(attempts, key=ordinal)``. Over one cell that key totally orders the retries and the
answer is correct; over forty-eight cells every key is 1, ``max`` returns the first maximal
element, and the run's outcome came out of whatever order S3 listed the prefix in. Seven
runs in the store are tied that way and two of them are split exactly twenty-four succeeded
and twenty-four failed, which is the pair that cannot be decided by any tiebreak that reads
position.

**THE COUNTS ARE THE OUTCOME, AND THAT IS THIS TREE'S OWN ANSWER RATHER THAN A NEW ONE.**
``notifications.messages.render_run_ended`` puts ``all 20 cells succeeded`` or ``19 of 20
cells succeeded, 1 failed`` in the slot a single-cell run puts its outcome word in, and says
no single word about a fan-out at all -- because a sweep that lost one cell and a sweep that
lost all but one are different events and one word cannot be both. :func:`said_of_cells`
is that sentence, restated here so that a reader with a :class:`CellOutcome` and a reader
with a Batch event produce the same line; ``tests/test_an_outcome_by_listing_order.py``
compares the two spellings, on the argument ``notifications.facts.CANCELLATION_MARKERS`` is
restated under.

:attr:`CellOutcome.state` exists because several readers have one field to say this in and
have to say something. It is the plain terminal word wherever the cells agree, which is
every single-cell run and twelve of the nineteen fan-outs, and :data:`CELLS_DISAGREE`
wherever some cells succeeded and some did not -- a word that sends the reader to the
counts rather than claiming the sweep worked or that it did not.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

__all__ = [
    "CELLS_DISAGREE",
    "CellOutcome",
    "outcome_of_cells",
    "said_of_cells",
]

#: What a run is called when some of its cells succeeded and some did not.
#:
#: Not a member of :class:`~.contracts.lifecycle.RunState`, and deliberately so. A lifecycle
#: event describes one Batch job -- one cell, or one single container -- and ``succeeded``
#: or ``failed`` is the whole truth about that job. This is a fact about a set of them, it
#: is derived rather than recorded, and adding it to the contract would export a state no
#: event can ever carry and no transition can reach.
CELLS_DISAGREE: Final = "partly_succeeded"

#: The terminal word for a cell that did not succeed and was not cancelled.
_FAILED: Final = "failed"
_SUCCEEDED: Final = "succeeded"
_CANCELLED: Final = "cancelled"


@dataclass(frozen=True)
class CellOutcome:
    """How a run's cells ended: one word, and the counts that are the real answer.

    ``failed`` is every cell that did not succeed, which is how Batch's own
    ``arrayProperties.statusSummary`` counts them -- a job this platform terminates comes
    back from Batch as ``FAILED`` carrying the reason the submitter typed, so a cancelled
    cell is already inside that figure wherever the counts are read off an event. Holding
    the two readings to one definition is what lets the message and the record render one
    sentence.
    """

    state: str
    total: int
    succeeded: int
    failed: int

    @property
    def said(self) -> str:
        """The clause the runs channel already prints, for a reader that has this record."""
        return said_of_cells(total=self.total, succeeded=self.succeeded)


def said_of_cells(*, total: int, succeeded: int) -> str:
    """How the array went, in the terms the eval group acts on.

    `all 20 cells succeeded` rather than `20 of 20 succeeded, 0 failed`. A zero somebody has
    to read past is a zero that stops being read.
    """
    failed = total - succeeded
    if failed == 0:
        return f"all {total} cells succeeded"
    return f"{succeeded} of {total} cells succeeded, {failed} failed"


def outcome_of_cells(cells: Iterable[tuple[str, int, str]]) -> CellOutcome | None:
    """What a run's attempt records make of it, or ``None`` because there are none.

    Each entry is one attempt record reduced to ``(scheduler_job_id, attempt_ordinal,
    terminal_state)``. Tuples rather than a model, so that the substrate's ``AttemptFacts``
    and the store's ``SchedulerAttempt`` can both reach this without either module having to
    import the other's shape.

    THE TWO GROUPINGS ARE THE WHOLE OF IT. Records are grouped by scheduler job, because
    that is a cell; inside a cell the highest ordinal wins, because those are sequential
    retries of one container and the last one is what happened; across cells nothing wins,
    because they ran beside each other and all of them count. Ordinals are unique inside a
    job -- 523 records over 523 groups with no repeat, read on 2026-08-06 -- so the choice
    inside a cell is not a tie; the tie was only ever created by grouping cells together.
    ``attempt_id`` breaks a repeat anyway rather than leaving one to arrival order, since a
    duplicate delivery is the one way the store could come to hold one.

    ``None`` for a run with no attempt record at all. Zero cells is not a fan-out that lost
    everything, and ``0 of 0 cells succeeded`` is a sentence about a run that ran.
    """
    latest: dict[str, tuple[int, str]] = {}
    for scheduler_job_id, ordinal, terminal_state in cells:
        standing = latest.get(scheduler_job_id)
        if standing is None or (ordinal, terminal_state) > standing:
            latest[scheduler_job_id] = (ordinal, terminal_state)
    if not latest:
        return None

    endings = [terminal_state for _, terminal_state in latest.values()]
    total = len(endings)
    succeeded = sum(1 for ending in endings if ending == _SUCCEEDED)
    distinct = set(endings)
    if len(distinct) == 1:
        # Every cell ended the same way, so the run ended that way, whatever way that was.
        # This is the branch every single-cell run takes and it is why one cell reports
        # exactly what it reported before this module existed.
        state = distinct.pop()
    elif succeeded:
        state = CELLS_DISAGREE
    else:
        # Failed and cancelled cells with nothing successful among them. No part of this
        # succeeded, so the reader is owed the plain word rather than one that implies a
        # part of it did.
        state = _FAILED
    return CellOutcome(state=state, total=total, succeeded=succeeded, failed=total - succeeded)
