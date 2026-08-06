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

**The manifest half of that list is derived from the models now, because a hand-maintained
list of a schema's fields is a second copy of the schema and it went stale.** It named
``intent.manifest.dataset_release``, which :class:`~...manifest.RunManifestV2` replaced with
``inputs``, and named nothing for what replaced it. So a stored v2 record would have been
compared against a field it cannot carry, and ``inputs[].role`` and ``inputs[].reference``
-- which are the whole of what a run says it read -- would have been compared by nothing.
:func:`required_manifest_fields` reads the fields off the model instead, and refuses rather
than skips a field it has no rule for.

**Which version's names a pair is held to is the pair's own answer, and the union is only
over the versions in play.** Holding two v2 records to a v1 field would report it unverified
on every comparison forever, which is a check that can never pass -- the same defect as one
that can never fail, wearing the other face. Holding a v1 record against a v2 one to both
sets is what makes the fields a version bump moved show up as present on one side and absent
from the other, rather than dropping out of the comparison unremarked.

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

**The payload is compared now, and the field this paragraph used to be about is still not the
one that does it.** ``result.checkpoints[].checksum`` reads as the digest and is a SHA-256 over
the names and sizes a listing returned, so two runs whose payloads differ in every byte record
one identical value in it -- measured on ``run_019fd3a1`` and ``run_019fd3a2``, whose 762 MB
checkpoints differ across 94 per cent of their length. ``payload`` is the field that answers,
carrying a digest per object taken from what S3 attests, and it is compared leaf by leaf like
anything else.

**A difference in it is information and is not a finding, which is the one thing here most
worth not getting wrong.** Two runs of one submission on one dataset are expected to hold
different weights, because the order a GPU reduces in is not fixed across two executions. So
the digest has a named cause and moves no exit code, and nothing anywhere refuses, retries or
warns because of it. What the platform proves is the code, the data and the machine shape, and
never the output bytes; the ruling is in ``docs-frank/reference/decisions.md``. The object's
name and size are a different matter and are required to match, because a checkpoint that lost
a shard or wrote a shorter one is a truncated write.

**Absence is split in two, because only one half is a hole.** A record with no payload reading
at all predates the field, which is every result record written before 2026-08-05, and the
report says so and exits as it would have. A record that carries the reading and says it read
no digest reports UNVERIFIED, on the same argument as a required field neither record carries:
nothing was compared, and reporting nothing as agreement is the defect this module exists to
remove.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Final, Literal, Self, TypeGuard, get_args, get_origin

from pydantic import BaseModel, BeforeValidator, Field, model_validator
from pydantic.fields import FieldInfo

from edullm_platform.contracts.base import ContractModel, require_ordered_sequence
from edullm_platform.contracts.identity import RunId
from edullm_platform.contracts.manifest import AnyRunManifest
from edullm_platform.contracts.results import PayloadDigestOutcome
from edullm_platform.evidence import (
    DigestBearingStr,
    EvidenceEnvironment,
    RecordedEventModel,
    SecretFreeStr,
)

