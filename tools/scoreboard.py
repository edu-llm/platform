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
    Slice,
    Sources,
    count_reached,
    read_manifest,
    render_stage_table,
    resolve_manifest,
)

__all__ = [
    "PLAN_TASK_HEADING",
    "SURFACES",
    "Row",
    "TaskCensus",
    "TaskStatus",
    "build_parser",
    "census_of_plan_tasks",
    "collected_test_files",
    "count_collected_tests",
    "gather",
    "healthy_stacks",
    "main",
    "pull_requests_in_other_repositories",
    "read_task_status",
    "rows",
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
    """
    listed = _git("ls-tree", "-r", "--name-only", "origin/main")
    if listed is None:
        listed = _git("ls-tree", "-r", "--name-only", "HEAD")
    return None if listed is None else frozenset(listed.splitlines())


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


def healthy_stacks(deployed: Mapping[str, str], applied: Iterable[str]) -> frozenset[str]:
    """The stacks the account holds in a status that means a template was applied.

    The allow-list is imported from `tools/verify_deployed_stacks.py` rather than restated,
    because it was widened once already to stop `ROLLBACK_COMPLETE` reading as a deploy, and a
    second copy of it here is a second place that fix would have had to be made.
    """
    permitted = frozenset(applied)
    return frozenset(name for name, status in deployed.items() if status in permitted)


def _stacks_in_the_account(arguments: argparse.Namespace) -> frozenset[str] | None:
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    try:
        from verify_deployed_stacks import STATUSES_WITH_A_TEMPLATE_APPLIED, list_deployed_stacks
    except ImportError:
        return None
    try:
        deployed = list_deployed_stacks(profile=arguments.profile, region=arguments.region)
    except Exception:  # noqa: BLE001 - no session is an unread row rather than a crash
        return None
    return healthy_stacks(deployed, STATUSES_WITH_A_TEMPLATE_APPLIED)


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


def gather(arguments: argparse.Namespace, *, collector_output: str | None) -> Sources:
    """Every source the board needs, read once each.

    Ninety-six rows over five stages is four hundred and eighty lookups, and performing them
    row by row would be several hundred subprocesses and a rate limit. Every one of these
    returns ``None`` when its source is unreachable, and ``None`` becomes `not read` in the
    cells that needed it rather than an exception that costs the whole board.
    """
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
    return Sources(
        tree=PROJECT_ROOT,
        on_main=_paths_on_default_branch(),
        collected_tests=(
            None if collector_output is None else collected_test_files(collector_output)
        ),
        healthy_stacks=_stacks_in_the_account(arguments),
        buckets=_buckets(),
        environments=None if environments is None else frozenset(json.loads(environments)),
        released=None if released is None else bool(json.loads(released)),
        plan_tasks=plans,
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


def stage_rows(board: Sequence[Slice]) -> list[Row]:
    """The three stage columns as fractions, which is the aggregate worth watching.

    These are the only aggregate figures the table above does not already show, and they come
    free from it. A count of merged pull requests moves every hour and says nothing about
    whether the platform does more than it did; `deployed 41 of 63` moves rarely and says
    exactly that.
    """
    counted = []
    for stage in ("built", "deployed", "proven"):
        reached, applicable = count_reached(board, stage)
        spoken = sum(
            1
            for group in board
            for surface in group.surfaces
            if surface.cells[stage].moved and not surface.cells[stage].derived
        )
        counted.append(
            Row(
                label=f"Surfaces {stage}",
                value=f"{reached} of {applicable}",
                command="uv run --frozen python tools/scoreboard.py",
                note=(
                    f"Out of the rows the stage applies to and something could answer. {spoken} "
                    "of the yes side is a person's answer rather than a reading"
                ),
            )
        )
    return counted


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


def render(board: Sequence[Row], *, moment: datetime) -> str:
    """The board as markdown, with the command beside every figure."""
    lines = [
        (
            f"Read at {moment.strftime('%Y-%m-%d %H:%M')} UTC by `tools/scoreboard.py`. Every "
            "figure is computed on the run that prints it."
        ),
        "",
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
    parser.add_argument("--region", default="us-east-2", help="AWS region the stacks are in")
    parser.add_argument("--surfaces", default=str(SURFACES), help="the surface manifest to resolve")
    parser.add_argument("--stages-only", action="store_true", help="the table and nothing else")
    parser.add_argument("--json", action="store_true", help="one JSON document instead of a table")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    moment = datetime.now(tz=UTC)

    collector_output = _run_collector()
    manifest = read_manifest(Path(arguments.surfaces))
    board = resolve_manifest(manifest, gather(arguments, collector_output=collector_output))
    counted = (
        []
        if arguments.stages_only
        else rows(
            arguments,
            now=moment,
            collected=None if collector_output is None else count_collected_tests(collector_output),
        )
        + stage_rows(board)
    )

    if arguments.json:
        print(json.dumps(_document(board, counted, manifest, moment), indent=2))
        return 0
    print(render_stage_table(board, checked=str(manifest["checked"])))
    if counted:
        print()
        print(render(counted, moment=moment))
    return 0


def _document(
    board: Sequence[Slice],
    counted: Sequence[Row],
    manifest: Mapping[str, Any],
    moment: datetime,
) -> dict[str, Any]:
    return {
        "read_at": moment.isoformat(),
        "manually_checked": str(manifest["checked"]),
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
