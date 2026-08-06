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
from collections.abc import Mapping
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


def test_a_lookup_that_could_not_run_is_not_answered_out_of_the_manifests_standing_note(
    tmp_path: Path,
) -> None:
    """Mutation: substitute the `or:` answer for the reading the lookup could not take.

    This is how the board answered until 2026-08-06 and it is the silent downgrade to opinion
    that made it untrustworthy. Fifteen `deployed` rows declare a fallback and nine of those
    say `reached`, so a run that could not reach CloudFormation promoted nine opinions into
    the tally and reported no unread rows at all: it printed 39 of 53 where the run beside it
    that could see printed 43 of 55, and the blind one looked the more complete of the two. A
    `*` in a table cell does not carry that. The standing answer is still worth printing, so
    it travels in the note, where it cannot be counted.
    """
    stage = {"stack": "sbsandbox-intern-edullm-janitor", "or": {"reached": "applied by hand"}}

    unread = resolve(stage, Sources(tree=tmp_path, healthy_stacks=None))
    contradicted = resolve(stage, Sources(tree=tmp_path, healthy_stacks=frozenset({"other"})))

    assert unread.mark is Mark.NOT_READ
    assert not unread.moved
    assert "applied by hand" in unread.note
    assert "not a reading" in unread.note
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


