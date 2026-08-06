"""What stage each surface of the platform has reached, worked out rather than written down.

WHY THIS IS CODE. A table with a row per surface and a column per stage is the one view that
shows this work moving, and the previous one was maintained by hand. It went stale within a day,
somebody rewrote it from what they could see, and the rewrite lost rows that had not changed
recently. Rows that never change are exactly the rows worth keeping, so losing them is the worst
possible failure. Nothing here is written down except what each surface *is* and where to look
for it, which lives in ``config/reports/surfaces.yaml``. The stage is looked up every time.

THE THING THIS REFUSES TO DO. It never infers a stage. `built` does not imply `deployed`, a
passing test suite does not imply `proven`, and a lookup that could not run prints `not read`
rather than the last thing anybody believed. A person's answer is allowed where no command can
give one, and it is printed with a `*` and a date so a reader can tell the difference at a
glance. The distinction is the whole point: a board that quietly mixes measurement with
recollection is a board that reads well and is wrong.

WHY THE LOOKUPS ARE HANDED IN RATHER THAN PERFORMED HERE. Every source is gathered once by the
caller and passed as a :class:`Sources`, so ninety-six rows cost one `git ls-tree`, one pytest
collection and at most three network calls rather than several hundred subprocesses. It also
makes the resolution a pure function over data, which is what lets the tests state a rule and
an input and assert the mark without a repository, an account or a network.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

__all__ = [
    "STAGES",
    "Cell",
    "Mark",
    "Slice",
    "Sources",
    "Surface",
    "count_reached",
    "read_manifest",
    "render_stage_table",
    "resolve",
    "resolve_manifest",
]

#: In order. A surface moves left to right, and the columns print in this order everywhere so
#: that a reader who has learned the shape does not have to re-read the header.
STAGES: Final = ("designed", "planned", "built", "deployed", "proven")


class Mark(StrEnum):
    """What one cell says.

    ``NOT_READ`` is the one that matters. It means a lookup existed and could not be run, which
    happens whenever this is invoked without an AWS session or without `gh`. Printing it is the
    honest answer and printing a remembered stage in its place is the dishonest one, so the
    fallback a surface may declare is printed as a person's answer rather than as a reading.
    """

    REACHED = "yes"
    NOT_REACHED = "no"
    NOT_APPLICABLE = "n/a"
    UNKNOWN = "?"
    NOT_READ = "not read"


#: The four ways a person, rather than a command, can answer a stage. These exist because two
#: columns cannot be looked up from here at all: `designed` is a judgement about a document that
#: is not in this repository, and several `deployed` cells describe buckets and policies another
#: group owns. Refusing them would leave a third of the board permanently blank, which teaches a
#: reader to ignore the board. Printing them with a `*` keeps them legible as opinion.
_SPOKEN: Final[Mapping[str, Mark]] = {
    "reached": Mark.REACHED,
    "not_reached": Mark.NOT_REACHED,
    "not_applicable": Mark.NOT_APPLICABLE,
    "unknown": Mark.UNKNOWN,
}


@dataclass(frozen=True)
class Cell:
    """One stage of one surface.

    `derived` is false when a person supplied the answer. ``NOT_APPLICABLE`` counts as derived
    because it is a statement about the shape of the thing rather than about its progress, and
    marking it as opinion would put a `*` beside ninety cells that will never move.
    """

    mark: Mark
    note: str = ""
    derived: bool = True

    @property
    def moved(self) -> bool:
        """Whether this counts towards a stage's tally, as opposed to not applying to it."""
        return self.mark is Mark.REACHED

    @property
    def countable(self) -> bool:
        """Whether this belongs in a tally's denominator.

        ONLY ``NOT_APPLICABLE`` LEAVES, AND ``NOT_READ`` USED TO LEAVE WITH IT. A denominator
        counts the rows a stage applies to, and whether a lookup ran tonight is not a fact
        about the row. Letting an unreadable source shrink the denominator meant the fraction
        improved as the instrument learned less: on 2026-08-06 the `deployed` column read
        43 of 55 with an AWS session and 39 of 53 without one, because the two rows whose
        stack lookup has no declared fallback left the denominator rather than landing in the
        unread bucket. A reader comparing the two sees a board that lost four deploys, when
        what it lost was the ability to look.
        """
        return self.mark is not Mark.NOT_APPLICABLE


@dataclass(frozen=True)
class Surface:
    """One row."""

    id: str
    name: str
    cells: Mapping[str, Cell]


@dataclass(frozen=True)
class Slice:
    """One group of rows, with the heading it prints under."""

    name: str
    surfaces: Sequence[Surface]


