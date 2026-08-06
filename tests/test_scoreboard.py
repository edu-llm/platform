"""The board that replaced a hand-maintained table, and the four readings it must get right.

`docs-frank/reference/status.md` doubled in one evening because ten agents each appended a
number and nobody removed one. The fix is that the numbers are computed rather than written, so
what these tests hold is the two properties that makes true. A figure the tool could not compute
must print as unread rather than as zero, and a plan's own note about a task must be read from
the one place a reader looks rather than found anywhere in the note.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from tools.scoreboard import (
    Row,
    TaskStatus,
    build_parser,
    census_of_plan_tasks,
    count_collected_tests,
    pull_requests_in_other_repositories,
    read_task_status,
    render,
    status_of_every_task,
)

MOMENT = datetime(2026, 8, 6, 1, 30, tzinfo=UTC)


def test_the_two_status_words_the_plans_already_used_are_both_read() -> None:
    """Mutation: recognise only the word this tool would have chosen.

    `BUILT, in #199 on 2026-08-04` and `DONE, 2026-08-04` are both in the plans and predate this
    tool. Recognising one spelling would have meant either a count that started wrong or seven
    plans rewritten to suit a reader, and rewriting a plan to make a counter happy is how a
    document stops describing the work.
    """
    assert read_task_status("> **BUILT, in #199 on 2026-08-04.** `precision.py` is 416 lines.") is (
        TaskStatus.DONE
    )
    assert read_task_status("> **DONE, 2026-08-04, and it weakened the claim.**") is TaskStatus.DONE


def test_a_status_word_is_read_through_the_markup_it_is_wearing() -> None:
    """Mutation: match at the start of the line and give up on anything else.

    The measurement plan opens its superseded task `> ## WARNING SUPERSEDED 2026-08-05`, with a
    heading marker inside the quote and a warning emoji before the word. That is a prose choice
    about how loud the note should be. Treating it as data means the loudest note in the plans is
    the one that does not count.
    """
    assert read_task_status("> ## \u26a0 SUPERSEDED 2026-08-05. DO NOT SEND THIS ASK.") is (
        TaskStatus.SUPERSEDED
    )
    assert read_task_status("> **GATED on an IAM deploy.**") is TaskStatus.BLOCKED


def test_prose_that_mentions_the_work_is_not_a_claim_that_the_work_is_done() -> None:
    """Mutation: search the whole note for a status word.

    The measurement plan's Task 9 note opens `This task spends money and depends on Tasks 1 and
    2` and goes on to describe what having built it would look like. A search over the note calls
    an unstarted task done, and a board that overstates progress is worse than no board, because
    the whole reason this exists is that the document it replaced overstated progress.
    """
    note = "> **This task spends money.** It cannot start until Task 1 is built and merged."

    assert read_task_status(note) is TaskStatus.UNMARKED
    assert read_task_status("") is TaskStatus.UNMARKED


def test_both_task_numbering_styles_and_both_heading_levels_are_counted() -> None:
    """Mutation: match one heading level, or match `Task \\d`.

    The spine plan numbers two independent tracks `S1` to `S5` and `P1` to `P8` in one file, and
    the heading level moved from `##` to `###` between the plans of 2026-08-04 and those of
    2026-08-05. Either assumption drops a whole plan's worth of tasks from the denominator
    without anything going red, which is the shape of bug this repository found nine of.
    """
    plan = (
        "## Task S1: Refuse bfloat16\n\n"
        "> **BUILT, in #199.**\n\n"
        "### Task 12: Delivery\n\n"
        "Ordinary prose, not a note.\n\n"
        "#### Task P3: Repair the fixtures\n\n"
        "> **DONE on 2026-08-05.**\n"
    )

    assert list(status_of_every_task(plan)) == [
        ("S1", TaskStatus.DONE),
        ("12", TaskStatus.UNMARKED),
        ("P3", TaskStatus.DONE),
    ]


def test_a_note_that_is_not_the_first_thing_under_the_heading_does_not_count(
    tmp_path: Path,
) -> None:
    """Mutation: scan forward for the first blockquote anywhere under the heading.

    A plan that opens a task with two screens of context and marks it done below them has not
    marked it anywhere a reader sees, and the next task's note is the one a forward scan finds.
    Stopping at the first non-empty line is what keeps one task's status from being read off
    another task's.
    """
    (tmp_path / "2026-08-05-a-plan.md").write_text(
        "### Task 1: The first\n\n"
        "Context nobody marked.\n\n"
        "> **DONE.** This note belongs to the prose above it, not to the heading.\n\n"
        "### Task 2: The second\n\n"
        "> **DONE.** Marked where a reader looks.\n",
        encoding="utf-8",
    )

    census = census_of_plan_tasks(sorted(tmp_path.glob("2026-08-0[45]-*.md")))

    assert (census.total, census.done, census.unmarked) == (2, 1, 1)


def test_the_census_adds_up_to_the_number_of_tasks(tmp_path: Path) -> None:
    """Mutation: let a task fall into no bucket.

    The four counts are what the board prints and a reader adds them against the total without
    being asked to. A fifth state, or a task counted twice, makes the arithmetic wrong in a way
    that reads as a lost task rather than as a bug in the counter.
    """
    (tmp_path / "2026-08-04-one.md").write_text(
        "## Task 1: a\n\n> **BUILT.**\n\n## Task 2: b\n\n> **SUPERSEDED.**\n",
        encoding="utf-8",
    )
    (tmp_path / "2026-08-05-two.md").write_text(
        "### Task 1: c\n\n> **BLOCKED on them.**\n\n### Task 2: d\n\nnothing\n",
        encoding="utf-8",
    )

    census = census_of_plan_tasks(sorted(tmp_path.glob("2026-08-0[45]-*.md")))

    assert census.total == 4
    assert census.done + census.superseded + census.blocked + census.unmarked == census.total
    assert (census.done, census.superseded, census.blocked, census.unmarked) == (1, 1, 1, 1)


def test_the_suite_size_is_pytests_own_arithmetic_and_not_a_line_count() -> None:
    """Mutation: count the lines the collector printed.

    `--collect-only -q` prints one line per test, then a summary, and a plugin banner or a
    warnings block puts the line count out by an amount nobody can predict. The summary line is
    the collector's own count and is the only figure here that pytest will keep correct.
    """
    output = "tests/test_a.py::test_one\ntests/test_a.py::test_two\n\n5804 tests collected in 1.10s"

    assert count_collected_tests(output) == 5804
    assert count_collected_tests("1 test collected in 0.01s") == 1
    assert count_collected_tests("ERROR: could not import conftest") is None


def test_a_pull_request_in_the_repository_you_can_merge_is_not_waiting_on_anybody() -> None:
    """Mutation: count every open pull request the author has anywhere.

    The row answers "how many things are waiting on somebody who is not you", and the author's
    own pull requests on this repository are waiting on the author. Counting them makes the
    number go up when he does more work, which inverts what the row is for.
    """
    found = pull_requests_in_other_repositories(
        [
            {"repository": {"name": "platform"}},
            {"repository": {"name": "OLMo-core"}},
            {"repository": {"name": "edullm-data"}},
        ],
        this_repository="platform",
    )

    assert found == 2


def test_a_figure_that_could_not_be_computed_prints_as_unread_rather_than_as_zero() -> None:
    """Mutation: render `None` as an empty cell, or as 0.

    This is the whole reason the tool exists. The document this board sits on top of filled with
    numbers that were true when somebody typed them, and an unreachable source rendering as a
    blank or a zero is the same failure with a machine doing the typing. A reader must be able
    to tell "nothing is owed" from "nobody asked".
    """
    board = [
        Row(label="Hand-applied deploy steps owed", value="0", command="a-command"),
        Row(label="Latest release", value=None, command="another-command", note="gh was absent"),
    ]

    printed = render(board, moment=MOMENT)

    assert "| Hand-applied deploy steps owed | 0 | `a-command` |" in printed
    assert "| Latest release | **not read** | `another-command` |" in printed
    assert "gh was absent" in printed


def test_every_row_carries_the_command_that_produced_it() -> None:
    """Mutation: print the label and the figure and leave the command out.

    A number on this board that a reader cannot re-run is a number they have to trust, and the
    document this replaced was two thousand lines of numbers a reader had to trust. The command
    beside the figure is what makes the board checkable by the person reading it rather than by
    the agent that wrote it.
    """
    board = [Row(label="Tests in the suite", value="5,804", command="pytest --collect-only -q")]

    printed = render(board, moment=MOMENT)

    assert "`pytest --collect-only -q`" in printed
    assert "2026-08-06 01:30 UTC" in printed


def test_the_plans_directory_has_no_default_because_it_is_outside_this_repository() -> None:
    """Mutation: default `--plans-dir` to where the plans actually live.

    The plans sit in a private tree that is not committed here, so a default would put a private
    path into tracked code and would make the two plan rows unanswerable in CI besides. Leaving
    it off has to be a supported way to run the tool rather than an error.
    """
    arguments = build_parser().parse_args([])

    assert arguments.plans_dir is None
    assert arguments.repo == "edu-llm/platform"
    assert isinstance(arguments, argparse.Namespace)
