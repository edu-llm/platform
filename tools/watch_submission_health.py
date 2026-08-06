"""Watch whether researchers can submit, at the layer where submitting actually breaks.

**On 2026-08-06 ``submit-run.yml`` failed seven times between 05:33 and 06:50 UTC and
nothing anywhere noticed.** It was found by an agent working the merge queue who happened
to glance at a run list, filed as outside its remit because it blocked no merge. Six of the
seven were one defect in ``edullm submit``, which rejoined a shlex-split command with a
plain space and destroyed the quoting that groups ``bash -lc``'s program into one word. The
seventh was the platform correctly refusing a commit with no published image. Between the
first failure and the fix in #317 there were seventy-seven minutes in which every person
who submitted a ``bash -lc`` command was refused, and the only reason anybody knows is
that somebody was looking for something else.

**The three places that could have caught it are all structurally blind, and the reason
will not change.** Every CloudWatch alarm on this platform is on ``AWS/Lambda`` or
``AWS/SQS``; the notifier consumes Batch job state changes; the audit reads the account.
All three watch AWS. These submissions died in the compile job, which runs on a GitHub
runner and refuses *before* admission is called, so no AWS event, metric or log line was
ever emitted for any of the seven. Wiring subscribers to the alarms topic is worth doing
and would not have caught this. Neither would a daily audit whose cron is ``0 5 * * *``,
thirty-three minutes before the window opened and twenty-two hours before its next look.

So this reads the GitHub Actions API. It needs no AWS credential, no OIDC role and no
deployed stack, and on a public repository the runner minutes are free.

**The hard part is not detecting failure, it is not crying wolf.** A refused submission is
usually the platform working: an unregistered dataset, a commit with no image, a bound
exceeded. Going red on those teaches everybody to ignore the signal, which is the failure
mode this whole file exists to prevent. But six of the seven *were* refusals too -- the
compile job rejected the mangled command exactly as it would reject a command a person got
wrong. Conclusion alone cannot separate them, and neither can the failing step, because
both land on ``Compile the submission``.

What separates them is repetition across unrelated work. A correct refusal is
idiosyncratic: one submission, one reason, and the next person is unaffected. A defect in
the tooling refuses everybody the same way. So a refusal is reported and not alarmed on
until the *same normalised reason* has refused several submissions that are not each other's
retries, at which point something systemic is producing it and no reviewer is at fault.

Two signals, held apart because they justify different confidence:

**A failure that is not a refusal on the merits at all.** The compile step distinguishes
these itself -- ``submission_form_unusable`` carries the workflow's own words, "this is not
a refusal on the merits" -- and a failure in any job other than the compile job is
infrastructure rather than judgement. One is enough. These are never correct.

**The same refusal reason across several distinct submissions.** Three runs sharing a
normalised reason, spanning at least two distinct submissions, is the threshold. The
seven-failure window clears it six times over on five distinct submissions; a researcher
retrying one bad dataset three times does not clear it, because all three retries carry the
same display title and count as one submission.

**Logs are read only for runs that already failed.** The run list is one API call and
carries no reason, so the reason costs one log download per failure. Failures are rare, so
the ordinary cost of a look is one call. A log GitHub has expired is reported as unreadable
rather than counted either way, because guessing would be the same mistake in the other
direction.

**Nothing this prints carries an account id.** It reads GitHub and never AWS, so it has no
way to learn one, but the refusal text it echoes is written by the compile job and quoting
it unexamined is how a number reaches a public step summary. Reasons are normalised before
they are printed, and normalising replaces every run of seven or more hex characters and
every run of digits, which is a superset of the shapes an account id or an ARN arrives in.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

__all__ = [
    "EXIT_DISAGREES",
    "EXIT_OK",
    "EXIT_UNUSABLE",
    "Failure",
    "Report",
    "classify_failure",
    "decide",
    "normalise_reason",
    "reason_of",
    "spoken",
]

#: Submissions look healthy over the window that was read.
EXIT_OK: Final = 0
#: Something systemic is stopping submissions, or one of them failed for a reason that is
#: never a judgement. A reader is sent to the platform rather than to a submitter.
EXIT_DISAGREES: Final = 1
#: This check did not manage to look. Reporting it as a pass would silently stop the check
#: covering anything, which is the state this file was written to end.
EXIT_UNUSABLE: Final = 2

#: The job that turns a submission form into a manifest, and the only job whose failure can
#: legitimately be a judgement about the submission rather than a fault in the platform.
COMPILE_JOB: Final = "Compile the submission and classify it"

#: The compile step's own two classifications, echoed to stderr before it exits. Exit 1 is a
#: refusal on the merits; anything else is the form or the reviewed configuration being
#: unreadable, which the workflow itself says is "not a refusal on the merits".
#:
#: These are read against a whole line and never against the log as a substring, because
#: GitHub echoes a step's script into that step's log before running it. Both markers are
#: therefore present in the log of every compile failure, as the source of the two ``echo``
#: statements that might print them. Searching the log for the text finds the script, reports
#: every refusal on the merits as a platform fault, and would have gone red on all eight
#: failures in the window this file was written for, including the one that was correct.
REFUSED_MARKER: Final = "submission_refused"
UNUSABLE_MARKER: Final = "submission_form_unusable"

#: One line of ``gh run view --log``: a job name, a step name, an ISO instant, and then what
#: the step actually printed. Anything the step printed is what this tool reasons about.
LOG_LINE: Final = re.compile(r"^(?:.*?\t)?\d{4}-\d{2}-\d{2}T[\d:.]+Z ?(?P<message>.*)$")

#: The colour GitHub wraps an echoed script line in. Belt to ``LOG_LINE``'s braces: a script
#: line survives the prefix strip like any other, and this is what marks it as not spoken.
ECHOED_SCRIPT: Final = "\x1b[36;1m"

#: A pydantic validation failure carries its human sentence after ``Value error,`` and before
#: the bracketed machine detail. This is the shape the quoting defect arrived in.
VALIDATION_REASON: Final = re.compile(r"Value error, (?P<reason>.+?)(?= \[type=|$)")
#: A refusal the compiler raised itself, rather than one pydantic raised for it.
REFUSAL_REASON: Final = re.compile(r"submission refused: (?P<reason>.+?)$", re.MULTILINE)

#: Run-specific values that must not enter a signature, or every failure looks unique and
#: nothing ever repeats. Hex first, so a commit sha does not survive as a run of digits.
HEX_RUN: Final = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{7,}(?![0-9a-fA-F])")
DIGIT_RUN: Final = re.compile(r"\d+")

#: How many runs must share a reason before it reads as systemic, and how many distinct
#: submissions those runs must span. Both, not either: three retries of one submission are
#: one person's afternoon, and three submissions refused identically are a defect.
SYSTEMIC_RUNS: Final = 3
SYSTEMIC_SUBMISSIONS: Final = 2

FailureClass = Literal["platform_fault", "refusal", "unreadable"]


@dataclass(frozen=True)
class Failure:
    """One failed submission, classified, with the reason it carries normalised."""

    run_id: int
    title: str
    created_at: str
    kind: FailureClass
    reason: str

    @property
    def url(self) -> str:
        return f"https://github.com/edu-llm/platform/actions/runs/{self.run_id}"


@dataclass(frozen=True)
class Report:
    """What the window said, and what a reader should do about it."""

    considered: int
    succeeded: int
    failures: tuple[Failure, ...]
    faults: tuple[Failure, ...]
    systemic: tuple[tuple[str, tuple[Failure, ...]], ...]
    tolerated: tuple[Failure, ...]
    unreadable: tuple[Failure, ...]

    @property
    def healthy(self) -> bool:
        return not self.faults and not self.systemic


def spoken(log: str) -> tuple[str, ...]:
    """What the steps printed, without the script GitHub echoed back before running it.

    The distinction is the whole of this tool's ability to tell a refusal from a fault. Both
    of the compile step's classifications appear in every compile failure's log as the body
    of an ``echo``, so a substring search over the raw log cannot tell which one was reached.
    """
    said: list[str] = []
    for line in log.splitlines():
        if ECHOED_SCRIPT in line:
            continue
        found = LOG_LINE.match(line.lstrip("\ufeff"))
        said.append(found.group("message") if found is not None else line)
    return tuple(said)


def _classified(said: Sequence[str], marker: str) -> bool:
    """Whether the step reached one of its own classifications, as a line and not a substring."""
    return any(line.strip() == marker for line in said)


def normalise_reason(reason: str) -> str:
    """Reduce a refusal to the part that is the same every time it happens.

    The first sentence only. The quoting defect's sentence ends at the word count it
    objected to -- three for one submission and fourteen for another -- and everything after
    it quotes the submitter's own command back at them, so keeping the tail would give two
    instances of one defect two different signatures and neither would ever reach a
    threshold. Digits and hex runs go the same way and for the same reason.
    """
    first = re.split(r"(?<=\.)\s", reason.strip(), maxsplit=1)[0]
    without_hex = HEX_RUN.sub("<hex>", first)
    without_digits = DIGIT_RUN.sub("<n>", without_hex)
    return " ".join(without_digits.split()).rstrip(".").lower()


def reason_of(said: Sequence[str]) -> str | None:
    """The reason a compile job refused, out of what it printed.

    The validation sentence is preferred where both are present, because pydantic's is the
    specific complaint and the compiler's is the envelope around it.
    """
    body = "\n".join(said)
    for pattern in (VALIDATION_REASON, REFUSAL_REASON):
        found = pattern.search(body)
        if found is not None:
            return found.group("reason").strip()
    return None


def classify_failure(
    *,
    run_id: int,
    title: str,
    created_at: str,
    failing_jobs: Sequence[str],
    log: str | None,
) -> Failure:
    """Decide whether a failed submission is a judgement or a fault.

    Order matters. A job other than the compile job is asked about first, because a run can
    fail after a clean compile and the compile job's own markers would then be absent and
    the failure would read as unreadable rather than as the infrastructure fault it is.
    """
    beyond_compile = [name for name in failing_jobs if name != COMPILE_JOB]
    if beyond_compile:
        return Failure(
            run_id=run_id,
            title=title,
            created_at=created_at,
            kind="platform_fault",
            reason=f"the {beyond_compile[0]!r} job failed, which is never a judgement",
        )
    if log is None:
        return Failure(
            run_id=run_id,
            title=title,
            created_at=created_at,
            kind="unreadable",
            reason="no log to read, so this failure is neither counted nor dismissed",
        )
    said = spoken(log)
    if _classified(said, UNUSABLE_MARKER):
        return Failure(
            run_id=run_id,
            title=title,
            created_at=created_at,
            kind="platform_fault",
            reason="the form or the reviewed configuration could not be read",
        )
    if _classified(said, REFUSED_MARKER):
        reason = reason_of(said)
        if reason is None:
            return Failure(
                run_id=run_id,
                title=title,
                created_at=created_at,
                kind="unreadable",
                reason="refused, but the log does not say what for",
            )
        return Failure(
            run_id=run_id,
            title=title,
            created_at=created_at,
            kind="refusal",
            reason=normalise_reason(reason),
        )
    return Failure(
        run_id=run_id,
        title=title,
        created_at=created_at,
        kind="unreadable",
        reason="the compile job classified nothing, so what failed is not written down",
    )


def decide(failures: Iterable[Failure], *, considered: int, succeeded: int) -> Report:
    """Hold the classified failures against the two thresholds."""
    seen = tuple(failures)
    faults = tuple(one for one in seen if one.kind == "platform_fault")
    unreadable = tuple(one for one in seen if one.kind == "unreadable")
    refusals = [one for one in seen if one.kind == "refusal"]

    grouped: defaultdict[str, list[Failure]] = defaultdict(list)
    for refusal in refusals:
        grouped[refusal.reason].append(refusal)

    systemic: list[tuple[str, tuple[Failure, ...]]] = []
    tolerated: list[Failure] = []
    for reason, sharing in sorted(grouped.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        submissions = {one.title for one in sharing}
        if len(sharing) >= SYSTEMIC_RUNS and len(submissions) >= SYSTEMIC_SUBMISSIONS:
            systemic.append((reason, tuple(sharing)))
        else:
            tolerated.extend(sharing)

    return Report(
        considered=considered,
        succeeded=succeeded,
        failures=seen,
        faults=faults,
        systemic=tuple(systemic),
        tolerated=tuple(tolerated),
        unreadable=unreadable,
    )


def render(report: Report, *, since: str, until: str) -> str:
    """The paragraphs a person reads, which say what to do rather than only what happened."""
    lines = [
        f"submit-run.yml between {since} and {until}",
        (
            f"{report.considered} run(s) considered, {report.succeeded} succeeded,"
            f" {len(report.failures)} failed."
        ),
        "",
    ]
    if report.faults:
        lines.append("FAILED FOR A REASON THAT IS NEVER A JUDGEMENT:")
        for fault in report.faults:
            lines.append(f"  {fault.created_at}  {fault.reason}")
            lines.append(f"    {fault.url}")
        lines.append("")
    if report.systemic:
        lines.append("REFUSED THE SAME WAY ACROSS UNRELATED SUBMISSIONS:")
        for reason, sharing in report.systemic:
            submissions = sorted({one.title for one in sharing})
            lines.append(f"  {len(sharing)} run(s) over {len(submissions)} submission(s): {reason}")
            for one in sharing:
                lines.append(f"    {one.created_at}  {one.url}")
        lines.append("")
    if report.tolerated:
        lines.append("REFUSED, AND LEFT ALONE AS THE PLATFORM DOING ITS JOB:")
        for one in report.tolerated:
            lines.append(f"  {one.created_at}  {one.reason}")
        lines.append("")
    if report.unreadable:
        lines.append("FAILED, AND NOT ATTRIBUTED EITHER WAY:")
        for one in report.unreadable:
            lines.append(f"  {one.created_at}  {one.reason}")
            lines.append(f"    {one.url}")
        lines.append("")
    if report.healthy:
        lines.append("Researchers can submit. Nothing here reads as a defect in the platform.")
    else:
        lines.append(
            "Submissions are failing for reasons no submitter can fix. Read the runs above"
            " before onboarding anybody, and do not close this by re-running them."
        )
    return "\n".join(lines)


def _gh(arguments: Sequence[str], *, timeout: int = 120) -> tuple[int, str, str]:
    call = ["gh", *arguments]
    try:
        finished = subprocess.run(call, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 127, "", str(error)
    return finished.returncode, finished.stdout, finished.stderr


def _runs(repository: str, limit: int) -> list[dict[str, object]] | None:
    code, out, _ = _gh(
        [
            "run",
            "list",
            "--repo",
            repository,
            "--workflow",
            "submit-run.yml",
            "--limit",
            str(limit),
            "--json",
            "databaseId,displayTitle,conclusion,createdAt",
        ]
    )
    if code != 0:
        return None
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    return [entry for entry in parsed if isinstance(entry, dict)]


def _failing_jobs(repository: str, run_id: int) -> list[str]:
    code, out, _ = _gh(
        ["run", "view", str(run_id), "--repo", repository, "--json", "jobs"],
    )
    if code != 0:
        return []
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        return []
    jobs = parsed.get("jobs") if isinstance(parsed, dict) else None
    if not isinstance(jobs, list):
        return []
    names: list[str] = []
    for job in jobs:
        if isinstance(job, dict) and job.get("conclusion") == "failure":
            name = job.get("name")
            if isinstance(name, str):
                names.append(name)
    return names


def _failed_log(repository: str, run_id: int) -> str | None:
    code, out, _ = _gh(["run", "view", str(run_id), "--repo", repository, "--log-failed"])
    if code != 0 or not out.strip():
        return None
    return out


def _moment(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="edu-llm/platform")
    parser.add_argument(
        "--hours",
        type=float,
        default=24.0,
        help="how far back to look, from --until or from now",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="an ISO instant to look from, overriding --hours",
    )
    parser.add_argument(
        "--until",
        default=None,
        help="an ISO instant to look to, defaulting to now",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="how many runs to ask GitHub for before filtering to the window",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)

    until = _moment(options.until) if options.until else datetime.now(tz=UTC)
    if until is None:
        print(f"--until is not an ISO instant: {options.until}", file=sys.stderr)
        return EXIT_UNUSABLE
    since = _moment(options.since) if options.since else until - timedelta(hours=options.hours)
    if since is None:
        print(f"--since is not an ISO instant: {options.since}", file=sys.stderr)
        return EXIT_UNUSABLE

    listed = _runs(options.repository, options.limit)
    if listed is None:
        print(
            "could not list submit-run.yml runs, so nothing was checked."
            " This is not a report that submissions are healthy.",
            file=sys.stderr,
        )
        return EXIT_UNUSABLE

    considered = 0
    succeeded = 0
    failures: list[Failure] = []
    for entry in listed:
        created = _moment(entry.get("createdAt"))
        if created is None or not since <= created <= until:
            continue
        conclusion = entry.get("conclusion")
        # A cancelled run says nothing either way: somebody stopped it, and the reason lives
        # with them rather than in the platform.
        if conclusion not in {"success", "failure"}:
            continue
        considered += 1
        if conclusion == "success":
            succeeded += 1
            continue
        run_id = entry.get("databaseId")
        title = entry.get("displayTitle")
        if not isinstance(run_id, int) or not isinstance(title, str):
            continue
        failures.append(
            classify_failure(
                run_id=run_id,
                title=title,
                created_at=str(entry.get("createdAt")),
                failing_jobs=_failing_jobs(options.repository, run_id),
                log=_failed_log(options.repository, run_id),
            )
        )

    report = decide(failures, considered=considered, succeeded=succeeded)
    print(render(report, since=since.isoformat(), until=until.isoformat()))
    return EXIT_OK if report.healthy else EXIT_DISAGREES


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
