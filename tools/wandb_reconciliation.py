"""Whether the W&B run a result record names is a run W&B actually has.

``ResultManifest.wandb_run`` is composed by
:func:`edullm_platform.lifecycle_projection.wandb_run_for` out of the entity and the project
the container was handed, plus the run id as the run's name. Nothing asks W&B whether that
run is there, and the docstring on that function says so in as many words: "This says where
the run would have reported, not that it did." Read live on 2026-08-04, 42 of the 102 result
records carry a reference and 28 of the 42 name a run that is not in the entity -- 25 of them
because the workload never logged at all, three because it logged under a different name in
the same project. Anybody reading a lineage record to answer "did this run log" gets `yes`
where the truth is `no`, and follows a reference to a page that is not there.

**THE FIX IS NOT IN ``wandb_run_for`` AND THAT IS THE WHOLE REASON THIS MODULE EXISTS.** That
function runs inside the lifecycle recorder, projecting one Batch event into the records the
event implies. A network call there is a network call inside an event handler with a timeout
and a dead-letter queue behind it: an outage at W&B would stop the event, the attempt and the
result being written for a run that demonstrably happened, to improve a field that names
where its charts would have been. The module already argues the same case against
``batch:DescribeJobs`` and against raising on a malformed reference. So the reference stays a
naming contract, and the question of whether the named run exists is asked afterwards, by
something that may fail without losing anything.

**Post-terminal, and it reads the terminal record rather than watching for one.** A result
manifest exists only for a run that reached a terminal state with an attempt behind it, so
the set of result records *is* the set of runs to ask about. Nothing here subscribes to
anything or holds state between runs.

**WHERE IT RUNS: THE AUDIT BOARD, NOT A LAMBDA, AND NOT A JOB OF ITS OWN.**
``tools/visibility_board.py`` already runs on ``audit.yml`` under
``sbsandbox-intern-edullm-audit-reader``, already resolves the W&B key out of Secrets
Manager, already reads *every* run in the entity across every project, and already syncs the
lineage bucket. So this reconciliation costs no role, no builder, no release record, no
stack, no CI step, no entry in either deployment verifier -- and no second call to W&B, since
the existence question is answered from the entity listing the board has already fetched. A
third Lambda would have cost all of those and would have had to be given an egress path and a
secret read that the recorder's role deliberately does not hold.

**WHAT IT WRITES, AND WHY NOT INTO THE RECORD IT IS ABOUT.** The result manifest is the
natural home and it cannot be amended. ``infra/lineage-bucket.yaml`` denies any
``s3:PutObject`` that does not carry ``If-None-Match``, every writer sends ``*``, and the
template states the consequence directly: "a lineage record is written once ... Anything that
needs to reshape these records writes the result somewhere else."

A new write-once key beside it -- ``wandb-observation/{run_id}.json`` -- would not violate
that, and is still wrong, for a reason that is about this observation rather than about the
grant. The answer has a third state. A write-once store would freeze whatever was true the
first time anybody looked, so one night W&B is unreachable would permanently record "nobody
could tell" for every run that ended that day, and nothing could ever correct it. An
observation that can be `unreachable` has to be re-askable, which means it belongs somewhere
recomputed rather than somewhere sealed.

So the answer is written into the audit board -- the step summary a person reads and, with
``--wandb-observations``, a JSON file a machine reads -- recomputed from the immutable result
records and a live read of W&B every night. It is idempotent, it corrects itself the night
after an outage, and it adds no writer to a role whose whole property is that it cannot
change what it is checking.

**THREE STATES, BECAUSE COLLAPSING THE THIRD MANUFACTURES THE DEFECT IN REVERSE.** The run is
there, the run is not there, or W&B could not be reached. Reading the third as the second
would print "this record names a run that does not exist" for all 42 references on the
morning a credential lapsed, which is exactly the false record this exists to remove, pointed
the other way. It is the same distinction ``verify_wandb_credential.verdict_for`` draws
between a refusal and an outage, and the same one
:class:`~edullm_platform.contracts.results.CheckpointListingOutcome` draws between a prefix
that was read and was bare and one nobody was allowed to look at.

**AN ABSENT REFERENCE IS NOT ALWAYS AN UNLOGGED RUN, AND THE DIFFERENCE IS REPORTED RATHER
THAN GUESSED AT.** Three of the 28 name a run that is not there while some *other* W&B run in
the entity carries the platform run id -- the ``$EDULLM_RUN_ID`` and ``-died`` spellings
``tools/visibility_board.py:run_id_of`` already grades as ``DERIVED``. Those runs logged. The
record is wrong about *where*, not about *whether*, and saying "this run never logged" about
one of them would be a false accusation aimed at a submitter who did nothing wrong. So an
absent observation carries where the run was found if it was found anywhere, and
:func:`never_logged` is the narrower list -- the runs for which nothing in the entity claims
the run id at all.

**IT REPORTS AND IT DOES NOT COMPEL.** A workload that ignores the project, the entity and
the key the platform hands it is not a platform fault, and nothing here fails a run for not
logging or tries to make one log. Making the record honest about it is the platform's job;
making the workload log is the researcher's.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Protocol

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from edullm_platform.contracts.results import ResultManifest

__all__ = [
    "OBSERVATION_SCHEMA_VERSION",
    "RESULT_PREFIX",
    "LoggedRun",
    "ReferenceReading",
    "WandbObservation",
    "WandbReference",
    "WandbRunPresence",
    "never_logged",
    "observation_document",
    "observe",
    "presence_counts",
    "read_references",
    "render_section",
]

#: The lineage prefix a reference is read out of. Named here because this module is what
#: makes the board sync it: the cost report reads ``intent/`` and ``attempt/`` and the
#: checkpoint reconciliation reads ``intent/`` and ``result/``, so the audit reader role
#: already grants this one and asking for it costs no IAM change. It reaches the role check
#: through ``visibility_board.REQUIRED_LINEAGE_PREFIXES``, which
#: ``tests/test_audit_workflow.py`` derives the expected grant from rather than restating
#: -- the arrangement that stopped ``attempt/`` being missing for months.
RESULT_PREFIX: Final = "result"

#: The shape of the machine-readable answer. Versioned because something else may come to
#: read it, and an unversioned document is one nobody can tell has changed.
OBSERVATION_SCHEMA_VERSION: Final = 1


class WandbRunPresence(StrEnum):
    """Whether the run a lineage record names is a run W&B has.

    Three values and not a boolean. The module docstring gives the argument at length; the
    short version is that "W&B says there is no such run" and "nobody could ask W&B" are
    opposite statements, and a board that printed the second as the first would file 42
    false findings on the morning a key lapsed.
    """

    #: A run with this name is in this entity and this project. The reference resolves.
    PRESENT = "present"
    #: The entity was read in full and no run of that name is in that project. The reference
    #: names a page that is not there, whether or not the run logged somewhere else.
    ABSENT = "absent"
    #: W&B could not be read, so nothing is claimed about this reference either way.
    UNREACHABLE = "unreachable"


class LoggedRun(Protocol):
    """One run as W&B reported it, described so this module needs no W&B client.

    Structural rather than imported. ``tools/visibility_board.py`` already reads the entity
    into a frozen dataclass of exactly this shape, and taking it through a Protocol is what
    lets the existence question be answered from a listing that has already been fetched
    instead of from a second round trip. A test supplies its own instances and gets the same
    code path the audit takes.
    """

    @property
    def project(self) -> str: ...

    @property
    def path(self) -> str: ...

    @property
    def display_name(self) -> str: ...

    @property
    def run_id(self) -> str | None: ...


@dataclass(frozen=True)
class WandbReference:
    """What one result record claims about where its run reported.

    ``name`` is the third field of :class:`~edullm_platform.contracts.results.WandbRunRef`,
    which is spelled ``run_id`` in the contract and holds the *name* a reader searches W&B
    with rather than the eight-character id W&B mints for the URL. That is why there is no
    ``url`` property here: the reference has never carried enough to build one, which is the
    same reason ``submit-run.yml`` prints a name to search for rather than a link.
    """

    run_id: str
    entity: str
    project: str
    name: str
    #: What the result record says the run ended as, carried so a reader can tell a
    #: succeeded run that logged nothing from a failed one that never got the chance.
    outcome: str

    @property
    def described(self) -> str:
        return f"{self.entity}/{self.project}/{self.name}"


@dataclass(frozen=True)
class WandbObservation:
    """One reference, and what W&B said about it.

    ``found_at`` and ``found_as`` are set only on an :attr:`WandbRunPresence.ABSENT`
    observation whose platform run id is claimed by some other run in the entity. They are
    what keeps "this record points at nothing" apart from "this run never logged", and the
    two need different people: the first is a record to stop trusting, the second is a
    workload that ignored the project it was handed.
    """

    reference: WandbReference
    presence: WandbRunPresence
    found_at: str | None = None
    found_as: str | None = None

    @property
    def names_nothing(self) -> bool:
        """The reference does not resolve, whatever else is true of the run."""
        return self.presence is WandbRunPresence.ABSENT

    @property
    def logged_nowhere(self) -> bool:
        """Nothing in the entity claims this run id, by any spelling.

        Narrower than :attr:`names_nothing` on purpose. A run that logged under
        ``$EDULLM_RUN_ID`` is unfindable and is not unlogged, and reporting it as unlogged
        would accuse a submitter of turning logging off when they did not.
        """
        return self.names_nothing and self.found_at is None


@dataclass(frozen=True)
class ReferenceReading:
    """Every reference in a result tree, and what the tree cost to read.

    ``unparsed`` is carried rather than dropped, for the reason
    ``tools/report_run_costs.read_records`` gives: a lineage store producing records this
    tree cannot read is a defect in the recorder, and a report that quietly described the
    readable subset would hide exactly that.
    """

    references: tuple[WandbReference, ...]
    results_read: int
    without_reference: int
    unparsed: int


def _document(path: Path) -> object:
    """One stored record, unwrapped.

    A record is sometimes a JSON string holding JSON, because the state machine writes the
    handler's canonical bytes rather than re-encoding them. Both spellings are in the
    committed fixtures for the same prefix.
    """
    loaded: object = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(loaded, str):
        try:
            loaded = json.loads(loaded)
        except ValueError:
            return loaded
    return loaded


def read_references(root: Path) -> ReferenceReading:
    """Every W&B reference in a synced ``result/`` tree.

    A tree that is not there raises rather than reading as a tree holding nothing, which is
    the same refusal ``tools/report_run_costs._load`` makes and the same reason. "102 result
    records were read and none of them names a W&B run" and "nobody synced the prefix" are
    opposite statements, and a reading has no room to carry the difference -- the caller
    holds it, as ``None``, and reports it as a gap.
    """
    directory = root / RESULT_PREFIX
    if not directory.is_dir():
        raise FileNotFoundError(f"no {RESULT_PREFIX}/ directory under {root}")
    references: list[WandbReference] = []
    results_read = 0
    without = 0
    unparsed = 0
    for path in sorted(directory.rglob("*.json")):
        try:
            document = _document(path)
        except (OSError, ValueError):
            unparsed += 1
            continue
        try:
            record = ResultManifest.model_validate(document)
        except ValueError:
            unparsed += 1
            continue
        results_read += 1
        if record.wandb_run is None:
            # Not a finding. A CPU job admitted before ``WANDB_PROJECT`` was set carries no
            # reference, and a record that claims nothing cannot claim anything false.
            without += 1
            continue
        references.append(
            WandbReference(
                run_id=record.run_id,
                entity=record.wandb_run.entity,
                project=record.wandb_run.project,
                name=record.wandb_run.run_id,
                outcome=str(record.outcome),
            )
        )
    return ReferenceReading(
        references=tuple(references),
        results_read=results_read,
        without_reference=without,
        unparsed=unparsed,
    )


def observe(
    references: Sequence[WandbReference], runs: Sequence[LoggedRun] | None
) -> tuple[WandbObservation, ...]:
    """Ask the entity listing about each reference, or say that nobody asked.

    ``runs is None`` is the third state and is the reason this takes an optional sequence
    rather than defaulting to an empty one. An empty entity is a claim -- every reference in
    it is false -- and an unread entity is not, and the two are one character apart.

    A reference resolves when a run in *that project* carries *that display name*, which is
    what a reader following the record would look for. A run that logged into another
    project, or under another name, does not make the reference true; it makes the record
    wrong about where, and that is recorded beside the observation rather than folded into
    it.
    """
    if runs is None:
        return tuple(
            WandbObservation(reference=reference, presence=WandbRunPresence.UNREACHABLE)
            for reference in references
        )

    named: dict[tuple[str, str], LoggedRun] = {}
    claiming: dict[str, LoggedRun] = {}
    for run in runs:
        named.setdefault((run.project, run.display_name), run)
        if run.run_id is not None:
            claiming.setdefault(run.run_id, run)

    observations: list[WandbObservation] = []
    for reference in references:
        if (reference.project, reference.name) in named:
            observations.append(
                WandbObservation(reference=reference, presence=WandbRunPresence.PRESENT)
            )
            continue
        elsewhere = claiming.get(reference.run_id)
        observations.append(
            WandbObservation(
                reference=reference,
                presence=WandbRunPresence.ABSENT,
                found_at=(
                    None if elsewhere is None else f"{elsewhere.project}/{elsewhere.path}"
                ),
                found_as=None if elsewhere is None else elsewhere.display_name,
            )
        )
    return tuple(observations)


def presence_counts(observations: Sequence[WandbObservation]) -> dict[str, int]:
    """How many references landed in each of the three states, every state named.

    Zeroes are kept rather than omitted. A document whose ``unreachable`` key is missing and
    one whose ``unreachable`` is ``0`` read the same to a person and differently to anything
    parsing it, and the second is the one that says the question was asked.
    """
    counts = {state.value: 0 for state in WandbRunPresence}
    for observation in observations:
        counts[observation.presence.value] += 1
    return counts


def never_logged(observations: Sequence[WandbObservation]) -> tuple[WandbObservation, ...]:
    """The references behind which nothing in the entity claims the run, sorted by run id.

    This is the list somebody actually wants: the runs that ran, were handed a project, an
    entity and a key, and logged nowhere anybody can find. It is a report and not a lever --
    nothing on this platform fails a run for it, and nothing should.
    """
    return tuple(
        sorted(
            (entry for entry in observations if entry.logged_nowhere),
            key=lambda entry: entry.reference.run_id,
        )
    )


def observation_document(
    observations: Sequence[WandbObservation],
    *,
    reading: ReferenceReading,
    entity: str,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """The machine-readable answer, for a caller that asked for one.

    Carries the population as well as the observations, because a count of false references
    means nothing without the number of records it was taken over -- and the denominator is
    exactly what the second half of this board's reconciliation exists to stop moving
    silently.
    """
    return {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "observed_at": (observed_at or datetime.now(tz=UTC)).isoformat(),
        "entity": entity,
        "results_read": reading.results_read,
        "results_without_reference": reading.without_reference,
        "results_unparsed": reading.unparsed,
        "counts": presence_counts(observations),
        "observations": [
            {
                "run_id": entry.reference.run_id,
                "entity": entry.reference.entity,
                "project": entry.reference.project,
                "name": entry.reference.name,
                "outcome": entry.reference.outcome,
                "presence": entry.presence.value,
                "found_at": entry.found_at,
                "found_as": entry.found_as,
            }
            for entry in sorted(observations, key=lambda entry: entry.reference.run_id)
        ],
    }


def _unreachable_section(observations: Sequence[WandbObservation]) -> list[str]:
    return [
        "## Whether the lineage records name W&B runs that exist",
        "",
        (
            f"Not asked. W&B was not read, so all {len(observations)} reference(s) in the "
            "result records are recorded as `unreachable` rather than as false. Reading an "
            "outage as an absence would print every one of them as a record naming a run "
            "that does not exist, which is the same false record this reconciliation "
            "removes, pointed the other way."
        ),
        "",
    ]


def render_section(
    observations: Sequence[WandbObservation],
    *,
    reading: ReferenceReading,
    entity: str,
) -> list[str]:
    """The reconciliation as the board prints it, findings first.

    A section rather than a table of everything. The present references are a count, because
    a record that turned out to be true is the state every record is supposed to be in and a
    page listing them buries the rows somebody has to act on.
    """
    counts = presence_counts(observations)
    if counts[WandbRunPresence.UNREACHABLE.value]:
        return _unreachable_section(observations)

    false = sorted(
        (entry for entry in observations if entry.names_nothing),
        key=lambda entry: (entry.found_at is not None, entry.reference.run_id),
    )
    unlogged = never_logged(observations)
    lines = [
        "## Whether the lineage records name W&B runs that exist",
        "",
        (
            f"{reading.results_read} result record(s) were read and {len(observations)} of "
            f"them name a W&B run under `{entity}`. "
            f"{counts[WandbRunPresence.PRESENT.value]} of those references resolve and "
            f"{len(false)} name a run W&B does not have."
        ),
        "",
        (
            "A reference is composed from the entity and the project the container was "
            "handed, plus the run id as the run's name, and nothing asks W&B whether the "
            "run is there -- "
            "`src/edullm_platform/lifecycle_projection.py:wandb_run_for` says so itself, "
            "and putting the question there would mean a network call inside the event "
            "recorder. So it is asked here instead, once a run is over, against the entity "
            "listing this board has already fetched."
        ),
        "",
    ]
    if not false:
        lines += [
            "Every reference resolves, which is the state they are all supposed to be in.",
            "",
        ]
        return lines

    lines += [
        (
            f"**{len(unlogged)} of the {len(false)} are runs nothing in the entity claims "
            "at all**, by name or by anything recoverable from the rest of the record. The "
            "other "
            f"{len(false) - len(unlogged)} logged under a different name or into a "
            "different project, so the run is findable and the record is wrong about where "
            "-- those are not unlogged runs and must not be read as an accusation against "
            "a submitter."
        ),
        "",
        (
            "The platform hands every container a project, an entity and a key, and it "
            "cannot make a workload call `wandb.init()`. Nothing here fails a run for not "
            "logging; what it does is stop the record saying it did."
        ),
        "",
        "| Run | The record names | Ended as | What W&B has |",
        "| --- | --- | --- | --- |",
    ]
    for entry in false:
        found = (
            "nothing in the entity carries this run id"
            if entry.found_at is None
            else f"`{entry.found_as}` in `{entry.found_at}`"
        )
        lines.append(
            f"| `{entry.reference.run_id}` | `{entry.reference.described}` "
            f"| {entry.reference.outcome} | {found} |"
        )
    lines.append("")
    if reading.unparsed:
        lines += [
            (
                f"{reading.unparsed} result record(s) did not parse against the contracts "
                "in this tree and are left out of every number above. A stored record the "
                "current tree cannot read means a contract was tightened after it was "
                "sealed, and the record is immutable, so what needs deciding is whether "
                "the rule that now refuses it should tolerate what came before it."
            ),
            "",
        ]
    return lines