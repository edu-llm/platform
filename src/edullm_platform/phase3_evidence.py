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

from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from edullm_platform.contracts.base import require_ordered_sequence
from edullm_platform.evidence import FreshEvidenceModel, SecretFreeStr

__all__ = [
    "PHASE3_ROLE_TEMPLATES",
    "AccountMeasurements",
    "AuthorizationControl",
    "BatchInventory",
    "NetworkPlacement",
    "RegionAuthorization",
    "ServiceLinkedRoleRecord",
    "SubnetOffering",
    "VpcQuotaRecord",
]

#: The roles Phase 3 creates, and the committed templates that declare them. Separate from
#: the Phase 1 and Phase 2 lists for the same reason those are separate from each other: a
#: Phase 3 role drifting must not make a Phase 1 capture fail.
PHASE3_ROLE_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("sbsandbox-intern-edullm-batch-execution", "infra/iam/batch-roles.yaml"),
    ("sbsandbox-intern-edullm-batch-workload", "infra/iam/batch-roles.yaml"),
    ("sbsandbox-intern-edullm-batch-instance", "infra/iam/batch-roles.yaml"),
    ("sbsandbox-intern-edullm-lifecycle-lambda", "infra/iam/lifecycle-lambda-role.yaml"),
)

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


class ActionVerdict(FreshEvidenceModel):
    """One EC2 action, in one region, as the service answered a dry run of it."""

    action: SecretFreeStr = Field(pattern=r"^ec2:[A-Za-z]+$")
    verdict: AuthorizationVerdict
    #: The service's own error code. ``None`` only when the CLI returned no parseable
    #: error, which is itself an inconclusive answer.
    error_code: SecretFreeStr | None


class RegionAuthorization(FreshEvidenceModel):
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


class AuthorizationControl(FreshEvidenceModel):
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


class VpcQuotaRecord(FreshEvidenceModel):
    """VPCs in use against the quota, and whether an increase has been asked for.

    The quota is the one thing standing between this phase and networking it owns, so the
    request id is part of the evidence rather than a note somebody kept.
    """

    region: AwsRegionName
    quota_code: SecretFreeStr = Field(pattern=r"^L-[0-9A-F]{8}$")
    quota_value: int = Field(ge=0)
    in_use: int = Field(ge=0)
    adjustable: bool
    #: The service-quotas request id, when one has been filed. Absent means nobody has.
    increase_request_id: SecretFreeStr | None = None
    increase_request_status: SecretFreeStr | None = None

    @property
    def exhausted(self) -> bool:
        return self.in_use >= self.quota_value


class SubnetOffering(FreshEvidenceModel):
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


class NetworkPlacement(FreshEvidenceModel):
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


class ServiceLinkedRoleRecord(FreshEvidenceModel):
    """Whether one service-linked role exists. Batch's does not, and that is a build step."""

    role_name: SecretFreeStr = Field(pattern=r"^AWSServiceRoleFor[A-Za-z0-9]+$")
    exists: bool


class BatchInventory(FreshEvidenceModel):
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
