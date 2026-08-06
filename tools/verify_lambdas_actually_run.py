"""Ask whether the deployed functions run, rather than whether the right bytes are deployed.

**Every check this platform had asked a question about the artifact, and the notifier
answered all of them correctly for the whole of the time it was broken.** The released zip
was the one this tree builds. The deployed digest was the one the release record named. The
parametrized tripwire confirmed all four functions. ``tools/verify_deployed_lambdas.py``
exited zero. The pending register was empty. And every one of the 934 invocations the
notifier had received since it was first deployed had raised ``FileNotFoundError`` on
``organization.yaml``, because the builder wrote the reviewed configuration to
``edullm_platform/config/`` and the handler read ``/var/task/config/``.

Nothing in that chain invoked anything. A verification chain made entirely of questions
about an artifact will agree that a function which has never once succeeded is fine, and it
will go on agreeing for as long as nobody looks. This is the check that does not.

It asks two questions and they are not the same question.

**Has this function succeeded since it was last deployed?** Read from CloudWatch, over the
window that opens at the function's own ``LastModified``, so a deploy resets it and yesterday's
health cannot vouch for today's code. ``Invocations`` minus ``Errors`` is the count of
invocations that returned. Zero successes against a non-zero invocation count is the
notifier's exact state tonight and is always a finding: the function ran, repeatedly, and
never once worked. No credential beyond the audit reader's is needed for this, and it covers
all four functions.

**Does it run right now, on a real event?** A committed fixture, invoked against the
deployed function, requiring a response with no ``FunctionError``. This is the stronger
question and the narrower one, because invoking a function is only safe where invoking it
does nothing -- see ``SMOKE_FIXTURE`` below, which is set for exactly one of the four and
explains why for the other three.

**Zero invocations is reported rather than assumed to be either.** A function nobody has
called is not a function that works and is not a function that is broken, and the two
functions here that are driven by researcher traffic have quiet nights that are not
incidents. Reporting those as failures would train a reader to skip the job, which is the
condition this file exists to end. So a function with a guaranteed trigger -- a schedule, or
the smoke invocation below -- is red when it has not been invoked, because for those a
silent window is a broken trigger; and one driven by traffic is printed as unexercised and
does not fail the run. What is never allowed is silence.

**Nothing this prints carries an account id**, for the reason
``tools/verify_deployed_lambdas.py`` gives at length: this runs in a scheduled job whose log
is public, and an AWS error message names the calling and resource ARNs. Only the error code
is repeated. The invocation response is read for its error *type* and never echoed whole,
because a handler traceback quotes paths and environment values.

**A METRIC THAT HAS NOT BEEN PUBLISHED YET IS NOT A FUNCTION THAT DID NOT RUN, AND THIS USED
TO REPORT IT AS ONE.** Lambda publishes ``Invocations`` and ``Errors`` to CloudWatch a couple
of minutes after the fact. The window here opens at the function's own ``LastModified``, so a
check run inside those first minutes measures a window in which nothing could have been
published whatever the function did, and it raised ``deployed_lambda_never_invoked`` for a
notifier that had just run four times cleanly. Confirmed rather than assumed on 2026-08-06:
the identical command against the same ``deployed_at`` answered ``3 of 3 invocations`` a few
minutes later with nothing invoked in between.

The window it lies in is exactly the window a deploy workflow runs it in, and the sentence it
lies with is the sentence it would use for a real outage. An instrument built to catch silent
failure that cries wolf on every deploy is ignored inside a week, and then it catches nothing.

Two changes, and neither of them makes the check weaker. :data:`PUBLICATION_LAG_SECONDS` is
how long the metric takes to appear, and a window younger than it cannot support a claim
either way, so it is reported as unmeasurable rather than as a finding. And where ``--invoke``
was passed and the smoke invocation returned, that return is a direct observation of the
function running and returning, which outranks a lagging count of the same event: a
``deployed_lambda_never_invoked`` raised in the same breath as an invocation this process just
made and read is a self-contradiction, not a finding.

What is deliberately unchanged is the outage this exists to catch. A function invoked 934
times that raised every time publishes those 934 errors, and is still red. A quiet
schedule-driven function whose window is older than the lag is still red. What stops being red
is the first two minutes after a deploy.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent

# Imported by bare module name for the reason tools/build_notifier_lambda.py gives: running
# this as a path puts tools/ on sys.path and not the repository root, while pytest does the
# opposite, and importing it as `tools.…` as well makes mypy see one file under two names.
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

from release_lambda import FUNCTIONS, Function
from verify_deployed_lambdas import (
    ERROR_CODE,
    EXIT_DISAGREES,
    EXIT_OK,
    EXIT_UNUSABLE,
    NOT_FOUND_CODE,
    deployed_function_name,
)

__all__ = [
    "EXIT_DISAGREES",
    "EXIT_OK",
    "EXIT_UNUSABLE",
    "SMOKE_FIXTURE",
    "TRIGGER",
    "LambdaLivenessFinding",
    "build_parser",
    "main",
    "smoke_payload",
]

#: Which functions are safe to invoke out of band, and what to invoke them with.
#:
#: ONE ENTRY, AND THE THREE ABSENCES ARE THE DESIGN RATHER THAN A BACKLOG. A smoke
#: invocation is only honest where invoking the function does nothing an operator would have
#: to undo, and that is a property of the handler and not of the fixture:
#:
#: * the **admission validator** admits submissions. Invoking it writes a decision record and
#:   can put work on a queue, so a nightly smoke of it would submit a job a night.
#: * the **lifecycle recorder** projects an event into a lineage record and writes it. A
#:   smoke invocation would file lineage for a run that did not happen, into the same prefix
#:   the real records live in, where nothing downstream could tell it apart.
#: * the **expiry janitor** stops machines. It ignores its event entirely -- it is a
#:   schedule, so there is nothing in the payload -- which means there is no such thing as a
#:   harmless payload for it. Its liveness is covered by the window check below instead, and
#:   by the alarm on its errors, which is the signal ``janitor_handler.handler`` deliberately
#:   raises to produce.
#:
#: The notifier is the exception because a non-terminal Batch event owes no message.
#: ``fixtures/events/batch-running.sanitized.json`` loads all three reviewed catalogs, reads
#: the webhook secret out of Secrets Manager, parses the SQS envelope and projects the event
#: -- which is every step that failed tonight -- and then correctly decides that a run which
#: has started but not ended has nothing to say, so ``WebhookTransport.deliver`` is never
#: reached and nothing is posted to anybody's channel. The one step it does not cover is the
#: HTTP POST itself, which cannot be covered without posting.
#:
#: The fixture is the same file ``tools/render_notification.py`` renders from rather than a
#: second copy, so there is one committed event and it cannot drift from the one a person
#: reads wording out of.
SMOKE_FIXTURE: Final[dict[str, Path]] = {
    "notifier": PROJECT_ROOT / "fixtures" / "events" / "batch-running.sanitized.json",
}

#: What guarantees each function gets invoked, which decides whether a silent window is a
#: finding. ``schedule`` and ``smoke`` are guaranteed and a quiet window means the trigger is
#: broken. ``traffic`` is not: the validator sees a submission when somebody submits and the
#: recorder sees an event when a job changes state, and a night with neither is an ordinary
#: night rather than an incident.
TRIGGER: Final[dict[str, str]] = {
    "validator": "traffic",
    "recorder": "traffic",
    "janitor": "schedule",
    "notifier": "smoke",
}

#: What Lambda calls the field naming an unhandled error. Present means the invocation
#: raised, whatever the HTTP status was: a handler that throws still answers 200, which is
#: the trap that makes `aws lambda invoke` look successful from a shell.
FUNCTION_ERROR_KEY: Final = "FunctionError"

#: How long an invocation is given. The notifier's own timeout is sixty seconds and the CLI
#: has to outlast it, or a slow cold start is reported as a check that could not look.
INVOKE_TIMEOUT_SECONDS: Final = 120

#: How long Lambda takes to publish ``Invocations`` and ``Errors`` to CloudWatch, and
#: therefore how young a window has to be before an empty one says nothing.
#:
#: MEASURED RATHER THAN TAKEN FROM THE DOCUMENTATION, WHICH GIVES NO FIGURE. On 2026-08-06 the
#: notifier was deployed at 07:05:16Z and invoked four times immediately; a read at 07:06
#: answered zero invocations and a read a few minutes later answered three, with nothing
#: invoked in between. Five minutes is above the gap that produced and below any window a
#: reader would call stale, and being generous here costs one line saying the answer is not
#: available yet while being ungenerous costs the whole instrument's credibility.
PUBLICATION_LAG_SECONDS: Final = 300


class LambdaLivenessFinding(Exception):
    """One function does not run, or could not be established to run.

    The same three fields the sibling verifier's finding carries, and the same rule about
    ``code``: exit 1 is a statement about the account and sends a reader to the function,
    exit 2 is a statement about this check and sends them to a credential or a grant.
    Merging them reports a lapsed grant as a broken function.
    """

    def __init__(self, reason: str, detail: str, *, code: int) -> None:
        self.reason = reason
        self.detail = detail
        self.code = code
        super().__init__(f"{reason}: {detail}")


@dataclass(frozen=True)
class Window:
    """What one function did between its last deploy and now."""

    deployed_at: datetime
    invocations: int
    errors: int
    #: How long the window is, in seconds, measured when it was read. Carried rather than
    #: recomputed by the caller, because the clock moves between the read and the verdict and
    #: a window that was too young to measure must not become old enough on the way back.
    seconds: int

    @property
    def published(self) -> bool:
        """Whether enough time has passed for an empty answer to mean anything.

        A WINDOW YOUNGER THAN THE PUBLICATION LAG CANNOT SAY THE FUNCTION DID NOT RUN. It
        cannot say the function did run either, and that is why this is a property of the
        window rather than a fallback verdict: what it justifies is refusing to answer, not
        answering the other way.

        Non-zero invocations are believed whatever this says, because the lag only ever
        withholds datapoints. CloudWatch does not publish an invocation that did not happen.
        """
        return self.seconds >= PUBLICATION_LAG_SECONDS

    @property
    def successes(self) -> int:
        """Invocations that returned. ``Errors`` counts the ones that raised.

        Subtraction rather than a metric of its own, because Lambda publishes no
        ``Successes``. It is exact for these four: none of them is invoked asynchronously in
        a way that would double-count a retry into ``Errors`` without a matching
        ``Invocations``, because the two queue-driven ones are read by an event source
        mapping, which invokes synchronously and counts one of each per attempt.
        """
        return self.invocations - self.errors


def _aws(arguments: Sequence[str], *, profile: str | None, region: str) -> str:
    """One CLI call, or a finding that says nothing was read.

    The CLI rather than boto3, for the reason ``tools/verify_deployed_lambdas.py`` gives:
    this project does not depend on an AWS SDK and the zips are size-limited enough that
    adding one would be paid for by every function.
    """
    call = ["aws", *arguments, "--region", region, *(["--profile", profile] if profile else [])]
    try:
        finished = subprocess.run(
            call, capture_output=True, text=True, timeout=INVOKE_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LambdaLivenessFinding(
            "aws_call_did_not_complete",
            f"`aws {arguments[0]} {arguments[1]}` did not complete "
            f"({error.__class__.__name__}), so nothing was read and nothing is claimed.",
            code=EXIT_UNUSABLE,
        ) from error
    if finished.returncode != 0:
        raise _refusal(arguments, finished.returncode, finished.stderr)
    return finished.stdout


def _refusal(arguments: Sequence[str], status: int, stderr: str) -> LambdaLivenessFinding:
    """A CLI failure as a finding, repeating the error code and nothing else.

    The message the CLI prints continues into the caller's ARN and the resource ARN, and both
    carry the account id. This runs in a job whose log is public, so the code inside the
    brackets is the only part repeated, and it is also the part that decides what to do.
    """
    found = ERROR_CODE.search(stderr)
    code = found.group(1) if found else None
    verb = f"{arguments[0]} {arguments[1]}"

    if code == NOT_FOUND_CODE:
        return LambdaLivenessFinding(
            "deployed_lambda_absent",
            f"`aws {verb}` reports no such function, so the question of whether it runs is "
            "answered by nothing being there to run. Either the stack was never applied, or "
            "this was pointed at another account or region, which answers identically.",
            code=EXIT_DISAGREES,
        )

    named = f"{code} " if code else ""
    return LambdaLivenessFinding(
        "aws_call_refused",
        f"`aws {verb}` was refused with {named}(the CLI exited {status}), so this run says "
        "nothing about whether the function works. A denial here is usually the grant: the "
        "audit reader needs cloudwatch:GetMetricStatistics, and the smoke invocation needs "
        "lambda:InvokeFunction on the notifier alone. Both are declared in "
        "infra/iam/audit-reader-role.yaml. The full message is not printed because it names "
        "the calling and resource ARNs and both carry the account id.",
        code=EXIT_UNUSABLE,
    )


def _metric_sum(
    name: str, function_name: str, *, since: datetime, profile: str | None, region: str
) -> int:
    """One Lambda metric summed over the window, as a whole number.

    A single period spanning the window rather than a series, because the question is a
    total and a series would have to be summed here anyway. CloudWatch requires the period
    to be a multiple of sixty, and rounding up rather than down keeps it one datapoint.
    """
    now = datetime.now(tz=UTC)
    seconds = max(int((now - since).total_seconds()), 60)
    period = ((seconds // 60) + 1) * 60
    answer = _aws(
        [
            "cloudwatch",
            "get-metric-statistics",
            "--namespace",
            "AWS/Lambda",
            "--metric-name",
            name,
            "--dimensions",
            f"Name=FunctionName,Value={function_name}",
            "--start-time",
            since.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "--end-time",
            now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "--period",
            str(period),
            "--statistics",
            "Sum",
            "--query",
            "sum(Datapoints[].Sum)",
            "--output",
            "text",
        ],
        profile=profile,
        region=region,
    ).strip()
    # CloudWatch publishes nothing at all for a metric with no datapoints, and the CLI
    # renders the sum of an empty list as `None`. That is zero invocations, which is a real
    # answer about the function rather than a failure to read one.
    if not answer or answer == "None":
        return 0
    return int(float(answer))


def read_window(function_name: str, *, profile: str | None, region: str) -> Window:
    """What the function has done since the deploy that is currently live.

    ``LastModified`` rather than a fixed lookback, and that is the whole point of the check.
    A twenty-four hour window would let a function that worked this morning vouch for code
    deployed this afternoon, which is exactly the substitution the artifact checks already
    make. The window opens when the code changed.
    """
    stamp = _aws(
        [
            "lambda",
            "get-function-configuration",
            "--function-name",
            function_name,
            "--query",
            "LastModified",
            "--output",
            "text",
        ],
        profile=profile,
        region=region,
    ).strip()
    try:
        deployed_at = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%f%z").astimezone(UTC)
    except ValueError as error:
        raise LambdaLivenessFinding(
            "deploy_time_unreadable",
            f"Lambda reported {function_name}'s LastModified as something this cannot parse, "
            "so the window to measure over is unknown. Measuring over a guessed window would "
            "report health from before the deploy as health of the deployed code.",
            code=EXIT_UNUSABLE,
        ) from error

    return Window(
        deployed_at=deployed_at,
        invocations=_metric_sum(
            "Invocations", function_name, since=deployed_at, profile=profile, region=region
        ),
        errors=_metric_sum(
            "Errors", function_name, since=deployed_at, profile=profile, region=region
        ),
        # Taken after both reads rather than before them, so the age is never larger than the
        # window the metrics were actually asked over. Two CloudWatch calls take a second or
        # two, and rounding the age up would let a window be treated as measurable when the
        # first of the two reads was made before it was.
        seconds=int((datetime.now(tz=UTC) - deployed_at).total_seconds()),
    )


def smoke_payload(fixture: Path) -> bytes:
    """The committed EventBridge envelope, wrapped the way its event source mapping delivers.

    Wrapped here rather than committed pre-wrapped, so there is one fixture. The notifier is
    fed by an SQS queue, so its handler reads ``Records`` and takes each envelope out of a
    ``body`` string; ``tools/render_notification.py`` reads the same file bare. Two committed
    copies of one event would drift, and the one that drifted would be the one nobody renders.
    """
    envelope = json.loads(fixture.read_text(encoding="utf-8"))
    return json.dumps(
        {
            "Records": [
                {
                    # A recognisable id rather than a plausible one. It reaches
                    # `batchItemFailures` if the handler decides this record failed, and a
                    # reader finding it in a log should be able to tell it is not a real
                    # message that a real queue is waiting on.
                    "messageId": "smoke-invocation-not-a-real-message",
                    "body": json.dumps(envelope),
                }
            ]
        }
    ).encode("utf-8")


def smoke_invoke(key: str, function_name: str, *, profile: str | None, region: str) -> bool:
    """Invoke the function on the committed fixture and require it not to raise.

    Answers ``True`` when the invocation returned, and that answer is evidence rather than
    bookkeeping. It is a direct observation, by this process, of the deployed code running
    and returning, and it is what the CloudWatch counters below are a delayed report of. The
    caller uses it to refuse to raise ``deployed_lambda_never_invoked`` about a function it
    just watched run.

    ``FunctionError`` rather than the HTTP status, and the distinction is the trap this check
    exists to avoid. A handler that raises still answers ``StatusCode: 200`` -- the
    invocation was delivered, and the exception is the payload -- so a shell that branches on
    the CLI's exit code sees a success. Tonight's ``FileNotFoundError`` answered 200, 934
    times.
    """
    fixture = SMOKE_FIXTURE[key]
    if not fixture.is_file():
        raise LambdaLivenessFinding(
            "smoke_fixture_absent",
            f"{fixture.relative_to(PROJECT_ROOT)} is committed and is not in this checkout, "
            "so there is no event to invoke with. Nothing is claimed about the function.",
            code=EXIT_UNUSABLE,
        )

    with tempfile.TemporaryDirectory() as directory:
        payload = Path(directory) / "payload.json"
        response = Path(directory) / "response.json"
        payload.write_bytes(smoke_payload(fixture))
        reported = _aws(
            [
                "lambda",
                "invoke",
                "--function-name",
                function_name,
                "--payload",
                f"fileb://{payload}",
                "--cli-read-timeout",
                str(INVOKE_TIMEOUT_SECONDS),
                "--query",
                FUNCTION_ERROR_KEY,
                "--output",
                "text",
                str(response),
            ],
            profile=profile,
            region=region,
        ).strip()
        body = response.read_text(encoding="utf-8") if response.is_file() else ""

    if reported and reported != "None":
        raise LambdaLivenessFinding(
            "smoke_invocation_raised",
            f"{function_name} was invoked with "
            f"{fixture.relative_to(PROJECT_ROOT)} and raised {_error_type(body)} "
            f"({reported}). The deployed function does not run. Read the invocation's own log "
            "group for the traceback: it is not repeated here, because a traceback quotes "
            "paths and environment values and this log is public.",
            code=EXIT_DISAGREES,
        )
    return True


def _error_type(body: str) -> str:
    """The exception class out of an invocation response, and nothing else from it.

    A class name is groupable, is what a reader greps, and carries no value. The
    ``errorMessage`` beside it does carry values -- tonight's named an absolute path inside
    the package -- and the ``stackTrace`` carries more, so neither is repeated.
    """
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return "an error this could not parse"
    named = parsed.get("errorType") if isinstance(parsed, dict) else None
    return named if isinstance(named, str) and named else "an unnamed error"


def check(key: str, function: Function, *, invoke: bool, profile: str | None, region: str) -> str:
    """Both questions for one function, and the sentence that reports the answer.

    The invocation happens before the window is read, so its own success is inside the window
    it is then measured over. That ordering is what makes the notifier's line say something
    rather than nothing on the morning after a deploy that nothing has yet triggered.

    **THE ORDERING IS NOT ENOUGH ON ITS OWN AND USED TO BE TREATED AS THOUGH IT WERE.** Being
    inside the window and being published into it are different things, and the second takes
    a couple of minutes. Two guards close that gap and both of them are about what may be
    concluded from an empty count rather than about what the count says. A window younger
    than :data:`PUBLICATION_LAG_SECONDS` cannot support a conclusion either way. And a smoke
    invocation that returned is a direct observation of the function running, which the
    counters are a delayed report of, so it settles the liveness question by itself.
    """
    name = deployed_function_name(function.template)
    smoked = invoke and key in SMOKE_FIXTURE
    observed = smoke_invoke(key, name, profile=profile, region=region) if smoked else False

    window = read_window(name, profile=profile, region=region)
    since = window.deployed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    smoked_note = " Smoke invocation returned without error." if observed else ""

    if window.invocations == 0:
        if observed:
            # The one case the old code got flatly wrong. This process invoked the function,
            # read the answer, and found no FunctionError, and then reported that the
            # function has never been invoked. Reporting the observation is the whole fix.
            return (
                f"{name} returned on a smoke invocation just now, {_lag_said(window)} after "
                f"it was deployed at {since}. CloudWatch has published no datapoint for it "
                "yet, which is the publication lag rather than a finding."
            )
        if TRIGGER[key] == "traffic":
            return (
                f"{name} has not been invoked since it was deployed at {since}. It is driven "
                "by researcher traffic, so a quiet window is not a finding, and this check "
                "says nothing about whether it works."
            )
        if not window.published:
            # Not a finding and not an answer. A schedule that has not fired yet and a
            # schedule that is detached look identical this soon after a deploy, and the
            # difference is a couple of minutes of waiting rather than anything measurable
            # now. Saying so is what keeps the sentence below meaning something.
            return (
                f"{name} was deployed {_lag_said(window)} ago at {since} and CloudWatch "
                f"publishes Lambda metrics about {PUBLICATION_LAG_SECONDS // 60} minutes "
                "late, so an empty window says nothing yet. Run this again in a few minutes."
            )
        raise LambdaLivenessFinding(
            "deployed_lambda_never_invoked",
            f"{name} has not been invoked once since it was deployed at {since}, and it is "
            f"triggered by a {TRIGGER[key]} rather than by traffic, so the window should not "
            "be empty. Either the trigger is not firing or it is not attached to this "
            "function. A function that has never run is not a function that works.",
            code=EXIT_DISAGREES,
        )

    if window.successes <= 0:
        if observed:
            # Every published invocation raised and the one this process made did not, which
            # is two true things rather than a contradiction: the errors are published and
            # this success is not yet. The function is named as running because it was
            # watched running, and the failures are named because they are the reason to look.
            return (
                f"{name} returned on a smoke invocation just now, and CloudWatch has "
                f"published {window.invocations} invocations since {since} of which all "
                "raised. The smoke is not in that count yet. Read the log group: real events "
                "are failing on a path the committed fixture does not take."
            )
        raise LambdaLivenessFinding(
            "deployed_lambda_has_never_succeeded",
            f"{name} has been invoked {window.invocations} times since it was deployed at "
            f"{since} and every one of them raised. The code that is deployed does not run. "
            "Every artifact check can still be green while this is true, which is the whole "
            "reason this one exists: they compare bytes and this one compares outcomes.",
            code=EXIT_DISAGREES,
        )

    return (
        f"{name} has succeeded {window.successes} of {window.invocations} invocations since "
        f"it was deployed at {since}.{smoked_note}"
    )


def _lag_said(window: Window) -> str:
    """How old the window is, in the units a person waits in."""
    minutes = window.seconds // 60
    return f"{window.seconds}s" if minutes < 1 else f"{minutes}m"


def build_parser() -> argparse.ArgumentParser:
    """Named so tests/test_workflow_tool_arguments.py can import and read it."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--function",
        choices=[*FUNCTIONS, "all"],
        default="all",
        help="which to check; all by default, because one release can move any of them",
    )
    parser.add_argument(
        "--invoke",
        action="store_true",
        help=(
            "also invoke the functions that have a committed fixture, which is the notifier "
            "and only the notifier. Needs lambda:InvokeFunction; without it this reads "
            "CloudWatch and nothing else"
        ),
    )
    # No default profile, for the reason tools/verify_deployed_lambdas.py gives: the audit
    # runs on an assumed role and passes none, and a default would send it looking for an
    # SSO session that is not there.
    parser.add_argument("--profile", default=None)
    parser.add_argument("--region", default="us-east-1")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    selected = (
        list(FUNCTIONS.items())
        if options.function == "all"
        else [(options.function, FUNCTIONS[options.function])]
    )

    findings: list[LambdaLivenessFinding] = []
    for key, function in selected:
        # Every function rather than a return on the first finding, and each answer written
        # where it happens with both streams flushed, for the reasons the sibling verifier
        # gives: one release can move all four, and a log that reports the second function
        # before the first gets misattributed.
        try:
            answer = check(
                key,
                function,
                invoke=options.invoke,
                profile=options.profile,
                region=options.region,
            )
        except LambdaLivenessFinding as finding:
            findings.append(finding)
            print(finding.reason, file=sys.stderr, flush=True)
            print(finding.detail, file=sys.stderr, flush=True)
            continue
        print(answer, flush=True)

    if not findings:
        print("Every function checked has run since it was deployed.")
        return EXIT_OK

    # A definite finding outranks an unanswered question, the sibling verifier's rule:
    # somebody with one function to repair has to repair it whatever happened to the other.
    if any(reported.code == EXIT_DISAGREES for reported in findings):
        return EXIT_DISAGREES
    return EXIT_UNUSABLE


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
