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

**WHY A RUN REGISTERS A JOB DEFINITION OF ITS OWN, WHICH IS THE EXPENSIVE ANSWER.** There is
no submit-time image override in AWS Batch. This was checked against the API reference
rather than assumed, and it is worth writing down so that nobody searches for it twice:
``ContainerOverrides`` carries ``command``, ``environment``, ``instanceType``,
``resourceRequirements`` and the deprecated ``memory`` and ``vcpus``; ``TaskContainerOverrides``,
the ECS-properties path, carries ``command``, ``environment``, ``name`` and
``resourceRequirements``. Neither has an image field, and job-definition parameter
substitution with ``Ref::`` is documented for the ``command`` field only.
``RegisterJobDefinition`` is the only mechanism that can change a container's image.

That matters because until :func:`batch_register_job_definition_request` existed, the digest
a submitter declared was validated, gated admission through the ECR scan and was written
immutably into the S3 lineage record -- while the container that actually ran was whatever
``infra/batch-compute.yaml`` and ``infra/batch-compute-gpu.yaml`` pinned. The two coincided
only because ``config/image-exceptions.yaml`` happened to hold exactly the two digests the
templates pin. The lineage record's image provenance was true by convention rather than by
mechanism, and every other guarantee this platform makes is read back off that record.

