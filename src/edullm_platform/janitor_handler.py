"""The scheduled sweep: read the running instances, warn the near ones, stop the expired ones.

TWO CALLS AND NO MORE. describe-instances filtered on the running state, and then create-tags
or stop-instances for whatever the decision function returned. Everything that is a judgement
lives in edullm_platform.expiry_janitor and is tested without an account.

boto3 and botocore are not project dependencies. Both are in the Lambda runtime, and adding
them to pyproject.toml would put the whole SDK into the admission validator's zip as well. So
the client is a Protocol and the real one is imported lazily -- the same discipline
lifecycle_handler.py uses, and for the same reason.

THE WARNING IS A TAG AND NOT A MESSAGE, and that is a scope decision rather than a design one.
system-overview.md makes the expiry warning an addressed event delivered by direct message; the
chat integration belongs to the code host and the instruments slice carries delivery as a task
gated on an external service. What is here is the durable half: the warning is recorded where
the machine is, so the stop that follows is provably not the first the researcher heard of it,
and the decisions are printed as one structured line for whoever delivers them.

THE SETTINGS COME FROM THE ENVIRONMENT AND NEVER FROM DISK. The zip carries no configuration --
tools/build_janitor_lambda.py declares none -- so config/reports/researcher-lane.yaml is not in
the package and reading it here would raise FileNotFoundError on the first sweep.
infra/expiry-janitor.yaml sets the three variables from that file, and
tests/test_janitor_infrastructure.py holds the template's values equal to it. What that buys is
a release digest that does not move when somebody edits a number, which is the coupling both
other builders were narrowed on 2026-08-04 to break.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from edullm_platform.expiry_janitor import (
    ExpiryAction,
    ExpiryDecision,
    TaggedInstance,
    decide_expiry_actions,
    instance_from_tags,
)
from edullm_platform.researcher_lane import WARNING_TAG_KEY, LaneSettings

__all__ = ["Ec2Client", "handler"]


def _settings_from_environment() -> LaneSettings:
    """The three numbers, out of the variables infra/expiry-janitor.yaml sets.

    ``os.environ[...]`` rather than ``.get(..., default)``, deliberately. A default here is a
    second copy of a number that lives in config/reports/researcher-lane.yaml, and the copy that
    wins is the one nobody reviewed. A function deployed without its environment should fail on
    its first sweep naming the variable, not sweep on a guess.

    Validated through LaneSettings rather than used raw, so the cross-field rule holds in the
    account as well as in the file: a warning lead shorter than the sweep interval means a
    machine can expire between two sweeps having never been warned.
    """
    return LaneSettings(
        schema_version=1,
        default_lifetime_hours=int(os.environ["EDULLM_DEFAULT_LIFETIME_HOURS"]),
        warning_lead_minutes=int(os.environ["EDULLM_WARNING_LEAD_MINUTES"]),
        sweep_minutes=int(os.environ["EDULLM_SWEEP_MINUTES"]),
    )


class Ec2Client(Protocol):
    def describe_instances(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def stop_instances(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def create_tags(self, **kwargs: Any) -> Mapping[str, Any]: ...


def _default_client() -> Ec2Client:
    import boto3  # type: ignore[import-not-found]  # in the runtime, not in pyproject

    return cast(Ec2Client, boto3.client("ec2"))


def _instances(client: Ec2Client) -> tuple[TaggedInstance, ...]:
    """Every instance that is running or stopping, as the janitor sees it.

    Filtered at the API rather than in Python, because a shared account holds other projects'
    terminated instances indefinitely and paging through them costs calls to reach a decision
    that is always LEAVE. ``stopping`` is included so a machine mid-stop is reported rather
    than silently absent from the denominator.
    """
    answer = client.describe_instances(
        Filters=[{"Name": "instance-state-name", "Values": ["running", "stopping"]}]
    )
    reservations = cast(Sequence[Mapping[str, Any]], answer.get("Reservations", ()))
    found: list[TaggedInstance] = []
    for reservation in reservations:
        for instance in cast(Sequence[Mapping[str, Any]], reservation.get("Instances", ())):
            tags = {
                str(tag["Key"]): str(tag["Value"])
                for tag in cast(Sequence[Mapping[str, Any]], instance.get("Tags", ()))
            }
            found.append(
                instance_from_tags(
                    str(instance["InstanceId"]),
                    str(instance["State"]["Name"]),
                    tags,
                )
            )
    return tuple(found)


def handler(
    event: Mapping[str, Any],
    context: object = None,
    *,
    client: Ec2Client | None = None,
) -> dict[str, object]:
    """One sweep. Returns the counts, and prints the decisions as one JSON line.

    The event is ignored: this is a schedule, so there is nothing in the payload. It is in the
    signature because Lambda passes one.

    WARNING AND STOPPING ARE NEVER THE SAME SWEEP, and that is enforced by the decision function
    rather than here -- a machine that has just been warned carries no warned-at tag until this
    call writes it, and the next sweep is the first that can see it. Writing the tag before
    stopping in one pass would satisfy every unit test and destroy somebody's work.
    """
    settings = _settings_from_environment()
    ec2 = client if client is not None else _default_client()
    now = datetime.now(tz=UTC)
    decisions = decide_expiry_actions(_instances(ec2), now=now, settings=settings)

    warned = [one for one in decisions if one.action is ExpiryAction.WARN]
    stopped = [one for one in decisions if one.action is ExpiryAction.STOP]
    if warned:
        ec2.create_tags(
            Resources=[one.instance_id for one in warned],
            Tags=[{"Key": WARNING_TAG_KEY, "Value": now.strftime("%Y-%m-%dT%H:%M:%SZ")}],
        )
    if stopped:
        ec2.stop_instances(InstanceIds=[one.instance_id for one in stopped])

    summary: dict[str, object] = {
        "swept_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "examined": len(decisions),
        "warned": len(warned),
        "stopped": len(stopped),
        "left": sum(1 for one in decisions if one.action is ExpiryAction.LEAVE),
        "skipped": sum(1 for one in decisions if one.action is ExpiryAction.SKIP),
        "decisions": [_as_line(one) for one in decisions],
    }
    print(json.dumps(summary, sort_keys=True))
    return summary


def _as_line(decision: ExpiryDecision) -> dict[str, str]:
    return {
        "instance_id": decision.instance_id,
        "action": decision.action.value,
        "reason": decision.reason,
    }
