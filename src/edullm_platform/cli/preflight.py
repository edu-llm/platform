"""Every refusal that can be reached without asking anything, reached before the queue.

THIS IS THE VERB THAT SAVES THE MONEY, AND IT SAVES IT BY BEING BORING. Nothing here is a
new rule. Each check hands the question to the module that already owns it and turns
whatever that module says into a line with a code on it:

===========================================  =========================================
question                                     who answers it
===========================================  =========================================
is the submitter on the roster               ``submission.require_submitter_on_the_roster``
is the repository registered                 ``submission.require_registered_repository``
is the submitter on the team they named      ``contracts.authorization.belongs_to_claimed_team``
is the dataset registered, and a corpus      ``contracts.dataset_registry.DatasetRegistry``
is the dataset still the current one         ``submission.require_a_dataset_release_that_is_current``
is the compute profile real and provisioned  ``contracts.workload.resolve_compute_profile_for_execution``
does the command start one process per card  ``launchers.require_a_process_for_every_device``
does it save where a retry will look         ``checkpoint_commands.require_a_save_folder_a_retry_can_find``
can the card run the dtype it asks for       ``precision.require_bfloat16_only_where_the_hardware_has_it``
is the command startable and still quoted    ``contracts.manifest.RunManifest``
what does it cost, and who releases it       ``manifest_helpers`` and ``contracts.policy``
what have runs like this one taken           ``run_history.history_for``
what does policy deny outright               ``admission.denied_outright_conditions``
===========================================  =========================================

A second spelling of any of them would be a second answer to a settled question, and the
direction it fails is the expensive one: the CLI clears a submission, a lead reads it and
releases it, and admission refuses it from inside AWS with the approval already spent.

**THAT GOES FOR THE CODES AS WELL AS FOR THE RULES, AND IT DID NOT USED TO.** This module
invented ``workload_profile_repository_mismatch``, ``process_per_device`` and four more of
its own, because the exception the compile step raises arrived with prose and no code to
read. It carries one now, so every code below that names a compile-time refusal is read off
the class that raises it: ``type(exc).reason_code`` where the exception is caught, and
``SomeError.reason_code`` where the question is asked over again here because there is no
catalog lookup to catch. Nothing here spells one, which is what makes a fork impossible
rather than merely discouraged, and ``tests/test_refusal_codes.py`` is what keeps it that
way. The codes with no compile-time counterpart -- a dirty tree, an unknown submitter, an
ambiguous team -- are still written here, because here is the only place that asks.

**A REAL ``RunManifest`` IS BUILT, WITH ONE PLACEHOLDER, AND THAT IS WHY THE ANSWERS ARE
THE SERVER'S.** ``build_request_facts``, ``compute_manifest_cost_inputs``,
``denied_outright_conditions`` and ``classify_request`` all take a manifest or the facts
derived from one, so constructing the manifest is what buys every rule at once rather than
a reimplementation of each. The one field a laptop cannot fill is the image digest: it is
whatever the registry published for the declared commit, and asking the registry needs a
credential this binary does not hold and must not. :data:`UNRESOLVED_IMAGE_DIGEST` stands
in its place, is never printed, and the two checks that depend on it -- whether the commit
published an image at all, and whether that image's scan findings have been read -- are
reported as deferred rather than as passed. Reporting them as passed is the failure this
paragraph exists to prevent; ``adarsh-rajesh-first-run.md`` is a transcript of what it
costs when a submitter believes a clean preflight means a submission will go through.

**IT IS TWO CHECKS DEFERRED AND NOT MORE, WHICH IS WHY THE VERB IS WORTH RUNNING.** Of the
seven compile-time refusals ``system-overview.md`` lists under "The submission path", five
are decided here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from edullm_platform.admission import denied_outright_conditions
from edullm_platform.checkpoint_commands import require_a_save_folder_a_retry_can_find
from edullm_platform.cli.configuration import ReviewedConfiguration
from edullm_platform.cli.preferences import DefaultTeam
from edullm_platform.cli.workspace import GitFacts
from edullm_platform.contracts.admission import ApprovalEnvironment
from edullm_platform.contracts.authorization import (
    AuthorizationReason,
    belongs_to_claimed_team,
)
from edullm_platform.contracts.bindings import SLUG_PATTERN
from edullm_platform.contracts.dataset_registry import PublishedDatasetReference
from edullm_platform.contracts.manifest import FanOut, RunManifest
from edullm_platform.contracts.policy import ApprovalClass, RequestFacts, classify_request
from edullm_platform.contracts.workload import (
    ComputeProfile,
    ComputeProfileResolutionError,
    CostInputs,
    WorkloadCatalog,
    WorkloadProfile,
    resolve_compute_profile_for_execution,
)
from edullm_platform.errors import (
    ExperimentNotASlugError,
    NoPublishedImageError,
    RetiredDatasetReleaseError,
    RetryWithoutACheckpointContractError,
    SubmissionRefusedError,
    UnregisteredWorkloadProfileError,
    WorkloadProfileRepositoryMismatchError,
)
from edullm_platform.launchers import require_a_process_for_every_device
from edullm_platform.manifest_helpers import build_request_facts, compute_manifest_cost_inputs
from edullm_platform.precision import require_bfloat16_only_where_the_hardware_has_it
from edullm_platform.run_history import HistoryAnswer, history_for
from edullm_platform.submission import (
    require_a_dataset_release_that_is_current,
    require_registered_repository,
    require_submitter_on_the_roster,
)

__all__ = [
    "DEFERRED_TO_SUBMIT",
    "SUBMITTER_UNKNOWN",
    "UNRESOLVED_IMAGE_DIGEST",
    "Preflight",
    "Refusal",
    "SubmissionRequest",
    "first_validation_message",
    "resolve_team",
    "run_preflight",
    "said_once",
    "validation_messages",
    "working_tree_refusals",
]

#: How many changed paths a dirty-tree refusal names before it stops listing them. Enough
#: to recognise which change was forgotten, and not so many that the refusal becomes the
#: output of ``git status`` with a sentence on top.
DIRTY_PATH_SAMPLE: Final = 5

#: The bin rather than a group, and the one team id every member of the roster is in.
#: ``docs-frank/reference/decisions.md`` records that the guides send every new person here
#: and that the storage tier gave up the word rather than the team id doing so, because a
#: team id cannot be renamed without stranding lineage records.
SCRATCH_TEAM: Final = "scratch"

#: A well-formed digest naming nothing, so that ``RunManifest`` can be built before the
#: registry has been asked which image the declared commit published. Never rendered and
#: never sent: ``submit`` leaves ``image_digest`` off the form entirely, which is what makes
#: the workflow derive it from the commit.
UNRESOLVED_IMAGE_DIGEST: Final = "sha256:" + "0" * 64

#: The two checks a laptop cannot make, named so the output can say so rather than imply a
#: clean bill of health. Both need the container registry, which needs a credential this
#: binary holds none of.
DEFERRED_TO_SUBMIT: Final = (
    (
        # The word the refusal itself carries, so a reader told the check was deferred
        # recognises it when the compile step makes it.
        NoPublishedImageError.reason_code,
        (
            "Whether this commit published an image. A push to edullm/** builds one, and "
            "the submission workflow holds the credential that asks the registry."
        ),
    ),
    (
        "image_scan_findings_unreviewed",
        (
            "Whether the registry's scan findings for that image have been read. Decided "
            "where the findings are, and admission re-derives it after approval."
        ),
    ),
)


@dataclass(frozen=True)
class Refusal:
    """One reason this submission would not survive, with a code and a remedy.

    Both halves, and ``docs-frank/reference/decisions.md`` settles why under "Notification
    decisions": the code is what a skill and a test match on, the text is what a person
    reads. A refusal carrying only the code sends a first-week researcher to edit a
    security exceptions file, which is the failure ``adarsh-rajesh-first-run.md`` records.
    """

    code: str
    detail: str


#: What every check that needs the submitter says when there is no submitter to have.
#:
#: **A CHECK THAT CANNOT MAKE ITS CHECK MUST NOT ANSWER AS THOUGH IT MADE IT.** Three things
#: here consult the login -- whether the roster names you, which group the roster puts you in,
#: and whether the group you claimed is one of them -- and two of them used to return an empty
#: answer when there was nobody to ask about. An empty answer from a check is indistinguishable
#: from a pass, so a broken ``gh`` login made ``check`` quieter and more permissive than a
#: working one: measured on 2026-08-05, one refusal against two, and the arm that says
#: ``gh auth login`` was never reached at all because an unresolved team stops ``_preflight``
#: before ``run_preflight`` runs.
#:
#: Held as one value rather than written at each site, so that a fourth reader of the submitter
#: has something to return and no reason to invent a second wording. Said once to the reader
#: even where several sites want it, which :func:`said_once` is for.
SUBMITTER_UNKNOWN: Final = Refusal(
    code="submitter_unknown",
    detail=(
        "run gh auth login. gh has not recorded who you are, so nothing here can say "
        "whether the roster names you, which group your runs are charged to, or whether a "
        "group you name is one of yours."
    ),
)


def said_once(refusals: Iterable[Refusal]) -> tuple[Refusal, ...]:
    """The refusals in the order they were reached, with a repeated code kept only once.

    One problem reported twice under one code sends a reader looking for a second problem,
    which is the argument :func:`run_preflight` already makes where it drops a denied-outright
    condition it has already stated in words. It matters here because
    :data:`SUBMITTER_UNKNOWN` is deliberately produced by every site that wanted the submitter
    -- that is what makes a silent site impossible -- and a reader needs the one command it
    names once rather than three times.

    The first occurrence is kept rather than the last, so a refusal stays where the ordering
    in :func:`run_preflight` put it, which is the order the compile job makes them in.
    """
    seen: set[str] = set()
    kept: list[Refusal] = []
    for refusal in refusals:
        if refusal.code in seen:
            continue
        seen.add(refusal.code)
        kept.append(refusal)
    return tuple(kept)


@dataclass(frozen=True)
class SubmissionRequest:
    """The fifteen-field form, resolved from the spec, the flags and the working tree.

    Field for field with ``SubmissionInputs``, deliberately, because the point of this
    binary is that it fills the form in rather than replacing it. ``experiment`` and
    ``wandb_project`` sit beside each other here for the same reason they do on the form:
    one groups related runs, the other picks the page they appear on.
    """

    repository: str
    commit_sha: str
    workload_profile: str
    compute_profile: str
    dataset_release: str
    team: str
    experiment: str
    wandb_project: str
    command: tuple[str, ...]
    maximum_runtime_hours: Decimal | None = None
    maximum_attempts: int | None = None
    fanout_size: int | None = None
    fanout_index_parameter: str | None = None


@dataclass(frozen=True)
class Preflight:
    """What a local check concluded: the refusals, and the run it would have described."""

    request: SubmissionRequest
    refusals: tuple[Refusal, ...] = ()
    #: How the team was arrived at, printed beside it because "from the roster, not from
    #: you" is the difference between a value somebody chose and one nothing checked.
    team_source: str = ""
    workload: WorkloadProfile | None = None
    compute: ComputeProfile | None = None
    dataset: PublishedDatasetReference | None = None
    manifest: RunManifest | None = None
    cost: CostInputs | None = None
    approval_class: ApprovalClass | None = None
    approving_environment: ApprovalEnvironment | None = None
    #: What runs of this shape have actually taken, or the reason there is no answer. Never
    #: ``None`` once a manifest was built, because "no history" is a finding and has to be
    #: printed rather than left as an absent block.
    #:
    #: ``exceeded`` sat here until v5 and carried which routine ceiling a request had
    #: crossed. There are no routine ceilings now, so the field could only ever be empty.
    history: HistoryAnswer | None = None

    @property
    def refused(self) -> bool:
        return bool(self.refusals)


def run_preflight(
    request: SubmissionRequest,
    *,
    configuration: ReviewedConfiguration,
    submitter: str | None,
    team_source: str = "",
) -> Preflight:
    """Every local check, in the order the compile job makes them.

    The order is not cosmetic. ``compile_submission`` asks about the submitter before the
    repository and about the repository before the workload, and its docstrings say why:
    a refusal that names a workload profile when the real problem is an unregistered
    repository points at a field that was never what stood in the way. Collecting every
    refusal rather than stopping at the first is the one place this departs from the
    workflow, and it is what ``system-overview.md`` asks of this verb -- "lists every
    refusal" -- because a submitter fixing three things one dispatch at a time is three
    queue waits rather than one edit.
    """
    refusals: list[Refusal] = []

    refusals.extend(_check_identity(submitter, configuration))
    refusals.extend(_check_repository(request, configuration))
    workload = _find_workload(request, configuration, refusals)
    compute = _find_compute(request, configuration, refusals)
    refusals.extend(_check_dataset(request, configuration))
    refusals.extend(_check_team(request, configuration, submitter))
    refusals.extend(_check_experiment(request))
    refusals.extend(_check_fanout(request))
    if workload is not None:
        refusals.extend(_check_runtime_against_the_profile(request, workload))

    if workload is None or compute is None:
        return Preflight(
            request=request,
            refusals=said_once(refusals),
            team_source=team_source,
            workload=workload,
            compute=compute,
            dataset=configuration.datasets.reference_for(request.dataset_release),
        )

    manifest, manifest_refusals = _build_manifest(request, workload)
    refusals.extend(manifest_refusals)
    if manifest is None:
        return Preflight(
            request=request,
            refusals=said_once(refusals),
            team_source=team_source,
            workload=workload,
            compute=compute,
            dataset=configuration.datasets.reference_for(request.dataset_release),
        )

    refusals.extend(_check_command(manifest, configuration.catalog))

    priced = _price_and_derive_facts(manifest, configuration)
    if isinstance(priced, Refusal):
        return Preflight(
            request=request,
            refusals=said_once((*refusals, priced)),
            team_source=team_source,
            workload=workload,
            compute=compute,
            dataset=configuration.datasets.reference_for(request.dataset_release),
        )
    cost, facts = priced
    tripped = tuple(
        condition
        for condition in denied_outright_conditions(facts, configuration.policy)
        # Already said, in a sentence naming the file to edit rather than a condition name.
        # A submitter who reads both gets one problem reported twice under two spellings.
        if condition not in {refusal.code for refusal in refusals}
    )
    refusals.extend(
        Refusal(
            code=condition,
            detail=(
                f"policy denies {condition} outright rather than classifying it. Admission "
                "would refuse this run whoever released the gate."
            ),
        )
        for condition in tripped
    )

    approval_class = classify_request(facts, configuration.policy.thresholds)
    return Preflight(
        request=request,
        refusals=said_once(refusals),
        team_source=team_source,
        workload=workload,
        compute=compute,
        dataset=configuration.datasets.reference_for(request.dataset_release),
        manifest=manifest,
        cost=cost,
        approval_class=approval_class,
        approving_environment=ApprovalEnvironment.for_approval_class(approval_class),
        history=history_for(manifest, history=configuration.run_history),
    )


def _price_and_derive_facts(
    manifest: RunManifest, configuration: ReviewedConfiguration
) -> tuple[CostInputs, RequestFacts] | Refusal:
    """The two contract models between a valid manifest and a classification, or why not.

    **A MANIFEST THAT VALIDATES IS NOT YET A REQUEST THESE TWO ACCEPT, AND THE GAP IS REAL
    RATHER THAN THEORETICAL.** ``RunManifest.team`` is any non-empty string and
    ``RequestFacts.claimed_team`` is a slug, so ``--team "Pre Training"`` builds a manifest
    and then fails here -- and before this it failed as a pydantic traceback, on a mistake
    somebody makes by capitalising a team name. ``CostInputs`` is the other one: the worst
    case is a product of five numbers and refuses a total it cannot represent, which
    ``--hours`` reaches with a large enough figure.

    Both are the contracts being right. What was wrong is that being right arrived as a
    stack trace rather than as the refusal every other rule on this path produces.
    """
    try:
        cost = compute_manifest_cost_inputs(manifest, configuration.catalog)
        facts = build_request_facts(
            manifest,
            repositories=configuration.repositories,
            catalog=configuration.catalog,
            dataset_registry=configuration.datasets,
            estimated_cost_usd=cost.maximum_compute_cost_usd,
            # THE ONE ARGUMENT DELIBERATELY LEFT OFF, AND THE ONLY PLACE IN THE TREE THAT
            # LEAVES IT OFF ON PURPOSE. ``build_request_facts`` reads a missing policy as
            # "this caller is not evaluating the scan gate", which is fail-open and is
            # exactly why it has to be asked for by omission rather than arrived at. It is
            # asked for here because the digest on the manifest is a placeholder:
            # evaluating a scan gate against an image that does not exist would refuse
            # every submission with a finding nobody reported. The two production callers
            # -- ``compile_submission`` and ``admit`` -- both pass the deployed policy, and
            # ``tests/test_phase3_image_scan.py`` holds them to it. The cost of the
            # omission is stated to the reader rather than swallowed: see
            # ``DEFERRED_TO_SUBMIT``.
            image_scan_policy=None,
        )
    except ValidationError as exc:
        return Refusal(
            code="submission_cannot_be_priced",
            detail=(
                f"{first_validation_message(exc)}. Correct that field. The manifest is well "
                "formed and this is the value that stops it being priced."
            ),
        )
    return cost, facts


def working_tree_refusals(facts: GitFacts, *, spec_path: Path | None = None) -> list[Refusal]:
    """What the recorded path needs of a checkout, asked of this one.

    ``docs-frank/reference/decisions.md`` states the three in one clause -- the recorded
    path needs a clean tree, a pushed commit and a published image -- and this answers the
    first two. Both are refusals rather than warnings, and the reason is that neither
    produces an error later: a dirty tree submits the last commit and silently runs code
    that is not what is on the laptop, and an unpushed commit published no image, so the
    refusal arrives from the registry naming a digest instead of naming a push.

    **A SPEC NOBODY HAS EVER COMMITTED IS EXCLUDED, AND THAT IS THE ONE CARVE-OUT.**
    ``check`` writes ``.edullm/run.yaml`` into a registered repository that has none, and
    then refused on the file it had just written. Neither remedy the refusal offers lands:
    ``git stash -u`` deletes the file, the next ``check`` writes it back, and the identical
    refusal prints again. Measured in a fresh ``git clone --depth 1`` of ``OLMo-core`` on
    2026-08-06, running the command ``guides/the-platform.md`` gives -- four minutes into a
    researcher's first day, in the flagship repository, on a file the tool created itself.

    The sentence above it is what makes the carve-out narrow rather than a hole. A dirty
    tree matters because the container is built from the commit and would run something
    other than what is on the laptop, and *nothing in the image reads this file*: it is the
    form, the command it carries travels on the submission and is recorded on the manifest,
    and the build reads ``.edullm/Dockerfile`` and the source. So the paragraph's own
    argument does not reach it.

    **THE BOUNDARY IS WHAT GIT ALREADY KNOWS, WHICH IS WHY IT HOLDS ACROSS INVOCATIONS.**
    Untracked is a file nobody has ever committed; modified is a change to the recipe this
    repository carries, and the next person to check this commit out gets the recipe without
    it. Only the first is excluded. Keying on "the file this invocation wrote" would have
    been the narrower-sounding rule and the wrong one: ``check`` would clear a tree that
    ``check`` again refuses, and ``submit`` -- which never scaffolds -- would refuse what
    ``check`` had just passed, which is the divergence this module's header calls the
    expensive direction.
    """
    if not facts.is_a_repository:
        return [
            Refusal(
                code="not_a_repository",
                detail=(
                    "stand in a checkout of a registered repository. This directory is not "
                    "inside a git repository, so there is no commit to submit."
                ),
            )
        ]
    refusals: list[Refusal] = []
    if facts.repository is None:
        refusals.append(
            Refusal(
                code="no_origin_remote",
                detail=(
                    "pass --repository, or add an origin remote. This clone has none, and "
                    "config/repositories.yaml is keyed on the GitHub name."
                ),
            )
        )
    if facts.commit_sha is None:
        refusals.append(
            Refusal(
                code="no_commit",
                detail=(
                    "commit something first. HEAD does not resolve to a commit, so there "
                    "is nothing for a submission to name."
                ),
            )
        )
    dirty = _dirty_paths_that_are_the_researcher_s(facts, spec_path)
    if dirty:
        shown = ", ".join(dirty[:DIRTY_PATH_SAMPLE])
        more = (
            f" and {len(dirty) - DIRTY_PATH_SAMPLE} more"
            if len(dirty) > DIRTY_PATH_SAMPLE
            else ""
        )
        refusals.append(
            Refusal(
                code="uncommitted_changes",
                detail=(
                    f"commit or stash {shown}{more}. A submission names a commit and the "
                    "image is built from it, so what would run is the last commit rather "
                    "than what is on your laptop."
                ),
            )
        )
    if facts.commit_sha is not None and not facts.commit_on_a_remote:
        refusals.append(
            Refusal(
                code="commit_not_pushed",
                detail=(
                    f"push {facts.commit_sha[:12]} to a branch under edullm/**, or git "
                    "fetch if you already have. No remote-tracking branch in this clone "
                    "contains it, so nothing has built an image from it, and this reads "
                    "the refs the clone holds rather than asking GitHub."
                ),
            )
        )
    return refusals


def _dirty_paths_that_are_the_researcher_s(
    facts: GitFacts, spec_path: Path | None
) -> tuple[str, ...]:
    """``facts.dirty_paths`` minus a spec nobody has ever committed. See the caller's header.

    Two spellings have to be recognised because ``git status --porcelain`` has two. A spec
    written into a tracked ``.edullm/`` is reported as ``.edullm/run.yaml``, and a spec
    written into a repository that had no ``.edullm/`` at all collapses to ``.edullm/``.

    **THE COLLAPSED SPELLING IS DROPPED ONLY WHERE THE SPEC IS THE WHOLE OF WHAT IS UNDER
    IT, AND THAT CONDITION IS THE POINT.** The entry stands for every file in that
    directory, so dropping it because the spec is inside would take an uncommitted
    ``.edullm/Dockerfile`` with it -- and the image is built from the commit, so a
    Dockerfile in no commit is the difference between a build and a refusal naming a digest.
    Asked of the filesystem rather than of git, because git has already said the whole
    directory is untracked and a second ``git status`` would be a second moment.
    """
    if spec_path is None or facts.root is None:
        return facts.dirty_paths
    try:
        relative = spec_path.resolve().relative_to(facts.root.resolve()).as_posix()
    except (OSError, ValueError):
        return facts.dirty_paths
    untracked = set(facts.untracked_paths)
    return tuple(
        entry
        for entry in facts.dirty_paths
        if entry not in untracked
        or not (
            entry == relative
            or (entry.endswith("/") and _the_only_file_under(facts.root / entry, spec_path))
        )
    )


def _the_only_file_under(directory: Path, path: Path) -> bool:
    """Whether ``path`` is the single file anywhere beneath ``directory``.

    Bounded rather than a full walk: two files is already the answer, and the directory this
    is asked about is one git has just reported as wholly untracked, which in the case this
    exists for holds exactly the file ``check`` wrote a moment ago.
    """
    found = 0
    for candidate in directory.rglob("*"):
        if not candidate.is_file():
            continue
        if candidate != path:
            return False
        found += 1
        if found > 1:
            return False
    return found == 1


def resolve_team(
    configuration: ReviewedConfiguration,
    *,
    submitter: str | None,
    default: DefaultTeam | None = None,
) -> tuple[str | None, str, Refusal | None]:
    """Which team this run is charged to, when the submitter did not name one.

    **THIS IS AN OPEN DECISION AND THIS FUNCTION DOES NOT CLOSE IT.**
    ``docs-frank/reference/decisions.md`` records "How a run picks a team when the submitter
    is on several" under Pending with two candidates -- the submitter names one from a
    closed list, or the platform resolves it from the roster and refuses where the roster
    cannot say -- and neither is ruled. What is implemented is the second, with ``--team``
    left in place so the first is still reachable by anybody who wants it. Nothing here
    silently picks, which is the third variant that document warns is the only one nobody
    would notice going wrong: it bills a lead's own group for work they did as a member of
    somebody else's.

    **A PERSONAL DEFAULT IS READ FIRST AND IS NOT A FOURTH VARIANT OF THAT.** It is not the
    platform picking, because the person picked, once, in a file with their name on the
    directory. It beats the roster rather than backing it up, for the same reason ``--team``
    does: the roster's single answer is an inference and a default is a statement, and a
    default that lost to an inference would be a preference that worked only where nothing
    else had an opinion. It loses to ``--team``, so the ordering is what somebody typed, then
    what they wrote down, then what the roster can derive.

    **IT PREFILLS AND IT BYPASSES NOTHING.** The value goes on to ``_check_team`` exactly as a
    typed one does, so a default naming a group the roster does not put the submitter on is
    refused here by ``submitter_not_in_claimed_team`` and, past the gate, recorded with
    ``team_verified`` false. Setting a preference buys fewer keystrokes and no outcome.

    ``scratch`` is excluded from the count rather than from the answer. Every one of the
    thirty-five is in it and the guides send every new person there, so counting it would
    make everybody ambiguous; it is still what somebody on no declared group gets, because
    that is what it is for.
    """
    if default is not None and default.team:
        return default.team, f"your default, in {default.path}", None
    if submitter is None:
        # NOT AN ABSENCE, AND ANSWERING IT AS ONE IS WHAT MADE A BROKEN LOGIN QUIET. The
        # caller stops on a team it could not resolve, so a team resolved to nothing with no
        # refusal beside it ends the whole check and reports nothing -- including the refusal
        # that would have named `gh auth login`. The roster cannot name a group for somebody
        # it cannot name, and that is a thing to say rather than a question that did not
        # arise.
        return None, "", SUBMITTER_UNKNOWN
    declared = tuple(
        team.team_id
        for team in configuration.inventory.teams_for_member(submitter)
        if team.team_id != SCRATCH_TEAM
    )
    if len(declared) == 1:
        return declared[0], "from the roster, not from you", None
    if not declared:
        return (
            SCRATCH_TEAM,
            f"the roster puts you on no declared group, so this is {SCRATCH_TEAM}",
            None,
        )
    # THE REFUSAL NAMES THE DEFAULT BECAUSE THIS IS THE MOMENT SOMEBODY WANTS ONE. Anybody
    # reading this line is on two declared groups and is going to read it again on the next
    # command and on every command after that. One sentence, and it is the address of the
    # file and nothing more: a refusal that explained the whole arrangement would be
    # documentation printed at somebody who is trying to submit a run.
    write_it_down = (
        ""
        if default is None
        else (
            f" Writing one into {default.path} answers this for every later run, and "
            "--team still overrides it."
        )
    )
    return (
        None,
        "",
        Refusal(
            code="team_is_ambiguous",
            detail=(
                f"pass --team with one of {', '.join(sorted(declared))}, or --team "
                f"{SCRATCH_TEAM} for anything you will not keep. The roster puts "
                f"{submitter} on more than one group, so it cannot say which this run is "
                f"charged to.{write_it_down}"
            ),
        ),
    )


def _check_identity(
    submitter: str | None, configuration: ReviewedConfiguration
) -> list[Refusal]:
    if submitter is None:
        return [SUBMITTER_UNKNOWN]
    try:
        require_submitter_on_the_roster(submitter, inventory=configuration.inventory)
    except SubmissionRefusedError as exc:
        return [Refusal(code=type(exc).reason_code, detail=str(exc))]
    return []


def _check_repository(
    request: SubmissionRequest, configuration: ReviewedConfiguration
) -> list[Refusal]:
    try:
        require_registered_repository(
            request.repository, repositories=configuration.repositories
        )
    except SubmissionRefusedError as exc:
        return [Refusal(code=type(exc).reason_code, detail=str(exc))]
    return []


def _find_workload(
    request: SubmissionRequest,
    configuration: ReviewedConfiguration,
    refusals: list[Refusal],
) -> WorkloadProfile | None:
    workload = next(
        (
            candidate
            for candidate in configuration.catalog.workloads
            if candidate.name == request.workload_profile
        ),
        None,
    )
    if workload is None:
        offered = ", ".join(sorted(entry.name for entry in configuration.catalog.workloads))
        refusals.append(
            Refusal(
                # Read off the class ``_resolve_workload`` raises rather than caught from
                # it, because that function is a lookup this one has already made. The code
                # is the same word either way, which is the point of reading it there.
                code=UnregisteredWorkloadProfileError.reason_code,
                detail=(
                    f"{request.workload_profile!r} is not in config/workload-catalog.yaml. "
                    f"Pass --workload with one of: {offered}. Adding a new one is a pull "
                    "request against the platform."
                ),
            )
        )
        return None
    if workload.repository != request.repository:
        # The two-field disagreement ``compile_submission`` refuses, asked here for the same
        # reason it is asked there: both sides are in a file already open, and a workload
        # written for another codebase brings that codebase's bounds with it.
        for_this_repository = sorted(
            entry.name
            for entry in configuration.catalog.workloads
            if entry.repository == request.repository
        )
        offered = ", ".join(for_this_repository) or "none at all"
        refusals.append(
            Refusal(
                code=WorkloadProfileRepositoryMismatchError.reason_code,
                detail=(
                    f"workload profile {workload.name!r} belongs to "
                    f"{workload.repository!r} rather than to {request.repository!r}. Pass "
                    f"--workload, or change workload_profile in {'.edullm/run.yaml'}. "
                    f"Registered for {request.repository}: {offered}."
                ),
            )
        )
        return None
    return workload


def _find_compute(
    request: SubmissionRequest,
    configuration: ReviewedConfiguration,
    refusals: list[Refusal],
) -> ComputeProfile | None:
    try:
        return resolve_compute_profile_for_execution(
            configuration.catalog, request.compute_profile
        )
    except ComputeProfileResolutionError as exc:
        provisioned = ", ".join(
            sorted(
                profile.name
                for profile in configuration.catalog.compute_profiles
                if profile.provisioned
            )
        )
        refusals.append(
            Refusal(
                code=type(exc).reason_code,
                detail=f"{exc}. Provisioned today: {provisioned}.",
            )
        )
        return None


def _check_dataset(
    request: SubmissionRequest, configuration: ReviewedConfiguration
) -> list[Refusal]:
    registry = configuration.datasets
    if not registry.is_registered(request.dataset_release):
        # WHAT A REFUSAL MAY SUGGEST IS NARROWER THAN WHAT THE REGISTRY CARRIES, AND THIS
        # LISTED THE REGISTRY. Twenty-four names, five of which the very next check refuses
        # by family, so a submitter correcting a typo could pick one and meet
        # `dataset_is_not_a_corpus` on the next run. That is the defect #232 took out of the
        # workload refusal, sitting inside this one, and the fix is the same: the registry
        # answers which names survive every check, so this cannot suggest one that does not.
        offered = ", ".join(registry.names_a_run_may_still_use())
        return [
            Refusal(
                code="unregistered_dataset",
                detail=(
                    f"{request.dataset_release!r} is not a release config/datasets.yaml "
                    f"carries. Registered and still usable: {offered}."
                ),
            )
        ]
    if not registry.is_a_trainable_corpus(request.dataset_release):
        reference = registry.reference_for(request.dataset_release)
        # Only reachable through a published reference, which is the only kind of entry
        # carrying a family, so the address is always in hand for the sentence.
        assert reference is not None
        return [
            Refusal(
                code="dataset_is_not_a_corpus",
                detail=(
                    f"{request.dataset_release!r} resolves to {reference.dataset_id}, which "
                    "is an input to a corpus rather than a corpus. Name a release under "
                    "pretrain/ or sft/, which are the two a run may train on."
                ),
            )
        ]
    # THE THIRD, AND THE ONE THAT USED TO BE ANSWERED BY A DROPDOWN. Asked over again here
    # rather than caught, because there is no exception to catch on this path: the compile
    # job raises it beside compiling and a laptop has nothing to call that would.
    # ``require_a_dataset_release_that_is_current`` is the same predicate on the same
    # registry, and its docstring carries the argument for refusing here instead of adding a
    # condition policy denies outright.
    try:
        require_a_dataset_release_that_is_current(
            request.dataset_release, datasets=registry
        )
    except RetiredDatasetReleaseError as exc:
        return [Refusal(code=type(exc).reason_code, detail=str(exc))]
    return []


def _check_team(
    request: SubmissionRequest,
    configuration: ReviewedConfiguration,
    submitter: str | None,
) -> list[Refusal]:
    inventory = configuration.inventory
    declared = {team.team_id for team in inventory.team_bindings.teams}
    if request.team not in declared:
        return [
            Refusal(
                code="unregistered_team",
                detail=(
                    f"{request.team!r} is not a team config/organization.yaml declares, so "
                    "this run's spend would land in no group's total. Declared: "
                    f"{', '.join(sorted(declared))}."
                ),
            )
        ]
    if submitter is None:
        # The claim goes unexamined and that is a thing the output has to carry. Returning
        # nothing here reads as "the claim was examined and held", which is the fail-open
        # direction on the one field that decides which lead is asked and whose budget pays.
        return [SUBMITTER_UNKNOWN]
    # THE ONLY PLACE THIS IS STILL ASKED OF A PERSON, WHICH IS WHY IT READS THE SHARED
    # HELPER RATHER THAN COMPARING TWO LISTS HERE. ``evaluate_authorization`` used to ask it
    # again inside AWS and refuse there; it does not, because that refusal landed past the
    # approval gate and its only recorded effect was four researchers whose lead had already
    # released them. What survives inside AWS is the ``team_verified`` flag on the decision
    # record. So this is a laptop refusal costing two seconds, and the part that makes it
    # safe is unchanged: membership is read per submitter, so somebody whose own membership
    # is unrecorded is not refused a team.
    if inventory.teams_for_member(submitter) and not belongs_to_claimed_team(
        inventory, submitter=submitter, claimed_team=request.team
    ):
        mine = ", ".join(
            sorted(team.team_id for team in inventory.teams_for_member(submitter))
        )
        return [
            Refusal(
                code=AuthorizationReason.SUBMITTER_NOT_IN_CLAIMED_TEAM.value,
                detail=(
                    f"pass --team with one of {mine}, or add {submitter} to "
                    f"{request.team!r} in config/organization.yaml. The roster does not "
                    "record you in that group, so the spend would be counted against a "
                    "group that did not do the work and nothing inside AWS would refuse it."
                ),
            )
        ]
    return []


def _check_experiment(request: SubmissionRequest) -> list[Refusal]:
    from re import fullmatch

    if fullmatch(SLUG_PATTERN, request.experiment):
        return []
    return [
        Refusal(
            code=ExperimentNotASlugError.reason_code,
            detail=(
                f"rewrite the experiment {request.experiment!r} in lower-case letters and "
                "digits, with single hyphens between words and none at either end, like "
                "context-length-sweep. It registers nothing, so any name of that shape "
                "will do."
            ),
        )
    ]


def _check_runtime_against_the_profile(
    request: SubmissionRequest, workload: WorkloadProfile
) -> list[Refusal]:
    """``--hours`` above what the profile declares, which nothing bounded.

    **THIS IS THE LAST RUNTIME BOUND IN THE TREE AND IT WAS NOT BEING APPLIED.**
    ``config/policy.yaml`` retired ``routine_maximum_runtime_hours`` at v5 and recorded why
    that was safe in the file itself: "the workload profiles in
    config/workload-catalog.yaml still declare their own runtime ceilings and those are
    what a submission is bounded by". They were not bounding anything. Measured on
    2026-08-06: ``--hours 10000`` against ``olmo-core-train``'s twenty-four was accepted
    with no refusal and no warning, priced at $10,520, and routed to a team lead as
    routine, who reads a figure with nothing beside it saying the profile said 24.

    **A REFUSAL RATHER THAN A SENTENCE, WHICH IS THE OTHER WAY THIS COULD HAVE GONE.** The
    argument for a sentence is that a profile is advice and the submitter is the one paying.
    That is true of ``suggested_compute``, which the overview calls a suggestion in version
    control and a decision at submit time, and it is not true of this field: the policy file
    calls the profile's ceiling the thing a submission is bounded by, and used that to
    justify removing the only other ceiling there was. A sentence would leave a mistyped
    flag with nothing in front of it.

    **AND IT COSTS NO DOCUMENTED USE OF THE FLAG.** ``--hours`` exists to lower a runtime --
    the cost block says lowering it is what moves a run under the automatic bound -- and
    everything at or below the profile is untouched. Only above is refused, and the remedy
    names both ways out, because a ceiling is a line in a reviewed file and moving it is a
    pull request rather than an argument with a binary.

    **WHAT THIS DOES NOT DO.** ``tools/compile_submission.py`` compares these two numbers
    nowhere, so the bound holds on a laptop and not inside AWS: ``submit --force`` and the
    Actions form still walk past it. Closing that is a change to the compile step and to the
    admission validator's released zip, which is a deployment rather than an edit.
    """
    asked = request.maximum_runtime_hours
    if asked is None or asked <= workload.maximum_runtime_hours:
        return []
    return [
        Refusal(
            code="runtime_above_the_workload_bound",
            detail=(
                f"lower --hours: this asks for {asked}h against the "
                f"{workload.maximum_runtime_hours}h {workload.name!r} declares. A workload "
                "profile fixes the runtime bound for one codebase and it is the only bound "
                "on runtime this platform has, so an override above it is priced and "
                "approved against a ceiling nothing agreed to. Raising it for everybody is "
                "an edit to config/workload-catalog.yaml and a pull request against the "
                "platform."
            ),
        )
    ]


def _check_fanout(request: SubmissionRequest) -> list[Refusal]:
    declared = (
        request.fanout_size is not None,
        request.fanout_index_parameter is not None,
    )
    if any(declared) and not all(declared):
        return [
            Refusal(
                code="fanout_incomplete",
                detail=(
                    "pass --fanout-size and --fanout-index-parameter together, or neither. "
                    "In .edullm/run.yaml the same pair is a fanout block with size and "
                    "index_parameter."
                ),
            )
        ]
    return []


def _build_manifest(
    request: SubmissionRequest, workload: WorkloadProfile
) -> tuple[RunManifest | None, list[Refusal]]:
    """The manifest the compile job would build, minus the digest only ECR can supply.

    Built rather than approximated because every rule downstream of it -- the cost, the
    facts, the denied-outright conditions, the classification -- takes a manifest or the
    facts derived from one. What the contract itself refuses on the way in is worth having
    too: a command whose quoting was lost, and one whose first word cannot name a program.
    """
    attempts = (
        request.maximum_attempts
        if request.maximum_attempts is not None
        else workload.maximum_attempts
    )
    if attempts > 1 and workload.checkpoint is None:
        return None, [
            Refusal(
                code=RetryWithoutACheckpointContractError.reason_code,
                detail=(
                    "lower --attempts to 1, or move to a workload that checkpoints. "
                    f"{workload.name!r} declares no checkpoint contract, so {attempts} "
                    "attempts would produce a run that restarts from nothing."
                ),
            )
        ]
    try:
        manifest = RunManifest(
            schema_version=1,
            repository=request.repository,
            commit_sha=request.commit_sha,
            image_digest=UNRESOLVED_IMAGE_DIGEST,
            dataset_release=request.dataset_release,
            command=request.command,
            team=request.team,
            wandb_project=request.wandb_project,
            workload_profile=workload.name,
            compute_profile=request.compute_profile,
            maximum_runtime_hours=(
                request.maximum_runtime_hours
                if request.maximum_runtime_hours is not None
                else workload.maximum_runtime_hours
            ),
            maximum_attempts=attempts,
            checkpoint=workload.checkpoint,
            fanout=(
                FanOut(
                    size=request.fanout_size,
                    index_parameter=request.fanout_index_parameter,
                )
                if request.fanout_size is not None
                and request.fanout_index_parameter is not None
                else None
            ),
        )
    except ValidationError as exc:
        return None, [
            Refusal(
                code="submission_does_not_describe_a_run",
                detail=(
                    f"{first_validation_message(exc)}. Correct that field in the spec or on "
                    "the command line."
                ),
            )
        ]
    return manifest, []


def _check_command(manifest: RunManifest, catalog: WorkloadCatalog) -> list[Refusal]:
    """The three rules about the text of a command, asked against the resolved profile.

    Against the resolved profile rather than the workload's, because ``--compute`` is what
    the run lands on and a device count read off anything else would clear a command that
    trains on one card and bills for four. The third rule reads the same field for the same
    reason, one step further along: the instance type behind the resolved profile is what
    decides whether the devices have bfloat16.

    The catalog is passed rather than closed over because that is where the shapes are, and
    the bfloat16 rule is derived from the instance type each profile declares there so that a
    shape added to that file is covered without an edit anywhere else.
    """
    refusals: list[Refusal] = []
    try:
        require_a_process_for_every_device(
            command=manifest.command, compute_profile=manifest.compute_profile
        )
    except SubmissionRefusedError as exc:
        refusals.append(Refusal(code=type(exc).reason_code, detail=str(exc)))
    try:
        require_a_save_folder_a_retry_can_find(
            command=manifest.command,
            workload_profile=manifest.workload_profile,
            checkpoint=manifest.checkpoint,
        )
    except SubmissionRefusedError as exc:
        refusals.append(Refusal(code=type(exc).reason_code, detail=str(exc)))
    try:
        require_bfloat16_only_where_the_hardware_has_it(
            command=manifest.command,
            compute_profile=manifest.compute_profile,
            catalog=catalog,
        )
    except SubmissionRefusedError as exc:
        refusals.append(Refusal(code=type(exc).reason_code, detail=str(exc)))
    return refusals


def first_validation_message(exc: ValidationError) -> str:
    """A pydantic error as one sentence naming a field, rather than as five lines and a URL.

    Public because ``main`` needs the same rendering for the same reason: whatever it is
    saying about a ``ValidationError``, the useful part is which field and what about it,
    and a second spelling of that would drift from this one.
    """
    return validation_messages(exc)[0]


def validation_messages(exc: ValidationError) -> tuple[str, ...]:
    """Every field pydantic objected to, one line each and no URLs.

    THE WHOLE LIST WHERE THE READER IS GOING TO EDIT A FILE, which is the same argument
    :func:`run_preflight` makes about collecting refusals: three problems reported one at a
    time is three trips through an editor, and the second and third were visible the first
    time. :func:`first_validation_message` is the same rendering for the places that have
    room for one sentence.
    """
    errors = exc.errors()
    if not errors:
        return (str(exc),)
    return tuple(
        f"{'.'.join(str(part) for part in error['loc']) or 'the submission'}: {error['msg']}"
        for error in errors
    )
