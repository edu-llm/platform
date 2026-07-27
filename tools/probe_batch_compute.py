"""Find out whether a Batch job actually runs before building a phase around one.

**The question, and why a compute environment reporting ``VALID`` is not the answer.**
``VALID``/``ENABLED`` says Batch accepted the instance type, the subnets, the security
group and the instance profile. It says nothing at all about whether a job can be placed,
because **Batch does not fail a job it cannot place -- it waits.** A subnet in an
availability zone that does not offer the instance type, a security group with no egress,
a missing service-linked role, an image the host cannot pull: every one of those produces a
job sitting in ``RUNNABLE`` indefinitely, with no error, no failed execution and nothing in
the console that reads as broken. Only a job observed in ``RUNNING`` establishes that the
placement, the egress and the registry pull all work.

So this probe's verdict is not "did the environment come up". It is **did a job leave
``RUNNABLE``**, and expiry of the hard timeout is a negative result rather than an
inconclusive one. Exit 1 says so.

**What it records.** Whether the compute environment reached ``VALID``/``ENABLED`` and its
``statusReason`` if not; every job status it observed and when; whether the job left
``RUNNABLE``, and the exact ``statusReason`` if it did not; whether the image pull
succeeded, which is readable from the job reaching ``RUNNING`` and from the
``CannotPullContainerError`` shape of the reason when it does not; and how long placement
took, measured from submission to the first observation of ``RUNNING``.

**Everything it creates, it deletes.** A compute environment, a job queue and a job
definition, all named ``sbsandbox-intern-edullm-batch-probe-*``, torn down in a ``finally``
so a crash midway does not strand them. Batch's teardown is ordered and slow -- a queue
must be ``DISABLED`` before it can be deleted, it must be gone before the compute
environment it points at can be disabled, and each transition is asynchronous -- so the
teardown polls rather than assuming. A teardown that could not finish is reported and exits
2 whatever else the run established, because a stranded compute environment in a shared
account is a running Auto Scaling group somebody else pays for.

**It does not create the IAM it passes.** ``--instance-profile-name`` names an instance
profile that already exists, for the same reason ``tools/probe_conditional_write.py`` does
not create its execution role: a probe that mints IAM puts role creation on a code path
nobody reviews. ``infra/iam/batch-roles.yaml`` declares the one this expects, and
``AWSServiceRoleForBatch`` has to exist as well -- ``infra/README.md`` has that command.

**``--dry-run`` prints the calls and makes none of them.** Four values are not knowable
without a call -- the account inside the instance profile ARN, the compute environment ARN,
the job id and each job's ARN -- and those print as named placeholders. Every other token is
literal. Pass the same command line without ``--dry-run`` to execute it.

**The account never reaches the record.** The image reference is an argument because it
carries the registry host, and the registry host is the account id with a suffix; only the
repository and digest half of it is written down.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shlex
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from edullm_platform.evidence import (
    AWS_ACCOUNT_ID_PLACEHOLDER,
    redact_aws_account_ids,
    redact_content_digests,
    scan_for_secrets,
)
from edullm_platform.publisher_denials import parse_aws_cli_error

#: Everything this probe creates is named under the prefix this project owns, so a stranded
#: resource is attributable and a sweep can find it by name. Distinct from the deployed
#: names -- sbsandbox-intern-edullm-cpu -- so a probe run can never be mistaken for, or
#: collide with, the environment the phase actually uses.
NAME_PREFIX: Final = "sbsandbox-intern-edullm-batch-probe-"

#: Batch resource names are alphanumerics, hyphens and underscores, up to 128 characters.
#: The prefix above is already 38 of them.
NAME_SUFFIX_PATTERN: Final = re.compile(r"[a-z0-9][a-z0-9-]{0,19}")

#: What the probe job does: prints one line and exits 0. Deliberately not a sleep and not a
#: workload -- the question is whether anything runs at all, and a job that prints proves
#: the image, the pull, the placement and the log path in one.
PROBE_COMMAND: Final = ("python", "-c", "print('sbsandbox-intern-edullm-batch-probe: ran')")

AWS_CALL_TIMEOUT_SECONDS: Final = 60

#: How often each poll asks, and how long the resource lifecycle transitions may take.
#: Creating a compute environment stands up an ECS cluster and an Auto Scaling group behind
#: it, and deleting one is slower still.
POLL_SECONDS: Final = 10
LIFECYCLE_TIMEOUT_SECONDS: Final = 600

#: The bound that makes an expiry a finding. An instance has to be launched and joined to
#: the cluster before a job can be placed, which is two to three minutes from cold; fifteen
#: is generous for that and short enough that "it is still waiting" means something.
DEFAULT_PLACEMENT_TIMEOUT_SECONDS: Final = 900

#: Batch's own job states, in the order a job passes through them. Recorded as a set so an
#: observation of something not in it is visible rather than silently classified.
TERMINAL_JOB_STATUSES: Final = frozenset({"SUCCEEDED", "FAILED"})
PLACED_JOB_STATUSES: Final = frozenset({"STARTING", "RUNNING", "SUCCEEDED", "FAILED"})

#: How much of a service reason is recorded. Truncation happens before redaction and the
#: result is re-scanned afterwards, so a cut that split a credential run cannot produce text
#: the scan then accepts.
MAXIMUM_RECORDED_REASON: Final = 4096

#: Values discovered at run time, spelled this way in the ``--dry-run`` plan.
COMPUTE_ENVIRONMENT_ARN_PLACEHOLDER: Final = "<compute-environment-arn>"
JOB_ID_PLACEHOLDER: Final = "<job-id>"

#: Why a piece of captured text is absent from the record rather than present and masked.
WITHHELD_CARRIES_A_TOKEN: Final = "carries_a_token_the_secret_scan_refuses"

#: What Batch says when the host could not fetch the image. Matched case-insensitively and
#: only to classify; the reason itself is recorded verbatim beside the classification, so a
#: reason this does not recognise is still readable.
IMAGE_PULL_FAILURE: Final = re.compile(r"(?i)cannotpullcontainer|image.{0,20}not\s*found|manifest")


class ProbeFailedError(RuntimeError):
    """The probe could not be run, so there is nothing honest to write down.

    Carries a machine-readable reason and never a service message, because the reason is
    printed and an AWS error message names the account.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class PlannedCall:
    """One AWS CLI call, what it is for, and whether it is made more than once."""

    purpose: str
    arguments: tuple[str, ...]
    repeated: bool = False

    def command(self, *, profile: str, region: str) -> list[str]:
        return [
            "aws",
            *self.arguments,
            "--profile",
            profile,
            "--region",
            region,
            "--output",
            "json",
        ]