__all__ = [
    "CHECKPOINT_URI",
    "IDENTICAL_FIELDS",
    "MANIFEST_MODELS",
    "MANIFEST_PREFIX",
    "MANIFEST_VERSION_FIELD",
    "RECORD_PREFIXES",
    "REQUIRED_FIELDS",
    "REQUIRED_FIELD_FAMILIES",
    "REQUIRED_MANIFEST_FIELDS",
    "UNPARSED_DIRECTORY",
    "VARIANCE_CAUSES",
    "CheckpointCoverage",
    "CheckpointPayloadReading",
    "ComparedField",
    "FieldDifference",
    "RecordField",
    "RecordedRun",
    "RequiredFamily",
    "RequiredFieldCoverage",
    "RequiredManifestFields",
    "TwoRunComparison",
    "TwoRunEvidence",
    "UnattestedPayload",
    "UnreadableCheckpoint",
    "VarianceCause",
    "agreed_required_fields",
    "cause_for",
    "checkpoint_coverage",
    "compare_runs",
    "flatten",
    "manifest_version",
    "read_run",
    "required_field_coverage",
    "required_manifest_fields",
    "required_of",
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

#: The payload outcomes under which a digest of the bytes is actually in the record.
#: Derived from the contract rather than spelled again, so a source added there cannot be
#: silently unrecognised here and counted as an absence.
_READ_PAYLOAD_OUTCOMES: Final[frozenset[str]] = frozenset(
    one.value for one in PayloadDigestOutcome if one.is_read
)


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


class UnattestedPayload(ContractModel):
    """One checkpoint whose record carries a payload reading that read no digest.

    NOT THE SAME THING AS A RECORD WITH NO PAYLOAD READING AT ALL, and the whole reason
    this is separate from ``payloads_absent``. A record written before
    :class:`~edullm_platform.contracts.results.CheckpointPayload` existed says nothing
    about the bytes because nothing could; a record that carries the field and says
    ``refused`` describes a live run whose bytes this platform was not able to read. The
    first is history and the second is a hole, and only the second moves an exit code.
    """

    run_id: RunId
    checkpoint: str = Field(min_length=1, max_length=512)
    #: The outcome the record gave, verbatim: ``refused``, ``not_attempted`` or
    #: ``too_many_objects``. Kept as the record's own word rather than mapped to a sentence,
    #: because which one it is says who has to fix it.
    outcome: str = Field(min_length=1, max_length=64)


@dataclass(frozen=True)
class CheckpointCoverage:
    """What the checkpoint half of a comparison did, as distinct from what its table shows.

    **Every member exists because a checkpoint row that is not there reads exactly like two
    runs agreeing about checkpoints, and there are several different reasons for it.**

    ``unreadable`` is the one that is a defect. The run wrote objects into a directory the
    recorder could not read a step out of, so the record describes no checkpoint, so every
    member of the checkpoint families is absent from both sides and nothing is compared.
    That is the case the whole spine exists to catch and it produced no output at all.

    ``compared`` is how many checkpoint entries the comparison did walk.

    **The other three are the payload, and they are three because the caveat that used to
    go with ``compared`` was unconditional and is not any more.** The record now carries a
    digest of what is in a checkpoint, so a comparison can say which of these it is.
    ``payloads_read`` is the good one: both records carry a digest of the bytes and the
    table above says whether they matched. ``payloads_absent`` counts entries written
    before the field existed, which is a statement about history and not about the runs.
    ``unattested`` is a record that carries the field and says it read nothing -- the only
    one of the three that is a hole in a live run, and the only one that reports UNVERIFIED.
    """

    compared: int
    unreadable: tuple[UnreadableCheckpoint, ...]
    payloads_read: int = 0
    payloads_absent: int = 0
    unattested: tuple[UnattestedPayload, ...] = ()

    @property
    def is_blocked(self) -> bool:
        """Whether a checkpoint comparison was prevented rather than merely limited."""
        return bool(self.unreadable)

    @property
    def payloads_unverified(self) -> bool:
        """Whether a record said in so many words that it did not read the bytes."""
        return bool(self.unattested)


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
            "runs of one submission for the same reason the size of a shard is. The digest "
            "of the payload is in the `payload` field beside this one as of 2026-08-05, "
            "and 'the bytes a GPU wrote' is its cause; this row must not be read as "
            "standing in for that one."
        ),
    ),
    VarianceCause(
        name="the bytes a GPU wrote",
        pattern=re.compile(r"^result\.checkpoints\[\d+\]\.payload\.objects\[\d+\]\.digest$"),
        detail=(
            "A DIFFERENCE HERE IS THE INFORMATION THIS FIELD WAS ADDED TO CARRY, AND IT IS "
            "NOT A FAULT. contracts/results.py::CheckpointPayload records a digest of the "
            "payload -- S3's attested CRC32C where the recorder may read it, the listing's "
            "entity tag otherwise -- so two runs holding different weights now say so, "
            "where the checksum beside it says nothing because it hashes the listing. Two "
            "runs of one submission on one dataset are EXPECTED to differ here: the order a "
            "GPU reduces a sum in is not fixed across two executions, so identical code on "
            "identical data produces different bytes, and the platform proves the code, the "
            "data and the machine shape rather than the output. That is the ruling in "
            "docs-frank/reference/decisions.md. Named as a cause so it appears in the table "
            "and moves no exit code -- the change adds information and adds no gate. What "
            "is NOT excused by it is the object's name or its size, which are in "
            "REQUIRED_FIELD_FAMILIES: a checkpoint missing a shard or holding a shorter one "
            "is a truncated write and is a finding."
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

@dataclass(frozen=True)
class RequiredFamily:
    """One required leaf set whose member paths the record decides, not the schema.

    ``label`` is what a report prints when the family has no members at all and one was
    required. It is not a path and cannot be: the whole reason a family is a family is
    that its members carry an index nobody can name in advance. ``intent.manifest.inputs[]``
    is a set that should not be empty, said in the one form that is honest about not
    knowing how many should be in it.
    """

    label: str
    pattern: re.Pattern[str]
    #: Whether a record that carries no member of this family is malformed. True where the
    #: model cannot produce a record without one -- a required field, and for a sequence a
    #: ``min_length`` of at least one. THIS IS WHAT GIVES A FAMILY TEETH, and without it a
    #: family is only ever checked against members some record happened to carry, so two
    #: records that both dropped the whole block are compared on nothing and report nothing.
    must_have_a_member: bool


@dataclass(frozen=True)
class RequiredManifestFields:
    """What one manifest version requires of a record written against it."""

    names: tuple[str, ...]
    families: tuple[RequiredFamily, ...]


#: Where a run manifest sits inside a flattened intent record.
MANIFEST_PREFIX: Final = "intent.manifest"

#: The field that says which model a stored manifest was written against. Excluded from the
#: required set on purpose and it is the only exclusion here: it is what selects that set, so
#: requiring it of itself is circular. Nothing is lost by the exclusion. Two records at two
#: versions differ at this leaf, no cause in :data:`VARIANCE_CAUSES` matches it, and an
#: unexplained difference is the loudest answer this module has -- louder than the required
#: set would have been.
MANIFEST_VERSION_FIELD: Final = "schema_version"


def _optional_member(annotation: object) -> object | None:
    """The one thing beside ``None`` in a nullable annotation, or ``None`` for anything else.

    ``tuple[str, ...]`` reads as two arguments and neither is ``NoneType``, so a sequence
    does not answer here and is asked about separately.
    """
    arguments: tuple[object, ...] = get_args(annotation)
    beside_none = [one for one in arguments if one is not type(None)]
    if len(beside_none) == len(arguments):
        return None
    if len(beside_none) != 1:
        raise ValueError(f"{annotation!r} is a union this comparison has no rule for")
    return beside_none[0]


def _sequence_member(annotation: object) -> object | None:
    """The element type of a homogeneous tuple annotation, or ``None`` for anything else."""
    if get_origin(annotation) is not tuple:
        return None
    arguments: tuple[object, ...] = get_args(annotation)
    return arguments[0]


def _is_model(annotation: object) -> TypeGuard[type[BaseModel]]:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _minimum_members(field: FieldInfo) -> int:
    lengths = [
        one.min_length
        for one in field.metadata
        if isinstance(getattr(one, "min_length", None), int)
    ]
    return max(lengths, default=0)


def _scalar_leaves(model: type[BaseModel], under: str) -> tuple[str, ...]:
    """Every leaf name of a nested model, refusing one this deriver cannot flatten.

    REFUSED RATHER THAN SKIPPED, which is the whole point. A nested field the rules below
    have no case for is a field that would silently leave the required set, and a required
    set that quietly shrank is the defect this module was written to remove. Import fails
    instead, so the day somebody puts a list inside :class:`~...manifest.RunInput` the suite
    goes red rather than the comparison going quiet.
    """
    leaves: list[str] = []
    for name, field in model.model_fields.items():
        annotation = field.annotation
        if (
            _sequence_member(annotation) is not None
            or _is_model(annotation)
            or _is_model(_optional_member(annotation))
        ):
            raise ValueError(
                f"{under} holds {name!r}, which is not a scalar leaf; the required-field "
                "deriver in run_comparison.py needs a rule for it before it can be compared"
            )
        leaves.append(name)
    return tuple(sorted(leaves))


def required_manifest_fields(model: type[ContractModel]) -> RequiredManifestFields:
    """What a record written against one manifest model must carry, read off the model.

    **HAND-MAINTAINED UNTIL 2026-08-06, AND THE HAND WAS BEHIND THE SCHEMA.** The list named
    ``intent.manifest.dataset_release``, which :class:`~...manifest.RunManifestV2` does not
    carry, and named nothing at all for the ``inputs`` block that replaced it. So the first
    v2 record stored would have been compared against a field it cannot have, and the two
    leaves that say what a run actually read would have been compared by nothing -- which is
    worse than the red test, because a comparison that silently checks less still passes.

    Four rules, and every field of the model falls under exactly one of them. A field that
    falls under none raises, for the reason :func:`_scalar_leaves` refuses one.

    - A scalar is a name. Its path is fixed by the schema, so the comparison can look for it
      in a record that does not carry it, which is the only reading under which "both records
      dropped this" is a sentence the tool can say.
    - A sequence is a family, because its members carry an index the data chooses.
    - A sequence of models is a family over that model's own leaves, so
      ``inputs[].role`` and ``inputs[].reference`` are each required rather than the block
      being required to merely exist.
    - A nullable model is a family too, because a null flattens to one leaf and a value
      flattens to several: the leaf set is data-dependent, which is what a family is for.
    """
    names: list[str] = []
    families: list[RequiredFamily] = []
    for name, field in model.model_fields.items():
        if name == MANIFEST_VERSION_FIELD:
            continue
        path = f"{MANIFEST_PREFIX}.{name}"
        member = _sequence_member(field.annotation)
        if member is not None:
            leaves = "|".join(_scalar_leaves(member, path)) if _is_model(member) else ""
            families.append(
                RequiredFamily(
                    label=f"{path}[]",
                    pattern=re.compile(
                        rf"^{re.escape(path)}\[\d+\]\.({leaves})$"
                        if leaves
                        else rf"^{re.escape(path)}\[\d+\]$"
                    ),
                    must_have_a_member=field.is_required() and _minimum_members(field) >= 1,
                )
            )
            continue
        nullable = _optional_member(field.annotation)
        if nullable is not None and _is_model(nullable):
            families.append(
                RequiredFamily(
                    label=path,
                    pattern=re.compile(rf"^{re.escape(path)}(\..+)?$"),
                    # A null is still a leaf, so a required nullable model always leaves
                    # something behind and a record carrying nothing under this name is a
                    # record the model could not have written.
                    must_have_a_member=field.is_required(),
                )
            )
            continue
        names.append(path)
    return RequiredManifestFields(names=tuple(names), families=tuple(families))


#: Every manifest model a stored record may have been written against, by the version it
#: declares. Read out of ``AnyRunManifest`` rather than listed here, so a third version
#: added to ``contracts/manifest.py`` is one this comparison requires fields of on the day it
#: lands rather than on the day somebody remembers this file.
MANIFEST_MODELS: Final[Mapping[int, type[ContractModel]]] = {
    get_args(model.model_fields[MANIFEST_VERSION_FIELD].annotation)[0]: model
    for model in get_args(AnyRunManifest)
}

#: What each of those versions requires, derived once at import.
REQUIRED_MANIFEST_FIELDS: Final[Mapping[int, RequiredManifestFields]] = {
    version: required_manifest_fields(model) for version, model in MANIFEST_MODELS.items()
}

#: The required leaves outside the manifest, which no model in this repository describes as
#: one shape: an intent record is a manifest plus a header, and the decision and result
#: records are walked as documents rather than parsed. Hand-maintained, deliberately, and
#: kept apart from the derived block above so that "which of these is a list somebody has to
#: remember to edit" has an answer.
_HEADER_FIELDS: Final[tuple[str, ...]] = (
    "attempt_count",
    "intent.manifest_sha256",
    "intent.submitter",
    "intent.approving_environment",
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

#: Every required leaf whose path is known before a record is read, across every manifest
#: version. Held as strings rather than patterns because a pattern can be asked whether a
#: path it was handed matches and cannot be asked what it wanted.
#:
#: THE UNION AND NOT THE INTERSECTION. A name one version requires and the other does not
#: know -- ``dataset_release`` today -- stays here rather than being dropped for not being
#: universal, because dropping it is how a field stops being compared without anybody
#: deciding that it should. Which of these names a given pair is actually held to is
#: :func:`required_field_coverage`'s answer and it is narrower: a record says which version
#: it was written against, and requiring a v2 record to carry a v1 field would be a check
#: that can never pass, which is the same defect as one that can never fail wearing the
#: other face.
REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    *_HEADER_FIELDS,
    *dict.fromkeys(
        name for required in REQUIRED_MANIFEST_FIELDS.values() for name in required.names
    ),
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
#:
#: The manifest's families answer the same question a different way, because a manifest
#: model can say that a block is never empty. ``command`` and ``inputs`` both declare
#: ``min_length=1``, so a record carrying no member of either is malformed rather than
#: sparse, and :class:`RequiredFamily` carries that as ``must_have_a_member``. There is no
#: such statement available about ``result.checkpoints[]`` and there should not be: a run
#: that saved nothing is ordinary.
_RECORD_FAMILIES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"^decision\.(cost|authorization)\.[a-z_]+$"),
    re.compile(r"^result\.checkpoints\[\d+\]\.(step|epoch|size_bytes)$"),
    # The payload reading, minus the digest. ``outcome`` is here because a CRC32C and an
    # entity tag are different functions of the same bytes: two runs read by different
    # sources cannot be compared at all, and a digest row between them would be noise
    # presented as a finding. ``name`` and ``size_bytes`` are here because a checkpoint that
    # lost a shard or wrote a shorter one is a truncated write, which is the finding the
    # digest's cause deliberately does not excuse.
    re.compile(r"^result\.checkpoints\[\d+\]\.payload\.outcome$"),
    re.compile(r"^result\.checkpoints\[\d+\]\.payload\.objects\[\d+\]\.(name|size_bytes)$"),
)

REQUIRED_FIELD_FAMILIES: Final[tuple[re.Pattern[str], ...]] = (
    *{
        family.pattern.pattern: family.pattern
        for required in REQUIRED_MANIFEST_FIELDS.values()
        for family in required.families
    }.values(),
    *_RECORD_FAMILIES,
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
    #: The checkpoints whose record carries a payload reading that read no digest. Defaulted
    #: for the same reason ``CheckpointManifest.payload`` is: the comparison documents
    #: already committed were written before this existed and must still read. An empty
    #: tuple here means no record said it failed to read the bytes -- not that every record
    #: read them, which is what ``payloads_read`` in the printed report is for.
    unattested_payloads: Annotated[
        tuple[UnattestedPayload, ...], BeforeValidator(require_ordered_sequence)
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
    walk covered, which is what the payload tally below is scoped to.

    **The payload tally is per entry and asks both records, because a digest one run
    recorded and the other did not compares nothing.** An entry counts as read only when
    both sides carry a payload outcome that got a digest; anything less lands in
    ``payloads_absent`` or, if a record named a reason, in ``unattested``.
    """
    entries = sorted(
        path
        for path in set(left.field_map()) | set(right.field_map())
        if CHECKPOINT_URI.fullmatch(path)
    )
    read = 0
    absent = 0
    unattested: list[UnattestedPayload] = []
    for uri_path in entries:
        stem = uri_path.removesuffix(".uri")
        outcomes = {
            run.run_id: run.field_map().get(f"{stem}.payload.outcome") for run in (left, right)
        }
        for run in (left, right):
            recorded = outcomes[run.run_id]
            if recorded is None or _is_read_outcome(recorded):
                continue
            unattested.append(
                UnattestedPayload(
                    run_id=run.run_id,
                    checkpoint=run.field_map().get(uri_path, stem).strip('"'),
                    outcome=recorded.strip('"'),
                )
            )
        if all(value is not None and _is_read_outcome(value) for value in outcomes.values()):
            read += 1
        elif all(value is None for value in outcomes.values()):
            absent += 1
    return CheckpointCoverage(
        compared=len(entries),
        unreadable=tuple(
            UnreadableCheckpoint(run_id=run.run_id, directory=name)
            for run in (left, right)
            for name in _unparsed_directories(run)
        ),
        payloads_read=read,
        payloads_absent=absent,
        unattested=tuple(unattested),
    )


def _is_read_outcome(encoded: str) -> bool:
    """Whether a recorded payload outcome is one a digest actually arrived under.

    Asked of the JSON-encoded leaf rather than of the enum, because everything in this
    module reads a flattened record and a record is the only thing here that can be older
    than the code reading it. An outcome this version has never heard of is treated as not
    read, which is the safe direction: it reports UNVERIFIED rather than quietly counting
    an unknown word as a digest.
    """
    return encoded.strip('"') in _READ_PAYLOAD_OUTCOMES


def manifest_version(run: RecordedRun) -> int | None:
    """Which manifest model this record says it was written against, or None.

    None is both "the leaf is not there" and "the leaf holds something no model declares",
    and the caller treats them alike because the safe answer to both is the same: require
    everything either version names, and report what is absent.
    """
    encoded = run.field_map().get(f"{MANIFEST_PREFIX}.{MANIFEST_VERSION_FIELD}")
    if encoded is None:
        return None
    try:
        declared = json.loads(encoded)
    except json.JSONDecodeError:
        return None
    return declared if declared in MANIFEST_MODELS else None


def required_of(left: RecordedRun, right: RecordedRun) -> RequiredManifestFields:
    """What the manifest half of the required set is for this pair, by their own versions.

    **THE UNION OF THE VERSIONS IN PLAY, AND NOT THE UNION OF EVERY VERSION.** A pair of v2
    records held to ``dataset_release`` would report it unverified on every comparison
    forever, which is a check that can never pass and would spend the meaning of the
    unverified answer on a permanent false alarm. A v1 record against a v2 one is held to
    both sets, so the fields the version change moved appear as present on one side and
    absent from the other rather than dropping out of the comparison -- which is the whole
    difference between a version bump being visible and being silent.

    A record whose version this cannot read is held to everything either version names. It
    is malformed -- both models require the field -- and requiring more of it is the
    direction that reports rather than the direction that shrugs.
    """
    declared = [manifest_version(run) for run in (left, right)]
    selected = (
        sorted(MANIFEST_MODELS)
        if None in declared
        else sorted({one for one in declared if one is not None})
    )
    required = [REQUIRED_MANIFEST_FIELDS[version] for version in selected]
    return RequiredManifestFields(
        names=tuple(dict.fromkeys(name for one in required for name in one.names)),
        families=tuple(
            {
                family.pattern.pattern: family
                for one in required
                for family in one.families
            }.values()
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
    manifest = required_of(left, right)
    patterns = (*(family.pattern for family in manifest.families), *_RECORD_FAMILIES)
    members = {
        path for path in left_paths | right_paths if any(one.fullmatch(path) for one in patterns)
    }
    names = set(_HEADER_FIELDS) | set(manifest.names)
    # A family the model says is never empty, and which neither record put anything in.
    # Reported by label because there is no path to report: the member that should have
    # been there is the one nobody wrote. Without this a family is only ever checked
    # against members some record happened to carry, so two v2 records that both dropped
    # `inputs` would be compared on nothing at all and the report would say nothing at all.
    empty = {
        family.label
        for family in manifest.families
        if family.must_have_a_member
        and not any(family.pattern.fullmatch(path) for path in left_paths | right_paths)
    }
    return RequiredFieldCoverage(
        missing=tuple(sorted((names | members) & (left_paths ^ right_paths))),
        # A named path and a family the model requires a member of. A family that may
        # legitimately be empty cannot reach this: its members are gathered from the two
        # records, so one neither record populates contributes nothing. That is the honest
        # answer for `result.checkpoints[]`, where an empty family has one ordinary cause
        # and one that is a defect -- checkpoint_coverage is what separates those, and it
        # reads a field this function does not.
        unverified=tuple(sorted((names - left_paths - right_paths) | empty)),
    )
