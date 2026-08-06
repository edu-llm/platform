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

**EVERY VALUE THAT CAME FROM SOMEBODY'S KEYBOARD GOES THROUGH :func:`escaped`, AND IT GOES
THROUGH IT BEFORE IT IS INTERPOLATED RATHER THAN AFTER THE LINE IS BUILT.** Slack reads
``&``, ``<`` and ``>`` as control characters, so an experiment named ``<!channel>`` is a
run that rings every phone in the workspace each time it ends, and a fan-out ending
sixty-four cells rings them sixty-four times. Nothing about that needs malice: an angle
bracket in a name is enough, and the same three characters break ordinary rendering in
duller ways.

The ordering is the part worth guarding. This module builds Slack control sequences of its
own -- the link in :func:`_how_to_answer` today, user mentions when there is an identity
map -- and a pass over the finished string would turn those into the entity codes a reader
sees rather than the link a reader clicks. Escaping each value on the way in leaves the
sequences this module wrote intact, because they are written after the escaping and never
pass through it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from ..accelerators import device_said, memory_said
from ..contracts.base import serialize_decimal
from .approval import ApprovalRequestedFacts
from .facts import RunEndedFacts
from .overnight import NAMED_FAILURES, Ended, OvernightFacts

__all__ = [
    "CHECKPOINT_CLAUSES",
    "NAMED_CELLS",
    "PRINTED_RUN_ID",
    "RUNS_CHANNEL",
    "Message",
    "duration",
    "escaped",
    "money",
    "plain",
    "render_approval_requested",
    "render_morning_page",
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


#: The whole of what Slack parses, and what each character becomes. Three and no more:
#: https://docs.slack.dev/messaging/formatting-message-text#escaping, which says in as many
#: words that only these are decoded again for display, so a fourth would be published as
#: the entity code rather than as the character somebody typed.
#:
#: A translation table rather than three chained ``str.replace`` calls, and the difference is
#: not style. Chained replacements are order-dependent in the one direction that fails
#: quietly: ``<`` before ``&`` writes ``&lt;`` and the next pass turns its ampersand into
#: ``&amp;lt;``, which reaches the channel as the literal text ``&lt;``. ``str.translate``
#: reads each character once and cannot substitute into its own output, so the ordering
#: mistake has nowhere to happen.
SLACK_ENTITIES: Final = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})


def escaped(value: str) -> str:
    """One value, made inert, on its way into a line this module is still assembling.

    CALLED PER FIELD AND NEVER OVER A FINISHED MESSAGE, WHICH IS THE ONE THING TO PRESERVE
    HERE. The rendered text legitimately holds Slack control sequences this module wrote --
    ``<url|label>`` below, and mentions once there is a map from a GitHub login to a chat
    account -- and a pass over the assembled string cannot tell those from an experiment
    named ``<!channel>``. It would publish the platform's own link as visible entity codes
    while doing nothing the per-field call has not already done.

    Idempotent it is not, and it is not meant to be: escaping an escaped value writes
    ``&amp;lt;`` where ``&lt;`` was. So each value passes through here exactly once, at the
    point it is interpolated, and every helper in this module returns text that has already
    been through it.

    A no-op for the overwhelming majority of values, which is why it is applied to reviewed
    configuration as readily as to a submitter's free text. ``gpu-8xa10g`` and ``A10G``
    carry none of the three characters and come back unchanged, so there is no cost to
    holding one rule rather than a judgement about each field.
    """
    return value.translate(SLACK_ENTITIES)


def money(value: Decimal | None) -> str:
    """A figure to the cent, or the words for not having one.

    Two reports of one figure at two precisions send somebody looking for a bug in the
    arithmetic, so everything is to the cent.
    """
    if value is None:
        return "cost unknown"
    return f"${value:,.2f}"


