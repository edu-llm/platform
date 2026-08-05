"""Print the exact text a run would post, from a fixture, with no AWS account.

    uv run python tools/render_notification.py --event fixtures/events/batch-failed.sanitized.json

**This exists so wording is cheap to change.** The owner intends to iterate on these
sentences, and iterating is only cheap if seeing the result costs nothing. There is no
credential here, no client, no queue and no deploy. The text this prints is the text
``notifier_handler`` posts, because both call ``render_run_ended`` and neither has anywhere
else to get a sentence from.

It reads no environment variable and takes no injected reader, so the person is whoever
``WANDB_USERNAME`` reverses to. That names thirty of the thirty-five without an account.
Passing ``--no-submitter`` shows the wording for a run neither source could name, which is
what the other five look like when the intent record cannot be read.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from edullm_platform.notifications.facts import Catalogs, read_run_ended
from edullm_platform.notifications.messages import render_run_ended

WANDB_USERNAME_VARIABLE = "WANDB_USERNAME"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--event",
        type=Path,
        required=True,
        help="An EventBridge envelope on disk. fixtures/events/ holds three.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "config",
        help="Where organization.yaml, workload-catalog.yaml and execution-targets.yaml are.",
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

    facts = read_run_ended(envelope, catalogs=Catalogs.load(options.config))
    if facts is None:
        print("No message is owed for this event.")
        return 0

    message = render_run_ended(facts)
    print(f"[{message.channel}] {message.text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
