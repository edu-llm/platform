"""The eduLLM platform's runtime conventions, as a package a research container can install.

Four things a job needs to know and no more. What the platform told it about itself, where
its objects go, where its corpus is, and how to report without being able to be killed by
reporting.

WHY THIS IS A PACKAGE AND NOT A PARAGRAPH IN EACH REPOSITORY'S GUIDE. The same helper had
been written three times before this existed, in ``paths.py`` and ``config.py`` in
``grpo-tutor``, in ``organizer/`` and ``msctl`` in ``Memory-Split-P3``, and in
``S3_CHECKPOINTS.md`` and ``S3_DATASETS.md`` in ``edullm-p1``. Every one reconstructed the
same layout from the same variables, and every one is a place that layout can drift from the
one the workload role is scoped against. A run whose copy has drifted does not fail; it
writes somewhere it is denied at the end of a long job, or somewhere no lineage record names.

It is published from the platform repository rather than from one of its own, deliberately.
Two hundred lines in a separate repository buys a release process, a second CI, and a
version skew between the layout and the thing that enforces it. Here, the tests that hold
this package to the platform's own contracts run in the same suite as the contracts.

Public surface, which is the whole of what a migrating script needs.

| Name | What it does |
| --- | --- |
| `run_environment()` | Every variable the platform set, as one typed frozen object |
| `RunEnvironment` | That object. `.dataset()` resolves the corpus, `.as_environment()` writes it back |
| `MissingRunEnvironmentError` | Raised naming every absent variable at once |
| `output_prefix()` | `s3://<outputs>/teams/<team>/runs/<run_id>/` |
| `checkpoint_prefix()` | The `checkpoints/` subprefix a retry resumes from |
| `team_dataset_prefix()` | `teams/<team>/datasets/<name>/`, for a corpus a group built itself |
| `published_dataset_uri()` | `s3://edullm-data/<dataset_id>/<version>/` |
| `resolve_dataset()` | Published mode or explicit-paths mode, as a `DatasetLocation` |
| `DatasetLocation` | `mode`, `paths`, and the registry's three facts when there are any |
| `UnresolvedDatasetError` | Raised when a run can name no corpus at all |
| `start_wandb()` | `wandb.init` that warns and returns `None` instead of raising |
| `start_wandb_for()` | The same, with every argument taken off a `RunEnvironment` |
| `wandb_log()` | Logs to a handle that may be `None`. Never raises |
| `finish_wandb()` | Closes a handle that may be `None`. Never raises |
| `WANDB_ENTITY` | `eduLLM` |
| `OUTPUTS_BUCKET`, `PUBLISHED_DATASET_BUCKET` | The two buckets a run touches |
| `REQUIRED_VARIABLES`, `OPTIONAL_VARIABLES` | Every name the platform sets, and which are conditional |

The shortest correct training script is four lines.

```python
from edullm_client import finish_wandb, run_environment, start_wandb_for

run = run_environment()
wandb_run = start_wandb_for(run)
train(data=run.dataset().paths, save_to=run.checkpoint_dir)
finish_wandb(wandb_run)
```
"""

from __future__ import annotations

from .environment import (
    OPTIONAL_VARIABLES,
    REQUIRED_VARIABLES,
    MissingRunEnvironmentError,
    RunEnvironment,
    run_environment,
)
from .storage import (
    OUTPUTS_BUCKET,
    PUBLISHED_DATASET_BUCKET,
    DatasetLocation,
    UnresolvedDatasetError,
    checkpoint_prefix,
    output_prefix,
    published_dataset_uri,
    resolve_dataset,
    team_dataset_prefix,
)
from .telemetry import (
    WANDB_ENTITY,
    finish_wandb,
    start_wandb,
    start_wandb_for,
    wandb_log,
)

__all__ = [
    "OPTIONAL_VARIABLES",
    "OUTPUTS_BUCKET",
    "PUBLISHED_DATASET_BUCKET",
    "REQUIRED_VARIABLES",
    "WANDB_ENTITY",
    "DatasetLocation",
    "MissingRunEnvironmentError",
    "RunEnvironment",
    "UnresolvedDatasetError",
    "checkpoint_prefix",
    "finish_wandb",
    "output_prefix",
    "published_dataset_uri",
    "resolve_dataset",
    "run_environment",
    "start_wandb",
    "start_wandb_for",
    "team_dataset_prefix",
    "wandb_log",
]