@dataclass(frozen=True)
class ProbeNames:
    """What this run calls the things it creates."""

    suffix: str

    @property
    def compute_environment(self) -> str:
        return f"{NAME_PREFIX}{self.suffix}"

    @property
    def job_queue(self) -> str:
        return f"{NAME_PREFIX}{self.suffix}"

    @property
    def job_definition(self) -> str:
        return f"{NAME_PREFIX}{self.suffix}"

    @property
    def job(self) -> str:
        return f"{NAME_PREFIX}{self.suffix}"


@dataclass(frozen=True)
class ProbeShape:
    """What the throwaway environment is made of, all of it from the command line."""

    instance_type: str
    instance_profile_name: str
    subnet_ids: tuple[str, ...]
    security_group_id: str
    image: str
    vcpus: int
    memory_mib: int
    placement_timeout_seconds: int


@dataclass(frozen=True)
class ProbeContext:
    aws_profile: str
    aws_region: str
    names: ProbeNames
    shape: ProbeShape
    observed_at: datetime
    #: Read from STS and used only to build the instance profile ARN. Never written down.
    account_id: str = ""

    @property
    def instance_profile_arn(self) -> str:
        return f"arn:aws:iam::{self.account_id}:instance-profile/{self.shape.instance_profile_name}"


# --------------------------------------------------------------------------------------
# Writing down what a service said
# --------------------------------------------------------------------------------------


def record_service_text(text: str | None) -> dict[str, str | None]:
    """Mask a service's own words, or withhold them and say that is what happened.

    Account IDs first, then content digests, which is the order the rest of this repository
    uses and the only one that works: masking a digest first leaves twelve of its
    characters looking like an account ID. Text that still carries something the scan
    refuses is withheld rather than laundered.
    """
    if text is None:
        return {"text": None, "withheld_reason": None}
    truncated = text[:MAXIMUM_RECORDED_REASON]
    try:
        masked = redact_content_digests(redact_aws_account_ids(truncated))
        scan_for_secrets(masked)
    except ValueError:
        return {"text": None, "withheld_reason": WITHHELD_CARRIES_A_TOKEN}
    return {"text": masked, "withheld_reason": None}


def repository_and_digest(image: str) -> str:
    """The half of an image reference that is not the account.

    ``<account>.dkr.ecr.<region>.amazonaws.com/repo@sha256:...`` becomes ``repo@sha256:...``.
    The registry host is the account id with a suffix, so recording the whole reference
    would put the account into a file the secret scan then refuses.
    """
    return image.rsplit("/", 1)[-1]


