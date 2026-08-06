"""Read back what a lane drill actually did, and write it where a test can read it.

THIS TOOL STARTS NOTHING AND STOPS NOTHING. The drill is run with the verbs, which is the point:
the thing being proved is that the verbs work, and a tool that launched its own machine would
prove that the tool works. Every call here is a read.

WHY THE PRINCIPAL ON THE STOP IS THE FIELD THAT MATTERS MOST. An instance that is stopped proves
nothing on its own, because a person watching a drill will stop a machine that is late.
CloudTrail records who called StopInstances, and that is the difference between a reclaim
service and somebody remembering.

WHY THE COMMAND'S OWN EXIT STATUS IS RECORDED SEPARATELY FROM THE SESSION'S. A session that
opens and runs nothing is indistinguishable from one that works, from everywhere except the
machine, and that is not a hypothetical: edullm run opened a session and ran nothing for the
whole of its life before 2026-08-06. The operator passes what the verb printed, because the
sentinel is the only place the remote shell's status is observable at all -- start-session exits
with the plugin's status rather than the command's.

TWO FIELDS ARE ATTESTED RATHER THAN READ, AND IT IS THE DRILL'S ORDERING THAT MAKES THEM SO.
This tool runs after the reclaim, because the reclaim is the thing being proved. But Systems
Manager drops a stopped machine out of describe-instance-information entirely, so the one field
that says the machine was ever reachable reads back empty, and the sentinel is gone with the
terminal that printed it. Both are therefore passed in, and both say so in their own help.
Everything else here is read from the account.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from edullm_platform.capture_tooling import CaptureFailedError, aws_json, report, write_record
from edullm_platform.cli.lane import LANE_TAG_KEY, SCRATCH_BUCKET

__all__ = ["build_parser", "main"]

RECORD_PATH = Path("fixtures") / "evidence" / "lane" / "drill.json"

#: What the janitor writes on a machine before it stops it. Matched on the suffix rather than
#: the whole key, because the prefix is the janitor's to choose and this tool is not the place
#: that decides it.
WARNING_TAG_SUFFIX = "expiry-warned-at"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--instance-id", required=True, help="the machine the drill started")
    parser.add_argument("--work-object", required=True, help="the s3:// key written on it")
    parser.add_argument(
        "--remote-exit-status",
        type=int,
        required=True,
        help=(
            "the status the verb read off its own edullm-exit: sentinel. Required, and there is "
            "no default, because absent is what a session that ran nothing also looks like"
        ),
    )
    parser.add_argument(
        "--agent-ping",
        required=True,
        help=(
            "what Systems Manager said about the machine while it was still running. Required "
            "for the same reason as --remote-exit-status: it cannot be read once the janitor "
            "has stopped the machine, and this tool runs after that by design"
        ),
    )
    parser.add_argument("--aws-profile", default="sbsandbox")
    parser.add_argument("--aws-region", default="us-east-1")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    profile, region = arguments.aws_profile, arguments.aws_region
    try:
        described = aws_json(
            [
                "ec2",
                "describe-instances",
                "--instance-ids",
                arguments.instance_id,
                "--query",
                "Reservations[0].Instances[0].{State:State.Name,Tags:Tags}",
            ],
            profile=profile,
            region=region,
        )
        agent = aws_json(
            [
                "ssm",
                "describe-instance-information",
                "--filters",
                f"Key=InstanceIds,Values={arguments.instance_id}",
                "--query",
                "InstanceInformationList[0]",
            ],
            profile=profile,
            region=region,
        )
        sessions = aws_json(
            [
                "ssm",
                "describe-sessions",
                "--state",
                "History",
                "--filters",
                f"key=Target,value={arguments.instance_id}",
                "--query",
                "Sessions[0].SessionId",
            ],
            profile=profile,
            region=region,
        )
        stops = aws_json(
            [
                "cloudtrail",
                "lookup-events",
                "--lookup-attributes",
                "AttributeKey=EventName,AttributeValue=StopInstances",
                "--max-results",
                "50",
                "--query",
                "Events[].{User:Username,Time:EventTime,Resources:Resources[].ResourceName}",
            ],
            profile=profile,
            region=region,
        )
        # An object read after the machine is gone. --request-payer is unset and the bucket is
        # ours, so a 404 here is the working tier not having survived rather than a permission.
        head = aws_json(
            [
                "s3api",
                "head-object",
                "--bucket",
                SCRATCH_BUCKET,
                "--key",
                arguments.work_object.removeprefix(f"s3://{SCRATCH_BUCKET}/"),
                "--query",
                "ContentLength",
            ],
            profile=profile,
            region=region,
        )
    except CaptureFailedError as error:
        print(error.reason)
        return 2

    tags = {tag["Key"]: tag["Value"] for tag in (described.get("Tags") or [])}
    stopped_by = next(
        (
            event["User"]
            for event in stops
            if arguments.instance_id in (event.get("Resources") or [])
        ),
        "",
    )
    record = {
        "instance_id": arguments.instance_id,
        "launched_by": "edullm run",
        "lane_tag": tags.get(LANE_TAG_KEY, ""),
        "expires_at": tags.get("ExpiresAt", ""),
        "final_state": described.get("State", ""),
        # Preferred from the account where the machine is still up, so a drill run without
        # waiting for the reclaim cannot quietly record a ping nobody saw.
        "agent_ping": (agent or {}).get("PingStatus") or arguments.agent_ping,
        "session_id": sessions or "",
        "remote_command_ran": arguments.remote_exit_status is not None,
        "remote_exit_status": arguments.remote_exit_status,
        "work_object": arguments.work_object,
        "work_object_read_after_stop": bool(head),
        "warned_before_stop": any(key.endswith(WARNING_TAG_SUFFIX) for key in tags),
        "stopped_by": stopped_by,
    }
    write_record(RECORD_PATH, record)
    report(record)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
