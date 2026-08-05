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


def test_a_fan_out_posts_once_and_says_what_it_spent_and_which_cell_died(
    catalogs: Catalogs,
) -> None:
    """It says spent as well as authorised, which the earlier version of this could not.

    The parent event carries no attempts, so nothing in it says what twenty cells burned.
    That is read from Batch, which has already moved every child to a terminal status by the
    time the parent goes terminal. The index comes off the child job id in the same answer.
    """
    from tests.test_notification_facts import ARRAY_PARENT_JOB_ID, cells

    envelope = json.loads(
        (EVENTS / "batch-array-parent-failed.sanitized.json").read_text(encoding="utf-8")
    )
    facts = read_run_ended(envelope, catalogs=catalogs, cell_lister=cells(ARRAY_PARENT_JOB_ID))
    assert facts is not None

    message = render_run_ended(facts)

    assert message.channel == RUNS_CHANNEL
    assert message.text == (
        "Aryan Verma · plan-b-phase0-100m-superbpe-eval · 19 of 20 cells succeeded, 1 failed "
        "· $18.14 spent, $55.83 authorised on gpu-1xl40s. Cell 13 is the one that failed."
    )


def test_a_fan_out_whose_cells_were_not_read_says_so_rather_than_showing_a_ceiling(
    catalogs: Catalogs,
) -> None:
    """MUTATION: FALL BACK TO THE CEILING AND CALL IT SPEND.

    A ceiling rendered where a spend belongs is the one wrong answer this message must not
    give, because $55.83 in the spend slot reads exactly like a measurement and is three
    times the real figure. Unknown is said instead, and the failed cells go unnamed with it,
    because the same call would have answered both.
    """
    message = rendered("batch-array-parent-failed", catalogs)

    assert message.text == (
        "Aryan Verma · plan-b-phase0-100m-superbpe-eval · 19 of 20 cells succeeded, 1 failed "
        "· spend not read, $55.83 authorised on gpu-1xl40s. Which cells failed was not read."
    )


def test_a_fan_out_that_lost_nothing_does_not_say_zero_failed(catalogs: Catalogs) -> None:
    from tests.test_notification_facts import ARRAY_PARENT_JOB_ID, FakeCellLister

    envelope = json.loads(
        (EVENTS / "batch-array-parent-failed.sanitized.json").read_text(encoding="utf-8")
    )
    envelope["detail"]["status"] = "SUCCEEDED"
    envelope["detail"]["arrayProperties"]["statusSummary"] = {"SUCCEEDED": 20, "FAILED": 0}
    lister = FakeCellLister(
        {
            "SUCCEEDED": [
                {
                    "jobId": f"{ARRAY_PARENT_JOB_ID}:{index}",
                    "status": "SUCCEEDED",
                    "startedAt": 1785965337885,
                    "stoppedAt": 1785965337885 + 1_800_000,
                }
                for index in range(20)
            ]
        }
    )
    facts = read_run_ended(envelope, catalogs=catalogs, cell_lister=lister)
    assert facts is not None

    assert render_run_ended(facts).text == (
        "Aryan Verma · plan-b-phase0-100m-superbpe-eval · all 20 cells succeeded "
        "· $18.61 spent, $55.83 authorised on gpu-1xl40s."
    )


@pytest.mark.parametrize(
    ("state", "clause"),
    [
        ("written", "a checkpoint survived"),
        ("none", "no checkpoint written"),
        ("unknown", "whether a checkpoint survived is unknown"),
    ],
)
def test_the_failed_message_says_what_survived(
    catalogs: Catalogs, state: str, clause: str
) -> None:
    """A failure that saved a checkpoint and one that saved nothing carry the same status
    and the same exit code, so only this clause distinguishes them."""
    import dataclasses

    envelope = json.loads((EVENTS / "batch-failed.sanitized.json").read_text(encoding="utf-8"))
    facts = read_run_ended(envelope, catalogs=catalogs)
    assert facts is not None

    message = render_run_ended(dataclasses.replace(facts, checkpoint_state=state))

    assert message.text.endswith(f"exit 1, {clause}.")