# --------------------------------------------------------------------------------------
# The calls, built once and used by both the plan and the run
# --------------------------------------------------------------------------------------


def caller_identity_call() -> PlannedCall:
    return PlannedCall(
        purpose="confirm the session and read the account the instance profile ARN needs",
        arguments=("sts", "get-caller-identity"),
    )


def compute_resources(context: ProbeContext) -> dict[str, Any]:
    shape = context.shape
    return {
        "type": "EC2",
        "allocationStrategy": "BEST_FIT_PROGRESSIVE",
        # Zero, so the environment holds nothing before the job is submitted and nothing
        # after it finishes. A probe that left capacity behind between its own steps would
        # not be measuring placement from cold, which is the case that fails.
        "minvCpus": 0,
        "maxvCpus": shape.vcpus,
        "instanceTypes": [shape.instance_type],
        "subnets": list(shape.subnet_ids),
        "securityGroupIds": [shape.security_group_id],
        "instanceRole": context.instance_profile_arn,
    }


def container_properties(context: ProbeContext) -> dict[str, Any]:
    shape = context.shape
    return {
        "image": shape.image,
        "command": list(PROBE_COMMAND),
        "resourceRequirements": [
            {"type": "VCPU", "value": str(shape.vcpus)},
            {"type": "MEMORY", "value": str(shape.memory_mib)},
        ],
    }


def create_compute_environment_call(context: ProbeContext) -> PlannedCall:
    return PlannedCall(
        purpose="create the throwaway compute environment",
        arguments=(
            "batch",
            "create-compute-environment",
            "--compute-environment-name",
            context.names.compute_environment,
            "--type",
            "MANAGED",
            "--state",
            "ENABLED",
            "--compute-resources",
            json.dumps(compute_resources(context), separators=(",", ":"), sort_keys=True),
        ),
    )


def describe_compute_environment_call(names: ProbeNames) -> PlannedCall:
    return PlannedCall(
        purpose="read the compute environment's status and statusReason",
        arguments=(
            "batch",
            "describe-compute-environments",
            "--compute-environments",
            names.compute_environment,
        ),
        repeated=True,
    )


def create_job_queue_call(names: ProbeNames, *, compute_environment_arn: str) -> PlannedCall:
    order = [{"order": 1, "computeEnvironment": compute_environment_arn}]
    return PlannedCall(
        purpose="create the throwaway job queue",
        arguments=(
            "batch",
            "create-job-queue",
            "--job-queue-name",
            names.job_queue,
            "--state",
            "ENABLED",
            "--priority",
            "1",
            "--compute-environment-order",
            json.dumps(order, separators=(",", ":"), sort_keys=True),
        ),
    )


def describe_job_queue_call(names: ProbeNames) -> PlannedCall:
    return PlannedCall(
        purpose="read the job queue's status",
        arguments=("batch", "describe-job-queues", "--job-queues", names.job_queue),
        repeated=True,
    )


def register_job_definition_call(context: ProbeContext) -> PlannedCall:
    return PlannedCall(
        purpose="register the throwaway job definition",
        arguments=(
            "batch",
            "register-job-definition",
            "--job-definition-name",
            context.names.job_definition,
            "--type",
            "container",
            "--platform-capabilities",
            "EC2",
            "--container-properties",
            json.dumps(container_properties(context), separators=(",", ":"), sort_keys=True),
            "--timeout",
            json.dumps({"attemptDurationSeconds": 600}, separators=(",", ":")),
        ),
    )


def submit_job_call(names: ProbeNames) -> PlannedCall:
    return PlannedCall(
        purpose="submit the one job that prints a line and exits 0",
        arguments=(
            "batch",
            "submit-job",
            "--job-name",
            names.job,
            "--job-queue",
            names.job_queue,
            "--job-definition",
            names.job_definition,
        ),
    )


def describe_jobs_call(job_id: str) -> PlannedCall:
    return PlannedCall(
        purpose="read the job's status and statusReason, which is the whole measurement",
        arguments=("batch", "describe-jobs", "--jobs", job_id),
        repeated=True,
    )


def deregister_job_definition_call(names: ProbeNames) -> PlannedCall:
    return PlannedCall(
        purpose="tear down the job definition",
        arguments=("batch", "deregister-job-definition", "--job-definition", names.job_definition),
    )


def disable_job_queue_call(names: ProbeNames) -> PlannedCall:
    return PlannedCall(
        purpose="disable the job queue, which Batch requires before it can be deleted",
        arguments=(
            "batch",
            "update-job-queue",
            "--job-queue",
            names.job_queue,
            "--state",
            "DISABLED",
        ),
    )


