"""What two runs of one submission agree and disagree about, field by field.

The spine's done-condition is that the synthetic job runs twice and the two lineage records
are identical except for time and id. That is a claim somebody has to be able to check, and
"identical except for" is not something an eye reads off two JSON documents reliably -- the
records are a hundred-odd leaves each and the interesting failure is one of them moving.

**The shape is deliberately** :mod:`edullm_platform.rebuild_comparison`'s. That module compares
two builds of one image and its argument transfers whole: the useful claim is not that two
things are identical, it is that every field which differs has a named cause and that no field
derived from a pinned input is among them. So there are two lists here and both are load
bearing. :data:`VARIANCE_CAUSES` is closed, and a difference outside it is a failure rather than
a line in a report. :data:`IDENTICAL_FIELDS` is checked positively, because a comparison whose
identical set quietly shrank would otherwise still pass -- an empty document differs from an
empty document in nothing at all.

**Three records and one integer.** ``intent/``, ``decision/`` and ``result/`` are what the
platform writes about a run and are walked leaf by leaf. ``attempt/{run_id}/{attempt_id}.json``
is per-attempt and keyed by an id Batch mints, so every leaf of it is an id or a time; walking
it would add a document that can only produce noise. What it carries that is not noise is how
many attempts there were, and two runs that needed different numbers of them are not the same
run twice. So the count is compared and the records are not.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import BeforeValidator, Field, model_validator

from edullm_platform.contracts.base import ContractModel, require_ordered_sequence
from edullm_platform.contracts.identity import RunId

__all__ = [
    "IDENTICAL_FIELDS",
    "RECORD_PREFIXES",
    "VARIANCE_CAUSES",
    "ComparedField",
    "FieldDifference",
    "RecordField",
    "RecordedRun",
    "TwoRunComparison",
    "VarianceCause",
    "cause_for",
    "compare_runs",
    "flatten",
    "identical_fields_missing",
    "read_run",
    "unexplained",
]

#: The three prefixes walked leaf by leaf, in the order a reader wants them.
RECORD_PREFIXES: Final[tuple[str, ...]] = ("intent", "decision", "result")


class RecordField(ContractModel):
    """One leaf of one record, by dotted path and JSON-encoded value.

    The value is the JSON encoding rather than the leaf itself, so a string and the number
    that prints the same way cannot compare equal.
    """

    path: str = Field(min_length=1, max_length=256)
    value: str = Field(max_length=8192)


class RecordedRun(ContractModel):
    """One run's three records flattened, plus how many attempts it took."""

    run_id: RunId
    attempt_count: int = Field(ge=0)
    fields: Annotated[tuple[RecordField, ...], BeforeValidator(require_ordered_sequence)] = Field(
        min_length=1, strict=False
    )

    @model_validator(mode="after")
    def validate_paths_are_unique(self) -> Self:
        paths = [field.path for field in self.fields]
        if len(set(paths)) != len(paths):
            raise ValueError("a record field path may appear once")
        return self

    def field_map(self) -> dict[str, str]:
        return {"attempt_count": str(self.attempt_count)} | {
            field.path: field.value for field in self.fields
        }


@dataclass(frozen=True)
class FieldDifference:
    path: str
    left: str
    right: str


@dataclass(frozen=True)
class VarianceCause:
    """One reason a field may differ between two runs of the same submission."""

    name: str
    pattern: re.Pattern[str]
    detail: str