def plain(value: Decimal) -> str:
    """A decimal the way a person writes it, rather than the way pydantic holds it.

    ``StrictDecimal`` normalizes on the way in, so the reviewed bound ``"500"`` is held as
    ``Decimal("5E+2")`` and a ten-hour runtime as ``Decimal("1E+1")``. Interpolating either
    puts ``$5E+2`` in front of a lead at two in the morning, which defeats the reason the
    factors are shown at all. The same call ``submission._plain`` makes, so the approver page
    and this message cannot render one figure two ways.
    """
    return serialize_decimal(value)


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
    """The submitter's display name, which ``config/organization.yaml`` records free.

    ``OrganizationMember.display_name`` carries a length and no pattern, unlike the login
    beside it, so this is one of the two ways a name a person typed reaches the channel.
    """
    return escaped(facts.person) if facts.person else NOBODY_NAMED


def _which(facts: RunEndedFacts) -> str:
    """The experiment, and the reason every message in this module is escaped at all.

    IT IS READ OFF THE JOB AND NOT OFF ANY RULE. The value is ``WANDB_RUN_GROUP`` from the
    terminal event's own container environment, which is whatever the job was launched with.
    ``submission.compile_submission`` holds a new submission to ``SLUG_PATTERN``, and that
    says nothing about the runs already in the account: a rule added on a Thursday does not
    reach into a job admitted on the Monday, and this reader answers about both.
    """
    return escaped(facts.experiment) if facts.experiment else NO_EXPERIMENT


def _where(facts: RunEndedFacts) -> str:
    """The machine, named by its profile where one is known and by its queue where none is.

    The queue is named as a queue so a reader can tell the two apart. A bare queue name in
    the slot a profile usually fills reads as a profile nobody has heard of.
    """
    if facts.compute_profile is not None:
        return escaped(facts.compute_profile)
    if facts.queue_name is not None:
        return f"the {escaped(facts.queue_name)} queue"
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

#: The one clause that distinguishes a failure that lost everything from one that did not.
CHECKPOINT_CLAUSES: Final = {
    "written": "a checkpoint survived",
    "none": "no checkpoint written",
    "unknown": "whether a checkpoint survived is unknown",
}


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
            f"{CHECKPOINT_CLAUSES[facts.checkpoint_state]}."
        )
    return Message(channel=RUNS_CHANNEL, text=f"{_who(facts)} · {_which(facts)} · {body}")


# -----------------------------------------------------------------------------------------
# Asking somebody to release a run
# -----------------------------------------------------------------------------------------
#
# THE MONEY AND THE EXPERIMENT ARE IN THE FIRST LINE AND NOTHING GOES ABOVE THEM. The reader
# is a team lead at two in the morning who was woken by a phone, and what they decide on is
# whatever is on the screen before they have to scroll. Every other message in this file opens
# with the person, because a message about a run that happened is accountable to somebody. This
# one opens with the number, because the question is not who did it, it is whether to spend it.
#
# FIVE LINES AND NOT SIX. The first says what and how much, the second says where the figure
# came from, the third says whether the figure is credible, the fourth says why this person is
# being asked, and the fifth says how to answer. Anything else on the page is between a lead
# and the decision.

#: How many characters of a run id a message prints, the length ``cli/actions.py`` measured
#: and for the reason it measured: a run id is a UUIDv7 whose leading twelve hex digits are
#: the millisecond it was minted, so eight characters collide between two submissions inside
#: the same minute and thirteen is the whole of the clock.
PRINTED_RUN_ID: Final = len("run_") + 13

SECONDS_AN_HOUR_DECIMAL: Final = Decimal(SECONDS_AN_HOUR)

#: Above this, the multiple is printed as a whole number. A bound eleven and a half times the
#: median is a bound eleven times the median for every purpose a lead has at two in the
#: morning, and the decimal place reads as a precision the sample does not carry.
ROUND_MULTIPLE_ABOVE: Final = Decimal(10)


def _count(quantity: int, noun: str) -> str:
    return f"{quantity} {noun}" if quantity == 1 else f"{quantity} {noun}s"


