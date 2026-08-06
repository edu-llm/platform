"""Print the exact text a delivery would post, from a fixture, with no AWS account.

    uv run python tools/render_notification.py --event fixtures/events/batch-failed.sanitized.json

**This exists so wording is cheap to change.** The owner intends to iterate on these
sentences, and iterating is only cheap if seeing the result costs nothing. There is no
credential here, no client, no queue and no deploy.

**It calls the handler's own dispatcher rather than a renderer.** ``notifier_handler.message_for``
is what decides which of the three messages an envelope is owed, so this cannot print a
message the deployed function would not have chosen, or choose differently from it. Nothing on
that path imports boto3 at module level, so importing the handler here costs nothing.

Three envelope shapes are understood, and ``fixtures/events/`` holds one of each.

* A Batch job state change becomes the run-ended line.
* ``Run Approval Requested`` becomes the five lines a lead decides on.
* ``Overnight Activity`` becomes the morning page.

**What differs from the deployed text, and it is only ever an unknown.** Four readers are
``None`` without a credential, so the submitter falls back to ``WANDB_USERNAME``, a fan-out's
spend reads as not read, a failed run's checkpoint reads as unknown, and the morning page has
no queue to list. Every one of those is a message saying it could not look rather than a
message saying something different. To see the deployed text, run the handler itself with real
clients and swap only the transport.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from edullm_platform.notifications.approval import load_accelerators, load_policy
from edullm_platform.notifications.facts import Catalogs
from edullm_platform.notifier_handler import message_for
from edullm_platform.run_history import load_run_history

WANDB_USERNAME_VARIABLE = "WANDB_USERNAME"

#: What SQS stamps on every delivery and what the morning page measures its window back from.
#: Supplied here so the tool prints a reproducible page rather than one that depends on when
#: it was run; the value is the ``time`` on the envelope, which is what the schedule fired at.
SENT_TIMESTAMP = "SentTimestamp"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--event",
        type=Path,
        required=True,
        help="An envelope on disk. fixtures/events/ holds one of each shape.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "config",
        help="Where the five reviewed files this function reads are.",
    )
    parser.add_argument(
        "--no-submitter",
        action="store_true",
        help="Drop WANDB_USERNAME, to see the wording for a run nothing could name.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    envelope = json.loads(options.event.read_text(encoding="utf-8"))

    if options.no_submitter:
        container = envelope.get("detail", {}).get("container", {})
        container["environment"] = [
            entry
            for entry in container.get("environment", [])
            if entry.get("name") != WANDB_USERNAME_VARIABLE
        ]

    catalogs = Catalogs.load(options.config)
    message = message_for(
        envelope,
        {SENT_TIMESTAMP: _milliseconds(envelope)},
        catalogs=catalogs,
        policy=lambda: load_policy(options.config),
        history=lambda: load_run_history(options.config),
        accelerators=lambda: load_accelerators(options.config),
        intent_reader=None,
        lineage_bucket="",
        cell_lister=None,
        checkpoint_lister=None,
    )
    if message is None:
        print("No message is owed for this event.")
        return 0

    print(f"[{message.channel}] {message.text}")
    return 0


def _milliseconds(envelope: dict[str, object]) -> str:
    """The envelope's own ``time`` as an SQS timestamp, or now where it carries none.

    A string because that is what SQS puts in the attribute, so the handler takes the same
    branch here that it takes in the account rather than a fallback only this tool reaches.
    """
    from datetime import UTC, datetime

    stamp = envelope.get("time")
    if isinstance(stamp, str):
        try:
            moment = datetime.fromisoformat(stamp)
        except ValueError:
            moment = datetime.now(tz=UTC)
    else:
        moment = datetime.now(tz=UTC)
    return str(int(moment.timestamp() * 1000))


if __name__ == "__main__":
    raise SystemExit(main())
