"""Capture what Phase 3 rests on, from the account, into a record that expires.

Phase 3's premises are facts about an account nobody here controls. The first revision of
its plan was written on one that was simply wrong, so the premises are captured and
committed rather than asserted. Everything this reads is read-only; the EC2 authorization
matrix uses ``--dry-run``, which evaluates authorization and then stops.

Writes only under ``docs-frank/working/phase-3-evidence/`` and refuses anywhere else, so a
capture stays local until somebody reads it and copies what they want into ``fixtures/``.
That is the same rule the Phase 1 and Phase 2 capture tools apply and it exists for the same
reason: a capture is raw account output until a human has looked at it.

Exit 0 when the capture is complete and its controls agree, 2 when it could not be taken or
a control disagreed. A control that disagrees means the classifier is wrong, which makes the
whole matrix untrustworthy rather than one row of it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from edullm_platform.ec2_authorization import (
    CONTROL_OBSERVATIONS,
    Ec2AuthorizationVerdict,
    classify_dry_run,
    phase3_ec2_probes,
)
from edullm_platform.evidence import scan_for_secrets

__all__ = ["ALLOWED_OUTPUT_SUFFIX", "CaptureFailedError", "main"]

ALLOWED_OUTPUT_SUFFIX: Final = Path("docs-frank/working/phase-3-evidence")
AWS_CALL_TIMEOUT_SECONDS: Final = 90

VPC_QUOTA_CODE: Final = "L-F678F1CE"
STANDARD_VCPU_QUOTA_CODE: Final = "L-1216C47A"
COMPUTE_ENVIRONMENTS_PER_QUEUE_QUOTA_CODE: Final = "L-0B0E4F5B"

SERVICE_LINKED_ROLES: Final = (
    "AWSServiceRoleForBatch",
    "AWSServiceRoleForEC2Spot",
    "AWSServiceRoleForECS",
    "AWSServiceRoleForAutoScaling",
)

CAPTURE_METHOD: Final = (
    "EC2 authorization is read with --dry-run against the real API, never from "
    "iam:SimulatePrincipalPolicy. DryRunOperation means the request would have succeeded; "
    "UnauthorizedOperation means it would not; a *LimitExceeded code means authorization "
    "passed and there is no room; anything else means the request never reached "
    "authorization. The simulator's OrganizationsDecisionDetail reported ten of these "
    "actions as denied in both regions when seven are authorized in us-east-1, which is "
    "why the method is recorded here rather than assumed."
)


class CaptureFailedError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def aws(
    arguments: Sequence[str], *, profile: str, region: str | None = None
) -> subprocess.CompletedProcess[str]:
    command = ["aws", *arguments, "--profile", profile, "--output", "json"]
    if region is not None:
        command += ["--region", region]
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=AWS_CALL_TIMEOUT_SECONDS,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CaptureFailedError(f"aws_call_timed_out:{arguments[0]}:{arguments[1]}") from exc
    except OSError as exc:
        raise CaptureFailedError("aws_cli_unavailable") from exc


def aws_json(
    arguments: Sequence[str], *, profile: str, region: str | None = None
) -> Any:
    completed = aws(arguments, profile=profile, region=region)
    if completed.returncode != 0:
        raise CaptureFailedError(f"aws_call_failed:{arguments[0]}:{arguments[1]}")
    if not completed.stdout.strip():
        return {}
    try:
        return json.loads(completed.stdout)
    except ValueError as exc:
        raise CaptureFailedError(f"aws_answer_unreadable:{arguments[0]}") from exc


def resolve_ami(*, profile: str, region: str) -> str:
    value = aws_json(
        [
            "ssm",
            "get-parameter",
            "--name",
            "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id",
            "--query",
            "Parameter.Value",
        ],
        profile=profile,
        region=region,
    )
    if not isinstance(value, str) or not value.startswith("ami-"):
        raise CaptureFailedError(f"ami_lookup_failed:{region}")
    return value


def capture_region_authorization(
    *, profile: str, region: str, vpc_id: str, subnet_id: str, instance_type: str
) -> dict[str, Any]:
    image_id = resolve_ami(profile=profile, region=region)
    verdicts: list[dict[str, Any]] = []
    for probe in phase3_ec2_probes(
        vpc_id=vpc_id,
        subnet_id=subnet_id,
        image_id=image_id,
        instance_type=instance_type,
    ):
        completed = aws(
            [*probe.arguments, "--dry-run"], profile=profile, region=region
        )
        result = classify_dry_run(
            action=probe.action,
            operation=probe.operation,
            region=region,
            returncode=completed.returncode,
            stderr=completed.stderr,
        )
        if result.verdict is Ec2AuthorizationVerdict.INCONCLUSIVE:
            raise CaptureFailedError(
                f"probe_inconclusive:{region}:{probe.action}:{result.reason}"
            )
        verdicts.append(
            {
                "action": probe.action,
                "verdict": result.verdict.value,
                "error_code": result.error_code,
            }
        )
    return {
        "region": region,
        "vpc_id": vpc_id,
        "subnet_id": subnet_id,
        "instance_type": instance_type,
        "verdicts": verdicts,
    }


def capture_controls() -> list[dict[str, Any]]:
    recorded: list[dict[str, Any]] = []
    for control in CONTROL_OBSERVATIONS:
        classified = classify_dry_run(
            action=control.action,
            operation=control.operation,
            region=control.region,
            returncode=control.returncode,
            stderr=control.stderr,
        ).verdict
        recorded.append(
            {
                "action": control.action,
                "region": control.region,
                "expected": control.expected.value,
                "classified": classified.value,
                "agrees": classified is control.expected,
                "established_by": control.established_by,
            }
        )
    return recorded


def capture_vpc_quota(*, profile: str, region: str) -> dict[str, Any]:
    quota = aws_json(
        [
            "service-quotas",
            "get-service-quota",
            "--service-code",
            "vpc",
            "--quota-code",
            VPC_QUOTA_CODE,
        ],
        profile=profile,
        region=region,
    )["Quota"]
    vpcs = aws_json(
        ["ec2", "describe-vpcs", "--query", "Vpcs[].VpcId"], profile=profile, region=region
    )
    history = aws_json(
        [
            "service-quotas",
            "list-requested-service-quota-change-history-by-quota",
            "--service-code",
            "vpc",
            "--quota-code",
            VPC_QUOTA_CODE,
        ],
        profile=profile,
        region=region,
    ).get("RequestedQuotas", [])
    latest = history[0] if history else None
    return {
        "region": region,
        "quota_code": VPC_QUOTA_CODE,
        "quota_value": int(quota["Value"]),
        "in_use": len(vpcs),
        "adjustable": bool(quota["Adjustable"]),
        "increase_request_id": None if latest is None else latest.get("Id"),
        "increase_request_status": None if latest is None else latest.get("Status"),
    }


def capture_placement(
    *, profile: str, region: str, vpc_id: str, vpc_is_ours: bool, terms: str, instance_type: str
) -> dict[str, Any]:
    offered_zones = set(
        aws_json(
            [
                "ec2",
                "describe-instance-type-offerings",
                "--location-type",
                "availability-zone",
                "--filters",
                f"Name=instance-type,Values={instance_type}",
                "--query",
                "InstanceTypeOfferings[].Location",
            ],
            profile=profile,
            region=region,
        )
    )
    subnets = aws_json(
        [
            "ec2",
            "describe-subnets",
            "--filters",
            f"Name=vpc-id,Values={vpc_id}",
            "--query",
            "sort_by(Subnets,&AvailabilityZone)[].{Id:SubnetId,Az:AvailabilityZone,"
            "Public:MapPublicIpOnLaunch,Free:AvailableIpAddressCount}",
        ],
        profile=profile,
        region=region,
    )
    return {
        "region": region,
        "vpc_id": vpc_id,
        "vpc_is_ours": vpc_is_ours,
        "borrowing_terms": terms,
        "subnets": [
            {
                "subnet_id": subnet["Id"],
                "availability_zone": subnet["Az"],
                "instance_type_offered": subnet["Az"] in offered_zones,
                "map_public_ip_on_launch": bool(subnet["Public"]),
                "available_ip_address_count": int(subnet["Free"]),
            }
            for subnet in subnets
        ],
    }


def capture_service_linked_roles(*, profile: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for role_name in SERVICE_LINKED_ROLES:
        completed = aws(["iam", "get-role", "--role-name", role_name], profile=profile)
        records.append({"role_name": role_name, "exists": completed.returncode == 0})
    return records


def _quota_value(code: str, *, service: str, profile: str, region: str, default: int) -> int:
    completed = aws(
        [
            "service-quotas",
            "get-service-quota",
            "--service-code",
            service,
            "--quota-code",
            code,
            "--query",
            "Quota.Value",
        ],
        profile=profile,
        region=region,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return default
    try:
        return int(float(json.loads(completed.stdout)))
    except (ValueError, TypeError):
        return default


def capture_batch(*, profile: str, region: str) -> dict[str, Any]:
    environments = aws_json(
        ["batch", "describe-compute-environments", "--query", "computeEnvironments[].computeEnvironmentName"],
        profile=profile,
        region=region,
    )
    queues = aws_json(
        ["batch", "describe-job-queues", "--query", "jobQueues[].jobQueueName"],
        profile=profile,
        region=region,
    )
    definitions = aws_json(
        [
            "batch",
            "describe-job-definitions",
            "--status",
            "ACTIVE",
            "--query",
            "jobDefinitions[].jobDefinitionName",
        ],
        profile=profile,
        region=region,
    )
    return {
        "region": region,
        "compute_environment_count": len(environments),
        "job_queue_count": len(queues),
        "job_definition_count": len(definitions),
        "compute_environments_per_queue_quota": _quota_value(
            COMPUTE_ENVIRONMENTS_PER_QUEUE_QUOTA_CODE,
            service="batch",
            profile=profile,
            region=region,
            default=3,
        ),
        "standard_on_demand_vcpu_quota": _quota_value(
            STANDARD_VCPU_QUOTA_CODE,
            service="ec2",
            profile=profile,
            region=region,
            default=0,
        ),
    }


def capture_account(
    *,
    profile: str,
    home_region: str,
    second_region: str,
    home_vpc: str,
    home_subnet: str,
    second_vpc: str,
    second_subnet: str,
    instance_type: str,
    vpc_is_ours: bool,
    borrowing_terms: str,
    observed_at: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "environment": "sandbox",
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "method": CAPTURE_METHOD,
        "controls": capture_controls(),
        "regions": [
            capture_region_authorization(
                profile=profile,
                region=home_region,
                vpc_id=home_vpc,
                subnet_id=home_subnet,
                instance_type=instance_type,
            ),
            capture_region_authorization(
                profile=profile,
                region=second_region,
                vpc_id=second_vpc,
                subnet_id=second_subnet,
                instance_type=instance_type,
            ),
        ],
        "vpc_quota": capture_vpc_quota(profile=profile, region=home_region),
        "placement": capture_placement(
            profile=profile,
            region=home_region,
            vpc_id=home_vpc,
            vpc_is_ours=vpc_is_ours,
            terms=borrowing_terms,
            instance_type=instance_type,
        ),
        "service_linked_roles": capture_service_linked_roles(profile=profile),
        "batch": capture_batch(profile=profile, region=home_region),
    }


def check_output_location(path: Path) -> None:
    if ALLOWED_OUTPUT_SUFFIX.as_posix() not in path.resolve().as_posix():
        raise CaptureFailedError(
            f"output_must_be_under:{ALLOWED_OUTPUT_SUFFIX.as_posix()}"
        )


def write_record(path: Path, record: Mapping[str, Any]) -> None:
    serialized = json.dumps(record, indent=2, sort_keys=True) + "\n"
    try:
        scan_for_secrets(serialized)
    except ValueError as exc:
        raise CaptureFailedError("record_holds_a_credential") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialized, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture the account facts Phase 3 rests on. Read-only."
    )
    parser.add_argument("--aws-profile", required=True)
    parser.add_argument("--target", choices=["account"], required=True)
    parser.add_argument("--home-region", default="us-east-1")
    parser.add_argument("--second-region", default="us-east-2")
    parser.add_argument("--home-vpc", required=True)
    parser.add_argument("--home-subnet", required=True)
    parser.add_argument("--second-vpc", required=True)
    parser.add_argument("--second-subnet", required=True)
    parser.add_argument("--instance-type", default="c7i.8xlarge")
    parser.add_argument(
        "--vpc-is-ours",
        action="store_true",
        help="set once the compute environment runs in a VPC this project created.",
    )
    parser.add_argument(
        "--borrowing-terms",
        default="",
        help="why we may use a VPC that is not ours. Required while --vpc-is-ours is unset.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    output = arguments.output_dir / "account-measurements.json"
    try:
        check_output_location(arguments.output_dir)
        if not arguments.vpc_is_ours and not arguments.borrowing_terms.strip():
            raise CaptureFailedError("borrowed_vpc_needs_terms")
        record = capture_account(
            profile=arguments.aws_profile,
            home_region=arguments.home_region,
            second_region=arguments.second_region,
            home_vpc=arguments.home_vpc,
            home_subnet=arguments.home_subnet,
            second_vpc=arguments.second_vpc,
            second_subnet=arguments.second_subnet,
            instance_type=arguments.instance_type,
            vpc_is_ours=arguments.vpc_is_ours,
            borrowing_terms=arguments.borrowing_terms,
            observed_at=datetime.now(tz=UTC).replace(microsecond=0),
        )
        write_record(output, record)
    except CaptureFailedError as exc:
        print(exc.reason, file=sys.stderr)
        return 2
    except OSError:
        print("output_unwritable", file=sys.stderr)
        return 2

    disagreeing = [entry for entry in record["controls"] if not entry["agrees"]]
    for entry in disagreeing:
        print(
            f"control_disagrees:{entry['action']}:{entry['region']}",
            file=sys.stderr,
        )
    print(f"wrote {output}")
    return 2 if disagreeing else 0


if __name__ == "__main__":
    sys.exit(main())