def _arithmetic(facts: ApprovalRequestedFacts) -> str:
    """The five factors and the product, out of the object that multiplies them.

    NOT SPELLED HERE, WHICH IS THE WHOLE OF WHY THIS READS OFF ``CostInputs``. The product is
    that model's computed field and comes from ``compute_maximum_compute_cost_usd``, the one
    function in the tree that multiplies rate by nodes by hours by attempts by cells. A
    renderer that re-derived it would agree with the platform on every profile in today's
    catalog, because all of them are one machine, and would understate the first multi-node
    profile anybody registers by exactly the node count. This is the last place in the tree
    where a figure being quietly low is affordable.
    """
    if facts.cost is None:
        return (
            f"No execution target prices {escaped(facts.compute_profile)}, so nothing here "
            "can say what this may cost. Admission prices it again and will refuse it if "
            "the catalog does not carry the machine."
        )
    cost = facts.cost
    return (
        f"${plain(cost.hourly_rate_usd)}/hour x {_count(cost.nodes, 'node')} x "
        f"{plain(cost.maximum_runtime_hours)}h x {_count(cost.maximum_attempts, 'attempt')} x "
        f"{_count(cost.cells, 'cell')}. A ceiling rather than an estimate."
        f"{_over_the_profile(facts)}"
    )


def _over_the_profile(facts: ApprovalRequestedFacts) -> str:
    """Whether the hours asked for are more than the workload says its runs take.

    A TYPED-IN ZERO PRICES AS CLEANLY AS A PLAN AND READS THE SAME. ``olmo-core-train``
    declares twenty-four hours, a submission naming ten thousand compiles clean, and the
    arithmetic on the line above is correct at $10,520 on a T4. Nothing else on this message
    would tell a lead that a bound had been crossed, because the crossing is between two
    numbers only one of which is on the page.

    Empty for every submission at or under the bound, which is nearly all of them. A clause
    saying "within the profile's bound" on every message is a clause that stops being read
    before the one that matters arrives.
    """
    if facts.cost is None or facts.profile_hours is None:
        return ""
    if facts.cost.maximum_runtime_hours <= facts.profile_hours:
        return ""
    return (
        f" It asks for {plain(facts.cost.maximum_runtime_hours)}h where "
        f"{escaped(facts.workload_profile)} declares {plain(facts.profile_hours)}h."
    )


def _multiple(bound_hours: Decimal, median_seconds: Decimal) -> str:
    multiple = bound_hours * SECONDS_AN_HOUR_DECIMAL / median_seconds
    if multiple >= ROUND_MULTIPLE_ABOVE:
        return f"{multiple.quantize(Decimal(1))}"
    return f"{multiple.quantize(Decimal('0.1'))}"


def _credible(facts: ApprovalRequestedFacts) -> str:
    """Whether the ceiling is one this shape has ever needed, which is the whole discriminator.

    AN EXPENSIVE RUN THAT IS CORRECT AND AN EXPENSIVE RUN THAT IS A TYPO RENDER IDENTICALLY
    WITHOUT THIS LINE, and an approval where both look the same is a formality. The five
    factors above say which of them is large; they cannot say whether the large one is
    right. ``24.0h`` looks the same on a shape that genuinely runs a day and on one that has
    never taken more than forty minutes, and the second is what a submitter typing the
    maximum into a form produces.

    The measurement is the median over runs of this shape that succeeded, which is the same
    reading ``config/run-history.json`` gives the approver page. The count is printed beside
    it so a lead can discount a thin one themselves rather than being handed a figure with
    no denominator.
    """
    shape, cost = facts.shape, facts.cost
    if not shape.was_read:
        return (
            "No run history is packaged with this notifier, so nothing here checks the "
            "ceiling against what the shape has taken."
        )
    if shape.median_seconds is None or cost is None:
        if shape.succeeded == 1:
            return "One run of this shape has succeeded here, too few to say what it takes."
        if shape.succeeded:
            return (
                f"Only {shape.succeeded} runs of this shape have succeeded here, too few to "
                "say what it takes."
            )
        return (
            "No run of this shape has succeeded here, so there is nothing to check the "
            "ceiling against."
        )
    took = duration(int(shape.median_seconds))
    over = _count(shape.succeeded, "run")
    if cost.maximum_runtime_hours * SECONDS_AN_HOUR_DECIMAL < shape.median_seconds:
        return (
            f"{escaped(shape.said_of)} has taken {took} over {over}, which is longer than "
            f"the {plain(cost.maximum_runtime_hours)}h bound, so this one is likely to be "
            "cut off at the bound."
        )
    multiple = _multiple(cost.maximum_runtime_hours, shape.median_seconds)
    return (
        f"{escaped(shape.said_of)} has taken {took} over {over}, and the "
        f"{plain(cost.maximum_runtime_hours)}h bound is {multiple} times that."
    )