def delete_job_queue_call(names: ProbeNames) -> PlannedCall:
    return PlannedCall(
        purpose="tear down the job queue",
        arguments=("batch", "delete-job-queue", "--job-queue", names.job_queue),
    )


def disable_compute_environment_call(names: ProbeNames) -> PlannedCall:
    return PlannedCall(
        purpose="disable the compute environment, which Batch requires before it can be deleted",
        arguments=(
            "batch",
            "update-compute-environment",
            "--compute-environment",
            names.compute_environment,
            "--state",
            "DISABLED",
        ),
    )


def delete_compute_environment_call(names: ProbeNames) -> PlannedCall:
    return PlannedCall(
        purpose="tear down the compute environment",
        arguments=(
            "batch",
            "delete-compute-environment",
            "--compute-environment",
            names.compute_environment,
        ),
    )


def planned_calls(context: ProbeContext) -> tuple[PlannedCall, ...]:
    """Every call one run makes, in order, with run-time values left as placeholders."""
    names = context.names
    return (
        caller_identity_call(),
        create_compute_environment_call(context),
        describe_compute_environment_call(names),
        create_job_queue_call(names, compute_environment_arn=COMPUTE_ENVIRONMENT_ARN_PLACEHOLDER),
        describe_job_queue_call(names),
        register_job_definition_call(context),
        submit_job_call(names),
        describe_jobs_call(JOB_ID_PLACEHOLDER),
        deregister_job_definition_call(names),
        disable_job_queue_call(names),
        delete_job_queue_call(names),
        disable_compute_environment_call(names),
        delete_compute_environment_call(names),
    )


# --------------------------------------------------------------------------------------
# Talking to the account
# --------------------------------------------------------------------------------------


def run_aws(context: ProbeContext, call: PlannedCall) -> subprocess.CompletedProcess[str]:
    command = call.command(profile=context.aws_profile, region=context.aws_region)
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=AWS_CALL_TIMEOUT_SECONDS,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeFailedError(f"aws_call_timed_out:{call.arguments[1]}") from exc
    except OSError as exc:
        raise ProbeFailedError("aws_cli_unavailable") from exc


def call_failure_reason(call: PlannedCall, stderr: str) -> str:
    """A reason token for a failed call, quoting the operation but not the message."""
    error = parse_aws_cli_error(stderr)
    if error is None:
        return f"aws_call_failed:{call.arguments[1]}"
    return f"aws_call_failed:{error.operation}:{error.code}"


def aws_json(context: ProbeContext, call: PlannedCall) -> dict[str, Any]:
    """Run one call and return its JSON answer, or stop the probe."""
    completed = run_aws(context, call)
    if completed.returncode != 0:
        raise ProbeFailedError(call_failure_reason(call, completed.stderr))
    if not completed.stdout.strip():
        # A delete answers with nothing at all, which is an answer rather than a failure.
        return {}
    try:
        payload = json.loads(completed.stdout)
    except ValueError as exc:
        raise ProbeFailedError(f"aws_answer_unreadable:{call.arguments[1]}") from exc
    if not isinstance(payload, dict):
        raise ProbeFailedError(f"aws_answer_was_not_an_object:{call.arguments[1]}")
    return payload


def read_account_id(context: ProbeContext) -> str:
    """Ask STS for the account, which is what the instance profile ARN needs.

    Used inside this process and never written down. ``GetCallerIdentity`` needs no
    permission, so a failure here means the session itself is not usable -- which is worth
    finding out before a compute environment exists.
    """
    answer = aws_json(context, caller_identity_call())
    account_id = answer.get("Account")
    if not isinstance(account_id, str) or not account_id:
        raise ProbeFailedError("caller_identity_unreadable")
    return account_id


def first_item(payload: Mapping[str, Any], key: str, *, what: str) -> dict[str, Any]:
    """The one element of a Batch describe answer, or a failure that says which was empty."""
    items = payload.get(key)
    if not isinstance(items, list) or not items:
        raise ProbeFailedError(f"describe_returned_nothing:{what}")
    item = items[0]
    if not isinstance(item, dict):
        raise ProbeFailedError(f"describe_returned_a_non_object:{what}")
    return item


# --------------------------------------------------------------------------------------
# The observation
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LifecycleOutcome:
    """How a compute environment or a job queue settled, and what it said if it did not."""

    status: str
    state: str | None
    status_reason: Mapping[str, str | None]
    seconds_to_settle: int

    @property
    def valid(self) -> bool:
        return self.status == "VALID"