The registration request is built here for the same reason the submit request is, and the
state machine template already argues it at length: a ``Parameters`` block reconstructing
the same structure in a template nobody unit-tests fails by silently omitting a field, and
omission is silent on every axis of a job definition -- a missing ``Secrets`` block is a
training run that cannot reach W&B, a missing ``LinuxParameters`` is a DataLoader bus error
partway into training, a missing GPU resource requirement is a container that trains on the
CPU at GPU prices and reports nothing wrong.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from .contracts.execution import (
    SANDBOX_RESOURCE_PREFIX,
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
    "PUBLISHED_IMAGE_REPOSITORY",
    "UnshapedComputeProfileError",
    "attempt_duration_seconds",
    "batch_register_job_definition_request",
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
    job_definition: str,
    wandb_username: str | None = None,
    # Beside the manifest rather than read off it, because a grouping key cannot live in a
    # hashed record without changing the digest of every record written before it existed.
    # CompiledSubmission.experiment carries the measurement. Optional here for the same
    # reason wandb_username is: a run admitted before the field existed has none, and an
    # empty tag value is a cost group named "" that Cost Explorer will happily total up.
    experiment: str | None = None,
) -> dict[str, Any]:
    """The exact parameter block the state machine sends to ``batch:SubmitJob``.

    Keys are Batch's own spelling, capitalised as the Step Functions SDK integration wants
    them, because this structure is passed through rather than translated. The ASL and this
    function are held to the same key set by a seam test; a field added here and not there
    would otherwise be silently dropped at the boundary.

    ``job_definition`` is required rather than defaulted to ``target.job_definition_arn``,
    and that is the point of it. The definition a run is submitted against is now the
    revision registered for that run, carrying the digest the manifest declared, and a
    default would let the old behaviour survive as an omission at a call site instead of as
    a change to this function. The compiler naming every caller is what makes the change
    reviewable.

    It is ``job_definition`` and not ``job_definition_arn``, which is a rename this change
    paid for rather than a preference. Batch's ``jobDefinition`` accepts a name, a
    ``name:revision``, or an ARN with or without the revision, and the admission handler
    passes the *name* the registration is about to mint -- because the revision ARN does
    not exist until Batch has answered, and the state machine is what puts it here. A
    parameter called ``_arn`` receiving a name is the kind of quiet inaccuracy that survives
    review and then misleads the next reader into building an ARN somewhere it is not
    needed.

    ``wandb_username`` names the human a run is attributed to, and defaults to ``None``
    because most of the roster has no recorded W&B account. It is a separate argument rather
    than something read off the manifest: what a run is labelled with is the platform's
    assertion, derived from the submitter admission recorded, and a submitter who could put
    it in their own manifest could put somebody else's name there.
    """
    request: dict[str, Any] = {
        # The run id, so the Batch job, the S3 keys and the execution name all carry the
        # same identifier and any two disagreeing is visible.
        "JobName": run_id,
        "JobQueue": target.job_queue_arn,
        "JobDefinition": job_definition,
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
                # WHERE CHECKPOINTS GO, FOR THE SAME REASON AND WITH A SHARPER EDGE. The
                # suffix is not a secret -- config/workload-catalog.yaml already records
                # that the path is output_prefix(team, run_id) + "checkpoints/" -- so this
                # is telling the container something the platform decided rather than
                # inventing a location.
                #
                # It is its own variable because of what the alternative costs. OLMo-core's
                # example defaults --save-folder to /tmp/{run_name}, which is on the
                # instance and gone when the instance is. A twelve-hour run that took the
                # default trains for twelve hours, writes checkpoints nobody can reach,
                # exits zero, and is recorded as a success. Making the path a variable is
                # what lets the guide print `--save-folder "$EDULLM_CHECKPOINT_DIR"` as one
                # line to copy, instead of a sentence about joining a prefix to a word --
                # which is the shape of instruction people get wrong at two in the morning.
                #
                # The run id is minted at compile time, so this cannot be filled in on the
                # form. It has to arrive through the environment, which is why a command
                # that needs it has to run under a shell: ContainerOverrides.Command is exec
                # form, and an unexpanded $EDULLM_CHECKPOINT_DIR reaches OLMo-core as a
                # literal path it will cheerfully create.
                {
                    "Name": "EDULLM_CHECKPOINT_DIR",
                    "Value": output_prefix(team=manifest.team, run_id=run_id) + "checkpoints/",
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
                # W&B'S OWN NAMES FROM HERE DOWN, because the wandb client reads these
                # itself. A prefixed copy would need every workload to forward it, and a
                # workload that forgot would log somewhere nobody looks under a name nobody
                # recognises -- which is the state this pair exists to end.
                #
                # The entity is named rather than left to the service account's default.
                # W&B's documented behaviour is that an unentitled team service account logs
                # into its parent team anyway, which is the same place, right up until it is
                # not.
                # The project under W&B's own name as well as the prefixed one above. The
                # prefixed spelling alone made the form's required `wandb_project` box
                # decorative: `EDULLM_WANDB_PROJECT` is not a name the client knows, and
                # nothing in OLMo-core, edullm-data or olmo-eval-full reads it, so a run
                # landed wherever its own training config said and the value the approver
                # read had no bearing on it.
                #
                # It does not take the choice away from a workload, which is what the
                # paragraph above is protecting. wandb applies an explicit argument over the
                # environment, and OLMo-core's WandBCallback defaults `project` to None -- so
                # a run that names its own project still wins, and a run that does not now
                # lands where the submission said it would.
                {"Name": "WANDB_PROJECT", "Value": manifest.wandb_project},
                {"Name": "WANDB_ENTITY", "Value": WANDB_ENTITY},
                # W&B's own name again, for the same reason the entity is: the client reads
                # WANDB_RUN_GROUP without being asked. A prefixed EDULLM_PROJECT would need
                # every workload to forward it, and one that forgot would produce ungrouped
                # runs -- indistinguishable from a submitter who left the field blank,
                # except that the field cannot be left blank.
                #
                # The grouping key itself is appended below, with the tag, because a manifest
                # written before the field existed has no value for it.
            ],
        },
        # Unconditional. See the module docstring for why there is no branch here.
        "Timeout": {"AttemptDurationSeconds": attempt_duration_seconds(manifest)},
        "RetryStrategy": {
            "Attempts": manifest.maximum_attempts,
            # A list rather than the module constant's tuple, because this dict is
            # serialised to JSON for Step Functions and compared against what the ASL
            # carries; a tuple would round-trip as a list and make the two look different.
            "EvaluateOnExit": [dict(rule) for rule in RETRY_ONLY_WHAT_A_RETRY_FIXES],
        },
        "Tags": {
            "edullm:run-id": run_id,
            "edullm:team": manifest.team,
            "edullm:compute-profile": target.compute_profile,
        },
        # Batch propagates job tags to the underlying ECS task only when asked, and the
        # tags are what Phase 5's cost attribution will read.
        "PropagateTags": True,
    }
    if wandb_username is not None:
        # APPENDED RATHER THAN SENT EMPTY, and the distinction is not cosmetic. W&B reads an
        # empty WANDB_USERNAME as an attribution that failed rather than as one that was
        # never attempted, and most of the roster has no recorded account -- so an
        # unconditional entry would turn every ordinary unattributed run into one that looks
        # broken.
        request["ContainerOverrides"]["Environment"].append(
            {"Name": "WANDB_USERNAME", "Value": wandb_username}
        )
    if experiment is not None:
        # Both together, because they are one fact told to two systems -- W&B groups runs on
        # the environment variable and Cost Explorer groups them on the tag, and a run that
        # appeared in one grouping and not the other would read as a billing discrepancy
        # rather than as a missing field.
        #
        # WANDB_RUN_GROUP keeps W&B's own spelling while the tag takes ours, and the two
        # disagreeing is not an oversight: the environment variable is a name the client
        # reads without being asked, so it is not ours to choose, whereas the tag key is.
        #
        # Conditional for the reason the parameter is optional: a run admitted before the
        # field existed carries none, and an empty tag value is a group named "" that Cost
        # Explorer will happily total up. Every submission compiled today passes one.
        request["ContainerOverrides"]["Environment"].append(
            {"Name": "WANDB_RUN_GROUP", "Value": experiment}
        )
        # Prefixed like its neighbours, and the prefix is load-bearing rather than decorative:
        # this is a shared sandbox account, Cost Explorer groups on the whole key, and a bare
        # `experiment` is a key somebody else's stack may also be writing.
        request["Tags"]["edullm:experiment"] = experiment
    if manifest.fanout is not None:
        # Absent for a single container rather than present with size one: Batch rejects an
        # array job of size one, so emitting ArrayProperties unconditionally would fail
        # every non-fan-out submission -- and no fan-out fixture would catch it.
        request["ArrayProperties"] = {"Size": manifest.fanout.size}
    refuse_an_oversized_override(request["ContainerOverrides"])
    return request