def _why_this_gate(facts: ApprovalRequestedFacts) -> str:
    """The reason this request needs a person, named rather than asserted.

    A LEAD HAS TO BE ABLE TO TELL WHY THEY ARE THE ONE BEING ASKED. "Approval required" is a
    sentence about a gate; "a fan-out is never released automatically, whatever it costs" is
    a sentence about this run, and only the second one lets somebody notice that the rule
    being applied is the wrong rule.

    The order matches ``contracts.policy.classify_request`` exactly, because the first test
    that holds is the one that decided the route and a message naming a later one would name
    a reason that was never reached. ``tests/test_notification_messages.py`` walks both in
    step so they cannot drift.
    """
    if facts.approval_class == "exception":
        return (
            f"exception, {escaped(facts.gate)}, because a capacity block is a dated purchase "
            "of reserved machines and a platform admin is who buys one. No lead is being "
            "asked for this."
        )
    if facts.approval_class == "automatic":
        return (
            f"automatic, {escaped(facts.gate)}, released by nobody. This is here so the "
            "channel has the record, and it is not waiting on anybody."
        )
    if facts.is_a_fanout:
        return (
            f"routine, {escaped(facts.gate)}, because a fan-out is never released "
            "automatically, whatever it costs."
        )
    if facts.scan_unreviewed:
        return (
            f"routine, {escaped(facts.gate)}, because this image digest carries registry "
            "findings nobody has recorded a review of."
        )
    if facts.cost is None:
        return (
            f"routine, {escaped(facts.gate)}, because nothing here could price the machine, "
            "and a request nobody can price is never released by nobody."
        )
    if facts.cost.maximum_compute_cost_usd < facts.automatic_below_cost_usd:
        return (
            f"routine, {escaped(facts.gate)}, because one of this request's inputs did not "
            "resolve. Admission refuses those outright, so releasing it buys a refusal "
            "rather than a run."
        )
    return (
        f"routine, {escaped(facts.gate)}, because "
        f"{money(facts.cost.maximum_compute_cost_usd)} is not under the "
        f"{money(facts.automatic_below_cost_usd)} nobody releases."
    )


def _routing(facts: ApprovalRequestedFacts) -> str:
    """Which people this run points at, and the fact that it points rather than restricts.

    NAMING THE WRONG SET IS WORSE THAN NAMING NONE, WHICH IS WHY THE CLASS DECIDES THIS AND
    NOT ONLY THE TEAM. An exception goes to the admin gate, and a message that printed a
    team's leads beside it would tell eight people a run was theirs to release when the gate
    will not open for any of them. Every message goes to one channel, so a lead reads all of
    them and has to be able to tell which are theirs in one line.

    Naming anybody invites the reading that they are the only person who may act, which would
    make an absent lead a stuck run. The gate admits any holder of the role, so the second
    sentence is what makes the first safe to print. It is the same pair
    ``submission._routing_note`` puts on the approver page, in one line instead of two.
    """
    if facts.approval_class == "automatic":
        return ""
    if facts.approval_class == "exception":
        if not facts.admins:
            return "No platform admin is recorded, so nothing here can say who releases it."
        admins = ", ".join(escaped(admin) for admin in facts.admins)
        return f"Any platform admin releases it, which is {admins}."
    if not facts.leads:
        return f"Team {escaped(facts.team)} records no lead. Any team lead may release it."
    named = ", ".join(escaped(lead) for lead in facts.leads)
    return f"Team {escaped(facts.team)} routes to {named}. Any team lead may release it."


