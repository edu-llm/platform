"""Turn what a researcher filled in on a form into the manifest policy judges.

Two jobs, and the separation between them is the whole design. Compiling happens in a job
that holds no ``id-token`` permission and reads no secret, so the classification that
decides which gate a submission goes to is computed before anything can reach AWS. The
workflow then names that gate through ``needs``, never through ``inputs`` — GitHub permits
either, and the wrong one lets a submitter choose their own approval path.

The form collects what a person genuinely chooses and derives the rest. A workload profile
already fixes the compute profile, the runtime bound, the attempt bound and the checkpoint
contract, so asking for them again invites a submitter to contradict the catalog. They stay
available as explicit overrides, because a sweep that needs longer than its profile's
default is ordinary — and an override is visible to the approver in a way a silently
different default would not be.

The image is the same question in its sharpest form. It used to be a required
seventy-one-character field that had to agree with the declared commit and was compared with
nothing, so a submission could name commit A beside an image built from commit B and be
faultless on every field. It is derived from the commit now, by
:mod:`edullm_platform.image_resolution`, out of what the resolve job read back from the
registry — and what survives is an override with the same visibility as the other six.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from re import fullmatch
from typing import Annotated, Self

from pydantic import BeforeValidator, Field, ValidationError, model_validator

from edullm_platform.canonical import sha256_digest
from edullm_platform.contracts.admission import ApprovalEnvironment
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
from edullm_platform.contracts.workload import CostInputs, WorkloadCatalog, WorkloadProfile
from edullm_platform.errors import SubmissionRefusedError
from edullm_platform.image_resolution import PublishedImage, ResolvedImage, resolve_image
from edullm_platform.manifest_helpers import (
    build_request_facts,
    compute_manifest_cost_inputs,
)

__all__ = [
    "CompiledSubmission",
    "SubmissionInputs",
    "compile_submission",
    "render_approver_context",
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
    question rather than a platform constraint. Eight are required and seven are overrides a
    submitter can leave alone. The newest of the seven is the image digest, which stopped
    being required when it started being derived, and which was the hardest of the eight to
    fill in by some distance.
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
    compute_profile: str | None = Field(default=None, min_length=1)
    maximum_runtime_hours: PositiveStrictDecimal | None = Field(default=None, gt=0)
    maximum_attempts: int | None = Field(default=None, ge=1)
    fanout_size: int | None = Field(default=None, ge=2)
    fanout_parallelism: int | None = Field(default=None, ge=1)
    fanout_index_parameter: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_fanout_is_whole_or_absent(self) -> Self:
        declared = (
            self.fanout_size is not None,
            self.fanout_parallelism is not None,
            self.fanout_index_parameter is not None,
        )
        if any(declared) and not all(declared):
            raise ValueError(
                "a fan-out must declare its size, its parallelism and what its index varies, "
                "or none of the three"
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


def _resolve_workload(catalog: WorkloadCatalog, name: str) -> WorkloadProfile:
    for workload in catalog.workloads:
        if workload.name == name:
            return workload
    registered = ", ".join(sorted(profile.name for profile in catalog.workloads))
    raise SubmissionRefusedError(
        f"unregistered workload profile {name!r}; the catalog registers: {registered}"
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
) -> CompiledSubmission:
    workload = _resolve_workload(catalog, inputs.workload_profile)
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
        raise SubmissionRefusedError(
            f"workload profile {workload.name!r} belongs to repository "
            f"{workload.repository!r} and this submission names {inputs.repository!r}. A "
            "workload profile fixes the runtime bound, the attempt bound and the checkpoint "
            "contract for the codebase it was written against, so the two have to be the "
            "same repository."
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
            max_parallel=inputs.fanout_parallelism,
            index_parameter=inputs.fanout_index_parameter,
        )
        if inputs.fanout_size is not None
        and inputs.fanout_parallelism is not None
        and inputs.fanout_index_parameter is not None
        else None
    )

    attempts = (
        inputs.maximum_attempts
        if inputs.maximum_attempts is not None
        else workload.maximum_attempts
    )
    if attempts > 1 and workload.checkpoint is None:
        raise SubmissionRefusedError(
            f"workload profile {workload.name!r} declares no checkpoint contract, so it "
            f"cannot be retried; asking for {attempts} attempts would produce a run that "
            "restarts from nothing. Raise the attempt bound on a workload that checkpoints, "
            "or add a checkpoint contract to this one."
        )

    # THE FORM'S IMAGE FIELD IS OPTIONAL AND THE MANIFEST'S IS NOT, AND THE ASYMMETRY IS
    # THE POINT. It is tempting to relax contracts/manifest.py to match the form and stop
    # having two spellings of one field. Two reasons not to, and the second is the real
    # one. Mechanically, RunManifest is the model every phase's proof bundle records a
    # structural digest for and the model the canonical hash is taken over, so making the
    # field optional moves that hash, the schema version and a cell in four committed
    # bundles. Substantively, what it would buy is the ability to express a run whose image
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
        raise SubmissionRefusedError(
            f"the experiment {inputs.experiment!r} is not a name this platform can group on. "
            "An experiment is written in lower-case letters and digits, with single hyphens "
            "between words and none at either end -- context-length-sweep, tokenizer-ablation. "
            "It registers nothing and needs no pull request; only the shape is fixed, so that "
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
        compute_profile=inputs.compute_profile or workload.compute_profile,
        maximum_runtime_hours=(
            inputs.maximum_runtime_hours
            if inputs.maximum_runtime_hours is not None
            else workload.maximum_runtime_hours
        ),
        maximum_attempts=attempts,
        checkpoint=workload.checkpoint,
        fanout=fanout,
    )

    try:
        cost = compute_manifest_cost_inputs(manifest, catalog)
    except ValueError as exc:
        raise SubmissionRefusedError(
            f"unregistered compute profile {manifest.compute_profile!r}; it has no rate, so "
            "the submission cannot be priced and policy denies it outright"
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
        raise SubmissionRefusedError(
            f"the team {manifest.team!r} is not a team name this platform can record. A "
            "team is written in lower-case letters and digits, with single hyphens between "
            "words and none at either end -- memory-split, data, olmo-core-eval. Correct "
            "the team field on the submission form."
        ) from exc

    # Imported here rather than at module scope: admission owns this rule, and importing
    # it the other way round would make the compile step the authority on what is denied.
    from edullm_platform.admission import denied_outright_conditions

    tripped = denied_outright_conditions(facts, policy)
    if tripped:
        raise SubmissionRefusedError(
            "the submission trips conditions policy denies outright rather than classifies: "
            f"{', '.join(tripped)}"
        )

    approval_class = classify_request(facts, policy.thresholds)
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


def _exceeded_bounds(submission: CompiledSubmission, policy: ApprovalPolicy) -> tuple[str, ...]:
    """Which routine ceilings this submission is over, said in words.

    A cost figure on its own invites a rubber stamp. Which bound was exceeded is the single
    most decision-relevant thing an approver can be told, so it is stated rather than left
    to be inferred from a table of numbers.
    """
    facts = submission.facts
    limits = policy.thresholds
    exceeded: list[str] = []
    if facts.estimated_cost_usd > limits.routine_maximum_cost_usd:
        exceeded.append(
            f"worst-case cost ${_plain(facts.estimated_cost_usd)} exceeds the routine "
            f"ceiling of ${_plain(limits.routine_maximum_cost_usd)}"
        )
    if facts.maximum_runtime_hours > limits.routine_maximum_runtime_hours:
        exceeded.append(
            f"runtime bound of {_plain(facts.maximum_runtime_hours)}h exceeds the routine "
            f"ceiling of {_plain(limits.routine_maximum_runtime_hours)}h"
        )
    if facts.maximum_attempts > limits.routine_maximum_attempts:
        exceeded.append(
            f"attempt bound of {facts.maximum_attempts} exceeds the routine ceiling of "
            f"{limits.routine_maximum_attempts}"
        )
    if facts.fanout_size > limits.routine_maximum_fanout_size:
        exceeded.append(
            f"fan-out size of {facts.fanout_size} exceeds the routine ceiling of "
            f"{limits.routine_maximum_fanout_size}"
        )
    if facts.fanout_parallelism > limits.routine_maximum_parallelism:
        exceeded.append(
            f"fan-out parallelism of {facts.fanout_parallelism} exceeds the routine ceiling "
            f"of {limits.routine_maximum_parallelism}"
        )
    return tuple(exceeded)


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


def render_approver_context(
    submission: CompiledSubmission,
    *,
    submitter: str,
    policy: ApprovalPolicy,
    repository_url: str,
    inventory: OrganizationInventory,
    wandb_username: str | None = None,
) -> str:
    """What the reviewer reads before deciding, as GitHub step-summary markdown.

    GitHub's approval notification carries none of this, so the reviewer has to open the
    run. That is a real limitation of the mechanism and not something this function can fix;
    what it can do is make the summary complete enough that opening the run is sufficient.
    """
    manifest = submission.manifest
    cost = submission.cost
    short_sha = manifest.commit_sha[:12]
    lines = [
        f"# Run submission `{submission.run_id}`",
        "",
        (
            f"**{submission.approval_class.value.upper()}** — this request must be released "
            f"by the `{submission.approving_environment.value}` gate."
        ),
        "",
        _routing_note(inventory, claimed_team=manifest.team),
        "",
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
                f"| W&B author | **this run will not be attributed** — no W&B account is "
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
            "attempt runs to its full time bound."
        ),
        "",
    ]

    if submission.approval_class is ApprovalClass.EXCEPTION:
        exceeded = _exceeded_bounds(submission, policy)
        lines.append("## Why this is an exception")
        lines.append("")
        if exceeded:
            lines.extend(f"- {reason}" for reason in exceeded)
        else:
            lines.append(
                "- No routine ceiling is exceeded; the submission is an exception because "
                "one of its inputs is not registered."
            )
        lines.append("")

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
