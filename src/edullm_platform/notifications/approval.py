"""One compiled submission, read as the facts a lead needs before releasing it.

**The same seam ``facts.py`` uses, for the same reasons.** No clock, no SDK import, no I/O
this module opens for itself. Every value is a function of one document, of the reviewed
configuration packaged beside it, and of readers the caller injects.

**The document is read as JSON and never as ``CompiledSubmission``.** ``compile_submission.py``
writes ``run_id``, ``submitter``, ``approval_class``, ``approving_environment``,
``manifest_sha256``, ``manifest`` and ``experiment``, and this reads six of the seven. Parsing
it back through the contract would drag ``RunManifest`` and the whole submission surface into
this function's zip, and would let a manifest field this message never reads fail validation
and lose the message that asks somebody to approve a run. It is the discipline
``facts.submitter_of`` already holds for the intent record, and
``tests/test_notification_approval.py`` compares every field name spelled here against the
contracts, so the spellings cannot drift.

**THE MONEY IS RE-DERIVED HERE AND THE DOCUMENT IS NOT ASKED FOR IT.** The compiled submission
carries no total, and if it carried one this would still recompute it. Every factor comes off
the manifest and the packaged catalog, and the product comes from
:func:`~edullm_platform.contracts.workload.compute_maximum_compute_cost_usd`, which is the one
function in the tree that multiplies them. That matters more here than anywhere else in this
package. A notifier that spelled ``rate * hours * attempts * cells`` a second time would agree
with the platform on every profile in today's catalog, because all seventeen of them are one
machine, and would understate the first multi-node profile anybody registers by the node
count. The figure a lead approves a spend on is the worst place in the tree for that.

**A profile the catalog does not price leaves the cost unknown and still sends the message.**
The experiment, the person and the shape are worth reading without a figure, and a message
nobody got is never better than a message with a gap in it that says it is a gap.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from ..accelerators import ACCELERATORS_FILENAME, AcceleratorRecord, read_accelerators, record_for
from ..config import load_yaml
from ..contracts.bindings import normalize_github_login
from ..contracts.policy import ApprovalPolicy
from ..contracts.workload import CostInputs, WorkloadCatalog
from ..run_history import RunHistory
from .facts import Catalogs

__all__ = [
    "APPROVAL_DETAIL_TYPE",
    "MANIFEST_FIELDS",
    "PLATFORM_EVENT_SOURCE",
    "POLICY_FILENAME",
    "RUNG_SAID",
    "SHAPE_FIELDS",
    "ApprovalRequestedFacts",
    "Shape",
    "load_accelerators",
    "load_policy",
    "read_approval_requested",
]

#: The fourth reviewed file this function opens, named as a literal for the reason
#: ``facts.ORGANIZATION_FILENAME`` is: ``tests/test_lambda_package_closure.py`` walks the
#: packaged modules for exactly these strings and holds the zip builder's list against what
#: it finds. A notifier that read a policy its builder did not carry would deploy, pass every
#: artifact check, and raise on the first approval it was asked to describe.
POLICY_FILENAME: Final = "policy.yaml"

#: The envelope this reader answers to. A source of its own rather than ``aws.batch``,
#: because nothing in AWS produces this event: it is the platform describing its own gate,
#: and a reader that could not tell the two apart would try to price a submission as a job.
PLATFORM_EVENT_SOURCE: Final = "edullm.platform"
APPROVAL_DETAIL_TYPE: Final = "Run Approval Requested"

#: Which manifest keys this reads, named here so a test can hold them against
#: ``RunManifest.model_fields``. The contract is not imported, for the reason the docstring
#: gives, so this list is the only thing standing between a renamed field and a message that
#: silently stops naming the machine.
MANIFEST_FIELDS: Final = (
    "repository",
    "team",
    "compute_profile",
    "workload_profile",
    "dataset_release",
    "maximum_runtime_hours",
    "maximum_attempts",
    "fanout",
)

#: The four fields ``run_history`` keys a cohort on. Spelled here rather than imported from
#: ``RUNGS`` because this builds the shape and that module consumes it, and a test compares
#: this tuple against the widest rung so a fifth key cannot appear on one side alone.
SHAPE_FIELDS: Final = ("repository", "workload_profile", "compute_profile", "dataset_release")

#: How each of ``run_history``'s rungs is named in a message, keyed by its index into
#: ``RUNGS``. Shorter than the words that module uses for the approver page, because this one
#: is read at two in the morning, and each of them is the subject of the sentence rather than
#: a parenthesis after it.
RUNG_SAID: Final = (
    "The same workload, machine and dataset",
    "The same workload on this machine",
    "The same workload on any machine",
)
UNNAMED_RUNG: Final = "Runs of this shape"

#: What a fan-out is. One cell is an ordinary run and the manifest carries no ``fanout`` for
#: it, so the absence is the default rather than a case.
SINGLE_CELL: Final = 1

SECONDS_AN_HOUR: Final = Decimal(3600)


@dataclass(frozen=True)
class Shape:
    """How long runs like this one have actually taken, and over how many of them.

    Held apart from the sentence ``run_history`` writes for the approver page. That page is
    read on a laptop with the manifest beside it and can afford a paragraph. This is read on
    a phone at two in the morning, so what survives is the median and the count behind it,
    and the renderer turns them into one clause.

    ``median_seconds`` is ``None`` for both of the ways there is no figure, and
    ``succeeded`` tells them apart: zero means nothing of this shape has finished here, and
    a small number means it has and there are too few to quote.
    """

    median_seconds: Decimal | None
    succeeded: int
    #: ``False`` where the install carries no reading at all, which is a fact about this
    #: deployment rather than about the platform. A message must not say "nothing like this
    #: has run" when what happened is that nobody packaged the measurement.
    was_read: bool
    #: How loose the match is, and the message says it. ``run_history`` walks from workload,
    #: machine and dataset down to workload alone, so a shape nobody has run on this machine
    #: is answered by the same workload on a different one. That answer is worth having and
    #: is not the same claim: a twelve-hour bound on eight H100s compared against a median
    #: taken on A10Gs is a comparison across machines that are not the same speed, and a
    #: lead has to be able to see that before discounting the figure or trusting it.
    said_of: str


@dataclass(frozen=True)
class ApprovalRequestedFacts:
    """Everything the message asking for an approval may say, and nothing worded yet.

    Frozen, for the reason ``RunEndedFacts`` is: a renderer that can edit its inputs
    produces output that depends on which message was built first.
    """

    run_id: str
    person: str | None
    submitter: str | None
    team: str
    experiment: str | None
    repository: str
    compute_profile: str
    workload_profile: str
    #: What the workload profile declares its runs may take, against what this submission
    #: asked for. ``None`` where the catalog names no such workload.
    #:
    #: CARRIED BECAUSE THE ARITHMETIC BEING RIGHT IS NOT THE SAME AS THE NUMBER BEING RIGHT.
    #: A submission naming ten thousand hours on a workload that declares twenty-four
    #: compiles clean and prices clean, and a lead who does not know the catalog by heart
    #: reads $10,520 on a T4 as a plan rather than as a typed-in zero.
    profile_hours: Decimal | None
    #: The five factors and their product, or ``None`` because the catalog prices no profile
    #: by this name. Never a partial answer: a rate with no node count is how the total ends
    #: up short by a machine.
    cost: CostInputs | None
    approval_class: str
    gate: str
    #: What the reviewed policy says, read out of the packaged file rather than remembered.
    #: The routing sentence quotes both, so a policy edit changes what a lead is told
    #: without anybody editing a string.
    routine_approver_role: str
    automatic_below_cost_usd: Decimal
    #: Which leads the submitter's own team records, first and empty rather than absent. The
    #: gate admits any lead whatever this says, and the renderer says so, because naming a
    #: lead invites the reading that they are the only person who may act.
    leads: tuple[str, ...]
    #: Who the admin gate asks, for the one class no run reaches under v5. Carried because a
    #: capacity block posted into the runs channel has to tell every lead reading it that
    #: this one is not theirs, and naming a team's leads under an admin gate would say the
    #: opposite of the truth.
    admins: tuple[str, ...]
    shape: Shape
    #: Where to go and release or decline it. ``None`` only where the emitter had no run to
    #: point at, which is a message worth sending without a link rather than one to drop.
    url: str | None
    #: Whether this digest's registry findings carry no recorded review. It is one of the
    #: three things that hold a cheap single cell back from releasing itself, so the routing
    #: sentence has to be able to name it.
    scan_unreviewed: bool
    #: What card the named profile puts under this run and how much memory is on it, or
    #: ``None`` where the packaged measurement has no row for the profile.
    #:
    #: THE ONE FIGURE ON THIS MESSAGE THAT IS ABOUT THE MACHINE RATHER THAN THE MONEY, AND
    #: IT IS HERE BECAUSE THE APPROVAL IS THE LAST MOMENT IT IS FREE. Three of the failures
    #: analysed in the week to 2026-08-06 were CUDA out-of-memory, which is a fault a
    #: researcher meets after the approval, after the queue and after the card has started
    #: billing. Nothing here refuses anything on it, and nothing here can: a compiled
    #: submission carries no model size, so a fit claim would be a guess wearing a refusal's
    #: clothes. What it does is put the ceiling in front of the person releasing the spend.
    accelerator: AcceleratorRecord | None

    @property
    def cells(self) -> int:
        return SINGLE_CELL if self.cost is None else self.cost.cells

    @property
    def is_a_fanout(self) -> bool:
        return self.cells > SINGLE_CELL


def load_policy(directory: Path) -> ApprovalPolicy:
    """The reviewed policy, from the copy packaged beside this function.

    Read rather than remembered, and that is the whole reason this file is in the zip. The
    routing sentence quotes the bound under which nobody releases a run and the role that
    releases everything else, and both of those are the sort of number a message ends up
    stating from memory and then stating wrongly for a month after somebody edits the file.
    ``config/policy.yaml`` has been bumped five times and the bound it carries has moved by a
    factor of a hundred once already.
    """
    return load_yaml(directory / POLICY_FILENAME, ApprovalPolicy)


def load_accelerators(directory: Path) -> tuple[AcceleratorRecord, ...]:
    """The measured cards, from the copy packaged beside this function.

    Empty rather than raising where the file is not there, which is the shape
    :func:`~edullm_platform.run_history.load_run_history` uses and for its reason: an
    editable install and a configuration directory a test built both carry no measurement,
    and neither is a broken deployment. The message then omits the clause rather than
    guessing at a card, and a zip that declared the file and did not carry it is caught by
    the builder before it is ever uploaded.

    A file that is there and will not parse raises, also as ``load_run_history`` does. That
    is a broken install rather than an absent measurement, and the two must not read alike.
    """
    path = directory / ACCELERATORS_FILENAME
    if not path.is_file():
        return ()
    return read_accelerators(path)


def _text(document: Mapping[str, Any], key: str) -> str | None:
    value = document.get(key)
    return value if isinstance(value, str) and value else None


def _whole(document: Mapping[str, Any], key: str) -> int | None:
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _decimal(document: Mapping[str, Any], key: str) -> Decimal | None:
    """A base-ten figure off a JSON document, or ``None`` because it is not one.

    ``str`` first because that is how every bound in this tree is serialized, and a bound
    that went through binary floating point is not the number the approver reads. An ``int``
    is accepted beside it because a hand-written form may carry ``24`` rather than ``"24"``,
    and a float is refused rather than converted for the same reason the string exists.
    """
    value = document.get(key)
    if isinstance(value, str):
        try:
            return Decimal(value)
        except ArithmeticError:
            return None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return Decimal(value)


def _cells(manifest: Mapping[str, Any]) -> int:
    fanout = manifest.get("fanout")
    if not isinstance(fanout, Mapping):
        return SINGLE_CELL
    size = _whole(fanout, "size")
    return size if size is not None and size > 0 else SINGLE_CELL


def _cost(manifest: Mapping[str, Any], catalog: WorkloadCatalog) -> CostInputs | None:
    """The five factors, or ``None`` because one of them has no source.

    ``CostInputs`` rather than a total, and the product is its computed field. So the
    renderer prints the factors and the figure out of one object that cannot hold a total
    disagreeing with the numbers beside it, and the multiplication happens in the same
    function ``compile_submission`` and ``admit`` both call.

    THE NODE COUNT COMES OFF THE PROFILE AND NEVER OFF THE MANIFEST. A submitter names a
    machine and the catalog says how many boxes that is, which is why a message re-deriving
    the arithmetic from the manifest alone would drop the factor nothing in today's catalog
    exercises.
    """
    named = _text(manifest, "compute_profile")
    hours = _decimal(manifest, "maximum_runtime_hours")
    attempts = _whole(manifest, "maximum_attempts")
    if named is None or hours is None or hours <= 0 or attempts is None or attempts < 1:
        return None
    for profile in catalog.compute_profiles:
        if profile.name == named:
            return CostInputs(
                hourly_rate_usd=profile.hourly_rate_usd,
                nodes=profile.nodes,
                maximum_runtime_hours=hours,
                maximum_attempts=attempts,
                cells=_cells(manifest),
            )
    return None


def _person(login: str | None, catalogs: Catalogs) -> str | None:
    """The display name behind a GitHub login, or ``None`` because the roster lacks it.

    Compared on the normalised login, for the reason ``facts._person_named_by_login``
    compares on it: GitHub logins are case insensitive, the contract normalises them to
    refuse duplicates, and an exact comparison silently fails to name somebody whose
    submission spelled their own login the other way.
    """
    if login is None:
        return None
    wanted = normalize_github_login(login)
    for member in catalogs.inventory.members:
        if member.normalized_github_login == wanted:
            return member.display_name or member.github_login
    return None


def _leads(team: str, catalogs: Catalogs) -> tuple[str, ...]:
    for binding in catalogs.inventory.team_bindings.teams:
        if binding.team_id == team:
            return tuple(binding.lead_logins)
    return ()


def _shape(manifest: Mapping[str, Any], history: RunHistory | None) -> Shape:
    """What runs of this shape have taken, reduced to a figure and a count.

    ``history.answer`` walks its own rungs from the most specific down, so a submission
    whose exact dataset has never run is still answered by the same workload on the same
    machine. What comes back here is the cohort rather than the sentence, because the
    sentence is written for a page and this is read on a phone.
    """
    if history is None:
        return Shape(median_seconds=None, succeeded=0, was_read=False, said_of=UNNAMED_RUNG)
    key = {field: _text(manifest, field) or "" for field in SHAPE_FIELDS}
    answer = history.answer(key)
    if answer.cohort is None:
        return Shape(median_seconds=None, succeeded=0, was_read=True, said_of=UNNAMED_RUNG)
    rung = answer.cohort.rung
    return Shape(
        median_seconds=answer.cohort.median_seconds if answer.cohort.answerable else None,
        succeeded=answer.cohort.succeeded,
        was_read=True,
        said_of=RUNG_SAID[rung] if 0 <= rung < len(RUNG_SAID) else UNNAMED_RUNG,
    )


def read_approval_requested(
    envelope: Mapping[str, Any],
    *,
    catalogs: Catalogs,
    policy: ApprovalPolicy,
    history: RunHistory | None = None,
    accelerators: Sequence[AcceleratorRecord] = (),
) -> ApprovalRequestedFacts | None:
    """The facts a lead needs, or ``None`` because this delivery is not one of these.

    ``None`` rather than an exception for every envelope this has nothing to say about, for
    the reason ``read_run_ended`` answers ``None``: the same queue carries Batch state
    changes and the morning trigger, and raising on either would send it round the retry
    loop into the dead-letter queue where a person is meant to find real failures.

    ``history`` defaults to ``None`` and the message then says the ceiling could not be
    checked against anything, which is a fact about this install rather than about the run.
    ``accelerators`` defaults to empty and the message then says nothing about the machine's
    memory, which is the one clause it omits rather than qualifies: an absent row means the
    two reviewed files have parted company, and there is nothing about this run to report.
    """
    if envelope.get("source") != PLATFORM_EVENT_SOURCE:
        return None
    if envelope.get("detail-type") != APPROVAL_DETAIL_TYPE:
        return None
    detail = envelope.get("detail")
    if not isinstance(detail, Mapping):
        return None
    manifest = detail.get("manifest")
    run_id = _text(detail, "run_id")
    if not isinstance(manifest, Mapping) or run_id is None:
        return None
    team = _text(manifest, "team")
    repository = _text(manifest, "repository")
    profile = _text(manifest, "compute_profile")
    if team is None or repository is None or profile is None:
        return None

    workload = _text(manifest, "workload_profile") or "a workload this event does not name"
    submitter = _text(detail, "submitter")
    return ApprovalRequestedFacts(
        run_id=run_id,
        person=_person(submitter, catalogs),
        submitter=submitter,
        team=team,
        experiment=_text(detail, "experiment"),
        repository=repository,
        compute_profile=profile,
        workload_profile=workload,
        profile_hours=next(
            (
                entry.maximum_runtime_hours
                for entry in catalogs.catalog.workloads
                if entry.name == workload
            ),
            None,
        ),
        cost=_cost(manifest, catalogs.catalog),
        approval_class=_text(detail, "approval_class") or "routine",
        gate=_text(detail, "approving_environment") or "run-approval-lead",
        routine_approver_role=policy.routine_approver_role,
        automatic_below_cost_usd=policy.thresholds.automatic_below_cost_usd,
        leads=_leads(team, catalogs),
        admins=tuple(catalogs.inventory.admins),
        shape=_shape(manifest, history),
        url=_text(detail, "url"),
        scan_unreviewed=detail.get("image_scan_reviewed") is False,
        accelerator=record_for(profile, accelerators=accelerators),
    )
