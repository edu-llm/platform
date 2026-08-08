"""The fan-out, with the AWS CLI replaced by a recording of what it answers.

What is worth testing here is not that Systems Manager works. It is the three places this tool
can quietly lie: sending to instance ids instead of a tag target, so one unregistered agent
loses the reading of the whole fleet; reporting a node with no invocation as though it had
answered; and starting from the invocation list rather than from EC2, which makes a missing
node invisible instead of visible.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest

from edullm_platform.block_fleet import NODE_TAG, RESERVATION_TAG
from tools import block_status

BLOCK = "cr-0afc33f3a1af417a7"


class FakeCli:
    """Answers the three calls the tool makes, and records the argv of each.

    Keyed on the first two words rather than on call order, because the order is an
    implementation detail and pinning it would fail a refactor that is not a regression.
    """

    def __init__(self, *, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(
        self, arguments: Sequence[str], *, profile: str | None = None, region: str | None = None
    ) -> Any:
        self.calls.append(list(arguments))
        return self.answers[" ".join(arguments[:2])]

    def argv_for(self, prefix: str) -> list[str]:
        matching = [call for call in self.calls if " ".join(call[:2]) == prefix]
        assert len(matching) >= 1, f"nothing called {prefix}"
        return matching[0]


def ec2_answer(*instance_ids: str) -> dict[str, Any]:
    return {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": instance_id,
                        "State": {"Name": "running"},
                        "PrivateIpAddress": f"172.31.0.{number + 10}",
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


@pytest.fixture
def busy_and_idle() -> dict[str, Any]:
    return {
        "ec2 describe-instances": ec2_answer("i-0001", "i-0002"),
        "ssm send-command": {"Command": {"CommandId": "command-1"}},
        "ssm list-command-invocations": {
            "CommandInvocations": [
                invocation(
                    "i-0001",
                    status="Success",
                    output=(
                        "gpus_total\t8\ngpus_busy\t8\nrun\tshared-experts-a\nwho\teric\n"
                        "started_at\t2026-08-08T11:48:00+00:00\nready\ttrue\n"
                    ),
                ),
                invocation(
                    "i-0002",
                    status="Success",
                    output="gpus_total\t8\ngpus_busy\t0\nready\ttrue\n",
                ),
            ]
        },
    }


def test_the_fan_out_targets_a_tag_rather_than_a_list_of_instance_ids(
    monkeypatch: pytest.MonkeyPatch, busy_and_idle: dict[str, Any]
) -> None:
    """Mutation: send with ``--instance-ids``, which is the obvious spelling.

    Systems Manager refuses that whole call with ``InvalidInstanceId`` when any one of the ids
    is not a managed instance, so a single node whose agent has not come up costs the reading
    of the other seven -- during the window in which somebody is trying to find out what is
    wrong with the fleet. A tag target produces no invocation for that node and reaches the
    rest, which is what the reconciliation below then reports.
    """
    cli = FakeCli(answers=busy_and_idle)
    monkeypatch.setattr(block_status, "aws_json", cli)

    block_status.collect(reservation_id=BLOCK, profile=None, region="us-east-2", wait_seconds=1)

    argv = cli.argv_for("ssm send-command")
    assert "--instance-ids" not in argv
    assert f"Key=tag:{RESERVATION_TAG},Values={BLOCK}" in argv


def test_without_a_reservation_the_target_is_every_node_carrying_the_tag(
    monkeypatch: pytest.MonkeyPatch, busy_and_idle: dict[str, Any]
) -> None:
    """The ordinary case is one live block, and typing its id to ask a question about it is
    friction on the command somebody runs forty times in a weekend."""
    cli = FakeCli(answers=busy_and_idle)
    monkeypatch.setattr(block_status, "aws_json", cli)

    block_status.collect(reservation_id=None, profile=None, region="us-east-2", wait_seconds=1)

    assert f"Key=tag-key,Values={NODE_TAG}" in cli.argv_for("ssm send-command")
    assert f"Name=tag-key,Values={NODE_TAG}" in cli.argv_for("ec2 describe-instances")


def test_a_running_node_with_no_invocation_is_reported_and_not_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: build the readings from the invocation list.

    That is the natural shape and it makes the interesting node disappear. An instance EC2 says
    is running, for which Systems Manager produced no invocation at all, is one whose agent has
    never registered -- a node still booting, or one whose bootstrap died before the agent came
    up. Iterating the invocations reports seven healthy nodes and no eighth, which reads as a
    fleet of seven.
    """
    cli = FakeCli(
        answers={
            "ec2 describe-instances": ec2_answer("i-0001", "i-0002"),
            "ssm send-command": {"Command": {"CommandId": "command-1"}},
            "ssm list-command-invocations": {
                "CommandInvocations": [
                    invocation(
                        "i-0001",
                        status="Success",
                        output="gpus_total\t8\ngpus_busy\t0\nready\ttrue\n",
                    )
                ]
            },
        }
    )
    monkeypatch.setattr(block_status, "aws_json", cli)

    readings = block_status.collect(
        reservation_id=BLOCK, profile=None, region="us-east-2", wait_seconds=0
    )

    assert [reading.instance_id for reading in readings] == ["i-0001", "i-0002"]
    assert readings[1].reachable is False
    assert "not registered" in readings[1].detail