@dataclass(frozen=True)
class Sources:
    """Everything gathered once, before a single row is resolved.

    Every field is ``None`` when its source could not be reached, and every rule that needs a
    ``None`` field yields ``NOT_READ``. That is why this is a dataclass of optionals rather than
    a set of callables: an unreachable source has to be a value the resolver can see, not an
    exception that aborts a row somewhere in the middle and leaves the board short.
    """

    tree: Path
    on_main: frozenset[str] | None = None
    collected_tests: frozenset[str] | None = None
    healthy_stacks: frozenset[str] | None = None
    buckets: frozenset[str] | None = None
    environments: frozenset[str] | None = None
    released: bool | None = None
    plan_tasks: frozenset[str] | None = None

    #: Stacks CloudFormation is part-way through an operation on. Separate from
    #: :attr:`healthy_stacks` because the account is not yet what any template says and will
    #: be something else in a few minutes, so the only honest reading is that it was not read.
    #: This is the one-row flap: five profiled runs in a minute on 2026-08-06 returned 43 four
    #: times and 42 once, while an agent was applying stacks and one of them was mid-update.
    stacks_mid_flight: frozenset[str] = frozenset()


def _all_paths(value: Any) -> list[str]:
    return [str(value)] if isinstance(value, str) else [str(item) for item in value]


def _from_membership(name: str, held: frozenset[str] | None, *, missing: str) -> Cell:
    if held is None:
        return Cell(Mark.NOT_READ, note=missing)
    return Cell(Mark.REACHED if name in held else Mark.NOT_REACHED)


def _grep(tree: Path, path: str, pattern: str) -> Cell:
    target = tree / path
    if not target.is_file():
        return Cell(Mark.NOT_REACHED, note=f"{path} does not exist")
    found = re.search(pattern, target.read_text(encoding="utf-8"))
    return Cell(Mark.REACHED if found else Mark.NOT_REACHED)


def _absent(tree: Path, directory: str, pattern: str) -> Cell:
    """Reached when the pattern appears nowhere, which is how an absence gets a check.

    Mutation this is written against: treat a missing directory as an absence and return
    reached. A rule asserting that nothing under `infra/` stops a run for cost would then pass
    against an empty checkout, which is the one case where the assertion means nothing.
    """
    root = tree / directory
    if not root.is_dir():
        return Cell(Mark.NOT_READ, note=f"{directory} does not exist")
    compiled = re.compile(pattern)
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file():
            continue
        try:
            body = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if compiled.search(body):
            return Cell(Mark.NOT_REACHED, note=f"{candidate.relative_to(tree)} matches")
    return Cell(Mark.REACHED)


def _tests(declared: Sequence[str], sources: Sources) -> Cell:
    """Reached when every named test file exists and pytest collected it on this run.

    Mutation this is written against: check the files exist and stop. A test file that exists
    and collects nothing, because its imports were left behind by a refactor and it is being
    skipped at collection, would read as proof. Requiring the collector to have seen it is what
    makes this a statement about a check that can fail rather than about a file on disk.
    """
    missing_on_disk = [path for path in declared if not (sources.tree / path).is_file()]
    if missing_on_disk:
        return Cell(Mark.NOT_REACHED, note=f"{missing_on_disk[0]} does not exist")
    if sources.collected_tests is None:
        return Cell(Mark.NOT_READ, note="pytest was not collected")
    uncollected = [path for path in declared if path not in sources.collected_tests]
    if uncollected:
        return Cell(Mark.NOT_REACHED, note=f"{uncollected[0]} collected no tests")
    return Cell(Mark.REACHED)


def _stack(name: str, sources: Sources) -> Cell:
    """Reached when the account holds this stack with a template applied in full.

    Mutation this is written against: read every status that is not one of the three healthy
    ones as the stack being absent. A stack in ``UPDATE_IN_PROGRESS`` is not absent, it is
    being deployed while the board is being read, and answering `no` makes the board disagree
    with itself twice in the same minute for a reason that has nothing to do with the work.
    That is the one-row flap of 2026-08-06. Mid-flight is a state to re-read, so it says so.
    """
    if sources.healthy_stacks is None:
        return Cell(Mark.NOT_READ, note="the account was not read")
    if name in sources.healthy_stacks:
        return Cell(Mark.REACHED)
    if name in sources.stacks_mid_flight:
        return Cell(Mark.NOT_READ, note=f"{name} is mid-flight, so the account is between states")
    return Cell(Mark.NOT_REACHED)


