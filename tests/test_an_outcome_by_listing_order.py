"""What a fan-out's outcome is, and why it stopped depending on what S3 listed first.

**THE ORDINAL WAS NEVER A CELL INDEX AND NOTHING WAS DEFAULTING IT.**
``lifecycle_projection._last_attempt`` enumerates ``detail["attempts"]``, which is Batch's
retry list for **one scheduler job**, so ``attempt_ordinal`` is scoped to
``(run_id, scheduler_job_id)`` and starts again at 1 in every cell of an array. Read from
the lineage store on 2026-08-06: 523 attempt records, 514 at ordinal 1 and 9 at ordinal 2,
falling into 523 distinct ``(run_id, scheduler_job_id)`` groups with **no** ordinal repeated
inside any one of them. The nine are single-container jobs Batch retried once. Every array
run's cells are separate scheduler jobs named ``<parent>:<index>``, which is where a cell's
index actually lives. So the records are right and no repair of them is owed.

``substrate._state`` grouped by ``run_id`` alone and then took
``max(attempts, key=ordinal)``. Over one cell that key totally orders the retries and the
answer is correct; over forty-eight cells it ties at 1 and ``max`` returns whichever record
came first, so the run's outcome was decided by listing order. Seven runs in the store are
tied that way, two of them split exactly 24 succeeded and 24 failed.

**THE COUNTS ARE THE OUTCOME, AND THAT VOCABULARY WAS ALREADY HERE.**
``notifications.messages.render_run_ended`` puts ``_cell_clause`` -- ``all 20 cells
succeeded``, ``19 of 20 cells succeeded, 1 failed`` -- in the slot a single-cell run puts
its outcome word in, and says no single word about a fan-out at all. This module follows
it: the counts ride on the record, the same sentence renders them, and the one word
``state`` still has to carry says ``partly_succeeded`` when some cells succeeded and some
did not, which sends a reader to the counts rather than claiming the sweep worked or that
it did not.

Nothing here reaches AWS.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime
from itertools import permutations
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from find_runs_that_saved_nothing import _load_outcomes

from edullm_platform.cells import CELLS_DISAGREE, outcome_of_cells, said_of_cells
from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import IntentRecord
from edullm_platform.contracts.lifecycle import SchedulerAttempt
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.substrate import (
    SUBSTRATE_FORMAT_VERSION,
    as_document,
    from_document,
    normalise,
)

INTENTS = PROJECT_ROOT / "fixtures/evidence/phase-2/lineage/records/intent"

RUN_A = "run_019fa446-8a4e-7094-9e29-d44fffbd2491"
RUN_B = "run_019fa468-c9b5-706a-8849-87c1d0b5befb"

COLLECTED = datetime(2026, 8, 4, 5, 0, tzinfo=UTC)

#: The parent job id an array's children hang off, spelled the way Batch spells it.
PARENT_JOB = "9ad04a1e-0e2f-4a4e-8d3f-2c9f0b7f5f10"


def _intent(run_id: str, **overrides: Any) -> IntentRecord:
    loaded: Any = json.loads((INTENTS / f"{run_id}.json").read_text(encoding="utf-8"))
    if isinstance(loaded, str):  # the fixture is a JSON string holding JSON
        loaded = json.loads(loaded)
    loaded.update(overrides)
    return IntentRecord.model_validate(loaded)


def _profiles() -> tuple[Any, ...]:
    catalog = load_yaml(PROJECT_ROOT / "config/workload-catalog.yaml", WorkloadCatalog)
    return tuple(catalog.compute_profiles)


def _cell(
    run_id: str,
    *,
    index: int,
    state: str,
    ordinal: int = 1,
    hour: int = 9,
    job: str | None = None,
) -> SchedulerAttempt:
    """One cell's attempt record, in the shape the projection writes for an array child.

    ``scheduler_job_id`` defaults to ``<parent>:<index>``, which is the only place a cell's
    index exists, and ``attempt_ordinal`` defaults to 1, which is what a cell that ran once
    genuinely carries.
    """
    return SchedulerAttempt.model_validate(
        {
            "schema_version": 1,
            "attempt_id": f"att_019fa974-10b2-74b7-86dd-0c93bc5c{index:02x}{ordinal:02x}",
            "run_id": run_id,
            "attempt_ordinal": ordinal,
            "scheduler_job_id": job if job is not None else f"{PARENT_JOB}:{index}",
            "started_at": datetime(2026, 8, 4, hour, tzinfo=UTC).isoformat(),
            "ended_at": datetime(2026, 8, 4, hour + 1, tzinfo=UTC).isoformat(),
            "terminal_state": state,
        }
    )


def _facts(run_id: str, attempts: tuple[SchedulerAttempt, ...]) -> Any:
    return normalise(
        collected_at=COLLECTED,
        intents=(_intent(run_id),),
        attempts=attempts,
        compute_profiles=_profiles(),
    ).runs[run_id]


def _split(run_id: str, *, succeeded: int, failed: int) -> tuple[SchedulerAttempt, ...]:
    """A fan-out of ``succeeded + failed`` cells, successes first."""
    return tuple(
        _cell(run_id, index=index, state="succeeded" if index < succeeded else "failed")
        for index in range(succeeded + failed)
    )


# ---------------------------------------------------------------------------------------
# The tie
# ---------------------------------------------------------------------------------------


def test_an_evenly_split_fan_out_is_labelled_the_same_whichever_cell_is_listed_first() -> None:
    """Mutation: pick the outcome with ``max(attempts, key=lambda a: a.ordinal)``.

    THIS IS THE DEFECT, AND IT IS THE ONE MUTATION THAT MATTERS. Both runs below are
    forty-eight cells split exactly twenty-four succeeded and twenty-four failed. Every
    record carries ordinal 1, because that is what a cell that ran once carries, so a
    ``max`` over that key is a tie and Python's ``max`` returns the first maximal element.
    The two runs differ in one respect only -- which cell the store happens to hand over
    first -- and under the mutation they get opposite labels. Two runs in the lineage store
    are exactly this shape.
    """
    successes_first = _split(RUN_A, succeeded=24, failed=24)
    failures_first = tuple(reversed(_split(RUN_B, succeeded=24, failed=24)))
    for one in failures_first:
        assert one.run_id == RUN_B

    forward = _facts(RUN_A, successes_first)
    backward = _facts(RUN_B, tuple(_cell(RUN_B, index=i, state=one.terminal_state)
                                   for i, one in enumerate(failures_first)))

    assert forward.state == backward.state, (
        "an evenly split fan-out was labelled by whichever cell was listed first"
    )
    assert forward.state == CELLS_DISAGREE
    assert (forward.cells_total, forward.cells_succeeded, forward.cells_failed) == (48, 24, 24)
    assert (backward.cells_total, backward.cells_succeeded, backward.cells_failed) == (48, 24, 24)


def test_no_ordering_of_a_fan_outs_cells_changes_what_it_is_called() -> None:
    """Mutation: any tiebreak that reads position -- first, last, or the store's order.

    Asked of the aggregation rather than of the substrate, because the substrate now sorts
    before it reads and that sort is half of the fix. This is the half underneath it: hand
    the same five cells over in all 120 orders and the answer must not move. The even split
    is the sharpest case and not the only one -- a sweep of five that lost one reads as a
    clean success in four of the five rotations ``max`` could see.
    """
    cells = [(f"{PARENT_JOB}:{index}", 1, "succeeded" if index < 4 else "failed")
             for index in range(5)]

    labelled = {
        (outcome.state, outcome.total, outcome.succeeded, outcome.failed)
        for ordering in permutations(cells)
        if (outcome := outcome_of_cells(ordering)) is not None
    }

    assert labelled == {(CELLS_DISAGREE, 5, 4, 1)}
    assert len(list(permutations(cells))) == 120


# ---------------------------------------------------------------------------------------
# What the label means
# ---------------------------------------------------------------------------------------


def test_a_fan_out_that_lost_one_cell_is_not_called_the_same_thing_as_one_that_lost_them_all()\
        -> None:
    """Mutation: return "failed" whenever any cell failed.

    A fan-out where forty-seven of forty-eight cells worked and one did not is not the
    event a fan-out where forty-seven failed is, and a researcher acts differently on each.
    Collapsing both into one word throws away the only thing that separates them.
    """
    nearly_all = _facts(RUN_A, _split(RUN_A, succeeded=47, failed=1))
    nearly_none = _facts(RUN_B, _split(RUN_B, succeeded=1, failed=47))

    assert nearly_all.state == nearly_none.state == CELLS_DISAGREE
    assert (nearly_all.cells_succeeded, nearly_all.cells_failed) == (47, 1)
    assert (nearly_none.cells_succeeded, nearly_none.cells_failed) == (1, 47)
    assert nearly_all.cells_said != nearly_none.cells_said


def test_a_fan_out_every_cell_of_which_worked_is_a_run_that_succeeded() -> None:
    """Mutation: report ``partly_succeeded`` for anything with more than one cell.

    Twelve of the nineteen fan-outs in the store are unanimous, and a word that sent
    every one of them to a page of counts would make the new state mean "this is an array"
    rather than "these cells disagree".
    """
    facts = _facts(RUN_A, _split(RUN_A, succeeded=24, failed=0))

    assert facts.state == "succeeded"
    assert facts.cells_said == "all 24 cells succeeded"


def test_a_fan_out_that_lost_every_cell_is_a_run_that_failed() -> None:
    """Mutation: report ``partly_succeeded`` whenever the cells are not all successes.

    Nothing succeeded, so there is no part of this that succeeded, and a reader owed the
    plain word must get it.
    """
    facts = _facts(RUN_A, _split(RUN_A, succeeded=0, failed=24))

    assert facts.state == "failed"
    assert facts.cells_said == "0 of 24 cells succeeded, 24 failed"


# ---------------------------------------------------------------------------------------
# The path that must not move
# ---------------------------------------------------------------------------------------


def test_a_single_cell_run_reports_what_it_reported_before() -> None:
    """Mutation: give every run the fan-out treatment.

    Most runs are one cell, that path was already correct, and a change to it reaches every
    researcher on their first submission. One cell means the aggregate is that cell, and
    ``cells_total`` of one says so without a clause anybody has to read past.
    """
    for state in ("succeeded", "failed", "cancelled"):
        facts = _facts(RUN_A, (_cell(RUN_A, index=0, state=state, job=PARENT_JOB),))
        assert facts.state == state
        assert facts.state_source == "attempt"
        assert facts.cells_total == 1
        assert facts.cells_succeeded == (1 if state == "succeeded" else 0)


def test_a_retried_container_reports_its_last_attempt_rather_than_an_aggregate() -> None:
    """Mutation: treat every attempt record as a cell.

    A RETRY AND A CELL ARE THE TWO THINGS THE ORDINAL CANNOT TELL APART ON ITS OWN, and
    conflating them in the other direction is just as wrong: retries of one container are
    sequential and the last one is the outcome, where cells run beside each other and all
    of them count. ``scheduler_job_id`` is what separates the two, and it is the same for
    both records here.
    """
    attempts = (
        _cell(RUN_A, index=0, state="failed", ordinal=1, hour=9, job=PARENT_JOB),
        _cell(RUN_A, index=0, state="succeeded", ordinal=2, hour=11, job=PARENT_JOB),
    )

    facts = _facts(RUN_A, attempts)

    assert facts.state == "succeeded"
    assert facts.cells_total == 1
    assert facts.cells_failed == 0
    assert len(facts.attempts) == 2, "both records stay on the run; only the label aggregates"


def test_a_retry_inside_one_cell_of_a_fan_out_is_still_one_cell() -> None:
    """Mutation: count attempt records instead of scheduler jobs.

    Counting records would report a three-cell sweep whose middle cell retried as four
    cells, and the denominator a researcher reads is the number they submitted.
    """
    attempts = (
        _cell(RUN_A, index=0, state="succeeded"),
        _cell(RUN_A, index=1, state="failed", ordinal=1, hour=9),
        _cell(RUN_A, index=1, state="succeeded", ordinal=2, hour=11),
        _cell(RUN_A, index=2, state="succeeded"),
    )

    facts = _facts(RUN_A, attempts)

    assert facts.cells_total == 3
    assert facts.state == "succeeded"
    assert facts.cells_said == "all 3 cells succeeded"


# ---------------------------------------------------------------------------------------
# One fact, and every reader of it
# ---------------------------------------------------------------------------------------


def test_the_aggregation_is_one_function_and_the_substrate_calls_it() -> None:
    """Mutation: recompute the aggregate inside ``_state``.

    Two implementations of one rule is how the substrate and the result records came to
    report 104 against 80 and 103 against 81 for the same store.
    """
    cells = [(f"{PARENT_JOB}:{index}", 1, "succeeded" if index < 5 else "failed")
             for index in range(6)]

    outcome = outcome_of_cells(cells)

    assert outcome is not None
    assert (outcome.state, outcome.total, outcome.succeeded, outcome.failed) == (
        CELLS_DISAGREE,
        6,
        5,
        1,
    )
    facts = _facts(RUN_A, _split(RUN_A, succeeded=5, failed=1))
    assert (facts.state, facts.cells_total, facts.cells_succeeded, facts.cells_failed) == (
        outcome.state,
        outcome.total,
        outcome.succeeded,
        outcome.failed,
    )


def test_a_run_with_no_attempt_record_has_no_cell_counts_rather_than_zeroes() -> None:
    """Mutation: return a zero-cell outcome for a run that never reached an instance.

    ``0 of 0 cells succeeded`` is a sentence about a sweep that ran and lost everything,
    and a queued run has not run. None is the honest answer and the substrate already
    distinguishes never-started from not-read one field over.
    """
    assert outcome_of_cells(()) is None

    queued = normalise(
        collected_at=COLLECTED,
        intents=(_intent(RUN_A),),
        attempts=(),
        compute_profiles=_profiles(),
    ).runs[RUN_A]

    assert queued.state == "submitted"
    assert queued.cells_total is None
    assert queued.cells_succeeded is None
    assert queued.cells_failed is None
    assert queued.cells_said is None


def test_the_clause_is_worded_the_way_the_run_ended_message_words_it() -> None:
    """Mutation: reword either spelling.

    ``notifications.messages._cell_clause`` is the sentence the eval group already reads
    every night, and it lives in a module packaged into a Lambda whose import closure is
    measured -- so it is restated here rather than imported, on the same argument
    ``notifications.facts.CANCELLATION_MARKERS`` is restated, and compared by this test so
    the two cannot drift into two ways of saying one thing.
    """
    from edullm_platform.notifications.facts import RunEndedFacts
    from edullm_platform.notifications.messages import _cell_clause

    for total, succeeded in ((20, 20), (20, 19), (48, 24), (24, 0)):
        theirs = _cell_clause(
            RunEndedFacts(
                run_id=RUN_A,
                outcome="failed",
                person=None,
                team=None,
                experiment=None,
                queue_name=None,
                compute_profile=None,
                hourly_rate_usd=None,
                seconds_spent=0,
                spent_usd=None,
                authorised_usd=None,
                exit_code=None,
                output_prefix=None,
                cells_total=total,
                cells_failed=total - succeeded,
                cells_succeeded=succeeded,
                cells_measured=None,
                failed_cell_indexes=None,
                checkpoint_state="unknown",
            )
        )
        assert said_of_cells(total=total, succeeded=succeeded) == theirs


# ---------------------------------------------------------------------------------------
# The records already in the store
# ---------------------------------------------------------------------------------------


def test_a_fan_out_whose_cells_disagreed_quotes_no_duration_at_the_next_submitter() -> None:
    """Mutation: fold ``partly_succeeded`` back in with the successes, or with the failures.

    ``run_history`` medians the wall clock of runs of one shape and ``edullm check`` quotes
    it. A fan-out's ``seconds`` is the sum across cells that ran beside each other, so it is
    not a wall clock anybody experienced -- ``notifications.messages.render_run_ended``
    refuses to print one for exactly that reason -- and half of this one's cells did not
    finish anyway. Counting it as a failure of the shape is no better: forty-seven
    containers of forty-eight worked.

    So it contributes to neither, which is what a run with no attempt record already does.
    This falls out of the two branches in ``_durations`` rather than being spelled there,
    and it is pinned here because a third branch added later would change what a submitter
    is quoted without anything else noticing.
    """
    from edullm_platform.run_history import summarise

    mixed = _facts(RUN_A, _split(RUN_A, succeeded=1, failed=1))
    clean = _facts(RUN_B, _split(RUN_B, succeeded=2, failed=0))

    assert mixed.state == CELLS_DISAGREE
    history = summarise((mixed, clean))

    counted = {(cohort.succeeded, cohort.failed) for cohort in history.cohorts}
    assert counted == {(1, 0)}, "only the unanimous run reached a cohort"
    assert history.runs_read == 2
    assert history.runs_with_a_duration == 1


def test_the_daily_page_prints_the_counts_beside_the_word_for_a_fan_out() -> None:
    """Mutation: render ``state`` alone, or render the clause on every row.

    ``partly_succeeded`` in a State column tells a reader that something is odd and nothing
    about what, which is most of the way back to the word this change replaced. The counts
    are what make it an answer. A single-cell run keeps a bare word, because ``all 1 cells
    succeeded`` on every row of a hundred and sixty-five is a clause that stops being read.
    """
    from edullm_platform.activity import day_activity, render_section

    substrate = normalise(
        collected_at=COLLECTED,
        intents=(_intent(RUN_A), _intent(RUN_B)),
        attempts=_split(RUN_A, succeeded=19, failed=1)
        + (_cell(RUN_B, index=0, state="succeeded", job=PARENT_JOB),),
        compute_profiles=_profiles(),
    )
    page = render_section(day_activity(day=date(2026, 8, 4), substrate=substrate))

    assert "partly_succeeded (19 of 20 cells succeeded, 1 failed)" in page
    assert "all 1 cells" not in page


def _store(root: Path, run_id: str, attempts: tuple[SchedulerAttempt, ...], *, ended: str) -> None:
    """One run's lineage records, laid out the way the recorder lays them out.

    One object per attempt under ``attempt/{run_id}/``, and exactly one under
    ``result/{run_id}.json`` however many cells there were -- which is the shape that made
    the result record a per-cell fact under a per-run key.
    """
    directory = root / "attempt" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    for one in attempts:
        (directory / f"{one.attempt_id}.json").write_text(
            one.model_dump_json(), encoding="utf-8"
        )
    (root / "result").mkdir(parents=True, exist_ok=True)
    (root / "result" / f"{run_id}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": run_id,
                "attempt_id": attempts[-1].attempt_id if attempts else None,
                "outcome": ended,
                "output_prefixes": [
                    f"s3://sbsandbox-intern-edullm-outputs/teams/scratch/runs/{run_id}/"
                ],
                "checkpoints": [],
                "wandb_run": None,
                "retention_class": "standard",
                "completed_at": "2026-08-06T00:30:47.365000Z",
            }
        ),
        encoding="utf-8",
    )


def test_the_substrate_and_the_result_reader_agree_about_every_run(tmp_path: Path) -> None:
    """Mutation: read the run's ending off ``result/{run_id}.json`` in either reader.

    NOTHING COMPARED THESE TWO AND THEY WERE ONE APART. Read from the lineage store on
    2026-08-06: the substrate said 104 succeeded against 80 failed, the result records said
    103 against 81, over the same 184 runs. Neither figure was chosen. The substrate's came
    from a ``max`` over a tied ordinal and the result records' from which cell's terminal
    event overwrote the key last, so the pair could have been any of several and happened to
    be these.

    They are two views of one fact, so they must match, and the way they are made to match
    is that neither computes it. Both hand the per-cell attempt records to
    ``cells.outcome_of_cells``. This test is the comparison that was missing: the store below
    is deliberately one where the result record disagrees with the cells, which is exactly
    the shape of the seven runs in the account.
    """
    even = _split(RUN_A, succeeded=24, failed=24)
    _store(tmp_path, RUN_A, even, ended="failed")
    _store(tmp_path, RUN_B, _split(RUN_B, succeeded=1, failed=0), ended="succeeded")

    substrate = normalise(
        collected_at=COLLECTED,
        intents=(_intent(RUN_A), _intent(RUN_B)),
        attempts=even + _split(RUN_B, succeeded=1, failed=0),
        compute_profiles=_profiles(),
    )
    read_back = _load_outcomes(tmp_path)

    assert read_back is not None
    for run_id in (RUN_A, RUN_B):
        facts = substrate.runs[run_id]
        outcome = read_back[run_id]
        assert (outcome.state, outcome.total, outcome.succeeded, outcome.failed) == (
            facts.state,
            facts.cells_total,
            facts.cells_succeeded,
            facts.cells_failed,
        ), f"the two readers disagree about {run_id}"

    assert read_back[RUN_A].state == CELLS_DISAGREE, (
        "the result record for this run says failed, and half its cells did not"
    )
    assert read_back[RUN_B].state == "succeeded"


def test_a_run_batch_never_placed_is_still_answered_by_its_result_record(tmp_path: Path) -> None:
    """Mutation: drop the result records once the attempt records are read.

    ``ResultManifest.attempt_id`` is nullable for the job Batch decides
    ``MISCONFIGURATION:JOB_RESOURCE_REQUIREMENT`` about: it is never placed, so there is no
    attempt to aggregate, and that record is the only account of why it stopped. Reading
    only ``attempt/`` would take those runs out of the report's scope entirely.
    """
    _store(tmp_path, RUN_A, (), ended="failed")

    read_back = _load_outcomes(tmp_path)

    assert read_back is not None
    assert read_back[RUN_A].state == "failed"
    assert read_back[RUN_A].total == 1


def test_a_committed_attempt_record_still_reads_and_still_carries_ordinal_one() -> None:
    """Mutation: change ``SchedulerAttempt`` to make the ordinal mean a cell index.

    THE 514 RECORDS AT ORDINAL 1 ARE CORRECT AND THIS IS WHAT SAYS SO. The contract is
    untouched by this change, every record already written parses exactly as before, and
    the cell's index is read off ``scheduler_job_id`` where Batch actually put it.
    """
    committed = sorted(
        (PROJECT_ROOT / "fixtures/evidence/phase-3/runs").rglob("records/attempt/*.json")
    )
    assert committed, "the phase 3 capture is what pins the record shape"

    for path in committed:
        loaded: Any = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, str):
            loaded = json.loads(loaded)
        record = SchedulerAttempt.model_validate(loaded)
        assert record.attempt_ordinal == 1
        assert ":" not in record.scheduler_job_id, "these three captures are single containers"
        outcome = outcome_of_cells(
            [(record.scheduler_job_id, record.attempt_ordinal, record.terminal_state.value)]
        )
        assert outcome is not None
        assert outcome.state == record.terminal_state.value
        assert outcome.total == 1


def test_the_capture_document_carries_the_counts_and_says_which_format_it_is() -> None:
    """Mutation: add the fields and leave ``SUBSTRATE_FORMAT_VERSION`` alone.

    ``from_document`` reads the new keys unconditionally, so a document written by the
    previous writer has no value for them. A reader that defaulted them would report every
    older fan-out as a single cell, which is the reading this change exists to end.
    """
    assert SUBSTRATE_FORMAT_VERSION == 3

    substrate = normalise(
        collected_at=COLLECTED,
        intents=(_intent(RUN_A),),
        attempts=_split(RUN_A, succeeded=5, failed=1),
        compute_profiles=_profiles(),
    )
    document = json.loads(json.dumps(as_document(substrate)))
    restored = from_document(document).runs[RUN_A]

    assert document["runs"][RUN_A]["cells_total"] == 6
    assert [one["scheduler_job_id"] for one in document["runs"][RUN_A]["attempts"]] == [
        f"{PARENT_JOB}:{index}" for index in range(6)
    ]
    assert restored == substrate.runs[RUN_A]