def _the_machine(facts: ApprovalRequestedFacts) -> str:
    """What the shape being paid for actually carries, as the first line's last field.

    ON THE FIRST LINE BECAUSE THAT IS WHERE THE SPEND IS, AND THE SPEND IS WHAT IS BEING
    RELEASED. The four fields before it say how much, on what, by whom and where; this says
    what "where" holds. A lead sizing a request has the money and the machine in one glance
    and does not have to know the catalog by heart to turn ``gpu-8xa100`` into 320 GiB.

    ``x`` rather than ``×``, matching the arithmetic on the line directly below. One message
    spelling the same operator two ways reads as two authors.

    MIB AND NOT GB, WHICH IS THE WHOLE REASON THE MEASUREMENT EXISTS. The A10G and the L4
    are sold as 24 GB cards and report 22,888 MiB, so a lead who reads "24 GB" and a
    submitter who sized a batch against 24 GiB are 1.65 GiB apart before the run starts.
    ``config/accelerators.yaml`` records what ``describe-instance-types`` answered, in the
    unit it answered in, and this prints that.

    THE PER-DEVICE FIGURE IS DROPPED ON A ONE-CARD SHAPE AND NOT ROUNDED OR REPEATED. On
    ``gpu-1xa10g`` the two numbers are the same number, and printing it twice is the
    invitation to look for the arithmetic error that :func:`money` exists to avoid.

    A profile with no row yields nothing at all rather than a stated unknown, which is the
    shape :func:`_over_the_profile` above already takes for its own absence. Every priced
    profile appears in the file and ``tests/test_accelerators.py`` holds the two level, so
    the only way here is a packaged copy that has parted company with the catalog -- a fact
    about the deployment with nothing in it about this run.

    WHAT THIS IS NOT IS A FIT CHECK, AND IT MUST NOT GROW INTO ONE HERE. Whether a model
    fits needs the model's size, and no submission carries one:
    ``schemas/submission-inputs.schema.json`` has no such property and neither does
    ``RunManifest``. A warning inferred from a free-text command would be a guess presented
    as a finding, in the one message where a guess costs somebody's approval.

    THE ESCAPING IS APPLIED HERE AND NOT INSIDE ``device_said``. ``config/accelerators.yaml``
    holds the card name as a non-empty string and enforces no pattern on it, and the same two
    functions write the table in ``guides/olmo-core.md``. A card named with an ampersand would
    have to reach that table as an ampersand and this channel as ``&amp;``, so the encoding
    belongs to the surface rather than to the reading.
    """
    record = facts.accelerator
    if record is None:
        return ""
    if record.device is None:
        return " · no accelerator"
    if record.devices == 1:
        return f" · {escaped(device_said(record))}, {escaped(memory_said(record))}"
    return (
        f" · {escaped(device_said(record))}, {record.memory_mib_per_device:,} MiB each, "
        f"{escaped(memory_said(record))} total"
    )


