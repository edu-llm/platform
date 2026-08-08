"""The fan-out that drains the fleet, with the AWS CLI replaced by a recording of its answers.

What is worth testing here is not that Systems Manager works. It is the handful of places this
tool can be quietly wrong on the one morning it matters:

* sending ``edullm-node drain --stop-runs`` when nobody asked, which ends people's runs;
* going red every quarter of an hour once the fleet is gone, which is how a check stops being
  read before the day it is needed;
* reporting a machine that never answered as one with nothing left to save;
* and exiting zero while a node is still holding files.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.block_fleet import NODE_TAG, RESERVATION_TAG
from tools import block_drain

BLOCK = "cr-0afc33f3a1af417a7"
OTHER_BLOCK = "cr-00000000000000001"
ENDS_AT = "2026-08-11T11:30:00+00:00"


class FakeCli:
    """Answers the calls the tool makes, and records the argv of each.

    Keyed on the first two words, with one exception: the reservation discovery reads
    ``describe-instances`` through a ``--query`` and would otherwise collide with the fleet
    listing, which is a different question with a differently shaped answer.
    """

    def __init__(self, *, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[list[str]] = []

    @staticmethod
    def key(arguments: Sequence[str]) -> str:
        head = " ".join(arguments[:2])
        if head == "ec2 describe-instances" and "--query" in arguments:
            return "ec2 describe-instances --query"
        return head

    def __call__(
        self, arguments: Sequence[str], *, profile: str | None = None, region: str | None = None
    ) -> Any:
        self.calls.append(list(arguments))
        return self.answers[self.key(arguments)]

    def argv_for(self, key: str) -> list[str]:
        matching = [call for call in self.calls if self.key(call) == key]
        assert len(matching) >= 1, f"nothing called {key}"
        return matching[0]

    def called(self, key: str) -> bool:
        return any(self.key(call) == key for call in self.calls)


def ec2_answer(*instance_ids: str) -> dict[str, Any]:
    return {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": instance_id,
                        "State": {"Name": "running"},
                        "Tags": [
                            {"Key": NODE_TAG, "Value": str(number + 1)},
                            {"Key": RESERVATION_TAG, "Value": BLOCK},
                        ],
                    }
                ]
            }
            for number, instance_id in enumerate(instance_ids)
        ]
    }


def invocation(instance_id: str, *, status: str, output: str) -> dict[str, Any]:
    return {
        "InstanceId": instance_id,
        "Status": status,
        "CommandPlugins": [{"Output": output}],
    }


def drained(node: int, run: str, *, local: int, remote: int) -> str:
    return (
        f"node\t{node}\n"
        "usable_seconds\t3600\n"
        f"claim\teric\t{run}\n"
        "container\trunning\n"
        f"run\t{run}\t{local}\t{remote}\tok\n"
        "drained_at\t2026-08-11T10:30:00Z\n"
    )


@pytest.fixture
def one_clean_node() -> dict[str, Any]:
    return {
        "ec2 describe-instances": ec2_answer("i-0001"),
        "ec2 describe-capacity-reservations": {
            "CapacityReservations": [{"EndDate": ENDS_AT}]
        },
        "ssm send-command": {"Command": {"CommandId": "command-1"}},
        "ssm list-command-invocations": {
            "CommandInvocations": [
                invocation(
                    "i-0001",
                    status="Success",
                    output=drained(1, "shared-experts-a", local=40, remote=40),
                )
            ]
        },
        "s3api list-objects-v2": {"Contents": []},
    }


def test_the_drain_is_sent_to_a_tag_and_never_to_a_list_of_instance_ids(
    monkeypatch: pytest.MonkeyPatch, one_clean_node: dict[str, Any]
) -> None:
    """Mutation: send with ``--instance-ids``, which is the obvious spelling.

    Systems Manager refuses that whole call with ``InvalidInstanceId`` when any one of the ids
    is not a managed instance, so a single node whose agent has fallen over costs the *drain*
    of the other seven -- not merely the reading of them, which is what the same mistake costs
    the status tool.
    """
    cli = FakeCli(answers=one_clean_node)
    monkeypatch.setattr(block_drain, "aws_json", cli)

    block_drain.collect(
        reservation_id=BLOCK, profile=None, region="us-east-2", wait_seconds=0, stop_runs=False
    )

    argv = cli.argv_for("ssm send-command")
    assert "--instance-ids" not in argv
    assert f"Key=tag:{RESERVATION_TAG},Values={BLOCK}" in argv


def test_nothing_stops_a_training_run_unless_the_caller_asked_for_it(
    monkeypatch: pytest.MonkeyPatch, one_clean_node: dict[str, Any]
) -> None:
    """THE MUTATION THAT WOULD DO REAL DAMAGE: pass ``--stop-runs`` always.

    A drain is meant to be run repeatedly and from a schedule, and the flag ends somebody's
    training run so that OLMo-core writes a final checkpoint on the way out. Doing that four
    times an hour for three days would make the fleet unusable, and the failure would look like
    everybody's runs mysteriously stopping rather than like a workflow being wrong.
    """
    cli = FakeCli(answers=one_clean_node)
    monkeypatch.setattr(block_drain, "aws_json", cli)

    block_drain.collect(
        reservation_id=BLOCK, profile=None, region="us-east-2", wait_seconds=0, stop_runs=False
    )
    quiet = json.loads(cli.argv_for("ssm send-command")[-1])

    assert quiet["commands"] == ["edullm-node drain"]


def test_asking_for_a_stop_asks_the_node_for_one(
    monkeypatch: pytest.MonkeyPatch, one_clean_node: dict[str, Any]
) -> None:
    """The other direction, so the flag is not merely accepted and dropped -- which is the
    shape a mistake here would take, and it would only be discovered at the deadline."""
    cli = FakeCli(answers=one_clean_node)
    monkeypatch.setattr(block_drain, "aws_json", cli)

    block_drain.collect(
        reservation_id=BLOCK, profile=None, region="us-east-2", wait_seconds=0, stop_runs=True
    )

    assert json.loads(cli.argv_for("ssm send-command")[-1])["commands"] == [
        "edullm-node drain --stop-runs"
    ]


def test_the_command_is_given_long_enough_to_copy_a_filesystem(
    monkeypatch: pytest.MonkeyPatch, one_clean_node: dict[str, Any]
) -> None:
    """Mutation: leave ``executionTimeout`` off and take the document default, or set only
    ``--timeout-seconds``.

    Those are two different Systems Manager timeouts. ``--timeout-seconds`` bounds delivery --
    how long a command may sit before it starts -- and ``executionTimeout`` bounds the run. A
    drain killed part way through a copy reports a shortfall it caused itself, and the first
    drain of a run directory nobody has drained before is the slow one by construction.
    """
    cli = FakeCli(answers=one_clean_node)
    monkeypatch.setattr(block_drain, "aws_json", cli)

    block_drain.collect(
        reservation_id=BLOCK, profile=None, region="us-east-2", wait_seconds=0, stop_runs=False
    )
    parameters = json.loads(cli.argv_for("ssm send-command")[-1])

    assert parameters["executionTimeout"] == [str(block_drain.COMMAND_EXECUTION_SECONDS)]
    assert block_drain.COMMAND_EXECUTION_SECONDS > block_drain.COMMAND_DELIVERY_SECONDS


def test_a_running_node_with_no_invocation_is_reported_and_not_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: build the readings from the invocation list.

    A machine EC2 says is running for which Systems Manager produced no invocation is one whose
    agent has never registered -- so nothing has drained it and nothing is going to. Iterating
    the invocations reports seven clean nodes and no eighth, which reads as a fleet that is
    entirely safe.
    """
    cli = FakeCli(
        answers={
            "ec2 describe-instances": ec2_answer("i-0001", "i-0002"),
            "ssm send-command": {"Command": {"CommandId": "command-1"}},
            "ssm list-command-invocations": {
                "CommandInvocations": [
                    invocation(
                        "i-0001", status="Success", output=drained(1, "a-run", local=4, remote=4)
                    )
                ]
            },
        }
    )
    monkeypatch.setattr(block_drain, "aws_json", cli)

    readings = block_drain.collect(
        reservation_id=BLOCK, profile=None, region="us-east-2", wait_seconds=0, stop_runs=False
    )

    assert [reading.instance_id for reading in readings] == ["i-0001", "i-0002"]
    assert readings[1].reachable is False
    assert readings[1].flushed is False
    assert "not registered" in readings[1].detail