@dataclass(frozen=True)
class JobObservation:
    """Every status this job was seen in, in order, and how long each took to appear."""

    job_id: str
    #: ``(status, seconds since submission)``, appended only when the status changes.
    transitions: tuple[tuple[str, int], ...]
    final_status: str
    status_reason: Mapping[str, str | None]
    container_exit_code: int | None
    timed_out_waiting: bool

    @property
    def observed_statuses(self) -> tuple[str, ...]:
        return tuple(status for status, _seconds in self.transitions)

    @property
    def left_runnable(self) -> bool:
        """The measurement. Not "did it finish" and not "was the environment valid"."""
        return bool(PLACED_JOB_STATUSES & set(self.observed_statuses))

    @property
    def seconds_to_placement(self) -> int | None:
        for status, seconds in self.transitions:
            if status in PLACED_JOB_STATUSES:
                return seconds
        return None

    @property
    def image_pull(self) -> str:
        """Whether the host fetched the image, read off what the job managed to do."""
        if "RUNNING" in self.observed_statuses or self.final_status == "SUCCEEDED":
            return "succeeded"
        reason = self.status_reason.get("text")
        if isinstance(reason, str) and IMAGE_PULL_FAILURE.search(reason):
            return "failed"
        return "not_reached"


def wait_for(
    context: ProbeContext,
    call: PlannedCall,
    *,
    key: str,
    what: str,
    settled: frozenset[str],
) -> LifecycleOutcome:
    """Poll one Batch resource until its status settles, and record how it settled."""
    started = time.monotonic()
    deadline = started + LIFECYCLE_TIMEOUT_SECONDS
    while True:
        item = first_item(aws_json(context, call), key, what=what)
        status = item.get("status")
        if isinstance(status, str) and status in settled:
            state = item.get("state")
            reason = item.get("statusReason")
            return LifecycleOutcome(
                status=status,
                state=state if isinstance(state, str) else None,
                status_reason=record_service_text(reason if isinstance(reason, str) else None),
                seconds_to_settle=int(time.monotonic() - started),
            )
        if time.monotonic() >= deadline:
            raise ProbeFailedError(f"did_not_settle:{what}")
        time.sleep(POLL_SECONDS)


def watch_job(context: ProbeContext, job_id: str) -> JobObservation:
    """Watch one job until it is terminal or until the placement bound expires.

    The bound is not a convenience. A job that cannot be placed stays ``RUNNABLE`` forever
    and Batch reports nothing wrong, so a probe with no deadline would hang rather than
    produce the negative result that is the whole reason to run it.
    """
    call = describe_jobs_call(job_id)
    started = time.monotonic()
    deadline = started + context.shape.placement_timeout_seconds
    transitions: list[tuple[str, int]] = []
    status = "UNKNOWN"
    reason: str | None = None
    exit_code: int | None = None
    timed_out = False
    while True:
        job = first_item(aws_json(context, call), "jobs", what="job")
        observed = job.get("status")
        status = observed if isinstance(observed, str) else "UNKNOWN"
        if not transitions or transitions[-1][0] != status:
            transitions.append((status, int(time.monotonic() - started)))
        raw_reason = job.get("statusReason")
        reason = raw_reason if isinstance(raw_reason, str) else reason
        container = job.get("container")
        if isinstance(container, dict) and isinstance(container.get("exitCode"), int):
            exit_code = container["exitCode"]
        if status in TERMINAL_JOB_STATUSES:
            break
        if time.monotonic() >= deadline:
            timed_out = True
            break
        time.sleep(POLL_SECONDS)
    return JobObservation(
        job_id=job_id,
        transitions=tuple(transitions),
        final_status=status,
        status_reason=record_service_text(reason),
        container_exit_code=exit_code,
        timed_out_waiting=timed_out,
    )


@dataclass(frozen=True)
class ProbeObservation:
    compute_environment: LifecycleOutcome
    job_queue: LifecycleOutcome
    job: JobObservation

    @property
    def verdict(self) -> str:
        if not self.compute_environment.valid:
            return "compute_environment_did_not_become_valid"
        if not self.job_queue.valid:
            return "job_queue_did_not_become_valid"
        if not self.job.left_runnable:
            return "job_never_left_runnable"
        if self.job.final_status == "SUCCEEDED":
            return "job_ran_and_succeeded"
        if self.job.final_status == "FAILED":
            return "job_ran_and_failed"
        return "job_started_and_did_not_finish_in_time"

    @property
    def proved_a_job_can_run(self) -> bool:
        """Exit 0 only for this. Anything short of a placed job proves nothing."""
        return self.job.left_runnable and self.job.final_status == "SUCCEEDED"


