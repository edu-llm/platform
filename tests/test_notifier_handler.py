"""What the deployed function does with a batch of deliveries.

Everything worth testing is in the three modules under `notifications/`. This exercises the
wiring: unwrap, render, deliver, and report which records could not be handled.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from edullm_platform.notifications.facts import Catalogs
from edullm_platform.notifications.messages import RUNS_CHANNEL, Message
from edullm_platform.notifier_handler import BATCH_ITEM_FAILURES_KEY, handler

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVENTS = PROJECT_ROOT / "fixtures" / "events"


@pytest.fixture(scope="module")
def catalogs() -> Catalogs:
    return Catalogs.load(PROJECT_ROOT / "config")


class Collector:
    def __init__(self, fails: bool = False) -> None:
        self.delivered: list[Message] = []
        self.fails = fails

    def deliver(self, message: Message) -> None:
        if self.fails:
            raise RuntimeError("the webhook answered 500")
        self.delivered.append(message)


def record(name: str, message_id: str) -> dict[str, object]:
    body = (EVENTS / f"{name}.sanitized.json").read_text(encoding="utf-8")
    return {"messageId": message_id, "body": body}


def test_a_terminal_delivery_becomes_one_posted_message(catalogs: Catalogs) -> None:
    transport = Collector()

    answer = handler(
        {"Records": [record("batch-succeeded", "m1")]},
        transport=transport,
        catalogs=catalogs,
    )

    assert answer == {BATCH_ITEM_FAILURES_KEY: []}
    assert transport.delivered == [
        Message(
            channel=RUNS_CHANNEL,
            text=(
                "Aryan Verma · plan-b-phase0-100m-superbpe-eval · "
                "$0.02 spent, $2.01 authorised · ran 1m on gpu-1xa10g."
            ),
        )
    ]


def test_a_delivery_nobody_is_owed_a_message_for_posts_nothing_and_succeeds(
    catalogs: Catalogs,
) -> None:
    """Mutation: report a suppressed delivery as a failure.

    Most deliveries on this queue are not endings. Reporting them as failures would send
    every RUNNABLE and RUNNING event round the retry loop and into the dead-letter queue,
    where the alarm would then fire on the platform working correctly.
    """
    transport = Collector()

    answer = handler(
        {"Records": [record("batch-running", "m1")]}, transport=transport, catalogs=catalogs
    )

    assert answer == {BATCH_ITEM_FAILURES_KEY: []}
    assert transport.delivered == []


def test_one_undeliverable_record_is_named_and_the_rest_are_posted(
    catalogs: Catalogs,
) -> None:
    class HalfBroken(Collector):
        def deliver(self, message: Message) -> None:
            if "died at" in message.text:
                raise RuntimeError("the webhook answered 500")
            self.delivered.append(message)

    transport = HalfBroken()

    answer = handler(
        {"Records": [record("batch-succeeded", "m1"), record("batch-failed", "m2")]},
        transport=transport,
        catalogs=catalogs,
    )

    assert answer == {BATCH_ITEM_FAILURES_KEY: [{"itemIdentifier": "m2"}]}
    assert len(transport.delivered) == 1


def test_a_batch_in_which_nothing_survived_raises(catalogs: Catalogs) -> None:
    """The only way to have a delivery retried when nothing else in the batch succeeded."""
    with pytest.raises(RuntimeError):
        handler(
            {"Records": [record("batch-succeeded", "m1")]},
            transport=Collector(fails=True),
            catalogs=catalogs,
        )


def test_a_record_that_is_not_json_is_named_rather_than_crashing_the_batch(
    catalogs: Catalogs,
) -> None:
    transport = Collector()

    answer = handler(
        {
            "Records": [
                record("batch-succeeded", "m1"),
                {"messageId": "m2", "body": "not json"},
            ]
        },
        transport=transport,
        catalogs=catalogs,
    )

    assert answer == {BATCH_ITEM_FAILURES_KEY: [{"itemIdentifier": "m2"}]}
    assert len(transport.delivered) == 1


def test_the_webhook_is_read_again_on_every_invocation(
    catalogs: Catalogs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: cache the endpoint in a module-level global on first read.

    That is the obvious optimisation and it is what makes a rotation silently ineffective.
    `put-secret-value` keeps the secret's ARN, so nothing about the deployed function changes
    and no redeploy happens; a warm container holding the old URL goes on posting to a webhook
    that has just been revoked. Slack answers a retired webhook with a 404 rather than a
    timeout, so the deliveries fail, retry three times, dead-letter, and the only sign is an
    alarm that reads as "the webhook is refusing" long after somebody thought they had fixed
    it by rotating.

    Two invocations, two reads. Asserted as an exact count rather than as "more than one",
    because a cache keyed on something that happens to change per test would pass that.

    A reader is handed in so the handler builds no boto3 client, and the delivery is one
    nobody is owed a message for, so nothing is posted to the endpoint this counts the reads
    of. The endpoint value never leaves this test and is not a real URL.
    """
    import edullm_platform.notifier_handler as module

    reads: list[str] = []

    def read() -> str:
        reads.append("read")
        return "https://example.invalid/not-a-webhook"

    monkeypatch.setattr(module, "_webhook_endpoint", read)

    class NoIntent:
        def get_object(self, **arguments: object) -> object:
            raise RuntimeError("no intent record here")

    for _ in range(2):
        handler(
            {"Records": [record("batch-running", "m1")]},
            catalogs=catalogs,
            intent_reader=NoIntent(),
        )

    assert len(reads) == 2