def test_a_fleet_that_does_not_exist_yet_costs_no_command_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: send the command before checking whether anything is there.

    A tag target matching nothing is not an error; it is a command with no invocations, which
    this would then wait the whole deadline for. Before the fleet is launched that is the
    normal state, and the tool should answer instantly.
    """
    cli = FakeCli(answers={"ec2 describe-instances": {"Reservations": []}})
    monkeypatch.setattr(block_status, "aws_json", cli)

    assert (
        block_status.collect(
            reservation_id=BLOCK, profile=None, region="us-east-2", wait_seconds=45
        )
        == ()
    )
    assert [" ".join(call[:2]) for call in cli.calls] == ["ec2 describe-instances"]


def test_the_json_mode_carries_every_field_the_table_reduces(
    monkeypatch: pytest.MonkeyPatch, busy_and_idle: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """The table is for a person and drops things on purpose; ``--json`` is for a script and
    must not, which is why the two are asserted separately."""
    monkeypatch.setattr(block_status, "aws_json", FakeCli(answers=busy_and_idle))

    assert block_status.main(["--reservation", BLOCK, "--no-profile", "--json"]) == 0

    records = json.loads(capsys.readouterr().out)
    assert [record["node"] for record in records] == [1, 2]
    assert records[0]["who"] == "eric"
    assert records[0]["started_at"] == "2026-08-08T11:48:00+00:00"
    assert records[1]["run"] is None
    assert records[1]["reachable"] is True


def test_the_printed_table_is_the_shape_people_read(
    monkeypatch: pytest.MonkeyPatch, busy_and_idle: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(block_status, "aws_json", FakeCli(answers=busy_and_idle))

    assert block_status.main(["--no-profile"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("node 1  ")
    assert "8/8 GPUs busy" in lines[0]
    assert lines[1].endswith("IDLE")


def test_a_laptop_gets_a_profile_and_a_runner_can_take_it_away() -> None:
    """Both callers are real and they want opposite defaults.

    A maintainer types this between runs and should not have to remember ``--profile
    sbsandbox``; a workflow runner holds ambient credentials from an assumed role and a profile
    default would send the CLI hunting for an SSO session that is not there.
    """
    parser = block_status.build_parser()

    assert parser.parse_args([]).profile == "sbsandbox"
    assert parser.parse_args(["--no-profile"]).profile is None
    assert parser.parse_args([]).region == "us-east-2"
    assert parser.parse_args([]).reservation is None
    assert parser.parse_args([]).json is False
