"""What a GPU run left behind, in the shapes Phase 4's checks are written against.

Phase 4's claim is narrower than it sounds and worth stating exactly: **one single-node GPU
training run went through this platform end to end, and every part of it can be verified
from what it left behind rather than from anybody's memory of watching it.** These models
are the "what it left behind" half. The criteria read them; the capture tool writes them.

**The division between what expires and what does not is load-bearing here.** A GPU job ran
at a particular instant and wrote a checkpoint whose digest is in the bucket; nothing about
the passage of time makes that less true, so the run records extend
:class:`~edullm_platform.evidence.RecordedEventModel`. A compute environment's instance-type
list, an availability-zone offering, the way a job definition reaches a secret -- those are
statements about how the account is configured today, are one console click from being
false, and extend :class:`~edullm_platform.evidence.FreshEvidenceModel`.

**Every model here can only hold what a reader may see.** ``SecretFreeStr`` refuses a field
whose value looks like a credential, which is a shape test and therefore imperfect -- but
the fields that could plausibly carry one, the container's environment and the container's
log, are the ones it is applied to.
"""

from __future__ import annotations

import fnmatch
from typing import Final, Literal, Self

from pydantic import Field, model_validator

from edullm_platform.contracts.base import ContractModel, Sha256Digest, UtcTimestamp
from edullm_platform.evidence import (
    DigestBearingStr,
    EvidenceEnvironment,
    FreshEvidenceModel,
    RecordedEventModel,
    SecretFreeStr,
)

__all__ = [
    "ACCESS_DENIED",
    "GPU_RESOURCE_TYPE",
    "WANDB_SECRET_VARIABLE",
    "CheckpointObservation",
    "ContainerVariable",
    "GpuComputeEnvironmentEvidence",
    "GpuJobEvidence",
    "InstanceTypeOffering",
    "InstanceTypeOfferingEvidence",
    "IsolationEvidence",
    "OutputObject",
    "OutputPrefixEvidence",
    "ResumeEvidence",
    "SecretDeliveryEvidence",
    "TrainingSummaryEvidence",
    "WorkloadRoleScopeEvidence",
]

#: What Batch calls a GPU in ``resourceRequirements``. A job definition without one gets no
#: device injected, runs on the CPU, bills at the GPU rate and looks entirely healthy --
#: which is the sharpest failure in this phase and the reason the type is named here rather
#: than matched as a string wherever it is read.
GPU_RESOURCE_TYPE: Final = "GPU"

#: The variable the W&B key arrives under. Named so the check that it is delivered by
#: reference rather than by value has one spelling to compare against.
WANDB_SECRET_VARIABLE: Final = "WANDB_API_KEY"

#: The shape ``gpu-1xa10g`` was promoted as. The catalog prices that profile at $1.006/hr on
#: the basis that it is one A10G with 24GB, so a job that landed on anything else would run,
#: would train, and would make the cost estimate in the decision record wrong -- in a store
#: nothing rewrites.
GPU_INSTANCE_TYPE_FOR_THE_PROMOTED_PROFILE: Final = "g5.xlarge"


class ContainerVariable(ContractModel):
    """One environment entry a Batch job's container was given.

    The value is recorded, and that is a deliberate risk taken on purpose: the whole
    prefix-agreement check is "the container was told X", and a record holding only the
    names could not make it. ``SecretFreeStr`` is what makes it safe enough -- a value
    shaped like a credential refuses the capture rather than being written.

    A secret delivered by ``valueFrom`` never appears here at all, because Batch resolves it
    at container start and the job definition carries only the ARN. That is the property
    :class:`SecretDeliveryEvidence` exists to record.
    """

    name: SecretFreeStr = Field(min_length=1)
    value: DigestBearingStr