def _how_to_answer(facts: ApprovalRequestedFacts) -> str:
    """Where to act, and what a decline leaves behind after the run page is gone.

    THE SECOND SENTENCE IS THE ONE THAT MATTERS AND IT IS THERE BECAUSE OF WHAT A DECLINE
    COSTS. GitHub takes a reason when a deployment review is rejected and puts it on the run
    page, and a workflow run is retained for ninety days and then is not there. So a lead who
    declines and types nothing leaves a submitter with a refused run and no sentence, and a
    lead who types one leaves it somewhere that expires. Naming ``edullm status`` here is
    what makes the reason reachable from a terminal for as long as GitHub holds the run, and
    the notifier posts the decline into this channel, which is where it outlives the run page.

    THE ONE SLACK CONTROL SEQUENCE THIS MODULE BUILDS, AND THE REASON THE ESCAPING IS PER
    FIELD RATHER THAN OVER THE FINISHED LINE. ``<url|label>`` is Slack's documented form for
    a link and its angle brackets are ours, written after :func:`escaped` has run over the
    address inside them. A pass over the assembled message would encode those two brackets
    along with everything else and publish ``&lt;https://…&gt;`` as text nobody can click,
    which is a link lost to the fix rather than to the defect.

    The address is escaped even though the platform minted it. It is a GitHub run URL and
    carries none of the three characters, so this costs nothing and means the rule holds
    without anybody having to be sure where each value came from.

    The run id is escaped like any other value, because the backticks around it are code
    formatting and not a control sequence, and because this path is the one that does not
    check its shape: ``read_run_ended`` holds a job name to ``RUN_ID_REGEX`` before it says
    anything, and ``read_approval_requested`` takes ``run_id`` off the envelope as free JSON
    text. Sliced before it is escaped, because the other order can cut an entity in half and
    leave ``&am`` in front of a lead.
    """
    where = (
        f"<{escaped(facts.url)}|Release or decline it>"
        if facts.url
        else "Release or decline it in Actions"
    )
    return (
        f"{where}. A decline takes a reason, and that reason is what `edullm status "
        f"{escaped(facts.run_id[:PRINTED_RUN_ID])}` prints back to whoever submitted this."
    )


def _experiment(facts: ApprovalRequestedFacts) -> str:
    """The grouping key, out of the envelope's own JSON and not out of any rule.

    ``compile_submission`` holds a new submission's experiment to ``SLUG_PATTERN`` and
    ``read_approval_requested`` reads the value back as free text, which is the right way
    round: this reader answers about documents written before that check existed as well as
    after it, and a reader that assumed the shape would be the one thing between a typed
    angle bracket and every phone in the workspace.
    """
    return escaped(facts.experiment) if facts.experiment else NO_EXPERIMENT


def _named_person(facts: ApprovalRequestedFacts) -> str:
    """Who is asking, from the roster first and from the envelope second.

    Only the second of the two is held to a pattern. ``GitHubLogin`` admits letters, digits
    and hyphens; ``OrganizationMember.display_name``, which is what the roster answers with,
    admits anything at least one character long. Escaped together, because which of the two
    answered is not a fact this line should have to know.
    """
    named = facts.person or facts.submitter
    return escaped(named) if named else NOBODY_NAMED


def render_approval_requested(facts: ApprovalRequestedFacts) -> Message:
    """One run waiting on a person, as the five lines that decide it.

    FOUR OF THE FIRST LINE'S FIVE FIELDS ARE READ OUT OF THE ENVELOPE AS FREE JSON TEXT.
    ``read_approval_requested`` takes the manifest as a mapping rather than through
    ``RunManifest``, deliberately and for a reason its own docstring gives, so the checks
    that model carries are not in force here and would not exclude these three characters if
    they were: ``repository``, ``team``, ``compute_profile`` and ``workload_profile`` are
    each ``Field(min_length=1)`` and nothing more.
    """
    total = money(None if facts.cost is None else facts.cost.maximum_compute_cost_usd)
    shape = f"{escaped(facts.repository)} on {escaped(facts.compute_profile)}"
    if facts.is_a_fanout:
        shape += f", {facts.cells} cells"
    return Message(
        channel=RUNS_CHANNEL,
        text="\n".join(
            (
                (
                    f"{total} · {_experiment(facts)} · {_named_person(facts)} · {shape}"
                    f"{_the_machine(facts)}"
                ),
                _arithmetic(facts),
                _credible(facts),
                f"{_why_this_gate(facts)} {_routing(facts)}".rstrip(),
                _how_to_answer(facts),
            )
        ),
    )


