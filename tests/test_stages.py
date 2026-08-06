"""The stage table, and the four ways a board like it goes quietly wrong.

The table this backs was maintained by hand until 2026-08-05, went stale inside a day, and lost
rows when somebody rewrote it from what they could see. Every test here holds one of the
properties that stops that happening again: a lookup that could not run says so rather than
guessing, a person's answer is visibly a person's answer, a stage is never inferred from the
stage before it, and the manifest cannot name a path, a test or a stack that does not exist.

The last of those is the one that earns its keep daily. A row whose `stack:` is misspelt reads
`no` forever and looks like honest bad news, which is the failure mode nobody notices.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.stages import (
    STAGES,
    Cell,
    Mark,
    Slice,
    Sources,
    Surface,
    count_reached,
    read_manifest,
    render_stage_table,
    resolve,
    resolve_manifest,
)
from tools.scoreboard import SURFACES, collected_test_files, healthy_stacks, tasks_in_plans

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: The rules a stage may carry. A manifest naming anything else is a typo that would otherwise
#: raise at render time, halfway down a board somebody is waiting on.
KNOWN_RULES = {
    "absent",
    "bucket",
    "environment",
    "evidence",
    "exists",
    "grep",
    "not_applicable",
    "not_reached",
    "on_main",
    "reached",
    "release",
    "stack",
    "task",
    "tests",
    "unknown",
}


@pytest.fixture(scope="module")
def manifest() -> Any:
    return read_manifest(SURFACES)


@pytest.fixture(scope="module")
def every_stage(manifest: Any) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (surface["id"], stage, surface[stage])
        for group in manifest["slices"]
        for surface in group["surfaces"]
        for stage in STAGES
    ]


def test_a_lookup_that_could_not_run_says_so_rather_than_answering(tmp_path: Path) -> None:
    """Mutation: treat an unreachable source as a no.

    This runs without an AWS session far more often than with one, so the difference between
    "the account does not hold this stack" and "nobody asked the account" is the difference
    between a board that reports work and a board that reports its own blindness as failure.
    Reading `no` off a missing credential would have every deployed cell go red the moment a
    session expired, and the first fix anybody reaches for is to stop believing the board.
    """
    sources = Sources(tree=tmp_path, healthy_stacks=None)

    cell = resolve({"stack": "sbsandbox-intern-edullm-phase3-batch"}, sources)

    assert cell.mark is Mark.NOT_READ
    assert "account" in cell.note


def test_a_fallback_is_used_only_when_the_lookup_cannot_run_and_prints_as_a_persons_answer(
    tmp_path: Path,
) -> None:
    """Mutation: let the declared fallback win, or let it apply when the lookup returned no.

    The fallback exists so a board read from a laptop is legible, not so a stale opinion can
    outrank a reading. When the account answers, the account is the answer, and when it does
    not, what stands in for it has to be visibly somebody's recollection.
    """
    stage = {"stack": "sbsandbox-intern-edullm-janitor", "or": {"reached": "applied by hand"}}

    unread = resolve(stage, Sources(tree=tmp_path, healthy_stacks=None))
    contradicted = resolve(stage, Sources(tree=tmp_path, healthy_stacks=frozenset()))

    assert (unread.mark, unread.derived) == (Mark.REACHED, False)
    assert (contradicted.mark, contradicted.derived) == (Mark.NOT_REACHED, True)


def test_a_persons_answer_is_marked_and_a_reading_is_not() -> None:
    """Mutation: render every cell the same way.

    Two thirds of the `designed` column and a good part of `deployed` can only be somebody's
    answer, and a board that prints those identically to a measured cell is a board claiming
    more than it knows. The star is the whole of the distinction a reader gets.
    """
    board = [
        Slice(
            name="A slice",
            surfaces=[
                Surface(
                    id="one",
                    name="A surface",
                    cells={
                        "designed": Cell(Mark.REACHED, derived=False),
                        "planned": Cell(Mark.NOT_APPLICABLE),
                        "built": Cell(Mark.REACHED),
                        "deployed": Cell(Mark.NOT_READ),
                        "proven": Cell(Mark.NOT_REACHED, derived=False),
                    },
                )
            ],
        )
    ]

    printed = render_stage_table(board, checked="2026-08-05")

    assert "| A surface | yes* | n/a | yes | not read | no* |" in printed
    assert "| **A slice** | | | | | |" in printed
    assert "2026-08-05" in printed


def test_a_test_file_that_exists_but_collects_nothing_is_not_proof(tmp_path: Path) -> None:
    """Mutation: prove a row by the test file being on disk.

    A test module whose imports were broken by a refactor still exists, and pytest reports it as
    a collection error rather than a failure, so a suite can be green while the check a row
    claims is not running at all. Requiring the collector to have produced a test out of the
    file is what makes `proven` a statement about a check that can fail.
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_thing.py").write_text("import nonexistent\n", encoding="utf-8")

    on_disk_only = resolve(
        {"tests": ["tests/test_thing.py"]},
        Sources(tree=tmp_path, collected_tests=frozenset()),
    )
    collected = resolve(
        {"tests": ["tests/test_thing.py"]},
        Sources(tree=tmp_path, collected_tests=frozenset({"tests/test_thing.py"})),
    )

    assert on_disk_only.mark is Mark.NOT_REACHED
    assert collected.mark is Mark.REACHED


