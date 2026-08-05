"""The exact words, asserted exactly.

Wording is the thing the owner will change, so it is tested by equality rather than by
substring. A test that only checks a figure is present goes green on a sentence nobody meant
to write, and the whole point of putting every string in one module is that changing one is
a visible, reviewable diff with a failing test beside it.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from edullm_platform.notifications.facts import Catalogs, read_run_ended
from edullm_platform.notifications.messages import (
    RUNS_CHANNEL,
    Message,
    duration,
    money,
    render_run_ended,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVENTS = PROJECT_ROOT / "fixtures" / "events"


@pytest.fixture(scope="module")
def catalogs() -> Catalogs:
    return Catalogs.load(PROJECT_ROOT / "config")


def rendered(name: str, catalogs: Catalogs) -> Message:
    envelope = json.loads((EVENTS / f"{name}.sanitized.json").read_text(encoding="utf-8"))
    facts = read_run_ended(envelope, catalogs=catalogs)
    assert facts is not None
    return render_run_ended(facts)


def test_a_finished_run_opens_with_the_person_the_experiment_and_the_money(
    catalogs: Catalogs,
) -> None:
    """The rule the whole design rests on: who, which experiment, how much, in that order."""
    message = rendered("batch-succeeded", catalogs)

    assert message.channel == RUNS_CHANNEL
    assert message.text == (
        "Aryan Verma · plan-b-phase0-100m-superbpe-eval · "
        "$0.02 spent, $2.01 authorised · ran 1m on gpu-1xa10g."
    )


def test_a_failed_run_says_what_was_burned_and_that_nothing_came_of_it(
    catalogs: Catalogs,
) -> None:
    """The more urgent half of the same trigger, and the one nothing in the design had."""
    message = rendered("batch-failed", catalogs)

    assert message.channel == RUNS_CHANNEL
    assert message.text == (
        "Aryan Verma · plan-b-phase0-100m-superbpe-eval · $0.70 spent, nothing produced · "
        "died at 42m on gpu-1xa10g, exit 1, whether a checkpoint survived is unknown."
    )


def test_a_run_nothing_could_name_a_submitter_for_says_so_rather_than_naming_the_team(
    catalogs: Catalogs,
) -> None:
    """Both sources have to fail together to reach this, so the wording blames neither.

    The message says it could not name the person, which is true of the message. It does not
    say the roster records nobody, which would be a claim about the person and is usually
    false: the intent record names all thirty-five, and reaching this line means it could
    not be read.
    """
    envelope = json.loads((EVENTS / "batch-succeeded.sanitized.json").read_text(encoding="utf-8"))
    envelope["detail"]["container"]["environment"] = [
        entry
        for entry in envelope["detail"]["container"]["environment"]
        if entry["name"] != "WANDB_USERNAME"
    ]
    facts = read_run_ended(envelope, catalogs=catalogs)
    assert facts is not None

    assert render_run_ended(facts).text.startswith("Somebody this message could not name · ")


def test_a_run_whose_queue_nothing_prices_says_the_cost_is_unknown(
    catalogs: Catalogs,
) -> None:
    """A message that cannot price a run is still worth more than silence about it."""
    envelope = json.loads((EVENTS / "batch-succeeded.sanitized.json").read_text(encoding="utf-8"))
    envelope["detail"]["jobQueue"] = (
        "arn:aws:batch:us-east-1:<aws-account-id>:job-queue/somebody-elses-queue"
    )
    facts = read_run_ended(envelope, catalogs=catalogs)
    assert facts is not None

    assert render_run_ended(facts).text == (
        "Aryan Verma · plan-b-phase0-100m-superbpe-eval · cost unknown · "
        "ran 1m on the somebody-elses-queue queue."
    )


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0s"), (7, "7s"), (63, "1m"), (2520, "42m"), (3600, "1h00m"), (13920, "3h52m")],
)
def test_a_duration_reads_the_way_somebody_says_it(seconds: int, expected: str) -> None:
    assert duration(seconds) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(Decimal(0), "$0.00"), (Decimal("7.16"), "$7.16"), (Decimal("1234.5"), "$1,234.50")],
)
def test_money_is_rendered_to_the_cent_with_thousands_separated(
    value: Decimal, expected: str
) -> None:
    assert money(value) == expected


def test_no_message_carries_an_em_dash(catalogs: Catalogs) -> None:
    """The house standard, held here because this is the only file with prose in it."""
    for name in ("batch-succeeded", "batch-failed"):
        assert "—" not in rendered(name, catalogs).text


def test_the_wording_module_reaches_nothing_that_opens_a_socket() -> None:
    """THE SEAM THIS WHOLE PACKAGE IS SHAPED AROUND. Mutation: post from the renderer.

    The owner intends to iterate on wording after this works. That is only cheap if changing
    a sentence cannot change what gets sent, which means the renderer must be reachable
    without the transport and must not be able to send anything itself. Checked by reading
    the source rather than by convention, because a convention is what erodes.
    """
    import ast

    source = Path("src/edullm_platform/notifications/messages.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            imported.add(node.module.split(".")[0])

    forbidden = {"urllib", "http", "socket", "boto3", "botocore", "requests"}
    assert not (imported & forbidden), f"the renderer reaches {sorted(imported & forbidden)}"
    assert "delivery" not in source