def test_nothing_in_the_handler_holds_state_between_invocations() -> None:
    """The other half of the mutation above, which the count cannot see.

    That test replaces `_webhook_endpoint` wholesale, so a cache written *inside* it would
    survive it. A module-level global rebound from a function is the only way this module
    could keep anything across invocations, and it has no reason to keep anything: every
    value a message carries is derived from the delivery, from the packaged configuration, or
    from a read made for that delivery.

    Read off the syntax tree rather than the text, because `global` inside a nested function
    or a conditional is still a rebinding and still outlives the call.
    """
    import ast

    tree = ast.parse(Path("src/edullm_platform/notifier_handler.py").read_text(encoding="utf-8"))

    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Global | ast.Nonlocal)]


def test_the_handler_imports_no_sdk_at_module_load() -> None:
    """Mutation: move `import boto3` to the top of the file.

    boto3 is in the Lambda runtime and deliberately not in pyproject.toml, so a top-level
    import makes this module unimportable in CI and unmeasurable by the zip builder, which
    imports the entry point in a clean interpreter to decide what to package. Checked over
    the syntax tree rather than by reading text, because a top-level import inside a
    `try` or an `if TYPE_CHECKING` is still a top-level import and still runs.
    """
    import ast

    tree = ast.parse(Path("src/edullm_platform/notifier_handler.py").read_text(encoding="utf-8"))
    top_level: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            top_level.add(node.module.split(".")[0])

    assert "boto3" not in top_level
    assert "botocore" not in top_level
    assert "boto3" in Path("src/edullm_platform/notifier_handler.py").read_text(encoding="utf-8")


def test_one_queue_carries_three_shapes_and_the_handler_picks_between_them(
    catalogs: Catalogs,
) -> None:
    """Mutation: route every delivery to ``render_run_ended``.

    Batch's own state changes, the platform's approval requests and the morning trigger all
    arrive on one queue, because the queue is where a message that could not be posted goes
    to be found and all three failures are the same incident. What that costs is that the
    handler has to pick, and picking wrongly is silent: ``read_run_ended`` answers ``None``
    for an approval envelope, so the message would simply never be sent.
    """
    from edullm_platform.notifications.approval import load_policy
    from edullm_platform.run_history import load_run_history

    transport = Collector()
    config = PROJECT_ROOT / "config"

    answer = handler(
        {
            "Records": [
                record("batch-succeeded", "m1"),
                record("approval-requested", "m2"),
                record("batch-running", "m3"),
            ]
        },
        transport=transport,
        catalogs=catalogs,
        policy=load_policy(config),
        history=load_run_history(config),
    )

    assert answer == {BATCH_ITEM_FAILURES_KEY: []}
    assert len(transport.delivered) == 2, "a run ended and a lead was asked, and RUNNING owes none"
    assert transport.delivered[0].text.startswith("Aryan Verma · ")
    assert transport.delivered[1].text.startswith("$781.82 · context-length-sweep · ")


def test_a_run_ended_message_never_opens_the_policy_or_the_history(
    catalogs: Catalogs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: load both files eagerly beside the catalogs.

    Nine deliveries in ten are Batch state changes owing a run-ended line, and that line
    reads neither file. Eagerly loading them would make every one of those invocations pay
    for two reads it does not use, and would lose a message about a run that demonstrably
    happened to a policy file it never opens.

    Pointed at an empty directory rather than watched for a call, because that is the failure
    as it would actually arrive: a deployment whose zip is missing one of the five files, or
    a ``EDULLM_CONFIG_DIRECTORY`` set to somewhere wrong, which is the 2026-08-06 outage.
    """
    monkeypatch.setenv("EDULLM_CONFIG_DIRECTORY", str(PROJECT_ROOT / "no" / "such" / "place"))
    transport = Collector()

    answer = handler(
        {"Records": [record("batch-succeeded", "m1")]},
        transport=transport,
        catalogs=catalogs,
    )

    assert answer == {BATCH_ITEM_FAILURES_KEY: []}
    assert len(transport.delivered) == 1


def test_the_morning_trigger_measures_its_window_from_the_delivery(catalogs: Catalogs) -> None:
    """Mutation: take the time from the clock instead of the record.

    SQS stamps ``SentTimestamp`` when the schedule fired, so a retry after a cold start
    measures the window the first attempt would have. A clock read here would slide the
    window by however long the retries took, which is exactly when a page is most likely to
    be re-sent.
    """
    asked: list[dict[str, object]] = []

    class Lister:
        def list_jobs(self, **arguments: object) -> object:
            asked.append(arguments)
            return {"jobSummaryList": []}

    fired = 1_785_984_000_000
    transport = Collector()
    handler(
        {
            "Records": [
                {**record("overnight-activity", "m1"), "attributes": {"SentTimestamp": str(fired)}}
            ]
        },
        transport=transport,
        catalogs=catalogs,
        cell_lister=Lister(),
    )

    assert asked, "no queue was listed"
    assert asked[0]["filters"] == [
        {"name": "AFTER_CREATED_AT", "values": [str(fired - 12 * 3600 * 1000)]}
    ]