def test_an_absence_asserted_against_a_directory_that_is_not_there_is_not_an_absence(
    tmp_path: Path,
) -> None:
    """Mutation: return reached when the directory does not exist.

    One row says nothing anywhere may stop a run for cost, and it is checked by a pattern
    appearing nowhere under `infra/`. Against a tree without `infra/` that check passes while
    asserting nothing, which is the one case where a green cell is worth less than a blank one.
    """
    missing = resolve({"absent": ["infra", "AlarmActions"]}, Sources(tree=tmp_path))

    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "a.yaml").write_text("Resources: {}\n", encoding="utf-8")
    empty = resolve({"absent": ["infra", "AlarmActions"]}, Sources(tree=tmp_path))

    (tmp_path / "infra" / "b.yaml").write_text("AlarmActions: [arn]\n", encoding="utf-8")
    breached = resolve({"absent": ["infra", "AlarmActions"]}, Sources(tree=tmp_path))

    assert missing.mark is Mark.NOT_READ
    assert empty.mark is Mark.REACHED
    assert breached.mark is Mark.NOT_REACHED


def test_a_thing_in_the_tree_and_not_on_the_branch_is_built_and_not_deployed(
    tmp_path: Path,
) -> None:
    """Mutation: answer `deployed` for a workflow off the working tree.

    A workflow, a skill and an issue template all go live by merging, so between writing one and
    merging it there is a real state the board should show. Reading both stages off the same
    working tree collapses that state and the row jumps straight to deployed.
    """
    (tmp_path / "w.yml").write_text("on: workflow_dispatch\n", encoding="utf-8")
    sources = Sources(tree=tmp_path, on_main=frozenset({"other.yml"}))

    assert resolve({"exists": "w.yml"}, sources).mark is Mark.REACHED
    assert resolve({"on_main": "w.yml"}, sources).mark is Mark.NOT_REACHED


def test_a_stage_is_never_inferred_from_the_one_before_it(manifest: Any) -> None:
    """Mutation: fill a missing stage in from its neighbour.

    Every surface declares all five stages, and the resolver reads each independently. Nothing
    anywhere maps built to deployed or a passing suite to proven, and the manifest carrying all
    five for every row is what keeps it that way: there is no absent stage to be helpful about.
    """
    for group in manifest["slices"]:
        for surface in group["surfaces"]:
            assert set(STAGES) <= set(surface), surface["id"]


def test_a_tally_counts_neither_the_rows_a_stage_skips_nor_the_rows_nobody_read() -> None:
    """Mutation: divide by the number of rows.

    Thirty-odd rows are `n/a` at `deployed` because they are contracts and config files, and
    several more read `not read` without a session. Counting those in the denominator makes the
    fraction move when the credential changes rather than when the work does.
    """
    board = [
        Slice(
            name="A slice",
            surfaces=[
                Surface("a", "A", {stage: Cell(Mark.REACHED) for stage in STAGES}),
                Surface("b", "B", {stage: Cell(Mark.NOT_REACHED) for stage in STAGES}),
                Surface("c", "C", {stage: Cell(Mark.NOT_APPLICABLE) for stage in STAGES}),
                Surface("d", "D", {stage: Cell(Mark.NOT_READ) for stage in STAGES}),
            ],
        )
    ]

    assert count_reached(board, "built") == (1, 2)


def test_a_stack_that_rolled_back_is_not_a_deploy() -> None:
    """Mutation: read any status the account returns as a stack that exists.

    A stack sitting in `ROLLBACK_COMPLETE` was created and never applied its template, and
    reading it as deployed is what `tools/verify_deployed_stacks.py` was widened to stop. This
    board imports that allow-list rather than keeping a second one, and this holds the join.
    """
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    from verify_deployed_stacks import STATUSES_WITH_A_TEMPLATE_APPLIED

    found = healthy_stacks(
        {"a": "CREATE_COMPLETE", "b": "ROLLBACK_COMPLETE", "c": "UPDATE_ROLLBACK_COMPLETE"},
        STATUSES_WITH_A_TEMPLATE_APPLIED,
    )

    assert found == frozenset({"a"})