#: What Batch will accept as one job's ``containerOverrides``, serialized. An AWS service
#: limit rather than a choice of ours, and not adjustable.
MAXIMUM_CONTAINER_OVERRIDES_BYTES: Final = 8192


class ContainerOverridesTooLargeError(ValueError):
    """The command and its environment exceed what Batch will accept in one submission.

    THIS COST A RUN AND AN APPROVAL BEFORE IT EXISTED. A training program of 9,230 bytes
    was compiled, validated locally, dispatched, approved at the environment gate, admitted,
    and submitted -- and Batch refused it with "Container Overrides length must be at most
    8192". The message names neither the command nor the fact that a limit was reached by
    the field the submitter controls, so the obvious reading is that something is wrong with
    the job definition.

    The cost of finding it late is the whole reason this is checked here. Everything before
    Batch is cheap and reversible; the approval is a person's attention, and spending it on
    a submission that cannot be accepted is the one thing this path should never do.

    Refused at request-build time rather than at compile time, because the environment is
    part of the budget and is added here. A check over the command alone would pass a
    command that fits and an override that does not.
    """

    reason_code = "container_overrides_too_large"


#: When a second attempt is worth paying for, in the order Batch reads them -- it takes the
#: first rule that matches and ignores the rest, so the order is the policy.
#:
#: THIS COST TWO INSTANCE STARTS TO LEARN, ON THE NIGHT IT WAS WRITTEN. A twelve-hour
#: submission carried a config override OLMo-core refuses -- ``ephemeral_save_interval``
#: has to be below ``save_interval`` -- and the program died in the first few seconds.
#: ``RetryStrategy`` carried ``Attempts`` and nothing else, so Batch dutifully pulled a
#: three-gigabyte image onto a second GPU instance and ran the identical command into the
#: identical error. Nothing about the first failure could have been different the second
#: time, and the platform had no way to say so.
#:
#: The distinction the rules draw is whether the failure was about *this run* or about *the
#: machine it landed on*. A host going away is the only one a retry genuinely fixes, and it
#: is the one that matters most here: a reclaimed instance eleven hours into a twelve-hour
#: run is exactly the case the checkpoint contract exists for.
#: PASCAL CASE, AND THE LOWER-CASE SPELLING BREAKS EVERY SUBMISSION. The Batch API
#: documents these keys as ``onStatusReason``, ``onReason``, ``onExitCode`` and ``action``,
#: and that is what a reader checking the AWS reference will write. This request is not made
#: by the Batch SDK -- Step Functions makes it, through its ``aws-sdk:batch:submitJob``
#: integration, which requires the PascalCase spelling of every field and refuses the
#: documented one outright: ``The field "onStatusReason" is not supported by Step Functions.``
#:
#: The cost is what makes the distinction worth writing down. It fails at ``SubmitToBatch``,
#: which runs *after* WriteIntent and WriteDecision, so the lineage records say the run was
#: admitted and no job ever reaches a queue -- an accepted run that does not exist. It
#: applies to every submission and not only retryable ones, because this block is sent
#: unconditionally. Found by a submission failing exactly that way.
RETRY_ONLY_WHAT_A_RETRY_FIXES: Final = (
    # The host went away underneath a running attempt: hardware failure today, a Spot
    # reclaim once the A100 tier is promoted. The attempt died with work behind it and the
    # next one resumes from the last checkpoint, which is the whole argument for two
    # attempts.
    {"OnStatusReason": "Host EC2*", "Action": "RETRY"},
    # A container that did not fit will not fit on the identical instance type. Retrying
    # buys a second identical OOM and a second hour of the approved ceiling.
    {"OnReason": "*OutOfMemoryError*", "Action": "EXIT"},
    # Everything else, which is every exit code including 1 -- what a Python traceback
    # produces, and what a bad config override produces. Last, because Batch stops at the
    # first match and this one matches everything.
    {"OnExitCode": "*", "Action": "EXIT"},
)


