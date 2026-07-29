"""Ask EC2, in each permitted region, which of Phase 3's calls it would allow.

Creates nothing. Every call carries ``--dry-run``, which makes EC2 evaluate authorization
and then stop, so the answer is the service's own rather than a model of its policy.

**Read** ``edullm_platform.ec2_authorization`` **before changing anything here.** It records
why this tool exists at all: a policy simulation reported ten of these actions as denied in
both regions, and seven of them are authorized in ``us-east-1``. The wrong answer was
specific, plausible and uncontrolled, and it went into a plan.

**Exit codes.** 0 when every probe returned a usable answer; 1 when at least one probe was
inconclusive, because a matrix with a hole in it should not read as a clean run; 2 when the
probe could not run at all. A denial is not a failure — it is one of the answers this tool
exists to collect — so a fully denied region still exits 0.

``--dry-run`` here means "print the AWS calls and make none of them", which is a different
thing from the ``--dry-run`` flag on the EC2 calls themselves. Those are always present.

**What it takes from the shared capture tooling.**
:func:`~edullm_platform.capture_tooling.write_record` and
:func:`~edullm_platform.capture_tooling.check_output_location`, which between them are the
whole of where this may write and what it may write there. The call wrapper stays local:
every call here carries ``--dry-run`` and is read through its exit status and stderr, so a
non-zero exit is the answer rather than a failure -- the opposite of what the shared
wrapper is for.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from edullm_platform.capture_tooling import (
    CaptureFailedError,
    check_output_location,
    write_record,
)
from edullm_platform.ec2_authorization import (
    CONTROL_OBSERVATIONS,
    Ec2AuthorizationProbe,
    Ec2AuthorizationResult,
    Ec2AuthorizationVerdict,
    classify_dry_run,
    phase3_ec2_probes,
)

AWS_CALL_TIMEOUT_SECONDS: Final = 60

#: Where a capture may be written. The same rule the Phase 1 and Phase 2 capture tools
#: apply: a capture stays local until somebody reads it and copies what they want into
#: fixtures/.
ALLOWED_OUTPUT_SUFFIX: Final = Path("docs-frank/working/phase-3-evidence")


class ProbeFailedError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class RegionInputs:
    """The resource ids a region's probes need in order to reach authorization."""

    region: str
    vpc_id: str
    subnet_id: str
    image_id: str


def command_for(
    probe: Ec2AuthorizationProbe, *, profile: str, region: str
) -> list[str]:
    return [
        "aws",
        *probe.arguments,
        "--dry-run",
        "--profile",
        profile,
        "--region",
        region,
        "--output",
        "json",
    ]


def run_probe(
    probe: Ec2AuthorizationProbe, *, profile: str, region: str
) -> Ec2AuthorizationResult:
    command = command_for(probe, profile=profile, region=region)
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=AWS_CALL_TIMEOUT_SECONDS,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return Ec2AuthorizationResult(
            action=probe.action,
            region=region,
            verdict=Ec2AuthorizationVerdict.INCONCLUSIVE,
            error_code=None,
            reason="probe_timed_out",
        )
    except OSError as exc:
        raise ProbeFailedError("aws_cli_unavailable") from exc
    return classify_dry_run(
        action=probe.action,
        operation=probe.operation,
        region=region,
        returncode=completed.returncode,
        stderr=completed.stderr,
    )