class GpuJobEvidence(RecordedEventModel):
    """One Batch job that ran on the GPU queue, as the scheduler describes it.

    ``gpu_count`` is read from the job's own ``resourceRequirements`` rather than inferred
    from the queue it ran on. A job on the GPU queue with no GPU requirement is exactly the
    silent failure this phase is about: it lands on a `g5.xlarge`, bills at the GPU rate,
    and the container finds no device.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    region: SecretFreeStr = Field(min_length=1)
    run_id: SecretFreeStr = Field(min_length=1)
    batch_job_id: SecretFreeStr = Field(min_length=1)
    job_queue_name: SecretFreeStr = Field(min_length=1)
    job_definition_name: SecretFreeStr = Field(min_length=1)
    status: Literal["SUCCEEDED", "FAILED"]
    status_reason: SecretFreeStr | None
    image_digest: Sha256Digest
    vcpus: int = Field(gt=0)
    memory_mib: int = Field(gt=0)
    gpu_count: int = Field(ge=0)
    log_stream_name: SecretFreeStr | None
    started_at: UtcTimestamp | None
    stopped_at: UtcTimestamp | None
    container_environment: tuple[ContainerVariable, ...] = Field(strict=False)

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        inverted = (
            self.started_at is not None
            and self.stopped_at is not None
            and self.stopped_at < self.started_at
        )
        if inverted:
            raise ValueError("a job cannot have stopped before it started")
        return self

    @property
    def ran_on_a_gpu(self) -> bool:
        return self.gpu_count > 0

    def told(self, name: str) -> str | None:
        """What the container was given for one variable, or None if it was given none."""
        for variable in self.container_environment:
            if variable.name == name:
                return variable.value
        return None


class TrainingSummaryEvidence(RecordedEventModel):
    """What the container itself reported, read back out of its log stream.

    THE ONLY PLACE SEVERAL OF THESE FACTS EXIST. Batch knows a GPU was requested; it does
    not know whether torch found one. The device name, the CUDA build, the parameter count
    and the loss are things only the process could say, and it said them to stdout -- so the
    log is not a diagnostic here, it is the evidence.

    ``losses`` is recorded and nothing asserts it fell. Twenty steps on synthetic tokens is
    not a claim about learning, and a check that pretended otherwise would be the kind of
    evidence this repository spends its time removing. What it does establish is that the
    optimizer ran: a loss that never moved at all is a backward pass that did nothing.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    run_id: SecretFreeStr = Field(min_length=1)
    log_group: SecretFreeStr = Field(min_length=1)
    log_stream: SecretFreeStr = Field(min_length=1)
    gpu_name: SecretFreeStr = Field(min_length=1)
    torch_version: SecretFreeStr = Field(min_length=1)
    cuda_version: SecretFreeStr | None
    parameters: int = Field(gt=0)
    steps: int = Field(gt=0)
    first_loss: float
    last_loss: float
    seconds: float = Field(gt=0)
    peak_memory_gib: float = Field(gt=0)
    checkpoint_uri: SecretFreeStr = Field(min_length=1)
    wandb_project: SecretFreeStr = Field(min_length=1)
    wandb_run_url: SecretFreeStr = Field(min_length=1)
    metric_keys: tuple[SecretFreeStr, ...] = Field(strict=False)

    @property
    def trained_on_a_gpu(self) -> bool:
        """Whether the process itself found a CUDA device, as against being offered one.

        Both halves. A CPU torch build reports no CUDA version at all; a CUDA build that
        allocated nothing never put a tensor on the device. Either alone leaves the other
        open, and the pair is the whole of the check that this run was not a CPU run at GPU
        prices.
        """
        return bool(self.cuda_version) and self.peak_memory_gib > 0


