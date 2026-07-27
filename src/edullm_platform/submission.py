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
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BeforeValidator, Field, model_validator

from edullm_platform.canonical import sha256_digest
from edullm_platform.contracts.admission import ApprovalEnvironment
from edullm_platform.contracts.base import (
    ContractModel,
    PositiveStrictDecimal,
    require_ordered_sequence,
    serialize_decimal,
)
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.image_scan import (
    ImageScanExceptionRegistry,
    ImageScanSummary,
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
from edullm_platform.contracts.workload import CostInputs, WorkloadCatalog, WorkloadProfile
from edullm_platform.manifest_helpers import (
    build_request_facts,
    compute_manifest_cost_inputs,
)

__all__ = [
    "CompiledSubmission",
    "SubmissionInputs",
    "SubmissionRefusedError",
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


class SubmissionRefusedError(ValueError):
    """The form describes something that cannot be resolved into a manifest.

    Raised in the credential-free compile job, before a reviewer is asked for anything.
    Refusing here rather than letting the request reach a gate is deliberate: a submission
    naming an unregistered dataset is going to be denied by admission whatever a reviewer
    says, and spending a human's attention on it first teaches reviewers that approving is
    a formality.
    """


class SubmissionInputs(ContractModel):
    """The ``workflow_dispatch`` form.

    Fourteen properties against a ceiling of twenty-five, so the count is a usability
    question rather than a platform constraint. Eight are required and six are overrides a
    submitter can leave alone.
    """

    repository: str = Field(min_length=1)
    commit_sha: str = Field(pattern=COMMIT_SHA_PATTERN)
    image_digest: str = Field(pattern=IMAGE_DIGEST_PATTERN)
    workload_profile: str = Field(min_length=1)
    dataset_release: str = Field(min_length=1)
    team: str = Field(min_length=1)
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
    inventory: OrganizationInventory,
    catalog: WorkloadCatalog,
    dataset_registry: DatasetRegistry,
    image_scan_registry: ImageScanExceptionRegistry,
    image_scan_summary: ImageScanSummary | None = None,
) -> CompiledSubmission:
    workload = _resolve_workload(catalog, inputs.workload_profile)

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

    manifest = RunManifest(
        schema_version=1,
        repository=inputs.repository,
        commit_sha=inputs.commit_sha,
        image_digest=inputs.image_digest,
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

    facts = build_request_facts(
        manifest,
        inventory=inventory,
        catalog=catalog,
        dataset_registry=dataset_registry,
        estimated_cost_usd=cost.maximum_compute_cost_usd,
        # The scan summary comes from the provenance record here, because the compile job
        # holds no AWS credentials and cannot ask ECR. Admission asks ECR itself and fails
        # closed on disagreement, so this value chooses the approval environment and is
        # never what the decision rests on -- the same split as the manifest hash.
        image_scan_policy=policy.image_scan,
        image_scan_registry=image_scan_registry,
        image_scan_summary=image_scan_summary,
    )

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


def render_approver_context(
    submission: CompiledSubmission,
    *,
    submitter: str,
    policy: ApprovalPolicy,
    repository_url: str,
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
