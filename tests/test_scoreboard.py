"""The board that replaced a hand-maintained table, and the four readings it must get right.

The status document this board sits on top of doubled in one evening because ten agents each
appended a number and nobody removed one. The fix is that the numbers are computed rather than
written, so what these tests hold is the two properties that makes true. A figure the tool could not compute
must print as unread rather than as zero, and a plan's own note about a task must be read from
the one place a reader looks rather than found anywhere in the note.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from edullm_platform.stages import (
    STAGES,
    Cell,
    Mark,
    Slice,
    Sources,
    Surface,
    read_manifest,
    resolve,
)
from tools import scoreboard
from tools.scoreboard import (
    SURFACES,
    AccountStacks,
    Row,
    TaskStatus,
    build_parser,
    census_of_plan_tasks,
    count_collected_tests,
    fraction,
    pull_requests_in_other_repositories,
    read_task_status,
    render,
    render_slice_rollup,
    stacks_in_the_account,
    stacks_mid_flight,
    status_of_every_task,
)

MOMENT = datetime(2026, 8, 6, 1, 30, tzinfo=UTC)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _sources(found: AccountStacks) -> Sources:
    return Sources(
        tree=PROJECT_ROOT, healthy_stacks=found.applied, stacks_mid_flight=found.mid_flight
    )


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


def _slice(name: str, *cells: Cell) -> Slice:
    return Slice(
        name=name,
        surfaces=[
            Surface(f"{name}-{index}", f"{name} {index}", dict.fromkeys(STAGES, cell))
            for index, cell in enumerate(cells)
        ],
    )


def test_a_slice_fraction_says_when_part_of_its_yes_side_is_somebodys_answer() -> None:
    """Mutation: roll ninety-six rows up into nine and print bare fractions.

    Collapsing the board is what makes it readable and it is also what hides the one distinction
    the board exists for. A slice reading `10 of 15` where four of those ten are a person's
    recollection is not the same claim as one where all ten were looked up, and a reader with
    only the fraction cannot tell.
    """
    read = _slice("Measured", Cell(Mark.REACHED), Cell(Mark.NOT_REACHED))
    recalled = _slice("Recalled", Cell(Mark.REACHED, derived=False), Cell(Mark.NOT_REACHED))

    assert fraction(read, "built") == "1 of 2"
    assert fraction(recalled, "built") == "1 of 2*"


def test_a_row_nobody_could_read_is_named_and_stays_in_the_denominator() -> None:
    """Mutation: leave the unread rows out of the denominator, which this used to assert.

    The name of this test was already right and the assertion under it was not: it asserted
    `1 of 1 (2 unread)`, which is the row being dropped from the denominator and named on the
    way out. Without a session most `deployed` lookups return unread, and a fraction over only
    the rows that happened to answer reads small and confident rather than largely unasked.
    The whole is what the stage applies to; the unread count is how much of that whole this
    run could not reach.
    """
    partly = _slice("Blind", Cell(Mark.REACHED), Cell(Mark.NOT_READ), Cell(Mark.NOT_READ))

    assert fraction(partly, "deployed") == "1 of 3 (2 unread)"


def test_a_stage_that_applies_to_nothing_in_a_slice_reads_as_not_applying() -> None:
    """Mutation: print `0 of 0` for a slice with nothing to deploy.

    The probe deploys nothing, because it is a dispatch and a verdict. `0 of 0` reads as failure
    at a glance and would have the owner asking about work that does not exist.
    """
    nothing = _slice("Probe", Cell(Mark.NOT_APPLICABLE), Cell(Mark.NOT_APPLICABLE))

    assert fraction(nothing, "deployed") == "n/a"


def test_the_total_keeps_its_qualifier_outside_the_bold() -> None:
    """Mutation: bold the whole cell.

    `**30 of 55***` is three closing asterisks, and markdown renders it as bold with the mark
    eaten. The one cell where losing that mark matters most is the total, because it is the
    figure anybody quotes.
    """
    board = [_slice("A", Cell(Mark.REACHED, derived=False)), _slice("B", Cell(Mark.NOT_REACHED))]

    printed = render_slice_rollup(board, checked="2026-08-05", moment=MOMENT)

    assert "| **Total** | **1 of 2**\\* | **1 of 2**\\* | **1 of 2**\\* |" in printed
    assert "| A | 1 of 1* | 1 of 1* | 1 of 1* |" in printed
    assert "***" not in printed


# ----------------------------------------------------------------------------------------
# The gathering, which is where the board went non-deterministic
#
# The resolution is a pure function over what was gathered and it was never the problem. Each
# of these stands one source up in a state it reaches in the field -- refused, capped, timed
# out, or pointed somewhere the stacks are not -- and holds the board to answering the same
# way twice and to saying which state it is in.
# ----------------------------------------------------------------------------------------


def _arguments(**overrides: object) -> argparse.Namespace:
    settings: dict[str, object] = {
        "repo": "edu-llm/platform",
        "author": None,
        "plans_dir": None,
        "plans_glob": "2026-08-0[45]-*.md",
        "profile": None,
        "region": "us-east-1",
    }
    settings.update(overrides)
    return argparse.Namespace(**settings)


def _answer_the_account_with(
    monkeypatch: pytest.MonkeyPatch, answer: Callable[..., dict[str, str]]
) -> None:
    """Stand in for the listing `tools/verify_deployed_stacks.py` performs.

    Patched on that module rather than on this one because the board imports the function
    inside the call, which is what keeps the board importable in a checkout with no AWS CLI.
    """
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    import verify_deployed_stacks

    monkeypatch.setattr(verify_deployed_stacks, "list_deployed_stacks", answer)


def test_a_source_that_raises_is_an_unread_row_and_not_an_absent_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: let a refused listing return an empty mapping instead of no mapping.

    A throttle, an expired session and a missing grant all arrive here as an exception, and
    an empty mapping is a perfectly good answer meaning the account holds none of our stacks.
    Collapsing the two turns "nobody could ask" into "fourteen stacks are gone", which reads
    as the worst morning the platform has ever had and is caused by a rate limit.
    """

    def throttled(**_: object) -> dict[str, str]:
        raise RuntimeError("Throttling: Rate exceeded")

    _answer_the_account_with(monkeypatch, throttled)

    found = stacks_in_the_account(_arguments())

    assert found.applied is None
    assert found.mid_flight == frozenset()
    assert "Rate exceeded" in found.why
    assert resolve({"stack": "sbsandbox-intern-edullm-janitor"}, _sources(found)).mark is (
        Mark.NOT_READ
    )