class GpuCapabilityEvidence(RecordedEventModel):
    """What the container found when it looked for a GPU, below the framework.

    THE SECOND, INDEPENDENT ANSWER TO THE SHARPEST CHECK IN THE PHASE. A training run
    reports that torch saw a CUDA device, which is the answer that matters and is also the
    answer a misconfigured job cannot give at all. This is the layer underneath: the device
    nodes Batch injected into the container, and what the driver's own tool says is on the
    other side of them.

    Both are worth having because they fail differently. No device nodes means the job
    definition asked for no GPU and the instance is being billed at the GPU rate for
    nothing. Device nodes with a torch that cannot use them means a CPU build, or a driver
    the framework does not match -- a container that looks correctly provisioned and trains
    on the processor anyway.

    ``nvidia_visible_devices`` is recorded because its value is surprising and worth not
    rediscovering: the ECS GPU agent sets it to ``void`` and injects the devices directly,
    so a check written against the conventional ``all`` would report a healthy container as
    broken.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    run_id: SecretFreeStr = Field(min_length=1)
    log_group: SecretFreeStr = Field(min_length=1)
    log_stream: SecretFreeStr = Field(min_length=1)
    device_nodes: tuple[SecretFreeStr, ...] = Field(strict=False)
    nvidia_smi: SecretFreeStr = Field(min_length=1)
    nvidia_visible_devices: SecretFreeStr
    output_prefix: SecretFreeStr = Field(min_length=1)
    team: SecretFreeStr = Field(min_length=1)
    wandb_key_injected: bool

    @property
    def the_driver_can_see_a_gpu(self) -> bool:
        return bool(self.device_nodes) and "MiB" in self.nvidia_smi


#: What S3 says when a role may not look, as against when it may look and found nothing.
#: The distinction is the whole of the isolation check: ``NoSuchKey`` is what a role
#: permitting everything returns from an empty prefix, and would establish nothing.
ACCESS_DENIED: Final = "AccessDenied"


class IsolationEvidence(RecordedEventModel):
    """What the workload role was refused, recorded from inside the only thing that can be.

    THE ONLY PRINCIPAL THAT CAN BE TOLD NO IS A CONTAINER. The workload role's trust policy
    names the Batch and ECS task services, so no human can assume it; before this, the
    cross-team criterion rested on reading the deployed policy document, which says what the
    grant is rather than what happened when something reached for it.

    ``s3:ListBucket`` is probed separately and is not redundant. It is a bucket-level action
    that cannot be scoped by an object ARN, so a role whose object grants look perfectly
    narrow can still enumerate every team's output if the prefix condition is missing.

    The lineage probe is the sharpest of the four. Every other grant on this role is
    arguable; the one that is not is that a workload cannot write to the store recording what
    it did, because every other guarantee here is downstream of that record being something
    only the platform writes.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    run_id: SecretFreeStr = Field(min_length=1)
    read_another_teams_prefix: SecretFreeStr = Field(min_length=1)
    write_to_another_teams_prefix: SecretFreeStr = Field(min_length=1)
    list_the_whole_outputs_bucket: SecretFreeStr = Field(min_length=1)
    write_to_the_lineage_bucket: SecretFreeStr = Field(min_length=1)

    @property
    def probes(self) -> dict[str, str]:
        return {
            "read_another_teams_prefix": self.read_another_teams_prefix,
            "write_to_another_teams_prefix": self.write_to_another_teams_prefix,
            "list_the_whole_outputs_bucket": self.list_the_whole_outputs_bucket,
            "write_to_the_lineage_bucket": self.write_to_the_lineage_bucket,
        }

    @property
    def everything_was_refused(self) -> bool:
        """Refused, specifically -- not merely "did not succeed".

        ``AccessDenied`` and nothing else. A ``NoSuchKey`` would mean the role was permitted
        to look and there was nothing there, which is what a role granting everything returns
        from an empty prefix and establishes no isolation whatsoever.
        """
        return all(code == ACCESS_DENIED for code in self.probes.values())


