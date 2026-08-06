"""The numbers a person watches move, each one derived and each one carrying its command.

WHY THIS IS A TOOL AND NOT A TABLE SOMEBODY MAINTAINS. The status document this feeds, which
lives in a tree this repository does not track, grew from about eight hundred lines to two
thousand in one evening because ten agents each appended their own reading and nobody removed
anything, so it accumulated numbers that were true when they were typed and false by morning. A
hand-maintained scoreboard at the top of it would be the same disease in a smaller box. Every row below is computed at the moment
it is printed, and a row that cannot be computed prints that it could not rather than printing
the last thing anybody believed.

WHAT COUNTS AS DERIVED. A row is derived when a command answers it. Three of the rows read the
working tree and need nothing else, four ask GitHub through `gh`, and one runs pytest's
collector. None of them reads a number out of a document. Where a source is unreachable the row
carries ``None`` and the reason travels with it, because a scoreboard that silently substitutes
a stale figure for an unreachable one is worse than one that admits the gap.

THE PLANS DIRECTORY IS AN ARGUMENT AND HAS NO DEFAULT, WHICH IS DELIBERATE. The plans this
counts live outside the repository in a private tree, so naming a default here would put a
private path into tracked code and would make the two plan rows unanswerable in CI besides.
Pass ``--plans-dir`` to get them and leave it off to get everything else.

THERE IS NO EXIT CODE 1, for the reason `tools/report_asks.py` gives. A scoreboard is an
instrument and not a control, and a red exit on a number nobody likes turns reading the board
into a thing that can block a merge. 0 when the board was printed, 2 when the arguments were
wrong.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from edullm_platform.stages import (
    STAGES,
    Mark,
    Slice,
    Sources,
    count_reached,
    paths_grepped_on_main,
    read_manifest,
    render_stage_table,
    resolve_manifest,
)

__all__ = [
    "PLAN_TASK_HEADING",
    "SURFACES",
    "AccountStacks",
    "Reading",
    "Row",
    "TaskCensus",
    "TaskStatus",
    "build_parser",
    "census_of_plan_tasks",
    "collected_test_files",
    "count_collected_tests",
    "default_region",
    "fraction",
    "gather",
    "healthy_stacks",
    "main",
    "pull_requests_in_other_repositories",
    "read_task_status",
    "render_slice_rollup",
    "rows",
    "stacks_in_the_account",
    "stacks_mid_flight",
    "status_of_every_task",
    "tasks_in_plans",
]

PROJECT_ROOT: Final = Path(__file__).resolve().parent.parent

#: What each surface is and where to look for it. The stage itself is not in there.
SURFACES: Final = PROJECT_ROOT / "config" / "reports" / "surfaces.yaml"

#: A task heading in a plan. Two spellings are in use and both are load-bearing rather than
#: sloppy. The spine plan numbers its tasks `S1` through `S5` and `P1` through `P8` because it
#: carries two independent tracks in one file, and every other plan numbers from 1. The heading
#: level moved from `##` to `###` between the plans written on 2026-08-04 and those written on
#: 2026-08-05, so the level is not part of the match.
PLAN_TASK_HEADING: Final = re.compile(r"^#{2,4}\s+Task\s+(?P<number>[A-Z]?\d+)\b")

#: Markup a status word can be wearing when it opens the blockquote under a task heading. The
#: established spellings include a heading marker inside the quote and a warning emoji before
#: the word, both of which are prose decisions rather than data, so they are stripped rather
#: than matched.
_MARKUP: Final = re.compile(r"^[>#*\s\u26a0\u2705\u2757\ufe0f\u274c]+")


class TaskStatus(StrEnum):
    """What a plan says about one of its own tasks.

    THE FOUR VALUES ARE NOT A JUDGEMENT AND ARE ONLY A READING. This reports what the plan says,
    so a task that was finished and never marked reads `unmarked` and is counted as such. That
    is the point rather than a flaw. The gap between `done` and the truth is a gap in the plans,
    and putting it on the board is what gets it closed. Inferring completion from anything else
    would put this tool back in the business of holding an opinion, which is exactly what the
    document it feeds was doing wrong.
    """

    DONE = "done"
    SUPERSEDED = "superseded"
    BLOCKED = "blocked"
    UNMARKED = "unmarked"


#: The first word of the note under a task heading, and what it means. Matched on the first word
#: only, because a note that opens `BUILT, in #199 on 2026-08-04` is a status and a note that
#: mentions the word `built` three sentences in is prose about something else. `BUILT` and
#: `DONE` are the two spellings the plans already used before this tool existed and both are
#: kept, so the count did not start at zero and no plan had to be rewritten to be read.
_STATUS_WORDS: Final[Mapping[str, TaskStatus]] = {
    "DONE": TaskStatus.DONE,
    "BUILT": TaskStatus.DONE,
    "MERGED": TaskStatus.DONE,
    "LANDED": TaskStatus.DONE,
    "SUPERSEDED": TaskStatus.SUPERSEDED,
    "WITHDRAWN": TaskStatus.SUPERSEDED,
    "BLOCKED": TaskStatus.BLOCKED,
    "GATED": TaskStatus.BLOCKED,
}


@dataclass(frozen=True)
class TaskCensus:
    """How many of a plan set's tasks stand where."""

    total: int
    done: int
    superseded: int
    blocked: int
    unmarked: int