def tear_down(
    context: ProbeContext,
    *,
    compute_environment_created: bool,
    job_queue_created: bool,
    job_definition_registered: bool,
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Delete everything this run created, in the one order Batch permits.

    A job queue must be ``DISABLED`` before it can be deleted, and it must be gone before
    the compute environment it points at can be disabled -- Batch refuses to delete a
    compute environment that any queue still references. Each of those transitions is
    asynchronous, so each is waited for rather than assumed, and every deletion is attempted
    whatever the ones before it answered: a job definition that could not be deregistered is
    no reason to leave an Auto Scaling group running.
    """
    record: dict[str, str] = {}
    stranded: list[str] = []

    def attempt(name: str, call: PlannedCall) -> bool:
        try:
            completed = run_aws(context, call)
        except ProbeFailedError as exc:
            record[name] = f"left_behind:{exc.reason}"
            stranded.append(name)
            return False
        if completed.returncode != 0:
            record[name] = f"left_behind:{call_failure_reason(call, completed.stderr)}"
            stranded.append(name)
            return False
        record[name] = "deleted"
        return True

    def describe(call: PlannedCall, key: str) -> dict[str, Any] | None:
        """The described resource, or ``None`` once Batch stops returning it."""
        payload = aws_json(context, call)
        items = payload.get(key)
        if not isinstance(items, list) or not items:
            return None
        return items[0] if isinstance(items[0], dict) else None

    def wait_until(name: str, call: PlannedCall, *, key: str, gone: bool) -> None:
        """Poll until the resource is settled, or gone, and record it if neither happens."""
        step = f"{name}_wait"
        deadline = time.monotonic() + LIFECYCLE_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                item = describe(call, key)
            except ProbeFailedError as exc:
                record[step] = f"unreadable:{exc.reason}"
                stranded.append(step)
                return
            if item is None:
                # Absent is the answer a delete is waiting for, and a surprise otherwise.
                if gone:
                    return
                record[step] = "vanished_before_it_settled"
                stranded.append(step)
                return
            if not gone and item.get("status") in ("VALID", "INVALID"):
                return
            time.sleep(POLL_SECONDS)
        record[step] = "did_not_settle"
        stranded.append(step)

    if job_definition_registered:
        attempt("job_definition", deregister_job_definition_call(context.names))
    else:
        record["job_definition"] = "not_created"

    if job_queue_created:
        queue_call = describe_job_queue_call(context.names)
        if attempt("job_queue_disable", disable_job_queue_call(context.names)):
            wait_until("job_queue_disable", queue_call, key="jobQueues", gone=False)
        attempt("job_queue", delete_job_queue_call(context.names))
        wait_until("job_queue_delete", queue_call, key="jobQueues", gone=True)
    else:
        record["job_queue"] = "not_created"

    if compute_environment_created:
        environment_call = describe_compute_environment_call(context.names)
        if attempt("compute_environment_disable", disable_compute_environment_call(context.names)):
            wait_until(
                "compute_environment_disable",
                environment_call,
                key="computeEnvironments",
                gone=False,
            )
        attempt("compute_environment", delete_compute_environment_call(context.names))
        wait_until(
            "compute_environment_delete",
            environment_call,
            key="computeEnvironments",
            gone=True,
        )
    else:
        record["compute_environment"] = "not_created"

    return record, tuple(stranded)


@dataclass(frozen=True)
class ProbeRun:
    """Everything one run established, including what it managed to clean up.

    ``observation`` and ``failure`` are exclusive, and the teardown is reported for both. A
    run that failed halfway still created things, and what happened to them is the part the
    operator has to act on first.
    """

    observation: ProbeObservation | None
    failure: str | None
    teardown: Mapping[str, str]
    stranded: tuple[str, ...]


def observe(context: ProbeContext) -> ProbeRun:
    """Create, submit one job, watch it, and tear down whatever happened in between."""
    compute_environment_created = False
    job_queue_created = False
    job_definition_registered = False
    observation: ProbeObservation | None = None
    failure: str | None = None
    try:
        created = aws_json(context, create_compute_environment_call(context))
        compute_environment_created = True
        arn = created.get("computeEnvironmentArn")
        if not isinstance(arn, str) or not arn:
            raise ProbeFailedError("compute_environment_not_created")
        environment = wait_for(
            context,
            describe_compute_environment_call(context.names),
            key="computeEnvironments",
            what="compute_environment",
            settled=frozenset({"VALID", "INVALID"}),
        )
        if not environment.valid:
            raise ProbeFailedError(f"compute_environment_{environment.status.lower()}")

        aws_json(context, create_job_queue_call(context.names, compute_environment_arn=arn))
        job_queue_created = True
        queue = wait_for(
            context,
            describe_job_queue_call(context.names),
            key="jobQueues",
            what="job_queue",
            settled=frozenset({"VALID", "INVALID"}),
        )
        if not queue.valid:
            raise ProbeFailedError(f"job_queue_{queue.status.lower()}")

        aws_json(context, register_job_definition_call(context))
        job_definition_registered = True

        submitted = aws_json(context, submit_job_call(context.names))
        job_id = submitted.get("jobId")
        if not isinstance(job_id, str) or not job_id:
            raise ProbeFailedError("job_not_submitted")
        observation = ProbeObservation(
            compute_environment=environment,
            job_queue=queue,
            job=watch_job(context, job_id),
        )
    except ProbeFailedError as exc:
        failure = exc.reason
    finally:
        teardown, stranded = tear_down(
            context,
            compute_environment_created=compute_environment_created,
            job_queue_created=job_queue_created,
            job_definition_registered=job_definition_registered,
        )
    return ProbeRun(
        observation=observation, failure=failure, teardown=teardown, stranded=stranded
    )


# --------------------------------------------------------------------------------------
# The record
# --------------------------------------------------------------------------------------


def build_record(
    context: ProbeContext,
    observation: ProbeObservation,
    teardown: Mapping[str, str],
) -> dict[str, Any]:
    job = observation.job
    return {
        "schema_version": 1,
        "probe": "batch-cpu-compute",
        "environment": "sandbox",
        "observed_at": context.observed_at.isoformat().replace("+00:00", "Z"),
        "region": context.aws_region,
        "compute_environment_name": context.names.compute_environment,
        "job_queue_name": context.names.job_queue,
        "job_definition_name": context.names.job_definition,
        # The name, not the ARN: the name identifies the profile and the ARN is the account
        # id with a name attached.
        "instance_profile_name": context.shape.instance_profile_name,
        "instance_type": context.shape.instance_type,
        "subnet_ids": list(context.shape.subnet_ids),
        "security_group_id": context.shape.security_group_id,
        # The repository and digest only. The registry host is the account id with a suffix.
        "image": repository_and_digest(context.shape.image),
        "command": list(PROBE_COMMAND),
        "placement_timeout_seconds": context.shape.placement_timeout_seconds,
        "compute_environment": {
            "status": observation.compute_environment.status,
            "state": observation.compute_environment.state,
            "status_reason": dict(observation.compute_environment.status_reason),
            "seconds_to_settle": observation.compute_environment.seconds_to_settle,
        },
        "job_queue": {
            "status": observation.job_queue.status,
            "state": observation.job_queue.state,
            "status_reason": dict(observation.job_queue.status_reason),
            "seconds_to_settle": observation.job_queue.seconds_to_settle,
        },
        "job": {
            "job_id": job.job_id,
            "transitions": [
                {"status": status, "seconds_since_submission": seconds}
                for status, seconds in job.transitions
            ],
            "final_status": job.final_status,
            "status_reason": dict(job.status_reason),
            "container_exit_code": job.container_exit_code,
            "timed_out_waiting": job.timed_out_waiting,
        },
        "findings": {
            # The four the phase plan asks for, and the second is the one that matters. A
            # compute environment reporting VALID proves nothing, because Batch does not
            # fail a job it cannot place -- it waits.
            "compute_environment_reached_valid_and_enabled": (
                observation.compute_environment.valid
                and observation.compute_environment.state == "ENABLED"
            ),
            "job_left_runnable": job.left_runnable,
            "image_pull": job.image_pull,
            "seconds_to_placement": job.seconds_to_placement,
        },
        "verdict": observation.verdict,
        "teardown": dict(teardown),
    }


def write_record(path: Path, record: Mapping[str, Any]) -> None:
    """Serialize the record, refuse it whole if anything in it would leak, then write."""
    serialized = json.dumps(record, indent=2, sort_keys=True) + "\n"
    try:
        scan_for_secrets(serialized)
    except ValueError as exc:
        raise ProbeFailedError("record_holds_a_credential") from exc
    path.write_text(serialized, encoding="utf-8")


# --------------------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Probe whether a Batch job placed on this networking actually leaves RUNNABLE."
        )
    )
    parser.add_argument("--aws-profile", required=True)
    parser.add_argument("--aws-region", required=True)
    parser.add_argument(
        "--subnet-ids",
        required=True,
        nargs="+",
        help=(
            "the subnets to place into. Pass only zones that offer the instance type: "
            "c7i.8xlarge is not offered in us-east-1e, and a subnet Batch can never place "
            "into produces a job that waits rather than one that errors."
        ),
    )
    parser.add_argument("--security-group-id", required=True)
    parser.add_argument(
        "--instance-profile-name",
        required=True,
        help=(
            "an existing instance profile the container hosts assume. This probe never "
            "creates IAM; infra/iam/batch-roles.yaml declares the one this expects."
        ),
    )
    parser.add_argument(
        "--image",
        required=True,
        help=(
            "the image to run, pinned by digest. Only the repository and digest half of it "
            "is written to the record; the registry host is the account id with a suffix."
        ),
    )
    parser.add_argument("--instance-type", default="c7i.8xlarge")
    parser.add_argument("--vcpus", type=int, default=32)
    parser.add_argument("--memory-mib", type=int, default=61440)
    parser.add_argument(
        "--placement-timeout-seconds",
        type=int,
        default=DEFAULT_PLACEMENT_TIMEOUT_SECONDS,
        help=(
            "how long a job may sit in RUNNABLE before the run is called a failure. Expiry "
            "is a negative result, not an inconclusive one."
        ),
    )
    parser.add_argument(
        "--name-suffix",
        default=None,
        help=(
            "what the throwaway environment, queue and definition are called after the "
            f"{NAME_PREFIX} prefix. Defaults to a fresh random suffix."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help=(
            "where the record is written. Required for --dry-run too, so the command line "
            "that gets reviewed is the one that runs."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the calls this run would make, and make none of them.",
    )
    return parser


def resolve_suffix(requested: str | None) -> str:
    suffix = requested if requested is not None else secrets.token_hex(4)
    if NAME_SUFFIX_PATTERN.fullmatch(suffix) is None:
        raise ProbeFailedError("name_suffix_unusable")
    return suffix


def dry_run_plan(context: ProbeContext) -> dict[str, Any]:
    planning = replace(context, account_id=AWS_ACCOUNT_ID_PLACEHOLDER)
    return {
        "dry_run": True,
        "compute_environment_name": planning.names.compute_environment,
        "job_queue_name": planning.names.job_queue,
        "job_definition_name": planning.names.job_definition,
        "compute_resources": compute_resources(planning),
        "container_properties": {
            **container_properties(planning),
            "image": repository_and_digest(planning.shape.image),
        },
        "placement_timeout_seconds": planning.shape.placement_timeout_seconds,
        "planned_calls": [
            {
                "purpose": call.purpose,
                "command": shlex.join(
                    call.command(profile=planning.aws_profile, region=planning.aws_region)
                ),
                "repeated_until_it_settles": call.repeated,
            }
            for call in planned_calls(planning)
        ],
    }


def report_teardown(stranded: Sequence[str], teardown: Mapping[str, str]) -> None:
    for name in stranded:
        print(f"teardown_incomplete:{name}:{teardown[name]}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    try:
        names = ProbeNames(suffix=resolve_suffix(arguments.name_suffix))
    except ProbeFailedError as exc:
        print(exc.reason, file=sys.stderr)
        return 2

    shape = ProbeShape(
        instance_type=arguments.instance_type,
        instance_profile_name=arguments.instance_profile_name,
        subnet_ids=tuple(arguments.subnet_ids),
        security_group_id=arguments.security_group_id,
        image=arguments.image,
        vcpus=arguments.vcpus,
        memory_mib=arguments.memory_mib,
        placement_timeout_seconds=arguments.placement_timeout_seconds,
    )
    context = ProbeContext(
        aws_profile=arguments.aws_profile,
        aws_region=arguments.aws_region,
        names=names,
        shape=shape,
        observed_at=datetime.now(tz=UTC).replace(microsecond=0),
    )

    if arguments.dry_run:
        print(json.dumps(dry_run_plan(context), indent=2, sort_keys=True))
        return 0

    try:
        context = replace(context, account_id=read_account_id(context))
    except ProbeFailedError as exc:
        print(exc.reason, file=sys.stderr)
        return 2

    run = observe(context)
    if run.observation is None:
        print(run.failure, file=sys.stderr)
        report_teardown(run.stranded, run.teardown)
        return 2

    record = build_record(context, run.observation, run.teardown)
    try:
        write_record(arguments.output, record)
    except ProbeFailedError as exc:
        print(exc.reason, file=sys.stderr)
        return 2
    except OSError:
        print("output_unwritable", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "verdict": record["verdict"],
                "findings": record["findings"],
                "job_status_reason": run.observation.job.status_reason["text"],
                "teardown": record["teardown"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    if run.stranded:
        report_teardown(run.stranded, run.teardown)
        return 2
    if not run.observation.proved_a_job_can_run:
        print(f"batch_placement_not_proven:{record['verdict']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