def refuse_an_oversized_override(overrides: Mapping[str, Any]) -> None:
    """Refuse a submission Batch would reject, with the numbers that explain why.

    Measured over the serialized block rather than over the command's characters, because
    the environment, the JSON punctuation and the key names are all inside the same limit --
    a command comfortably under 8,192 can still push the override over it.

    Compact separators, matching what an SDK puts on the wire. This will not agree with
    Batch to the byte for every input, and does not need to: it is an early refusal with a
    readable reason, and Batch remains the authority. What it must not do is pass something
    Batch will reject, which is why the budget below is spent conservatively.
    """
    serialized = len(json.dumps(overrides, separators=(",", ":")).encode("utf-8"))
    if serialized <= MAXIMUM_CONTAINER_OVERRIDES_BYTES:
        return
    command = sum(len(word) for word in overrides.get("Command", []))
    raise ContainerOverridesTooLargeError(
        f"this submission's container overrides serialize to {serialized} bytes and Batch "
        f"accepts at most {MAXIMUM_CONTAINER_OVERRIDES_BYTES}. The command accounts for "
        f"{command} of them. A program this long belongs in the image, or in an object the "
        "container fetches, rather than in the command line."
    )


# ---------------------------------------------------------------------------------------
# The job definition a run registers for itself
# ---------------------------------------------------------------------------------------

#: The ECR repository the images this platform runs are published to.
#:
#: A constant, and the honest reading of that is that this is the *second* place the
#: repository is hardcoded rather than looked up: the admission state machine's
#: ReadImageScan state names the same string when it asks ECR for the declared digest's
#: findings. The two have to agree or the scan that gated admission was read against a
#: different image, so a seam test compares them.
#:
#: WHAT IS DELIBERATELY ABSENT AND WHERE IT BELONGS. The repository an image lives in is
#: properly a fact about the submission's source repository -- config/repositories.yaml
#: maps OLMo-core to sbsandbox-intern-edullm-olmo-core and edullm-data to
#: sbsandbox-intern-edullm-data, and the mapping is not derivable from the name. Looking it
#: up here would mean passing the registry in, and the state machine would still be reading
#: the scan from the wrong repository. Both are the same edit and it is not this one; the
#: first submission naming edullm-data is what forces it, and it will fail loudly at the
#: image pull rather than quietly.
PUBLISHED_IMAGE_REPOSITORY: Final = f"{SANDBOX_RESOURCE_PREFIX}olmo-core"

#: The W&B entity every run logs into, which is the parent team of the service account whose
#: key ``CONTAINER_SHAPES`` injects. The two belong together: a team-scoped service account
#: can only log to its own team, so changing one without the other is a run that authenticates
#: and then has nowhere to write. Named here rather than in ``config/organization.yaml``
#: because it is a property of that key rather than of the roster.
WANDB_ENTITY: Final = "eduLLM"

#: Batch's bound on a job definition name.
MAXIMUM_JOB_DEFINITION_NAME_LENGTH: Final = 128