def test_a_window_with_no_fleet_up_costs_one_call_and_exits_clean(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """THE PROPERTY THAT DECIDES WHETHER ANYBODY READS THIS REPORT AT ALL.

    The schedule keeps firing after the window closes and before it opens, and a run that
    refuses because no instance carries a block tag paints the repository red every quarter of
    an hour for a state that is completely normal. A check that is red when nothing is wrong is
    one people have stopped looking at by the morning something is.
    """
    cli = FakeCli(answers={"ec2 describe-instances --query": []})
    monkeypatch.setattr(block_drain, "aws_json", cli)

    assert block_drain.main(["--no-profile"]) == 0
    assert "nothing to drain" in capsys.readouterr().out
    assert not cli.called("ssm send-command")


def test_two_live_blocks_are_a_refusal_rather_than_a_choice(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Node numbers repeat across fleets, so "the block" is not a question with one answer.
    Guessing is how the wrong eight machines are told to stop their runs."""
    cli = FakeCli(answers={"ec2 describe-instances --query": [[BLOCK], [OTHER_BLOCK]]})
    monkeypatch.setattr(block_drain, "aws_json", cli)

    assert block_drain.main(["--no-profile"]) == 2
    assert "more_than_one_block_has_a_fleet_up" in capsys.readouterr().err


def test_a_node_still_holding_files_makes_the_run_fail(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Mutation: always exit zero, because the report is the output.

    Nobody watches a job summary. A red run in the Actions list is the only part of this that
    reaches somebody who was not already looking, and the state it has to reach them for is a
    machine that will be terminated with files on it.
    """
    monkeypatch.setattr(
        block_drain,
        "aws_json",
        FakeCli(
            answers={
                "ec2 describe-instances": ec2_answer("i-0001"),
                "ec2 describe-capacity-reservations": {
                    "CapacityReservations": [{"EndDate": ENDS_AT}]
                },
                "ssm send-command": {"Command": {"CommandId": "command-1"}},
                "ssm list-command-invocations": {
                    "CommandInvocations": [
                        invocation(
                            "i-0001",
                            status="Success",
                            output=drained(1, "curriculum-b", local=310, remote=298),
                        )
                    ]
                },
                "s3api list-objects-v2": {"Contents": []},
            }
        ),
    )
    summary = tmp_path / "summary.md"

    exit_code = block_drain.main(
        ["--no-profile", "--reservation", BLOCK, "--summary", str(summary)]
    )

    assert exit_code == 1
    assert "block_drain_incomplete:i-0001" in capsys.readouterr().err
    assert "12 files short of the 310" in summary.read_text(encoding="utf-8")


def test_a_fleet_that_saved_everything_exits_zero_and_writes_the_page(
    monkeypatch: pytest.MonkeyPatch, one_clean_node: dict[str, Any], tmp_path: Path
) -> None:
    monkeypatch.setattr(block_drain, "aws_json", FakeCli(answers=one_clean_node))
    summary = tmp_path / "summary.md"

    exit_code = block_drain.main(
        ["--no-profile", "--reservation", BLOCK, "--summary", str(summary)]
    )

    page = summary.read_text(encoding="utf-8")
    assert exit_code == 0
    assert "nodes with work still in flight | 0" in page
    assert "SAVED" in page


def test_the_deadline_comes_off_the_reservation_rather_than_out_of_a_file(
    monkeypatch: pytest.MonkeyPatch, one_clean_node: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: hardcode the window end, which is the shortest way to make this run.

    The purchase is the only thing that knows when the window closes, a second block would
    inherit the first one's date, and a date in a file is wrong silently rather than loudly.
    """
    cli = FakeCli(answers=one_clean_node)
    monkeypatch.setattr(block_drain, "aws_json", cli)
    summary = tmp_path / "summary.md"

    block_drain.main(["--no-profile", "--reservation", BLOCK, "--summary", str(summary)])

    assert "--capacity-reservation-ids" in cli.argv_for("ec2 describe-capacity-reservations")
    assert "2026-08-11T11:30:00+00:00" in summary.read_text(encoding="utf-8")
    assert "2026-08-11T11:00:00+00:00" in summary.read_text(encoding="utf-8")


def test_the_json_mode_carries_the_clock_and_every_field_the_table_reduces(
    monkeypatch: pytest.MonkeyPatch, one_clean_node: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """The table is for a person and drops things on purpose; ``--json`` is what a later script
    reads, and the arithmetic it most wants is the one nobody should redo."""
    monkeypatch.setattr(block_drain, "aws_json", FakeCli(answers=one_clean_node))

    assert block_drain.main(["--no-profile", "--reservation", BLOCK, "--json"]) == 0

    record = json.loads(capsys.readouterr().out)
    assert record["reclaim_at"] == "2026-08-11T11:00:00+00:00"
    assert record["nodes"][0]["flushed"] is True
    assert record["nodes"][0]["runs"][0]["remote"] == 40


def test_a_laptop_gets_a_profile_and_a_runner_can_take_it_away() -> None:
    """Both callers are real and want opposite defaults, and the laptop one is not a
    convenience here -- it is the fallback for the morning GitHub is the broken thing."""
    parser = block_drain.build_parser()

    assert parser.parse_args([]).profile == "sbsandbox"
    assert parser.parse_args(["--no-profile"]).profile is None
    assert parser.parse_args([]).region == "us-east-2"
    assert parser.parse_args([]).reservation is None
    assert parser.parse_args([]).stop_runs is False
