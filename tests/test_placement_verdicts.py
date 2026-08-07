"""That the audit placement check reproduces the shipped verdicts and can still fail.

``config/capacity.yaml`` has taken eight corrections and every one ran the same way: an
instant probe was refused, the refusal was written down as a verdict, and a queue that kept
asking later obtained the machine. The check this module covers exists to catch the ninth, and
the way it could fail to is by making the identical mistake itself -- reading a queue with no
placements as a queue that was refused.

**So the property asserted hardest here is the one that keeps the check honest rather than the
one that makes it useful.** A shape nothing has been submitted to, and a shape whose only job
was cancelled for a misconfiguration before it could test anything, must both come out as
unsettled. Neither may become a disagreement, because the account has not said anything about
either, and a check that reads silence as a refusal would put the file's own defect into the
thing meant to guard it.

The agreement case is run against the committed ``config/capacity.yaml`` and the committed
templates rather than against a fixture, with only the account's answers stubbed. A synthetic
capacity file shaped to pass proves that the comparison runs; the shipped one proves that the
rule this check encodes is the rule the file was written under, which is the claim that
matters and the one that goes stale.

Nothing here reaches AWS. ``subprocess.run`` is replaced in every case.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOL = PROJECT_ROOT / "tools" / "verify_placement_verdicts.py"
CAPACITY_PATH = PROJECT_ROOT / "config" / "capacity.yaml"
TARGETS_PATH = PROJECT_ROOT / "config" / "execution-targets.yaml"
INFRA_ROOT = PROJECT_ROOT / "infra"

#: The sixteen queues the templates declare. Spelled here rather than derived, because this
#: module is the second opinion on the deriving: a rule that read the templates the same way
#: the tool does would agree with itself about a queue that had quietly gone missing.
QUEUES = (
    "sbsandbox-intern-edullm-cpu",
    "sbsandbox-intern-edullm-gpu",
    "sbsandbox-intern-edullm-gpu-1xt4",
    "sbsandbox-intern-edullm-gpu-4xt4",
    "sbsandbox-intern-edullm-gpu-8xt4",
    "sbsandbox-intern-edullm-gpu-4xa10g",
    "sbsandbox-intern-edullm-gpu-8xa10g",
    "sbsandbox-intern-edullm-gpu-1xl4",
    "sbsandbox-intern-edullm-gpu-4xl4",
    "sbsandbox-intern-edullm-gpu-8xl4",
    "sbsandbox-intern-edullm-gpu-1xl40s",
    "sbsandbox-intern-edullm-gpu-4xl40s",
    "sbsandbox-intern-edullm-gpu-8xl40s",
    "sbsandbox-intern-edullm-gpu-1xh100",
    "sbsandbox-intern-edullm-gpu-8xa100",
    "sbsandbox-intern-edullm-gpu-8xh100",
)

#: The one profile with no Batch queue and no prospect of one. It is SageMaker training, so a
#: probe is not the weaker of two available instruments, it is the only instrument there is.
NO_QUEUE_PROFILE = "gpu-1xa10g-sagemaker"

#: The documentation account id. Any other run of twelve digits reads as a real account to
#: ``tests/test_evidence.py``, which scans the tracked tree and does not exempt tests.
AWS_EXAMPLE_ACCOUNT_ID = "123456789012"

#: What the queues themselves cancel a job waiting on capacity at. The tool reads this off the
#: templates; this is the value they are expected to hold, so a template edit that moved the
#: threshold has to move this line too.
CAPACITY_LIMIT_SECONDS = 1800

MINUTE = 60_000
CREATED = 1_785_000_000_000


def load() -> Any:
    cached = sys.modules.get("verify_placement_verdicts")
    if cached is not None:
        return cached
    specification = importlib.util.spec_from_file_location("verify_placement_verdicts", TOOL)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    # Registered before it is executed, because ``@dataclass`` resolves a string annotation by
    # looking the defining module up in sys.modules, and a module built from a file path is
    # not there unless it is put there.
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def module() -> Any:
    return load()


# ----------------------------------------------------------------------------------------
# Jobs, in the shape Batch returns them
# ----------------------------------------------------------------------------------------


def placed(*, waited_minutes: float, name: str = "run_one", status: str = "SUCCEEDED") -> dict:
    started = CREATED + int(waited_minutes * MINUTE)
    return {
        "jobId": name,
        "jobName": name,
        "status": status,
        "createdAt": CREATED,
        "startedAt": started,
        "attempts": [{"startedAt": started, "statusReason": "Essential container in task exited"}],
        "statusReason": "Essential container in task exited",
    }


def retried(*, first_wait_minutes: float, ran_for_minutes: float) -> dict:
    """A job whose host was terminated under it and which started again on a second attempt.

    The job-level ``startedAt`` is the second attempt's, so measuring from that spans the whole
    of the first run. Six ``cpu-32vcpu`` jobs read as eight-hour waits that way.
    """
    first = CREATED + int(first_wait_minutes * MINUTE)
    second = first + int(ran_for_minutes * MINUTE)
    return {
        "jobId": "run_retried",
        "jobName": "run_retried",
        "status": "SUCCEEDED",
        "createdAt": CREATED,
        "startedAt": second,
        "attempts": [
            {"startedAt": first, "statusReason": "Host EC2 (instance i-0abc) terminated."},
            {"startedAt": second, "statusReason": "Essential container in task exited"},
        ],
    }


def refused(reason: str, *, name: str = "run_refused") -> dict:
    return {
        "jobId": name,
        "jobName": name,
        "status": "FAILED",
        "createdAt": CREATED,
        "attempts": [],
        "statusReason": reason,
    }


def still_waiting() -> dict:
    return {
        "jobId": "run_waiting",
        "jobName": "run_waiting",
        "status": "RUNNABLE",
        "createdAt": CREATED,
        "attempts": [],
        "statusReason": "",
    }


# ----------------------------------------------------------------------------------------
# What the queue's record establishes
# ----------------------------------------------------------------------------------------


def test_a_job_that_started_is_a_placement_whatever_it_did_next(module: Any) -> None:
    """Mutation: count only the runs that succeeded.

    Exiting non-zero, being cancelled by its submitter, running past its own timeout: none of
    that is a fact about whether the account could obtain the machine. Counting successes
    would quietly turn this into a check on the workloads, and the shape it would break first
    is ``gpu-8xa10g``, where two of seven placements ended on ``Job attempt duration exceeded
    timeout``.
    """
    found = module.classify(
        "gpu-8xa10g",
        "queue",
        [
            placed(waited_minutes=9, status="SUCCEEDED"),
            placed(waited_minutes=10, name="run_two", status="FAILED"),
            placed(waited_minutes=11, name="run_three", status="RUNNING"),
        ],
    )

    assert found.placements == 3
    assert found.capacity_refusals == []
    assert found.silent == 0


def test_the_wait_is_the_first_attempt_and_not_the_retry(module: Any) -> None:
    """Mutation: read ``startedAt`` off the job instead of off the earliest attempt.

    A job that lost its host and started again carries the second attempt's start at job
    level. Six ``cpu-32vcpu`` jobs measured 506 minutes that way and every one of them reached
    a machine in about two minutes, which would put the CPU queue past its own capacity limit
    and make this check red about the least contended pool in the account.
    """
    found = module.classify("cpu-32vcpu", "queue", [retried(first_wait_minutes=2, ran_for_minutes=504)])

    assert found.placements == 1
    assert found.longest_wait == pytest.approx(2 * 60.0)


@pytest.mark.parametrize(
    "reason",
    [
        (
            "cancelled by platform: gpu-1xh100 cannot place a job. EC2 has returned "
            "InsufficientInstanceCapacity for every p5.4xlarge launch"
        ),
        "Cancelled by ericrcwu001: g6e.12xlarge capacity unavailable: 100 min in RUNNABLE",
        (
            "Canceled by JobStateTimeLimit action due to reason: "
            "CAPACITY:INSUFFICIENT_INSTANCE_CAPACITY"
        ),
    ],
)
def test_a_refusal_naming_capacity_is_counted_as_one(module: Any, reason: str) -> None:
    found = module.classify("gpu-1xh100", "queue", [refused(reason)])

    assert len(found.capacity_refusals) == 1
    assert found.limit_refusals == []
    assert found.silent == 0


@pytest.mark.parametrize(
    "reason",
    [
        "capacity diagnosis complete",
        "probe cleanup: superseded by per-queue measurement",
        "Superseded by active A10G trace job",
        (
            "Canceled by JobStateTimeLimit action due to reason: "
            "MISCONFIGURATION:JOB_RESOURCE_REQUIREMENT"
        ),
        "Container Overrides length must be at most 8192",
        "Array Child Job failed",
        "",
    ],
)
def test_a_job_that_did_not_test_capacity_establishes_nothing(module: Any, reason: str) -> None:
    """Mutation: match ``capacity`` anywhere in the reason.

    ``capacity diagnosis complete`` is a real reason on this account and it belongs to a probe
    that finished its work, not to a pool that refused. A generous match would read it as
    scarcity, and the eight corrections this check exists to prevent were all made by reading
    something that was not a refusal as one.
    """
    found = module.classify("gpu-8xa100", "queue", [refused(reason)])

    assert found.capacity_refusals == []
    assert found.limit_refusals == []
    assert found.silent == 1


def test_a_ceiling_is_not_a_shortage_even_when_the_reason_names_both(module: Any) -> None:
    """THE ONE THAT ``gpu-8xa10g`` IS ABOUT. Mutation: test capacity before the limit markers.

    That shape logged 871 ``VcpuLimitExceeded`` refusals against the then 768-vCPU G bucket
    beside 105 capacity ones. From ``RUNNABLE`` the two are indistinguishable and their
    remedies are opposite: one is weather and the other is a support ticket. The quota has
    since gone to 3,696, so a check that counted the ceiling as scarcity would have reported
    a shortage and then changed its mind for a reason that was never about EC2.
    """
    both = (
        "VcpuLimitExceeded: the G bucket is full, and InsufficientInstanceCapacity "
        "in two zones besides"
    )
    found = module.classify("gpu-8xa10g", "queue", [refused(both)])

    assert len(found.limit_refusals) == 1
    assert found.capacity_refusals == []


def test_a_job_still_in_the_queue_is_neither_a_placement_nor_a_refusal(module: Any) -> None:
    """Mutation: count a RUNNABLE job's elapsed wait toward the medians.

    A job that is still waiting has not placed and has not been refused, and folding its
    running total into the wait would let a verdict move every hour of a long queue rather
    than when something is settled.
    """
    found = module.classify("gpu-8xa100", "queue", [still_waiting()])

    assert found.placements == 0
    assert found.capacity_refusals == []
    assert found.still_waiting == 1


# ----------------------------------------------------------------------------------------
# What the templates and the target rows say exists
# ----------------------------------------------------------------------------------------


def test_the_templates_declare_the_sixteen_queues_and_what_each_cancels_a_wait_at(
    module: Any,
) -> None:
    limits = module.declared_queues(INFRA_ROOT)

    assert sorted(limits) == sorted(QUEUES)
    assert set(limits.values()) == {CAPACITY_LIMIT_SECONDS}


def test_a_queue_declaring_no_capacity_time_limit_is_refused_rather_than_defaulted(
    module: Any, tmp_path: Path
) -> None:
    """Mutation: default the threshold when a queue does not declare one.

    The default would have to be a number chosen in the tool, which is the whole thing reading
    it off the queues avoids. A queue whose limit was deleted should stop this check rather
    than have it quietly measure that queue against a figure nobody applied to it.
    """
    (tmp_path / "batch-compute.yaml").write_text(
        yaml.safe_dump(
            {
                "Resources": {
                    "Queue": {
                        "Type": "AWS::Batch::JobQueue",
                        "Properties": {"JobQueueName": "sbsandbox-intern-edullm-cpu"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    for name in ("batch-compute-gpu.yaml", "batch-compute-gpu-shapes.yaml"):
        (tmp_path / name).write_text("Resources: {}\n", encoding="utf-8")

    with pytest.raises(module.PlacementFinding) as raised:
        module.declared_queues(tmp_path)

    assert raised.value.reason == "queue_templates_unusable"
    assert raised.value.code == module.EXIT_UNUSABLE


def test_every_declared_queue_is_mapped_to_a_profile_including_the_withdrawn_two(
    module: Any,
) -> None:
    """Both H100 rows were withdrawn from ``config/execution-targets.yaml`` on 2026-08-04.

    Their queues are still deployed, still enabled and still measuring, so a mapping that came
    only from that file would stop checking the two shapes whose verdicts rest on the largest
    refusal counts in the account. Restoring the rows must not change this either, which is
    what the second assertion is for.
    """
    capacity = load_capacity(module)
    mapped, unaccounted = module.profile_queues(capacity, QUEUES, targets_path=TARGETS_PATH)

    assert unaccounted == []
    assert set(mapped.values()) == set(QUEUES)
    assert mapped["gpu-1xh100"] == "sbsandbox-intern-edullm-gpu-1xh100"
    assert mapped["gpu-8xh100"] == "sbsandbox-intern-edullm-gpu-8xh100"
    assert NO_QUEUE_PROFILE not in mapped


def test_a_queue_no_profile_claims_is_reported_rather_than_skipped(module: Any) -> None:
    """Mutation: ignore a queue nothing maps to.

    The queue this cannot name is the one somebody deploys next, and skipping it means a new
    shape's verdict is never checked and nothing says so. It is the same property
    ``tools/verify_deployed_stacks.py`` holds about a stack the table does not claim.
    """
    capacity = load_capacity(module)
    invented = "sbsandbox-intern-edullm-gpu-16xb200"

    _, unaccounted = module.profile_queues(
        capacity, [*QUEUES, invented], targets_path=TARGETS_PATH
    )

    assert unaccounted == [invented]
    findings = module.compare(
        capacity,
        module.QueueEvidence(by_profile={}, limits={}),
        {},
        unaccounted,
    )
    assert any(invented in str(finding) for finding in findings)


# ----------------------------------------------------------------------------------------
# The comparison, against the file that shipped
# ----------------------------------------------------------------------------------------


def load_capacity(module: Any) -> Sequence[Any]:
    from edullm_platform.placement import read_capacity

    return read_capacity(CAPACITY_PATH)


def some(count: int, *, waited_minutes: float, tag: str) -> list[dict]:
    return [
        placed(waited_minutes=waited_minutes, name=f"run_{tag}_{index}") for index in range(count)
    ]


#: Which queue measures which profile, spelled rather than derived for the reason ``QUEUES``
#: is: this module is the second opinion on the tool's mapping.
QUEUE_FOR = {
    "cpu-32vcpu": "sbsandbox-intern-edullm-cpu",
    "gpu-1xa10g": "sbsandbox-intern-edullm-gpu",
    **{
        profile: f"sbsandbox-intern-edullm-{profile}"
        for profile in (
            "gpu-1xt4",
            "gpu-4xt4",
            "gpu-8xt4",
            "gpu-4xa10g",
            "gpu-8xa10g",
            "gpu-1xl4",
            "gpu-4xl4",
            "gpu-8xl4",
            "gpu-1xl40s",
            "gpu-4xl40s",
            "gpu-8xl40s",
            "gpu-1xh100",
            "gpu-8xa100",
            "gpu-8xh100",
        )
    },
}


def evidence_for(module: Any, **overrides: Any) -> Any:
    """Queue evidence in the shape the account showed on 2026-08-05, with named rows replaced.

    The counts matter as well as the waits, which is why ``cpu-32vcpu`` gets several short
    placements rather than one. Two observations make a median the mean of them, so a fixture
    that gave that queue one short wait and one long one would put its median past the limit
    and prove the opposite of what the account shows: 133 jobs at a median of two minutes, one
    of which waited 68.
    """
    live: dict[str, list[dict]] = {
        "cpu-32vcpu": [
            *some(6, waited_minutes=2, tag="cpu"),
            placed(waited_minutes=68, name="run_cpu_slow"),
        ],
        "gpu-1xa10g": some(4, waited_minutes=4, tag="a10g"),
        "gpu-1xt4": some(3, waited_minutes=4, tag="t4"),
        "gpu-4xt4": [],
        "gpu-8xt4": [],
        "gpu-4xa10g": [
            *some(3, waited_minutes=7, tag="4xa10g"),
            placed(waited_minutes=92, name="run_4xa10g_slow"),
        ],
        "gpu-8xa10g": [
            *some(3, waited_minutes=9, tag="8xa10g"),
            placed(waited_minutes=201, name="run_8xa10g_slow"),
        ],
        "gpu-1xl4": [refused("MISCONFIGURATION:JOB_RESOURCE_REQUIREMENT on an unproven queue")],
        "gpu-4xl4": [],
        "gpu-8xl4": [],
        "gpu-1xl40s": [placed(waited_minutes=4, name="run_1xl40s")],
        "gpu-4xl40s": [
            *some(3, waited_minutes=19, tag="4xl40s"),
            placed(waited_minutes=384, name="run_4xl40s_slow"),
        ],
        "gpu-8xl40s": [],
        "gpu-1xh100": [refused("EC2 has returned InsufficientInstanceCapacity for p5.4xlarge")],
        "gpu-8xa100": [
            *some(3, waited_minutes=61, tag="8xa100"),
            placed(waited_minutes=404, name="run_8xa100_slow"),
        ],
        "gpu-8xh100": [refused("p5.48xlarge InsufficientInstanceCapacity in all reachable AZs")],
    }
    live.update(overrides)
    queues = {profile: QUEUE_FOR[profile] for profile in live}
    return (
        module.QueueEvidence(
            by_profile={
                profile: module.classify(profile, queues[profile], jobs)
                for profile, jobs in live.items()
            },
            limits={queue: CAPACITY_LIMIT_SECONDS for queue in queues.values()},
        ),
        queues,
    )


def test_the_shipped_file_is_one_the_queues_support(module: Any) -> None:
    """THE CLAIM THAT GOES STALE, AND THE REASON THIS RUNS AGAINST THE COMMITTED FILE.

    A rule that reproduces every shipped verdict is a rule the file was plausibly written
    under. One that disagrees with a shipped entry is either a wrong rule or a wrong entry,
    and the useful thing is to find out which rather than to tune until they match. All
    sixteen reproduced against the live account on 2026-08-05, including the two the check
    declines to judge for lack of evidence.
    """
    capacity = load_capacity(module)
    evidence, queues = evidence_for(module)

    assert module.compare(capacity, evidence, queues, []) == []


def test_a_shape_recorded_unplaceable_that_placed_is_the_finding_this_exists_for(
    module: Any,
) -> None:
    """Mutation: require several placements before believing the shape places.

    Every one of the eight corrections was this: a shape recorded ``unreliably`` that a queue
    had already obtained. One started job settles it, because the account demonstrably held
    the machine and no later reading makes that untrue. Requiring a second would mean the
    submission path went on saying "may not place" over a pool that was running work.
    """
    capacity = load_capacity(module)
    evidence, queues = evidence_for(module, **{"gpu-8xl40s": [placed(waited_minutes=12)]})

    findings = module.compare(capacity, evidence, queues, [])

    assert [finding.profile for finding in findings] == ["gpu-8xl40s"]
    assert "started" in findings[0].detail


def test_a_shape_recorded_prompt_that_keeps_people_waiting_is_a_finding(module: Any) -> None:
    """Mutation: leave ``reliably`` alone because over-warning is cheap.

    It is cheap in the other direction. ``reliably`` prints nothing at all to a submitter, so
    a shape that has quietly started taking an hour reaches them as silence, which is the
    state this whole file was written to end.
    """
    capacity = load_capacity(module)
    evidence, queues = evidence_for(
        module,
        **{"gpu-1xt4": [placed(waited_minutes=45), placed(waited_minutes=95, name="run_slow")]},
    )

    findings = module.compare(capacity, evidence, queues, [])

    assert [finding.profile for finding in findings] == ["gpu-1xt4"]
    assert "1800s" in findings[0].detail


def test_one_long_wait_among_many_short_ones_is_not_a_finding(module: Any) -> None:
    """Mutation: compare the worst case against the limit instead of the median.

    ``cpu-32vcpu`` has placed 133 jobs at a median of two minutes and exactly one of them
    waited 68. A worst-case rule calls the least contended queue in the account contended, and
    a check that cries wolf is the same as not having one.
    """
    capacity = load_capacity(module)
    evidence, queues = evidence_for(module)

    assert evidence.by_profile["cpu-32vcpu"].longest_wait > CAPACITY_LIMIT_SECONDS
    assert module.compare(capacity, evidence, queues, []) == []


def test_an_entry_claiming_a_queue_measured_a_shape_nothing_was_submitted_to_is_a_finding(
    module: Any,
) -> None:
    """THE RULE THAT KEEPS THE CHECK FROM MAKING THE FILE'S OWN MISTAKE.

    Mutation: let ``measured_by: queue`` stand on an empty queue. Three shapes are in that
    state today and the file says ``probe`` for all three. A verdict attributed to an
    instrument that never ran is exactly what the eight corrections were, one field over.
    """
    capacity = load_capacity(module)
    # gpu-1xt4 is recorded `measured_by: queue`; empty its queue and the claim has nothing
    # under it.
    evidence, queues = evidence_for(module, **{"gpu-1xt4": []})

    findings = module.compare(capacity, evidence, queues, [])

    assert [finding.profile for finding in findings] == ["gpu-1xt4"]
    assert "never been submitted to" in findings[0].detail


def test_a_queue_that_has_settled_nothing_is_not_read_as_a_refusal(module: Any) -> None:
    """THE MISTAKE THIS CHECK COULD MOST EASILY MAKE ITSELF.

    Mutation: treat an absence of placements as ``unreliably``. ``gpu-1xl4`` is recorded as
    placing and its single job was cancelled for ``MISCONFIGURATION:JOB_RESOURCE_REQUIREMENT``
    before it could test anything; the g6.xlarge that did arrive for it is in the autoscaling
    history, which this role cannot read. Reading that silence as a refusal would be the
    absence of evidence becoming evidence, which is the one thing the whole design is against.
    """
    capacity = load_capacity(module)
    evidence, queues = evidence_for(module)

    assert evidence.by_profile["gpu-1xl4"].placements == 0
    assert evidence.by_profile["gpu-1xl4"].capacity_refusals == []
    assert module.compare(capacity, evidence, queues, []) == []


def test_a_shape_recorded_as_placing_that_the_queue_has_only_refused_is_a_finding(
    module: Any,
) -> None:
    """Mutation: never contradict a verdict that says the shape places.

    An entry carrying ``after_a_wait`` quotes nodes and a median that came from submitted runs
    starting. If nothing on that queue has ever started and jobs there name capacity as the
    reason, the sentence a submitter is planning a day around cannot be reproduced from the
    instrument that is supposed to have produced it.
    """
    capacity = load_capacity(module)
    evidence, queues = evidence_for(
        module,
        **{"gpu-4xl40s": [refused("g6e.12xlarge capacity unavailable: 100 min in RUNNABLE")]},
    )

    findings = module.compare(capacity, evidence, queues, [])

    assert [finding.profile for finding in findings] == ["gpu-4xl40s"]
    assert "cannot be reproduced" in findings[0].detail


def test_the_shape_with_no_queue_may_not_claim_a_queue_measured_it(
    module: Any, tmp_path: Path
) -> None:
    """``gpu-1xa10g-sagemaker`` has no Batch queue and never will while it is SageMaker.

    Its ``measured_by: probe`` is not the weaker of two instruments there, it is the only
    instrument there is. What must not stand is the opposite claim, so this is asserted by
    editing the entry rather than by trusting that nobody will.
    """
    from edullm_platform.placement import read_capacity

    document = yaml.safe_load(CAPACITY_PATH.read_text(encoding="utf-8"))
    for entry in document["profiles"]:
        if entry["profile"] == NO_QUEUE_PROFILE:
            entry["measured_by"] = "queue"
    edited = tmp_path / "capacity.yaml"
    edited.write_text(yaml.safe_dump(document), encoding="utf-8")

    evidence, queues = evidence_for(module)
    findings = module.compare(read_capacity(edited), evidence, queues, [])

    assert [finding.profile for finding in findings] == [NO_QUEUE_PROFILE]
    assert "no Batch queue is mapped to it" in findings[0].detail


# ----------------------------------------------------------------------------------------
# End to end, with the account's answers stubbed
# ----------------------------------------------------------------------------------------


def stub_aws(monkeypatch: pytest.MonkeyPatch, module: Any, jobs: dict[str, list[dict]]) -> None:
    """Answer ``list-jobs`` and ``describe-jobs`` from a table keyed by queue."""

    def fake_run(call: Sequence[str], **_: Any) -> subprocess.CompletedProcess[str]:
        arguments = list(call)
        if "list-jobs" in arguments:
            queue = arguments[arguments.index("--job-queue") + 1]
            status = arguments[arguments.index("--job-status") + 1]
            listed = [
                {"jobId": job["jobId"]}
                for job in jobs.get(queue, [])
                if job["status"] == status
            ]
            payload = {"jobSummaryList": listed}
        else:
            wanted = set(arguments[arguments.index("--jobs") + 1 : arguments.index("--region")])
            payload = {
                "jobs": [job for queued in jobs.values() for job in queued if job["jobId"] in wanted]
            }
        return subprocess.CompletedProcess(arguments, 0, json.dumps(payload), "")

    monkeypatch.setattr(module.subprocess, "run", fake_run)


def agreeing_account() -> dict[str, list[dict]]:
    """One placement per queue that the shipped file says places, and nothing anywhere else.

    Deliberately thinner than the account: the point is that the committed verdicts survive
    the smallest evidence consistent with them, so a case that turns red is red about the rule
    rather than about a number that moved overnight.
    """
    table: dict[str, list[dict]] = {queue: [] for queue in QUEUES}
    for queue, waited in (
        ("sbsandbox-intern-edullm-cpu", 2),
        ("sbsandbox-intern-edullm-gpu", 4),
        ("sbsandbox-intern-edullm-gpu-1xt4", 4),
        ("sbsandbox-intern-edullm-gpu-4xa10g", 92),
        ("sbsandbox-intern-edullm-gpu-8xa10g", 201),
        ("sbsandbox-intern-edullm-gpu-1xl40s", 4),
        ("sbsandbox-intern-edullm-gpu-4xl40s", 384),
        ("sbsandbox-intern-edullm-gpu-8xa100", 404),
    ):
        table[queue] = [placed(waited_minutes=waited, name=f"run_{queue}")]
    table["sbsandbox-intern-edullm-gpu-1xl4"] = [
        refused("MISCONFIGURATION:JOB_RESOURCE_REQUIREMENT", name="run_1xl4")
    ]
    for queue in ("sbsandbox-intern-edullm-gpu-1xh100", "sbsandbox-intern-edullm-gpu-8xh100"):
        table[queue] = [refused("InsufficientInstanceCapacity", name=f"run_{queue}")]
    return table


def test_an_agreeing_account_exits_zero_and_says_what_it_checked(
    module: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stub_aws(monkeypatch, module, agreeing_account())

    code = module.main([])
    printed = capsys.readouterr()

    assert code == module.EXIT_OK, printed.err
    assert "Every recorded verdict is one the queues support" in printed.out
    # Every profile appears whether or not anything is wrong with it, because the row saying a
    # queue has settled nothing is what tells a reader the green tick is narrower than it looks.
    #
    # Twenty-one since 2026-08-07, when the four block-backed shapes were priced. All four report
    # that no queue has settled anything, which is exactly the row this count exists to keep in
    # the report rather than filtering out as uninteresting.
    assert printed.out.count("| `gpu-") + printed.out.count("| `cpu-") == 21
    assert "nothing submitted, so the queue has measured nothing" in printed.out


def test_a_disagreeing_account_exits_one_and_names_the_profile(
    module: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    account = agreeing_account()
    account["sbsandbox-intern-edullm-gpu-8xl40s"] = [
        placed(waited_minutes=6, name="run_8xl40s_placed")
    ]
    stub_aws(monkeypatch, module, account)

    code = module.main([])
    printed = capsys.readouterr()

    assert code == module.EXIT_DISAGREES
    assert "placement_verdict_disagrees_with_the_account" in printed.err
    assert "gpu-8xl40s" in printed.out
    assert "### Disagreements" in printed.out
    # It reports and never rewrites, and the failure says so where somebody reads it.
    assert "Nothing here has been rewritten" in printed.err


def test_a_refused_read_is_not_reported_as_a_pass_or_as_a_disagreement(
    module: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: merge the two non-zero exits.

    Exit 1 sends a reader to a compute profile and exit 2 sends them to a grant. Reporting the
    second as the first sends somebody re-measuring a pool on the morning a role lapsed, and
    reporting it as a pass silently stops the check covering anything.
    """

    def denied(call: Sequence[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            list(call),
            255,
            "",
            "An error occurred (AccessDeniedException) when calling the ListJobs operation",
        )

    monkeypatch.setattr(module.subprocess, "run", denied)

    code = module.main([])
    printed = capsys.readouterr()

    assert code == module.EXIT_UNUSABLE
    assert "queues_not_read" in printed.err
    assert "placement_verdict_disagrees_with_the_account" not in printed.err
    assert "batch:ListJobs" in printed.err


def test_a_denial_does_not_repeat_the_account_id(
    module: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The scheduled log is public and every ARN in a denial carries the account number.

    The number here is the documentation one, because ``tests/test_evidence.py`` scans the
    tracked tree for anything twelve digits long and does not care that this file is a test.
    """

    def denied(call: Sequence[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            list(call),
            255,
            "",
            f"An error occurred (AccessDeniedException): User: arn:aws:sts::"
            f"{AWS_EXAMPLE_ACCOUNT_ID}:assumed-role/some-role/session is not authorized to "
            "perform: batch:ListJobs",
        )

    monkeypatch.setattr(module.subprocess, "run", denied)

    module.main([])
    printed = capsys.readouterr()

    assert AWS_EXAMPLE_ACCOUNT_ID not in printed.err + printed.out
    assert "AccessDeniedException" in printed.err


def test_the_report_is_written_where_the_step_summary_can_read_it(
    module: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stub_aws(monkeypatch, module, agreeing_account())
    output = tmp_path / "placement.md"

    assert module.main(["--output", str(output)]) == module.EXIT_OK
    assert "## Placement verdicts against the queues" in output.read_text(encoding="utf-8")


def test_an_unreadable_capacity_file_is_not_a_disagreement(
    module: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "capacity.yaml"
    broken.write_text("profiles: not-a-list\n", encoding="utf-8")

    code = module.main(["--capacity", str(broken)])
    printed = capsys.readouterr()

    assert code == module.EXIT_UNUSABLE
    assert "capacity_file_unusable" in printed.err


def test_nothing_the_tool_can_do_writes_to_the_account(module: Any) -> None:
    """Mutation: reach for a mutating Batch call to clear a stuck job while measuring.

    A check able to change what it is checking can produce its own all-clear, and the tempting
    one here is the worst available: cancelling a job that is waiting would let this decide the
    shape it is measuring does not place.
    """
    source = TOOL.read_text(encoding="utf-8")
    body = source.split('"""', 2)[2]

    for verb in ("submit-job", "cancel-job", "terminate-job", "register-job-definition"):
        assert verb not in body, f"{verb} is a write and this tool holds no write anywhere"
