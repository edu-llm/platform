"""Hold each ``places`` verdict in ``config/capacity.yaml`` against what the queues show.

``config/capacity.yaml``'s header asks for this by name: an audit that reads the sixteen
queues, recomputes each verdict, and goes red when one disagrees with what is committed. It
was not built because the audit reader role held neither ``batch:ListJobs`` nor
``batch:DescribeJobs``; #227 added both and the stack is applied, so the job can ship without
being red from its first night.

**THE VERDICT AND NOT THE MEDIAN, WHICH IS A SMALLER THING THAN IT SOUNDS.** ``gpu-8xa100``
was written at a median of 89 minutes and a thirteenth run took it to 61 the next morning;
``gpu-4xa10g`` was 11 and became 7. No submitter has ever chosen differently on those numbers,
and a check that went red on them would be red most mornings for a difference nobody acts on.
Every error that file has made has been in the ``places`` column, so that column is what this
recomputes, and the wait sentences are left to a person.

**IT REPORTS AND WRITES NOTHING.** A bot editing a reviewed file that is nine tenths prose
needs a write credential, a pull-request path and a surgical anchored substitution, which is
three new failure points bought to save one hand edit. The role this runs as holds no write
anywhere in the account and that is deliberate: a check able to change what it is checking can
produce its own all-clear.

THE ASYMMETRY IS THE WHOLE DESIGN, AND GETTING IT WRONG IS HOW THE FILE ACCUMULATED EIGHT
WRONG ANSWERS
-------------------------------------------------------------------------------------------

Every one of those eight was a refused ``create-fleet --type instant`` probe recorded as a
verdict, and every one was overturned by a queue that kept asking. The instrument erred in one
direction only, because a refusal and an absence look identical from outside: a pool that has
nothing to sell and a pool nobody asked both return no machine.

So this makes claims only in the direction its evidence supports:

*A job that started is conclusive.* The account demonstrably held the machine. Nothing a later
reading can show will make that untrue, so a shape recorded as ``unreliably`` with a started
job on its queue is a definite disagreement and the one this exists to catch.

*A job that did not start is not conclusive.* It may have been refused for capacity, or asked
for more memory than the environment has, or been superseded, or simply still be waiting. So
an absence of placements never becomes ``unreliably`` on its own. It becomes that only where a
job's own record names capacity as the reason it never ran.

*A queue nothing has been submitted to has measured nothing.* Three shapes are in that state
and the file says so with ``measured_by: probe``. What this holds them to is the negative
claim, that no entry may say a queue measured it when no job has ever reached that queue. That
is the same error one level up, and it is the one this check could most easily make itself.

A QUOTA REFUSAL AND A CAPACITY REFUSAL LOOK IDENTICAL FROM ``RUNNABLE`` AND HAVE OPPOSITE
REMEDIES
-------------------------------------------------------------------------------------------

``gpu-8xa10g`` logged 871 ``VcpuLimitExceeded`` refusals against the then 768-vCPU G bucket on
2026-08-05, beside 105 ``InsufficientInstanceCapacity`` ones. Four ``g5.48xlarge`` is exactly
768 vCPUs, so the shape reached the account's own ceiling and queued behind itself. A submitter
who reads that as EC2 being short waits for weather to change; what it needed was a quota
increase, which is a person. The quota has since been raised to 3,696, so that shape's
behaviour moves over the coming days for a reason that is not capacity at all.

The separation here is structural rather than a matter of reading error strings, which is what
makes it hold when the strings change:

*A wait never produces a scarcity verdict.* However long a job sat before it started, this
records that it started. A job held back by a vCPU ceiling waits and then runs, so it lands in
the placements column where it belongs and can never be counted as EC2 refusing.

*Only a job that never ran, whose own record names capacity, counts as a capacity refusal.*
:data:`LIMIT_MARKERS` is tested before :data:`CAPACITY_MARKERS`, so a reason naming both a
ceiling and a shortage is read as the ceiling. That is the quiet direction: a limit never
justifies a finding about scarcity, and mistaking scarcity for a limit costs a report rather
than sending somebody to buy a Capacity Block against a support ticket.

Worth saying plainly: ``VcpuLimitExceeded`` does not reach a Batch job field at all. It is an
autoscaling failure, and ``autoscaling:DescribeScalingActivities`` is not a grant this role
holds. So the quota half of ``gpu-8xa10g``'s history is invisible here, and the protection
against reporting it as scarcity is the structural one above rather than the marker list.

WHERE THE THRESHOLD COMES FROM, WHICH IS NOT FROM ANYBODY'S JUDGEMENT
-------------------------------------------------------------------------------------------

One number separates ``reliably`` from ``after_a_wait`` and it is read off the queues rather
than chosen here. Every queue carries a ``JobStateTimeLimitActions`` entry that cancels a job
sitting in ``RUNNABLE`` under ``CAPACITY:INSUFFICIENT_INSTANCE_CAPACITY`` after
``MaxTimeSeconds``. ``infra/batch-compute-gpu.yaml`` says what that number is for:

    Thirty minutes is chosen against the wait it must not interrupt: this environment scales
    from zero and took two to three minutes to bring up a g5 on 2026-08-01, so the limit is an
    order of magnitude above the normal case.

That is this repository's own line between a cold scale-up and a job waiting on capacity, it
is declared once per queue in the templates, and it is read from there so the two cannot drift.
It falls between the two anchors the shipped file already encodes: ``gpu-1xa10g`` is
``reliably`` at a four-minute median, and ``gpu-8xa100`` is ``after_a_wait`` at sixty-one.

The median rather than the worst case, for the ``reliably`` test. ``cpu-32vcpu`` has placed 132
jobs at a median of 1.7 minutes and exactly one of them waited 68, and a worst-case rule would
call the CPU queue contended on the strength of that one. The median is also the quiet
direction, because the ad-hoc probes that land on nodes a submitted run has already warmed up
start in seconds and only pull it down.

WHAT IT WILL NOT ADJUDICATE, AND SAYING SO IS THE POINT
-------------------------------------------------------------------------------------------

``gpu-1xl40s`` places, on one job that started in 3.9 minutes, and the file records it
``after_a_wait`` rather than ``reliably`` on an argument the file states openly: on evidence
this thin the useful thing is to say what was seen, and ``reliably`` is the one verdict that
prints nothing at all. That is a judgement about how much to warn, not a measurement, and a
check that overturned it would be asserting ``reliably`` from a single observation.

So ``after_a_wait`` is never contradicted by a shape that placed promptly. It claims the shape
arrives and that there may be a wait, over-warning costs a submitter a sentence, and the file's
own position is that a warning is cheaper than a refusal. What is contradicted is the pair that
costs a researcher a day: ``unreliably`` on a shape that places, and ``reliably`` on a shape
that keeps people waiting past the queue's own capacity limit.

``gpu-1xl4`` is the other one it declines to judge. Its single job was cancelled for
``MISCONFIGURATION:JOB_RESOURCE_REQUIREMENT`` before it could test anything, and the g6.xlarge
that arrived for it is in the autoscaling history, which this cannot read. One job submitted is
enough for ``measured_by: queue`` to stand; nothing about capacity was established either way,
and the report says exactly that rather than reading the silence as a refusal.

**The two non-zero exits mean different things and a caller must not merge them.** Exit 1 says
the account and the file disagree and sends a reader to a compute profile. Exit 2 says this did
not manage to look and sends them to a credential or a grant. Reporting the second as the first
sends somebody re-measuring a pool on the morning a role lapsed; reporting it as a pass
silently stops the check covering anything.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml

from edullm_platform.placement import (
    CAPACITY_FILENAME,
    MEASURED_BY_A_PROBE,
    MEASURED_BY_A_QUEUE,
    PLACES_AFTER_A_WAIT,
    PLACES_RELIABLY,
    PLACES_UNRELIABLY,
    PlacementRecord,
    UnreadableCapacityError,
    read_capacity,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_ROOT = PROJECT_ROOT / "config"
INFRA_ROOT = PROJECT_ROOT / "infra"

__all__ = [
    "CAPACITY_MARKERS",
    "EXIT_DISAGREES",
    "EXIT_OK",
    "EXIT_UNUSABLE",
    "JOB_STATUSES",
    "LIMIT_MARKERS",
    "QUEUE_TEMPLATES",
    "Evidence",
    "Finding",
    "PlacementFinding",
    "QueueEvidence",
    "build_parser",
    "classify",
    "compare",
    "declared_queues",
    "main",
    "profile_queues",
    "read_jobs",
    "render",
]

EXIT_OK: Final = 0

#: The account and ``config/capacity.yaml`` disagree. A definite answer about a compute
#: profile, and the reader's next move is to go and look at that profile's entry.
EXIT_DISAGREES: Final = 1

#: Nothing was read, so nothing is claimed. Never reported as a pass, because a check that
#: could not look is not a check that found nothing.
EXIT_UNUSABLE: Final = 2

#: Every state a job can be listed under. ``batch:ListJobs`` takes one status per call and
#: defaults to ``RUNNING``, so all seven are asked for by name; a status left out would drop
#: whole jobs silently, and the two that matter most are the terminal ones.
JOB_STATUSES: Final = (
    "SUBMITTED",
    "PENDING",
    "RUNNABLE",
    "STARTING",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
)

#: A job still in one of these has not placed and has not been refused, so it is evidence of
#: neither. Its wait is real and still running, and it is reported as a wait in progress
#: rather than folded into the medians, which would let a verdict move every hour of a long
#: queue rather than when something is settled.
STATUSES_STILL_WAITING: Final = frozenset({"SUBMITTED", "PENDING", "RUNNABLE", "STARTING"})

#: How many jobs one ``batch:DescribeJobs`` call takes. The API's own ceiling.
DESCRIBE_BATCH: Final = 100

#: The templates that declare a job queue, which between them declare all sixteen. Read
#: rather than listed as names, because ``batch:DescribeJobQueues`` is not a grant this role
#: holds and a hand-written list of sixteen is the thing that falls behind the account.
#: A queue added to a template with no compute profile mapped to it is reported rather than
#: skipped, for the reason ``tools/verify_deployed_stacks.py`` gives about stacks.
QUEUE_TEMPLATES: Final = (
    "batch-compute.yaml",
    "batch-compute-gpu.yaml",
    "batch-compute-gpu-shapes.yaml",
)

#: The queue rule whose ``MaxTimeSeconds`` is the line between a cold scale-up and a wait.
CAPACITY_TIME_LIMIT_REASON: Final = "CAPACITY:INSUFFICIENT_INSTANCE_CAPACITY"

#: What a job's own record says when the account's ceiling stopped it rather than EC2. Tested
#: before :data:`CAPACITY_MARKERS`, so a reason naming both is read as a ceiling: a limit
#: never justifies a finding about scarcity, and the quiet direction is the safe one here.
#:
#: ``VcpuLimitExceeded`` is listed and has never appeared, because Batch does not surface it:
#: it is an autoscaling failure and this role cannot read scaling activities. It is named so
#: that the day Batch does put one in a ``statusReason``, it lands in this column rather than
#: in the one that means EC2 had nothing to sell.
LIMIT_MARKERS: Final = (
    "VcpuLimitExceeded",
    "InstanceLimitExceeded",
    "MaxSpotInstanceCountExceeded",
    "MISCONFIGURATION:COMPUTE_ENVIRONMENT_MAX_RESOURCE",
    "vCPU limit",
    "service quota",
)

#: What a job's own record says when EC2 had nothing to sell. Deliberately short, and every
#: entry is either AWS's own error name or a near-verbatim quote of it, because the rest of a
#: cancellation reason is free text a person typed. A conservative list fails toward "this is
#: not evidence", which can only make the check quieter; a generous one would read ``capacity
#: diagnosis complete`` -- a real reason on this account, from a probe that finished its work
#: -- as a pool refusing.
CAPACITY_MARKERS: Final = (
    "InsufficientInstanceCapacity",
    CAPACITY_TIME_LIMIT_REASON,
    "capacity unavailable",
)

#: How the CLI opens every service error. The code is the only part repeated, because the
#: rest of the line carries the calling ARN and every ARN carries the account id.
ERROR_CODE = re.compile(r"An error occurred \(([A-Za-z]+)\)")

#: What the sixteen queues are called. Used only to turn a queue name into a profile name
#: where ``config/execution-targets.yaml`` has no row for it, which is the state the two H100
#: profiles are in: their rows were withdrawn on 2026-08-04 because a submission routed there
#: can never place, and the queues they named are still deployed and still enabled.
QUEUE_NAME_PREFIX: Final = "sbsandbox-intern-edullm-"


class PlacementFinding(Exception):
    """This run could not establish something it needs, or the account contradicts the file.

    Carries a machine-readable reason first and a sentence naming what to do, the way the
    sibling verifiers do. ``code`` travels with the reason rather than being decided by the
    caller, so a failure mode added later has to choose which of the two non-zero exits it is
    instead of inheriting whichever the caller assumed.
    """

    def __init__(self, reason: str, detail: str, *, code: int) -> None:
        self.reason = reason
        self.detail = detail
        self.code = code
        super().__init__(f"{reason}: {detail}")


@dataclass(frozen=True)
class Finding:
    """One place the account and ``config/capacity.yaml`` disagree about a profile."""

    profile: str
    detail: str

    def __str__(self) -> str:
        return f"{self.profile}: {self.detail}"


@dataclass
class Evidence:
    """What the queue's own record establishes about one shape, and what it does not.

    The four counts are kept apart rather than netted off because they answer different
    questions and only two of them are conclusive. ``placements`` proves the account held the
    machine. ``capacity_refusals`` is a job that never ran and whose record names EC2 as the
    reason. ``limit_refusals`` is a job the account's own ceiling stopped, which is a support
    ticket rather than weather. ``silent`` is everything else, and folding it into the
    refusals is precisely the mistake that produced eight wrong answers.
    """

    profile: str
    queue: str
    waits: list[float] = field(default_factory=list)
    capacity_refusals: list[str] = field(default_factory=list)
    limit_refusals: list[str] = field(default_factory=list)
    silent: int = 0
    still_waiting: int = 0
    jobs: int = 0

    @property
    def placements(self) -> int:
        return len(self.waits)

    @property
    def median_wait(self) -> float | None:
        return statistics.median(self.waits) if self.waits else None

    @property
    def longest_wait(self) -> float | None:
        return max(self.waits) if self.waits else None


@dataclass(frozen=True)
class QueueEvidence:
    """Every queue's evidence, and the per-queue capacity time limit read off the templates."""

    by_profile: dict[str, Evidence]
    limits: dict[str, int]


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:  # pragma: no cover - only reachable outside a checkout
        return str(path)


