"""Nothing a person typed can be read by Slack as an instruction.

**WHAT THIS IS ABOUT, MEASURED RATHER THAN IMAGINED.** ``experiment`` is chosen by the
submitter and reaches the runs channel on every ending, ``<!channel>`` is Slack's own syntax
for notifying every member of a channel, and nothing between the two escaped anything. A run
named that way rings the whole workspace each time it ends and a fan-out of sixty-four cells
rings it sixty-four times, with no malice required: an angle bracket in a name is enough, and
``&`` and ``<`` also break ordinary rendering in duller ways.

**THE ORDERING IS THE PART A FIX GETS BACKWARDS.** Escaping the assembled message would be
the obvious shape and it destroys the link ``_how_to_answer`` builds, because that link's
angle brackets are Slack control characters this platform wrote on purpose.
:func:`test_the_link_the_platform_builds_is_still_a_link` is the test that fails when
somebody makes that trade, and it is the reason the escaping is per field.

**TWO NETS OVER A NEW FIELD, BECAUSE NEITHER ONE COVERS THE OTHER'S HOLE.**
:func:`test_no_message_carries_a_string_nobody_escaped` poisons every string on every facts
object and reads the rendered text, so it catches whatever the exercised branches actually
print, including values reached through a helper. It cannot reach a branch no variant below
takes. :func:`test_every_interpolated_string_field_goes_through_the_escaper` reads the source
instead and holds every branch at once, and it cannot see a value that was put in a local
first. A field added carelessly has to miss both.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
from decimal import Decimal
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

import pytest

from edullm_platform.accelerators import AcceleratorRecord
from edullm_platform.notifications.approval import (
    ApprovalRequestedFacts,
    Shape,
    load_accelerators,
    load_policy,
    read_approval_requested,
)
from edullm_platform.notifications.facts import Catalogs, RunEndedFacts, read_run_ended
from edullm_platform.notifications.messages import (
    Message,
    escaped,
    render_approval_requested,
    render_morning_page,
    render_run_ended,
)
from edullm_platform.notifications.overnight import Ended, OvernightFacts, read_overnight
from edullm_platform.run_history import load_run_history

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT_ROOT / "config"
EVENTS = PROJECT_ROOT / "fixtures" / "events"
MESSAGES = PROJECT_ROOT / "src" / "edullm_platform" / "notifications" / "messages.py"

#: Slack's own table, copied from the guidance rather than remembered:
#: https://docs.slack.dev/messaging/formatting-message-text#escaping. Three rows and no
#: fourth, and the page says why a fourth would be wrong -- only these are decoded again for
#: display, so anything else escaped here would reach the channel as its entity code.
SLACK_SAYS = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))

#: Somebody's whole workspace, in eleven characters. The escaped form of this string is what
#: every assertion below looks for, because a message carrying it inert is a message that
#: named the experiment and rang nobody.
RINGS_EVERY_PHONE = "<!channel>"

NOW = 1_785_984_000_000


@pytest.fixture(scope="module")
def catalogs() -> Catalogs:
    return Catalogs.load(CONFIG)


def envelope(name: str) -> dict[str, Any]:
    text = (EVENTS / f"{name}.sanitized.json").read_text(encoding="utf-8")
    return dict(json.loads(text))


def run_ended(delivery: dict[str, Any], catalogs: Catalogs) -> RunEndedFacts:
    facts = read_run_ended(delivery, catalogs=catalogs)
    assert facts is not None
    return facts


def approval(delivery: dict[str, Any], catalogs: Catalogs) -> ApprovalRequestedFacts:
    facts = read_approval_requested(
        delivery,
        catalogs=catalogs,
        policy=load_policy(CONFIG),
        history=load_run_history(CONFIG),
        accelerators=load_accelerators(CONFIG),
    )
    assert facts is not None
    return facts


def overnight(delivery: dict[str, Any], catalogs: Catalogs, summaries: list[Any]) -> OvernightFacts:
    class Listing:
        def list_jobs(self, **arguments: Any) -> Any:
            queue = arguments["jobQueue"]
            return {"jobSummaryList": summaries if queue.endswith("-cpu") else []}

    facts = read_overnight(delivery, catalogs=catalogs, cell_lister=Listing(), now_ms=NOW)
    assert facts is not None
    return facts


def with_an_experiment_of(name: str, delivery: dict[str, Any]) -> dict[str, Any]:
    """The Batch event with ``WANDB_RUN_GROUP`` set, which is where the experiment travels.

    Set on the container environment rather than on a tag, because that variable is what
    ``facts.read_run_ended`` reads and what ``execution.py`` writes beside the tag.
    """
    environment = delivery["detail"]["container"]["environment"]
    delivery["detail"]["container"]["environment"] = [
        entry for entry in environment if entry["name"] != "WANDB_RUN_GROUP"
    ] + [{"name": "WANDB_RUN_GROUP", "value": name}]
    return delivery


# -----------------------------------------------------------------------------------------
# The hazard, and the thing the fix must not cost
# -----------------------------------------------------------------------------------------


def test_escaped_converts_what_slack_says_and_nothing_else(catalogs: Catalogs) -> None:
    """The three characters, and the reason there is no fourth.

    Over-escaping is the failure in the other direction and it is not hypothetical: Slack
    decodes exactly these three back for display, so a quote or an apostrophe encoded here
    would arrive in the channel as ``&#39;``. Every other printable character has to survive
    this untouched, which is what the second half asserts.
    """
    del catalogs
    for character, entity in SLACK_SAYS:
        assert escaped(character) == entity

    survives = "'\"`*_~:@#|/\\[](){}!?$%^+=,.;-–—·ø愛"
    assert escaped(survives) == survives


def test_an_experiment_named_channel_rings_nobody(catalogs: Catalogs) -> None:
    """THE WHOLE POINT, ON THE MESSAGE THAT IS ACTUALLY BEING DELIVERED TODAY.

    Equality on the whole line rather than a substring, matching the discipline in
    ``tests/test_notification_messages.py``: a containment check passes on a message that
    also carries the live form somewhere else in it.
    """
    delivery = with_an_experiment_of(RINGS_EVERY_PHONE, envelope("batch-succeeded"))

    message = render_run_ended(run_ended(delivery, catalogs))

    assert message.text == (
        "Aryan Verma · &lt;!channel&gt; · $0.02 spent, $2.01 authorised · "
        "ran 1m on gpu-1xa10g."
    )
    assert RINGS_EVERY_PHONE not in message.text


@pytest.mark.parametrize(
    "typed",
    [
        "<!channel>",
        "<!here>",
        "<!everyone>",
        "<@U012AB3CD>",
        "<!subteam^SAZ94GDB8>",
        "a<b>c&d",
    ],
)
def test_no_shape_of_mention_survives_an_experiment(typed: str, catalogs: Catalogs) -> None:
    """The four shapes that notify somebody, and the two characters that only break rendering.

    Parametrized over the shapes rather than asserted once on ``<!channel>``, because the
    thing being relied on is that the escaping is about the characters and not about the
    words between them. A fix that special-cased the four names Slack documents would pass a
    single-case test and would still be one Slack release from being wrong.
    """
    delivery = with_an_experiment_of(typed, envelope("batch-succeeded"))

    text = render_run_ended(run_ended(delivery, catalogs)).text

    assert "<" not in text
    assert ">" not in text
    assert "&amp;" in text if "&" in typed else True


def test_the_link_the_platform_builds_is_still_a_link(catalogs: Catalogs) -> None:
    """THE TEST THAT FAILS IF THE ESCAPING MOVES TO THE ASSEMBLED STRING.

    ``<url|label>`` is Slack's documented form for a link and the two angle brackets are
    control characters this module writes on purpose. A pass over the finished message
    cannot tell them from a submitter's, so it would publish ``&lt;https://…&gt;`` and the
    lead being asked for money would have nothing to click. This asserts the live form and
    the escaped form's absence in the same breath, because a fix that escaped twice would
    still contain the address.
    """
    delivery = envelope("approval-requested")
    url = delivery["detail"]["url"]
    assert url.startswith("https://")

    text = render_approval_requested(approval(delivery, catalogs)).text

    assert f"<{url}|Release or decline it>" in text
    assert "&lt;" not in text
    assert "&gt;" not in text


def test_an_experiment_and_the_platform_s_link_are_both_right_at_once(
    catalogs: Catalogs,
) -> None:
    """The two halves together, which is the state neither ordering reaches on its own.

    Escaping after assembly gets the first assertion and loses the second. Escaping nothing
    gets the second and loses the first. Only escaping each value on the way in gets both,
    and the whole reason this file exists is that the difference is invisible in the diff.
    """
    delivery = envelope("approval-requested")
    delivery["detail"]["experiment"] = RINGS_EVERY_PHONE
    url = delivery["detail"]["url"]

    text = render_approval_requested(approval(delivery, catalogs)).text

    assert "&lt;!channel&gt;" in text
    assert RINGS_EVERY_PHONE not in text
    assert f"<{url}|Release or decline it>" in text


def test_an_ampersand_is_escaped_once_and_not_twice(catalogs: Catalogs) -> None:
    """The over-escaping failure, which reads as a bug in the platform rather than a risk.

    ``&`` has to become ``&amp;`` and must not become ``&amp;amp;``. It is what a second
    pass produces, it is what chaining the three replacements in the wrong order produces
    for ``<``, and either way the channel shows a reader the entity code instead of the
    character somebody typed.
    """
    delivery = with_an_experiment_of("regmix&dolma", envelope("batch-succeeded"))

    text = render_run_ended(run_ended(delivery, catalogs)).text

    assert "regmix&amp;dolma" in text
    assert "&amp;amp;" not in text


# -----------------------------------------------------------------------------------------
# The first net: poison every string a facts object holds, and read what came out
# -----------------------------------------------------------------------------------------
#
# The escaping is a property of the rendered text rather than of any one line, so this walks
# the facts objects instead of naming their fields. Anything added to one of them is poisoned
# by construction, and a renderer that prints it raw fails here without anybody remembering
# to extend a list.

#: The classes a message is built out of. Every string on any of them, or on any dataclass
#: they hold, is a string that can reach the channel.
FACTS_CLASSES = (RunEndedFacts, ApprovalRequestedFacts, Shape, Ended, OvernightFacts,
                 AcceleratorRecord)

#: Which of those strings the variants below must actually be seen to render. Without this
#: the whole file would pass on a renderer that had stopped naming anybody: no raw poison is
#: trivially true of a message that dropped every field. It is also the answer to "which
#: fields carry text somebody typed", written where it can go stale loudly.
REACHES_THE_CHANNEL = frozenset(
    {
        "person",
        "experiment",
        "compute_profile",
        "queue_name",
        "submitter",
        "team",
        "repository",
        "workload_profile",
        "gate",
        "url",
        "run_id",
        "said_of",
        "leads",
        "admins",
        "device",
        "name",
        "queue",
    }
)


def admits_a_string(annotation: Any) -> bool:
    """Whether a value of this type can be a string a person wrote.

    ``Literal["written", "none", "unknown"]`` deliberately does not, and that is the one
    exclusion worth naming. ``RunEndedFacts.checkpoint_state`` and ``.outcome`` are literals
    whose values index a constant table in the renderer, so what reaches the text is the
    table's own sentence and never the field. Poisoning them would raise a ``KeyError``
    rather than find a defect.
    """
    if annotation is str:
        return True
    if get_origin(annotation) in (Union, UnionType):
        return any(argument is str for argument in get_args(annotation))
    return False


def a_tuple_of_strings(annotation: Any) -> bool:
    return get_origin(annotation) is tuple and get_args(annotation)[:1] == (str,)


def poisoned(subject: Any, *, touched: set[str]) -> Any:
    """The same object with ``<!channel>&<field>`` in place of every string it holds.

    A distinct value per field, so a failure names the field that got through rather than
    reporting that something did. Recursive, because ``ApprovalRequestedFacts`` holds a
    ``Shape`` and an ``AcceleratorRecord`` and ``OvernightFacts`` holds a tuple of ``Ended``,
    and a field added to any of those is a field that reaches the channel.
    """
    hints = get_type_hints(type(subject))
    changes: dict[str, Any] = {}
    for field in dataclasses.fields(subject):
        annotation = hints[field.name]
        current = getattr(subject, field.name)
        if admits_a_string(annotation):
            changes[field.name] = f"{RINGS_EVERY_PHONE}&{field.name}"
            touched.add(field.name)
        elif a_tuple_of_strings(annotation):
            changes[field.name] = (f"{RINGS_EVERY_PHONE}&{field.name}",)
            touched.add(field.name)
        elif dataclasses.is_dataclass(current) and not isinstance(current, type):
            changes[field.name] = poisoned(current, touched=touched)
        elif isinstance(current, tuple) and current and dataclasses.is_dataclass(current[0]):
            changes[field.name] = tuple(poisoned(item, touched=touched) for item in current)
    return dataclasses.replace(subject, **changes)


def every_variant(catalogs: Catalogs, touched: set[str]) -> list[tuple[str, Message]]:
    """One poisoned render per branch that words a value, named so a failure says which.

    Spread across branches rather than taken once, because a renderer only prints what its
    conditionals reach: the queue is named only where no profile is, the machine is named in
    prose only where nothing prices it, and the admins are named only under the exception
    gate. A single fixture would leave three quarters of the interpolations unexercised.

    ``outcome`` AND ``approval_class`` ARE PUT BACK AFTER THE POISONING, WHICH IS NOT AN
    EXEMPTION FROM IT. Both are strings the renderer compares against constants rather than
    prints, so poisoning them is a real check that they are never printed -- and it also
    sends every message down its last ``else``, which is how the exception routing and the
    succeeded wording went unexercised in the first draft of this file. Poisoned everywhere,
    restored on the variants whose whole purpose is the branch they choose.
    """
    ended = poisoned(run_ended(envelope("batch-succeeded"), catalogs), touched=touched)
    worked = dataclasses.replace(ended, outcome="succeeded")
    failed = poisoned(run_ended(envelope("batch-failed"), catalogs), touched=touched)
    array = poisoned(run_ended(envelope("batch-array-parent-failed"), catalogs), touched=touched)
    asked = poisoned(approval(envelope("approval-requested"), catalogs), touched=touched)
    fanout = poisoned(approval(envelope("approval-requested-fanout"), catalogs), touched=touched)
    block = poisoned(
        approval(envelope("approval-requested-capacity-block"), catalogs), touched=touched
    )
    night = poisoned(
        overnight(
            envelope("overnight-activity"),
            catalogs,
            [
                {
                    "jobName": "validator-preflight-091",
                    "status": "FAILED",
                    "startedAt": 1_000_000,
                    "stoppedAt": 1_600_000,
                    "container": {"exitCode": 1},
                }
            ],
        ),
        touched=touched,
    )
    return [
        ("a run that worked", render_run_ended(worked)),
        ("a run that died", render_run_ended(failed)),
        ("a run somebody cancelled", render_run_ended(dataclasses.replace(ended, outcome="cancelled"))),
        ("a fan-out that lost cells", render_run_ended(array)),
        (
            "a run on a queue nothing prices",
            render_run_ended(dataclasses.replace(worked, compute_profile=None)),
        ),
        ("an approval", render_approval_requested(asked)),
        ("a fan-out approval", render_approval_requested(fanout)),
        (
            "a capacity block",
            render_approval_requested(dataclasses.replace(block, approval_class="exception")),
        ),
        (
            "a release nobody has to make",
            render_approval_requested(dataclasses.replace(asked, approval_class="automatic")),
        ),
        (
            "an approval the roster could not name a person for",
            render_approval_requested(dataclasses.replace(asked, person=None)),
        ),
        (
            "an approval over its workload's declared hours",
            render_approval_requested(dataclasses.replace(asked, profile_hours=Decimal(1))),
        ),
        (
            "an approval nothing could price",
            render_approval_requested(dataclasses.replace(asked, cost=None, profile_hours=None)),
        ),
        (
            "an approval whose team records no lead",
            render_approval_requested(dataclasses.replace(asked, leads=())),
        ),
        (
            "an approval with no run page",
            render_approval_requested(dataclasses.replace(asked, url=None)),
        ),
        ("the morning page", render_morning_page(night)),
    ]


def test_no_message_carries_a_string_nobody_escaped(catalogs: Catalogs) -> None:
    """THE GUARD THAT SURVIVES A FIELD ADDED NEXT MONTH BY SOMEBODY WHO HAS NOT READ THIS.

    Every string on every facts object is replaced with ``<!channel>&<field>`` and every
    message is rendered from the result. A value that reached the text unescaped is a value
    that would have rung the workspace, and the name in the poison says which field it was.
    """
    touched: set[str] = set()

    for described, message in every_variant(catalogs, touched):
        for field in sorted(touched):
            live = f"{RINGS_EVERY_PHONE}&{field}"
            assert live not in message.text, (
                f"{described} prints {field} without escaping it, so a run whose {field} "
                f"reads {live!r} notifies the whole workspace. Wrap it in "
                f"messages.escaped() where it is interpolated, not after the line is built."
            )
        assert RINGS_EVERY_PHONE not in message.text, described


def test_the_poisoned_render_actually_exercises_the_fields_that_reach_the_channel(
    catalogs: Catalogs,
) -> None:
    """The check above passes trivially on a renderer that says nothing, so this is the floor.

    Each name here is a field that came out of a message escaped, which is the list this
    change was written from: what carries text somebody typed, as opposed to what is a
    number, a literal or a sentence this module owns.
    """
    touched: set[str] = set()
    rendered = "\n".join(message.text for _, message in every_variant(catalogs, touched))

    seen = {
        field
        for field in touched
        if f"&lt;!channel&gt;&amp;{field}" in rendered
    }

    assert REACHES_THE_CHANNEL <= seen, (
        "these fields no longer reach the channel escaped, so either the wording dropped "
        f"them or the escaping did: {sorted(REACHES_THE_CHANNEL - seen)}"
    )


# -----------------------------------------------------------------------------------------
# The second net: the source, where every branch is present at once
# -----------------------------------------------------------------------------------------


def string_fields_of(subject: type) -> set[str]:
    """Every field and property of one facts class whose value can be a string.

    Properties as well as fields, because ``Ended.is_a_run`` and ``ApprovalRequestedFacts.
    cells`` are read in f-strings exactly as fields are, and a string-valued one added later
    would be interpolated the same way.
    """
    hints = get_type_hints(subject)
    found = {
        name
        for name, annotation in hints.items()
        if admits_a_string(annotation) or a_tuple_of_strings(annotation)
    }
    for name, value in inspect.getmembers(subject, lambda member: isinstance(member, property)):
        returns = get_type_hints(value.fget).get("return") if value.fget else None
        if admits_a_string(returns) or a_tuple_of_strings(returns):
            found.add(name)
    return found


def escapes_the_whole_thing(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == escaped.__name__
    )


def test_every_interpolated_string_field_goes_through_the_escaper() -> None:
    """A field read straight into an f-string has to be one that cannot hold a string.

    READS THE SOURCE BECAUSE THE RENDER ONLY COVERS THE BRANCHES A FIXTURE REACHES. The
    poison above walks eleven variants and there are more conditionals than that in the
    module; this holds every branch at once, at the cost of seeing only what is written
    inside the braces. The two together are why a careless field has to slip past both.

    What it cannot see is a value assigned to a local first, which is deliberate rather than
    overlooked: ``_routing`` and ``_named`` both do exactly that, having escaped the value on
    the line above. Reporting those would mean teaching this to follow assignments, and a
    check nobody can read is a check somebody deletes.
    """
    strings: set[str] = set()
    for subject in FACTS_CLASSES:
        strings |= string_fields_of(subject)
    assert "experiment" in strings, "the derivation found nothing, so the check is vacuous"

    tree = ast.parse(MESSAGES.read_text(encoding="utf-8"), filename=str(MESSAGES))
    offences: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FormattedValue) or escapes_the_whole_thing(node.value):
            continue
        for read in ast.walk(node.value):
            if isinstance(read, ast.Attribute) and read.attr in strings:
                offences.append(f"line {read.lineno}: {read.attr}")

    assert not offences, (
        "these are interpolated into a Slack message without going through "
        f"messages.escaped(), so whatever a person typed into them is parsed: {offences}"
    )
