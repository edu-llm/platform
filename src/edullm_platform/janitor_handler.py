"""The scheduled sweep: read the running instances, warn the near ones, stop the expired ones.

ONE DESCRIBE AND THEN ONE CALL PER MACHINE ACTED ON. describe-instances filtered on the running
state, and then create-tags or stop-instances, one instance at a time, for whatever the decision
function returned. Everything that is a judgement lives in edullm_platform.expiry_janitor and is
tested without an account.

It was one describe and two batched writes, which is fewer calls and was the wrong shape. Both
mutating verbs validate every id in a request before acting on any of them, so one machine that
refuses refuses the request for every machine sharing it -- measured, not feared. The extra cost
is one call per machine the sweep actually acts on, which on the eighteen-instance sweeps this
account produces is one or two calls beyond the describe, because a sweep acts on the machines
at their expiry rather than on everything it examines.

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
from collections.abc import Callable, Mapping, Sequence
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

__all__ = ["Ec2Client", "SweepIncomplete", "handler"]


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


class SweepIncomplete(RuntimeError):
    """A sweep reached every machine it was asked to and could not act on all of them.

    Raised after the summary has been printed, never instead of it, and carrying that same
    summary so a caller holding the exception holds the whole sweep rather than a sentence
    about the part that failed.
    """

    def __init__(self, summary: dict[str, object]) -> None:
        self.summary = summary
        refusals = cast(Sequence[Mapping[str, str]], summary.get("refusals", ()))
        super().__init__(
            "the sweep finished and could not act on "
            + ", ".join(
                f"{one['instance_id']} ({one['action']}: {one['code']})" for one in refusals
            )
            + ". Every other machine was acted on and the swept_at line above this one carries "
            "the whole sweep. This invocation fails on purpose: a machine that is expired, "
            "warned and unstoppable is the case somebody has to hear about, and a sweep that "
            "returned cleanly would report it only to a log nobody reads."
        )


def _act(
    call: Callable[..., Any],
    request: Mapping[str, Any],
    instance_id: str,
    action: str,
    refusals: list[dict[str, str]],
) -> bool:
    """Make one EC2 call for one machine, and record a refusal rather than raising it.

    ONE CALL PER MACHINE, AND THE COST IS THE POINT RATHER THAN AN OVERSIGHT. Both mutating
    EC2 verbs here validate every id in a request before acting on any of them, so a batched
    call is an all-or-nothing sweep: one instance carrying DisableApiStop, or one deleted
    between the describe and the stop, refuses the request for every machine in it. That was
    measured on 2026-08-06 -- a stop-protected machine kept an ordinary expired one running
    across two sweeps and their retries -- and the blast radius of one bad machine has to be
    that machine.

    ``except Exception`` and not a named type, deliberately. botocore is in the runtime and
    not in pyproject.toml -- see this module's docstring -- so naming ClientError here would
    put the SDK into the admission validator's zip. The width is bounded instead by what is
    inside the ``try``, which is one client call and nothing else: no decision, no arithmetic
    and no formatting can be swallowed by it. And nothing is swallowed in any case, because
    every refusal is recorded, printed and then raised.
    """
    try:
        call(**request)
    except Exception as error:  # noqa: BLE001 -- botocore's type is not importable here
        refusals.append(
            {
                "instance_id": instance_id,
                "action": action,
                "code": _error_code(error),
                "detail": str(error),
            }
        )
        return False
    return True


def _error_code(error: BaseException) -> str:
    """The AWS error code out of a botocore exception, without importing botocore.

    ``OperationNotPermitted`` is groupable and ``An error occurred (OperationNotPermitted)
    when calling ...`` is a sentence, so the code is what a reader greps and an alarm counts.
    The class name is the fallback for everything that is not a client error, which keeps a
    timeout or a connection failure reported in the same shape rather than as an empty field.
    """
    response = getattr(error, "response", None)
    if isinstance(response, Mapping):
        code = cast(Mapping[str, Any], response).get("Error", {})
        if isinstance(code, Mapping):
            named = code.get("Code")
            if isinstance(named, str) and named:
                return named
    return type(error).__name__


def handler(
    event: Mapping[str, Any],
    context: object = None,
    *,
    client: Ec2Client | None = None,
) -> dict[str, object]:
    """One sweep. Prints the decisions as one JSON line, and returns the counts.

    The event is ignored: this is a schedule, so there is nothing in the payload. It is in the
    signature because Lambda passes one.

    WARNING AND STOPPING ARE NEVER THE SAME SWEEP, and that is enforced by the decision function
    rather than here -- a machine that has just been warned carries no warned-at tag until this
    call writes it, and the next sweep is the first that can see it. Writing the tag before
    stopping in one pass would satisfy every unit test and destroy somebody's work.

    THE SUMMARY IS PRINTED BEFORE ANYTHING CAN RAISE, AND THAT ORDER IS THE HALF OF THIS
    FUNCTION MOST WORTH KEEPING. It used to be printed after the stop, so a sweep that could
    not stop one machine printed nothing at all -- not the eighteen it examined, not the one it
    warned, not the one it did stop. Two such sweeps on 2026-08-06 are simply missing from the
    record, and a reclaim service whose failure mode is producing nothing is indistinguishable
    from a quiet night. Every refusal is collected, the line goes out, and only then does the
    invocation fail.
    """
    settings = _settings_from_environment()
    ec2 = client if client is not None else _default_client()
    now = datetime.now(tz=UTC)
    decisions = decide_expiry_actions(_instances(ec2), now=now, settings=settings)

    warned = [one for one in decisions if one.action is ExpiryAction.WARN]
    stopped = [one for one in decisions if one.action is ExpiryAction.STOP]
    refusals: list[dict[str, str]] = []
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    acted: dict[str, bool] = {}
    for one in warned:
        acted[one.instance_id] = _act(
            ec2.create_tags,
            {
                "Resources": [one.instance_id],
                "Tags": [{"Key": WARNING_TAG_KEY, "Value": stamp}],
            },
            one.instance_id,
            ExpiryAction.WARN.value,
            refusals,
        )
    for one in stopped:
        acted[one.instance_id] = _act(
            ec2.stop_instances,
            {"InstanceIds": [one.instance_id]},
            one.instance_id,
            ExpiryAction.STOP.value,
            refusals,
        )

    summary: dict[str, object] = {
        "swept_at": stamp,
        "examined": len(decisions),
        "warned": len(warned),
        "stopped": len(stopped),
        "left": sum(1 for one in decisions if one.action is ExpiryAction.LEAVE),
        "skipped": sum(1 for one in decisions if one.action is ExpiryAction.SKIP),
        # What was decided against what happened, kept as separate numbers rather than one.
        # `warned` and `stopped` are judgements and are what makes the arithmetic add up to
        # `examined`; these two are outcomes. Collapsing them would make a sweep that stopped
        # nothing because there was nothing to stop read the same as one that stopped nothing
        # because every machine refused.
        "warnings_written": sum(1 for one in warned if acted[one.instance_id]),
        "stops_completed": sum(1 for one in stopped if acted[one.instance_id]),
        "refused": len(refusals),
        "refusals": refusals,
        "decisions": [_as_line(one, acted) for one in decisions],
    }
    print(json.dumps(summary, sort_keys=True))
    if refusals:
        raise SweepIncomplete(summary)
    return summary


def _as_line(decision: ExpiryDecision, acted: Mapping[str, bool]) -> dict[str, str]:
    """One instance's judgement and what came of it.

    ``action`` is what the sweep decided and ``outcome`` is what the account allowed, and they
    are two fields because they disagree exactly when somebody needs to know. A line reading
    ``stop`` with no outcome beside it was read for two sweeps as a machine that had been
    stopped.
    """
    done = acted.get(decision.instance_id)
    return {
        "instance_id": decision.instance_id,
        "action": decision.action.value,
        "reason": decision.reason,
        "outcome": "nothing_to_do" if done is None else ("done" if done else "refused"),
    }