class ResumeEvidence(RecordedEventModel):
    """A checkpoint one run wrote, loaded back into another run's model.

    ``inspect_checkpoint`` establishes that a marker certifies its payload and that the store
    agrees with the marker. It says nothing about whether torch will accept the bytes, which
    is the thing a researcher resuming a run actually needs, and the two are different
    claims.

    ``first_loss`` is the evidence and not a decoration. A freshly initialised olmo2_190M on
    random tokens starts near 11.0; this run started at 9.71. That number can only come from
    trained weights being in the model, which is a harder thing to fake than a digest match.

    **What a resume here does not restore.** The checkpoint carries the model state dict and
    the step, and no optimizer state. So this restores a *model*, not a training run: a
    resumed AdamW starts with no moment estimates, and the loss moves accordingly. Recorded
    because "resumable checkpoint" reads as more than it is, and the gap is the difference
    between reproducing a result and continuing a run.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    run_id: SecretFreeStr = Field(min_length=1)
    resumed_from_run_id: SecretFreeStr = Field(min_length=1)
    uri: SecretFreeStr = Field(min_length=1)
    checksum: Sha256Digest
    size_bytes: int = Field(gt=0)
    step: int = Field(ge=0)
    tensors: int = Field(gt=0)
    first_loss: float
    #: What a run with no checkpoint to resume from began at, for comparison. Carried in the
    #: record rather than left to a reader's memory, because the whole claim is the gap.
    cold_start_first_loss: float
    restores_optimizer_state: bool = False

    @property
    def loaded_trained_weights(self) -> bool:
        return self.first_loss < self.cold_start_first_loss


class CheckpointObservation(RecordedEventModel):
    """The platform's own reader, run against the objects a live run actually wrote.

    NOT A RESTATEMENT OF WHAT THE CONTAINER CLAIMED. The container printed a digest; this
    is ``inspect_checkpoint`` asking S3 what it holds and comparing the two. The difference
    matters because everything else in this phase's evidence is the run describing itself,
    and a checkpoint that cannot be resumed from is the one failure a run cannot detect
    about its own output.

    ``state`` carries the reader's vocabulary rather than a boolean, so a checkpoint that is
    present-but-uncertified is distinguishable in the record from one that is absent.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    run_id: SecretFreeStr = Field(min_length=1)
    prefix: SecretFreeStr = Field(min_length=1)
    state: Literal["absent", "uncommitted", "orphaned", "corrupt", "committed"]
    detail: SecretFreeStr = Field(min_length=1)
    step: int | None = Field(ge=0)
    size_bytes: int | None = Field(gt=0)
    checksum: Sha256Digest | None
    success_marker_uri: SecretFreeStr | None
    container_claimed_checksum: Sha256Digest | None

    @property
    def is_resumable(self) -> bool:
        return self.state == "committed"

    @property
    def store_agrees_with_the_container(self) -> bool:
        """Whether what the run said it wrote is what the store attests it holds.

        Three-way rather than two: the marker's claim, the store's own digest, and the
        digest the container printed to its log before either object existed. The first two
        are compared by ``inspect_checkpoint``; this is the third, and it closes the loop
        between the process and the bucket.
        """
        if self.checksum is None or self.container_claimed_checksum is None:
            return False
        return self.checksum == self.container_claimed_checksum


class OutputObject(ContractModel):
    #: Typed as a digest rather than as scanned text, because ``scan_for_secrets`` reads
    #: sixty-four hexadecimal characters as a credential -- correctly, for free text, and
    #: not for the one field whose entire content is a digest. The pattern is the check.
    key: SecretFreeStr = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    checksum_sha256: Sha256Digest | None


