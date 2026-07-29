"""Turn an accepted manifest into the exact request that goes to Batch.

Pure. No SDK, no I/O, no clock. Everything here is a function of the manifest and of
deployed configuration, which is what lets the same code be checked locally and run inside
the validator Lambda without a second implementation.

**The resolution is two gates, not one, and they fail differently.**
``resolve_compute_profile_for_execution`` answers whether the catalog claims the profile is
runnable; :func:`resolve_execution_target` then answers whether anything actually backs that
claim. A profile that is priced but not provisioned is an honest "ask for something else". A
profile the catalog calls provisioned with no execution target is two configuration files
disagreeing, and it raises :class:`~.contracts.execution.UnbackedComputeProfileError` so the
two are distinguishable in a decision record.

**Why the submit request is built here rather than in the state machine's ASL.** The
timeout, the retry count and the array size are all derived from manifest fields, and
Amazon States Language can do arithmetic only awkwardly and cannot be unit-tested. Building
the whole parameter block in Python means the ASL passes a structure through rather than
computing one, and means the mutation that matters -- a hardcoded timeout, a dropped retry
count -- is visible to a test. The seam between what this builds and what the ASL sends is
itself asserted, because Phase 2 shipped a state machine whose two sides had each been
checked and never compared.

**The timeout is unconditional.** Every submit carries ``attemptDurationSeconds``. The
master plan requires a mandatory timeout, and the specific way that requirement dies quietly
is a timeout applied only when the manifest sets a runtime bound -- which passes every
fixture that sets one. ``RunManifest.maximum_runtime_hours`` is required by the contract, so
there is no case where the bound is unknown, and this function has no branch that omits it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Final

from .contracts.execution import (
    ExecutionTarget,
    ExecutionTargetCatalog,
    UnbackedComputeProfileError,
)
from .contracts.manifest import RunManifest
from .contracts.results import output_prefix
from .contracts.workload import (
    WorkloadCatalog,
    resolve_compute_profile_for_execution,
)

__all__ = [
    "MINIMUM_ATTEMPT_DURATION_SECONDS",
    "attempt_duration_seconds",
    "batch_submit_request",
    "resolve_execution_target",
]

#: Batch refuses an attempt duration below sixty seconds. A manifest may legitimately ask
#: for less -- 0.001 hours is 3.6 seconds and is a perfectly reasonable smoke test -- so the
#: floor is applied here rather than left for Batch to reject at submit time, which would
#: turn a valid manifest into a submission failure with a message about seconds.
MINIMUM_ATTEMPT_DURATION_SECONDS: Final = 60

SECONDS_PER_HOUR: Final = Decimal(3600)


def resolve_execution_target(
    *,
    compute_profile: str,
    catalog: WorkloadCatalog,
    targets: ExecutionTargetCatalog,
    account_id: str,
    partition: str = "aws",
) -> ExecutionTarget:
    """Where a run on this profile goes, or why it cannot go anywhere.

    Raises ``UnregisteredComputeProfileError`` when the catalog has never heard of the
    profile, ``UnprovisionedComputeProfileError`` when it is priced but no compute
    environment backs it, and :class:`UnbackedComputeProfileError` when the catalog says
    provisioned and this catalog has no entry. The three are separate because the first two
    are answers a submitter can act on and the third is a deployment somebody half did.

    The account id is an argument rather than something read from the environment so that
    this stays a pure function; the Lambda passes the account its context reports.
    """
    profile = resolve_compute_profile_for_execution(catalog, compute_profile)
    binding = targets.binding_for(profile.name)
    if binding is None:
        raise UnbackedComputeProfileError(
            f"compute profile {profile.name!r} is marked provisioned in the workload "
            "catalog but no execution target backs it; the two configuration files "
            "disagree about whether capacity exists"
        )
    region = binding.region
    return ExecutionTarget(
        compute_profile=profile.name,
        region=region,
        job_queue_arn=(
            f"arn:{partition}:batch:{region}:{account_id}:job-queue/{binding.job_queue}"
        ),
        job_definition_arn=(
            f"arn:{partition}:batch:{region}:{account_id}:"
            f"job-definition/{binding.job_definition}"
        ),
        execution_role_arn=f"arn:{partition}:iam::{account_id}:role/{binding.execution_role}",
        workload_role_arn=f"arn:{partition}:iam::{account_id}:role/{binding.workload_role}",
        log_group=binding.log_group,
    )


def attempt_duration_seconds(manifest: RunManifest) -> int:
    """The per-attempt bound Batch is told, in whole seconds and never below its floor.

    Rounded down rather than up, because the manifest's figure is a ceiling the approver
    was shown and a job permitted to run longer than the number on the approval is a job
    that outran its authorization.
    """
    requested = int(manifest.maximum_runtime_hours * SECONDS_PER_HOUR)
    return max(requested, MINIMUM_ATTEMPT_DURATION_SECONDS)


def batch_submit_request(
    *,
    manifest: RunManifest,
    target: ExecutionTarget,
    run_id: str,
) -> dict[str, Any]:
    """The exact parameter block the state machine sends to ``batch:SubmitJob``.

    Keys are Batch's own spelling, capitalised as the Step Functions SDK integration wants
    them, because this structure is passed through rather than translated. The ASL and this
    function are held to the same key set by a seam test; a field added here and not there
    would otherwise be silently dropped at the boundary.
    """
    request: dict[str, Any] = {
        # The run id, so the Batch job, the S3 keys and the execution name all carry the
        # same identifier and any two disagreeing is visible.
        "JobName": run_id,
        "JobQueue": target.job_queue_arn,
        "JobDefinition": target.job_definition_arn,
        "ContainerOverrides": {
            "Command": list(manifest.command),
            "Environment": [
                {"Name": "EDULLM_RUN_ID", "Value": run_id},
                {"Name": "EDULLM_TEAM", "Value": manifest.team},
                {"Name": "EDULLM_DATASET_RELEASE", "Value": manifest.dataset_release},
                {"Name": "EDULLM_COMMIT_SHA", "Value": manifest.commit_sha},
                # Told, not computed. The container could assemble this from the two
                # variables above, and a container that assembled it would be a container
                # that decides where its own output goes -- which is the same value the
                # workload role is scoped against, so a container that computed it
                # differently would simply be denied, at the end of a run rather than at
                # the start. Sending the whole prefix keeps one function the author of it.
                {
                    "Name": "EDULLM_OUTPUT_PREFIX",
                    "Value": output_prefix(team=manifest.team, run_id=run_id),
                },
                # THE PROJECT COMES FROM THE MANIFEST, NOT FROM THE COMMAND, AND THAT IS
                # THE WHOLE OF D4 IN ONE LINE.
                #
                # A training command needs a W&B project to write to and could perfectly
                # well carry one in its own argv. It must not. The key in the container
                # authenticates a shared platform-owned account; it does not attribute. What
                # a run is labelled with is our assertion, derived from the same admission
                # record that was approved -- so a submitter who wrote a different project
                # into their command would be attributing their spend somewhere the decision
                # record does not say, and nothing downstream would notice.
                #
                # This is the same reasoning that has the state machine read the image scan
                # itself rather than accept findings from a caller.
                #
                # The container still has to be trusted to use it, which this cannot force.
                # What it removes is the need to supply it, which is the difference between
                # a submitter choosing an attribution and a submitter overriding one.
                {"Name": "EDULLM_WANDB_PROJECT", "Value": manifest.wandb_project},
            ],
        },
        # Unconditional. See the module docstring for why there is no branch here.
        "Timeout": {"AttemptDurationSeconds": attempt_duration_seconds(manifest)},
        "RetryStrategy": {"Attempts": manifest.maximum_attempts},
        "Tags": {
            "edullm:run-id": run_id,
            "edullm:team": manifest.team,
            "edullm:compute-profile": target.compute_profile,
        },
        # Batch propagates job tags to the underlying ECS task only when asked, and the
        # tags are what Phase 5's cost attribution will read.
        "PropagateTags": True,
    }
    if manifest.fanout is not None:
        # Absent for a single container rather than present with size one: Batch rejects an
        # array job of size one, so emitting ArrayProperties unconditionally would fail
        # every non-fan-out submission -- and no fan-out fixture would catch it.
        request["ArrayProperties"] = {"Size": manifest.fanout.size}
    return request