#: The floors the registered definition carries, copied from the deployed definitions
#: because they are floors rather than settings. Every real submission overrides both --
#: ``batch_submit_request`` derives the attempt duration from the manifest and the retry
#: count from ``maximum_attempts`` -- so these are only reached by a job submitted against
#: this definition by hand, during an incident, and the point of them is that such a job
#: still cannot run unbounded.
JOB_DEFINITION_ATTEMPT_FLOOR: Final = 1
JOB_DEFINITION_TIMEOUT_FLOOR_SECONDS: Final = 3600


class UnshapedComputeProfileError(ValueError):
    """A profile with somewhere to run and no statement of what its container asks for.

    A third way for two files to disagree, alongside the two ``resolve_execution_target``
    already separates. ``config/execution-targets.yaml`` says where a profile's jobs go;
    the shapes below say what the container that runs there asks for, and a profile present
    in the first and absent from the second is a promotion somebody did half of.

    Refused rather than defaulted, because the cost of a guess is asymmetric and silent. A
    guessed CPU shape on a GPU profile produces a container with no device that trains at
    GPU prices and reports nothing wrong; a guessed GPU shape on a CPU profile produces a
    job that waits in RUNNABLE forever. Neither surfaces as an error anywhere.
    """

    reason_code = "unshaped_compute_profile"


@dataclass(frozen=True)
class ContainerShape:
    """What one compute profile's deployed job definition asks for, beside its image.

    THIS DUPLICATES infra/batch-compute.yaml AND infra/batch-compute-gpu.yaml, WHICH IS THE
    UNCOMFORTABLE PART OF THIS CHANGE AND IS NOT AN OVERSIGHT. RegisterJobDefinition is the
    only mechanism that can change a container's image, so a run that wants to be executed
    on the digest it declared has to restate a whole job definition in Python -- and the
    template remains the thing that is deployed, so the two are now two statements of one
    shape with nothing in CloudFormation connecting them.

    Two things follow. The values here are the templates' values verbatim, including the
    CPU definition's ``teams/data-prep/runs/`` default output prefix, which the GPU template
    already records as a default that fails open: reproducing it keeps a registered
    definition equal to the deployed one, and improving it here would be a second change
    hiding inside this one. And a seam test in tests/test_phase3_execution.py reads both
    templates and compares them field by field against what this builds, because a table
    that drifts from the templates is exactly as bad as no table.

    Moving this into ``config/execution-targets.yaml`` beside the queue and the roles is the
    better long-run home and was deliberately not done here: it would add fields to two
    contract models, and ``proof_bundle.discover_contract_models`` records every contract
    model's structural digest in four committed proof bundles.
    """

    vcpus: int
    memory_mib: int
    #: Zero for a CPU profile, and the entry is then omitted rather than sent as "0".
    #: Without a GPU entry ECS does not select the NVIDIA runtime for the task, so even on
    #: the NVIDIA AMI the container sees no device.
    gpus: int
    #: ``/dev/shm``, in MiB, or ``None`` to leave ECS's 64 MiB default in place. A PyTorch
    #: DataLoader with worker processes moves batches through shared memory and dies on the
    #: default partway into training, with a bus error naming neither shared memory nor the
    #: setting that fixes it.
    shared_memory_mib: int | None
    #: ``(container variable, Secrets Manager secret name)``. ECS resolves these under the
    #: execution role while starting the task, so the workload never holds a
    #: ``secretsmanager`` action of its own. The name carries the suffix Secrets Manager
    #: assigned, because ``ValueFrom`` is a lookup and not a pattern.
    secrets: tuple[tuple[str, str], ...]
    #: The default environment, which every real submission overrides. Declared rather than
    #: omitted because an override can only replace a key the definition declares.
    default_environment: tuple[tuple[str, str], ...]