@dataclass(frozen=True)
class Row:
    """One line of the board.

    `value` is ``None`` where the source could not be reached, and `note` says why. A row never
    carries a figure it did not compute on this run.
    """

    label: str
    value: str | None
    command: str
    note: str = ""


@dataclass(frozen=True)
class Reading:
    """One pass over every source, and what it could not see.

    A board printed from a complete `blind` list and a board printed from an empty one are two
    different claims about the same account, and the fractions alone do not distinguish them.
    Carrying the list beside the sources is what lets the printed board say which it is.
    """

    sources: Sources
    blind: Sequence[str]
    region: str


def read_task_status(note: str) -> TaskStatus:
    """The status a plan's own note under a task heading declares.

    Mutation this is written against: read the whole note and look for the word anywhere in it.
    The measurement plan's Task 9 note opens `This task spends money and depends on Tasks 1 and
    2`, and three sentences later says what being done would look like. A search over the whole
    note calls that task done. Reading the first word only calls it unmarked, which is right.
    """
    stripped = _MARKUP.sub("", note).strip()
    if not stripped:
        return TaskStatus.UNMARKED
    first = re.split(r"[^A-Za-z-]", stripped, maxsplit=1)[0].upper()
    return _STATUS_WORDS.get(first, TaskStatus.UNMARKED)


def _note_under(lines: Sequence[str], heading: int) -> str:
    """The first non-empty line under a task heading, when that line is a blockquote.

    A task with no note at all and a task whose first line is ordinary prose are the same thing
    here, which is why this stops at the first non-empty line rather than scanning forward for a
    blockquote. A plan that opens a task with two paragraphs of context and marks it done six
    screens later has not marked it in a place a reader sees.
    """
    for line in lines[heading + 1 : heading + 6]:
        if not line.strip():
            continue
        return line if line.lstrip().startswith(">") else ""
    return ""


def status_of_every_task(plan: str) -> Iterator[tuple[str, TaskStatus]]:
    """Every task heading in one plan, with what the plan says about it."""
    lines = plan.splitlines()
    for index, line in enumerate(lines):
        heading = PLAN_TASK_HEADING.match(line)
        if heading is None:
            continue
        yield heading.group("number"), read_task_status(_note_under(lines, index))


def census_of_plan_tasks(plans: Iterable[Path]) -> TaskCensus:
    """The four counts across a set of plan files."""
    tally = {status: 0 for status in TaskStatus}
    for plan in plans:
        for _, status in status_of_every_task(plan.read_text(encoding="utf-8")):
            tally[status] += 1
    return TaskCensus(
        total=sum(tally.values()),
        done=tally[TaskStatus.DONE],
        superseded=tally[TaskStatus.SUPERSEDED],
        blocked=tally[TaskStatus.BLOCKED],
        unmarked=tally[TaskStatus.UNMARKED],
    )


def count_collected_tests(collector_output: str) -> int | None:
    """The size of the suite, off pytest's own collection line.

    Mutation this is written against: count the lines pytest printed. `--collect-only -q` prints
    one line per test and then a summary, so counting lines is off by the summary and by any
    warning block, and it silently becomes very wrong when a plugin prints a banner. The summary
    line is pytest's own arithmetic and is the thing to read.
    """
    match = re.search(r"(\d+)\s+tests? collected", collector_output)
    return int(match.group(1)) if match else None


