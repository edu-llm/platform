"""Hold every alarm on this platform to having somewhere to fire, and that somewhere to a reader.

**Six alarms existed on 2026-08-06 and every one had an empty ``AlarmActions``.** Four of
them were in ALARM and had been for hours. The thresholds were right, the dimensions were
right, the templates that declared them were reviewed, and none of it mattered, because a
state change with no action is a row in a console nobody opens. It was found by somebody
looking for something else.

An alarm that fires into nothing is worse than no alarm, and the reason is not rhetorical.
The next person to ask whether a condition is watched greps for it, finds an alarm, and
stops looking. The absence would have been noticed; the empty action was not.

This asks the whole question rather than half of it, because there are two ways to fire into
nothing and fixing only the first leaves the failure intact one layer along.

**Does every alarm have an action?** Read from the account, over every alarm whose name
begins with this platform's prefix, so an alarm added to a template nobody thought to wire is
caught by the same check as one that was wired and then edited.

**Does the action reach a person?** An SNS topic with no confirmed subscription accepts every
publish and returns a message id, so an alarm wired to one looks correct from every angle
except the one that matters. An email subscription is also not usable until the recipient
clicks a confirmation link, so ``PendingConfirmation`` is a subscription that exists, appears
in a listing, and delivers nothing. It is counted as absent here, which is what it is.

Nothing this prints carries an account id. Topic and alarm ARNs contain one, so alarms are
named and the topic is named, both of which are in this public repository already.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent

if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

from verify_deployed_lambdas import ERROR_CODE, EXIT_DISAGREES, EXIT_OK, EXIT_UNUSABLE

__all__ = [
    "ALARM_PREFIX",
    "CONFIRMED",
    "TOPIC_NAME",
    "build_parser",
    "main",
]

#: Every alarm this platform owns is named with it, and the templates that declare them are
#: held to it by tests/test_notifications_infrastructure.py. Reading the account by prefix
#: rather than reading the templates is deliberate: the question is whether the *deployed*
#: alarms fire anywhere, and an alarm created by hand or left behind by a deleted stack is
#: exactly the kind this should find.
ALARM_PREFIX: Final = "sbsandbox-intern-edullm-"

#: The topic infra/alarm-destination.yaml creates. Named rather than resolved from the export,
#: because a check that asked CloudFormation which topic the alarms should point at would be
#: asking the same tree that declared them and would agree with itself.
TOPIC_NAME: Final = "sbsandbox-intern-edullm-alarms"

#: What SNS calls a subscription that has been accepted by its recipient. Everything else --
#: `PendingConfirmation` above all -- is a row in a listing that delivers nothing.
CONFIRMED: Final = "Confirmed"


def _aws(arguments: Sequence[str], *, profile: str | None, region: str) -> str:
    call = ["aws", *arguments, "--region", region, *(["--profile", profile] if profile else [])]
    try:
        finished = subprocess.run(call, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        print(
            f"alarm_check_unusable\n`aws {arguments[0]} {arguments[1]}` did not complete "
            f"({error.__class__.__name__}), so nothing was read and nothing is claimed.",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(EXIT_UNUSABLE) from error
    if finished.returncode != 0:
        found = ERROR_CODE.search(finished.stderr)
        named = f"{found.group(1)} " if found else ""
        print(
            f"alarm_check_unusable\n`aws {arguments[0]} {arguments[1]}` was refused with "
            f"{named}(the CLI exited {finished.returncode}), so this run says nothing about "
            "whether the alarms reach anybody. The audit reader needs "
            "cloudwatch:DescribeAlarms, sns:GetTopicAttributes and "
            "sns:ListSubscriptionsByTopic, all declared in infra/iam/audit-reader-role.yaml. "
            "The full message is not printed because it names ARNs that carry the account id.",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(EXIT_UNUSABLE)
    return finished.stdout


def _alarms(*, profile: str | None, region: str) -> list[dict[str, Any]]:
    answer = _aws(
        [
            "cloudwatch",
            "describe-alarms",
            "--alarm-name-prefix",
            ALARM_PREFIX,
            "--query",
            "MetricAlarms[].{Name:AlarmName,State:StateValue,Actions:AlarmActions}",
            "--output",
            "json",
        ],
        profile=profile,
        region=region,
    )
    parsed = json.loads(answer or "[]")
    return parsed if isinstance(parsed, list) else []


def _confirmed_subscriptions(topic_arn: str, *, profile: str | None, region: str) -> int:
    answer = _aws(
        [
            "sns",
            "list-subscriptions-by-topic",
            "--topic-arn",
            topic_arn,
            "--query",
            "Subscriptions[].SubscriptionArn",
            "--output",
            "json",
        ],
        profile=profile,
        region=region,
    )
    parsed = json.loads(answer or "[]")
    if not isinstance(parsed, list):
        return 0
    # SNS reports an unconfirmed subscription's ARN as the literal `PendingConfirmation`
    # rather than as an ARN, which is the cheapest way to tell the two apart and needs no
    # second call per subscription.
    return sum(1 for one in parsed if isinstance(one, str) and one.startswith("arn:"))


def build_parser() -> argparse.ArgumentParser:
    """Named so tests/test_workflow_tool_arguments.py can import and read it."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--region", default="us-east-1")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    profile, region = options.profile, options.region

    findings: list[str] = []

    alarms = _alarms(profile=profile, region=region)
    if not alarms:
        print(
            "no_alarms_found\n"
            f"the account holds no alarm named {ALARM_PREFIX}*, so either every alarm this "
            "platform declares has been deleted or this was pointed at another account or "
            "region. Both answer identically and the second is worth ruling out first.",
            file=sys.stderr,
            flush=True,
        )
        return EXIT_DISAGREES

    silent = sorted(one["Name"] for one in alarms if not one.get("Actions"))
    if silent:
        findings.append(
            "alarms_fire_into_nothing\n"
            f"{len(silent)} of {len(alarms)} alarms have no alarm action, so they change "
            "state and tell nobody: " + ", ".join(silent) + ". Every alarm this platform "
            "declares sends to the topic in infra/alarm-destination.yaml, through an "
            "AlarmActions block naming the exported topic ARN."
        )

    # The topic ARN is built from the account the caller is already in rather than passed,
    # so this needs no account id in the repository and cannot be pointed at the wrong one.
    account = _aws(
        ["sts", "get-caller-identity", "--query", "Account", "--output", "text"],
        profile=profile,
        region=region,
    ).strip()
    topic_arn = f"arn:aws:sns:{region}:{account}:{TOPIC_NAME}"

    confirmed = _confirmed_subscriptions(topic_arn, profile=profile, region=region)
    if confirmed == 0:
        findings.append(
            "alarm_topic_reaches_nobody\n"
            f"{TOPIC_NAME} has no confirmed subscription, so every alarm wired to it "
            "publishes successfully and reaches no reader. An email subscription is not "
            "usable until the recipient clicks the confirmation link, and SNS reports one "
            "that has not been clicked as PendingConfirmation, which this counts as absent "
            "because that is what it is. infra/alarm-destination.yaml carries the subscribe "
            "command."
        )

    for finding in findings:
        print(finding, file=sys.stderr, flush=True)
    if findings:
        return EXIT_DISAGREES

    firing = sorted(one["Name"] for one in alarms if one.get("State") == "ALARM")
    print(
        f"All {len(alarms)} alarms send to {TOPIC_NAME}, which has {confirmed} confirmed "
        f"subscription{'' if confirmed == 1 else 's'}."
    )
    if firing:
        # Reported and not failed. Whether a condition is currently true is that alarm's
        # business and somebody has now been told about it by the topic; this job's question
        # is whether being told is possible at all. Failing here as well would conflate a
        # platform nobody can hear with a platform having a bad night, and the repair for the
        # two is nothing alike.
        print(f"Currently in ALARM, and the topic has said so: {', '.join(firing)}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