#: Every reason a leaf may differ. Ordered most specific first. A difference matching nothing
#: here is unexplained, and an unexplained difference is what this module exists to find.
VARIANCE_CAUSES: Final[tuple[VarianceCause, ...]] = (
    VarianceCause(
        name="the run id",
        pattern=re.compile(r"^(intent|decision|result)\.run_id$"),
        detail=(
            "The run id is minted per submission and is what names the Batch job, the "
            "Step Functions execution and the S3 prefix. Two runs cannot share one."
        ),
    ),
    VarianceCause(
        name="the attempt id",
        pattern=re.compile(r"^result\.attempt_id$"),
        detail="Minted by Batch when it starts an attempt. Nothing on our side chooses it.",
    ),
    VarianceCause(
        name="a prefix carrying the run id",
        pattern=re.compile(
            r"^result\.(output_prefixes\[\d+\]|checkpoints\[\d+\]\.(uri|success_marker_uri))$"
        ),
        detail=(
            "contracts/results.py::output_prefix derives every one of these from the team "
            "and the run id, so they differ exactly as the run id does and for no other "
            "reason. A difference here that is not the run id substitution is a different "
            "finding, and comparing the whole string is what would hide it -- see the note "
            "on this cause in the plan that introduced it."
        ),
    ),
    VarianceCause(
        name="the W&B run id, which is the platform run id",
        pattern=re.compile(r"^result\.wandb_run\.run_id$"),
        detail=(
            "execution.py sets WANDB_RUN_ID to the platform run id, so this field is the "
            "run id under another name. Entity and project are not in this cause and are "
            "required to be identical."
        ),
    ),
    VarianceCause(
        name="the instant a record was written",
        pattern=re.compile(r"^(intent|decision)\.recorded_at$|^result\.completed_at$"),
        detail="Wall clock. Two runs happen at two times; that is the whole of it.",
    ),
    VarianceCause(
        name="the instant a checkpoint was written",
        pattern=re.compile(r"^result\.checkpoints\[\d+\]\.created_at$"),
        detail="Wall clock, recorded by the program that wrote the payload.",
    ),
    VarianceCause(
        name="the GitHub Actions run that dispatched it",
        pattern=re.compile(r"^intent\.workflow_run\.run_id$"),
        detail=(
            "A dispatch id, allocated by GitHub. The workflow path, ref, repository and "
            "attempt number are not in this cause and are required to be identical: those "
            "are what the admission role's trust policy pins on."
        ),
    ),
    VarianceCause(
        name="the checkpoint payload digest",
        pattern=re.compile(r"^result\.checkpoints\[\d+\]\.checksum$"),
        detail=(
            "Floating-point reduction order on a GPU is not fixed by a seed, so two runs of "
            "one program can write two payloads that differ in their last bits. This "
            "platform has never claimed a workload is bit-reproducible and this comparison "
            "does not start claiming it. The payload's SIZE is a different fact -- "
            "torch.save writes a length fixed by shapes and dtypes -- and it is in "
            "IDENTICAL_FIELDS, so a truncated checkpoint is still caught."
        ),
    ),
    VarianceCause(
        name="the bytes the checkpoint listing saw",
        pattern=re.compile(r"^result\.checkpoint_survey\.bytes_seen$"),
        detail=(
            "The same argument as the digest above, one level up: this is a sum over "
            "payloads whose sizes are fixed and whose success markers carry a digest and a "
            "timestamp of their own. objects_seen is the field that must not move and it is "
            "in IDENTICAL_FIELDS."
        ),
    ),
)

#: The leaves that must be present on both sides and equal. Stated as a list to be checked
#: rather than left implicit in the absence of a difference: two records that both stopped
#: carrying a field agree about it, and that is not the same as agreeing.
IDENTICAL_FIELDS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"^attempt_count$"),
    re.compile(r"^intent\.manifest_sha256$"),
    re.compile(r"^intent\.submitter$"),
    re.compile(r"^intent\.approving_environment$"),
    re.compile(
        r"^intent\.manifest\."
        r"(repository|commit_sha|image_digest|workload_profile|compute_profile"
        r"|dataset_release|team|wandb_project|maximum_runtime_hours|maximum_attempts)$"
    ),
    re.compile(r"^intent\.manifest\.command\[\d+\]$"),
    re.compile(
        r"^intent\.workflow_run\."
        r"(run_repository|workflow_repository|workflow_path|workflow_ref|run_attempt)$"
    ),
    re.compile(
        r"^decision\.(accepted|reason|detail|policy_version|approval_class"
        r"|approving_environment|manifest_sha256)$"
    ),
    re.compile(r"^decision\.(cost|authorization)\.[a-z_]+$"),
    re.compile(r"^result\.(outcome|exit_code|retention_class)$"),
    re.compile(r"^result\.wandb_run\.(entity|project)$"),
    re.compile(r"^result\.checkpoint_survey\.(outcome|objects_seen)$"),
    re.compile(r"^result\.checkpoints\[\d+\]\.(step|epoch|size_bytes)$"),
)