def pull_requests_in_other_repositories(
    pull_requests: Iterable[Mapping[str, Any]], *, this_repository: str
) -> int:
    """Open pull requests one person opened somewhere they cannot merge them.

    This is the machine-readable half of "waiting on somebody who is not you". It is a floor
    rather than the whole list, because an ask sent in a chat message leaves no artifact
    anywhere and nothing can count it. What it does count is exact. A pull request the owner
    opened in another group's repository is a merge somebody else has to perform, and the
    number falling is the only evidence that the asking worked.
    """
    return sum(
        1
        for pull_request in pull_requests
        if str(pull_request.get("repository", {}).get("name", "")) != this_repository
    )


def collected_test_files(collector_output: str) -> frozenset[str]:
    """Which test files pytest actually collected a test out of.

    Mutation this is written against: take every line that ends in `.py`. `--collect-only -q`
    prints one line per test as `path::name`, and it also prints error and warning blocks that
    name files. Requiring the `::` is what separates a file a test came out of from a file
    something went wrong in, and only the first is evidence that a check exists.
    """
    return frozenset(
        line.split("::", 1)[0].strip()
        for line in collector_output.splitlines()
        if "::" in line and line.strip().endswith(tuple("]") + ("",)) and line.startswith("tests/")
    )


def _paths_on_default_branch() -> frozenset[str] | None:
    """Every path the default branch holds, which is what `deployed` means for a workflow.

    A workflow, a skill or an issue template goes live by being merged, so a working tree that
    has it and `origin/main` that does not is a thing built and not deployed. That distinction
    is invisible to a check that reads the tree, and it is precisely the state a row sits in
    while its pull request is open.

    Mutation this is written against: fall back to `HEAD` when `origin/main` cannot be read.
    That reads the fifteen `on_main` rows off the working tree, which is the same tree the
    `built` column reads, so every one of them jumps to deployed the moment somebody writes
    the file -- and it does it silently, in a checkout without a remote, which is where
    somebody is most likely to be reading the board and least likely to notice. An
    unreadable branch is a row nobody read.
    """
    listed = _git("ls-tree", "-r", "--name-only", "origin/main")
    return None if listed is None else frozenset(listed.splitlines())


def _contents_on_default_branch(paths: frozenset[str]) -> tuple[dict[str, str], list[str]]:
    """What the default branch holds inside each of these files, and which ones would not read.

    One `git show` per path, which is why the caller passes only the paths a `grep_on_main` rule
    names rather than everything `on_main` covers. Four `git show`s is not the four hundred and
    eighty subprocesses `gather` exists to avoid; four hundred would be.

    Mutation this is written against: return the empty mapping for a path that would not read and
    let the cell fall through to whatever it makes of a missing key. It already makes `not read`
    of it, so the cell is right either way -- but the run would say it read everything, and a
    board that lists its blind spots above the table and then omits one is worse than a board
    that lists none, because the omission is the thing a reader has stopped checking for.
    """
    found: dict[str, str] = {}
    refused: list[str] = []
    for path in sorted(paths):
        blob = _git("show", f"origin/main:{path}")
        if blob is None:
            refused.append(path)
        else:
            found[path] = blob
    return found, refused


