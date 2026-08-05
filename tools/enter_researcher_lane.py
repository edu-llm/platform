"""Enter the edullm-researcher lane, declaring a project, a lifetime and who you are.

A PYTHON TOOL RATHER THAN THE SHELL SCRIPT docs-frank/reference/aws-spend-controls.md CARRIES.
The logic is that script's and the reasoning behind every line of it is there. What moves by
being here: the default lifetime is read from config/reports/researcher-lane.yaml instead of
being a required argument, the two computed values are unit-tested, and mypy --strict covers
it the way it covers every other tool in this directory.

WHAT THIS PRINTS IS AS IMPORTANT AS WHAT IT MINTS. The lane refuses a launch that does not tag
both the project and an absolute expiry, and neither tag is something a person will get right
from memory at the point of typing a run-instances command. So the exports and the exact
--tag-specifications block are printed, and the failure this prevents is a researcher meeting
an UnauthorizedOperation that names none of it.

It does not exec a shell. The shell version did, which makes the session hard to script and
impossible to test; printing the exports leaves the caller to `eval` them or not.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from edullm_platform.capture_tooling import CaptureFailedError, aws_json, report
from edullm_platform.researcher_lane import (
    EXPIRES_AT_TAG_KEY,
    PROJECT_TAG_KEY,
    RESEARCHER_ROLE_NAME,
    load_lane_settings,
)

__all__ = ["build_parser", "expires_at", "main", "source_identity_from"]

#: What STS accepts for a source identity: 2-64 characters from this set, not starting "aws:".
UNSAFE_IN_SOURCE_IDENTITY = re.compile(r"[^\w+=,.@-]")


def source_identity_from(caller_arn: str) -> str:
    """The person behind an assumed-role ARN, in a form STS will accept.

    The broker mints session names as ``broker-<person>-<epoch>``, so the person is recoverable
    from the session segment and from nowhere else in the identity -- the role name is close
    but is the dashboard's spelling rather than the caller's. A session that is not a broker
    session keeps its own name, which is worse attribution and better than an empty string:
    the trust policy's presence test refuses empty and the refusal talks about a tag.

    THE STRING THIS RETURNS IS ALSO A DIRECTORY NAME, which is the part worth not breaking.
    ``infra/iam/researcher-role.yaml``'s seventh statement excepts
    ``edullm-work/*/${aws:SourceIdentity}/*`` from a deny on every object write, so whatever
    comes back here is the only segment of the working tier this session may write into. The
    exploration route derives the same person from the same caller ARN when it picks that
    prefix. Change the derivation on one side only and every lane write is denied, naming
    nothing.

    Self-asserted either way. aws-spend-controls.md, "What the lane does not cover", records
    that nothing stops a researcher passing someone else's, so this is an attribution aid and
    not authentication.
    """
    session = caller_arn.rsplit("/", 1)[-1]
    session = re.sub(r"^broker-", "", session)
    session = re.sub(r"-\d+$", "", session)
    return UNSAFE_IN_SOURCE_IDENTITY.sub("-", session)[:64]


def expires_at(now: datetime, lifetime_hours: int) -> str:
    """The absolute UTC instant the machine may be stopped at, ISO-8601 with a Z.

    Absolute rather than a duration, for the two reasons aws-spend-controls.md gives under "The
    helper" and infra/iam/researcher-role.yaml repeats beside the statement that requires it.
    Seconds are included and sub-seconds are not, because the janitor compares this to a sweep
    that runs on a minute boundary.
    """
    return (now + timedelta(hours=lifetime_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_parser() -> argparse.ArgumentParser:
    """Named so tests/test_workflow_tool_arguments.py can import and read it."""
    settings = load_lane_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True, help="the project this work belongs to")
    parser.add_argument(
        "--lifetime-hours",
        type=int,
        default=settings.default_lifetime_hours,
        help="whole hours; the default is config/reports/researcher-lane.yaml's",
    )
    parser.add_argument("--aws-profile", default="sbsandbox")
    parser.add_argument("--aws-region", default="us-east-1")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        identity = aws_json(
            ["sts", "get-caller-identity"],
            profile=arguments.aws_profile,
            region=arguments.aws_region,
        )
        who = source_identity_from(str(identity["Arn"]))
        assumed = aws_json(
            [
                "sts",
                "assume-role",
                "--role-arn",
                f"arn:aws:iam::{identity['Account']}:role/{RESEARCHER_ROLE_NAME}",
                "--role-session-name",
                f"lane-{arguments.project}"[:64],
                "--source-identity",
                who,
                "--tags",
                f"Key=project,Value={arguments.project}",
                f"Key=lifetime,Value={arguments.lifetime_hours}",
                "--duration-seconds",
                "3600",
            ],
            profile=arguments.aws_profile,
            region=arguments.aws_region,
        )
    except CaptureFailedError as error:
        print(error.reason)
        return 2

    credentials = assumed["Credentials"]
    expiry = expires_at(datetime.now(tz=UTC), arguments.lifetime_hours)
    report(
        {
            "project": arguments.project,
            "source_identity": who,
            "session_expires": credentials["Expiration"],
            "machine_expires_at": expiry,
            "exports": [
                f"export AWS_ACCESS_KEY_ID={credentials['AccessKeyId']}",
                "export AWS_SECRET_ACCESS_KEY=<printed below>",
                "export AWS_SESSION_TOKEN=<printed below>",
                f"export AWS_REGION={arguments.aws_region}",
                f"export EDULLM_PROJECT={arguments.project}",
                f"export EDULLM_EXPIRES_AT={expiry}",
            ],
            "every_launch_must_carry": (
                "--tag-specifications "
                f"'ResourceType=instance,Tags=[{{Key={PROJECT_TAG_KEY},Value={arguments.project}}},"
                f"{{Key={EXPIRES_AT_TAG_KEY},Value={expiry}}}]' "
                f"'ResourceType=volume,Tags=[{{Key={PROJECT_TAG_KEY},Value={arguments.project}}}]'"
            ),
        }
    )
    print(f"export AWS_SECRET_ACCESS_KEY={credentials['SecretAccessKey']}")
    print(f"export AWS_SESSION_TOKEN={credentials['SessionToken']}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