def test_a_source_that_times_out_is_an_unread_row_and_not_an_absent_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: catch only the errors the stack reader raises on purpose.

    The reader turns a `TimeoutExpired` into its own finding, but the board's guard has to
    hold whatever comes out of a subprocess that hung, including the ones nobody predicted.
    A timeout escaping here takes the whole board down rather than one column, on exactly the
    run where somebody is trying to find out what is going on.
    """

    def hung(**_: object) -> dict[str, str]:
        raise subprocess.TimeoutExpired(cmd=["aws", "cloudformation", "list-stacks"], timeout=120)

    _answer_the_account_with(monkeypatch, hung)

    found = stacks_in_the_account(_arguments())

    assert found.applied is None
    assert "was not read" in found.why


def test_a_region_holding_none_of_these_stacks_is_refused_rather_than_reported_as_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: believe an empty listing, which is a reading and therefore looks trustworthy.

    This is the 30 of 55 that reached the status document on 2026-08-06. The session was
    valid, the call succeeded, and it succeeded against a region these stacks were never
    deployed into, so every stack row went to a confident `no`, the denominator stayed at 55
    and nothing was marked unread. It was the most measured-looking reading of the four taken
    that night and the only one that was nonsense. An empty answer to a question naming
    fourteen stacks is a question asked in the wrong place.
    """
    _answer_the_account_with(monkeypatch, lambda **_: {})

    found = stacks_in_the_account(_arguments(region="us-east-2"))

    assert found.applied is None
    assert "us-east-2" in found.why
    assert "never deployed into" in found.why


