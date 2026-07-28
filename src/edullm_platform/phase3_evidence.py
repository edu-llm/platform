"""What Phase 3 captures from the account, and what a captured record is allowed to say.

Phase 3's premises are facts about an account nobody here controls: how many VPCs exist
against the quota, which EC2 calls each region would authorize, whether Batch has ever been
used, and which availability zones offer the instance type the compute environment is going
to ask for. Every one of those can change without anybody telling us, and the first revision
of Phase 3's plan was written on a premise that was simply wrong. So the premises are
captured, committed and expired like any other evidence, rather than being asserted in a
document and believed thereafter.

**The authorization matrix records a verdict, not a boolean.** "Denied" and "authorized but
there is no room" are different problems with different owners -- one is a support request
and the other is not fixable by us -- and the whole shape of this phase turned on telling
them apart. :class:`RegionAuthorization` therefore carries the four-valued verdict from
``edullm_platform.ec2_authorization`` and the service's own error code beside it, so a
reader can check the classification rather than take it.

**The controls travel with the matrix.** :class:`AuthorizationControl` records, for each
captured control, the verdict the classifier assigned and the verdict established some other
way. A matrix whose controls disagree is not a matrix with one bad row; it is a matrix whose
classifier is wrong, and the record says so in a field rather than leaving a reader to
notice.

**A subnet is recorded with whether the instance type is actually offered in its zone.**
Batch does not fail a job it cannot place -- it waits. A subnet list including a zone that
does not offer ``c7i.8xlarge`` produces a job stuck in ``RUNNABLE`` and no error anywhere,
which is the least debuggable failure this phase can have. Recording the offering beside the
subnet makes the exclusion checkable instead of remembered.

**Borrowed networking is recorded as borrowed.** :class:`NetworkPlacement` carries whether
the VPC is one this project owns. While it is not, that is the phase's largest known
limitation, and a record that listed only the ids would make a borrowed VPC indistinguishable
from ours a month later.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BeforeValidator, Field

from edullm_platform.contracts.base import (
    ContractModel,
    UtcTimestamp,
    require_ordered_sequence,
)
from edullm_platform.contracts.execution import LogGroupName
from edullm_platform.contracts.identity import RunId
from edullm_platform.contracts.lifecycle import SchedulerJobId
from edullm_platform.evidence import (
    EvidenceEnvironment,
    FreshEvidenceModel,
    SecretFreeStr,
)
from edullm_platform.role_drift import PHASE3_ROLE_TEMPLATES

__all__ = [
    "EVERY_BATCH_JOB_STATUS",
    "PHASE3_ROLE_TEMPLATES",
    "AccountMeasurements",
    "AuthorizationControl",
    "BatchInventory",
    "BatchJobEvidence",
    "ComputeEnvironmentEvidence",
    "LineageObjectAttestation",
    "LogStreamEvidence",
    "NetworkPlacement",
    "RefusedRunEvidence",
    "RegionAuthorization",
    "RunLineageAttestation",
    "ServiceLinkedRoleRecord",
    "SubnetOffering",
    "VpcQuotaRecord",
    "group_opaque_identifier",
    "ungroup_opaque_identifier",
]

#: How many characters of an opaque identifier go between hyphens. See
#: :func:`group_opaque_identifier` for why they are there at all.
OPAQUE_IDENTIFIER_GROUP: Final = 8


def group_opaque_identifier(value: str) -> str:
    """Hyphenate an opaque identifier so a shape-based secret scan cannot mistake it.

    A service-quotas request id is forty characters of ``[A-Za-z0-9]``, which is exactly
    what ``AWS_SECRET_ACCESS_KEY_PATTERN`` matches. It is not a credential -- it appears in
    the AWS console URL for the request -- but ``scan_for_secrets`` works on shape and
    cannot know that, so a record carrying one raw is refused whole.

    The two obvious responses are both wrong. Masking it, the way an S3 extended request id
    is masked, throws away the one field that lets a reader go and look at the request.
    Widening the scanner to allow forty-character runs in this field would weaken the check
    everywhere, to admit one identifier.

    So it is reformatted rather than hidden: hyphens every
    :data:`OPAQUE_IDENTIFIER_GROUP` characters, which breaks the run the scanner matches
    while keeping every character. :func:`ungroup_opaque_identifier` reverses it exactly,
    and a test holds the pair to round-tripping, so this is a presentation change and not a
    lossy one.
    """
    if not value:
        return value
    return "-".join(
        value[index : index + OPAQUE_IDENTIFIER_GROUP]
        for index in range(0, len(value), OPAQUE_IDENTIFIER_GROUP)
    )


def ungroup_opaque_identifier(value: str) -> str:
    """Recover the identifier AWS issued from the form :func:`group_opaque_identifier` wrote."""
    return value.replace("-", "")

# The roles Phase 3 creates are registered in ``edullm_platform.role_drift`` beside the
# comparison machinery that acts on them, and re-exported here because this is the module a
# Phase 3 capture reads. It was declared in both places, identically, which is one edit away
# from two registries disagreeing about which roles are compared to anything at all.

#: The four verdicts ``edullm_platform.ec2_authorization`` assigns. Repeated here as a
#: Literal rather than imported as an enum so that a captured record is checked against the
#: exact strings that were written, and a renamed enum member fails to load instead of
#: quietly reading as something else.
AuthorizationVerdict = Literal["authorized", "denied", "quota_blocked", "inconclusive"]

AwsRegionName = Annotated[str, Field(pattern=r"^[a-z]{2}(-[a-z]+)+-[1-9][0-9]*$")]
VpcId = Annotated[str, Field(pattern=r"^vpc-[0-9a-f]{8,17}$")]
SubnetId = Annotated[str, Field(pattern=r"^subnet-[0-9a-f]{8,17}$")]
AvailabilityZone = Annotated[str, Field(pattern=r"^[a-z]{2}(-[a-z]+)+-[1-9][0-9]*[a-z]$")]
Iso8601Date = Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]
OrderedStrings = Annotated[tuple[str, ...], BeforeValidator(require_ordered_sequence)]


class ActionVerdict(ContractModel):
    """One EC2 action, in one region, as the service answered a dry run of it."""

    action: SecretFreeStr = Field(pattern=r"^ec2:[A-Za-z]+$")
    verdict: AuthorizationVerdict
    #: The service's own error code. ``None`` only when the CLI returned no parseable
    #: error, which is itself an inconclusive answer.
    error_code: SecretFreeStr | None


class RegionAuthorization(ContractModel):
    """What one region would allow, and the resources the probes had to name to find out.

    ``vpc_id`` and ``subnet_id`` are recorded because a probe pointed at a resource that
    does not exist is answered by the resource rather than by the caller, and a reader
    checking this matrix has to be able to see that the probes had something real to aim at.
    """

    region: AwsRegionName
    vpc_id: VpcId
    subnet_id: SubnetId
    instance_type: SecretFreeStr = Field(min_length=1)
    verdicts: Annotated[tuple[ActionVerdict, ...], BeforeValidator(require_ordered_sequence)] = (
        Field(min_length=1, strict=False)
    )

    def verdict_for(self, action: str) -> AuthorizationVerdict | None:
        for entry in self.verdicts:
            if entry.action == action:
                return entry.verdict
        return None


class AuthorizationControl(ContractModel):
    """One captured answer whose verdict was established somewhere other than the classifier.

    ``agrees`` is stored rather than derived on read so that a record which disagreed at
    capture time still says so after somebody changes the classifier to agree with itself.
    """

    action: SecretFreeStr = Field(pattern=r"^ec2:[A-Za-z]+$")
    region: AwsRegionName
    expected: AuthorizationVerdict
    classified: AuthorizationVerdict
    agrees: bool
    established_by: SecretFreeStr = Field(min_length=1)


class VpcQuotaRecord(ContractModel):
    """VPCs in use against the quota, and whether an increase has been asked for.

    The quota is the one thing standing between this phase and networking it owns, so the
    request id is part of the evidence rather than a note somebody kept.
    """

    region: AwsRegionName
    quota_code: SecretFreeStr = Field(pattern=r"^L-[0-9A-F]{8}$")
    quota_value: int = Field(ge=0)
    in_use: int = Field(ge=0)
    adjustable: bool
    #: The service-quotas request id, hyphenated by :func:`group_opaque_identifier`
    #: because AWS issues it as forty characters that a shape-based scan reads as a secret
    #: key. Absent means nobody has filed one.
    increase_request_id: SecretFreeStr | None = None
    increase_request_status: SecretFreeStr | None = None

    @property
    def increase_requested(self) -> bool:
        return self.increase_request_id is not None

    @property
    def exhausted(self) -> bool:
        return self.in_use >= self.quota_value


class SubnetOffering(ContractModel):
    """One subnet, its zone, and whether the instance type is offered there.

    ``instance_type_offered`` is the field that prevents the quiet failure. A subnet in a
    zone that does not offer the shape leaves a job in ``RUNNABLE`` with no error, so the
    exclusion has to be checkable rather than remembered.
    """

    subnet_id: SubnetId
    availability_zone: AvailabilityZone
    instance_type_offered: bool
    map_public_ip_on_launch: bool
    available_ip_address_count: int = Field(ge=0)


class NetworkPlacement(ContractModel):
    """The VPC and subnets the compute environment will use, and whose they are."""

    region: AwsRegionName
    vpc_id: VpcId
    #: False while the VPC belongs to another project. The phase's largest known
    #: limitation for as long as this is false, and it belongs in the proof bundle.
    vpc_is_ours: bool
    #: Why we may use it, when it is not ours. Empty when it is.
    borrowing_terms: SecretFreeStr = ""
    subnets: Annotated[tuple[SubnetOffering, ...], BeforeValidator(require_ordered_sequence)] = (
        Field(min_length=1, strict=False)
    )

    @property
    def usable_subnet_ids(self) -> tuple[str, ...]:
        return tuple(
            subnet.subnet_id for subnet in self.subnets if subnet.instance_type_offered
        )


class ServiceLinkedRoleRecord(ContractModel):
    """Whether one service-linked role exists. Batch's does not, and that is a build step."""

    role_name: SecretFreeStr = Field(pattern=r"^AWSServiceRoleFor[A-Za-z0-9]+$")
    exists: bool


