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

**The checkpoint half of the done-condition had the same hole one level down, and it is what
:class:`CheckpointCoverage` is for.** Every checkpoint leaf lives in a family, because its path
carries a list index nobody can name in advance, and the note on
:data:`REQUIRED_FIELD_FAMILIES` used to read an empty family as a run that wrote no
checkpoints. That reading is available only when the record says the prefix was read and was
bare. A run that wrote a checkpoint into a directory neither layout matched records the same
empty list, and the two are opposite: the first is ordinary and the second is a checkpoint
nothing here describes. ``checkpoint_survey.unparsed_directories`` is what tells them apart,
so it is read here, and a directory in it is a finding rather than a family with no members.
Measured on ``run_019fd2c9`` and ``run_019fd2ca``, which each wrote 762 MB into ``step-20/``
and compared clean over ten named differences with no checkpoint field among them.

**What is still not compared, and is now said rather than implied.**
``result.checkpoints[].checksum`` is the only digest in the record and it is not a digest of
the payload. Every checkpoint the lifecycle recorder writes takes it from
``described_listing_checksum``, a SHA-256 over the names and sizes a listing returned, because
the recorder holds ``s3:ListBucket`` and nothing that could open the ``_SUCCESS`` beside the
payload. So two runs whose payloads genuinely differ record the same value in the one field
named for a digest, and a comparison that stopped there would report the two checkpoints as
agreeing. It says so instead.
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
from edullm_platform.evidence import (
    DigestBearingStr,
    EvidenceEnvironment,
    RecordedEventModel,
    SecretFreeStr,
)

__all__ = [
    "CHECKPOINT_URI",
    "IDENTICAL_FIELDS",
    "RECORD_PREFIXES",
    "REQUIRED_FIELDS",
    "REQUIRED_FIELD_FAMILIES",
    "UNPARSED_DIRECTORY",
    "VARIANCE_CAUSES",
    "CheckpointCoverage",
    "CheckpointPayloadReading",
    "ComparedField",
    "FieldDifference",
    "RecordField",
    "RecordedRun",
    "RequiredFieldCoverage",
    "TwoRunComparison",
    "TwoRunEvidence",
    "UnreadableCheckpoint",
    "VarianceCause",
    "agreed_required_fields",
    "cause_for",
    "checkpoint_coverage",
    "compare_runs",
    "flatten",
    "read_run",
    "required_field_coverage",
    "unexplained",
]

#: The three prefixes walked leaf by leaf, in the order a reader wants them.
RECORD_PREFIXES: Final[tuple[str, ...]] = ("intent", "decision", "result")

#: The leaf a survey uses to name a directory under the checkpoint prefix that no layout
#: matched. Asked of the flattened records rather than of a contract model, because
#: everything else here reads a path and this has to read the same way.
UNPARSED_DIRECTORY: Final = re.compile(
    r"^result\.checkpoint_survey\.unparsed_directories\[\d+\]$"
)

#: The leaf that names one recorded checkpoint, counted so a report can say how many
#: checkpoints it had to work with rather than leaving a reader to infer it from the table.
CHECKPOINT_URI: Final = re.compile(r"^result\.checkpoints\[\d+\]\.uri$")


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


class UnreadableCheckpoint(ContractModel):
    """One directory a run wrote that no layout could read a checkpoint out of.

    A model rather than a pair of strings because it is recorded in the comparison document,
    which is what a reader has months later. ``directory`` is the name the survey kept, and
    the name is the whole value of it: ``checkpoint-32`` says HuggingFace in one glance and
    ``step-20`` says a hyphen nobody's matcher wants, where a count says go and look.
    """

    run_id: RunId
    directory: str = Field(min_length=1, max_length=256)


