import pytest

from edullm_platform.contracts.manifest import FanOut
from edullm_platform.execution import FANOUT_INDEX_VARIABLE
from edullm_platform.weights import (
    SWEEP_INDEX_PARAMETER,
    ResolvedWeights,
    SweepCell,
    WeightsSource,
    fanout_for_sweep,
    plan_checkpoint_sweep,
    sweep_plan_document,
)
from tests.test_weights_resolution import PREFIX, RUN, checkpoint


def resolved(steps: tuple[int, ...], *, uncertified: tuple[int, ...] = ()) -> ResolvedWeights:
    return ResolvedWeights(
        source=WeightsSource.PRIOR_RUN,
        reference=RUN,
        checkpoints=tuple(
            checkpoint(
                step,
                certified=step not in uncertified,
                created_at="2026-08-04T10:00:00.000000Z",
            )
            for step in steps
        ),
        uncertified_steps=uncertified,
    )


def test_one_cell_per_checkpoint_indexed_from_zero():
    cells = plan_checkpoint_sweep(resolved((100, 200, 300)))
    assert cells == (
        SweepCell(
            index=0, step=100, checkpoint_uri=f"{PREFIX}checkpoints/step100/", certified=True
        ),
        SweepCell(
            index=1, step=200, checkpoint_uri=f"{PREFIX}checkpoints/step200/", certified=True
        ),
        SweepCell(
            index=2, step=300, checkpoint_uri=f"{PREFIX}checkpoints/step300/", certified=True
        ),
    )


def test_a_cell_records_whether_its_own_checkpoint_was_certified():
    # The bug this catches: carrying the run-level uncertified_steps list onto every cell, or
    # onto none. Exactly one of the three cells here is uncertified.
    cells = plan_checkpoint_sweep(resolved((100, 200, 300), uncertified=(200,)))
    assert [cell.certified for cell in cells] == [True, False, True]


def test_the_index_is_position_and_not_the_step():
    # The bug this catches: using the step as the array index. Batch array indices are
    # contiguous from zero, so a checkpoint at step 5000 would ask for 5001 cells.
    cells = plan_checkpoint_sweep(resolved((5000, 10000)))
    assert [cell.index for cell in cells] == [0, 1]
    assert [cell.step for cell in cells] == [5000, 10000]


def test_one_checkpoint_is_not_a_fanout():
    # FanOut declares size >= 2. A one-cell array is a single job, and asking Batch for an
    # array of one is a different job shape with a different output layout.
    cells = plan_checkpoint_sweep(resolved((100,)))
    assert len(cells) == 1
    assert fanout_for_sweep(cells) is None


def test_two_or_more_checkpoints_become_a_fanout_of_that_size():
    cells = plan_checkpoint_sweep(resolved((100, 200, 300)))
    assert fanout_for_sweep(cells) == FanOut(size=3, index_parameter=SWEEP_INDEX_PARAMETER)


def test_the_index_parameter_is_what_varies_and_not_a_variable_name():
    # The bug this catches: writing "EDULLM_SWEEP_INDEX" here. execution.py puts this string
    # into the container as the VALUE of EDULLM_FANOUT_INDEX_PARAMETER, so a variable name
    # there would tell a cell that what it varies is called EDULLM_SWEEP_INDEX -- and the
    # variable holding the actual index is AWS_BATCH_JOB_ARRAY_INDEX, which Batch sets.
    assert SWEEP_INDEX_PARAMETER == "checkpoint"
    assert not SWEEP_INDEX_PARAMETER.startswith("EDULLM_")
    # Held against execution.py's own name for the index rather than against a copy of it,
    # so a rename there fails here instead of leaving two strings that used to agree.
    assert SWEEP_INDEX_PARAMETER != FANOUT_INDEX_VARIABLE


def test_a_sealed_model_is_not_a_sweep():
    weights = ResolvedWeights(
        source=WeightsSource.SEALED_MODEL,
        reference="model-smollm2-135m-v1",
        checkpoints=(),
        uncertified_steps=(),
    )
    with pytest.raises(ValueError, match="a sealed model is one set of weights"):
        plan_checkpoint_sweep(weights)


def test_the_plan_document_is_ordered_and_carries_every_cell():
    # This document is what the container reads to find its own checkpoint, so a cell missing
    # from it is a cell that starts, finds nothing and exits.
    cells = plan_checkpoint_sweep(resolved((100, 200)))
    document = sweep_plan_document(cells)
    assert document == {
        "schema_version": 1,
        "index_parameter": SWEEP_INDEX_PARAMETER,
        "cells": [
            {
                "index": 0,
                "step": 100,
                "checkpoint_uri": f"{PREFIX}checkpoints/step100/",
                "certified": True,
            },
            {
                "index": 1,
                "step": 200,
                "checkpoint_uri": f"{PREFIX}checkpoints/step200/",
                "certified": True,
            },
        ],
    }


def test_the_plan_document_indexes_agree_with_the_fanout_it_is_submitted_beside():
    # The two halves are computed separately and a cell reads one while Batch reads the
    # other. If they disagreed by one, the last cell would index past the end of the plan
    # and exit, having been paid for.
    cells = plan_checkpoint_sweep(resolved((0, 40, 80, 120)))
    fanout = fanout_for_sweep(cells)
    document = sweep_plan_document(cells)
    assert fanout is not None
    assert fanout.size == len(document["cells"])  # type: ignore[arg-type]
    assert [entry["index"] for entry in document["cells"]] == list(range(fanout.size))  # type: ignore[index]
    assert fanout.index_parameter == document["index_parameter"]


def test_every_cell_of_a_sweep_names_a_different_checkpoint():
    # The failure this catches is the one Task 9 Step 10 says to check by hand: four cells
    # that all evaluate cell zero's checkpoint produce four successful jobs, four sets of
    # metrics and one point, and nothing else in the pipeline notices.
    cells = plan_checkpoint_sweep(resolved((0, 40, 80, 120)))
    assert len({cell.checkpoint_uri for cell in cells}) == len(cells)