class BatchInventory(ContractModel):
    """What Batch already holds in the region. Greenfield is a premise worth recording.

    Counts rather than names: another team creating a compute environment would change
    these, and the fact worth capturing is that nothing here was inherited.
    """

    region: AwsRegionName
    compute_environment_count: int = Field(ge=0)
    job_queue_count: int = Field(ge=0)
    job_definition_count: int = Field(ge=0)
    compute_environments_per_queue_quota: int = Field(ge=1)
    standard_on_demand_vcpu_quota: int = Field(ge=0)

    @property
    def greenfield(self) -> bool:
        return (
            self.compute_environment_count == 0
            and self.job_queue_count == 0
            and self.job_definition_count == 0
        )


class AccountMeasurements(FreshEvidenceModel):
    """Every premise Phase 3 rests on, captured at one moment and expiring like any other.

    This is deliberately one record rather than several. The premises are only useful
    together -- "we may create a VPC" and "there is no room for one" are each half an
    answer -- and a set of separately expiring records would let a reader assemble a
    picture from parts observed weeks apart.
    """

    schema_version: Literal[1]
    environment: Literal["sandbox"]
    #: The method, in the record, because the method is the part that was wrong last time.
    method: SecretFreeStr = Field(min_length=1)
    controls: Annotated[
        tuple[AuthorizationControl, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)
    regions: Annotated[
        tuple[RegionAuthorization, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)
    vpc_quota: VpcQuotaRecord
    placement: NetworkPlacement
    service_linked_roles: Annotated[
        tuple[ServiceLinkedRoleRecord, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)
    batch: BatchInventory

    @property
    def controls_agree(self) -> bool:
        return all(control.agrees for control in self.controls)

    def region(self, name: str) -> RegionAuthorization | None:
        for entry in self.regions:
            if entry.region == name:
                return entry
        return None

    def service_linked_role_exists(self, role_name: str) -> bool | None:
        for record in self.service_linked_roles:
            if record.role_name == role_name:
                return record.exists
        return None


# --------------------------------------------------------------------------------------
# What one live run left behind
# --------------------------------------------------------------------------------------

#: A CloudWatch Logs stream name. Colons and asterisks are the two characters the service
#: refuses, so this is the service's own rule rather than a guess at one.
LogStreamName = Annotated[str, Field(pattern=r"^[^:*]{1,512}$")]

#: Every status Batch reports. Written as a Literal rather than imported from an enum so a
#: captured record is checked against the exact strings the service wrote, and a renamed
#: member fails to load instead of quietly reading as something else.
BatchJobStatus = Literal[
    "SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING", "SUCCEEDED", "FAILED"
]

#: The six kinds of object one run puts in the lineage store. ``events`` is plural because
#: a run writes one object per lifecycle event and the others write exactly one each.
LineageRecordKind = Literal["intent", "decision", "binding", "events", "attempt", "result"]

#: What Batch says when it stops a job for outrunning ``attemptDurationSeconds``. Observed
#: on 2026-07-28 against a job given 180 seconds and a command that slept 600.
BATCH_TIMEOUT_STATUS_REASON: Final = "Job attempt duration exceeded timeout"


class BatchJobEvidence(FreshEvidenceModel):
    """One Batch job as the service describes it, joined to its run by the job name.

    **The exit code is the field this record exists for.** The lineage store says a run
    ``failed``; only Batch says the container exited 3. Those are different facts, and the
    criterion about a failure preserving its reason is about the second one. A record that
    carried only the outcome would let a job killed by the scheduler read exactly like a
    command that returned non-zero.

    ``container_exit_code`` and ``status_reason`` are both optional because a job that
    never placed has neither, and recording a zero for "no container ran" would be the
    worst available answer -- zero is the success value.

    ``log_stream_name`` is the stream and never the group. The group is in the binding
    already and resolves to every job on the queue; only the stream resolves to this one.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    region: AwsRegionName
    run_id: RunId
    batch_job_id: SchedulerJobId
    #: The job name Batch holds, which the platform sets to the run id. Recorded rather
    #: than assumed equal to ``run_id``: the whole join rests on them agreeing, so a
    #: capture that wrote one value into both fields could not show that they do.
    batch_job_name: RunId
    status: BatchJobStatus
    status_reason: SecretFreeStr | None = Field(default=None, max_length=1024)
    container_exit_code: int | None = None
    log_stream_name: LogStreamName | None = None
    job_queue_name: SecretFreeStr = Field(min_length=1)
    job_definition_name: SecretFreeStr = Field(min_length=1)
    started_at: UtcTimestamp | None = None
    stopped_at: UtcTimestamp | None = None
    #: How many attempts Batch recorded. A retry that the platform did not ask for would
    #: show up here and nowhere else in the lineage.
    attempt_count: int = Field(ge=0)

    @property
    def joins_to_its_run(self) -> bool:
        return self.batch_job_name == self.run_id

    @property
    def succeeded(self) -> bool:
        return self.status == "SUCCEEDED" and self.container_exit_code == 0

    @property
    def timed_out(self) -> bool:
        """Whether Batch stopped this job for outrunning its attempt duration.

        Matched on the service's own wording, pinned rather than approximated. Two
        failures that look alike in every other field are entirely different events: a
        command that returned non-zero decided its own fate and has an exit code, while a
        job the scheduler killed has none. Reading them the same way would let a timeout
        that never fired look like a workload that failed on its own.

        If AWS rewords the reason this goes false and the check resting on it fails
        loudly, which is the right outcome -- somebody re-reads it rather than a timeout
        quietly reclassifying itself as an ordinary failure.
        """
        return (
            self.status == "FAILED"
            and self.status_reason == BATCH_TIMEOUT_STATUS_REASON
            and self.container_exit_code is None
        )


class LogStreamEvidence(FreshEvidenceModel):
    """The lines a container actually printed, fetched back out of its recorded stream.

    This is the record that distinguishes a recorded log *group* from a recorded log
    *stream*, which is the mutation the logs criterion exists to catch: a group name reads
    as complete and resolves to every job on the queue. Fetching the stream back and
    finding the line the container printed is the only thing that tells them apart.

    ``lines`` holds the messages rather than a count, because a stream that exists and is
    empty is a different failure from one that has the output in it, and a count of zero
    cannot say which of those a reader is looking at.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    region: AwsRegionName
    run_id: RunId
    log_group_name: LogGroupName
    log_stream_name: LogStreamName
    lines: Annotated[tuple[str, ...], BeforeValidator(require_ordered_sequence)] = Field(
        strict=False
    )
    #: True when the capture stopped short of the end of the stream. A truncated record
    #: still proves the stream resolves and carries output; it cannot prove what the last
    #: line was, and a reader has to be able to tell.
    truncated: bool = False


class LineageObjectAttestation(ContractModel):
    """What S3 attests about one lineage object, and whether the object still loads.

    ``checksum_sha256`` and ``version_id`` are what the store says about the bytes. They
    are the criterion about attestation, and they are deliberately separate from the
    manifest hash an approval was taken against -- a reader who conflated the two would
    think one proved the other.

    ``loads_as_contract`` is the field that keeps this honest. Three bindings written
    before the ASL fix carry a whole admission payload where ``array_size`` belongs, so
    they are attested, versioned, intact, and refused by the contract that defines what a
    binding is. Recording only the attestation would describe those objects as sound.
    """

    key: SecretFreeStr = Field(min_length=1)
    record_kind: LineageRecordKind
    version_id: SecretFreeStr = Field(min_length=1)
    checksum_sha256: SecretFreeStr = Field(min_length=1)
    content_length: int = Field(ge=0)
    #: Whether the stored bytes are exactly the canonical serialization of the record they
    #: hold, computed here rather than taken on trust.
    canonical: bool
    #: Whether the object loads as the contract its key claims it is.
    loads_as_contract: bool


class RunLineageAttestation(FreshEvidenceModel):
    """Every lineage object one run wrote, with what S3 attests about each.

    Driven by the run id rather than by the bucket listing, so an object another run wrote
    cannot arrive in this record, and an object this run should have written and did not
    is absent rather than substituted for.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    run_id: RunId
    bucket: SecretFreeStr = Field(min_length=1)
    objects: Annotated[
        tuple[LineageObjectAttestation, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)

    def kinds(self) -> tuple[str, ...]:
        return tuple(sorted({record.record_kind for record in self.objects}))

    @property
    def every_object_is_attested(self) -> bool:
        return all(
            record.checksum_sha256 and record.version_id for record in self.objects
        )

    @property
    def unloadable(self) -> tuple[LineageObjectAttestation, ...]:
        return tuple(record for record in self.objects if not record.loads_as_contract)


#: Every Batch status a job can be sitting in. A search for "is there a job for this run"
#: has to name where it looked, because Batch's ListJobs answers one status at a time and
#: a search that quietly skipped one would report an absence it had not established.
EVERY_BATCH_JOB_STATUS: Final = (
    "SUBMITTED",
    "PENDING",
    "RUNNABLE",
    "STARTING",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
)


class RefusedRunEvidence(FreshEvidenceModel):
    """A run admission refused, and the absence of the job it would otherwise have started.

    **The absence is the evidence, so it is recorded rather than implied.** A capture that
    simply had no Batch job record for a refused run would look identical to one where
    nobody went and checked. ``matching_batch_job_ids`` being empty is the claim, and
    ``searched_job_statuses`` is what makes it a claim somebody can audit: Batch answers
    ``ListJobs`` one status at a time, so an absence established without naming the
    statuses searched is an absence established nowhere.

    ``decision_accepted`` is typed as a plain bool rather than pinned to ``False``. A
    refusal record that could not express "the decision actually said yes" would have no
    way to report the one outcome that matters most -- a run this platform believed it had
    refused and did not.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    region: AwsRegionName
    run_id: RunId
    decision_accepted: bool
    decision_reason: SecretFreeStr = Field(min_length=1)
    decision_detail: SecretFreeStr = Field(min_length=1)
    execution_status: Literal["SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED", "RUNNING"]
    execution_error: SecretFreeStr | None = None
    matching_batch_job_ids: OrderedStrings = Field(strict=False)
    searched_job_statuses: OrderedStrings = Field(min_length=1, strict=False)

    @property
    def refused_and_started_nothing(self) -> bool:
        return not self.decision_accepted and not self.matching_batch_job_ids

    @property
    def searched_every_status(self) -> bool:
        return set(self.searched_job_statuses) == set(EVERY_BATCH_JOB_STATUS)


class ComputeEnvironmentEvidence(FreshEvidenceModel):
    """The deployed compute environment, its capacity and the networking it landed on.

    Two criteria read this and they want opposite things from it. One asks that the
    environment exists and is usable, which is ``status`` VALID and ``state`` ENABLED. The
    other asks that it holds nothing while idle, which is ``desired_vcpus`` at zero. Both
    are properties of the same object at the same instant, so they are captured together
    rather than as two records that could be observed hours apart and read as one moment.

    The subnet and security group ids are here because the networking criterion asks what
    the environment *uses*, which is not what a template asks for -- a stack applied by
    hand can land somewhere else, and only the deployed object says where.
    """

    source: Literal["aws"]
    environment: EvidenceEnvironment
    region: AwsRegionName
    compute_environment_name: SecretFreeStr = Field(min_length=1)
    status: Literal["CREATING", "UPDATING", "DELETING", "DELETED", "VALID", "INVALID"]
    state: Literal["ENABLED", "DISABLED"]
    desired_vcpus: int = Field(ge=0)
    minimum_vcpus: int = Field(ge=0)
    maximum_vcpus: int = Field(ge=0)
    vpc_id: VpcId
    subnet_ids: Annotated[
        tuple[SubnetId, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)
    security_group_ids: Annotated[
        tuple[str, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)
    instance_types: OrderedStrings = Field(strict=False)
    #: The queues that route to this environment. Recorded so "exactly one profile is
    #: provisioned and it is backed" can be read from one record.
    job_queue_names: OrderedStrings = Field(strict=False)

    @property
    def usable(self) -> bool:
        return self.status == "VALID" and self.state == "ENABLED"

    @property
    def idle_and_holding_nothing(self) -> bool:
        return self.desired_vcpus == 0
