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

**Checking a required field positively means naming it before reading a record, and for a
while this did not.** The required set was a tuple of patterns, and a pattern can only be
asked whether a path it was handed matches. So the check gathered the paths the two records
carried, kept the ones a pattern matched, and required those to be on both sides -- which
means a field absent from BOTH records was never in the gathered set and was never checked.
That is the exact case the paragraph above says the positive check exists for, and it could
not fail. On a real July pair five required fields went unexamined this way, among them
``result.exit_code``. :data:`REQUIRED_FIELDS` is therefore a list of names rather than of
patterns, and :func:`required_field_coverage` reports a name neither record carries as
UNVERIFIED rather than as agreement or as a difference: nothing was compared, and neither
of the other two words is true of nothing.

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
    "REQUIRED_FIELDS",
    "REQUIRED_FIELD_FAMILIES",
    "VARIANCE_CAUSES",
    "ComparedField",
    "FieldDifference",
    "RecordField",
    "RecordedRun",
    "RequiredFieldCoverage",
    "TwoRunComparison",
    "VarianceCause",
    "cause_for",
    "compare_runs",
    "flatten",
    "read_run",
    "required_field_coverage",
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
class RequiredFieldCoverage:
    """Which required fields two runs let a comparison check, and which they do not.

    **Two absences that read alike in a difference count and mean opposite things.**
    ``missing`` is a field one record carries and the other does not, which is a finding
    about the runs and shows in the table as a value against ``<absent>``. ``unverified``
    is a field NEITHER record carries, which is a finding about the comparison: nothing was
    compared, no difference could have been produced, and the silence is not agreement.
    """

    missing: tuple[str, ...]
    unverified: tuple[str, ...]


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

#: Every required leaf whose path is known before a record is read. Written out one path per
#: line rather than folded into alternations, because a name here is a name this comparison
#: can look for in a record that does not carry it -- and that is the only reading under
#: which "both records dropped the field" is a sentence the tool can say. Held as strings
#: rather than patterns for the same reason: a pattern can be asked whether a path it was
#: given matches, and cannot be asked what it wanted.
REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "attempt_count",
    "intent.manifest_sha256",
    "intent.submitter",
    "intent.approving_environment",
    "intent.manifest.repository",
    "intent.manifest.commit_sha",
    "intent.manifest.image_digest",
    "intent.manifest.workload_profile",
    "intent.manifest.compute_profile",
    "intent.manifest.dataset_release",
    "intent.manifest.team",
    "intent.manifest.wandb_project",
    "intent.manifest.maximum_runtime_hours",
    "intent.manifest.maximum_attempts",
    "intent.workflow_run.run_repository",
    "intent.workflow_run.workflow_repository",
    "intent.workflow_run.workflow_path",
    "intent.workflow_run.workflow_ref",
    "intent.workflow_run.run_attempt",
    "decision.accepted",
    "decision.reason",
    "decision.detail",
    "decision.policy_version",
    "decision.approval_class",
    "decision.approving_environment",
    "decision.manifest_sha256",
    "result.outcome",
    "result.exit_code",
    "result.retention_class",
    "result.wandb_run.entity",
    "result.wandb_run.project",
    "result.checkpoint_survey.outcome",
    "result.checkpoint_survey.objects_seen",
)

#: The required leaves whose paths the data decides: a list index, or a key set the record
#: chooses. These cannot be enumerated in advance and so cannot be reported as absent from
#: both records -- a family with no members is a run that wrote no checkpoints, which is a
#: fact about the run and not a field that went missing. Every member a record does carry is
#: still required to be present on the other side and equal, which is the part that catches
#: a checkpoint appearing in one run and not the other.
REQUIRED_FIELD_FAMILIES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"^intent\.manifest\.command\[\d+\]$"),
    re.compile(r"^decision\.(cost|authorization)\.[a-z_]+$"),
    re.compile(r"^result\.checkpoints\[\d+\]\.(step|epoch|size_bytes)$"),
)

#: The leaves that must be present on both sides and equal, as one set of patterns to ask a
#: path about. Derived rather than written a second time, so the named set and this stay one
#: thing. Checked positively rather than left implicit in the absence of a difference: two
#: records that both stopped carrying a field agree about it, and that is not the same as
#: agreeing.
IDENTICAL_FIELDS: Final[tuple[re.Pattern[str], ...]] = (
    *(re.compile(f"^{re.escape(path)}$") for path in REQUIRED_FIELDS),
    *REQUIRED_FIELD_FAMILIES,
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
    #: Required fields NEITHER run carries, so nothing about them was compared. Recorded
    #: here and not only printed, because this document is what a reader has months later,
    #: and a document listing differences alone cannot distinguish a comparison that found
    #: nothing wrong from one that did not look. The fields present on exactly one side are
    #: deliberately not duplicated here: those appear in ``differences`` against
    #: ``<absent>``, which is the same fact written once.
    unverified: Annotated[tuple[str, ...], BeforeValidator(require_ordered_sequence)] = Field(
        default=(), strict=False
    )

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


def required_field_coverage(left: RecordedRun, right: RecordedRun) -> RequiredFieldCoverage:
    """How much of the required set these two records actually let a comparison check.

    Absence and agreement are different facts, and the whole reason this function exists is
    that the second is what a difference count reports when it means the first. There are
    two absences and they are not the same absence, which is why they leave here separately.
    """
    left_paths = set(left.field_map())
    right_paths = set(right.field_map())
    families = {
        path
        for path in left_paths | right_paths
        if any(one.fullmatch(path) for one in REQUIRED_FIELD_FAMILIES)
    }
    required = set(REQUIRED_FIELDS) | families
    return RequiredFieldCoverage(
        missing=tuple(sorted(required & (left_paths ^ right_paths))),
        # Only a named path can reach this. A family's members are gathered from the two
        # records, so a family neither record populates contributes nothing to `required`
        # and is silently uncheckable -- see the note on REQUIRED_FIELD_FAMILIES for why
        # that is the honest answer for a list index rather than a second hole.
        unverified=tuple(sorted(set(REQUIRED_FIELDS) - left_paths - right_paths)),
    )