# -----------------------------------------------------------------------------------------
# The morning page
# -----------------------------------------------------------------------------------------
#
# ONE PAGE, READ IN TWO MINUTES, ANSWERING WHAT HAPPENED OVERNIGHT. Everything on it is
# measured. The approval message above names ceilings because a ceiling is what is being
# authorised; by morning the night is over and a ceiling is the wrong number. A page reporting
# authorised totals would describe an account spending several times what it spent.


def _named(job: Ended) -> str:
    """A finished job, short enough that five of them fit on a line somebody reads.

    A platform run is abbreviated to the whole of its timestamp, which is what every other
    surface prints. A job somebody named by hand keeps the name they gave it, because that is
    the only handle it has.

    The name a person gave it is the one value on this page that did not come from this
    platform, and it is the one Batch already constrains: ``SubmitJob`` takes letters,
    digits, hyphens and underscores and refuses the rest, so no job in the account can be
    called ``<!channel>``. Escaped anyway, because that is a promise another service makes
    about a field this reads out of its answer, and holding the rule costs a call that
    changes nothing on every name the account has.
    """
    identity = escaped(job.name[:PRINTED_RUN_ID] if job.is_a_run else job.name)
    ending = f"exit {job.exit_code}" if job.exit_code is not None else "no exit code"
    machine = escaped(job.compute_profile or job.queue)
    return f"{identity} ({ending}, {duration(job.seconds)} on {machine})"


def _budget(facts: OvernightFacts) -> str:
    """What the night cost, and what the figure is and is not.

    MEASURED AND NOT AUTHORISED, SAID RATHER THAN LEFT TO BE INFERRED. Every other money
    figure a lead has seen this week is a ceiling, so a bare total here would be read as one
    and the account would look several times more expensive than it is.
    """
    spent = money(facts.spent_usd)
    if not facts.ended:
        return f"{spent} spent, because nothing ran."
    shortfalls = []
    if facts.queues_read != facts.queues_asked:
        shortfalls.append(
            f"{facts.queues_asked - facts.queues_read} of the {facts.queues_asked} queues "
            "could not be listed"
        )
    if facts.unpriced:
        shortfalls.append(
            f"{facts.unpriced} of the jobs ran on a queue nothing prices"
        )
    measured = (
        f"{spent} spent, measured from what the machines ran rather than from what anybody "
        "authorised."
    )
    if not shortfalls:
        return measured
    return f"{measured} A floor rather than a total, because {' and '.join(shortfalls)}."


def render_morning_page(facts: OvernightFacts) -> Message:
    """The overnight window as one page, in the order somebody reads it at eight."""
    if not facts.ended and not facts.running and not facts.waiting:
        return Message(
            channel=RUNS_CHANNEL,
            text=(
                f"Overnight, {facts.hours}h: nothing ran and nothing is queued. "
                f"{facts.queues_read} of {facts.queues_asked} queues answered."
            ),
        )

    failed = facts.failed
    lines = [
        (
            f"Overnight, {facts.hours}h: {_count(len(facts.ended), 'job')} ended and "
            f"{facts.succeeded} of them worked."
        ),
        _budget(facts),
    ]
    if failed:
        named = ", ".join(_named(job) for job in failed[:NAMED_FAILURES])
        more = (
            f", and {len(failed) - NAMED_FAILURES} more" if len(failed) > NAMED_FAILURES else ""
        )
        lines.append(f"{len(failed)} failed. {named}{more}.")
    if facts.running or facts.waiting:
        lines.append(
            f"Right now {facts.running} running and {facts.waiting} waiting for a machine."
        )
    return Message(channel=RUNS_CHANNEL, text="\n".join(lines))