def test_the_collector_output_names_the_files_a_test_came_out_of() -> None:
    """Mutation: take every line that looks like a path.

    `--collect-only -q` prints tests as `path::name` and prints error blocks that name files
    without one. Only the first is evidence a check exists, and counting the second would let a
    module that fails to import prove the row it was written for.
    """
    output = (
        "tests/test_a.py::test_one\n"
        "tests/test_a.py::test_two[case-1]\n"
        "tests/test_b.py::TestThing::test_three\n"
        "ERROR tests/test_broken.py\n"
        "\n3 tests collected in 0.4s\n"
    )

    assert collected_test_files(output) == frozenset({"tests/test_a.py", "tests/test_b.py"})


def test_a_plan_task_is_keyed_by_a_name_rather_than_by_a_path(tmp_path: Path) -> None:
    """Mutation: key the manifest's tasks by the plan's filename or its path.

    The plans live in a private tree this repository does not carry, so the tracked manifest may
    name a plan but must not name where it is. Dropping the date prefix also means a plan can be
    renamed by date without silently unplanning every surface pointing at it.
    """
    (tmp_path / "2026-08-04-the-measurement.md").write_text(
        "### Task 4\n\n> **DONE.** it landed\n\n### Task 9\n\nprose\n", encoding="utf-8"
    )

    assert tasks_in_plans(tmp_path, "*.md") == frozenset({"the-measurement/4", "the-measurement/9"})


def test_every_rule_the_manifest_uses_is_one_the_resolver_knows(
    every_stage: list[tuple[str, str, dict[str, Any]]],
) -> None:
    """Mutation: let an unrecognised rule fall through to a default.

    A misspelt rule key is a cell that either crashes the board halfway down or, worse, quietly
    becomes whatever the default is. Refusing it here means the manifest is checked by CI rather
    than by the person waiting for the table.
    """
    for surface_id, stage, spec in every_stage:
        rule = {key for key in spec if key != "or"}
        assert len(rule) == 1, f"{surface_id} {stage} carries {len(rule)} rules"
        assert rule <= KNOWN_RULES, f"{surface_id} {stage} names {rule}"
        if "or" in spec:
            assert set(spec["or"]) <= {"reached", "not_reached", "not_applicable", "unknown"}


def test_every_path_the_manifest_names_is_a_path_that_exists(
    every_stage: list[tuple[str, str, dict[str, Any]]],
) -> None:
    """Mutation: let the manifest name a file that was renamed or deleted.

    This is the rot the previous table died of, arriving by a different door. A row pointing at
    `src/edullm_platform/aws_identities.py` after that module was folded into `contracts/` reads
    `no` and looks like honest bad news, and nobody re-checks a row that has been red for a
    week. A moved file has to break the build instead.
    """
    for surface_id, stage, spec in every_stage:
        for key in ("exists", "evidence", "on_main"):
            if key not in spec:
                continue
            value = spec[key]
            for path in [value] if isinstance(value, str) else value:
                assert (PROJECT_ROOT / path).exists(), f"{surface_id} {stage} names {path}"
        if "grep" in spec:
            path, _ = spec["grep"]
            assert (PROJECT_ROOT / path).is_file(), f"{surface_id} {stage} names {path}"
        if "tests" in spec:
            for path in spec["tests"]:
                assert (PROJECT_ROOT / path).is_file(), f"{surface_id} {stage} names {path}"


def test_every_stack_the_manifest_names_is_a_stack_this_repository_deploys(
    every_stage: list[tuple[str, str, dict[str, Any]]],
) -> None:
    """Mutation: let the manifest name a stack that does not exist.

    A misspelt stack name reads `no` against any account forever, and it reads it in the column
    the owner is watching for movement. The deployed-stack checker already holds the list of
    stacks this repository owns, so the manifest is held against that rather than against a
    second copy of it.
    """
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    from verify_deployed_stacks import STACKS

    for surface_id, stage, spec in every_stage:
        if "stack" in spec:
            assert spec["stack"] in STACKS, f"{surface_id} {stage} names {spec['stack']}"


def test_the_manifest_resolves_end_to_end_without_a_network(manifest: Any) -> None:
    """Every row produces a cell for every stage against a tree and nothing else.

    Sources left at ``None`` is the shape this runs in on CI and on a laptop without a session,
    and it has to produce a whole board there. A rule that raised instead of yielding `not read`
    would take the table down for everybody the moment a credential lapsed.
    """
    board = resolve_manifest(manifest, Sources(tree=PROJECT_ROOT))

    surfaces = [surface for group in board for surface in group.surfaces]
    assert len(surfaces) == len({surface.id for surface in surfaces})
    assert all(isinstance(surface.cells[stage], Cell) for surface in surfaces for stage in STAGES)
    assert len(surfaces) > 50