@dataclass(frozen=True)
class CheckpointCoverage:
    """What the checkpoint half of a comparison did, as distinct from what its table shows.

    **Both members exist because a checkpoint row that is not there reads exactly like two
    runs agreeing about checkpoints, and there are two different reasons for it.**

    ``unreadable`` is the one that is a defect. The run wrote objects into a directory the
    recorder could not read a step out of, so the record describes no checkpoint, so every
    member of the checkpoint families is absent from both sides and nothing is compared.
    That is the case the whole spine exists to catch and it produced no output at all.

    ``compared`` is how many checkpoint entries the comparison did walk. It is reported
    even when it is the good number, because the sentence that goes with it is a caveat
    that holds every time: the digest of the bytes is not in the record, so a checkpoint
    was compared on its size, its step and a description of its listing, and not on what is
    in it.
    """

    compared: int
    unreadable: tuple[UnreadableCheckpoint, ...]

    @property
    def is_blocked(self) -> bool:
        """Whether a checkpoint comparison was prevented rather than merely limited."""
        return bool(self.unreadable)


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
        name="the description of one checkpoint's listing",
        pattern=re.compile(r"^result\.checkpoints\[\d+\]\.checksum$"),
        detail=(
            "NOT A DIGEST OF THE PAYLOAD, WHICH IS WHAT THIS CAUSE USED TO CLAIM IT WAS. "
            "The lifecycle recorder holds s3:ListBucket and nothing that can open an "
            "object, so lifecycle_projection.described_listing_checksum computes this over "
            "the names and sizes the listing returned. Floating-point reduction order does "
            "not move it and neither does anything else inside the bytes: two runs whose "
            "payloads differ record the same value here, which is measured on "
            "run_019fd2c9 and run_019fd2ca. What does move it is a checkpoint whose "
            "objects were named or sized differently, which is worth excusing between two "
            "runs of one submission for the same reason the size of a shard is. The "
            "payload's own digest is in the _SUCCESS beside it and is not in this record, "
            "so CheckpointCoverage says so rather than letting this row stand in for it."
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
#: both records. Every member a record does carry is still required to be present on the
#: other side and equal, which is the part that catches a checkpoint appearing in one run
#: and not the other.
#:
#: A FAMILY WITH NO MEMBERS IS NOT ONE FACT, AND READING IT AS ONE IS WHAT LET A RUN THAT
#: SAVED 762 MB PASS AS A RUN THAT SAVED NOTHING. This note used to say an empty checkpoint
#: family was a run that wrote no checkpoints, and called that a fact about the run. It is
#: available as a reading only when the record also says the prefix was read and held
#: nothing. ``checkpoint_survey`` is what separates the two, and
#: :func:`checkpoint_coverage` is what asks it, so the silence here is now only the silence
#: of a run that genuinely saved nothing.

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
    #: The directories a run wrote and no layout could read a checkpoint out of. Recorded
    #: rather than only printed for the reason ``unverified`` is: a document listing
    #: differences alone cannot say that the checkpoint half of this comparison never ran,
    #: and that is the half the done-condition this tool serves is actually about.
    unreadable_checkpoints: Annotated[
        tuple[UnreadableCheckpoint, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(default=(), strict=False)

    @property
    def unexplained_paths(self) -> tuple[str, ...]:
        return tuple(one.path for one in self.differences if one.cause is None)


class CheckpointPayloadReading(ContractModel):
    """What the two checkpoint payloads are, read from the store rather than the record.

    **This exists because the record cannot say it and the comparison therefore cannot
    either.** ``result.checkpoints[].checksum`` is a SHA-256 over the names and sizes a
    listing returned, so two runs holding different weights record one value in it and the
    comparison prints no ``checksum`` row at all. A reader takes that silence for agreement.
    These fields are the store speaking about the bytes, and they say the opposite.

    The checksums are S3's own, read with ``--checksum-mode ENABLED`` on a HEAD, so nothing
    here is a claim this repository computed and nothing had to be downloaded to make it. A
    HEAD is also what makes the reading cheap enough to take every time the record is
    captured, which is what keeps it from going stale beside a comparison that was
    recomputed.

    ``differing_bytes`` and ``first_differing_offset`` are the one part a HEAD cannot
    produce, so they are optional and carry ``measured_by`` saying what did produce them.
    Absent is honest. A zero would read as two identical payloads, which is the reading this
    whole class exists to refuse.
    """

    #: Both payloads, by size. Two fields rather than one, because ``torch.save`` writes a
    #: length fixed by shapes and dtypes, so a difference here is a truncated checkpoint and
    #: is a finding. Recording one number would state the conclusion instead of the reading.
    left_size_bytes: int = Field(ge=0)
    right_size_bytes: int = Field(ge=0)
    #: The CRC32C S3 attests for each payload, base64 as the API returns it.
    left_crc32c: SecretFreeStr = Field(min_length=1, max_length=64)
    right_crc32c: SecretFreeStr = Field(min_length=1, max_length=64)
    differing_bytes: int | None = Field(default=None, ge=0)
    first_differing_offset: int | None = Field(default=None, ge=0)
    measured_by: SecretFreeStr | None = Field(default=None, min_length=1, max_length=512)

    @property
    def payloads_agree(self) -> bool:
        return self.left_crc32c == self.right_crc32c


class TwoRunEvidence(RecordedEventModel):
    """One comparison of two runs, committed so it can be re-checked without the account.

    A ``TwoRunComparison`` on its own is a table of differences and nothing else. Committed
    as evidence it needs three things that table does not carry, and each of them is a way
    the artifact would otherwise mislead the person who finds it.

    **Where it came from.** ``observed_at``, ``source``, ``environment`` and
    ``lineage_bucket``, which is what every other record under ``fixtures/evidence/`` says
    about itself. This is a :class:`~edullm_platform.evidence.RecordedEventModel` and not a
    :class:`~edullm_platform.evidence.FreshEvidenceModel`: two runs happened, and no amount
    of elapsed time makes them stop having happened. A freshness window here would turn a
    settled result red thirty days on for a reason unrelated to any change.

    **What was equal, and not only what differed.** ``agreed`` holds every name in
    :data:`REQUIRED_FIELDS` that both records carry, with the value they share. A document
    listing only differences cannot be told apart from a document produced by a comparison
    that looked at almost nothing, and the manifest digest, the approval class and the exit
    code are the fields the claim actually rests on. They are absent from the table for the
    good reason that they matched.

    **What the pair does not establish.** ``does_not_establish`` is required to hold at
    least one line, which is deliberate. The word a reader brings to this file is
    "reproducible", and it is wider than what two agreeing records support: the payloads
    these two runs wrote differ in most of their bytes, which is ordinary on a GPU and is
    recorded in ``checkpoint_payloads`` rather than left to be discovered. An artifact that
    could be written with nothing in this field would be an artifact whose caveats live in a
    plan nobody opens.
    """

    schema_version: Literal[1]
    source: Literal["aws"]
    environment: EvidenceEnvironment
    lineage_bucket: SecretFreeStr = Field(min_length=1, max_length=256)
    #: The digest both runs' manifests hashed to, which is what makes them two dispatches of
    #: one submission rather than two submissions that resemble each other.
    manifest_sha256: DigestBearingStr = Field(min_length=1, max_length=128)
    establishes: SecretFreeStr = Field(min_length=1, max_length=2048)
    does_not_establish: Annotated[
        tuple[SecretFreeStr, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)
    checkpoint_payloads: CheckpointPayloadReading | None = None
    agreed: Annotated[tuple[RecordField, ...], BeforeValidator(require_ordered_sequence)] = Field(
        min_length=1, strict=False
    )
    comparison: TwoRunComparison

    @model_validator(mode="after")
    def validate_the_manifest_digest_is_the_one_both_records_carry(self) -> Self:
        """A header fact that disagrees with the records under it is worse than no header.

        ``manifest_sha256`` is quoted at the top because it is the sentence's subject, and a
        quoted value nothing checks is a value somebody will eventually edit by hand. Both
        records carry it, the comparison found them equal, so this is asking the document
        whether it agrees with itself.
        """
        recorded = {field.value for field in self.agreed if field.path.endswith("manifest_sha256")}
        if not recorded:
            raise ValueError("no agreed field names a manifest digest")
        if recorded != {json.dumps(self.manifest_sha256)}:
            raise ValueError("manifest_sha256 is not the digest the compared records carry")
        return self


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


def agreed_required_fields(left: RecordedRun, right: RecordedRun) -> tuple[RecordField, ...]:
    """Every required name both records carry with one value, and what that value is.

    The inverse of :func:`compare_runs`, and it is here so that a committed comparison can
    say what matched rather than only what did not. A table of thirteen differences reads
    the same whether the comparison examined two hundred leaves or four, and the fields the
    done-condition actually rests on -- the manifest digest, the approval class, the exit
    code -- are absent from it precisely because they held.

    :data:`REQUIRED_FIELDS` and not the families. A family's members are gathered from the
    data, and ``intent.manifest.command[*]`` is one of them: on this platform's own
    synthetic workload that is five thousand characters of Python per element, which would
    make the artifact mostly a copy of a program nobody reads it for. The named set is
    bounded, is the set :func:`required_field_coverage` reports against, and is the one a
    reader can check the record against by eye.
    """
    left_fields = left.field_map()
    right_fields = right.field_map()
    return tuple(
        RecordField(path=path, value=left_fields[path])
        for path in REQUIRED_FIELDS
        if path in left_fields and left_fields[path] == right_fields.get(path)
    )


def _unparsed_directories(run: RecordedRun) -> tuple[str, ...]:
    """The directory names this run's survey could not read a step out of, in path order.

    Values come back JSON-encoded, so they are decoded here. A value that is not a string
    is skipped rather than rendered: the field is typed to a tuple of strings and anything
    else is a record this function has nothing true to say about, where guessing would put
    a fabricated directory name into a report somebody acts on.
    """
    names: list[str] = []
    for path, value in sorted(run.field_map().items()):
        if UNPARSED_DIRECTORY.fullmatch(path) is None:
            continue
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, str) and decoded:
            names.append(decoded)
    return tuple(names)


def checkpoint_coverage(left: RecordedRun, right: RecordedRun) -> CheckpointCoverage:
    """How much of the checkpoint half of this comparison could run, and how much did.

    THE COUNT IS OVER THE UNION AND NOT THE INTERSECTION, DELIBERATELY. A checkpoint one
    run recorded and the other did not is already a row against ``<absent>``, so counting
    it here as compared is not a second claim -- it is the number of checkpoint entries the
    walk covered, which is what the caveat about the payload digest is scoped to.
    """
    return CheckpointCoverage(
        compared=sum(
            1
            for path in set(left.field_map()) | set(right.field_map())
            if CHECKPOINT_URI.fullmatch(path)
        ),
        unreadable=tuple(
            UnreadableCheckpoint(run_id=run.run_id, directory=name)
            for run in (left, right)
            for name in _unparsed_directories(run)
        ),
    )


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
        # and cannot be reported absent from here. That is the honest answer for a list
        # index and it is not the whole answer for checkpoints, because an empty checkpoint
        # family has one ordinary cause and one that is a defect. checkpoint_coverage is
        # what separates them, and it reads a field this function does not.
        unverified=tuple(sorted(set(REQUIRED_FIELDS) - left_paths - right_paths)),
    )
