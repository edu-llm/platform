"""Turn what a researcher filled in on a form into the manifest policy judges.

Two jobs, and the separation between them is the whole design. Compiling happens in a job
that holds no ``id-token`` permission and reads no secret, so the classification that
decides which gate a submission goes to is computed before anything can reach AWS. The
workflow then names that gate through ``needs``, never through ``inputs``. GitHub permits
either, and the wrong one lets a submitter choose their own approval path.

The form collects what a person genuinely chooses and derives the rest. A workload profile
fixes the runtime bound, the attempt bound and the checkpoint contract, so asking for those
again invites a submitter to contradict the catalog. They stay available as explicit
overrides, because a sweep that needs longer than its profile's default is ordinary, and
an override is visible to the approver in a way a silently different default would not be.

The machine is the exception, and it is asked for outright. It was an override too, on the
strength of a ``compute_profile`` the workload profile declared, and that arrangement was
incoherent from both ends: the form's value won whenever it was supplied, nothing refused a
disagreement between the two, and a name like ``olmo-core-train-1gpu`` therefore promised a
placement the dropdown beside it was already outranking. The catalog carries policy presets
now and declares no machine, so there is nothing to derive the field from and no default
that resolves to anything.

The image is the same question in its sharpest form. It used to be a required
seventy-one-character field that had to agree with the declared commit and was compared with
nothing, so a submission could name commit A beside an image built from commit B and be
faultless on every field. It is derived from the commit now, by
:mod:`edullm_platform.image_resolution`, out of what the resolve job read back from the
registry, and what survives is an override with the same visibility as the other five.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from re import fullmatch
from typing import Annotated, Self

from pydantic import BeforeValidator, Field, ValidationError, model_validator

from edullm_platform.canonical import sha256_digest
from edullm_platform.checkpoint_commands import (
    require_a_demonstrated_resume_for_retries,
    require_a_save_folder_a_retry_can_find,
    resume_note,
    waived_checkpoint_check_note,
)
from edullm_platform.contracts.admission import ApprovalEnvironment
from edullm_platform.contracts.authorization import is_organization_member
from edullm_platform.contracts.base import (
    ContractModel,
    PositiveStrictDecimal,
    require_ordered_sequence,
    serialize_decimal,
)
from edullm_platform.contracts.bindings import SLUG_PATTERN
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.image_scan import (
    ImageScanExceptionRegistry,
    ImageScanSummary,
    ScanFinding,
)
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.manifest import (
    COMMIT_SHA_PATTERN,
    IMAGE_DIGEST_PATTERN,
    FanOut,
    RunManifest,
)
from edullm_platform.contracts.policy import (
    ApprovalClass,
    ApprovalPolicy,
    RequestFacts,
    classify_request,
)
from edullm_platform.contracts.repository_registry import RepositoryRegistry
from edullm_platform.contracts.resume_evidence import (
    NO_RESUME_DEMONSTRATIONS,
    ResumeDemonstrations,
)
from edullm_platform.contracts.workload import CostInputs, WorkloadCatalog, WorkloadProfile
from edullm_platform.daily_ceiling import CeilingReading, class_under_the_ceiling
from edullm_platform.errors import (
    DeniedOutrightError,
    ExperimentNotASlugError,
    RetiredDatasetReleaseError,
    RetryWithoutACheckpointContractError,
    SubmitterNotOnTheRosterError,
    TeamNotASlugError,
    UnpriceableComputeProfileError,
    UnregisteredRepositoryError,
    UnregisteredWorkloadProfileError,
    WorkloadProfileRepositoryMismatchError,
)
from edullm_platform.image_resolution import PublishedImage, ResolvedImage, resolve_image
from edullm_platform.launchers import (
    require_a_process_for_every_device,
    require_a_tensor_parallel_flag_vllm_reads,
    waived_launch_check_note,
)
from edullm_platform.manifest_helpers import (
    build_request_facts,
    compute_manifest_cost_inputs,
)
from edullm_platform.precision import require_bfloat16_only_where_the_hardware_has_it
from edullm_platform.run_history import RunHistory, history_for

__all__ = [
    "CompiledSubmission",
    "SubmissionInputs",
    "compile_submission",
    "render_approver_context",
    "require_a_dataset_release_that_is_current",
    "require_registered_repository",
    "require_submitter_on_the_roster",
]


def _plain(value: Decimal) -> str:
    """Render a decimal the way a person writes it.

    ``StrictDecimal`` normalizes on the way in, so the reviewed ceiling ``"500"`` is held as
    ``Decimal("5E+2")`` and a ten-hour bound as ``Decimal("1E+1")``. Interpolating either
    directly puts ``$5E+2`` in front of an approver, which defeats the reason the factors
    are shown at all. The contract layer already settled the presentation question for
    serialization; this is the same answer, applied where a human reads it.
    """
    return serialize_decimal(value)


class SubmissionInputs(ContractModel):
    """The ``workflow_dispatch`` form.

    Fifteen properties against a ceiling of twenty-five, so the count is a usability
    question rather than a platform constraint. Nine are required and six are overrides a
    submitter can leave alone. The newest of the six is the image digest, which stopped
    being required when it started being derived, and which was the hardest of them to fill
    in by some distance.

    ``compute_profile`` went the other way and is the ninth required field. It was an
    override that defaulted to the workload profile's own declaration, and that declaration
    was a fiction: this value won whenever it was supplied and nothing refused a
    disagreement between the two. The catalog no longer declares one, so there is nothing to
    fall back to and the field is asked for rather than defaulted.
    """

    repository: str = Field(min_length=1)
    commit_sha: str = Field(pattern=COMMIT_SHA_PATTERN)
    # A run's image is derived from the commit it declares and is never supplied beside it.
    # What survives here is an override for a deliberate rebuild-and-pin -- a researcher
    # reproducing an earlier result needs the image that produced it rather than the newest
    # one -- and it is checked against the digests published from the declared commit, so a
    # digest built somewhere else has nowhere to go. Optional rather than removed, and
    # still patterned, because the shape a pin has to have has not changed.
    image_digest: str | None = Field(default=None, pattern=IMAGE_DIGEST_PATTERN)
    workload_profile: str = Field(min_length=1)
    dataset_release: str = Field(min_length=1)
    team: str = Field(min_length=1)
    # Free text on the form and shaped by compile_submission. Held as a plain string here,
    # so the refusal a submitter meets is the one that function writes rather than a pydantic
    # dump about a form field -- the same split as `team`, for the same reason.
    #
    # Named `experiment` rather than `project` because `wandb_project` sits beside it on the
    # form and the two are different things: that one picks which Weights and Biases project
    # the charts appear in, this one groups related runs inside it. Two adjacent fields both
    # called some kind of project is a question every submitter would have asked once.
    experiment: str = Field(min_length=1)
    wandb_project: str = Field(min_length=1)
    command: Annotated[tuple[str, ...], BeforeValidator(require_ordered_sequence)] = Field(
        min_length=1, strict=False
    )
    compute_profile: str = Field(min_length=1)
    maximum_runtime_hours: PositiveStrictDecimal | None = Field(default=None, gt=0)
    maximum_attempts: int | None = Field(default=None, ge=1)
    # Two fields rather than the three this form used to take. `fanout_parallelism` was
    # removed because Batch's arrayProperties accepts a size and no concurrency cap, so
    # the number a submitter typed was recorded and never applied -- and a box on a form
    # is read as a control whatever the description beside it says. See FanOut in
    # contracts/manifest.py for the whole argument and for what it cost to keep.
    fanout_size: int | None = Field(default=None, ge=2)
    fanout_index_parameter: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_fanout_is_whole_or_absent(self) -> Self:
        declared = (
            self.fanout_size is not None,
            self.fanout_index_parameter is not None,
        )
        if any(declared) and not all(declared):
            raise ValueError(
                "a fan-out must declare both its size and what its index varies, or neither"
            )
        return self


@dataclass(frozen=True)
class CompiledSubmission:
    run_id: str
    manifest: RunManifest
    manifest_sha256: str
    facts: RequestFacts
    approval_class: ApprovalClass
    approving_environment: ApprovalEnvironment
    cost: CostInputs
    # Carried beside the manifest rather than folded into it. The manifest records which
    # image ran; this records how that image was arrived at -- derived or pinned, and out
    # of how many candidates -- which is the difference between a commit built once and a
    # commit built four times, and reads identically in the manifest either way.
    resolved_image: ResolvedImage
    # BESIDE THE MANIFEST FOR A HARDER REASON THAN resolved_image'S, AND NOT BY PREFERENCE.
    #
    # A manifest is hashed whole and the digest is what an approver releases, so a field
    # added to RunManifest changes the digest of every manifest ever written -- the
    # recomputed form carries a key the stored bytes never had, and
    # test_the_manifest_in_every_intent_still_hashes_to_its_recorded_value stops agreeing
    # with records nobody touched. Measured on a real record rather than reasoned about:
    # as stored it rehashes to 819aaf8a, with the field added as null to 0439d570.
    #
    # No serialization setting rescues it. Dropping nulls instead gives e75c8f8a, because
    # the stored manifests already carry `fanout: null` and excluding it moves the digest
    # the other way. This is schema evolution against content addressing, and it is general:
    # any field added to RunManifest does this.
    #
    # Nothing is lost by keeping it out. An experiment groups runs; it does not say what
    # ran. Its three consumers -- the W&B run group, the `edullm:experiment` Batch tag and
    # the cost view -- are all set when the job is launched, and none reads the sealed
    # document.
    experiment: str


def _resolve_workload(
    catalog: WorkloadCatalog, name: str, *, repository: str
) -> WorkloadProfile:
    """The catalog entry a submission names, or a refusal saying what it could have named.

    SCOPED TO THE DECLARED REPOSITORY BECAUSE THE WHOLE CATALOG IS NOT AN ANSWER TO THE
    QUESTION. This listed every entry, and doing so reproduced inside a refusal the exact
    defect ``tests/test_submission_form_options.py`` exists to keep out of a dropdown: a
    name presented as a thing to pick whose only outcome is another refusal. Seven of the
    nine were for other repositories, so typing one gets
    ``workload_profile_repository_mismatch`` from the check immediately below, and
    ``dolma-tokenize`` names a repository nothing registers, so it can never be submitted
    for at all.

    THIS IS WHAT THE FORM'S DROPDOWN USED TO BE, AND IT IS NOW THE ONLY THING PLAYING THAT
    PART. ``.github/workflows/submit-run.yml`` made this input free text, because the
    catalog is owned by the admins and all eight leads while that workflow is owned by two
    people, so a lead could merge an entry nobody could then select. Trading a menu for a
    refusal is only a good trade while the refusal names what the menu would have offered,
    which is why ``test_submission_form_options.py`` asserts this text against the catalog
    in both directions rather than asserting a list of options that no longer exists.
    """
    for workload in catalog.workloads:
        if workload.name == name:
            return workload
    for_this_repository = sorted(
        profile.name for profile in catalog.workloads if profile.repository == repository
    )
    if not for_this_repository:
        raise UnregisteredWorkloadProfileError(
            f"add a workload profile for {repository!r} to config/workload-catalog.yaml in "
            f"a pull request, or submit against a repository that has one. That file "
            f"registers none for {repository!r}, so {name!r} has nothing to resolve against."
        )
    registered = ", ".join(for_this_repository)
    raise UnregisteredWorkloadProfileError(
        f"name one of {registered}. config/workload-catalog.yaml registers those for "
        f"{repository!r} and carries no workload profile {name!r}. A profile registered for "
        "another repository is refused against this one, so change the repository as well "
        "if you meant one of those."
    )


def require_registered_repository(
    repository: str, *, repositories: RepositoryRegistry
) -> None:
    """Refuse a repository nothing registers, before a lead is asked to release the run.

    ADMISSION ALREADY REFUSES THIS AND STAYS THE AUTHORITY ON IT, with
    ``unregistered_repository`` derived from the registry inside the validator's own zip
    rather than from a file the compile job could be pointed at. What it cannot do is
    happen early: it runs past the approval gate, so the submitter fills in the whole form,
    a lead reads the approver context and releases it, and the refusal arrives from inside
    AWS with the approval already spent.

    ``compile_submission`` refuses it too, and that refusal is neither early enough nor
    legible enough to be the one a submitter meets. It comes out of
    ``denied_outright_conditions``, which owns the verdict and says so as a reason code:
    "the submission trips conditions policy denies outright rather than classifies:
    unregistered_repository". That names a condition rather than a file, so it reads as a
    permissions fault and sends the reader looking for access to grant. And it is reachable
    only when a workload profile names the unregistered repository, because the two fields
    are compared first -- so for every other unregistered repository the refusal is about a
    workload profile belonging to somebody else, which points at a field that was never
    what stood in the way.

    So this is asked first, out of the same registry, and it leads with the command that
    clears it. ``edullm add repository`` is the one kind in ``cli.intake.SELF_SERVICE_KINDS``
    and ``register-repository.yml`` edits the platform files and prepares the pull request, so
    the reader who meets this can serve themselves. Naming only the registry file, as this
    refusal did until 2026-08-06, sent that reader off to hand-edit five files instead.

    A function beside :func:`compile_submission` rather than a check inside it, which is
    where ``require_submitter_on_the_roster`` sits and for a related reason.
    ``test_a_submission_naming_a_repository_nothing_registers_is_refused_before_a_reviewer_is_asked``
    holds that condition to one refusal path inside compiling, so that policy keeps owning
    the verdict there; a second check in that function would split it. Asked beside
    compiling, this adds no path through the classification -- it decides whether there is
    anything to compile at all.

    Raises :class:`SubmissionRefusedError` so the caller needs no second branch, as the
    roster check does.
    """
    if repositories.is_registered(repository):
        return
    registered = ", ".join(entry.repository for entry in repositories.repositories)
    raise UnregisteredRepositoryError(
        f"run edullm add repository --reason '<why>' to register {repository!r}, which "
        f"prepares the pull request that does it. config/repositories.yaml carries no entry "
        f"for it, so admission would refuse this run with unregistered_repository whoever "
        f"released it. That pull request also carries the ECR repository the images go to, "
        f"created when it merges, and the place on the publisher role that writes them, "
        f"which is a stack no workflow may deploy. Registered today: {registered}."
    )


def require_a_dataset_release_that_is_current(
    dataset_release: str, *, datasets: DatasetRegistry
) -> None:
    """Refuse a corpus the registry carries and its owner has stopped naming as current.

    **THE FLAG HAD NO ENFORCEMENT ANYWHERE AND THE DROPDOWN WAS STANDING IN FOR ONE.**
    ``retired`` has been set on ``dolma-2026-07`` and on ``pretrain/fineweb-edu-1b`` at v2
    since each needed it, and until this function the only thing that read it was the join
    computing the submission form's option list. Measured on 2026-08-05 rather than reasoned
    about: both names clear ``edullm check`` with "no refusals", compile as routine, and are
    admitted. ``config/datasets.yaml`` already said so, that the flag "keeps admission's
    answer and removes the menu item" -- the menu item was the mechanism.

    What that buys a wrong answer is not a wasted afternoon. ``dolma-2026-07`` had nothing
    published under it and no run ever read one, so a run naming it finishes green and
    leaves an intent record and a lineage attestation asserting it read a corpus that does
    not exist, in documents that are immutable by design and cannot be corrected. v2 is the
    quieter half of the same thing: it is real, so the record is true and the result is
    against a version of the data its owner has superseded, which looks exactly like a
    result against the current one.

    **REFUSED BEFORE THE GATE AND NOT DENIED OUTRIGHT, WHICH IS A DECISION AND NOT AN
    OVERSIGHT.** Adding ``dataset_is_retired`` to ``denied_outright`` in
    ``config/policy.yaml`` is the obvious move and it is the wrong one, on that file's own
    test. It denies a condition outright when "there is no approver for whom this is a
    judgement call", and retirement is exactly a judgement somebody can make: the approver
    context prints the dataset release by name, and "I am reproducing the v2 result and need
    the corpus that produced it" is a statable reason a reader can act on. That is the
    opposite of the tokenizer case beside it, which an approver cannot see from the request
    and which reports nothing wrong afterwards either.

    It also buys nothing the two pre-gate refusals do not. No submission reaches a record
    without compiling, so the permanent record is prevented here just as completely; what
    denied-outright would add is that nobody could ever lift it, and the same file records
    what that cost the image-scan gate before policy v4 took it back out.

    **AND IT IS THE RESUME CASE THAT SETTLES IT.** A run resuming from a checkpoint written
    against a retired corpus has to keep naming that corpus, because naming a current one to
    get past a refusal writes the false record this rule exists to prevent. A gate refusable
    by nobody would make the dishonest answer the only one that works. Refused here, the
    honest route stays open and is a reviewed line in ``config/datasets.yaml`` rather than a
    silent bypass. Nothing in the tree needs it today -- Batch's second attempt is the same
    job and never re-enters this path, and ``tools/build_gpu_training_submission.py``
    dispatches ``--resume-from`` with ``dataset_release: none`` -- so this is scoping written
    down before the case arrives rather than after.

    **NARROWED TO WHAT NOTHING ELSE REFUSES**, which is what keeps the two sides of the
    submission path saying one thing. This runs before compiling and the denied-outright
    conditions are derived inside it, so a name that is both retired and not a corpus would
    otherwise be reported as retired here and as the policy condition by ``edullm check``.
    Policy owns the stronger verdict, so this stands aside for it.

    A function beside :func:`compile_submission` rather than a check inside it, which is
    where :func:`require_registered_repository` sits and for the reason recorded there:
    ``test_a_submission_naming_a_repository_nothing_registers_is_refused_before_a_reviewer_is_asked``
    holds the dataset and repository verdicts to one refusal path inside compiling, so that
    policy keeps owning them, and a second check in that function would split it.
    """
    if not datasets.is_registered(dataset_release):
        return
    if not datasets.is_a_trainable_corpus(dataset_release):
        return
    if not datasets.is_retired(dataset_release):
        return
    current = datasets.current_versions_of(dataset_release)
    instead = (
        f"name {', '.join(current)} instead, which its owner names as current."
        if current
        else (
            "name `none` instead. Nothing was ever published under this release, so that "
            "is the true answer for a run that reads no corpus."
        )
    )
    raise RetiredDatasetReleaseError(
        f"{instead} {dataset_release!r} is registered in config/datasets.yaml and retired "
        "there, so it is not a corpus to start new work against. If you are reproducing an "
        "earlier result and need exactly this corpus, clear the flag in a pull request "
        "rather than naming a different one. A run that names the corpus it did not read "
        "is the record this refusal exists to prevent."
    )


def require_submitter_on_the_roster(submitter: str, *, inventory: OrganizationInventory) -> None:
    """Refuse a submitter admission is going to refuse, while a refusal is still cheap.

    ADMISSION ALREADY CHECKS THIS AND STAYS THE AUTHORITY ON IT. It reads the roster out of
    the validator's own zip rather than out of a file the compile job could be pointed at,
    which is why this does not replace it. What that check cannot do is happen early: it
    runs on the far side of the approval gate, so an off-roster submitter fills in the whole
    form, a lead reads the approver context and releases it, and ``submitter_not_in_roster``
    arrives from inside AWS with the approval already spent. The person who then has to act
    is neither of the two who were involved.

    Answering it needs nothing from the account, so the compile step answers it too. It is
    kept out of :func:`compile_submission` deliberately: that function turns a form into a
    manifest and takes no identity, and
    ``test_compiling_is_given_nothing_that_would_let_it_ask_a_reviewer`` is what holds it to
    that. This is a fact about who dispatched the workflow rather than about what they
    filled in, so it is asked beside compiling rather than inside it, and first, because no
    edit to the form makes an off-roster submitter admissible.

    Raises :class:`SubmissionRefusedError` so the caller needs no second branch: the
    workflow already separates a refusal on the merits from a form it could not read, and
    this is the first of those.

    ``is_organization_member`` is imported rather than reimplemented, and it is imported
    despite not being in that module's ``__all__``. Adding it there would rewrite a file
    the admission validator's zip carries, which starts a Lambda release for a line of
    punctuation. A second membership test written here would be the thing worth avoiding:
    the roster is compared login by login with normalization, and two spellings of that
    comparison would disagree the first time only one of them was corrected.
    """
    if is_organization_member(inventory, submitter):
        return
    raise SubmitterNotOnTheRosterError(
        f"add {submitter!r} to `members` in config/organization.yaml, in a pull request "
        "against this repository. It needs a reviewer and a merge rather than an owner's "
        "access to anything, and until it merges admission would refuse this run with "
        "submitter_not_in_roster whoever released it. Write access here and a place on the "
        "roster are granted separately, which is why the form is open to somebody the "
        "roster does not name."
    )


def compile_submission(
    inputs: SubmissionInputs,
    *,
    run_id: str,
    policy: ApprovalPolicy,
    repositories: RepositoryRegistry,
    catalog: WorkloadCatalog,
    dataset_registry: DatasetRegistry,
    image_scan_registry: ImageScanExceptionRegistry,
    image_scan_summary: ImageScanSummary | None = None,
    image_scan_findings: Sequence[ScanFinding] | None = None,
    # Every image the registry holds for the declared commit, as the resolve job read them.
    # Defaulted to nothing rather than made required, and the default is the fail-closed
    # one: a caller that never passes this gets the unbuilt-commit refusal rather than a
    # manifest whose image nobody established.
    published_images: Sequence[PublishedImage] = (),
    # WHAT THE DAY HAS ALREADY COMMITTED WITH NOBODY ASKED, OR ``None`` WHERE NOTHING IS
    # BOUNDING THE DAY. Read by the compile job out of the run index and passed in for the
    # reason every other environmental fact is: this function is given loaded configuration
    # and reads no file. ``edullm check`` passes nothing, because a laptop holds no ledger
    # and the sentence it would print instead is one about the tool rather than about the
    # run; the deferred checks the CLI already names are where that belongs.
    #
    # It can only raise the class. See daily_ceiling.class_under_the_ceiling.
    daily_ceiling: CeilingReading | None = None,
    # WHICH REPOSITORIES HAVE BEEN WATCHED RESUMING, defaulted to none for the reason
    # ``published_images`` is defaulted to nothing: a caller that never passes it gets the
    # refusal rather than a second attempt nobody established the value of.
    resume_demonstrations: ResumeDemonstrations = NO_RESUME_DEMONSTRATIONS,
) -> CompiledSubmission:
    workload = _resolve_workload(
        catalog, inputs.workload_profile, repository=inputs.repository
    )
    if workload.repository != inputs.repository:
        # TWO FIELDS THAT MUST AGREE, AND NOTHING COMPARED THEM. A submission naming
        # repository OLMo-core with workload profile dolma-tokenize-smoke was accepted,
        # compiled, classified routine and routed to a lead. What would then have run is
        # whichever image the digest named, under a workload contract written for a
        # different codebase -- so the runtime bound, the attempt bound and the checkpoint
        # contract would all be the other repository's.
        #
        # Refused here rather than at admission because this needs nothing from the
        # account: both sides are in the catalog the compile job already reads. Everything
        # before Batch is cheap, and an approval spent on a submission that cannot be
        # coherent is the expensive thing to avoid.
        raise WorkloadProfileRepositoryMismatchError(
            f"change the repository to {workload.repository!r}, or name a workload profile "
            f"registered for {inputs.repository!r}. Workload profile {workload.name!r} "
            f"belongs to repository {workload.repository!r} and this submission names "
            f"{inputs.repository!r}. A profile fixes the runtime bound, the attempt bound "
            "and the checkpoint contract for the codebase it was written against, so the "
            "two have to be the same repository."
        )

    # After the repository check and before anything else, because the images below were
    # read out of whichever ECR repository the declared one resolves to: a submission whose
    # repository and workload disagree has already named the wrong registry, and resolving
    # against it first would refuse the image before saying which of the two fields to fix.
    resolved_image = resolve_image(
        commit_sha=inputs.commit_sha,
        published=published_images,
        override=inputs.image_digest,
    )

    fanout = (
        FanOut(
            size=inputs.fanout_size,
            index_parameter=inputs.fanout_index_parameter,
        )
        if inputs.fanout_size is not None and inputs.fanout_index_parameter is not None
        else None
    )

    attempts = (
        inputs.maximum_attempts
        if inputs.maximum_attempts is not None
        else workload.maximum_attempts
    )
    if attempts > 1 and workload.checkpoint is None:
        raise RetryWithoutACheckpointContractError(
            f"ask for one attempt, or add a checkpoint contract to workload profile "
            f"{workload.name!r} in config/workload-catalog.yaml. It declares none today, so "
            f"asking for {attempts} attempts would produce a run that restarts from nothing."
        )

    # THE FORM'S IMAGE FIELD IS OPTIONAL AND THE MANIFEST'S IS NOT, AND THE ASYMMETRY IS
    # THE POINT. It is tempting to relax contracts/manifest.py to match the form and stop
    # having two spellings of one field. Two reasons not to, and the second is the real
    # one. Mechanically, RunManifest is the model the recorded contract inventory records a
    # structural digest for and the model the canonical hash is taken over, so making the
    # field optional moves that hash, the schema version and a recorded digest.
    # Substantively, what it would buy is the ability to express a run whose image
    # is unknown -- and the lineage record is the one document in this system that must
    # never be able to say that. A form may leave the image to be derived; a record of what
    # ran may not leave it undetermined. So the field is filled in here, from the
    # resolution above, on every path.
    #
    # Checked here rather than on SubmissionInputs, so that what a submitter meets is a
    # sentence rather than a pydantic dump. Same split as `team`, and checked before the
    # manifest is built rather than after, because the experiment is no longer part of it
    # -- see CompiledSubmission.experiment for why a grouping key cannot live in a hashed
    # record.
    if not fullmatch(SLUG_PATTERN, inputs.experiment):
        raise ExperimentNotASlugError(
            "write the experiment in lower-case letters and digits, with single hyphens "
            "between words and none at either end. context-length-sweep and "
            f"tokenizer-ablation have that shape and {inputs.experiment!r} does not. It "
            "registers nothing and needs no pull request. Only the shape is fixed, so that "
            "two people naming the same experiment get one group rather than two."
        )

    manifest = RunManifest(
        schema_version=1,
        repository=inputs.repository,
        commit_sha=inputs.commit_sha,
        image_digest=resolved_image.image_digest,
        dataset_release=inputs.dataset_release,
        command=inputs.command,
        team=inputs.team,
        wandb_project=inputs.wandb_project,
        workload_profile=workload.name,
        compute_profile=inputs.compute_profile,
        maximum_runtime_hours=(
            inputs.maximum_runtime_hours
            if inputs.maximum_runtime_hours is not None
            else workload.maximum_runtime_hours
        ),
        maximum_attempts=attempts,
        checkpoint=workload.checkpoint,
        fanout=fanout,
    )

    # AFTER THE MANIFEST RATHER THAN BEFORE IT, BECAUSE THE PROFILE THE COMMAND IS CHECKED
    # AGAINST HAS TO BE THE ONE THE RUN LANDS ON. `compute_profile` is an override on the
    # form, so the workload's own profile is not always it, and there is exactly one line
    # above that resolves the two -- reading the resolved value back off the manifest keeps
    # that line the only place the override is applied.
    #
    # Inside compile_submission rather than beside it, which is the opposite of where
    # require_submitter_on_the_roster sits and for the reason recorded there: that check is
    # about who dispatched the workflow, and this function is deliberately given no identity.
    # This one is entirely about what was filled in, so it belongs where the form is turned
    # into a manifest.
    require_a_process_for_every_device(
        command=manifest.command,
        compute_profile=manifest.compute_profile,
    )
    require_a_tensor_parallel_flag_vllm_reads(manifest.command)

    # THE OTHER HALF OF THE RULE THE ATTEMPT CHECK ABOVE ALREADY HOLDS. That one refuses a
    # retry bound with no checkpoint contract behind it; this refuses a checkpoint contract
    # with no command behind it, which is the direction that costs twelve hours of GPU time
    # rather than an argument about a form field. Both say a retry bound and a checkpoint
    # contract have to agree, and only one of them was ever checked.
    #
    # Beside the device-count rule rather than up beside the attempt check, because the two
    # here are the same kind of thing -- a rule about the text of a submitted command, needing
    # the manifest's resolved values -- and reading them in one place is what stops a third
    # one being added somewhere else again.
    require_a_save_folder_a_retry_can_find(
        command=manifest.command,
        workload_profile=manifest.workload_profile,
        checkpoint=manifest.checkpoint,
    )

    # AND THE HALF NEITHER OF THOSE REACHES, WHICH IS WHETHER THE PROGRAM EVER LOADS BACK
    # WHAT IT SAVED. The two above establish that a contract exists and that the command
    # names the prefix, and both pass for a trainer that writes there and never reads. This
    # asks whether anybody has watched this repository resume, which is the only question
    # whose answer justifies the attempt factor in the price.
    require_a_demonstrated_resume_for_retries(
        command=manifest.command,
        maximum_attempts=manifest.maximum_attempts,
        repository=manifest.repository,
        workload_profile=manifest.workload_profile,
        demonstrations=resume_demonstrations,
    )

    # THE LAST RULE ABOUT THE TEXT OF A COMMAND, AND THE ONE WHOSE COST LANDS ON THE DEVICE
    # RATHER THAN IN THE RECORD. The ones above refuse a command that would waste a machine
    # or lose its state; this refuses one the machine cannot run at all, because bfloat16 is
    # absent from Turing's silicon and the only shape above four cards this account can
    # obtain is eight T4s. It is beside them for the reason they are beside each other: every
    # one of them needs the manifest's resolved compute profile, and reading them in one
    # place is what stops the next being added somewhere else.
    #
    # It takes the catalog, which the others do not, because the answer comes from the
    # instance type the catalog declares rather than from the profile's name. That is the
    # whole of why this rule survives a shape being added: config/workload-catalog.yaml is
    # the only place the set of shapes is written down.
    require_bfloat16_only_where_the_hardware_has_it(
        command=manifest.command,
        compute_profile=manifest.compute_profile,
        catalog=catalog,
    )

    try:
        cost = compute_manifest_cost_inputs(manifest, catalog)
    except ValueError as exc:
        raise UnpriceableComputeProfileError(
            "name a compute profile config/workload-catalog.yaml registers. "
            f"{manifest.compute_profile!r} has no rate there, so the submission cannot be "
            "priced and policy denies it outright."
        ) from exc

    # THE FORM SAYS `team` AND THE VALIDATOR SAYS `claimed_team`, AND ONLY ONE OF THOSE IS
    # A PLACE THE SUBMITTER CAN GO AND FIX SOMETHING.
    #
    # The refusal itself is not new and is not added here: RunManifest.team takes any
    # non-empty string, RequestFacts.claimed_team is a TeamId, and TeamId carries
    # SLUG_PATTERN -- so a team with a capital or a space has always been rejected, one
    # line below. What escaped was a pydantic ValidationError, which is not the type the
    # submitting workflow reports as a refusal, quoting a field name that appears nowhere
    # on the form. `claimed_team` earns its name inside RequestFacts, where the distinction
    # between a claim and a fact is exactly what policy is built on and exactly why
    # membership is recorded rather than enforced. None of that is the submitter's problem.
    #
    # Translated rather than suppressed, and only this one field: a ValidationError from
    # any other part of the facts is a bug in this platform's own derivation, and turning
    # that into a refusal would blame a submitter for it.
    try:
        facts = build_request_facts(
            manifest,
            repositories=repositories,
            catalog=catalog,
            dataset_registry=dataset_registry,
            estimated_cost_usd=cost.maximum_compute_cost_usd,
            # The scan summary comes from the provenance record here, because the compile
            # job holds no AWS credentials and cannot ask ECR. Admission asks ECR itself and
            # fails closed on disagreement, so this value chooses the approval environment
            # and is never what the decision rests on -- the same split as the manifest hash.
            image_scan_policy=policy.image_scan,
            image_scan_registry=image_scan_registry,
            image_scan_summary=image_scan_summary,
            image_scan_findings=image_scan_findings,
        )
    except ValidationError as exc:
        if not any(error["loc"] == ("claimed_team",) for error in exc.errors()):
            raise
        raise TeamNotASlugError(
            "correct the team field on the submission form. A team is written in "
            "lower-case letters and digits, with single hyphens between words and none at "
            f"either end, like memory-split or olmo-core-eval, and {manifest.team!r} is not."
        ) from exc

    # Imported here rather than at module scope: admission owns this rule, and importing
    # it the other way round would make the compile step the authority on what is denied.
    from edullm_platform.admission import denied_outright_conditions

    tripped = denied_outright_conditions(facts, policy)
    if tripped:
        raise DeniedOutrightError(
            "the submission trips conditions policy denies outright rather than classifies: "
            f"{', '.join(tripped)}"
        )

    # THE PER-RUN QUESTION FIRST, THEN THE AGGREGATE ONE, AND NEVER THE OTHER WAY ROUND.
    # `classify_request` is re-derived inside AWS from the manifest and the deployed
    # thresholds, so it is the floor and it must go on being a pure function of the
    # submission. The ceiling then reads a ledger that only this side can see and may raise
    # what that answered. Nothing here can lower a class.
    approval_class = class_under_the_ceiling(
        classify_request(facts, policy.thresholds), reading=daily_ceiling
    )
    return CompiledSubmission(
        run_id=run_id,
        manifest=manifest,
        manifest_sha256=sha256_digest(manifest),
        facts=facts,
        approval_class=approval_class,
        approving_environment=ApprovalEnvironment.for_approval_class(approval_class),
        cost=cost,
        resolved_image=resolved_image,
        experiment=inputs.experiment,
    )


def _routing_note(inventory: OrganizationInventory, *, claimed_team: str) -> str:
    """Who this run would normally go to, and the sentence that stops that being a rule.

    WHAT DECLARING A TEAM BUYS HERE, AND WHAT IT DOES NOT. Any lead may release any run, so
    naming one is routing rather than authority. What the bindings can answer is "whose run
    is this and who would normally look at it", which is the question a reviewer opening an
    approval they were not expecting is actually asking.

    This used to say that the authorization path does not consult the bindings, and that was
    wrong in a way that mattered: ``evaluate_authorization`` checks the claimed team against
    the submitter's recorded membership and refuses a mismatch. It reads that membership per
    submitter, so declaring a team changes nothing for anybody whose own membership is
    unrecorded, which is what makes declaring one safe. It is not the no-op this paragraph
    once promised.

    The fallback is stated rather than left implicit, because naming an expected lead invites
    the reading that they are the only person who may act. If that were true an absent lead
    would be a stuck run and an unbound team an unusable one, and neither is: the gate admits
    any lead. Saying so here is what makes the routing safe to show at all.

    No team records a lead today, so the second branch is the ordinary path rather than the
    edge case. It says no lead is recorded instead of leaving a blank, because a blank where
    a name belongs reads as a lookup that broke.
    """
    bound = next(
        (team for team in inventory.team_bindings.teams if team.team_id == claimed_team),
        None,
    )
    if bound is not None and bound.lead_logins:
        routed = ", ".join(f"`{login}`" for login in bound.lead_logins)
        expected = f"Team `{claimed_team}` routes to {routed}."
    else:
        expected = f"No lead is recorded for team `{claimed_team}`."
    return (
        f"{expected} This is a hint and not a gate: **any team lead may release this run**, "
        "so an unrecorded or unavailable lead delays nobody."
    )


def _waiver_lines(manifest: RunManifest) -> tuple[str, ...]:
    """Whichever command checks this run waived, said where the person releasing it will see it.

    Empty for almost every submission, which is the point of it being a function rather than
    two rows in the table below. The command is not on this page -- it is long, it is often
    the least readable thing about a submission, and a reviewer is being asked about cost and
    attribution rather than about argv -- so a waiver written into it would otherwise reach
    nobody. Rows that said "not waived" on every run are the version of this that gets
    skipped.

    Both waivers can be on one command and each is stated separately, because they answer
    different questions: one says a process count is deliberate and the other says a
    checkpoint path is, and a run that waived one has said nothing about the other.
    """
    notes = (
        waived_launch_check_note(
            command=manifest.command,
            compute_profile=manifest.compute_profile,
        ),
        waived_checkpoint_check_note(
            command=manifest.command,
            workload_profile=manifest.workload_profile,
            checkpoint=manifest.checkpoint,
        ),
    )
    return tuple(line for note in notes if note is not None for line in (note, ""))


def _retry_lines(
    manifest: RunManifest, demonstrations: ResumeDemonstrations
) -> tuple[str, ...]:
    """What the attempt factor above buys, for a request that asks for more than one.

    Under the worst case rather than beside the table, because the sentence is about a
    number the approver has just read: the ceiling multiplies by attempts and says nothing
    about whether the later ones reach further than the first. Empty for a single-attempt
    run, which has nothing to qualify.

    A section of its own rather than a line among the waivers. The two waiver notes report
    something the submitter did; this reports something the platform does not know, and it
    is owed to every lead releasing a multi-attempt run rather than to the unusual ones.
    """
    said = resume_note(
        command=manifest.command,
        maximum_attempts=manifest.maximum_attempts,
        repository=manifest.repository,
        workload_profile=manifest.workload_profile,
        checkpoint=manifest.checkpoint,
        demonstrations=demonstrations,
    )
    if said is None:
        return ()
    return ("## What the second attempt buys", "", said, "")


def _daily_ceiling_lines(
    submission: CompiledSubmission,
    *,
    policy: ApprovalPolicy,
    reading: CeilingReading | None,
) -> tuple[str, ...]:
    """The section that appears only when the day is the reason this request is here.

    Empty for every submission that would have reached a lead anyway, which is most of the
    ones a lead ever sees. A section printed on all of them would be a paragraph nine people
    learn to scroll past, and the one submission it exists for is the one where scrolling
    past it loses the whole point: a request whose worst case is a few dollars, which a
    reader would otherwise release without asking why they were asked.

    Whether the day is the reason is answered by re-asking ``classify_request``, which is
    the only exact form of the question. Comparing the cost against the per-run bound was
    the obvious shortcut and it is wrong on every submission held back by one of the three
    tests that are not about money: a cheap fan-out, a cheap run on an unreviewed digest and
    a cheap run whose inputs do not resolve all reach a lead on their own merits, and all
    three would have been told the day put them there.
    """
    if reading is None or not reading.asks_a_lead:
        return ()
    if classify_request(submission.facts, policy.thresholds) is not ApprovalClass.AUTOMATIC:
        return ()
    return (
        "## Why this is in front of you",
        "",
        reading.said,
        "",
        (
            "**This request is under the per-run bound and would have been released by "
            "nobody on any other day.** What put it here is the account's daily ceiling on "
            "spending nobody looks at, in `config/policy.yaml`. You are being asked about "
            "the day rather than about this run, so the question is whether the account "
            "should go on committing money unattended today and not whether this figure is "
            "reasonable. The ceiling resets at midnight UTC."
        ),
        "",
    )


def render_approver_context(
    submission: CompiledSubmission,
    *,
    submitter: str,
    policy: ApprovalPolicy,
    repository_url: str,
    inventory: OrganizationInventory,
    wandb_username: str | None = None,
    placement_note: str | None = None,
    scan_note: str | None = None,
    run_history: RunHistory | None = None,
    daily_ceiling: CeilingReading | None = None,
    resume_demonstrations: ResumeDemonstrations = NO_RESUME_DEMONSTRATIONS,
) -> str:
    """What the reviewer reads before deciding, as GitHub step-summary markdown.

    GitHub's approval notification carries none of this, so the reviewer has to open the
    run. That is a real limitation of the mechanism and not something this function can fix;
    what it can do is make the summary complete enough that opening the run is sufficient.

    ``placement_note`` is :func:`~edullm_platform.placement.placement_warning`'s answer for
    the shape this run asked for, and it is passed in rather than derived here for the
    reason every other reviewed fact is: this function is given loaded configuration and
    reads no file. It is a sentence for the submitter as much as for the approver -- this
    markdown is the step summary on the run page, which is where both of them look -- and
    it sits above the table because a shape that may never start is worth knowing before
    the cost of running it.

    ``scan_note`` is :func:`~edullm_platform.admission.image_scan_refusal_detail`'s sentence
    for a digest whose registry findings carry no recorded review, and it is passed in for
    the same reason: the verdict needs the summary, the findings and the exception registry,
    and this function is given loaded configuration and reads no file.

    ``run_history`` is the reading ``config/run-history.json`` carries, passed in for that
    reason too, and ``None`` means no reading was available rather than that this shape has
    never run. :func:`~edullm_platform.run_history.history_for` is what tells those apart in
    words, and the section is printed either way.

    **IT IS REQUIRED READING RATHER THAN A NOTE, WHICH IS WHY IT HAS A SECTION AND THE
    PLACEMENT WARNING DOES NOT.** Until policy v4 an unreviewed digest was denied outright
    and no approver could release one, so there was nothing for a person to decide and
    nothing to show them. v4 made it an admin's call and v5 makes it a team lead's, and at
    each step the section matters more rather than less: an approval given without the
    findings in front of the approver is worse than the gate that refused everybody, and
    v5 widened who is asked. The sentence names which of the four scan verdicts happened,
    because only one of them is a judgement anybody can make: a scan that has not finished
    and a set of findings this platform failed to read are both "come back later" rather
    than "decide".

    **THERE IS NO "WHY THIS IS AN EXCEPTION" SECTION AND THERE WAS ONE.** It listed which
    routine ceiling a request had crossed, and under v5 there are no routine ceilings and
    no run classifies as an exception. What replaced it is the pair of sections above: the
    worst case, which is what is being authorised, and what runs of this shape have
    actually taken, which is what the worst case overstates.
    """
    manifest = submission.manifest
    cost = submission.cost
    short_sha = manifest.commit_sha[:12]
    # Answered even when no reading was passed, because the answer to "what have runs like
    # this taken" is a sentence in every case and one of the cases is that nobody has
    # measured. A section that appeared only when there was a figure would let an approver
    # read its absence as a reassurance.
    history = history_for(manifest, history=run_history)
    lines = [
        f"# Run submission `{submission.run_id}`",
        "",
        (
            f"**{submission.approval_class.value.upper()}**. This request must be released "
            f"by the `{submission.approving_environment.value}` gate."
        ),
        "",
        _routing_note(inventory, claimed_team=manifest.team),
        "",
        *((placement_note, "") if placement_note is not None else ()),
        *_waiver_lines(manifest),
        "| | |",
        "| --- | --- |",
        f"| Submitter | `{submitter}` |",
        f"| Team claimed | `{manifest.team}` |",
        f"| Repository | [{manifest.repository}]({repository_url}) |",
        f"| Commit | [`{short_sha}`]({repository_url}/commit/{manifest.commit_sha}) |",
        f"| Image digest | `{manifest.image_digest}` |",
        f"| Dataset release | `{manifest.dataset_release}` |",
        f"| Workload profile | `{manifest.workload_profile}` |",
        (
            f"| Compute profile | `{manifest.compute_profile}` at "
            f"${_plain(cost.hourly_rate_usd)}/hour |"
        ),
        f"| Policy version | `{policy.policy_version}` |",
        # WHOSE NAME THIS RUN WILL CARRY IN W&B, SAID BEFORE THE RUN RATHER THAN FOUND
        # AFTERWARDS. An unattributed run works -- it logs, it charts, it finishes -- and
        # W&B reports nothing about the missing author: it simply shows the platform's own
        # service account, which is indistinguishable from a run nobody tried to attribute.
        # This page is the only moment a person sees the gap, and it names the submitter so
        # the fix is a line in config/organization.yaml rather than an investigation.
        (
            f"| W&B author | `{wandb_username}` |"
            if wandb_username is not None
            else (
                f"| W&B author | **this run will not be attributed**, no W&B account is "
                f"recorded for `{submitter}` |"
            )
        ),
        "",
        "## Worst-case cost",
        "",
        (
            f"`${_plain(cost.hourly_rate_usd)}/hour x {cost.nodes} node(s) x "
            f"{_plain(cost.maximum_runtime_hours)}h x {cost.maximum_attempts} attempt(s) x "
            f"{cost.cells} cell(s)` = **${_plain(cost.maximum_compute_cost_usd)}**"
        ),
        "",
        (
            "This is the ceiling, not an estimate. It is what the run may cost if every "
            "attempt runs to its full time bound, and it is what routed this request to "
            "you."
        ),
        "",
        *_retry_lines(manifest, resume_demonstrations),
        # SAID ONLY WHERE IT IS THE REASON, AND THAT IS WHAT MAKES IT WORTH READING. A run
        # over `automatic_below_cost_usd` was always going to reach a lead and the day has
        # nothing to do with why. A run under it that reaches one anyway is here because the
        # account has spent what it will spend unattended today, and an approver who is not
        # told that reads a $12 request and cannot see what they are actually being asked.
        *_daily_ceiling_lines(submission, policy=policy, reading=daily_ceiling),
        "## What runs of this shape have taken",
        "",
        history.said,
        "",
        (
            "Measured from the lineage store's attempt records, over runs that succeeded. "
            "It decides nothing: what you are releasing is the ceiling above, because that "
            "is what the submission is authorised to spend."
        ),
        "",
    ]

    if scan_note is not None:
        lines.extend(
            [
                "## Unreviewed image scan findings",
                "",
                scan_note,
                "",
                (
                    "**Read this before releasing.** Until policy v4 this was refused "
                    "outright and no approver could release it, and until v5 only an admin "
                    "could. Releasing it now is your judgement about this digest, recorded "
                    "against your name. The sentence above says what would clear it "
                    "instead, and three of the four things it can say are that nobody can "
                    "review anything yet."
                ),
                "",
                (
                    "This section is also the reason the run is in front of you at all. A "
                    "digest carrying unreviewed findings is never released automatically, "
                    "whatever it costs."
                ),
                "",
            ]
        )

    lines.extend(
        [
            "## Integrity",
            "",
            (
                f"Manifest SHA-256 `{submission.manifest_sha256}`. Recomputed inside AWS "
                "after approval and compared with this value; a submission whose content "
                "changed in between is refused there rather than here."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def cost_total(submission: CompiledSubmission) -> Decimal:
    return submission.cost.maximum_compute_cost_usd