def resolve_ecs_optimized_ami(*, profile: str, region: str) -> str:
    """Read a real AMI id for the region, because a fake one answers about itself.

    Public SSM parameter, no permission needed beyond the session. A failure here stops the
    run rather than falling back to a placeholder: a placeholder makes the RunInstances
    probe inconclusive, which is the confident non-answer this whole module exists to avoid.
    """
    command = [
        "aws",
        "ssm",
        "get-parameter",
        "--name",
        "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id",
        "--query",
        "Parameter.Value",
        "--output",
        "text",
        "--profile",
        profile,
        "--region",
        region,
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=AWS_CALL_TIMEOUT_SECONDS,
            shell=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise ProbeFailedError(f"ami_lookup_failed:{region}") from exc
    image_id = completed.stdout.strip()
    if completed.returncode != 0 or not image_id.startswith("ami-"):
        raise ProbeFailedError(f"ami_lookup_failed:{region}")
    return image_id


def probe_region(
    inputs: RegionInputs,
    *,
    profile: str,
    instance_type: str,
) -> tuple[Ec2AuthorizationResult, ...]:
    probes = phase3_ec2_probes(
        vpc_id=inputs.vpc_id,
        subnet_id=inputs.subnet_id,
        image_id=inputs.image_id,
        instance_type=instance_type,
    )
    return tuple(
        run_probe(probe, profile=profile, region=inputs.region) for probe in probes
    )


def control_results() -> list[dict[str, Any]]:
    """Re-run the classifier over the four captured controls and record what it said.

    Present in the record rather than only in the test suite, because a reader holding the
    capture should be able to see that the classifier still agrees with four answers whose
    verdicts were established some other way.
    """
    recorded: list[dict[str, Any]] = []
    for control in CONTROL_OBSERVATIONS:
        result = classify_dry_run(
            action=control.action,
            operation=control.operation,
            region=control.region,
            returncode=control.returncode,
            stderr=control.stderr,
        )
        recorded.append(
            {
                "action": control.action,
                "region": control.region,
                "expected": control.expected.value,
                "classified": result.verdict.value,
                "agrees": result.verdict is control.expected,
                "established_by": control.established_by,
            }
        )
    return recorded


def build_record(
    *,
    observed_at: datetime,
    instance_type: str,
    by_region: Mapping[str, Sequence[Ec2AuthorizationResult]],
    inputs_by_region: Mapping[str, RegionInputs],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "probe": "ec2-authorization-dry-run",
        "environment": "sandbox",
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "instance_type": instance_type,
        "method": (
            "Every call carries --dry-run, so EC2 evaluates authorization and stops. "
            "DryRunOperation means the request would have succeeded; UnauthorizedOperation "
            "means it would not; a *LimitExceeded code means authorization passed and there "
            "is no room. Any other code means the request never reached authorization and "
            "the probe says nothing about the caller."
        ),
        "controls": control_results(),
        "regions": {
            region: {
                "vpc_id": inputs_by_region[region].vpc_id,
                "subnet_id": inputs_by_region[region].subnet_id,
                "image_id": inputs_by_region[region].image_id,
                "results": [
                    {
                        "action": result.action,
                        "verdict": result.verdict.value,
                        "error_code": result.error_code,
                        "reason": result.reason,
                    }
                    for result in results
                ],
            }
            for region, results in by_region.items()
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ask EC2 which of Phase 3's calls it would allow, creating nothing."
    )
    parser.add_argument("--aws-profile", required=True)
    parser.add_argument(
        "--region",
        action="append",
        dest="regions",
        metavar="REGION:VPC_ID:SUBNET_ID",
        required=True,
        help=(
            "a region and the existing vpc and subnet its probes should name, colon "
            "separated. Repeat for each region. The ids must exist, or the probes are "
            "answered by the resource rather than by the caller."
        ),
    )
    parser.add_argument(
        "--instance-type",
        default="c7i.8xlarge",
        help="the instance type the RunInstances probe names. Defaults to Phase 3's.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the AWS calls this run would make, and make none of them.",
    )
    return parser


def parse_region_argument(value: str) -> tuple[str, str, str]:
    parts = value.split(":")
    if len(parts) != 3 or not all(parts):
        raise ProbeFailedError(f"region_argument_unusable:{value}")
    return parts[0], parts[1], parts[2]


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        check_output_location(arguments.output, allowed_suffix=ALLOWED_OUTPUT_SUFFIX)
        requested = [parse_region_argument(value) for value in arguments.regions]
    except (ProbeFailedError, CaptureFailedError) as exc:
        print(exc.reason, file=sys.stderr)
        return 2

    if arguments.dry_run:
        plan = {
            "dry_run": True,
            "planned_calls": [
                {
                    "region": region,
                    "action": probe.action,
                    "command": shlex.join(
                        command_for(probe, profile=arguments.aws_profile, region=region)
                    ),
                }
                for region, vpc_id, subnet_id in requested
                for probe in phase3_ec2_probes(
                    vpc_id=vpc_id,
                    subnet_id=subnet_id,
                    image_id="<resolved-from-ssm>",
                    instance_type=arguments.instance_type,
                )
            ],
        }
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0

    inputs_by_region: dict[str, RegionInputs] = {}
    by_region: dict[str, tuple[Ec2AuthorizationResult, ...]] = {}
    try:
        for region, vpc_id, subnet_id in requested:
            inputs = RegionInputs(
                region=region,
                vpc_id=vpc_id,
                subnet_id=subnet_id,
                image_id=resolve_ecs_optimized_ami(
                    profile=arguments.aws_profile, region=region
                ),
            )
            inputs_by_region[region] = inputs
            by_region[region] = probe_region(
                inputs, profile=arguments.aws_profile, instance_type=arguments.instance_type
            )
    except ProbeFailedError as exc:
        print(exc.reason, file=sys.stderr)
        return 2

    record = build_record(
        observed_at=datetime.now(tz=UTC).replace(microsecond=0),
        instance_type=arguments.instance_type,
        by_region=by_region,
        inputs_by_region=inputs_by_region,
    )
    try:
        write_record(arguments.output, record)
    except (ProbeFailedError, CaptureFailedError) as exc:
        print(exc.reason, file=sys.stderr)
        return 2
    except OSError:
        print("output_unwritable", file=sys.stderr)
        return 2

    print(json.dumps(record["regions"], indent=2, sort_keys=True))

    disagreeing = [entry for entry in record["controls"] if not entry["agrees"]]
    if disagreeing:
        for entry in disagreeing:
            print(
                f"control_disagrees:{entry['action']}:{entry['region']}"
                f":expected={entry['expected']}:classified={entry['classified']}",
                file=sys.stderr,
            )
        return 2

    inconclusive = [
        result
        for results in by_region.values()
        for result in results
        if result.verdict is Ec2AuthorizationVerdict.INCONCLUSIVE
    ]
    if inconclusive:
        for result in inconclusive:
            print(
                f"inconclusive:{result.region}:{result.action}:{result.reason}",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