def test_a_default_branch_that_cannot_be_read_is_not_answered_off_the_working_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: fall back to `git ls-tree HEAD` when `origin/main` does not answer.

    It did, and that answers a different question without saying so. Fifteen `deployed` rows
    ask whether a file is on the default branch, which is the whole of what "live" means for a
    workflow, a skill or an issue template. Answered off the working tree they read the same
    tree the `built` column reads, so every one of them reaches deployed the moment somebody
    writes the file, and the state a row sits in while its pull request is open disappears.
    The fallback fires in a checkout with no remote, which is where a reader is least likely
    to suspect the board of answering a question they did not ask.
    """
    asked: list[tuple[str, ...]] = []

    def only_head(*arguments: str) -> str | None:
        asked.append(arguments)
        return "AGENTS.md\n" if arguments[-1] == "HEAD" else None

    monkeypatch.setattr(scoreboard, "_git", only_head)

    assert scoreboard._paths_on_default_branch() is None
    assert all(arguments[-1] != "HEAD" for arguments in asked), "the working tree was consulted"


def test_a_stack_part_way_through_an_operation_is_told_apart_from_one_that_is_applied() -> None:
    """Mutation: sort every status into applied or absent and keep no third pile.

    Two of the three piles are stable and the third is not. A stack in `UPDATE_IN_PROGRESS`
    will be something else before the reader finishes the table, so putting it in either
    stable pile is what makes two readings a minute apart disagree.
    """
    account = {
        "a": "CREATE_COMPLETE",
        "b": "UPDATE_IN_PROGRESS",
        "c": "ROLLBACK_COMPLETE",
        "d": "REVIEW_IN_PROGRESS",
        "e": "UPDATE_COMPLETE_CLEANUP_IN_PROGRESS",
    }

    assert stacks_mid_flight(account) == frozenset({"b", "e"})


def test_the_board_looks_for_the_stacks_where_the_checker_that_owns_them_says_they_are() -> None:
    """Mutation: give the board its own region default.

    It had one. `tools/scoreboard.py` said `us-east-2` and `tools/verify_deployed_stacks.py`
    said `us-east-1`, and the disagreement was invisible for as long as everybody passed
    `--region`. The first run that did not read an empty region and reported thirteen live
    deploys as undeployed. Two constants that have to agree and are written twice will stop
    agreeing, and nothing about the output said which region had been asked.
    """
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    from verify_deployed_stacks import DEFAULT_REGION, STACKS

    assert build_parser().parse_args([]).region == DEFAULT_REGION
    assert STACKS, "the region default is only meaningful against a declared stack list"


def test_a_run_that_could_not_read_a_source_says_so_above_the_table_and_names_it() -> None:
    """Mutation: print the fractions and leave what did not answer to the reader.

    Four readings of this board went into the status document inside two hours on 2026-08-06,
    ranging from 30 to 43 out of denominators of 55 and 53, and not one of them carried a word
    about what its run had been able to reach. A reader holding two of them cannot tell a
    deploy from a rate limit, and the reasonable thing to conclude from a board that disagrees
    with itself is that the board is not worth reading.
    """
    board = [_slice("A", Cell(Mark.NOT_READ), Cell(Mark.REACHED))]

    silent = render_slice_rollup(board, checked="2026-08-05", moment=MOMENT)
    loud = render_slice_rollup(
        board,
        checked="2026-08-05",
        moment=MOMENT,
        blind=["CloudFormation: us-east-1 was not read (Throttling)"],
        region="us-east-1",
    )

    assert "could not read" not in silent
    assert "could not read 1 of its sources" in loud
    assert "Throttling" in loud
    assert "against us-east-1" in loud
    assert loud.index("could not read") < loud.index("| Slice |")


def test_the_built_column_rests_on_the_working_tree_and_on_no_lookup_that_can_fail() -> None:
    """Mutation: give a `built` row a rule that needs a network, or a fallback to fall to.

    `Built` is the column with the fewest excuses for being wrong, and the claim made for it
    is that every cell is derived. Verified here rather than trusted: sixty-three of the
    ninety-six read the tree, thirty-three are somebody's answer that a thing does not exist
    or does not apply, and none of them reaches a network or declares an `or:`. That last part
    is what matters, because the silent downgrade to opinion that made `deployed` untrustworthy
    can only happen to a cell whose lookup can fail. None of these can.

    What this does not claim is that `Built` is all measurement. Thirty-three of the cells are
    a person's answer, and nineteen of those are a hand-written `no` sitting in the
    denominator that nobody re-reads. They cannot move without somebody editing the manifest,
    which is a different failure from the one this test holds and is not a quiet one.

    THE COUNT FELL BY FIVE ON 2026-08-06 AND FOUR OF THE FIVE WERE A HAND-WRITTEN `no` OVER A
    BUILT THING. Three rows in the unowned slice said `deferred` or `nothing computes it`
    about the exploration route, the run-ended post and the median runtime, all three of which
    were in the tree with tests. The fourth was a withdrawn row nobody could move. Only the
    fifth, the generated profile table, was a `no` that somebody cleared by building the thing.
    A hand-written cell is the one kind this file cannot re-read for itself, so the number is
    held here to make somebody look each time it changes.

    IT FELL BY ONE MORE THE SAME MORNING, AND THAT ONE IS THE ARITHMETIC WORTH READING BECAUSE
    IT WENT BOTH WAYS. Three hand-written `no`s came off rows that were built, tested and, in
    two cases, running on a schedule: the collector is `tools/read_substrate.py`, the day's
    activity is `activity.py` with `tools/report_activity.py`, and the first outside codebase
    is registered in `config/repositories.yaml` on `main`. One `n/a` replaced a fourth, on the
    onboarding waves, where `built` and `proven` carried the same sentence and three of the
    four stages already said the row was a rollout. Against those, one hand-written `no`
    arrived: the morning message, which had been reading `yes` off `notifications/messages.py`
    and `tests/test_notification_messages.py`. Those are the run-ended post, which is a
    different surface with its own row, so a built thing was counted twice and an unbuilt one
    reported as built and proven. A person's `no` is the honest cell there, because
    `test_every_path_the_manifest_names_is_a_path_that_exists` will not let the manifest name
    the two files that are owed until somebody writes them.

    IT ROSE BY ONE LATER THE SAME DAY AND THAT IS THE COUNT MOVING IN THE RIGHT DIRECTION.
    `test_no_cell_may_be_implied_by_another_rows_cell_unless_the_manifest_says_why` was written
    to catch what `morning-message` had been doing, and the first thing it caught was
    `verb-reconciliation`, whose `built` was `exists: cli/main.py` -- character for character
    `cli-binary`'s cell -- one line below a `planned` reading `settled and not built`. Nothing in
    the tree reconciles a verb list. Its `built` is a person's `no` now, for the same reason the
    morning message's is: the manifest may not name the file that would prove it, because that
    file has not been written. A number rising because a lookup stopped answering for a surface
    it was never about is a better board than one where it stayed put.
    """
    manifest = read_manifest(SURFACES)
    surfaces = [surface for group in manifest["slices"] for surface in group["surfaces"]]
    needs_a_source_that_can_fail = {"on_main", "stack", "bucket", "environment", "release", "task"}

    reaching = {
        surface["id"]: rule
        for surface in surfaces
        for rule in [next(key for key in surface["built"] if key != "or")]
        if rule in needs_a_source_that_can_fail
    }
    falling_back = [surface["id"] for surface in surfaces if "or" in surface["built"]]
    spoken = [
        surface["id"]
        for surface in surfaces
        if next(key for key in surface["built"] if key != "or")
        in {"reached", "not_reached", "not_applicable", "unknown"}
    ]

    assert reaching == {}, "a built cell that needs a network can be downgraded under load"
    assert falling_back == [], "a built cell with an `or:` can be answered from memory"
    assert not [
        surface["id"]
        for surface in surfaces
        if next(key for key in surface["built"] if key != "or") == "reached"
    ], "no built cell may be a person's yes, which is the claim the `*` on the column makes"
    assert len(spoken) == 33, "the count of hand-written built cells moved; re-read them"


def test_the_detail_view_is_behind_a_flag_and_the_summary_is_the_default() -> None:
    """Mutation: print the per-surface table by default.

    Ninety-six rows is a detail view. It answers which surface is undeployed and it cannot
    answer where the platform is, because nothing on it is a total. The document at the top of
    this carries the summary and names the flag for the other one.
    """
    default = build_parser().parse_args([])
    detailed = build_parser().parse_args(["--detail"])

    assert default.detail is False
    assert detailed.detail is True


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