class OutputPrefixEvidence(RecordedEventModel):
    """Every object in the outputs bucket, and whether each one is where it is permitted.

    THE WHOLE BUCKET, NOT THE RUN'S OWN PREFIX. A capture scoped to
    ``teams/{team}/runs/{run_id}/`` can only ever report that what is there is there, which
    is not the check. "S3 receives outputs only under the authorized run prefix" is a claim
    about what is *absent* elsewhere, and the only way to make it is to look elsewhere.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    bucket: SecretFreeStr = Field(min_length=1)
    authorized_prefixes: tuple[SecretFreeStr, ...] = Field(strict=False)
    objects: tuple[OutputObject, ...] = Field(strict=False)

    @property
    def stray_keys(self) -> tuple[str, ...]:
        """Objects sitting outside every prefix a run was authorized to write under."""
        return tuple(
            entry.key
            for entry in self.objects
            if not any(entry.key.startswith(prefix) for prefix in self.authorized_prefixes)
        )


class SecretDeliveryEvidence(FreshEvidenceModel):
    """How the W&B key reaches a container, and whether it turned up anywhere it must not.

    ``secret_arn_suffix`` rather than the ARN. Secrets Manager appends an unpredictable
    six-character suffix, and the ARN carries the account id; recording the suffix alone
    establishes that the reference is to a specific version of a specific secret without
    putting either in a committed file.

    ``value_appears_in_environment`` is the check that matters and reads backwards on first
    encounter. False is the healthy answer: the job definition names the secret under
    ``secrets``/``valueFrom``, Batch resolves it at container start, and the value therefore
    exists only in the running container's memory -- never in the definition, never in a
    ``DescribeJobs`` answer, never in the log.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    job_definition_name: SecretFreeStr = Field(min_length=1)
    variable_name: SecretFreeStr = Field(min_length=1)
    delivered_by_reference: bool
    secret_arn_suffix: SecretFreeStr = Field(min_length=1)
    value_appears_in_environment: bool
    plain_environment_names: tuple[SecretFreeStr, ...] = Field(strict=False)
    log_lines_scanned: int = Field(ge=0)
    log_holds_a_credential_shape: bool
    #: Digests the scan was told to ignore, and which digests they were.
    #:
    #: THE SCAN CANNOT TELL A BARE SHA-256 FROM A CREDENTIAL AND SHOULD NOT TRY. The first
    #: GPU training run printed ``"checkpoint_sha256": "dc5bc83a..."`` with no prefix,
    #: because its program predated ``commit_checkpoint``; sixty-four hexadecimal characters
    #: is also the shape of a long credential, so the scan flagged the log of a run that
    #: leaked nothing.
    #:
    #: Listing them is what keeps that from being a hole. Each one is a digest the capture
    #: verified independently -- the store attests the same value for the object it belongs
    #: to -- so the exemption is "this exact string is a digest we can prove", not "ignore
    #: things that look like digests". A reader can check every entry.
    #:
    #: Expect this to empty out. Everything ``commit_checkpoint`` writes and the current
    #: training program prints carries the ``sha256:`` prefix, which the ordinary digest
    #: mask already recognises.
    exempted_content_digests: tuple[Sha256Digest, ...] = Field(default=(), strict=False)

    @property
    def stayed_out_of_every_record(self) -> bool:
        return (
            self.delivered_by_reference
            and not self.value_appears_in_environment
            and not self.log_holds_a_credential_shape
        )