def declared_queues(infra_root: Path = INFRA_ROOT) -> dict[str, int]:
    """Every job queue the templates declare, and its capacity time limit in seconds.

    Both halves come out of the same resource on purpose. The queue names are what the account
    holds, and the limit beside each is the number that queue will actually cancel a waiting
    job at, so a template that changed one queue's limit moves this check's threshold for that
    queue and for no other.

    A queue declaring no ``CAPACITY:INSUFFICIENT_INSTANCE_CAPACITY`` rule is refused rather
    than defaulted. The default would have to be a number chosen here, which is the one thing
    this function exists to avoid.
    """
    limits: dict[str, int] = {}
    for filename in QUEUE_TEMPLATES:
        path = infra_root / filename
        try:
            template = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise PlacementFinding(
                "queue_templates_unusable",
                f"{_relative(path)} could not be read as YAML "
                f"({error.__class__.__name__}), so which queues exist and what each cancels a "
                "waiting job at are both unknown. Every template this reads is committed, so "
                "this usually means it was run from somewhere other than a checkout.",
                code=EXIT_UNUSABLE,
            ) from error

        resources = (template or {}).get("Resources") or {}
        for logical, resource in resources.items():
            if not isinstance(resource, dict) or resource.get("Type") != "AWS::Batch::JobQueue":
                continue
            properties = resource.get("Properties") or {}
            name = properties.get("JobQueueName")
            actions = properties.get("JobStateTimeLimitActions") or []
            seconds = next(
                (
                    action.get("MaxTimeSeconds")
                    for action in actions
                    if isinstance(action, dict)
                    and action.get("Reason") == CAPACITY_TIME_LIMIT_REASON
                ),
                None,
            )
            if not isinstance(name, str) or not isinstance(seconds, int):
                raise PlacementFinding(
                    "queue_templates_unusable",
                    f"{logical} in {_relative(path)} is a job queue and does not declare both "
                    f"a name and a {CAPACITY_TIME_LIMIT_REASON} time limit. That limit is "
                    "where this check's line between a cold scale-up and a wait comes from, "
                    "and defaulting it would mean choosing the number here instead of reading "
                    "it off the queue it applies to.",
                    code=EXIT_UNUSABLE,
                )
            limits[name] = seconds

    if not limits:
        raise PlacementFinding(
            "queue_templates_unusable",
            "the templates declare no job queue at all, so there is nothing to read. "
            f"{', '.join(QUEUE_TEMPLATES)} are where the sixteen are declared.",
            code=EXIT_UNUSABLE,
        )
    return limits