CONTAINER_SHAPES: Final[Mapping[str, ContainerShape]] = {
    # 32 vCPU and 60 GiB against a c7i.8xlarge's 32 and 64. The gap is not rounding: the
    # ECS agent and the host's own processes need memory, and a container asking for all
    # 65536 MiB never fits on the instance it was sized for.
    "cpu-32vcpu": ContainerShape(
        vcpus=32,
        memory_mib=61440,
        gpus=0,
        shared_memory_mib=None,
        # THE SAME KEY THE GPU SHAPE CARRIES, AND A PILOT RUN PAID FOR THE ASYMMETRY. This
        # was an empty tuple, so a CPU workload that called wandb.init died on `No API key
        # configured` -- after being admitted, released by a lead and given an instance. The
        # submission form offers wandb_project on every profile and the container is told it
        # on every profile, so the key belonging to only one of them was never a decision;
        # W&B was wired up during the GPU training work and this shape was left behind.
        secrets=(("WANDB_API_KEY", f"{SANDBOX_RESOURCE_PREFIX}wandb-api-key-fnwEVp"),),
        default_environment=(
            ("EDULLM_OUTPUT_BUCKET", f"{SANDBOX_RESOURCE_PREFIX}outputs"),
            ("EDULLM_OUTPUT_PREFIX", "teams/data-prep/runs/"),
        ),
    ),
    # 4 vCPU and 15 GiB against a g5.xlarge's 4 and 16, leaving 1 GiB for the ECS agent and
    # the host.
    "gpu-1xa10g": ContainerShape(
        vcpus=4,
        memory_mib=15360,
        gpus=1,
        shared_memory_mib=4096,
        secrets=(("WANDB_API_KEY", f"{SANDBOX_RESOURCE_PREFIX}wandb-api-key-fnwEVp"),),
        default_environment=(
            ("EDULLM_OUTPUT_BUCKET", f"{SANDBOX_RESOURCE_PREFIX}outputs"),
            (
                "EDULLM_OUTPUT_PREFIX",
                f"s3://{SANDBOX_RESOURCE_PREFIX}outputs/no-submission-supplied-a-prefix/",
            ),
        ),
    ),
}


def job_definition_name(run_id: str) -> str:
    """What the definition registered for one run is called.

    The run id, under this project's resource prefix. Everything else in this platform
    joins on the run id -- it is the S3 key, the Step Functions execution name and the Batch
    job name -- and a definition registered for a run that then vanished has to be findable
    the same way. The prefix is what a ``batch:RegisterJobDefinition`` grant can be scoped
    to, so a name minted outside it would be a name the submitting role could not register.

    Sixty-four characters against Batch's 128, so the bound is checked rather than reasoned
    about: a run id is fixed-length today, and the check is here to fail loudly rather than
    at the API if either the prefix or the identifier ever grows.
    """
    name = f"{SANDBOX_RESOURCE_PREFIX}{run_id}"
    if len(name) > MAXIMUM_JOB_DEFINITION_NAME_LENGTH:
        raise ValueError(
            f"a job definition name may be at most {MAXIMUM_JOB_DEFINITION_NAME_LENGTH} "
            f"characters and {name!r} is {len(name)}"
        )
    return name


def partition_and_account(role_arn: str) -> tuple[str, str]:
    """The partition and account an ARN the resolver assembled was assembled from.

    Read back out rather than taken as arguments, because this function takes what
    ``batch_submit_request`` takes and nothing else. ``resolve_execution_target`` built
    these ARNs from an account the Lambda read off its own context, so the account in them
    is the account this code is running in by construction -- and reading it back keeps the
    account from becoming a second argument that some caller could pass a different value
    for.
    """
    segments = role_arn.split(":")
    return segments[1], segments[4]