class GpuComputeEnvironmentEvidence(FreshEvidenceModel):
    """The GPU compute environment as it stands, including what it is still holding.

    ``live_instance_count`` beside ``desired_vcpus`` because the first is what bills and the
    second is only what was asked for. Batch drops the desired count to zero as soon as the
    queue empties and leaves the instance running for several minutes afterwards; a record
    of the first alone reports an idle environment while EC2 is still charging for it.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    region: SecretFreeStr = Field(min_length=1)
    compute_environment_name: SecretFreeStr = Field(min_length=1)
    state: SecretFreeStr = Field(min_length=1)
    status: SecretFreeStr = Field(min_length=1)
    image_type: SecretFreeStr = Field(min_length=1)
    instance_types: tuple[SecretFreeStr, ...] = Field(strict=False)
    subnet_count: int = Field(gt=0)
    minimum_vcpus: int = Field(ge=0)
    maximum_vcpus: int = Field(gt=0)
    desired_vcpus: int = Field(ge=0)
    live_instance_count: int = Field(ge=0)

    @property
    def idle_and_holding_nothing(self) -> bool:
        return self.desired_vcpus == 0 and self.live_instance_count == 0

    @property
    def can_fall_back_to_another_shape(self) -> bool:
        """Whether the scheduler has an alternative when the preferred type is short.

        One entry means one shape. With ``us-east-2`` closed to compute there is no region
        to fail over to, so the breadth of this list is the only lever on availability the
        account has left.
        """
        return len(self.instance_types) > 1


class InstanceTypeOffering(ContractModel):
    availability_zone: SecretFreeStr = Field(min_length=1)
    offered: bool


class InstanceTypeOfferingEvidence(FreshEvidenceModel):
    """Which zones offer a shape, from ``describe-instance-type-offerings`` and nothing else.

    **A dry-run is not evidence for this and the distinction cost a measurement to find.**
    ``run-instances --dry-run`` answers "is this principal permitted to make this call", and
    returned ``DryRunOperation`` for two instance types in an availability zone that offers
    neither. A compute environment built on that answer puts jobs in ``RUNNABLE`` for ever
    with no error anywhere.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    region: SecretFreeStr = Field(min_length=1)
    instance_type: SecretFreeStr = Field(min_length=1)
    offerings: tuple[InstanceTypeOffering, ...] = Field(strict=False)

    @property
    def offering_zones(self) -> tuple[str, ...]:
        return tuple(
            offering.availability_zone for offering in self.offerings if offering.offered
        )


class WorkloadRoleScopeEvidence(FreshEvidenceModel):
    """Which S3 prefixes the GPU workload role may actually write to and read from.

    Read off the deployed policy rather than simulated. ``iam:SimulatePrincipalPolicy`` has
    twice given confidently wrong answers in this account -- reporting ten EC2 actions as
    denied in both regions when seven are authorized in one -- so what the role permits is
    established from the document AWS says it is holding.

    **This is a policy statement and not a live denial, and the difference is worth naming.**
    A live probe would need a principal that can assume this role, and the trust policy
    names the Batch and ECS task services rather than any human. So the cross-team check
    rests on what the grant says, which is honest and is one step weaker than a refusal
    somebody actually received.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    role_name: SecretFreeStr = Field(min_length=1)
    writable_prefixes: tuple[SecretFreeStr, ...] = Field(strict=False)
    readable_prefixes: tuple[SecretFreeStr, ...] = Field(strict=False)
    grants_delete: bool
    reaches_the_lineage_bucket: bool
    #: Every S3 grant on a bucket that is not the outputs bucket, as ``bucket/key``. Recorded
    #: rather than filtered away: the two prefix tuples above are a claim about the outputs
    #: bucket and folding a dataset grant into them turned "cannot reach" into "reaches
    #: everything" without any test noticing. Defaulted to an empty tuple so the capture taken
    #: on 2026-07-30 still loads and still says something true about the role as deployed then.
    grants_outside_the_outputs_bucket: tuple[SecretFreeStr, ...] = Field(
        default=(), strict=False
    )

    def may_reach(self, prefix: str) -> bool:
        """Whether any grant on this role covers a given key in the OUTPUTS bucket.

        The bucket is part of the question and used not to be. Wildcards are expanded the way
        IAM reads them -- ``*`` matches any run of characters -- because ``teams/*/runs/*``
        and ``teams/other/runs/x`` do not look alike and IAM says one covers the other.

        Grants on other buckets are in ``grants_outside_the_outputs_bucket`` and are
        deliberately not consulted here. A dataset read is not a wider reach into this
        platform's outputs, and a reader that could not say so reported the first as the
        second.
        """
        return any(
            fnmatch.fnmatchcase(prefix, pattern)
            for pattern in (*self.writable_prefixes, *self.readable_prefixes)
        )