def profile_queues(
    capacity: Sequence[PlacementRecord], queues: Iterable[str], *, targets_path: Path
) -> tuple[dict[str, str], list[str]]:
    """Which queue measures which profile, and any queue nothing accounts for.

    ``config/execution-targets.yaml`` is the authority wherever it has a row, because that is
    the file whose whole job is saying where a profile runs and it is already held against the
    templates by ``tests/test_phase3_infrastructure.py``.

    The two H100 profiles have no row. They were withdrawn on 2026-08-04 because a submission
    routed to either reaches a compute environment that can never place it, and that file's
    own note records that the queues stay deployed and names them. So a queue with no row falls
    back to the one convention the names follow, and the fallback is accepted only when it
    lands on a profile ``config/capacity.yaml`` actually records. Restoring those rows changes
    nothing here, which is the property worth having: the mapping does not depend on a shape
    being on the submission form.

    A queue that neither route accounts for is returned rather than skipped. The queue this
    cannot name is the one somebody deploys next, and skipping it would mean a new shape's
    verdict was never checked and nothing said so.
    """
    recorded = {record.profile for record in capacity}
    try:
        document = yaml.safe_load(targets_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise PlacementFinding(
            "execution_targets_unusable",
            f"{_relative(targets_path)} could not be read as YAML "
            f"({error.__class__.__name__}), so which queue belongs to which compute profile is "
            "unknown and no verdict can be attributed to anything.",
            code=EXIT_UNUSABLE,
        ) from error

    by_queue: dict[str, str] = {}
    for target in document.get("targets") or []:
        if isinstance(target, dict):
            queue, profile = target.get("job_queue"), target.get("compute_profile")
            if isinstance(queue, str) and isinstance(profile, str):
                by_queue[queue] = profile

    mapped: dict[str, str] = {}
    unaccounted: list[str] = []
    for queue in sorted(queues):
        profile = by_queue.get(queue)
        if profile is None and queue.startswith(QUEUE_NAME_PREFIX):
            candidate = queue[len(QUEUE_NAME_PREFIX) :]
            profile = candidate if candidate in recorded else None
        if profile is None:
            unaccounted.append(queue)
            continue
        mapped[profile] = queue
    return mapped, unaccounted


def _aws(*arguments: str, profile: str | None, region: str) -> Any:
    """One CLI call, or a finding saying the account was not read.

    The CLI rather than boto3, for the reason ``tools/verify_wandb_credential.py`` gives: this
    project does not depend on an AWS SDK, and the two Lambda zips are size-limited enough that
    adding one would be paid for by both functions.
    """
    call = [
        "aws",
        *arguments,
        "--region",
        region,
        *(["--profile", profile] if profile else []),
        "--output",
        "json",
    ]
    try:
        finished = subprocess.run(call, capture_output=True, text=True, timeout=180, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PlacementFinding(
            "queues_not_read",
            f"asking Batch for {arguments[1]} did not complete "
            f"({error.__class__.__name__}), so the queues have not been read and no verdict is "
            "recomputed on this run.",
            code=EXIT_UNUSABLE,
        ) from error

    if finished.returncode != 0:
        found = ERROR_CODE.search(finished.stderr)
        named = f"{found.group(1)} " if found else ""
        raise PlacementFinding(
            "queues_not_read",
            f"Batch refused {arguments[1]} with {named}(the CLI exited "
            f"{finished.returncode}), so what the account has placed has not been read. A "
            "denial here is usually the grant: this needs batch:ListJobs and "
            "batch:DescribeJobs, which infra/iam/audit-reader-role.yaml declares under "
            "ReadTheQueuesThePlacementVerdictNeeds and which is applied from a laptop like "
            "every IAM stack in infra/README.md. The full message is not printed because it "
            "names the calling ARN, which carries the account id.",
            code=EXIT_UNUSABLE,
        )

    try:
        return json.loads(finished.stdout)
    except ValueError as error:
        raise PlacementFinding(
            "queues_not_read",
            f"the answer Batch gave for {arguments[1]} did not parse as JSON, so nothing has "
            "been read. That is a statement about the answer rather than about the account.",
            code=EXIT_UNUSABLE,
        ) from error


def read_jobs(queue: str, *, profile: str | None, region: str) -> list[dict[str, Any]]:
    """Every job the account holds for one queue, described.

    Listed per status and paginated, then described in hundreds. ``list-jobs`` returns
    summaries that carry neither ``createdAt`` against ``startedAt`` nor the attempt records,
    and the wait is the difference between two of those, so the describe pass is not optional.
    """
    identifiers: list[str] = []
    for status in JOB_STATUSES:
        token: str | None = None
        while True:
            page = _aws(
                "batch",
                "list-jobs",
                "--job-queue",
                queue,
                "--job-status",
                status,
                *(["--next-token", token] if token else []),
                profile=profile,
                region=region,
            )
            identifiers += [
                summary["jobId"]
                for summary in page.get("jobSummaryList", [])
                if isinstance(summary, dict) and "jobId" in summary
            ]
            token = page.get("nextToken")
            if not token:
                break

    described: list[dict[str, Any]] = []
    for start in range(0, len(identifiers), DESCRIBE_BATCH):
        answer = _aws(
            "batch",
            "describe-jobs",
            "--jobs",
            *identifiers[start : start + DESCRIBE_BATCH],
            profile=profile,
            region=region,
        )
        described += [job for job in answer.get("jobs", []) if isinstance(job, dict)]
    return described


def _first_start(job: dict[str, Any]) -> int | None:
    """When this job first reached a machine, in epoch milliseconds, or ``None``.

    The earliest attempt rather than the job's own ``startedAt``. A job whose host was
    terminated under it retries, and the job-level field then holds the second attempt's
    start, so the difference from ``createdAt`` spans the whole of the first run. Six
    ``cpu-32vcpu`` jobs read as eight-hour waits that way and every one of them started in
    about two minutes.
    """
    attempts = job.get("attempts") or []
    starts: list[int] = [
        attempt["startedAt"]
        for attempt in attempts
        if isinstance(attempt, dict) and isinstance(attempt.get("startedAt"), int)
    ]
    if starts:
        return min(starts)
    started = job.get("startedAt")
    return started if isinstance(started, int) else None


def _reason(job: dict[str, Any]) -> str:
    """Everything the job says about why it ended, job level and attempt level together."""
    attempts = job.get("attempts") or []
    return " ".join(
        [str(job.get("statusReason") or "")]
        + [
            str(attempt.get("statusReason") or "")
            for attempt in attempts
            if isinstance(attempt, dict)
        ]
    )


def classify(profile: str, queue: str, jobs: Sequence[dict[str, Any]]) -> Evidence:
    """Sort one queue's jobs into what they do and do not establish.

    A job that started is a placement whatever it did afterwards. Exiting non-zero, being
    cancelled by its submitter, running out of its own timeout: none of that is a fact about
    whether the account could obtain the machine, and counting only successful runs would
    quietly make this a check on the workloads rather than on the pools.
    """
    evidence = Evidence(profile=profile, queue=queue, jobs=len(jobs))
    for job in jobs:
        started = _first_start(job)
        created = job.get("createdAt")
        if started is not None and isinstance(created, int):
            evidence.waits.append((started - created) / 1000.0)
            continue
        if started is not None:
            # It ran, and the record cannot say how long it waited. Still a placement.
            evidence.waits.append(0.0)
            continue
        if job.get("status") in STATUSES_STILL_WAITING:
            evidence.still_waiting += 1
            continue
        reason = _reason(job)
        if any(marker in reason for marker in LIMIT_MARKERS):
            evidence.limit_refusals.append(reason)
        elif any(marker in reason for marker in CAPACITY_MARKERS):
            evidence.capacity_refusals.append(reason)
        else:
            evidence.silent += 1
    return evidence


def compare(
    capacity: Sequence[PlacementRecord],
    evidence: QueueEvidence,
    queues: dict[str, str],
    unaccounted: Sequence[str],
) -> list[Finding]:
    """Every place the account contradicts what ``config/capacity.yaml`` commits.

    Four contradictions, and each is conclusive on its own evidence. What is deliberately
    absent is a fifth that would read an absence as a refusal: a shape whose queue has placed
    nothing and refused nothing for capacity is reported as unsettled, because that is the
    state ``gpu-1xl4`` is in and the state every one of the eight wrong answers was read out
    of.
    """
    findings: list[Finding] = []

    for unclaimed in unaccounted:
        findings.append(
            Finding(
                unclaimed,
                "is a job queue the templates declare and no compute profile is mapped to it, "
                "so whatever shape it places for has no verdict being checked. Add a row to "
                f"config/execution-targets.yaml, or an entry to config/{CAPACITY_FILENAME} if "
                "the profile is missing from there too.",
            )
        )

    for record in capacity:
        queue = queues.get(record.profile)
        if queue is None:
            # No queue at all is a real state and the file has one entry in it:
            # gpu-1xa10g-sagemaker is SageMaker training, so a probe is not the weaker of two
            # instruments, it is the only instrument there is. What must not stand is an entry
            # claiming a queue measured a shape that has no queue to be measured by.
            if record.measured_by == MEASURED_BY_A_QUEUE:
                findings.append(
                    Finding(
                        record.profile,
                        f"records {MEASURED_BY_A_QUEUE!r} and no Batch queue is mapped to it, "
                        "so no queue can have measured anything about it.",
                    )
                )
            continue

        found = evidence.by_profile[record.profile]
        limit = evidence.limits[queue]

        if found.jobs == 0:
            if record.measured_by == MEASURED_BY_A_QUEUE:
                findings.append(
                    Finding(
                        record.profile,
                        f"records that a queue measured it and {queue} has never been "
                        "submitted to, across every job status. A verdict cannot be "
                        f"attributed to an instrument that has not run; {MEASURED_BY_A_PROBE!r} "
                        "is what this entry can honestly claim until somebody submits to it.",
                    )
                )
            continue

        median = found.median_wait

        if record.places == PLACES_UNRELIABLY and found.placements and median is not None:
            findings.append(
                Finding(
                    record.profile,
                    f"is recorded {PLACES_UNRELIABLY!r} and {found.placements} job(s) have "
                    f"started on {queue}, at a median wait of {_minutes(median)}. The account "
                    "held the machine, so the submission path is telling submitters a shape "
                    "may not place when it does. This is the direction all eight previous "
                    "corrections ran in.",
                )
            )

        if record.places == PLACES_RELIABLY and median is not None and median > limit:
            findings.append(
                Finding(
                    record.profile,
                    f"is recorded {PLACES_RELIABLY!r} and the median job on {queue} waited "
                    f"{_minutes(median)} before it started, past the {limit}s this queue "
                    f"itself cancels a job waiting on capacity at. {PLACES_RELIABLY!r} prints "
                    f"nothing at all to a submitter, so a wait this long reaches them as "
                    f"silence. {PLACES_AFTER_A_WAIT!r} with a wait sentence is what the "
                    "measurement supports.",
                )
            )

        if (
            record.places in {PLACES_RELIABLY, PLACES_AFTER_A_WAIT}
            and not found.placements
            and found.capacity_refusals
        ):
            findings.append(
                Finding(
                    record.profile,
                    f"is recorded as placing and no job has ever started on {queue}, while "
                    f"{len(found.capacity_refusals)} job(s) there record capacity as the "
                    "reason they never ran. The nodes and the wait this entry quotes cannot "
                    "be reproduced from the queue.",
                )
            )

    return findings


def _minutes(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    return f"{seconds / 60:.0f}m"


def _wait_column(found: Evidence) -> str:
    median, longest = found.median_wait, found.longest_wait
    if median is None or longest is None:
        return "-"
    return f"{_minutes(median)} / {_minutes(longest)}"


def render(
    capacity: Sequence[PlacementRecord],
    evidence: QueueEvidence,
    queues: dict[str, str],
    findings: Sequence[Finding],
) -> str:
    """The report, as markdown, for the step summary and the log.

    Every profile appears whether or not anything is wrong with it, because the row that says
    a queue has settled nothing is the one a reader needs in order to know the green tick is
    narrower than it looks.
    """
    lines = ["## Placement verdicts against the queues", ""]
    lines.append(
        "| Profile | File | Instrument | Jobs | Placed | Wait median / worst | "
        "Capacity refusals | Limit refusals | No evidence | What the queue shows |"
    )
    lines.append("| --- | --- | --- | ---: | ---: | --- | ---: | ---: | ---: | --- |")

    for record in sorted(capacity, key=lambda item: item.profile):
        queue = queues.get(record.profile)
        if queue is None:
            lines.append(
                f"| `{record.profile}` | {record.places} | {record.measured_by} | - | - | - | "
                "- | - | - | no Batch queue, so no queue can measure it |"
            )
            continue
        found = evidence.by_profile[record.profile]
        limit = evidence.limits[queue]
        lines.append(
            f"| `{record.profile}` | {record.places} | {record.measured_by} | {found.jobs} | "
            f"{found.placements} | {_wait_column(found)} | "
            f"{len(found.capacity_refusals)} | {len(found.limit_refusals)} | "
            f"{found.silent + found.still_waiting} | {_shows(found, limit)} |"
        )

    lines += ["", _limits_note(evidence), ""]

    if findings:
        lines += ["### Disagreements", ""]
        lines += [f"- **`{finding.profile}`** {finding.detail}" for finding in findings]
    else:
        lines.append(
            "Every recorded verdict is one the queues support. No shape recorded as "
            "unplaceable has placed, no shape recorded as placing promptly is keeping people "
            "waiting past its queue's own capacity limit, and no entry claims a queue "
            "measured a shape nothing has been submitted to."
        )
    return "\n".join(lines) + "\n"


def _shows(found: Evidence, limit: int) -> str:
    """One sentence per profile, saying what the queue establishes rather than a verdict.

    It does not print a recomputed ``places`` value, and that is deliberate. A column of
    verdicts beside the file's own reads as a proposed replacement, which is a rewrite in
    everything but the commit; what a reader needs is the evidence and the disagreements.
    """
    if found.jobs == 0:
        return "nothing submitted, so the queue has measured nothing"
    if found.placements:
        longest = found.longest_wait or 0.0
        waited = (
            f", worst {_minutes(longest)} against this queue's {limit}s limit"
            if longest > limit
            else ", every wait inside this queue's own capacity limit"
        )
        return f"places, {found.placements} started{waited}"
    if found.capacity_refusals:
        return f"{len(found.capacity_refusals)} refusal(s) naming capacity, nothing ever started"
    if found.limit_refusals:
        return (
            f"{len(found.limit_refusals)} refusal(s) naming the account's own ceiling, which "
            "is a support ticket rather than EC2 being short"
        )
    return "nothing started and nothing names capacity, so the queue has settled nothing"


def _limits_note(evidence: QueueEvidence) -> str:
    seconds = sorted(set(evidence.limits.values()))
    spelled = ", ".join(f"{value}s" for value in seconds)
    return (
        f"The line between a cold scale-up and a wait is each queue's own "
        f"`{CAPACITY_TIME_LIMIT_REASON}` time limit, read from "
        f"{', '.join('`infra/' + name + '`' for name in QUEUE_TEMPLATES)}: {spelled}. "
        "A `VcpuLimitExceeded` refusal never reaches a Batch job field, so the quota half of "
        "a shape's history is not visible here; what protects this report from calling a "
        "quota a shortage is that a wait is only ever recorded as a placement."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    # No default profile. The audit runs on an assumed role and passes none, and a default
    # of `sbsandbox` would send it looking for an SSO session that is not there.
    parser.add_argument("--profile", default=None)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--capacity", type=Path, default=CONFIG_ROOT / CAPACITY_FILENAME)
    parser.add_argument(
        "--execution-targets", type=Path, default=CONFIG_ROOT / "execution-targets.yaml"
    )
    parser.add_argument("--infra-root", type=Path, default=INFRA_ROOT)
    parser.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)

    try:
        capacity = read_capacity(options.capacity)
    except (OSError, UnreadableCapacityError) as error:
        print("capacity_file_unusable", file=sys.stderr, flush=True)
        print(
            f"{_relative(options.capacity)} could not be read as placement records "
            f"({error.__class__.__name__}: {error}), so there is nothing to hold the account "
            "against and no comparison was made.",
            file=sys.stderr,
            flush=True,
        )
        return EXIT_UNUSABLE

    try:
        limits = declared_queues(options.infra_root)
        queues, unaccounted = profile_queues(
            capacity, limits, targets_path=options.execution_targets
        )
        by_profile = {
            profile: classify(
                profile, queue, read_jobs(queue, profile=options.profile, region=options.region)
            )
            for profile, queue in sorted(queues.items())
        }
    except PlacementFinding as finding:
        print(finding.reason, file=sys.stderr, flush=True)
        print(finding.detail, file=sys.stderr, flush=True)
        return finding.code

    evidence = QueueEvidence(by_profile=by_profile, limits=limits)
    findings = compare(capacity, evidence, queues, unaccounted)
    report = render(capacity, evidence, queues, findings)

    if options.output:
        options.output.write_text(report, encoding="utf-8")
    print(report, end="", flush=True)

    if findings:
        print("placement_verdict_disagrees_with_the_account", file=sys.stderr, flush=True)
        print(
            f"{len(findings)} compute profile(s) are recorded in config/{CAPACITY_FILENAME} as "
            "something the queues do not support. The table above says what each queue shows. "
            "Nothing here has been rewritten: the file is reviewed prose and the edit is a "
            "person's, in a pull request against this repository.",
            file=sys.stderr,
            flush=True,
        )
        return EXIT_DISAGREES
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
