"""Reading what the eval harness reported into the shape the lineage record keeps.

**Out of ``tasks[].metrics`` and never out of ``summary``.** olmo-eval's ``metrics.json`` carries
both, and ``summary`` holds each task's primary metric alone -- so a task reporting two numbers
loses one, and the loss is silent because the record still looks populated. The eval team learned
this building ``run_eval_sweep.sh`` and wrote it into their own skill; it is repeated here as a
guard rather than as a comment, because a comment does not fail a test.

**A document with no ``tasks`` block is refused rather than read as empty.** A run that scored
nothing and a document this cannot parse are different facts, and the second recorded as the
first is a hole in a curve with no explanation beside it -- the defect ``CheckpointSurvey`` exists
to have closed, in the neighbouring field.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from edullm_platform.contracts.results import EvalMetric, EvalMetrics

__all__ = ["HARNESS_NAME", "read_olmo_eval_metrics"]

HARNESS_NAME = "olmo-eval"


def read_olmo_eval_metrics(document: Mapping[str, Any]) -> EvalMetrics:
    tasks = document.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError(
            "this metrics document has no `tasks` block to read. Scores come from "
            "tasks[].metrics and never from the top-level `summary`, which carries only each "
            "task's primary metric and drops the second on any task reporting more than one."
        )

    found: list[EvalMetric] = []
    for task in tasks:
        name = str(task.get("task_name", ""))
        metrics = task.get("metrics")
        instances = task.get("num_instances")
        if not isinstance(metrics, Mapping) or not metrics:
            raise ValueError(
                f"task {name!r} reported no metric. A task that ran and scored nothing is a "
                "failure to record, not a score of zero."
            )
        if not isinstance(instances, int) or instances < 1:
            raise ValueError(
                f"task {name!r} reported {instances!r} instances. A score with no denominator "
                "cannot be compared against another run's, which is what these are for."
            )
        for key, value in metrics.items():
            found.append(
                EvalMetric(task=name, key=str(key), value=float(value), instances=instances)
            )

    return EvalMetrics(
        schema_version=1,
        harness=HARNESS_NAME,
        metrics=tuple(sorted(found, key=lambda entry: (entry.task, entry.key))),
    )
