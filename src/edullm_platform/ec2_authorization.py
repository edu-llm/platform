"""Ask EC2 whether a call is authorized, without making it.

**Why this exists rather than a policy simulation.** Phase 3's plan opened, in its first
revision, with the claim that a service control policy forbids creating a VPC in this
account. Every action it named is in fact authorized in ``us-east-1``. The claim came from
``iam:SimulatePrincipalPolicy``, whose ``OrganizationsDecisionDetail.AllowedByOrganizations``
came back ``false`` for ten EC2 actions that a peer principal had performed successfully
hours earlier — and came back ``false`` with ``aws:RequestedRegion`` supplied and
``--resource-arns`` supplied, which is more context than the master plan's standing warning
about empty simulation context asks for. It also returned the same answer for both permitted
regions when the two genuinely differ.

So the rule this module encodes is narrower than "supply the context keys". It is: for an
EC2 authorization question, ask EC2. ``--dry-run`` evaluates authorization and then stops
before doing anything, which makes it both non-mutating and authoritative in a way a model
of the policy is not.

**The four verdicts, and why quota is one of them.** A simulator cannot tell "you may not"
from "you may, and there is no room". EC2 can, and the difference decided this phase's plan:
``ec2:CreateVpc`` in ``us-east-1`` is :data:`~Ec2AuthorizationVerdict.QUOTA_BLOCKED`, which
is a support request, while in ``us-east-2`` it is :data:`~Ec2AuthorizationVerdict.DENIED`,
which is not something we can fix. Collapsing those two into "failed" would have hidden the
only actionable half.

**Why an unrecognised answer is inconclusive rather than denied.** A dry run that never
reached authorization answers about the request instead of about the caller. ``RunInstances``
with a malformed AMI id returns ``InvalidAMIID.Malformed`` whatever the caller may do, and
reading that as a denial would report a permitted action as refused. This was not
hypothetical: it happened while measuring the matrix this module now carries, and the
malformed id was in the first command anybody ran. :data:`Ec2AuthorizationVerdict.INCONCLUSIVE`
is therefore a first-class outcome and the caller is expected to fix the probe, not the
policy.

**The operation is checked, not assumed.** Same discipline as the denial matrices: an error
naming a different operation is an answer to a different question, and is refused rather
than filed under the action that was asked about.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from .publisher_denials import parse_aws_cli_error

__all__ = [
    "AUTHORIZED_ERROR_CODE",
    "CONTROL_OBSERVATIONS",
    "DENIED_ERROR_CODES",
    "PHASE3_EC2_PROBES",
    "QUOTA_ERROR_CODE_SUFFIX",
    "ControlObservation",
    "Ec2AuthorizationProbe",
    "Ec2AuthorizationResult",
    "Ec2AuthorizationVerdict",
    "classify_dry_run",
]

#: What EC2 answers a dry run it would have allowed. The message reads "Request would have
#: succeeded, but DryRun flag is set", which is an authorization answer and nothing else.
AUTHORIZED_ERROR_CODE: Final = "DryRunOperation"

#: What EC2 answers a dry run it would have refused. ``UnauthorizedOperation`` is EC2's own
#: spelling; the other two appear when the refusal is raised by IAM rather than by EC2, which
#: happens for actions reached through a different service surface.
DENIED_ERROR_CODES: Final = frozenset(
    {"UnauthorizedOperation", "AccessDenied", "AccessDeniedException"}
)

#: How EC2 spells running out of room. Matched as a suffix because every one of these is
#: ``<Thing>LimitExceeded`` — ``VpcLimitExceeded``, ``AddressLimitExceeded``,
#: ``InternetGatewayLimitExceeded`` — and the list of things is longer than it is useful to
#: enumerate. A quota answer only ever arrives from a call that got past authorization, so
#: it is recorded as authorized-with-no-room rather than as a failure.
QUOTA_ERROR_CODE_SUFFIX: Final = "LimitExceeded"


class Ec2AuthorizationVerdict(StrEnum):
    """What one dry run established about one action."""

    #: EC2 would have allowed it.
    AUTHORIZED = "authorized"
    #: EC2 would have refused it, and no policy we control can change that.
    DENIED = "denied"
    #: Authorized, and there is no room. A quota increase, not a permission change.
    QUOTA_BLOCKED = "quota_blocked"
    #: The call never reached authorization, so it says nothing about the caller.
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class Ec2AuthorizationProbe:
    """One EC2 call to ask about, and the operation its answer must name.

    ``arguments`` omit ``--profile``, ``--region`` and ``--dry-run``; the runner adds those,
    so a probe cannot accidentally be defined without the flag that makes it non-mutating.
    """

    action: str
    operation: str
    arguments: tuple[str, ...]
    #: Why the probe is shaped the way it is, where that is not obvious. A probe needing a
    #: real resource id is the usual reason.
    note: str = ""


@dataclass(frozen=True)
class Ec2AuthorizationResult:
    action: str
    region: str
    verdict: Ec2AuthorizationVerdict
    #: The service's error code, or ``None`` when the CLI returned no parseable error.
    error_code: str | None
    #: A machine-readable reason, present only when the verdict is inconclusive. Never a
    #: service message, because an EC2 error message names the account.
    reason: str | None = None

    @property
    def authorized(self) -> bool:
        """Whether authorization passed, quota notwithstanding."""
        return self.verdict in {
            Ec2AuthorizationVerdict.AUTHORIZED,
            Ec2AuthorizationVerdict.QUOTA_BLOCKED,
        }


def classify_dry_run(
    *,
    action: str,
    operation: str,
    region: str,
    returncode: int,
    stderr: str,
) -> Ec2AuthorizationResult:
    """Read one ``--dry-run`` outcome as an authorization answer, or refuse to.

    A zero return code is inconclusive rather than authorized. ``--dry-run`` is supposed to
    make every EC2 call fail, so a success means the flag was dropped somewhere between here
    and the service — and if the flag was dropped, something may have been created. That is
    worth surfacing loudly rather than filing as a pass.
    """

    def result(
        verdict: Ec2AuthorizationVerdict,
        *,
        error_code: str | None,
        reason: str | None = None,
    ) -> Ec2AuthorizationResult:
        return Ec2AuthorizationResult(
            action=action,
            region=region,
            verdict=verdict,
            error_code=error_code,
            reason=reason,
        )

    if returncode == 0:
        return result(
            Ec2AuthorizationVerdict.INCONCLUSIVE,
            error_code=None,
            reason="dry_run_succeeded_so_the_flag_was_not_honoured",
        )

    error = parse_aws_cli_error(stderr)
    if error is None:
        return result(
            Ec2AuthorizationVerdict.INCONCLUSIVE,
            error_code=None,
            reason="no_service_error_in_stderr",
        )
    if error.operation != operation:
        return result(
            Ec2AuthorizationVerdict.INCONCLUSIVE,
            error_code=error.code,
            reason=f"answer_named_another_operation:{error.operation}",
        )
    if error.code == AUTHORIZED_ERROR_CODE:
        return result(Ec2AuthorizationVerdict.AUTHORIZED, error_code=error.code)
    if error.code in DENIED_ERROR_CODES:
        return result(Ec2AuthorizationVerdict.DENIED, error_code=error.code)
    if error.code.endswith(QUOTA_ERROR_CODE_SUFFIX):
        return result(Ec2AuthorizationVerdict.QUOTA_BLOCKED, error_code=error.code)
    return result(
        Ec2AuthorizationVerdict.INCONCLUSIVE,
        error_code=error.code,
        reason="request_did_not_reach_authorization",
    )


@dataclass(frozen=True)
class ControlObservation:
    """One captured answer whose verdict is already known from somewhere else.

    The controls are the reason to believe the rest of the matrix. Each one was observed
    against the live account and its verdict is established independently: by CloudTrail
    showing a peer principal performing the action, or by the real call being made and
    failing on quota. A classifier change that reclassified any of these is a regression
    whatever else still passes.
    """

    action: str
    operation: str
    region: str
    returncode: int
    stderr: str
    expected: Ec2AuthorizationVerdict
    established_by: str


#: Four captured answers, one per verdict. Kept as literal CLI stderr rather than as a
#: parsed shape so that a change to the parsing is covered too.
CONTROL_OBSERVATIONS: Final[tuple[ControlObservation, ...]] = (
    ControlObservation(
        action="ec2:CreateSecurityGroup",
        operation="CreateSecurityGroup",
        region="us-east-1",
        returncode=254,
        stderr=(
            "\nAn error occurred (DryRunOperation) when calling the CreateSecurityGroup "
            "operation: Request would have succeeded, but DryRun flag is set.\n"
        ),
        expected=Ec2AuthorizationVerdict.AUTHORIZED,
        established_by=(
            "CloudTrail records CreateSecurityGroup succeeding for a peer "
            "Intern-*-sbsandbox role in us-east-1 on 2026-07-27, with no errorCode."
        ),
    ),
    ControlObservation(
        action="ec2:CreateVpc",
        operation="CreateVpc",
        region="us-east-2",
        returncode=254,
        stderr=(
            "\nAn error occurred (UnauthorizedOperation) when calling the CreateVpc "
            "operation: You are not authorized to perform this operation.\n"
        ),
        expected=Ec2AuthorizationVerdict.DENIED,
        established_by=(
            "The same probe in us-east-1 returns DryRunOperation, so the difference is "
            "the region rather than the credentials or the probe."
        ),
    ),
    ControlObservation(
        action="ec2:CreateVpc",
        operation="CreateVpc",
        region="us-east-1",
        returncode=254,
        stderr=(
            "\nAn error occurred (VpcLimitExceeded) when calling the CreateVpc operation: "
            "The maximum number of VPCs has been reached.\n"
        ),
        expected=Ec2AuthorizationVerdict.QUOTA_BLOCKED,
        established_by=(
            "Observed by making the real call, not a dry run: five VPCs exist against a "
            "quota of five. Authorization passed and there was no room, which is the "
            "distinction a policy simulation cannot draw."
        ),
    ),
    ControlObservation(
        action="ec2:RunInstances",
        operation="RunInstances",
        region="us-east-1",
        returncode=254,
        stderr=(
            "\nAn error occurred (InvalidAMIID.Malformed) when calling the RunInstances "
            'operation: Invalid id: "ami-0abcdef1234567890" (expecting "ami-...")\n'
        ),
        expected=Ec2AuthorizationVerdict.INCONCLUSIVE,
        established_by=(
            "A request EC2 rejected before authorizing anybody. The same probe with a "
            "real AMI id returns DryRunOperation, so reading this as a denial would have "
            "reported an authorized action as refused."
        ),
    ),
)


def _vpc_probe(cidr: str) -> Ec2AuthorizationProbe:
    return Ec2AuthorizationProbe(
        action="ec2:CreateVpc",
        operation="CreateVpc",
        arguments=("ec2", "create-vpc", "--cidr-block", cidr),
        note=(
            "The CIDR is never allocated, because a dry run stops before allocation. It "
            "still has to be well-formed or EC2 answers about the CIDR instead."
        ),
    )


def phase3_ec2_probes(
    *,
    vpc_id: str,
    subnet_id: str,
    image_id: str,
    instance_type: str,
    probe_cidr: str = "10.99.0.0/16",
    probe_subnet_cidr: str = "10.99.250.0/24",
) -> tuple[Ec2AuthorizationProbe, ...]:
    """Every EC2 authorization Phase 3 depends on, as calls that create nothing.

    Three arguments name resources that must already exist. A dry run against an absent
    VPC or a malformed AMI id is answered by the resource rather than by the caller, which
    is the inconclusive case above and the reason these are parameters rather than
    constants: the ids differ per account and a wrong one produces a confident non-answer.
    """
    return (
        _vpc_probe(probe_cidr),
        Ec2AuthorizationProbe(
            action="ec2:CreateSubnet",
            operation="CreateSubnet",
            arguments=(
                "ec2",
                "create-subnet",
                "--vpc-id",
                vpc_id,
                "--cidr-block",
                probe_subnet_cidr,
            ),
        ),
        Ec2AuthorizationProbe(
            action="ec2:CreateSecurityGroup",
            operation="CreateSecurityGroup",
            arguments=(
                "ec2",
                "create-security-group",
                "--group-name",
                "edullm-authorization-probe",
                "--description",
                "authorization probe, creates nothing",
                "--vpc-id",
                vpc_id,
            ),
        ),
        Ec2AuthorizationProbe(
            action="ec2:CreateRouteTable",
            operation="CreateRouteTable",
            arguments=("ec2", "create-route-table", "--vpc-id", vpc_id),
        ),
        Ec2AuthorizationProbe(
            action="ec2:CreateInternetGateway",
            operation="CreateInternetGateway",
            arguments=("ec2", "create-internet-gateway"),
        ),
        Ec2AuthorizationProbe(
            action="ec2:RunInstances",
            operation="RunInstances",
            arguments=(
                "ec2",
                "run-instances",
                "--image-id",
                image_id,
                "--instance-type",
                instance_type,
                "--subnet-id",
                subnet_id,
            ),
            note=(
                "The AMI id must be real and in this region. A malformed or foreign id is "
                "answered by EC2 before authorization and the probe says nothing."
            ),
        ),
        Ec2AuthorizationProbe(
            action="ec2:CreateLaunchTemplate",
            operation="CreateLaunchTemplate",
            arguments=(
                "ec2",
                "create-launch-template",
                "--launch-template-name",
                "edullm-authorization-probe",
                "--launch-template-data",
                "{}",
            ),
        ),
    )


#: The action names the matrix covers, in the order :func:`phase3_ec2_probes` returns them.
#: Kept separately so a test can assert the two agree rather than trusting the docstring.
PHASE3_EC2_PROBES: Final[tuple[str, ...]] = (
    "ec2:CreateVpc",
    "ec2:CreateSubnet",
    "ec2:CreateSecurityGroup",
    "ec2:CreateRouteTable",
    "ec2:CreateInternetGateway",
    "ec2:RunInstances",
    "ec2:CreateLaunchTemplate",
)


def verdicts_by_action(
    results: Sequence[Ec2AuthorizationResult],
) -> dict[str, Ec2AuthorizationVerdict]:
    return {result.action: result.verdict for result in results}
