"""Ask whether the deployed lifecycle rule can still deliver an event the recorder refuses.

**1327 dead letters, five to eight days old, and not one of them a run.** That was
``sbsandbox-intern-edullm-batch-lifecycle-dlq`` on 2026-08-06, and every message in it was a
``Batch Job State Change`` for a job this platform did not submit -- dataset builds, validator
jobs, memory probes, shims -- forwarded to a recorder that correctly refused each one with
``the Batch job name is not a run id``. The rule was scoped to the job queues rather than to
this platform's own submissions, and those queues are shared.

Nothing was lost. There was no lineage in any of those messages to lose, and the storage cost
of the whole pile was pennies. **The damage was the alarm.** ``batch-lifecycle-dead-letters``
watches for a state change that never became a record, and it sat in ALARM continuously on a
cause nobody was going to fix that week, so a genuine dead letter would have changed nothing
about what anybody could see. An alarm that is always red is an alarm that is off.

So this asks the condition rather than the symptom. Not "is the queue empty", which is the
alarm's question and which a purge answers for a day. **Can the deployed rule deliver an event
whose job name the recorder will refuse?** That is the thing that was true for at least eight
days, that a purge does not change, and that comes back the moment somebody widens the pattern.

## Where the answer comes from

Three reads, and the point of each is that it is not this repository talking to itself.

**The pattern is read from EventBridge**, not from ``infra/batch-events.yaml``. A template
says what should be deployed. The question here is what *is*, and a stack that failed half
way, a rule edited in the console and a template nobody applied all read identically from the
tree. This does not read the template at all, and that is deliberate rather than an omission:
whether the deployed stack matches what ``main`` declares is a different fact and
``tools/verify_deployed_stacks.py`` already answers it, over every stack including
``sbsandbox-intern-edullm-phase3-events``. Asking it here as well would mean two tools
disagreeing about which is authoritative on the same morning.

**The population is read from AWS Batch**, over every queue the deployed pattern names, in
every job state Batch will list. Those job names are the real feed: they are the jobs whose
state changes EventBridge actually carried.

**The verdict is read from EventBridge as well**, through ``TestEventPattern``. That is the
same matcher the bus runs, answering about the same pattern, so this cannot be wrong in the
way a reimplementation of EventBridge's matching semantics would eventually be wrong -- and
the wildcard the fix turns on is exactly the kind of rule that is easy to reimplement almost
correctly.

## The one place this assembles rather than observes

``TestEventPattern`` needs a whole event -- it refuses one missing ``id``, ``account``,
``region``, ``time`` or ``resources`` with a ``ValidationException`` -- and what is available
from ``ListJobs`` is the job. So the envelope handed to it is assembled: the source and detail
type the rule matches, the ``jobQueue``, ``jobName`` and ARN of a real job, the account and
region being read, and an ``id`` and ``time`` that are scaffolding the API demands and no
pattern here reads.

That is sound only for as long as the pattern reads nothing else, so it is not assumed.
:func:`fields_read_by` walks the deployed pattern and returns every field path in it, and this
refuses to report at all if the pattern reads a field the assembled envelope does not carry. A
pattern that grows a ``status`` clause makes this exit ``EXIT_UNUSABLE`` with the field named,
rather than quietly answering a question it can no longer answer -- which would be the worst
available outcome, because an absent field is a non-match and a rule that matches nothing
reads here as a rule with nothing wrong with it.

## What makes it able to fail

Run against the rule as it stood on 2026-08-06, this exits 1 and names the utility jobs. Run
against the narrowed rule it exits 0. Widen the pattern again and it exits 1 again. It is a
check with a live population behind it and a real defect in its history, which is the property
the other checks on this path did not have: the dead-letter alarm could not go red for a new
reason because it was already red, and every check on the notifier asked about the artifact
rather than invoking anything.

Nothing printed carries an account id. Job names and queue names are printed; queue ARNs are
not.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Final

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent

if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

from verify_deployed_lambdas import ERROR_CODE, EXIT_DISAGREES, EXIT_OK, EXIT_UNUSABLE

from edullm_platform.contracts.identity import RUN_ID_REGEX

__all__ = [
    "ASSEMBLED_FIELDS",
    "BATCH_DETAIL_TYPE",
    "BATCH_SOURCE",
    "CONCURRENT_READS",
    "JOB_STATES",
    "RULE_NAME",
    "assembled_event",
    "build_parser",
    "fields_read_by",
    "main",
    "unreadable_by_the_recorder",
]

#: The rule infra/batch-events.yaml creates. Named rather than resolved through
#: CloudFormation, because a rule left behind by a deleted stack, or created by hand, delivers
#: to the recorder's queue exactly as well as one a template owns -- and asking CloudFormation
#: which rule to look at would skip precisely those.
RULE_NAME: Final = "sbsandbox-intern-edullm-batch-lifecycle"

#: What the rule matches on, and what an assembled envelope therefore has to carry to be
#: judged the same way a real one would be.
BATCH_SOURCE: Final = "aws.batch"
BATCH_DETAIL_TYPE: Final = "Batch Job State Change"

#: How many ``aws`` processes to have in flight at once. One question per job name against
#: 112 queue-and-state listings is a few hundred calls, and a CLI process costs more than the
#: call inside it: serially this took fourteen minutes, which is a check nobody puts in a
#: daily job. Both reads are idempotent and neither is rate-limited at this size.
CONCURRENT_READS: Final = 8

#: Every state AWS Batch will list a job in. All seven rather than the terminal two, because
#: the rule matches every transition and a job that is RUNNABLE now produced SUBMITTED and
#: PENDING events already.
JOB_STATES: Final = (
    "SUBMITTED",
    "PENDING",
    "RUNNABLE",
    "STARTING",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
)

#: The field paths :func:`assembled_event` fills. Compared against what the deployed pattern
#: reads, so that a pattern clause on a field not in this set stops the run rather than being
#: silently judged against an absent value -- which EventBridge would report as "no match",
#: and which would read here as a rule that delivers nothing at all.
ASSEMBLED_FIELDS: Final = frozenset(
    {
        ("id",),
        ("account",),
        ("region",),
        ("time",),
        ("resources",),
        ("source",),
        ("detail-type",),
        ("detail", "jobQueue"),
        ("detail", "jobName"),
    }
)


def _aws(arguments: Sequence[str], *, profile: str | None, region: str) -> str:
    call = ["aws", *arguments, "--region", region, *(["--profile", profile] if profile else [])]
    # ONE RETRY, AND ONLY FOR A FAILURE THAT NAMED NOTHING. Several hundred `aws` processes
    # start at once here and one of them has been seen to exit 255 having printed no error at
    # all, which is a process that did not get as far as making a call. A refusal AWS
    # explained -- an AccessDenied, a ValidationException -- is not retried, because running
    # it again reaches the same place and hides how long it took to get there.
    for attempt in (1, 2):
        try:
            finished = subprocess.run(call, capture_output=True, text=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            print(
                f"event_scope_check_unusable\n`aws {arguments[0]} {arguments[1]}` did not "
                f"complete ({error.__class__.__name__}), so nothing was read and nothing is "
                "claimed.",
                file=sys.stderr,
                flush=True,
            )
            raise SystemExit(EXIT_UNUSABLE) from error
        if finished.returncode == 0 or ERROR_CODE.search(finished.stderr) or attempt == 2:
            break
    if finished.returncode != 0:
        found = ERROR_CODE.search(finished.stderr)
        named = f"{found.group(1)} " if found else ""
        print(
            f"event_scope_check_unusable\n`aws {arguments[0]} {arguments[1]}` was refused with "
            f"{named}(the CLI exited {finished.returncode}), so this run says nothing about "
            "what the lifecycle rule delivers. The audit reader needs events:DescribeRule, "
            "events:TestEventPattern and batch:ListJobs, all declared in "
            "infra/iam/audit-reader-role.yaml. The full message is not printed because it "
            "names ARNs that carry the account id.",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(EXIT_UNUSABLE)
    return finished.stdout


def fields_read_by(pattern: Mapping[str, Any]) -> frozenset[tuple[str, ...]]:
    """Every field path this event pattern tests, as a tuple of keys.

    An EventBridge pattern is a nested object whose leaves are lists of match expressions, so
    a path ends wherever a list does. Content filters such as ``{"prefix": "run_"}`` live
    inside those lists and are not descended into: they qualify the field above them rather
    than naming another one.
    """

    def walk(node: Mapping[str, Any], prefix: tuple[str, ...]) -> Iterator[tuple[str, ...]]:
        for key, value in node.items():
            here = (*prefix, key)
            if isinstance(value, Mapping):
                yield from walk(value, here)
            else:
                yield here

    return frozenset(walk(pattern, ()))


def assembled_event(
    *,
    job_name: str,
    job_queue_arn: str,
    job_arn: str,
    account: str,
    region: str,
) -> dict[str, Any]:
    """One ``Batch Job State Change`` carrying a real job's name, queue and ARN.

    Only the fields in :data:`ASSEMBLED_FIELDS`, and no attempt to look like a whole Batch
    event. Filling ``status``, ``jobId``, ``attempts`` and the rest with invented values would
    make this look more like the real thing and be no more true, and the guard in :func:`main`
    is what keeps the shortfall from mattering: a pattern that reads a field absent here
    refuses to be judged rather than being judged against nothing.

    ``id`` and ``time`` are the two values here that describe nothing real. ``TestEventPattern``
    rejects an event without them, and no pattern that reads either could be answered by this
    tool at all, so the guard would stop the run before these were consulted.
    """
    return {
        # Hex letters rather than a run of twelve zeroes, which tests/test_evidence.py reads
        # as an AWS account id -- correctly, since twelve digits in a row in this tree usually
        # is one.
        "id": "0000aaaa-0000-0000-0000-0000aaaa0000",
        "account": account,
        "region": region,
        "time": "1970-01-01T00:00:00Z",
        "resources": [job_arn],
        "source": BATCH_SOURCE,
        "detail-type": BATCH_DETAIL_TYPE,
        "detail": {"jobName": job_name, "jobQueue": job_queue_arn},
    }


def unreadable_by_the_recorder(job_name: str) -> bool:
    """Whether ``project_batch_event`` would refuse this job name as not a run id.

    Asked with :data:`~edullm_platform.contracts.identity.RUN_ID_REGEX`, which is the same
    object ``lifecycle_projection`` imports and tests against -- not a second pattern written
    to look like it. tests/test_lifecycle_event_scope_tool.py pins the two to each other, so a
    change to what a run id looks like cannot leave this check agreeing with a recorder that
    has moved.
    """
    return RUN_ID_REGEX.fullmatch(job_name) is None


def _deployed_pattern(*, profile: str | None, region: str) -> dict[str, Any]:
    answer = _aws(
        ["events", "describe-rule", "--name", RULE_NAME, "--query", "EventPattern", "--output", "text"],
        profile=profile,
        region=region,
    ).strip()
    if not answer or answer == "None":
        print(
            f"no_deployed_rule\nEventBridge has no rule named {RULE_NAME} carrying an event "
            "pattern, so either the phase 3 events stack is not applied in this account and "
            "region or the rule has been deleted. Both answer identically and the second is "
            "worth ruling out first.",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(EXIT_DISAGREES)
    parsed = json.loads(answer)
    return parsed if isinstance(parsed, dict) else {}


def _queue_arns(pattern: Mapping[str, Any]) -> list[str]:
    detail = pattern.get("detail")
    queues = detail.get("jobQueue") if isinstance(detail, Mapping) else None
    if not isinstance(queues, list):
        return []
    return [one for one in queues if isinstance(one, str)]


def _jobs_on(queue_arn: str, *, profile: str | None, region: str) -> tuple[dict[str, str], int]:
    """Every distinct job name Batch still lists on this queue, and how many jobs that is.

    Deduplicated by name and paired with one of that name's ARNs, because the pattern reads
    the name and asking EventBridge the same question once per repeated ``edullm-validate-on-
    manifest`` would buy nothing but calls. The undeduplicated count comes back beside it, so
    what is reported can say how many jobs the delivered names stand for.
    """
    queue = queue_arn.rsplit("/", 1)[-1]

    def listing(state: str) -> str:
        return _aws(
            [
                "batch",
                "list-jobs",
                "--job-queue",
                queue,
                "--job-status",
                state,
                "--query",
                "jobSummaryList[].[jobName,jobArn]",
                "--output",
                "json",
            ],
            profile=profile,
            region=region,
        )

    found: dict[str, str] = {}
    listed = 0
    with ThreadPoolExecutor(max_workers=CONCURRENT_READS) as pool:
        answers = list(pool.map(listing, JOB_STATES))
    for answer in answers:
        parsed = json.loads(answer or "[]")
        if not isinstance(parsed, list):
            continue
        for entry in parsed:
            if not isinstance(entry, list) or len(entry) != 2:
                continue
            name, arn = entry
            if not isinstance(name, str) or not isinstance(arn, str):
                continue
            listed += 1
            found.setdefault(name, arn)
    return found, listed


def _matches(event: Mapping[str, Any], pattern: Mapping[str, Any], *, profile: str | None, region: str) -> bool:
    answer = _aws(
        [
            "events",
            "test-event-pattern",
            "--event-pattern",
            json.dumps(pattern),
            "--event",
            json.dumps(event),
            "--query",
            "Result",
            "--output",
            "text",
        ],
        profile=profile,
        region=region,
    ).strip()
    return answer.lower() == "true"


def build_parser() -> argparse.ArgumentParser:
    """Named so tests/test_workflow_tool_arguments.py can import and read it."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--json",
        action="store_true",
        help="write the whole reading to stdout as one document, whatever the verdict",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    profile, region = options.profile, options.region

    pattern = _deployed_pattern(profile=profile, region=region)
    unsupported = sorted(
        ".".join(path) for path in fields_read_by(pattern) - ASSEMBLED_FIELDS
    )
    if unsupported:
        print(
            "pattern_reads_a_field_this_cannot_supply\n"
            f"the deployed rule tests {', '.join(unsupported)}, which the event assembled "
            "here does not carry, so TestEventPattern would answer about an absent value and "
            "this run would report a rule that delivers nothing. Add the field to "
            "assembled_event and to ASSEMBLED_FIELDS, or read whole envelopes off the bus.",
            file=sys.stderr,
            flush=True,
        )
        return EXIT_UNUSABLE

    queue_arns = _queue_arns(pattern)
    if not queue_arns:
        print(
            "rule_names_no_queue\n"
            f"the deployed {RULE_NAME} pattern carries no detail.jobQueue list, so it is not "
            "scoped to this platform's queues at all and every Batch job state change in the "
            "account reaches the recorder.",
            file=sys.stderr,
            flush=True,
        )
        return EXIT_DISAGREES

    account = _aws(
        ["sts", "get-caller-identity", "--query", "Account", "--output", "text"],
        profile=profile,
        region=region,
    ).strip()

    events: list[tuple[str, dict[str, Any]]] = []
    jobs_listed = 0
    for queue_arn in queue_arns:
        jobs, listed = _jobs_on(queue_arn, profile=profile, region=region)
        jobs_listed += listed
        for job_name, job_arn in sorted(jobs.items()):
            events.append(
                (
                    job_name,
                    assembled_event(
                        job_name=job_name,
                        job_queue_arn=queue_arn,
                        job_arn=job_arn,
                        account=account,
                        region=region,
                    ),
                )
            )

    with ThreadPoolExecutor(max_workers=CONCURRENT_READS) as pool:
        verdicts = list(
            pool.map(
                lambda one: _matches(one[1], pattern, profile=profile, region=region),
                events,
            )
        )

    names_considered = len(events)
    delivered = [name for (name, _), matched in zip(events, verdicts, strict=True) if matched]
    refused = [name for name in delivered if unreadable_by_the_recorder(name)]

    refused_names = sorted(set(refused))
    reading = {
        "rule": RULE_NAME,
        "queues": len(queue_arns),
        "jobs_batch_still_lists": jobs_listed,
        "distinct_job_names": names_considered,
        "names_delivered_to_the_recorder": len(delivered),
        "names_the_recorder_would_refuse": len(refused_names),
        "refused": refused_names,
    }
    if options.json:
        print(json.dumps(reading, indent=2, sort_keys=True))

    if refused_names:
        shown = ", ".join(refused_names[:12])
        if len(refused_names) > 12:
            shown += f", and {len(refused_names) - 12} more"
        print(
            "the_rule_delivers_jobs_the_recorder_refuses\n"
            f"{len(refused_names)} of the {names_considered} distinct job names AWS Batch "
            f"still lists on these {len(queue_arns)} queues are delivered to the lifecycle "
            "recorder and are not named for a run, so every state change any of them makes is "
            f"projected, refused, retried five times and dead-lettered: {shown}. While that is "
            "true the dead-letter alarm is red for a cause nobody is going to fix, so it "
            "cannot go red for one somebody would. The scope belongs in the rule's "
            "detail.jobName, not in the handler.",
            file=sys.stderr,
            flush=True,
        )
        return EXIT_DISAGREES

    if not delivered:
        # Not a pass. Zero delivered with jobs present on the queues is the half-working
        # failure infra/batch-events.yaml warns about in as many words: a pattern that matches
        # nothing deploys perfectly and writes no lineage ever again.
        print(
            "the_rule_delivers_nothing\n"
            f"none of the {names_considered} job names on these {len(queue_arns)} queues "
            "matches the deployed pattern, so no run's state changes are reaching the recorder "
            "and no lineage event, attempt or result is being written. A rule scoped to a "
            "renamed queue, and a jobName clause no run id satisfies, both look exactly like "
            "this.",
            file=sys.stderr,
            flush=True,
        )
        return EXIT_DISAGREES

    print(
        f"{RULE_NAME} delivers {len(delivered)} of the {names_considered} distinct job names "
        f"on its {len(queue_arns)} queues, standing for {jobs_listed} jobs Batch still lists, "
        "and the recorder can read every one of them."
    )
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