def batch_register_job_definition_request(
    *,
    manifest: RunManifest,
    target: ExecutionTarget,
    run_id: str,
) -> dict[str, Any]:
    """The exact parameter block the state machine sends to ``batch:RegisterJobDefinition``.

    What comes out is the definition ``infra/batch-compute.yaml`` or
    ``infra/batch-compute-gpu.yaml`` already deploys for this profile, with two things
    changed: the name is the run's, and the image is the digest the manifest declared. Not
    a new shape -- see :class:`ContainerShape` for why the deployed shape is restated here
    at all, and for the seam test that holds the restatement to the templates.

    Keys are Batch's own spelling, capitalised as the Step Functions SDK integration wants
    them, because this structure is passed through whole rather than translated.

    Both roles come from the target and are set here rather than at submission, because
    Batch takes them when a definition is registered and nowhere else -- which is what
    :class:`~.contracts.execution.ExecutionTarget` already says about why it carries two
    roles that no submit request mentions.
    """
    shape = CONTAINER_SHAPES.get(target.compute_profile)
    if shape is None:
        raise UnshapedComputeProfileError(
            f"compute profile {target.compute_profile!r} has an execution target but no "
            "container shape, so what its job definition should ask for is unknown; the "
            "profile was promoted in config/execution-targets.yaml without being given a "
            "shape in src/edullm_platform/execution.py"
        )
    partition, account_id = partition_and_account(target.execution_role_arn)
    # The deployed definition's name, without a revision suffix. resolve_execution_target
    # never puts one there, but BatchJobDefinitionArn permits ``:5`` and a colon reaching
    # the log stream prefix below would be a stream nobody can find rather than an error.
    deployed_name = target.job_definition_arn.rsplit("/", maxsplit=1)[1].split(":", maxsplit=1)[0]

    resource_requirements: list[dict[str, str]] = [
        {"Type": "VCPU", "Value": str(shape.vcpus)},
        {"Type": "MEMORY", "Value": str(shape.memory_mib)},
    ]
    if shape.gpus > 0:
        resource_requirements.append({"Type": "GPU", "Value": str(shape.gpus)})

    container: dict[str, Any] = {
        # THE ONE FIELD THIS FUNCTION EXISTS FOR. Pinned by digest and never by a tag, for
        # the reason the templates give: a tag is a mutable pointer, so the bytes that run
        # would stop being the bytes the decision record names.
        "Image": (
            f"{account_id}.dkr.ecr.{target.region}.amazonaws.com/"
            f"{PUBLISHED_IMAGE_REPOSITORY}@{manifest.image_digest}"
        ),
        # Two roles, and the difference between them is the point. The execution role is
        # ECS's identity while it starts the task -- it pulls the image and opens the log
        # stream, and the container never sees those credentials. The workload role is what
        # the container's own process runs as, and it is the one an untrusted training
        # command can reach.
        "ExecutionRoleArn": target.execution_role_arn,
        "JobRoleArn": target.workload_role_arn,
        "ResourceRequirements": resource_requirements,
    }
    if shape.secrets:
        container["Secrets"] = [
            {
                "Name": variable,
                "ValueFrom": (
                    f"arn:{partition}:secretsmanager:{target.region}:{account_id}"
                    f":secret:{secret}"
                ),
            }
            for variable, secret in shape.secrets
        ]
    # The default command and environment, both of which this run's own submission
    # overrides a moment later. Declared rather than omitted because ContainerOverrides can
    # only override a key the definition declares in the first place, and because the key
    # names here and in the submit request have to match exactly -- a Command here against
    # a Commands there is a silently dropped override.
    #
    # The default prints the *deployed* definition's name rather than this registration's,
    # so that a registered definition is the deployed one with the image swapped and a test
    # can compare the two verbatim.
    container["Command"] = [
        "python",
        "-c",
        f'print("{deployed_name}: no command override was supplied")',
    ]
    container["Environment"] = [
        {"Name": name, "Value": value} for name, value in shape.default_environment
    ]
    if shape.shared_memory_mib is not None:
        container["LinuxParameters"] = {"SharedMemorySize": shape.shared_memory_mib}
    container["LogConfiguration"] = {
        "LogDriver": "awslogs",
        "Options": {
            "awslogs-group": target.log_group,
            # The deployed definition's stream prefix, which is its own name without this
            # project's resource prefix -- cpu-run, gpu-run. Derived rather than tabulated
            # so that a stream cannot end up filed under a run id, which would make a log
            # stream findable only by the reader who already knew the run.
            "awslogs-stream-prefix": deployed_name.removeprefix(SANDBOX_RESOURCE_PREFIX),
            "awslogs-region": target.region,
        },
    }
    # Explicit rather than left to the default, because the default is the answer that
    # matters and a reader should not have to know it. A privileged container shares the
    # host's kernel capabilities, which on a shared container host means reaching the ECS
    # agent's credentials -- and those are the instance role.
    container["Privileged"] = False

    return {
        "JobDefinitionName": job_definition_name(run_id),
        "Type": "container",
        "PlatformCapabilities": ["EC2"],
        # Job tags reach the underlying ECS task only when asked, and the tags carrying the
        # run id and the team are what Phase 5's cost attribution reads.
        "PropagateTags": True,
        "RetryStrategy": {"Attempts": JOB_DEFINITION_ATTEMPT_FLOOR},
        "Timeout": {"AttemptDurationSeconds": JOB_DEFINITION_TIMEOUT_FLOOR_SECONDS},
        "ContainerProperties": container,
    }
