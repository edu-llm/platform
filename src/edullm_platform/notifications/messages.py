"""Every word anybody reads, and nothing that can send one.

THIS IS THE FILE TO EDIT WHEN THE WORDING IS WRONG. Nothing outside it holds a sentence, and
``tests/test_notification_messages.py`` refuses this module any import that could open a
socket. So a change here is a change to what people read and cannot be a change to what gets
sent, which is what makes iterating on the wording cheap.

Print any of it without an AWS account:

    uv run python tools/render_notification.py --event fixtures/events/batch-failed.sanitized.json

**The rule every message obeys.** Who, which experiment, how much, in that order, before
anything else. A message opening with a total opens with something nobody is accountable for.

**Every unknown is said rather than filled in.** A run nothing could name a submitter for
says so, a run whose queue nothing prices says the cost is unknown, a fan-out whose cells
were not read says the spend was not read, and a failed run whose checkpoint prefix nobody
listed says the survival is unknown. Substituting the team for a person, zero for a price, or
the authorised ceiling for a spend each produces a message that reads exactly like a correct
one, and the last of those is the worst because a ceiling is a real number several times too
big.

**What is deliberately not here.** The canvas's finished message ends with a bits-per-byte
figure. That number lives in Weights and Biases and is in no Batch event, so it would need a
W&B call and a credential this path does not hold. The money and the machine are what the
event can support, and inventing the rest is worse than leaving it out.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from .facts import RunEndedFacts

__all__ = [
    "NAMED_CELLS",
    "RUNS_CHANNEL",
    "Message",
    "duration",
    "money",
    "render_run_ended",
]

#: The one channel this slice posts to. The approvals channel and the direct messages in the
#: design need a chat integration that does not exist in this organization and, for the
#: direct messages, a map from a GitHub login to a chat account that nothing here holds.
RUNS_CHANNEL: Final = "runs"

#: Said rather than guessed, and it says what is true of the message rather than what is true
#: of the person. Two sources have to fail together to reach this: the intent record, which
#: names all thirty-five and is read from the lineage store, and WANDB_USERNAME, which names
#: thirty and is in the envelope. So this is a reader that could not look rather than a
#: submitter nobody recorded, and the wording no longer blames the roster for it.
NOBODY_NAMED: Final = "Somebody this message could not name"

#: A run admitted before the experiment field existed carries none.
NO_EXPERIMENT: Final = "no experiment recorded"

SECONDS_A_MINUTE: Final = 60
SECONDS_AN_HOUR: Final = 3600


@dataclass(frozen=True)
class Message:
    """One thing to say, and where to say it.

    A channel and a string, and nothing about how it travels. That is the whole interface
    between the wording and the transport, and keeping it this small is what lets either
    change without the other.
    """

    channel: str
    text: str


def money(value: Decimal | None) -> str:
    """A figure to the cent, or the words for not having one.

    Two reports of one figure at two precisions send somebody looking for a bug in the
    arithmetic, so everything is to the cent.
    """
    if value is None:
        return "cost unknown"
    return f"${value:,.2f}"


def duration(seconds: int) -> str:
    """How long, the way a person says it.

    Three shapes rather than one. `3h52m` for anything over an hour, `42m` for anything over
    a minute, and `7s` below that, because a run that died in seven seconds died at startup
    and rounding it to `0m` hides the one fact worth having.
    """
    if seconds >= SECONDS_AN_HOUR:
        return f"{seconds // SECONDS_AN_HOUR}h{(seconds % SECONDS_AN_HOUR) // SECONDS_A_MINUTE:02d}m"
    if seconds >= SECONDS_A_MINUTE:
        return f"{seconds // SECONDS_A_MINUTE}m"
    return f"{seconds}s"


def _who(facts: RunEndedFacts) -> str:
    return facts.person or NOBODY_NAMED


def _which(facts: RunEndedFacts) -> str:
    return facts.experiment or NO_EXPERIMENT


def _where(facts: RunEndedFacts) -> str:
    """The machine, named by its profile where one is known and by its queue where none is.

    The queue is named as a queue so a reader can tell the two apart. A bare queue name in
    the slot a profile usually fills reads as a profile nobody has heard of.
    """
    if facts.compute_profile is not None:
        return facts.compute_profile
    if facts.queue_name is not None:
        return f"the {facts.queue_name} queue"
    return "a machine this event does not name"


def _spend_clause(facts: RunEndedFacts) -> str:
    if facts.spent_usd is None:
        return money(None)
    return f"{money(facts.spent_usd)} spent, {money(facts.authorised_usd)} authorised"


def _spent_only(facts: RunEndedFacts) -> str:
    """What was burned, on the two endings that produced nothing to weigh it against."""
    if facts.spent_usd is None:
        return money(None)
    return f"{money(facts.spent_usd)} spent"


#: How many failed cell indexes a message names before it stops counting them out. A sweep
#: that lost most of its cells is one somebody opens the run id of and goes to look at, and a
#: line carrying a hundred and fifty numbers is a line nobody reads at all.
NAMED_CELLS: Final = 10


def _cell_clause(facts: RunEndedFacts) -> str:
    """How the array went, in the terms the eval group acts on.

    `all 20 cells succeeded` rather than `20 of 20 succeeded, 0 failed`. A zero somebody has
    to read past is a zero that stops being read.
    """
    total, failed = facts.cells_total, facts.cells_failed or 0
    if failed == 0:
        return f"all {total} cells succeeded"
    return f"{facts.cells_succeeded} of {total} cells succeeded, {failed} failed"


def _array_spend_clause(facts: RunEndedFacts) -> str:
    """What a sweep cost, or which of the two ways that is not known.

    THREE OUTCOMES AND NOT TWO, AND THE MIDDLE ONE IS THE POINT. A ceiling rendered where a
    spend belongs reads exactly like a measurement and is several times the real figure, so
    a sweep whose cells were not read says that rather than showing $55.83 in the slot the
    money goes in. A sweep whose queue nothing prices has no ceiling either, and says the
    one thing that covers both.
    """
    if facts.authorised_usd is None:
        return money(None)
    if facts.spent_usd is None:
        return f"spend not read, {money(facts.authorised_usd)} authorised"
    if facts.cells_measured != facts.cells_total:
        return (
            f"at least {money(facts.spent_usd)} spent over the {facts.cells_measured} cells "
            f"that were read, {money(facts.authorised_usd)} authorised"
        )
    return f"{money(facts.spent_usd)} spent, {money(facts.authorised_usd)} authorised"


def _failed_cell_clause(facts: RunEndedFacts) -> str:
    """Which cells died, where that was read, and otherwise that it was not.

    Empty for a sweep that lost nothing. There is nothing to name and a sentence saying so
    is a sentence between the reader and the next message.
    """
    if (facts.cells_failed or 0) == 0:
        return ""
    if facts.failed_cell_indexes is None:
        return " Which cells failed was not read."
    named = facts.failed_cell_indexes[:NAMED_CELLS]
    listed = ", ".join(str(index) for index in named)
    if len(facts.failed_cell_indexes) > NAMED_CELLS:
        return f" Cells {listed} failed, and {len(facts.failed_cell_indexes) - NAMED_CELLS} more."
    if len(named) == 1:
        return f" Cell {listed} is the one that failed."
    return f" Cells {listed} failed."


def render_run_ended(facts: RunEndedFacts) -> Message:
    """One ended run, as one line in the runs channel.

    One message and not two. A run that failed fires the same trigger and carries the more
    urgent version of the same sentence, and splitting them into two events would be two
    subscriptions to the same fact.
    """
    if facts.cells_total is not None:
        # WHY THIS SAYS BOTH FIGURES AND HOW IT GETS THE FIRST ONE.
        #
        # The array parent's terminal event carries no attempts, so nothing in it says what
        # twenty cells burned. The ceiling is in it, through the attempt timeout, the retry
        # count and the array size. The spend is read from Batch, which has already moved
        # every child to a terminal status by the time the parent goes terminal, and the
        # failed indexes come off the child job ids in the same answer.
        #
        # No duration. `ran 9h45m` on a sweep would be the sum across cells that ran beside
        # each other, which is not a wall clock anybody experienced and reads as one.
        return Message(
            channel=RUNS_CHANNEL,
            text=(
                f"{_who(facts)} · {_which(facts)} · {_cell_clause(facts)} · "
                f"{_array_spend_clause(facts)} on {_where(facts)}."
                f"{_failed_cell_clause(facts)}"
            ),
        )
    if facts.outcome == "succeeded":
        body = f"{_spend_clause(facts)} · ran {duration(facts.seconds_spent)} on {_where(facts)}."
    elif facts.outcome == "cancelled":
        body = (
            f"{_spent_only(facts)} · cancelled at {duration(facts.seconds_spent)} "
            f"on {_where(facts)}."
        )
    else:
        exit_clause = "no exit code, so the host went rather than the program"
        if facts.exit_code is not None:
            exit_clause = f"exit {facts.exit_code}"
        body = (
            f"{_spent_only(facts)}, nothing produced · died at "
            f"{duration(facts.seconds_spent)} on {_where(facts)}, {exit_clause}, "
            "whether a checkpoint survived is unknown."
        )
    return Message(channel=RUNS_CHANNEL, text=f"{_who(facts)} · {_which(facts)} · {body}")