def test_a_tally_skips_the_rows_a_stage_misses_and_keeps_the_rows_nobody_read() -> None:
    """Mutation: drop the unread rows out of the denominator along with the `n/a` ones.

    Forty-one rows are `n/a` at `deployed` because they are contracts and config files, and
    those genuinely are not rows the stage applies to. An unread row is: it is a row nobody
    got an answer for tonight. Letting it leave meant the fraction improved as the instrument
    learned less, which is the wrong way round for every purpose a board has. Measured on
    2026-08-06: with a session the denominator was 55 and without one it was 53, so the two
    figures a reader was invited to compare were fractions of different wholes.
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

    assert count_reached(board, "built") == (1, 3)


def test_a_stack_being_deployed_while_the_board_is_read_is_unread_rather_than_absent(
    tmp_path: Path,
) -> None:
    """Mutation: read every status that is not one of the three healthy ones as absent.

    This is the one-row flap. Five profiled runs in the same minute on 2026-08-06 returned
    43 of 55 four times and 42 once, while an agent was applying stacks and one of them was
    part-way through an update. A stack mid-update is not a stack that is not there, and both
    the yes and the no it gets are true for less than a minute. Saying so is the only answer
    that survives being read twice, and it tells the reader to re-read rather than to go and
    look for a deploy that was undone.

    `REVIEW_IN_PROGRESS` is deliberately outside this. It reads like the others and it does
    not resolve on its own, so calling it unread would hide a real `no` behind a word, and one
    of those sat in this account unreported for five days already.
    """
    applied = frozenset({"sbsandbox-intern-edullm-phase3-batch"})
    in_flight = frozenset({"sbsandbox-intern-edullm-janitor"})
    sources = Sources(tree=tmp_path, healthy_stacks=applied, stacks_mid_flight=in_flight)

    deployed = resolve({"stack": "sbsandbox-intern-edullm-phase3-batch"}, sources)
    mid_flight = resolve({"stack": "sbsandbox-intern-edullm-janitor"}, sources)
    absent = resolve({"stack": "sbsandbox-intern-edullm-notifications"}, sources)

    assert deployed.mark is Mark.REACHED
    assert mid_flight.mark is Mark.NOT_READ
    assert "mid-flight" in mid_flight.note
    assert absent.mark is Mark.NOT_REACHED


def test_the_denominator_of_every_stage_is_the_same_whatever_the_run_could_reach(
    manifest: Any,
) -> None:
    """Mutation: let any source's reachability change how many rows the stage applies to.

    This is the property the whole repair exists for, stated over the real manifest rather
    than over a fixture, because the fixture cannot catch a row added later with a rule whose
    unreadable form leaves the tally. Four readings inside two hours on 2026-08-06 reported
    denominators of 55 and 53 for the same ninety-six rows, and a reader given both had no way
    to know they were not comparable. A denominator is a fact about the manifest.
    """
    blind = Sources(tree=PROJECT_ROOT)
    seeing = Sources(
        tree=PROJECT_ROOT,
        on_main=frozenset({"AGENTS.md"}),
        collected_tests=frozenset({"tests/test_stages.py"}),
        healthy_stacks=frozenset({"sbsandbox-intern-edullm-phase3-batch"}),
        buckets=frozenset({"edullm-landing"}),
        environments=frozenset({"run-approval-lead"}),
        released=True,
        plan_tasks=frozenset({"the-measurement/4"}),
    )
    partial = Sources(tree=PROJECT_ROOT, released=False, buckets=frozenset())

    for stage in STAGES:
        wholes = {
            count_reached(resolve_manifest(manifest, sources), stage)[1]
            for sources in (blind, seeing, partial)
        }
        assert len(wholes) == 1, f"{stage} counted {wholes} rows depending on what answered"


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


#: The stages where a false `yes` misleads about the system rather than about a document or a
#: plan. `designed` is a person's answer in every row by construction, and `planned` is a claim
#: about a plan, where two surfaces built by one task is the ordinary case rather than a defect.
MEASURED_STAGES = ("built", "deployed", "proven")

#: What a cell guarantees, so that one cell can be compared with another. The first element is
#: the kind of claim -- two cells are only comparable when they make the same kind -- the second
#: is everything that has to be present for the cell to be green, and the third is the extra
#: condition beyond presence, which is a pattern for `grep` and `absent` and nothing otherwise.
#: `release: true` gets no entry on purpose: it names nothing, is one global fact about the
#: repository, and is shared by six rows because CLI features go live by being released.
_CLAIM_KINDS = {
    "exists": "in the tree",
    "evidence": "in the tree",
    "grep": "in the tree",
    "on_main": "on the default branch",
    "tests": "collected by pytest",
    "stack": "a stack",
    "bucket": "a bucket",
    "environment": "an environment",
    "absent": "an absence",
}


def _claim(spec: Mapping[str, Any]) -> tuple[str, frozenset[str], str | None] | None:
    """What this cell guarantees, or `None` for a cell that is a person speaking."""
    for rule, kind in _CLAIM_KINDS.items():
        if rule not in spec:
            continue
        value = spec[rule]
        if rule in ("grep", "absent"):
            return (kind, frozenset([str(value[0])]), str(value[1]))
        named = [value] if isinstance(value, str) else list(value)
        return (kind, frozenset(str(item) for item in named), None)
    return None


def _guarantees(
    stronger: tuple[str, frozenset[str], str | None],
    weaker: tuple[str, frozenset[str], str | None],
) -> bool:
    """True when `stronger` being green forces `weaker` green, so `weaker` measures nothing new.

    Three ways one cell can imply another, and all three occur in this manifest. A superset of
    names implies any subset of them, which is how a roll-up implies each of its parts. A `grep`
    implies a bare `exists` on the same file, because a pattern cannot be found in a file that is
    not there -- that is the half `morning-message` hid behind. And a pattern is only implied by
    the identical pattern, which is what lets two rows grep one file for different things.
    """
    kind, names, condition = weaker
    if stronger[0] != kind or not names <= stronger[1]:
        return False
    return True if condition is None else stronger[2] == condition


def _implications(manifest: Any) -> set[tuple[str, str, str]]:
    """Every `(stage, row whose cell is implied, row implying it)` the manifest currently holds."""
    cells = [
        (surface["id"], stage, claim)
        for group in manifest["slices"]
        for surface in group["surfaces"]
        for stage in MEASURED_STAGES
        if (claim := _claim(surface[stage])) is not None
    ]
    return {
        (stage, weak_id, strong_id)
        for weak_id, stage, weak in cells
        for strong_id, other_stage, strong in cells
        if weak_id != strong_id and stage == other_stage and _guarantees(strong, weak)
    }


def _declarations(manifest: Any) -> list[tuple[str, frozenset[str], str]]:
    """The `shared_readings:` block as `(stage, the rows it covers, the reason given)`."""
    return [
        (str(entry["stage"]), frozenset(entry["rows"]), str(entry.get("why", "")))
        for entry in manifest.get("shared_readings", ())
    ]


def test_no_cell_may_be_implied_by_another_rows_cell_unless_the_manifest_says_why(
    manifest: Any,
) -> None:
    """Mutation: point one row's `built` and `proven` at another row's module and test file.

    Not a hypothetical. `morning-message` did exactly that and read `yes` on both, for a surface
    nobody has ever written, because `notifications/messages.py` and
    `test_notification_messages.py` belong to the run-ended feed post -- a different surface with
    its own row. One built thing was counted twice and an unbuilt one was reported as built and
    proven. `test_every_path_the_manifest_names_is_a_path_that_exists`, directly above, cannot
    catch it: every path named was real. It was the wrong row's paths, and nothing looked at that
    until a person read the file.

    THE FIRST VERSION OF THIS TEST ASKED FOR EQUAL READINGS AND THE MUTATION WALKED STRAIGHT
    THROUGH IT. Restoring `morning-message` left it green, because neither cell was identical to
    the run-ended post's -- `exists: messages.py` is weaker than that row's `grep` over the same
    file by a pattern, and `tests: [test_notification_messages.py]` is weaker than its test set by
    a file. Implication is the relation that matters and equality is only its special case. Worth
    knowing before somebody simplifies this back into the version that passes.

    WHY IMPLICATION IS NOT ITSELF A DEFECT, WHICH IS WHY THE ANSWER IS A DECLARATION. Nineteen
    paths here are named by more than one row and almost all of it is honest. `capacity-yaml`
    asserting that `capacity.yaml` exists is implied by `control-plane` asserting that all eight
    config files do, and that is fine, because `capacity-yaml` checks its own file and earns its
    green whether or not the roll-up passes. The test cannot tell that apart from
    `morning-message` by looking at the manifest, and it does not try. It forces somebody to say
    which kind it is. Every declaration is a case where the answer is "the same fact, honestly,
    and here is why"; `morning-message` was the case where the answer would have had to be
    "because I pointed at another surface's files", which nobody writes down.

    WHY THIS TEST IS WORTH ITS WEIGHT -- THE PART THAT GETS RE-DERIVED IN A MONTH OTHERWISE. On
    2026-08-06, fourteen cells on this board were corrected. Twelve were false negatives, rows
    calling built and tested things absent: `collector` said `nothing collects` about nine hundred
    lines with a test file that an audit job had been running on a schedule for days. Two were
    false positives, both on `morning-message`. That ratio reads as reassuring and is the
    opposite. A false negative is self-correcting -- it understates, somebody trips over the work
    existing, and the cost is duplicated effort that gets noticed and complained about. A false
    positive is absorbing: the board says a thing is built, everybody downstream plans on it, and
    nothing ever contradicts it, because the thing that would have contradicted it is the row
    that is lying. Twelve cheap errors and one expensive one is not a good ratio. It is a board
    whose `no` is worth more than its `yes`, which is backwards for an instrument, because the
    green readings are the ones people act on. Every other guard in this file protects the `no`
    side. This is the only one on the `yes` side.

    IT FOUND A SECOND FALSE POSITIVE ON THE FIRST RUN. `verb-reconciliation` had
    `built: {exists: cli/main.py}`, character for character `cli-binary`'s cell, one line under a
    `planned` that said `settled and not built`; and a `proven` implied by two other rows' test
    sets. Nothing in the tree reconciles a verb list. It had read `yes` since #274 wrote this file.
    """
    implications = _implications(manifest)
    declarations = _declarations(manifest)

    undeclared = sorted(
        (stage, weak, strong)
        for stage, weak, strong in implications
        if not any(
            stage == at and {weak, strong} <= rows for at, rows, _ in declarations
        )
    )
    assert not undeclared, (
        "each of these cells is green whenever the other row's is, so it is resting on that "
        "row's artifact rather than measuring its own surface. Either give it a reading of its "
        "own, or add both rows to an entry in `shared_readings:` and say why the two are "
        f"honestly one fact. (stage, implied row, row implying it): {undeclared}"
    )


def test_a_shared_reading_declaration_that_stopped_being_true_is_deleted(manifest: Any) -> None:
    """Mutation: fix an implication and leave its entry in `shared_readings:` behind.

    The block is only worth anything if it is exactly the set of places this board cannot tell
    two rows apart. An entry that outlives what it described is an exemption sitting open over a
    reading nobody makes, and the next row that happens to make it inherits a permission with
    somebody else's reasoning attached -- which is how a guard stops guarding without anybody
    deciding to weaken it. So the block ratchets both ways: the test above refuses an undeclared
    implication, and this one refuses a declaration that has stopped earning its place.

    Every row an entry lists has to be in at least one implication it covers, so widening an
    entry to make a failure go away is itself a failure. The `why` is measured rather than merely
    required, because `why: shared` satisfies a presence check and is what gets written at four
    in the morning. Somebody who has to produce a sentence has to have a reason.
    """
    implications = _implications(manifest)

    for stage, rows, why in _declarations(manifest):
        covered = {
            (weak, strong)
            for at, weak, strong in implications
            if at == stage and {weak, strong} <= rows
        }
        assert covered, (
            f"no cell among {sorted(rows)} implies another at `{stage}` any more, so delete this "
            "entry rather than leaving an exemption open for whoever makes that reading next."
        )
        idle = sorted(rows - {row for pair in covered for row in pair})
        assert not idle, (
            f"{idle} are listed in the `{stage}` entry for {sorted(rows)} but take part in no "
            "implication, so the entry is wider than the thing it excuses."
        )
        assert len(why.split()) >= 12, (
            f"the `{stage}` entry for {sorted(rows)} needs a reason somebody can argue with."
        )


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