def resolve(stage: Mapping[str, Any], sources: Sources) -> Cell:
    """One cell, from the rule the manifest declares for it.

    A LOOKUP THAT COULD NOT RUN STAYS ``NOT_READ``, AND THE ANSWER UNDER ``or`` IS CARRIED AS
    CONTEXT RATHER THAN SUBSTITUTED FOR IT. It used to be substituted, and that made the board
    answer differently depending on whether a call happened to get through: fifteen `deployed`
    rows declare a fallback and twelve of those fallbacks are a bare yes or no, so a throttled
    or unauthenticated run silently promoted twelve opinions into the tally and reported no
    unread rows where it had read nothing. A `*` in a table cell is not loud enough to carry
    that, and a run that quietly answers from memory under load is the failure this board was
    built to stop rather than one it may commit.

    What the fallback is still good for is telling a reader what somebody last believed, so it
    is printed in the note beside the reason the lookup could not run.
    """
    rule = {key: value for key, value in stage.items() if key != "or"}
    (name, value), *rest = rule.items()
    if rest:
        raise ValueError(f"a stage takes one rule, and this one has {len(rule)}")

    if name in _SPOKEN:
        return Cell(_SPOKEN[name], note=str(value), derived=name == "not_applicable")

    cell = _lookup(name, value, sources)
    if cell.mark is not Mark.NOT_READ or "or" not in stage:
        return cell
    standing = resolve(stage["or"], sources)
    return Cell(
        Mark.NOT_READ,
        note=(
            f"{cell.note}; the manifest records a standing answer of "
            f"'{standing.mark.value}' ({standing.note}), which is not a reading and is not "
            "counted"
        ),
    )


def _lookup(name: str, value: Any, sources: Sources) -> Cell:
    match name:
        case "exists" | "evidence":
            paths = _all_paths(value)
            absent = [path for path in paths if not (sources.tree / path).exists()]
            return Cell(Mark.NOT_REACHED, note=absent[0]) if absent else Cell(Mark.REACHED)
        case "on_main":
            paths = _all_paths(value)
            if sources.on_main is None:
                return Cell(Mark.NOT_READ, note="the default branch was not read")
            absent = [path for path in paths if path not in sources.on_main]
            return Cell(Mark.NOT_REACHED, note=absent[0]) if absent else Cell(Mark.REACHED)
        case "grep":
            return _grep(sources.tree, *value)
        case "absent":
            return _absent(sources.tree, *value)
        case "tests":
            return _tests(_all_paths(value), sources)
        case "stack":
            return _stack(str(value), sources)
        case "bucket":
            return _from_membership(str(value), sources.buckets, missing="S3 was not read")
        case "environment":
            return _from_membership(str(value), sources.environments, missing="GitHub was not read")
        case "release":
            if sources.released is None:
                return Cell(Mark.NOT_READ, note="GitHub was not read")
            return Cell(Mark.REACHED if sources.released else Mark.NOT_REACHED)
        case "task":
            return _from_membership(
                str(value), sources.plan_tasks, missing="no plans directory was given"
            )
    raise ValueError(f"{name} is not a rule this understands")


def read_manifest(path: Path) -> Any:
    """The manifest, through the loader that refuses a duplicate key.

    A surface pasted twice under two ids is a row that reads as two rows, and a stage written
    twice is a rule silently overwritten by the one below it. Both are the copy-and-edit
    mistakes this file invites, so the strict loader is the one to use rather than the plain
    one.
    """
    import yaml

    from edullm_platform.config import SafeUniqueKeyLoader

    return yaml.load(path.read_text(encoding="utf-8"), Loader=SafeUniqueKeyLoader)


def resolve_manifest(manifest: Mapping[str, Any], sources: Sources) -> list[Slice]:
    """Every row of the board, in the order the manifest lists them."""
    return [
        Slice(
            name=str(group["name"]),
            surfaces=[
                Surface(
                    id=str(surface["id"]),
                    name=str(surface["name"]),
                    cells={stage: resolve(surface[stage], sources) for stage in STAGES},
                )
                for surface in group["surfaces"]
            ],
        )
        for group in manifest["slices"]
    ]


def count_reached(board: Sequence[Slice], stage: str) -> tuple[int, int]:
    """How many surfaces have reached a stage, over how many the stage applies to.

    The denominator excludes only the rows the stage does not apply to, so it is a property of
    the manifest and not of tonight's credentials. Two runs against an unchanged account get
    the same denominator whatever either of them managed to read. A row nothing could read is
    in the denominator and out of the numerator, and :func:`tools.scoreboard.fraction` prints
    how many of those there were beside the figure.
    """
    cells = [surface.cells[stage] for group in board for surface in group.surfaces]
    return sum(1 for cell in cells if cell.moved), sum(1 for cell in cells if cell.countable)


def _cell_text(cell: Cell) -> str:
    return f"{cell.mark.value}*" if not cell.derived else cell.mark.value


def render_stage_table(board: Sequence[Slice], *, checked: str) -> str:
    """The board as one markdown table, a row per surface and a group heading per slice."""
    lines = [
        "| Surface | designed | planned | built | deployed | proven |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for group in board:
        lines.append(f"| **{group.name}** | | | | | |")
        for surface in group.surfaces:
            marks = " | ".join(_cell_text(surface.cells[stage]) for stage in STAGES)
            lines.append(f"| {surface.name} | {marks} |")
    lines.append("")
    lines.append(
        f"`*` is a person's answer as of {checked} rather than a reading. `n/a` is a stage that "
        "does not apply to that row. `not read` is a lookup that could not run on this pass. "
        "The reason behind every `no` is in `config/reports/surfaces.yaml` under that surface."
    )
    return "\n".join(lines)
