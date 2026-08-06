"""Which weights a run starts from, and where they are.

Weights resolve two ways and both are the same statement: a sealed ``model/`` artifact, or the
checkpoints a prior run wrote, named by that run's id. ``system-overview.md``, "Where data
lives", is the specification. The second form is what makes evaluating a training run possible
without writing twenty paths, and it is the whole reason this module exists.

**Every recorded checkpoint resolves and the uncertified ones are named rather than dropped.**
``CheckpointManifest.is_resumable`` is true when a success marker sits beside the payload, and
``resume_reference()`` refuses one without. That is a rule about resuming, where reading
uncertified bytes means continuing training from a state that may be half-written, and it does
not transfer to a read that scores and discards.

**Counted on 2026-08-05 over every ``result/`` object in the lineage store: 260 checkpoints
across 29 runs, of which two carry a marker.** Both of the two are the platform team's own runs
from that evening; every research team's checkpoint is uncertified. So a certification-required
rule would resolve all 27 research runs to nothing and leave a re-run campaign with nothing to
re-run. The premise has moved -- it was zero of 116 when this was designed and zero of 242 the
day before -- and it has moved in a way that strengthens rather than weakens the reading: the
producer side is now demonstrably fixable, and 258 records that predate the fix still have to be
readable.

Whether an evaluation may read an uncertified checkpoint is recorded in
``docs-frank/reference/decisions.md``. The ruling is that it may, and that the result manifest
records that it did.

**A step written twice resolves to the later write.** A retried attempt legitimately writes step
200 again, and both attempts' result manifests report it. Evaluating both puts two points on one
x value. The comparison is on ``created_at`` rather than on the order the reader hands back
attempts, because nothing promises that order.

**The eval team's own sweep script orders checkpoints by the trailing integer in the directory
name, so that ``step9`` sorts before ``step10``.** That rule is not reproduced here and its
absence is not a lost behaviour: ``CheckpointManifest.step`` is an integer that the lifecycle
projection parsed out of the layout, so the ordering is numeric already and a string sort has
nothing to fix. What is preserved is the reason for the rule -- a curve whose points are in the
wrong order is worse than no curve.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

from edullm_platform.contracts.manifest import FanOut
from edullm_platform.contracts.results import CheckpointManifest, ResultManifest

__all__ = [
    "SWEEP_INDEX_PARAMETER",
    "ResolvedWeights",
    "ResultManifestReader",
    "SweepCell",
    "UnresolvableWeightsError",
    "WeightsSource",
    "fanout_for_sweep",
    "plan_checkpoint_sweep",
    "resolve_weights_from_run",
    "sweep_plan_document",
]


class UnresolvableWeightsError(ValueError):
    pass


class WeightsSource(StrEnum):
    #: A digest-pinned artifact under ``model/`` in the sealed bucket.
    SEALED_MODEL = "sealed_model"
    #: The checkpoints a prior platform run wrote, named by that run's id.
    PRIOR_RUN = "prior_run"


@dataclass(frozen=True)
class ResolvedWeights:
    source: WeightsSource
    #: The registry reference id, or the run id, exactly as the submission named it.
    reference: str
    #: Every recorded checkpoint, one per step, ascending. Empty is not reachable: resolution
    #: raises rather than handing back a sweep of nothing.
    checkpoints: tuple[CheckpointManifest, ...]
    #: The subset of those steps carrying no success marker. A strict subset of the steps in
    #: ``checkpoints`` and never a set removed from it: this is what the approver is told and
    #: what a cell records, not what was filtered out.
    uncertified_steps: tuple[int, ...]


class ResultManifestReader(Protocol):
    """The one question this module asks the lineage store.

    A Protocol rather than a store class for the reason ``checkpoints.CheckpointStore`` is one:
    boto3 is not a project dependency, and the module that ends up holding the S3 calls is not
    the module that decides what a resolution means.
    """

    def result_manifests_for(self, run_id: str) -> Sequence[ResultManifest]: ...


def resolve_weights_from_run(
    reader: ResultManifestReader,
    *,
    run_id: str,
) -> ResolvedWeights:
    manifests = tuple(reader.result_manifests_for(run_id))
    if not manifests:
        raise UnresolvableWeightsError(
            f"{run_id} has no result record in the lineage store, so nothing here knows what "
            "it wrote. Check the run id; `edullm status` prints it for a run that finished."
        )

    latest_at_step: dict[int, CheckpointManifest] = {}
    for manifest in manifests:
        for entry in manifest.checkpoints:
            held = latest_at_step.get(entry.step)
            if held is None or entry.created_at > held.created_at:
                latest_at_step[entry.step] = entry

    if not latest_at_step:
        raise UnresolvableWeightsError(
            f"{run_id} recorded no checkpoint at all, so there is nothing to evaluate. A "
            "workload profile whose `checkpoint` is null saves nothing by design; see "
            "config/workload-catalog.yaml for the profile this run declared."
        )

    steps = sorted(latest_at_step)
    return ResolvedWeights(
        source=WeightsSource.PRIOR_RUN,
        reference=run_id,
        checkpoints=tuple(latest_at_step[step] for step in steps),
        uncertified_steps=tuple(step for step in steps if not latest_at_step[step].is_resumable),
    )


#: What a checkpoint sweep's array index varies.
#:
#: A LABEL AND NOT A VARIABLE NAME, WHICH IS EASY TO GET BACKWARDS AND WAS. ``FanOut``'s
#: ``index_parameter`` records what the index means, and ``execution.py`` puts that string into
#: the container as the *value* of ``EDULLM_FANOUT_INDEX_PARAMETER``. The index itself is
#: ``AWS_BATCH_JOB_ARRAY_INDEX``, which Batch sets per child, and that is what a cell reads to
#: select its row from the sweep plan. So the right value here is the word for what varies -- a
#: cell learns it is varying the checkpoint, and where the list of them is.
SWEEP_INDEX_PARAMETER: Final = "checkpoint"


@dataclass(frozen=True)
class SweepCell:
    #: Position in the array, contiguous from zero, which is what Batch requires. Never the
    #: step: a checkpoint at step 5000 would otherwise ask for an array of 5001.
    index: int
    step: int
    checkpoint_uri: str
    #: Whether this cell's own checkpoint carried a success marker. Per cell rather than read
    #: off the run, because a curve where one point came from an uncertified checkpoint needs
    #: to say which point. Two of the 260 checkpoints in the store are certified as of
    #: 2026-08-05 and both are the platform team's own, so on a research team's run this is
    #: False on every cell -- which is what the record is for rather than a reason to drop it.
    certified: bool


def plan_checkpoint_sweep(weights: ResolvedWeights) -> tuple[SweepCell, ...]:
    """One cell per recorded checkpoint, in step order.

    EVERY CELL IS A MACHINE. A sweep multiplies the run's cost by its length, and nothing in
    Batch caps how many run at once -- what bounds it is the compute environment's MaxvCpus
    divided by what one cell reserves. See config/policy.yaml for the fan-out ceiling and
    contracts/manifest.py's FanOut for why no parallelism field is offered.
    """
    if weights.source is WeightsSource.SEALED_MODEL:
        raise ValueError(
            "a sealed model is one set of weights and not a sweep; a sweep is what a prior "
            "run's checkpoints make"
        )
    return tuple(
        SweepCell(
            index=index,
            step=entry.step,
            checkpoint_uri=entry.uri,
            certified=entry.is_resumable,
        )
        for index, entry in enumerate(weights.checkpoints)
    )


def fanout_for_sweep(cells: Sequence[SweepCell]) -> FanOut | None:
    """The fan-out a sweep of this length needs, or None where it needs none.

    ``FanOut`` declares ``size >= 2``, so one checkpoint is an ordinary single job. Asking Batch
    for an array of one is a different job shape whose outputs land under a cell directory, and
    a curve of one point does not need one.
    """
    if len(cells) < 2:
        return None
    return FanOut(size=len(cells), index_parameter=SWEEP_INDEX_PARAMETER)


def sweep_plan_document(cells: Sequence[SweepCell]) -> dict[str, object]:
    """What the container reads to find its own checkpoint.

    Written to the run's output prefix before the array is submitted, because a cell cannot be
    told its own checkpoint through the job definition: every cell of a Batch array runs the
    same container overrides and differs only in its index.
    """
    return {
        "schema_version": 1,
        "index_parameter": SWEEP_INDEX_PARAMETER,
        "cells": [
            {
                "index": cell.index,
                "step": cell.step,
                "checkpoint_uri": cell.checkpoint_uri,
                "certified": cell.certified,
            }
            for cell in cells
        ],
    }
