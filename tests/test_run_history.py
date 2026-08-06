"""What runs of a shape have taken, and the three things this must never invent.

Every test here names the mutation it would catch. The subject is a measurement printed
beside a ceiling, and a measurement that is wrong in the reassuring direction is worse than
no measurement, so most of these are about the cases where the honest answer is silence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

import pytest

from edullm_platform.cells import said_of_cells
from edullm_platform.run_history import (
    HISTORY_FILENAME,
    HISTORY_FORMAT_VERSION,
    NO_HISTORY_PACKAGED,
    NOTHING_LIKE_THIS_YET,
    RUNGS,
    RUNS_FOR_A_FIGURE,
    SHAPE_FIELDS,
    RunHistoryFormatError,
    as_document,
    coverage,
    elapsed_said,
    from_document,
    history_for,
    load_run_history,
    shape_of,
    summarise,
)
from edullm_platform.substrate import AttemptFacts, RunFacts

STARTED = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Shape:
    """The four fields a submission and a run both spell the same way."""

    repository: str = "OLMo-core"
    workload_profile: str = "olmo-core-train"
    compute_profile: str = "gpu-4xa10g"
    dataset_release: str = "regmix-10b-v1"


def a_run(
    *,
    seconds: int,
    state: str = "succeeded",
    attempts: int = 1,
    shape: Shape | None = None,
    run_id: str = "run_1",
) -> RunFacts:
    """One normalised run, built through the substrate's own record rather than a stub.

    A stub carrying the same attribute names would pass every test here and would stop
    agreeing with the substrate the first time a field was renamed. This is the type the
    tool actually hands over.
    """
    described = shape or Shape()
    return RunFacts(
        run_id=run_id,
        submitter="caiiris",
        team="memory-split",
        experiment=None,
        repository=described.repository,
        commit_sha="a" * 40,
        image_digest="sha256:" + "0" * 64,
        dataset_release=described.dataset_release,
        workload_profile=described.workload_profile,
        compute_profile=described.compute_profile,
        wandb_project="memory-split",
        fanout_size=None,
        submitted_at=STARTED,
        approving_environment="run-approval-automatic",
        workflow_run_id=None,
        workflow_run_url=None,
        attempts=tuple(
            AttemptFacts(
                attempt_id=f"att_{ordinal}",
                ordinal=ordinal,
                # One scheduler job across all of them, because these are retries of one
                # container rather than cells of a fan-out, and the ordinal only means a
                # retry inside one job. See edullm_platform.cells.
                scheduler_job_id="00000000-0000-0000-0000-00000000000a",
                started_at=STARTED,
                ended_at=STARTED + timedelta(seconds=seconds),
                terminal_state=state,
            )
            for ordinal in range(1, attempts + 1)
        ),
        state=state,
        state_source="attempt",
        cells_total=1,
        cells_succeeded=1 if state == "succeeded" else 0,
        cells_failed=0 if state == "succeeded" else 1,
        cells_said=said_of_cells(total=1, succeeded=1 if state == "succeeded" else 0),
        seconds=Decimal(seconds),
        cost_usd=Decimal("1.00"),
        unpriced_reason=None,
    )


def a_run_that_never_started(*, shape: Shape | None = None) -> RunFacts:
    return RunFacts(
        **{
            **a_run(seconds=0, shape=shape).__dict__,
            "attempts": (),
            "state": "submitted",
            "state_source": "intent",
            "cells_total": None,
            "cells_succeeded": None,
            "cells_failed": None,
            "cells_said": None,
            "seconds": Decimal(0),
            "cost_usd": None,
            "unpriced_reason": "no attempt record: this run never reached an instance",
        }
    )


def three_successes(shape: Shape | None = None) -> tuple[RunFacts, ...]:
    return tuple(
        a_run(seconds=seconds, shape=shape, run_id=f"run_{index}")
        for index, seconds in enumerate((600, 1800, 5400))
    )


# ---------------------------------------------------------------------------------------
# The three refusals
# ---------------------------------------------------------------------------------------


def test_a_shape_nothing_has_run_is_told_so_rather_than_given_a_number() -> None:
    """The first refusal, and the one an average would quietly break.

    Mutation: fall back to a median over every run when no cohort matches. This returns a
    figure, and a submitter reading "runs like yours took forty minutes" would be reading
    the platform's average over unrelated work.
    """
    history = summarise(three_successes(), built_at=STARTED)

    answer = history.answer(shape_of(Shape(workload_profile="something-nobody-has-run")))

    assert answer.cohort is None
    assert answer.said.startswith(NOTHING_LIKE_THIS_YET)
    assert "median" not in answer.said


def test_an_install_carrying_no_reading_says_that_and_not_that_nothing_has_run() -> None:
    """The two silences are different findings and are worded differently.

    One is about the platform and one is about this install, and they send somebody to
    different places: a shape nobody has run is a fact, and a missing reading is a file that
    has not been committed yet.

    Mutation: have ``history_for`` build an empty ``RunHistory`` when handed ``None``. The
    second assertion fails, and every install without a reading starts telling researchers
    that the platform has never run their shape.
    """
    answer = history_for(Shape(), history=None)

    assert answer.cohort is None
    assert answer.said == NO_HISTORY_PACKAGED
    assert answer.said != NOTHING_LIKE_THIS_YET


def test_a_failed_run_is_counted_and_is_not_in_the_duration() -> None:
    """The second refusal. A run that died in four minutes is not evidence about duration.

    Mutation: drop the ``state == "succeeded"`` test in ``_durations`` and take every run.
    The median falls to four minutes, which is the length of a crash, and a submitter would
    be told a full training run takes minutes.
    """
    runs = (
        *three_successes(),
        a_run(seconds=240, state="failed", run_id="run_f1"),
        a_run(seconds=240, state="failed", run_id="run_f2"),
    )
    history = summarise(runs, built_at=STARTED)

    answer = history.answer(shape_of(Shape()))

    assert answer.cohort is not None
    assert answer.cohort.succeeded == 3
    assert answer.cohort.failed == 2
    assert answer.cohort.median_seconds == Decimal(1800)
    assert "2 more runs failed and are not in that figure." in answer.said


def test_a_thin_cohort_is_counted_rather_than_quoted() -> None:
    """The third refusal. Two successes are not a distribution.

    Mutation: drop the ``RUNS_FOR_A_FIGURE`` bar, or lower it to one. A median over a single
    run reads exactly like a median over thirty, and the sentence gives a reader nothing to
    discount it with.
    """
    runs = tuple(
        a_run(seconds=seconds, run_id=f"run_{index}")
        for index, seconds in enumerate((600, 1800))
    )
    history = summarise(runs, built_at=STARTED)

    answer = history.answer(shape_of(Shape()))

    assert answer.cohort is not None
    assert answer.cohort.answerable is False
    assert "median" not in answer.said
    assert f"fewer than the {RUNS_FOR_A_FIGURE} successes" in answer.said


# ---------------------------------------------------------------------------------------
# The fourth thing it must not invent: that the reading is current
# ---------------------------------------------------------------------------------------


def test_every_answer_says_when_it_was_measured_and_over_how_many_runs() -> None:
    """THE STALENESS ANSWER. Mutation: drop ``_measured`` from one of the three sentences.

    The digest is a committed file that travels with an install, so a reader on a month-old
    tag is quoting a month-old platform and nothing on their laptop can tell them. All three
    sentences carry it, because the one most likely to be acted on wrongly is the refusal:
    "nothing of this shape has run" invites somebody to conclude they are first, and it is
    only ever true of the runs the reading saw.

    Asserted over all three together rather than one case each, so a sentence added later
    without the clause fails here rather than shipping bare.
    """
    thin = summarise(
        tuple(a_run(seconds=s, run_id=f"run_{i}") for i, s in enumerate((600, 1800))),
        built_at=STARTED,
    )
    quotable = summarise(three_successes(), built_at=STARTED)

    answers = (
        quotable.answer(shape_of(Shape())),
        thin.answer(shape_of(Shape())),
        quotable.answer(shape_of(Shape(workload_profile="nobody-has-run-this"))),
    )

    for answer in answers:
        assert "Measured on 2026-08-01" in answer.said
        assert answer.measured_at == STARTED
    assert "over 3 run(s) recorded by this platform" in answers[0].said
    assert "over 2 run(s) recorded by this platform" in answers[1].said


def test_the_install_that_carries_no_reading_claims_no_date() -> None:
    """Mutation: give the missing-reading answer a date too, from the clock.

    There is no reading, so there is nothing that was measured and no date that would be
    true. A sentence saying "measured on today" over an install carrying nothing is the
    worst of the four answers: it is the only one that would be actively false.
    """
    answer = history_for(Shape(), history=None)

    assert answer.measured_at is None
    assert "Measured on" not in answer.said


def test_the_date_is_the_readings_own_and_not_the_clock_of_whoever_asks() -> None:
    """Mutation: render an age in days instead of the date it was built.

    An age is computed against the reader's clock, which makes one reading three different
    strings -- in a test, in a pull request body and on a terminal -- and rots a golden
    overnight. Two answers from one reading taken at different moments have to be the same
    string, which is what this asserts by asking the same history twice.
    """
    history = summarise(three_successes(), built_at=datetime(2026, 1, 2, 3, 4, tzinfo=UTC))

    first = history.answer(shape_of(Shape()))
    second = history.answer(shape_of(Shape()))

    assert first.said == second.said
    assert "Measured on 2026-01-02 over" in first.said
    # The date and not the time. A median over a handful of runs does not become a
    # different measurement at a different hour of the day it was taken.
    assert "03:04" not in first.said


def test_a_run_that_never_reached_an_instance_is_in_neither_count() -> None:
    """A queued run is not a failure of this shape and is certainly not a duration.

    Mutation: treat a run with no attempts as a failure. The failed count goes to one and a
    submitter is told this shape fails a quarter of the time, when what happened is that a
    run was submitted and never placed.
    """
    history = summarise((*three_successes(), a_run_that_never_started()), built_at=STARTED)

    answer = history.answer(shape_of(Shape()))

    assert answer.cohort is not None
    assert answer.cohort.succeeded == 3
    assert answer.cohort.failed == 0


# ---------------------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------------------


def test_the_most_specific_rung_answers_when_it_can() -> None:
    """Precision where it exists, which is the reason for a ladder rather than one key.

    Mutation: reverse the walk and answer from the coarsest rung first. This returns the
    cohort that also holds the other dataset's runs, so a submitter is told the median of
    two different corpora and the sentence claims their own.
    """
    mine = three_successes()
    theirs = tuple(
        a_run(
            seconds=100000,
            shape=Shape(dataset_release="dolma-2026-07"),
            run_id=f"run_other_{index}",
        )
        for index in range(3)
    )
    history = summarise((*mine, *theirs), built_at=STARTED)

    answer = history.answer(shape_of(Shape()))

    assert answer.cohort is not None
    assert answer.cohort.rung == 0
    assert answer.cohort.median_seconds == Decimal(1800)


def test_a_coarser_rung_answers_when_the_specific_one_is_thin() -> None:
    """Coverage where precision is not available, and the sentence says which rung answered.

    One run on this dataset and three on another, all on the same workload and machine. The
    specific rung is too thin, so the answer comes from the rung that drops the dataset, and
    the words name what was dropped.

    Mutation: stop at the first rung that matched anything rather than the first that can
    answer. This returns the one-run cohort with no figure, and a submitter who would have
    been given a useful answer is told there is not enough history.
    """
    history = summarise(
        (
            a_run(seconds=1200, run_id="run_mine"),
            *(
                a_run(
                    seconds=seconds,
                    shape=Shape(dataset_release="dolma-2026-07"),
                    run_id=f"run_other_{index}",
                )
                for index, seconds in enumerate((900, 1500, 2100))
            ),
        ),
        built_at=STARTED,
    )

    answer = history.answer(shape_of(Shape()))

    assert answer.cohort is not None
    assert answer.cohort.rung == 1
    assert answer.cohort.succeeded == 4
    assert RUNGS[1][1] in answer.said
    assert "on any dataset" in answer.said


def test_the_rungs_get_strictly_less_specific() -> None:
    """The ladder only works downwards, and nothing else says so.

    Mutation: reorder ``RUNGS``, or add a rung that is not a subset of the one above it. The
    walk would then be able to skip past a cohort with more evidence in it, and the sentence
    naming the rung would stop meaning what it says.
    """
    for finer, coarser in pairwise(RUNGS):
        assert set(coarser[0]) < set(finer[0])
    assert set(RUNGS[0][0]) == set(SHAPE_FIELDS)


def test_the_workload_is_never_dropped_from_the_key() -> None:
    """A duration across two different programs is not a measurement of anything.

    Mutation: add a rung keyed on the repository alone. A tokenizer run and a training run
    in one repository would then answer for each other, and the coarsest rung would answer
    for almost everything, which is what makes a useless number look like good coverage.
    """
    for fields, _ in RUNGS:
        assert "workload_profile" in fields
        assert "repository" in fields


# ---------------------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------------------


def test_the_median_is_a_duration_some_run_actually_took() -> None:
    """Never a mean, even across an even count.

    Mutation: average the two middle values. Four runs at 600, 1200, 1800 and 2400 seconds
    would report 1500, which no run took, and somebody looking for the run behind the figure
    finds nothing.
    """
    runs = tuple(
        a_run(seconds=seconds, run_id=f"run_{index}")
        for index, seconds in enumerate((600, 1200, 1800, 2400))
    )
    history = summarise(runs, built_at=STARTED)
    cohort = history.answer(shape_of(Shape())).cohort

    assert cohort is not None
    assert cohort.median_seconds == Decimal(1200)
    assert cohort.median_seconds in {Decimal(seconds) for seconds in (600, 1200, 1800, 2400)}


def test_the_range_is_the_fastest_and_slowest_success() -> None:
    """A median without a spread invites the reading that every run takes that long.

    Mutation: report the range across every run rather than across the successes. The
    fastest becomes the four-minute crash, and the spread stops describing the work.
    """
    runs = (*three_successes(), a_run(seconds=60, state="failed", run_id="run_f"))
    cohort = summarise(runs, built_at=STARTED).answer(shape_of(Shape())).cohort

    assert cohort is not None
    assert cohort.fastest_seconds == Decimal(600)
    assert cohort.slowest_seconds == Decimal(5400)


@pytest.mark.parametrize(
    ("seconds", "said"),
    [
        (0, "0s"),
        (59, "59s"),
        (60, "1m"),
        (3599, "59m"),
        (3600, "1h"),
        (5400, "1h30m"),
        (86400, "24h"),
    ],
)
def test_a_duration_is_printed_the_way_a_person_says_one(seconds: int, said: str) -> None:
    """Whole units, and never a decimal place the sample does not support.

    Mutation: print the seconds. ``7842s`` is a number a reader has to divide, and a figure
    quoted to the second claims a precision a median over three runs does not carry. The
    boundary rows are the ones that catch an off-by-one in the unit change.
    """
    assert elapsed_said(Decimal(seconds)) == said


# ---------------------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------------------


def test_a_reading_survives_a_round_trip_including_a_cohort_with_no_successes() -> None:
    """The document has to carry a cohort that is entirely failures.

    That cohort answers nothing and is still a finding: this shape has been tried and has
    not worked, which is different from a shape nobody has tried.

    Mutation: skip cohorts with no successes when writing. The reader would then report a
    shape that fails every time as one nobody has run.
    """
    runs = (
        *three_successes(),
        *(
            a_run(
                seconds=120,
                state="failed",
                shape=Shape(compute_profile="gpu-8xa10g"),
                run_id=f"run_broken_{index}",
            )
            for index in range(2)
        ),
    )
    history = summarise(runs, built_at=STARTED)

    restored = from_document(as_document(history))

    assert restored == history
    # Read off the cohorts rather than through ``answer``, which would walk past this one to
    # the rung that drops the machine and has three successes in it. That fall-through is
    # correct and is tested above; what is tested here is that the all-failures cohort
    # survives being written and read.
    broken = next(
        cohort
        for cohort in restored.cohorts
        if cohort.rung == 1 and "gpu-8xa10g" in cohort.key
    )
    assert broken.succeeded == 0
    assert broken.failed == 2
    assert broken.median_seconds is None


def test_a_document_from_a_newer_writer_is_refused_rather_than_half_read() -> None:
    """Mutation: default an unknown ``format_version`` to the one this tree knows.

    A reader that took the fields it recognised would report the rest as absent, and absent
    is the one meaning a measurement may not invent.
    """
    document = as_document(summarise(three_successes(), built_at=STARTED))
    document["format_version"] = HISTORY_FORMAT_VERSION + 1

    with pytest.raises(RunHistoryFormatError):
        from_document(document)


def test_money_shaped_numbers_leave_as_text_rather_than_as_json_numbers() -> None:
    """Seconds are a Decimal and JSON's number is a float.

    Mutation: write the Decimal through ``float``. The round trip stops being exact, and a
    figure that changes in its eleventh place between the capture and the page is an
    afternoon somebody spends.
    """
    document = as_document(summarise(three_successes(), built_at=STARTED))
    cohort = document["cohorts"][0]

    assert isinstance(cohort["median_seconds"], str)
    assert isinstance(cohort["fastest_seconds"], str)
    assert isinstance(cohort["slowest_seconds"], str)
    assert json.loads(json.dumps(document)) == document


def test_an_absent_reading_is_none_and_an_unreadable_one_raises(tmp_path: Path) -> None:
    """The two failures are not the same and are not reported the same way.

    Mutation: catch the parse error in ``load_run_history`` and return ``None``. A broken
    install would then be indistinguishable from one that was never given a reading, and the
    CLI would go on saying nothing is packaged while a corrupt file sat beside it.
    """
    assert load_run_history(tmp_path) is None

    (tmp_path / HISTORY_FILENAME).write_text("{ not json", encoding="utf-8")
    with pytest.raises(RunHistoryFormatError):
        load_run_history(tmp_path)


def test_a_written_reading_is_read_back_by_the_loader(tmp_path: Path) -> None:
    history = summarise(three_successes(), built_at=STARTED)
    (tmp_path / HISTORY_FILENAME).write_text(
        json.dumps(as_document(history), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    assert load_run_history(tmp_path) == history


# ---------------------------------------------------------------------------------------
# Coverage, which is the figure the key is chosen on
# ---------------------------------------------------------------------------------------


def test_coverage_counts_the_runs_that_would_get_a_figure_and_not_the_cohorts() -> None:
    """The honest denominator for "what fraction can this answer for".

    Three runs of one shape and one of another: the three are answered for and the one is
    not, so coverage is three quarters rather than one half of the two cohorts.

    Mutation: count cohorts instead of runs. A store with one enormous cohort and nine tiny
    ones would report ten percent coverage while answering for nearly every submission.
    """
    runs = (*three_successes(), a_run(seconds=99, shape=Shape(workload_profile="olmo-eval-check")))
    history = summarise(runs, built_at=STARTED)

    assert coverage(history, runs) == (3, 4)


def test_coverage_is_zero_when_nothing_reaches_the_bar() -> None:
    """The case the committed fixtures are in, so the number is not accidentally reassuring.

    Mutation: count a run as answered when any cohort matches it. This returns two, and a
    reader deciding whether the ladder is worth having would be told it answers for
    everything when it answers for nothing.
    """
    runs = tuple(
        a_run(seconds=seconds, run_id=f"run_{index}")
        for index, seconds in enumerate((600, 1800))
    )
    history = summarise(runs, built_at=STARTED)

    assert coverage(history, runs) == (0, 2)


def test_a_substrate_run_and_a_manifest_are_keyed_by_the_same_four_field_names() -> None:
    """The join that makes a submission findable in the history, asserted rather than hoped.

    Mutation: rename one field on either side. ``shape_of`` would raise on the renamed
    carrier rather than quietly mismatching, but the failure would surface at the moment a
    submitter ran ``check`` rather than here.
    """
    from edullm_platform.contracts.manifest import RunManifest

    assert set(SHAPE_FIELDS) <= set(RunManifest.model_fields)
    assert set(SHAPE_FIELDS) <= set(RunFacts.__dataclass_fields__)
