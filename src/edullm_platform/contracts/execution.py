"""What an accepted submission resolves to, and what came back when it was submitted.

Phase 0 defined the whole execution lineage -- :class:`~.lifecycle.LogicalRun`,
:class:`~.lifecycle.SchedulerAttempt`, :class:`~.lifecycle.LifecycleEvent`,
:class:`~.results.ResultManifest` -- and nothing constructed any of it until Phase 3. Those
models are reused here rather than reinvented. This module adds only what they cannot say.

**Why :class:`BatchJobBinding` exists when ``SchedulerAttempt`` looks like it should cover
it.** ``SchedulerAttempt`` requires ``started_at``, ``ended_at`` and ``terminal_state``, so
it can only be written once a job has finished. The moment that needs recording first is the
opposite one: the instant Batch accepted a submission and returned a job id, when nothing has
started and no outcome exists. Without a record at that instant, a run that is submitted and
then never heard from again has no trace at all -- which is precisely the failure worth being
able to see. The master plan separates "record the Batch job binding" from "record scheduler
attempts separately from the logical run" for this reason.

**Why the target is resolved from configuration rather than carried in the manifest.** A
manifest names a compute profile, which is a shape and a price. Which queue and which job
definition back that shape is an infrastructure fact that changes when infrastructure
changes, and a manifest that named a queue would be a manifest a submitter could point
somewhere else. :class:`ExecutionTargetCatalog` is the mapping, it is deployed alongside the
validator, and the submitter never sees it.

**The catalog is separate from the workload catalog on purpose.** ``config/workload-catalog.yaml``
is a pricing-and-shape document that answers "what would this cost"; it is read by the
compile step, by the approver context and by cost estimation, none of which should acquire an
opinion about queues. Keeping "what actually backs this profile" in its own file means a
profile can be priced without being runnable, which is the state eleven of the twelve are in.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, model_validator

from .base import ContractModel, UtcTimestamp, require_ordered_sequence
from .identity import RunId
from .lifecycle import SchedulerJobId

__all__ = [
    "BATCH_JOB_ARN_PATTERN",
    "BATCH_JOB_DEFINITION_ARN_PATTERN",
    "BATCH_JOB_QUEUE_ARN_PATTERN",
    "IAM_ROLE_ARN_PATTERN",
    "BatchJobBinding",
    "ExecutionTarget",
    "ExecutionTargetBinding",
    "ExecutionTargetCatalog",
    "UnbackedComputeProfileError",
]

#: Batch ARNs, pinned by shape rather than merely by prefix. The account segment is left
#: open because these models are validated in tests and in the Lambda alike and neither
#: should have the account id written into it, but the resource type is fixed: a job-queue
#: ARN in the job-definition field is the kind of mistake that submits successfully and
#: fails somewhere else.
BATCH_JOB_QUEUE_ARN_PATTERN = (
    r"^arn:aws[a-z0-9-]*:batch:[a-z0-9-]+:[0-9]{12}:job-queue/[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
)
BATCH_JOB_DEFINITION_ARN_PATTERN = (
    r"^arn:aws[a-z0-9-]*:batch:[a-z0-9-]+:[0-9]{12}:job-definition/"
    r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}(:[0-9]{1,10})?$"
)
BATCH_JOB_ARN_PATTERN = (
    r"^arn:aws[a-z0-9-]*:batch:[a-z0-9-]+:[0-9]{12}:job/[A-Za-z0-9][A-Za-z0-9:_-]{0,255}$"
)
IAM_ROLE_ARN_PATTERN = r"^arn:aws[a-z0-9-]*:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_-]{1,64}$"
LOG_GROUP_PATTERN = r"^/[A-Za-z0-9_./#-]{1,511}$"

#: Names, for the catalog. Pinned to this project's prefix so a target cannot point at
#: another team's queue in the shared account by a typo, which an ARN pattern alone would
#: allow.
SANDBOX_RESOURCE_NAME_PATTERN = r"^sbsandbox-intern-edullm-[a-z0-9][a-z0-9-]{0,80}$"
BatchResourceName = Annotated[str, Field(pattern=SANDBOX_RESOURCE_NAME_PATTERN)]
IamRoleName = Annotated[str, Field(pattern=SANDBOX_RESOURCE_NAME_PATTERN)]

BatchJobQueueArn = Annotated[str, Field(pattern=BATCH_JOB_QUEUE_ARN_PATTERN)]
BatchJobDefinitionArn = Annotated[str, Field(pattern=BATCH_JOB_DEFINITION_ARN_PATTERN)]
BatchJobArn = Annotated[str, Field(pattern=BATCH_JOB_ARN_PATTERN)]
IamRoleArn = Annotated[str, Field(pattern=IAM_ROLE_ARN_PATTERN)]
LogGroupName = Annotated[str, Field(pattern=LOG_GROUP_PATTERN)]
AwsRegionName = Annotated[str, Field(pattern=r"^[a-z]{2}(-[a-z]+)+-[1-9][0-9]*$")]


class UnbackedComputeProfileError(ValueError):
    """A profile the catalog calls provisioned that no execution target backs.

    Distinct from ``UnprovisionedComputeProfileError``, which is the honest case: a profile
    priced but never claimed to be runnable. This one is a configuration contradiction --
    two files disagreeing about whether capacity exists -- and it is worth its own type
    because the fix is different. One means "ask for a different profile"; the other means
    "somebody flipped a flag without deploying anything".
    """

    reason_code = "unbacked_compute_profile"


class ExecutionTarget(ContractModel):
    """Where a run goes, resolved from deployed configuration rather than from the caller.

    Both roles are recorded even though neither is passed at submit time -- Batch takes them
    when the job definition is registered. They are here so a binding record says which
    identity a container ran as without a reader having to go and read a job definition
    revision that may since have been replaced.
    """

    compute_profile: str = Field(min_length=1)
    region: AwsRegionName
    job_queue_arn: BatchJobQueueArn
    job_definition_arn: BatchJobDefinitionArn
    execution_role_arn: IamRoleArn
    workload_role_arn: IamRoleArn
    log_group: LogGroupName

    @model_validator(mode="after")
    def validate_the_two_roles_are_distinct(self) -> Self:
        # The execution role pulls the image and writes logs; the workload role is what the
        # container's own code runs as. One role doing both would hand the workload the
        # registry credentials, which is the separation ECS task roles exist to provide.
        if self.execution_role_arn == self.workload_role_arn:
            raise ValueError(
                "the execution role and the workload role must be different roles: the "
                "first pulls the image, the second is what the container runs as"
            )
        return self


class ExecutionTargetBinding(ContractModel):
    """What backs one compute profile, written as names rather than as ARNs.

    Names, deliberately. An ARN carries the account id, and committing one would put the
    account into reviewed configuration that every capture tool then has to redact --
    exactly the problem ``Fn::Sub`` on ``${AWS::AccountId}`` solves in the templates. The
    ARNs are assembled at resolution time from the account the resolver is told about, and
    :class:`ExecutionTarget` is the assembled result.
    """

    compute_profile: str = Field(min_length=1)
    region: AwsRegionName
    job_queue: BatchResourceName
    job_definition: BatchResourceName
    execution_role: IamRoleName
    workload_role: IamRoleName
    log_group: LogGroupName


class ExecutionTargetCatalog(ContractModel):
    """Which compute profiles are actually backed, and by what."""

    schema_version: Literal[1]
    targets: Annotated[
        tuple[ExecutionTargetBinding, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(default=(), strict=False)

    @model_validator(mode="after")
    def validate_one_target_per_profile(self) -> Self:
        names = [target.compute_profile for target in self.targets]
        if len(set(names)) != len(names):
            raise ValueError("a compute profile must not be backed by two execution targets")
        return self

    def binding_for(self, compute_profile: str) -> ExecutionTargetBinding | None:
        for target in self.targets:
            if target.compute_profile == compute_profile:
                return target
        return None

    @property
    def backed_profiles(self) -> frozenset[str]:
        return frozenset(target.compute_profile for target in self.targets)


class BatchJobBinding(ContractModel):
    """The instant Batch accepted a submission, and what it called the job.

    Written write-once to the lineage store immediately after the submit succeeds, before
    anything has started. That ordering is the point: a job that is submitted and then
    vanishes leaves this record behind, and a run whose binding is missing is a run the
    state machine never got as far as submitting.

    ``batch_job_name`` is the run id. Batch does not enforce unique job names, so this is
    not deduplication -- ``ExecutionAlreadyExists`` and the conditional write already refuse
    a duplicate before Batch is reached. It is join-ability: the run id is the S3 key, the
    execution name and the job name, and any two of the three disagreeing is visible.
    """

    schema_version: Literal[1]
    run_id: RunId
    batch_job_id: SchedulerJobId
    batch_job_arn: BatchJobArn
    batch_job_name: RunId
    job_queue_arn: BatchJobQueueArn
    job_definition_arn: BatchJobDefinitionArn
    compute_profile: str = Field(min_length=1)
    log_group: LogGroupName
    #: The bound on one attempt, as it was sent to Batch. Recorded rather than recomputed
    #: because the manifest's runtime bound and what Batch was actually told are two
    #: different facts, and a later reading that could not tell them apart could not tell a
    #: timeout that fired correctly from one that fired because the wrong number was sent.
    attempt_duration_seconds: int = Field(gt=0)
    attempts: int = Field(ge=1)
    #: Present only for a fan-out. ``None`` means one container, which is not the same as a
    #: fan-out of size one -- Batch rejects an array job of size one, so the distinction is
    #: real rather than cosmetic.
    array_size: int | None = Field(default=None, ge=2)
    submitted_at: UtcTimestamp

    @model_validator(mode="after")
    def validate_the_job_name_is_the_run_id(self) -> Self:
        if self.batch_job_name != self.run_id:
            raise ValueError(
                "the Batch job name must be the run id, so a job and its lineage records "
                "join without a lookup table"
            )
        return self