def _git(*arguments: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            capture_output=True,
            text=True,
            check=False,
            cwd=PROJECT_ROOT,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def default_region() -> str:
    """Where to look for the stacks, taken from the checker that owns the list of them.

    Mutation this is written against: write the region here as well. It was written here as
    well, as `us-east-2`, while `tools/verify_deployed_stacks.py` said `us-east-1`, and the
    two disagreed for as long as nobody ran the board without `--region`. When somebody did,
    on 2026-08-06, it listed a region holding none of these stacks and printed 30 of 55 where
    the account holds 43. Two constants that must agree and are written twice will disagree,
    and the one that gets read less is the one that goes wrong quietly.
    """
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    from verify_deployed_stacks import DEFAULT_REGION

    return str(DEFAULT_REGION)


def healthy_stacks(deployed: Mapping[str, str], applied: Iterable[str]) -> frozenset[str]:
    """The stacks the account holds in a status that means a template was applied.

    The allow-list is imported from `tools/verify_deployed_stacks.py` rather than restated,
    because it was widened once already to stop `ROLLBACK_COMPLETE` reading as a deploy, and a
    second copy of it here is a second place that fix would have had to be made.
    """
    permitted = frozenset(applied)
    return frozenset(name for name, status in deployed.items() if status in permitted)


def stacks_mid_flight(deployed: Mapping[str, str]) -> frozenset[str]:
    """The stacks CloudFormation is part-way through an operation on.

    `REVIEW_IN_PROGRESS` is excluded and is the whole reason this is not a substring test. It
    reads like the others and it is not transient: a change set was created against the name
    and never executed, so nothing is deployed and nothing is going to become deployed on its
    own. Calling that unread would hide a real `no` behind a word, and one of those sat in
    this account for five days already. Every other `_IN_PROGRESS` resolves in minutes, which
    is what makes re-reading the right answer rather than guessing which way it will land.
    """
    return frozenset(
        name
        for name, status in deployed.items()
        if status.endswith("_IN_PROGRESS") and status != "REVIEW_IN_PROGRESS"
    )


@dataclass(frozen=True)
class AccountStacks:
    """What one reading of the account found, or why there was not one.

    `applied` is ``None`` when the account was not read at all. `why` is empty exactly when it
    was, so a caller can print the reason without inferring it from the absence.
    """

    applied: frozenset[str] | None
    mid_flight: frozenset[str] = frozenset()
    why: str = ""


def stacks_in_the_account(arguments: argparse.Namespace) -> AccountStacks:
    """Every stack of ours the account holds, or a refusal that says why there is no answer.

    A LISTING HOLDING NONE OF OUR STACKS IS REFUSED RATHER THAN BELIEVED. The manifest names
    fourteen stacks by name, so a region answering with none of them is not the news that
    fourteen deploys were rolled back overnight, it is the news that this looked somewhere
    they were never deployed. That is not hypothetical: it produced the 30 of 55 that went
    into the status document on 2026-08-06, from a run with a working session and the wrong
    default region. The empty reading was the dangerous one precisely because it is a
    reading -- every cell went to a confident `no`, the denominator stayed at 55, and no
    fallback fired, so the board looked more measured than the correct run beside it.
    """
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    try:
        from verify_deployed_stacks import STATUSES_WITH_A_TEMPLATE_APPLIED, list_deployed_stacks
    except ImportError as error:
        return AccountStacks(None, why=f"the stack reader could not be imported ({error})")
    try:
        deployed = list_deployed_stacks(profile=arguments.profile, region=arguments.region)
    except Exception as error:  # noqa: BLE001 - no session is an unread row rather than a crash
        return AccountStacks(None, why=f"{arguments.region} was not read ({error})")
    if not deployed:
        return AccountStacks(
            None,
            why=(
                f"{arguments.region} holds no stack this repository deploys, which is a "
                "region or an account this platform was never deployed into rather than an "
                "account that lost every stack overnight"
            ),
        )
    return AccountStacks(
        applied=healthy_stacks(deployed, STATUSES_WITH_A_TEMPLATE_APPLIED),
        mid_flight=stacks_mid_flight(deployed),
    )


def _buckets() -> frozenset[str] | None:
    raw = _aws("s3api", "list-buckets", "--output", "json")
    if raw is None:
        return None
    try:
        return frozenset(str(bucket["Name"]) for bucket in json.loads(raw)["Buckets"])
    except (ValueError, KeyError, TypeError):
        return None


def _aws(*arguments: str) -> str | None:
    try:
        completed = subprocess.run(
            ["aws", *arguments], capture_output=True, text=True, check=False, timeout=120
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def tasks_in_plans(directory: Path, glob: str) -> frozenset[str]:
    """Every task a plan carries, keyed the way the surface manifest names them.

    The key drops the date prefix off the filename, so `2026-08-04-the-measurement.md` Task 4 is
    `the-measurement/4`. The manifest is tracked and the plans are not, and a tracked file must
    not carry a path into a private tree. A stem is a name rather than a path, and it survives
    the plans moving.
    """
    found: set[str] = set()
    for plan in sorted(directory.glob(glob)):
        stem = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", plan.stem)
        for number, _ in status_of_every_task(plan.read_text(encoding="utf-8")):
            found.add(f"{stem}/{number}")
    return frozenset(found)


def gather(
    arguments: argparse.Namespace,
    *,
    collector_output: str | None,
    wanted_from_main: frozenset[str] = frozenset(),
) -> Reading:
    """Every source the board needs, read once each, and a note for every one that refused.

    Ninety-six rows over five stages is four hundred and eighty lookups, and performing them
    row by row would be several hundred subprocesses and a rate limit. Every one of these
    returns ``None`` when its source is unreachable, and ``None`` becomes `not read` in the
    cells that needed it rather than an exception that costs the whole board.

    THE REFUSALS ARE COLLECTED RATHER THAN INFERRED FROM THE ``None``s. Two runs of this
    against an unchanged account differ only in what they managed to read, and the fraction
    alone cannot tell a reader which of the two they are holding. Naming the sources that did
    not answer, above the table, is what turns "the number moved" into "the number moved
    because nobody could reach CloudFormation".
    """
    blind: list[str] = []
    environments = _gh(
        "api",
        f"repos/{arguments.repo}/environments",
        "--jq",
        "[.environments[].name]",
    )
    released = _gh("release", "list", "--repo", arguments.repo, "--limit", "1", "--json", "tagName")
    plans = (
        tasks_in_plans(Path(arguments.plans_dir), arguments.plans_glob)
        if arguments.plans_dir
        else None
    )
    stacks = stacks_in_the_account(arguments)
    if stacks.why:
        blind.append(f"CloudFormation: {stacks.why}")

    on_main = _paths_on_default_branch()
    if on_main is None:
        blind.append("the default branch: `git ls-tree origin/main` did not answer")
    main_contents, unread_on_main = _contents_on_default_branch(wanted_from_main)
    if unread_on_main:
        blind.append(
            "files on the default branch: `git show origin/main:` did not answer for "
            + ", ".join(unread_on_main)
        )
    buckets = _buckets()
    if buckets is None:
        blind.append("S3: `aws s3api list-buckets` did not answer")
    if environments is None:
        blind.append(f"GitHub environments: `gh api repos/{arguments.repo}/environments` refused")
    if released is None:
        blind.append("GitHub releases: `gh release list` refused")
    if collector_output is None:
        blind.append("the test suite: pytest did not collect")
    if plans is None:
        blind.append("the plans: no --plans-dir was given")

    return Reading(
        sources=Sources(
            tree=PROJECT_ROOT,
            on_main=on_main,
            main_contents=main_contents,
            collected_tests=(
                None if collector_output is None else collected_test_files(collector_output)
            ),
            healthy_stacks=stacks.applied,
            stacks_mid_flight=stacks.mid_flight,
            buckets=buckets,
            environments=None if environments is None else frozenset(json.loads(environments)),
            released=None if released is None else bool(json.loads(released)),
            plan_tasks=plans,
        ),
        blind=blind,
        region=str(arguments.region),
    )


def _gh(*arguments: str) -> str | None:
    """`gh`, or ``None`` when it is missing, unauthenticated or refused.

    Every caller treats ``None`` as a row that could not be computed rather than as a zero. A
    zero here would read as good news on a board whose whole purpose is that a number nobody
    recomputed is not evidence.
    """
    try:
        completed = subprocess.run(
            ["gh", *arguments],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def _merged_since(repository: str, since: str) -> str | None:
    return _gh(
        "pr",
        "list",
        "--repo",
        repository,
        "--state",
        "merged",
        "--search",
        f"merged:>={since}",
        "--limit",
        "500",
        "--json",
        "number",
        "--jq",
        "length",
    )


#: The three stages a slice is rolled up over. `designed` and `planned` are left off the summary
#: because neither moves when the work moves: `designed` is a judgement about a document and is a
#: person's answer on every row, and `planned` says whether a task exists rather than whether
#: anything happened. Both are still in the detail view, which is where a question about them
#: gets asked.
ROLLED_UP: Final = ("built", "deployed", "proven")


def fraction(group: Slice, stage: str) -> str:
    """One slice at one stage, as the fraction and the two things that qualify it.

    THE QUALIFIERS ARE NOT DECORATION. Rolling ninety-six rows into nine loses exactly the
    information that makes a fraction trustworthy, so both losses are put back on the cell. A
    `*` means part of the yes side is somebody's recollection rather than a reading, which is
    what the whole board is built to keep visible. An `unread` count is rows in the
    denominator that no lookup could answer, and without it a `deployed` column read with no
    AWS session looks small and confident rather than largely unasked.

    THE DENOMINATOR DOES NOT MOVE WHEN THE UNREAD COUNT DOES. It used to: an unread row left
    both sides, so a blind run printed a smaller fraction of a smaller whole and read better
    than the run beside it that could see. Now the whole is what the manifest says the stage
    applies to, and the unread count is how much of that whole this run failed to reach.
    """
    reached, applicable = count_reached([group], stage)
    cells = [surface.cells[stage] for surface in group.surfaces]
    unread = sum(1 for cell in cells if cell.mark is Mark.NOT_READ)
    spoken = sum(1 for cell in cells if cell.moved and not cell.derived)
    if applicable == 0:
        return "n/a"
    text = f"{reached} of {applicable}{'*' if spoken else ''}"
    return f"{text} ({unread} unread)" if unread else text


def _bold(text: str) -> str:
    """The figure in bold with its qualifiers left outside.

    Mutation this is written against: wrap the whole cell. `**30 of 55***` is three closing
    asterisks and markdown renders it as bold text followed by nothing, silently eating the one
    mark that says part of the total is somebody's recollection. The qualifier has to sit
    outside the emphasis to survive.
    """
    figure, opened, tail = text.partition(" (")
    marked = figure.endswith("*")
    bolded = f"**{figure.removesuffix('*')}**" + ("\\*" if marked else "")
    return bolded + (f"{opened}{tail}" if opened else "")


def render_slice_rollup(
    board: Sequence[Slice],
    *,
    checked: str,
    moment: datetime,
    blind: Sequence[str] = (),
    region: str = "",
) -> str:
    """The whole platform on one screen, a row per slice and a fraction per stage.

    WHY THIS IS THE DEFAULT AND THE PER-SURFACE TABLE IS NOT. Ninety-six rows is a detail view.
    It answers "which thing is undeployed" and it cannot answer "where are we", because nothing
    on it is a total and a reader has to count ninety-six rows to get one. These nine rows
    answer the second question and name the command that answers the first.

    WHAT DID NOT ANSWER IS PRINTED ABOVE THE TABLE AND NOT BELOW IT. Four readings of this
    board went into the status document within two hours on 2026-08-06 disagreeing by up to
    thirteen rows, and a reader holding any two of them had nothing on either to say which was
    the trustworthy one. A figure produced by a run that could not reach CloudFormation has to
    say so before the figure, because after the figure is after the reader has believed it.
    """
    total = Slice(name="Total", surfaces=[s for group in board for s in group.surfaces])
    legend = (
        "A denominator counts the rows the stage applies to and does not move when a lookup "
        f"fails. `*` means part of that yes side is a person's answer as of {checked} rather "
        "than a reading, and `unread` is rows in the denominator that no lookup could answer "
        "on this run, counted as neither reached nor not."
    )
    read_at = (
        f"Read at {moment.strftime('%Y-%m-%d %H:%M')} UTC by `tools/scoreboard.py`"
        + (f", against {region}" if region else "")
        + ". Every figure is computed on the run that prints it."
    )
    warning = (
        []
        if not blind
        else [
            "",
            (
                f"**This run could not read {len(blind)} of its sources, so every row that "
                "needed one is unread rather than answered. Do not hold these figures "
                "against a run that could read them.**"
            ),
            "",
            *[f"- {reason}" for reason in blind],
        ]
    )
    lines = [
        read_at,
        *warning,
        "",
        "| Slice | Built | Deployed | Proven |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(
        "| {} | {} |".format(group.name, " | ".join(fraction(group, s) for s in ROLLED_UP))
        for group in board
    )
    joined = " | ".join(_bold(fraction(total, stage)) for stage in ROLLED_UP)
    lines.append(f"| **Total** | {joined} |")
    lines.extend(["", legend])
    return "\n".join(lines)


def rows(
    arguments: argparse.Namespace,
    *,
    now: datetime | None = None,
    collected: int | None = None,
) -> list[Row]:
    """The board, computed."""
    moment = now or datetime.now(tz=UTC)
    yesterday = (moment - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    repository = arguments.repo
    short = repository.split("/")[-1]

    board = [
        Row(
            label="Tests in the suite",
            value=None if collected is None else f"{collected:,}",
            command="uv run --frozen pytest --collect-only -q",
        ),
        Row(
            label="Latest release",
            value=_gh(
                "release", "view", "--repo", repository, "--json", "tagName", "--jq", ".tagName"
            ),
            command=f"gh release view --repo {repository} --json tagName --jq .tagName",
        ),
        Row(
            label="Pull requests merged in the last 24 hours",
            value=_merged_since(repository, yesterday),
            command=(
                f"gh pr list --repo {repository} --state merged "
                f'--search "merged:>=$(date -u -v-1d +%Y-%m-%dT%H:%M:%SZ)" --limit 500 '
                "--json number --jq length"
            ),
        ),
        Row(
            label="Pull requests open here",
            value=_gh(
                "pr",
                "list",
                "--repo",
                repository,
                "--state",
                "open",
                "--limit",
                "200",
                "--json",
                "number",
                "--jq",
                "length",
            ),
            command=f"gh pr list --repo {repository} --state open --json number --jq length",
        ),
    ]
    board.append(_blocked_row(arguments, short))
    board.extend(_plan_rows(arguments))
    board.append(_hand_applied_row())
    return board


def _blocked_row(arguments: argparse.Namespace, short: str) -> Row:
    owner = arguments.repo.split("/")[0]
    command = (
        f"gh search prs --owner {owner} --state open --author {arguments.author or '<login>'} "
        f"--limit 200 --json repository --jq '[.[]|select(.repository.name!=\"{short}\")]|length'"
    )
    if not arguments.author:
        return Row(
            label="Open pull requests waiting on somebody else",
            value=None,
            command=command,
            note="Pass --author to compute it",
        )
    raw = _gh(
        "search",
        "prs",
        "--owner",
        owner,
        "--state",
        "open",
        "--author",
        arguments.author,
        "--limit",
        "200",
        "--json",
        "repository",
    )
    if raw is None:
        return Row(
            label="Open pull requests waiting on somebody else",
            value=None,
            command=command,
            note="`gh` could not be asked",
        )
    found = pull_requests_in_other_repositories(json.loads(raw), this_repository=short)
    return Row(
        label="Open pull requests waiting on somebody else",
        value=str(found),
        command=command,
        note="Pull requests this author opened in other groups' repositories",
    )


def _plan_rows(arguments: argparse.Namespace) -> list[Row]:
    command = "uv run --frozen python tools/scoreboard.py --plans-dir <plans>"
    if arguments.plans_dir is None:
        return [
            Row("Plan tasks done", None, command, note="Pass --plans-dir to compute it"),
            Row(
                "Plan tasks nobody has marked", None, command, note="Pass --plans-dir to compute it"
            ),
        ]
    every_file = sorted(Path(arguments.plans_dir).glob(arguments.plans_glob))
    # A file matching the glob that carries no task heading is a design document filed beside
    # the plans rather than a plan, and counting it inflates the denominator's reach without
    # moving the denominator. `2026-08-04-the-build.md` is the standing example.
    plans = [
        plan for plan in every_file if any(status_of_every_task(plan.read_text(encoding="utf-8")))
    ]
    census = census_of_plan_tasks(plans)
    reach = f"{len(plans)} plans"
    return [
        Row(
            label="Plan tasks done",
            value=f"{census.done} of {census.total}",
            command=command,
            note=(
                f"Across {reach}. {census.superseded} superseded, {census.blocked} blocked, "
                f"{census.unmarked} carry no marker"
            ),
        ),
        Row(
            label="Plan tasks nobody has marked",
            value=str(census.unmarked),
            command=command,
            note="A task finished and not marked reads here. The number falling is the plans "
            "catching up with the work",
        ),
    ]


def _hand_applied_row() -> Row:
    """Deploy steps that need a laptop, counted off the tree rather than off the account.

    THIS IS A FLOOR AND SAYS SO. The permission boundary withholds IAM from CI, so every stack
    creating a role is applied by a person holding an SSO session, and the tree records the ones
    somebody wrote down as owed. A stack declared and never deployed is not in this count,
    because answering that needs the account. `tools/verify_deployed_stacks.py` is the reading
    that does need it, and the board names it rather than guessing at it.
    """
    try:
        from edullm_platform.pending_amendments import PENDING_AMENDMENTS, PENDING_RELEASES
    except Exception:  # noqa: BLE001 - an import failure is an unanswerable row, not a crash
        return Row(
            label="Hand-applied deploy steps owed",
            value=None,
            command="uv run --frozen python -c '...pending_amendments...'",
            note="the package could not be imported",
        )
    owed = len(PENDING_AMENDMENTS) + len(PENDING_RELEASES)
    return Row(
        label="Hand-applied deploy steps owed",
        value=str(owed),
        command=(
            'uv run --frozen python -c "from edullm_platform.pending_amendments import '
            "PENDING_AMENDMENTS, PENDING_RELEASES; print(len(PENDING_AMENDMENTS) + "
            'len(PENDING_RELEASES))"'
        ),
        note=(
            f"{len(PENDING_AMENDMENTS)} IAM amendments and {len(PENDING_RELEASES)} Lambda "
            "releases recorded in the tree. Stacks declared and never deployed need the "
            "account. Run tools/verify_deployed_stacks.py for that half"
        ),
    )


def _run_collector() -> str | None:
    """pytest's collection, run once and read twice.

    Both the suite size and every `proven` cell backed by a test come out of this, so running
    the collector twice would double the slowest part of the board for no new information.
    """
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            capture_output=True,
            text=True,
            check=False,
            cwd=PROJECT_ROOT,
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout


def render(board: Sequence[Row], *, moment: datetime, heading: bool = True) -> str:
    """The board as markdown, with the command beside every figure.

    `heading` is off when the slice rollup printed above this, because that carries the same
    read-at line, and two identical timestamps fifteen lines apart is the duplication this
    document is being cured of.
    """
    heading_lines = [
        (
            f"Read at {moment.strftime('%Y-%m-%d %H:%M')} UTC by `tools/scoreboard.py`. Every "
            "figure is computed on the run that prints it."
        ),
        "",
    ]
    lines = [
        *(heading_lines if heading else []),
        "| | Now | Re-run it |",
        "| --- | --- | --- |",
    ]
    for row in board:
        value = row.value if row.value is not None else "**not read**"
        lines.append(f"| {row.label} | {value} | `{row.command}` |")
    notes = [row for row in board if row.note]
    if notes:
        lines.append("")
        for row in notes:
            lines.append(f"- **{row.label}.** {row.note}.")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="edu-llm/platform", help="owner/name to ask GitHub about")
    parser.add_argument("--author", help="the login whose cross-repository pull requests to count")
    parser.add_argument("--plans-dir", help="directory of plan markdown files to take a census of")
    parser.add_argument(
        "--plans-glob",
        default="2026-08-0[45]-*.md",
        help="which files in --plans-dir are plans",
    )
    parser.add_argument("--profile", help="AWS profile to read the account's stacks under")
    parser.add_argument("--region", default=default_region(), help="AWS region the stacks are in")
    parser.add_argument("--surfaces", default=str(SURFACES), help="the surface manifest to resolve")
    parser.add_argument("--stages-only", action="store_true", help="the table and nothing else")
    parser.add_argument(
        "--detail",
        action="store_true",
        help="a row per surface rather than a row per slice, for asking which one",
    )
    parser.add_argument("--json", action="store_true", help="one JSON document instead of a table")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    moment = datetime.now(tz=UTC)

    collector_output = _run_collector()
    manifest = read_manifest(Path(arguments.surfaces))
    reading = gather(
        arguments,
        collector_output=collector_output,
        wanted_from_main=paths_grepped_on_main(manifest),
    )
    board = resolve_manifest(manifest, reading.sources)
    counted = (
        []
        if arguments.stages_only
        else rows(
            arguments,
            now=moment,
            collected=None if collector_output is None else count_collected_tests(collector_output),
        )
    )
    checked = str(manifest["checked"])

    if arguments.json:
        print(json.dumps(_document(board, counted, manifest, moment, reading), indent=2))
        return 0
    if arguments.detail:
        print(render_stage_table(board, checked=checked))
        return 0
    print(
        render_slice_rollup(
            board, checked=checked, moment=moment, blind=reading.blind, region=reading.region
        )
    )
    if counted:
        print()
        print(render(counted, moment=moment, heading=False))
    return 0


def _document(
    board: Sequence[Slice],
    counted: Sequence[Row],
    manifest: Mapping[str, Any],
    moment: datetime,
    reading: Reading,
) -> dict[str, Any]:
    return {
        "read_at": moment.isoformat(),
        "manually_checked": str(manifest["checked"]),
        "region": reading.region,
        # A caller reading this rather than the table needs the same warning the table
        # carries, and needs it as a list rather than as a sentence, because the whole point
        # of the JSON is that nobody has to parse the prose.
        "sources_not_read": list(reading.blind),
        "surfaces": [
            {
                "slice": group.name,
                "id": surface.id,
                "name": surface.name,
                **{
                    stage: {
                        "mark": surface.cells[stage].mark.value,
                        "derived": surface.cells[stage].derived,
                        "note": surface.cells[stage].note,
                    }
                    for stage in STAGES
                },
            }
            for group in board
            for surface in group.surfaces
        ],
        "rows": [
            {"label": row.label, "value": row.value, "command": row.command, "note": row.note}
            for row in counted
        ],
    }


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
