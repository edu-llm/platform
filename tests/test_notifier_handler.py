"""What the deployed function does with a batch of deliveries.

Everything worth testing is in the three modules under `notifications/`. This exercises the
wiring: unwrap, render, deliver, and report which records could not be handled.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from edullm_platform.notifications.facts import Catalogs
from edullm_platform.notifications.messages import Message
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
    assert len(transport.delivered) == 1
    assert transport.delivered[0].text.startswith("Aryan Verma · ")


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