class ComparedField(ContractModel):
    path: str = Field(min_length=1, max_length=256)
    left: str = Field(max_length=8192)
    right: str = Field(max_length=8192)
    #: The name of the cause that explains this difference, or ``None`` where none does.
    cause: str | None = Field(default=None, max_length=128)


class TwoRunComparison(ContractModel):
    """The committed record of one comparison, so the answer outlives the terminal."""

    schema_version: Literal[1]
    left: RunId
    right: RunId
    compared_at: datetime = Field(strict=False)
    differences: Annotated[
        tuple[ComparedField, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(default=(), strict=False)

    @property
    def unexplained_paths(self) -> tuple[str, ...]:
        return tuple(one.path for one in self.differences if one.cause is None)


def flatten(document: object, prefix: str) -> tuple[RecordField, ...]:
    """One leaf per entry, by dotted path, with the value JSON-encoded."""
    leaves: list[RecordField] = []

    def walk(value: object, path: str) -> None:
        if isinstance(value, dict):
            for key in sorted(str(one) for one in value):
                walk(value[key], f"{path}.{key}")
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")
            return
        leaves.append(RecordField(path=path, value=json.dumps(value, sort_keys=True)))

    walk(document, prefix)
    return tuple(leaves)


def _document(path: Path) -> object:
    """One record, allowing for the JSON string holding JSON the state machine writes."""
    loaded: object = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(loaded, str):
        loaded = json.loads(loaded)
    return loaded


def read_run(root: Path, run_id: str) -> RecordedRun:
    """One run's three records and its attempt count, out of a synced lineage tree.

    Every record is required. A run missing one is a run this comparison has nothing to say
    about, and reading the two that are there would produce an answer about a subset nobody
    asked for.
    """
    fields: list[RecordField] = []
    for prefix in RECORD_PREFIXES:
        source = root / prefix / f"{run_id}.json"
        if not source.is_file():
            raise FileNotFoundError(f"{run_id} has no {prefix} record under {root}")
        fields.extend(flatten(_document(source), prefix))
    attempts = root / "attempt" / run_id
    return RecordedRun(
        run_id=run_id,
        attempt_count=len(sorted(attempts.glob("*.json"))) if attempts.is_dir() else 0,
        fields=tuple(fields),
    )


def compare_runs(left: RecordedRun, right: RecordedRun) -> tuple[FieldDifference, ...]:
    """Every leaf the two runs disagree about, in path order.

    A leaf present in one and absent from the other is a difference too, reported with
    ``<absent>`` on the side that lacks it. A record that dropped a field entirely would
    otherwise compare equal on every field it still had.
    """
    left_fields = left.field_map()
    right_fields = right.field_map()
    return tuple(
        FieldDifference(
            path=path,
            left=left_fields.get(path, "<absent>"),
            right=right_fields.get(path, "<absent>"),
        )
        for path in sorted(set(left_fields) | set(right_fields))
        if left_fields.get(path, "<absent>") != right_fields.get(path, "<absent>")
    )


def cause_for(path: str) -> VarianceCause | None:
    return next((cause for cause in VARIANCE_CAUSES if cause.pattern.fullmatch(path)), None)


def unexplained(differences: Sequence[FieldDifference]) -> tuple[str, ...]:
    return tuple(one.path for one in differences if cause_for(one.path) is None)


def identical_fields_missing(left: RecordedRun, right: RecordedRun) -> tuple[str, ...]:
    """Paths that IDENTICAL_FIELDS requires and at least one of the two runs does not carry.

    Absence and agreement are different facts, and the whole reason this function exists is
    that the second is what a difference count reports when it means the first.
    """
    shared = set(left.field_map()) & set(right.field_map())
    either = set(left.field_map()) | set(right.field_map())
    required = {path for path in either if any(one.fullmatch(path) for one in IDENTICAL_FIELDS)}
    return tuple(sorted(required - shared))
