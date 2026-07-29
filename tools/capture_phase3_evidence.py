"""Capture what Phase 3 rests on, from the account, into a record that expires.

Phase 3's premises are facts about an account nobody here controls. The first revision of
its plan was written on one that was simply wrong, so the premises are captured and
committed rather than asserted. Everything this reads is read-only; the EC2 authorization
matrix uses ``--dry-run``, which evaluates authorization and then stops.

**Three targets, and the third is what a completed run leaves behind.** ``account``
records the standing facts, ``roles`` compares the four deployed roles to the templates
that declare them, and ``run`` records one finished run: the Batch job as the service
describes it, the lines its container printed read back out of the recorded stream, what
S3 attests about every lineage object the run wrote, and the bodies of those objects. The
compute environment is captured alongside, because two criteria ask opposite things of it
-- that it is usable, and that it is holding nothing -- and they have to be read at one
instant rather than assembled from two.

**Runs are named rather than discovered.** Three runs were written before the ``"Result":
null`` fix in the admission ASL, and their bindings carry a whole admission payload in the
field where a fan-out size belongs. The lineage store is write-once, so those objects are
permanent: they are attested, versioned, intact, and refused by the contract that defines
what a binding is. ``loads_as_contract`` records exactly that, and a body that does not
load is described in the attestation rather than committed -- the corrupt ones carry the
approver's name and the image scan, and publishing a person to establish something the
attestation already says is the wrong trade.

Writes only under ``docs-frank/working/phase-3-evidence/`` and refuses anywhere else, so a
capture stays local until somebody reads it and copies what they want into ``fixtures/``.
That is the same rule the Phase 1 and Phase 2 capture tools apply and it exists for the same
reason: a capture is raw account output until a human has looked at it. The rule itself now
lives once, in :mod:`edullm_platform.capture_tooling`, along with the CLI wrappers, the
credential scan on every write and the exit-code mapping -- this module had its own copy of
all four, and the copy of ``aws`` was 69% the same text as the shared one.

Exit 0 when the capture is complete and its controls agree, 2 when it could not be taken or
a control disagreed. A control that disagrees means the classifier is wrong, which makes the
whole matrix untrustworthy rather than one row of it.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from edullm_platform.capture_tooling import (
    CaptureFailedError,
    account_identity,
    aws,
    aws_json,
    observed_now,
    report,
    run_capture,
    write_model,
    write_record,
    write_sanitized_text,
)
from edullm_platform.contracts.admission import DecisionRecord, IntentRecord
from edullm_platform.contracts.base import ContractModel
from edullm_platform.contracts.execution import BatchJobBinding
from edullm_platform.contracts.lifecycle import LifecycleEvent, SchedulerAttempt
from edullm_platform.contracts.results import ResultManifest
from edullm_platform.ec2_authorization import (
    CONTROL_OBSERVATIONS,
    Ec2AuthorizationVerdict,
    classify_dry_run,
    phase3_ec2_probes,
)
from edullm_platform.evidence import CAPTURE_SUFFIX
from edullm_platform.phase1_evidence import OidcSessionEvidence
from edullm_platform.phase2_evidence import AdmissionExecution

#: The reader's own name for the file this tool writes. Imported rather than restated,
#: because the writer spelled "compute-environment.sanitized.json" as a literal in two
#: places and the reader looked for it through a constant -- three spellings of one
#: filename, agreeing today. A capture written under a name the reader does not look for is
#: an absent record, and an absent record reads as a run that never happened.
from edullm_platform.phase3_capture import COMPUTE_ENVIRONMENT_RECORD
from edullm_platform.phase3_evidence import (
    EVERY_BATCH_JOB_STATUS,
    BatchJobEvidence,
    ComputeEnvironmentEvidence,
    LogStreamEvidence,
    RefusedRunEvidence,
    RunLineageAttestation,
    group_opaque_identifier,
)
from edullm_platform.publisher_denials import assumed_role_identity
from edullm_platform.role_drift import (
    PHASE3_ROLE_TEMPLATES,
    PolicyNotComparableError,
    compare_role_to_template,
    load_template_roles,
    project_deployed_role,
    split_arn_fields,
)

__all__ = [
    "ALLOWED_OUTPUT_SUFFIX",
    "LINEAGE_BUCKET",
    "RECORD_CONTRACTS",
    "CaptureFailedError",
    "capture_roles",
    "capture_run",
    "main",
]

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]

ALLOWED_OUTPUT_SUFFIX: Final = Path("docs-frank/working/phase-3-evidence")

VPC_QUOTA_CODE: Final = "L-F678F1CE"
STANDARD_VCPU_QUOTA_CODE: Final = "L-1216C47A"
COMPUTE_ENVIRONMENTS_PER_QUEUE_QUOTA_CODE: Final = "L-0B0E4F5B"

SERVICE_LINKED_ROLES: Final = (
    "AWSServiceRoleForBatch",
    "AWSServiceRoleForEC2Spot",
    "AWSServiceRoleForECS",
    "AWSServiceRoleForAutoScaling",
)

#: The deployed names. Written here rather than derived, so a capture aimed at somebody
#: else's bucket or queue fails on the name instead of quietly recording their objects as
#: this project's lineage. Same rule, and the same reason, as the Phase 2 capture tool.
LINEAGE_BUCKET: Final = "sbsandbox-intern-edullm-lineage"
COMPUTE_ENVIRONMENT_NAME: Final = "sbsandbox-intern-edullm-cpu"
JOB_QUEUE_NAME: Final = "sbsandbox-intern-edullm-cpu"
STATE_MACHINE_NAME: Final = "sbsandbox-intern-edullm-admission"

#: How far before the submit instant to start a CloudTrail lookup. The session is issued
#: before the submission it makes, and GitHub's approval gate sits between the two, so the
#: window has to cover an approval somebody took their time over.
SESSION_LOOKUP_MARGIN: Final = timedelta(hours=12)

#: What an OIDC session lasts when CloudTrail did not record an expiry. Only used to fill
#: a field the contract requires; the assumption is visible here rather than buried.
DEFAULT_SESSION_DURATION: Final = timedelta(hours=1)

#: CloudTrail answers fifty events a page. A lookup needing more pages than this is not a
#: lookup that failed, it is one whose window is too wide to be answering about one run.
MAXIMUM_LOOKUP_PAGES: Final = 40

#: Which contract each lineage key is read through when deciding whether the stored object
#: still loads as the thing its key says it is. ``events`` is the only prefix holding more
#: than one object per run, which is why the mapping is by prefix rather than by file.
#
# Annotated rather than inferred. Left bare, mypy joins six unrelated model classes and
# lands on their shared metaclass, which has no ``model_validate`` -- so the one call this
# mapping exists to serve stops typechecking while reading perfectly.
RECORD_CONTRACTS: Final[dict[str, type[ContractModel]]] = {
    "intent": IntentRecord,
    "decision": DecisionRecord,
    "binding": BatchJobBinding,
    "events": LifecycleEvent,
    "attempt": SchedulerAttempt,
    "result": ResultManifest,
}

#: How many log events one capture will read back. A stream this platform writes holds a
#: handful of lines; the cap exists so a runaway container cannot turn a capture into a
#: megabyte of committed fixture, and ``truncated`` records when it bit.
MAXIMUM_LOG_LINES: Final = 200

CAPTURE_METHOD: Final = (
    "EC2 authorization is read with --dry-run against the real API, never from "
    "iam:SimulatePrincipalPolicy. DryRunOperation means the request would have succeeded; "
    "UnauthorizedOperation means it would not; a *LimitExceeded code means authorization "
    "passed and there is no room; anything else means the request never reached "
    "authorization. The simulator's OrganizationsDecisionDetail reported ten of these "
    "actions as denied in both regions when seven are authorized in us-east-1, which is "
    "why the method is recorded here rather than assumed."
)


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
        # Hyphenated, because AWS issues a request id as forty characters that
        # scan_for_secrets reads as a secret access key. See group_opaque_identifier.
        "increase_request_id": (
            None if latest is None else group_opaque_identifier(str(latest.get("Id", "")))
        ),
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
            (
                "sort_by(Subnets,&AvailabilityZone)[].{Id:SubnetId,Az:AvailabilityZone,"
                "Public:MapPublicIpOnLaunch,Free:AvailableIpAddressCount}"
            ),
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


# --------------------------------------------------------------------------------------
# The four roles Phase 3 creates, compared to the templates that declare them
# --------------------------------------------------------------------------------------


def account_and_partition(*, profile: str, region: str) -> tuple[str, str]:
    """The account this is running against, and its partition.

    Neither is ever written to a file. The account ID is what tells a captured ARN naming
    *this* account from one naming another, and the partition is what the drift
    comparison is allowed to fold. The partition is read here rather than by
    :func:`~edullm_platform.capture_tooling.account_identity`, because which spellings of
    a partition may be folded together is the drift comparison's question.
    """
    identity = account_identity(profile=profile, region=region)
    fields = split_arn_fields(identity.arn)
    if fields is None:
        raise CaptureFailedError("caller_identity_unreadable")
    return identity.account_id, fields[1]


def capture_role(role_name: str, *, profile: str, region: str, account_id: str,
                 observed_at: datetime) -> Any:
    role = aws_json(["iam", "get-role", "--role-name", role_name], profile=profile,
                    region=region)["Role"]
    listed = aws_json(["iam", "list-role-policies", "--role-name", role_name], profile=profile,
                      region=region)
    inline_documents = [
        aws_json(
            ["iam", "get-role-policy", "--role-name", role_name, "--policy-name", policy_name],
            profile=profile,
            region=region,
        )
        for policy_name in listed.get("PolicyNames", [])
    ]
    attached = aws_json(
        ["iam", "list-attached-role-policies", "--role-name", role_name],
        profile=profile,
        region=region,
    )
    return project_deployed_role(
        role,
        inline_documents,
        attached.get("AttachedPolicies", []),
        own_account=account_id,
        environment="sandbox",
        observed_at=observed_at,
    )


def capture_roles(*, profile: str, region: str, observed_at: datetime) -> list[tuple[str, Any]]:
    """Each Phase 3 role, followed by how it compares to the template that declares it.

    Mirrors ``tools/capture_phase1_evidence.py``'s roles target, including its output
    layout, because the two produce the same kind of record and a reader comparing a
    Phase 1 role to a Phase 3 one should not have to learn a second shape. What differs is
    only which registry is walked: ``PHASE3_ROLE_TEMPLATES`` rather than
    ``COMMITTED_ROLE_TEMPLATES``, so a Phase 3 role drifting cannot fail a Phase 1 capture.
    """
    account_id, partition = account_and_partition(profile=profile, region=region)
    records: list[tuple[str, Any]] = []
    for role_name, relative_path in PHASE3_ROLE_TEMPLATES:
        evidence = capture_role(
            role_name,
            profile=profile,
            region=region,
            account_id=account_id,
            observed_at=observed_at,
        )
        try:
            declared = [
                role
                for role in load_template_roles(PROJECT_ROOT / relative_path)
                if role.role_name == role_name
            ]
        except PolicyNotComparableError as exc:
            raise CaptureFailedError(f"template_unreadable:{relative_path}") from exc
        if len(declared) != 1:
            raise CaptureFailedError(f"template_does_not_declare_the_role:{relative_path}")
        report = compare_role_to_template(
            evidence,
            declared[0],
            template_path=relative_path,
            partition=partition,
            region=region,
        )
        records.append((f"sanitized/roles/{role_name}.sanitized.json", evidence))
        records.append((f"drift/{role_name}.json", report))
    return records


# --------------------------------------------------------------------------------------
# What one live run left behind
# --------------------------------------------------------------------------------------


def epoch_millis_to_instant(value: object) -> str | None:
    """One Batch timestamp, as an instant the contracts accept, or ``None`` if absent.

    Batch reports epoch milliseconds and omits the field entirely for a job that never
    reached the state in question. Absent has to stay absent: a job that never started
    has no start time, and substituting the submit time would make a job that never
    placed read like one that ran instantly.
    """
    if not isinstance(value, int):
        return None
    return (
        datetime.fromtimestamp(value / 1000, tz=UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def name_from_arn(arn: object) -> str:
    """The resource name at the end of an ARN, with any revision suffix kept.

    Names rather than ARNs throughout the committed records, for the reason
    ``ExecutionTargetBinding`` gives: an ARN carries the account id, and committing one
    puts the account into reviewed configuration that every reader then has to redact.
    """
    text = str(arn)
    return text.rsplit("/", 1)[-1] if "/" in text else text


def capture_batch_job(
    run_id: str, *, batch_job_id: str, profile: str, region: str, observed_at: datetime
) -> BatchJobEvidence:
    """One Batch job as the service describes it, looked up by the id the binding recorded.

    Looked up by id rather than searched for by name. ``ListJobs`` is filtered by queue and
    status and would find a job whose name happens to match in a shared account; the
    binding is the platform's own record of which job it submitted, so following it is the
    join the criterion is actually about.
    """
    described = aws_json(
        ["batch", "describe-jobs", "--jobs", batch_job_id], profile=profile, region=region
    ).get("jobs", [])
    if len(described) != 1:
        raise CaptureFailedError(f"batch_job_not_found:{run_id}")
    job = described[0]
    container = job.get("container") or {}
    status_reason = job.get("statusReason")
    return BatchJobEvidence.model_validate(
        {
            "observed_at": observed_at,
            "source": "aws",
            "environment": "sandbox",
            "region": region,
            "run_id": run_id,
            "batch_job_id": str(job["jobId"]),
            "batch_job_name": str(job["jobName"]),
            "status": str(job["status"]),
            "status_reason": None if status_reason is None else str(status_reason),
            "container_exit_code": container.get("exitCode"),
            "log_stream_name": container.get("logStreamName"),
            "job_queue_name": name_from_arn(job["jobQueue"]),
            "job_definition_name": name_from_arn(job["jobDefinition"]),
            "started_at": epoch_millis_to_instant(job.get("startedAt")),
            "stopped_at": epoch_millis_to_instant(job.get("stoppedAt")),
            "attempt_count": len(job.get("attempts") or []),
        }
    )


def capture_log_stream(
    run_id: str,
    *,
    log_group: str,
    log_stream: str,
    profile: str,
    region: str,
    observed_at: datetime,
) -> LogStreamEvidence:
    """The lines the container printed, read back out of the stream the job recorded.

    Read from the head, because the criterion is that the output is *there* rather than
    that the tail is. A stream that resolves and returns nothing is captured as an empty
    line list rather than as a failure: an empty stream is a true observation about a
    container that printed nothing, and refusing it here would hide the case.
    """
    answer = aws_json(
        [
            "logs",
            "get-log-events",
            "--log-group-name",
            log_group,
            "--log-stream-name",
            log_stream,
            "--start-from-head",
            "--limit",
            str(MAXIMUM_LOG_LINES + 1),
        ],
        profile=profile,
        region=region,
    )
    events = answer.get("events") or []
    lines = [str(event.get("message", "")).rstrip("\n") for event in events]
    return LogStreamEvidence.model_validate(
        {
            "observed_at": observed_at,
            "source": "aws",
            "environment": "sandbox",
            "region": region,
            "run_id": run_id,
            "log_group_name": log_group,
            "log_stream_name": log_stream,
            "lines": lines[:MAXIMUM_LOG_LINES],
            "truncated": len(lines) > MAXIMUM_LOG_LINES,
        }
    )


def lineage_keys_for(run_id: str, *, profile: str, region: str) -> tuple[str, ...]:
    """Every lineage key belonging to one run, found by asking for that run's keys.

    Listed per prefix rather than by filtering a listing of the whole bucket, so another
    run's object cannot arrive in this record through a prefix collision, and so a store
    that grows does not make the capture slower.
    """
    keys: list[str] = []
    for prefix in RECORD_CONTRACTS:
        # events and attempt are written one directory per run; the other four are a
        # single object whose key is the run id. Asking for the run-scoped prefix covers
        # both shapes without the capture needing to know which is which.
        listing = aws_json(
            [
                "s3api",
                "list-objects-v2",
                "--bucket",
                LINEAGE_BUCKET,
                "--prefix",
                f"{prefix}/{run_id}",
                "--query",
                "Contents[].Key",
            ],
            profile=profile,
            region=region,
        )
        keys.extend(str(key) for key in (listing or []))
    return tuple(sorted(keys))


def read_lineage_object(
    key: str, *, profile: str, region: str
) -> tuple[bytes, Mapping[str, Any]]:
    """One object's bytes and what S3 attests about them.

    Downloaded to a file rather than to stdout, for the reason the Phase 2 capture
    records: ``get-object`` writes its metadata response to stdout beside the body, so
    reading the pipe compares the object against a document containing a summary of
    itself.
    """
    head = aws_json(
        [
            "s3api",
            "head-object",
            "--bucket",
            LINEAGE_BUCKET,
            "--key",
            key,
            "--checksum-mode",
            "ENABLED",
        ],
        profile=profile,
        region=region,
    )
    with tempfile.TemporaryDirectory() as directory:
        downloaded = Path(directory) / "object"
        aws_json(
            [
                "s3api",
                "get-object",
                "--bucket",
                LINEAGE_BUCKET,
                "--key",
                key,
                str(downloaded),
            ],
            profile=profile,
            region=region,
        )
        return downloaded.read_bytes(), head


def is_canonical_json(body: bytes) -> bool:
    """Whether these bytes are exactly the canonical serialization of the record they hold."""
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return False
    if not isinstance(parsed, dict):
        return False
    canonical = json.dumps(
        parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return body == canonical


def loads_as_its_contract(key: str, body: bytes) -> bool:
    """Whether the stored object still loads as the thing its key says it is.

    Judged on the bytes S3 holds, before any redaction, because the ARNs a binding carries
    are part of what its contract checks. A capture that judged the redacted copy would
    report every binding as broken and the distinction this field exists to draw -- three
    corrupt records among four -- would be lost.
    """
    contract = RECORD_CONTRACTS[key.split("/", 1)[0]]
    try:
        contract.model_validate(json.loads(body))
    except (ValidationError, ValueError, UnicodeDecodeError):
        return False
    return True


def capture_run_lineage(
    run_id: str, *, profile: str, region: str, observed_at: datetime
) -> tuple[RunLineageAttestation, dict[str, bytes]]:
    """Every lineage object one run wrote, with S3's attestation and the bodies themselves.

    The attestation says what the store holds; only the body says what the platform
    decided. Criteria about record content have to read one, so both are returned and both
    are written.
    """
    keys = lineage_keys_for(run_id, profile=profile, region=region)
    if not keys:
        raise CaptureFailedError(f"run_has_no_lineage:{run_id}")
    attestations: list[Mapping[str, Any]] = []
    bodies: dict[str, bytes] = {}
    for key in keys:
        body, head = read_lineage_object(key, profile=profile, region=region)
        bodies[key] = body
        attestations.append(
            {
                "key": key,
                "record_kind": key.split("/", 1)[0],
                "version_id": str(head["VersionId"]),
                "checksum_sha256": str(head["ChecksumSHA256"]),
                "content_length": int(head["ContentLength"]),
                "canonical": is_canonical_json(body),
                "loads_as_contract": loads_as_its_contract(key, body),
            }
        )
    return (
        RunLineageAttestation.model_validate(
            {
                "observed_at": observed_at,
                "source": "aws",
                "environment": "sandbox",
                "run_id": run_id,
                "bucket": LINEAGE_BUCKET,
                "objects": attestations,
            }
        ),
        bodies,
    )


def capture_compute_environment(
    *, profile: str, region: str, observed_at: datetime
) -> ComputeEnvironmentEvidence:
    """The deployed compute environment, its capacity now, and the networking it landed on.

    ``desiredvCpus`` is read at whatever instant the capture runs, and that is the point:
    the criterion is that the environment holds nothing when nothing is running, so a
    capture taken while a job is in flight records a non-zero figure and says so rather
    than waiting for a number somebody would prefer.
    """
    described = aws_json(
        [
            "batch",
            "describe-compute-environments",
            "--compute-environments",
            COMPUTE_ENVIRONMENT_NAME,
        ],
        profile=profile,
        region=region,
    ).get("computeEnvironments", [])
    if len(described) != 1:
        raise CaptureFailedError(f"compute_environment_not_found:{COMPUTE_ENVIRONMENT_NAME}")
    found = described[0]
    resources = found.get("computeResources") or {}
    subnet_ids = sorted(str(subnet) for subnet in resources.get("subnets") or [])
    if not subnet_ids:
        raise CaptureFailedError("compute_environment_has_no_subnets")
    vpc = aws_json(
        [
            "ec2",
            "describe-subnets",
            "--subnet-ids",
            *subnet_ids,
            "--query",
            "Subnets[].VpcId",
        ],
        profile=profile,
        region=region,
    )
    distinct_vpcs = sorted({str(item) for item in vpc})
    if len(distinct_vpcs) != 1:
        # An environment spanning two VPCs is not a placement this platform can describe,
        # and recording the first one would be a record that reads as complete.
        raise CaptureFailedError(f"compute_environment_spans_vpcs:{len(distinct_vpcs)}")
    queues = aws_json(
        [
            "batch",
            "describe-job-queues",
            "--query",
            "jobQueues[].{name:jobQueueName,order:computeEnvironmentOrder}",
        ],
        profile=profile,
        region=region,
    )
    attached = sorted(
        str(queue["name"])
        for queue in queues
        if any(
            name_from_arn(entry.get("computeEnvironment")) == COMPUTE_ENVIRONMENT_NAME
            for entry in queue.get("order") or []
        )
    )
    # WHAT THE ACCOUNT IS ACTUALLY PAYING FOR, WHICH NEITHER OF THE OTHER TWO NUMBERS SAYS.
    #
    # Batch puts the instances it starts into an auto scaling group named
    # AWSBatch-<compute environment>-asg-<uuid>, and AWS assigns that tag rather than this
    # project, so an instance somebody launched by hand cannot land in the count and one
    # Batch started cannot escape it.
    #
    # The states are every state that bills or is about to stop billing. `terminated` is
    # excluded and `stopped` is not: a stopped instance holds its EBS volume, and an
    # environment sitting on stopped hosts is not one holding nothing.
    instances = aws_json(
        [
            "ec2",
            "describe-instances",
            "--filters",
            f"Name=tag:aws:autoscaling:groupName,Values=AWSBatch-{COMPUTE_ENVIRONMENT_NAME}-asg-*",
            "Name=instance-state-name,Values=pending,running,shutting-down,stopping,stopped",
            "--query",
            "length(Reservations[].Instances[])",
        ],
        profile=profile,
        region=region,
    )
    return ComputeEnvironmentEvidence.model_validate(
        {
            "observed_at": observed_at,
            "source": "aws",
            "environment": "sandbox",
            "region": region,
            "compute_environment_name": str(found["computeEnvironmentName"]),
            "status": str(found["status"]),
            "state": str(found["state"]),
            "live_instance_count": int(instances),
            "desired_vcpus": int(resources.get("desiredvCpus", 0)),
            "minimum_vcpus": int(resources.get("minvCpus", 0)),
            "maximum_vcpus": int(resources.get("maxvCpus", 0)),
            "vpc_id": distinct_vpcs[0],
            "subnet_ids": subnet_ids,
            "security_group_ids": sorted(
                str(group) for group in resources.get("securityGroupIds") or []
            ),
            "instance_types": sorted(
                str(shape) for shape in resources.get("instanceTypes") or []
            ),
            "job_queue_names": attached,
        }
    )


def binding_exists(run_id: str, *, profile: str, region: str) -> bool:
    """Whether this run ever got as far as being submitted to Batch.

    The binding is written immediately after the submit succeeds and before anything
    starts, so its absence is what separates a run that was refused from one that ran. The
    two need entirely different captures -- one has a container and a log stream, the
    other has to establish that neither exists -- so this is the fork.
    """
    listing = aws_json(
        [
            "s3api",
            "list-objects-v2",
            "--bucket",
            LINEAGE_BUCKET,
            "--prefix",
            f"binding/{run_id}",
            "--query",
            "Contents[].Key",
        ],
        profile=profile,
        region=region,
    )
    return bool(listing)


def batch_jobs_named(run_id: str, *, profile: str, region: str) -> tuple[list[str], list[str]]:
    """Every Batch job on the queue carrying this run id as its name, and where we looked.

    Searched across every status rather than the terminal ones. A job refused at admission
    should not exist at all, and a search that skipped ``RUNNABLE`` would miss precisely
    the case where the refusal failed to prevent a submission -- a job sitting in the queue
    waiting for capacity.
    """
    found: list[str] = []
    searched: list[str] = []
    for status in EVERY_BATCH_JOB_STATUS:
        summaries = aws_json(
            [
                "batch",
                "list-jobs",
                "--job-queue",
                JOB_QUEUE_NAME,
                "--job-status",
                status,
                "--query",
                f"jobSummaryList[?jobName=='{run_id}'].jobId",
            ],
            profile=profile,
            region=region,
        )
        searched.append(status)
        found.extend(str(job_id) for job_id in (summaries or []))
    return sorted(found), searched


def capture_refused_run(
    run_id: str,
    *,
    profile: str,
    region: str,
    account_id: str,
    decision: Mapping[str, Any],
    observed_at: datetime,
) -> RefusedRunEvidence:
    """What a refusal left behind: the decision, the failed execution, and no job anywhere."""
    execution = capture_admission_execution(
        run_id,
        profile=profile,
        region=region,
        account_id=account_id,
        observed_at=observed_at,
    )
    found, searched = batch_jobs_named(run_id, profile=profile, region=region)
    return RefusedRunEvidence.model_validate(
        {
            "observed_at": observed_at,
            "source": "aws",
            "environment": "sandbox",
            "region": region,
            "run_id": run_id,
            "decision_accepted": bool(decision.get("accepted")),
            "decision_reason": str(decision.get("reason")),
            "decision_detail": str(decision.get("detail")),
            "execution_status": execution.status,
            "execution_error": execution.error,
            "matching_batch_job_ids": found,
            "searched_job_statuses": searched,
        }
    )


def decision_body_for(run_id: str, *, profile: str, region: str) -> Mapping[str, Any]:
    """This run's admission decision, as JSON, whatever shape it was stored in."""
    body, _head = read_lineage_object(
        f"decision/{run_id}.json", profile=profile, region=region
    )
    parsed = json.loads(body)
    if isinstance(parsed, str):
        parsed = json.loads(parsed)
    if not isinstance(parsed, dict):
        raise CaptureFailedError(f"decision_unreadable:{run_id}")
    return parsed


def binding_body_for(run_id: str, *, profile: str, region: str) -> Mapping[str, Any]:
    """The binding as JSON, whatever state it is in, for the one field the capture needs.

    Read as a mapping rather than through ``BatchJobBinding`` on purpose. Three bindings
    written before the ASL fix do not load as the contract, and the Batch job id inside
    them is still correct -- refusing to read it would make the corrupt runs uncapturable
    rather than captured-and-recorded-as-corrupt.
    """
    body, _head = read_lineage_object(
        f"binding/{run_id}.json", profile=profile, region=region
    )
    try:
        parsed = json.loads(body)
    except ValueError as exc:
        raise CaptureFailedError(f"binding_unreadable:{run_id}") from exc
    if isinstance(parsed, str):
        # Records written before the encoding fix are a JSON string holding the document.
        try:
            parsed = json.loads(parsed)
        except ValueError as exc:
            raise CaptureFailedError(f"binding_unreadable:{run_id}") from exc
    if not isinstance(parsed, dict) or "batch_job_id" not in parsed:
        raise CaptureFailedError(f"binding_names_no_job:{run_id}")
    return parsed


def capture_admission_execution(
    run_id: str, *, profile: str, region: str, account_id: str, observed_at: datetime
) -> AdmissionExecution:
    """The admission execution for this run, found by name because the name is the run id.

    Described by ARN rather than found by listing. ``ListExecutions`` pages, and a store
    that grows would eventually push an older run off the first page and report it as
    absent -- which is the same answer this gives for a run that never reached admission.
    """
    described = aws_json(
        [
            "stepfunctions",
            "describe-execution",
            "--execution-arn",
            f"arn:aws:states:{region}:{account_id}:execution:{STATE_MACHINE_NAME}:{run_id}",
        ],
        profile=profile,
        region=region,
    )
    return AdmissionExecution.model_validate(
        {
            "observed_at": observed_at,
            "name": str(described["name"]),
            "status": str(described["status"]),
            "error": described.get("error") or None,
        }
    )


def cloudtrail_events(
    event_name: str, *, profile: str, region: str, since: datetime
) -> tuple[Mapping[str, Any], ...]:
    """Every CloudTrail record of one operation since an instant, across every page.

    Paginated because this is a shared account: CloudTrail answers fifty events at a time
    in reverse time order, and another team's activity means the page holding one run's
    call is not page one. A reader that took the first page would report the event as
    absent, which is the answer it also gives when the call never happened.
    """
    records: list[Mapping[str, Any]] = []
    next_token: str | None = None
    for _page in range(MAXIMUM_LOOKUP_PAGES):
        arguments = [
            "cloudtrail",
            "lookup-events",
            "--lookup-attributes",
            f"AttributeKey=EventName,AttributeValue={event_name}",
            "--start-time",
            since.isoformat(),
        ]
        if next_token is not None:
            arguments += ["--next-token", next_token]
        answer = aws_json(arguments, profile=profile, region=region)
        for event in answer.get("Events") or []:
            try:
                parsed = json.loads(str(event.get("CloudTrailEvent")))
            except ValueError as exc:
                raise CaptureFailedError(f"cloudtrail_event_unreadable:{event_name}") from exc
            if isinstance(parsed, dict):
                records.append(parsed)
        token = answer.get("NextToken")
        if not isinstance(token, str) or not token:
            return tuple(records)
        next_token = token
    raise CaptureFailedError(f"cloudtrail_lookup_too_long:{event_name}")


def capture_oidc_session(
    run_id: str, *, profile: str, region: str, submitted_at: datetime, observed_at: datetime
) -> OidcSessionEvidence:
    """The GitHub session that started this run's admission execution.

    Joined through the ``StartExecution`` call rather than by taking the most recent
    session, and the difference matters: every submission assumes the same role, so "the
    latest session" names whichever run happened last. The ``StartExecution`` event names
    this run's execution and carries the creation instant of the session that made it, and
    exactly one ``AssumeRoleWithWebIdentity`` has that instant. That is the same join the
    Phase 1 capture uses to tie a push to the session that pushed it.
    """
    window = submitted_at - SESSION_LOOKUP_MARGIN
    starts = [
        record
        for record in cloudtrail_events(
            "StartExecution", profile=profile, region=region, since=window
        )
        if record.get("errorCode") is None
        and isinstance(record.get("requestParameters"), dict)
        and str(record["requestParameters"].get("name")) == run_id
    ]
    if len(starts) != 1:
        raise CaptureFailedError(f"start_execution_event_not_found:{run_id}")
    identity = starts[0].get("userIdentity") or {}
    attributes = (identity.get("sessionContext") or {}).get("attributes") or {}
    started_under = attributes.get("creationDate")
    if not isinstance(started_under, str):
        raise CaptureFailedError(f"start_execution_names_no_session:{run_id}")
    issued_at = datetime.fromisoformat(started_under)
    sessions = [
        record
        for record in cloudtrail_events(
            "AssumeRoleWithWebIdentity", profile=profile, region=region, since=window
        )
        if datetime.fromisoformat(str(record.get("eventTime"))) == issued_at
    ]
    if len(sessions) != 1:
        raise CaptureFailedError(f"session_for_the_execution_not_found:{run_id}")
    return project_oidc_session(sessions[0], region=region, observed_at=observed_at)


def split_web_identity_principal(identity: Mapping[str, Any]) -> tuple[str, str, str]:
    """The issuer, audience and subject of a web identity, taken apart rather than guessed.

    CloudTrail spells the principal as ``{provider arn}:{audience}:{subject}`` and repeats
    the subject in ``userName``. Both are read and required to agree: the split is
    positional, so a subject containing a colon -- which every GitHub subject does --
    would be silently truncated by a parser that split on the wrong one, and the
    cross-check is what turns that from a wrong answer into a failed capture.

    The subject is the field this exists for. It carries
    ``:environment:run-approval-lead``, which is what says the session was issued to a job
    that had passed the approval gate rather than to any workflow in the repository.
    """
    provider = str(identity.get("identityProvider", ""))
    principal = str(identity.get("principalId", ""))
    reported_subject = str(identity.get("userName", ""))
    prefix = f"{provider}:"
    if not provider or not principal.startswith(prefix):
        raise CaptureFailedError("session_principal_unreadable")
    audience, separator, subject = principal[len(prefix) :].partition(":")
    if not separator or not subject:
        raise CaptureFailedError("session_principal_unreadable")
    if subject != reported_subject:
        raise CaptureFailedError("session_subject_disagrees_with_principal")
    return provider.rsplit("/", 1)[-1], audience, subject


def project_oidc_session(
    record: Mapping[str, Any], *, region: str, observed_at: datetime
) -> OidcSessionEvidence:
    """One CloudTrail session record, projected onto the contract. Credentials never leave here."""
    response = record.get("responseElements")
    if not isinstance(response, dict):
        raise CaptureFailedError("session_response_unreadable")
    assumed = response.get("assumedRoleUser")
    if not isinstance(assumed, dict):
        raise CaptureFailedError("session_response_unreadable")
    try:
        role_name, session_name = assumed_role_identity(str(assumed.get("arn")))
    except ValueError as exc:
        raise CaptureFailedError("session_caller_is_not_an_assumed_role") from exc
    issuer, audience, subject = split_web_identity_principal(
        record.get("userIdentity") or {}
    )
    issued_at = datetime.fromisoformat(str(record.get("eventTime")))
    expiration = (response.get("credentials") or {}).get("expiration")
    requested = (record.get("requestParameters") or {}).get("durationSeconds")
    if isinstance(expiration, str):
        expires_at = expiration
    else:
        # CloudTrail records the requested duration even where it omits the expiry, so the
        # window comes from what the caller asked for rather than from a house default.
        held_for = (
            timedelta(seconds=requested)
            if isinstance(requested, int)
            else DEFAULT_SESSION_DURATION
        )
        expires_at = (issued_at + held_for).isoformat().replace("+00:00", "Z")
    return OidcSessionEvidence.model_validate(
        {
            "observed_at": observed_at,
            "source": "aws",
            "environment": "sandbox",
            "status": "ok",
            "region": region,
            "event_id": record.get("eventID"),
            "event_name": record.get("eventName"),
            "event_source": record.get("eventSource"),
            "role_name": role_name,
            "session_name": session_name,
            "oidc_issuer": issuer,
            "oidc_audience": audience,
            "oidc_subject": subject,
            "assumed_at": issued_at.isoformat().replace("+00:00", "Z"),
            "expires_at": expires_at,
        }
    )


def capture_run(
    run_id: str, *, profile: str, region: str, observed_at: datetime
) -> tuple[list[tuple[str, Any]], dict[str, bytes]]:
    """Everything one run left behind, as records and as the lineage bodies themselves.

    Two shapes, forked on whether a binding exists. A run that was submitted has a job, a
    stream and a full lineage chain; a run that admission refused has an intent, a
    decision, and the obligation to establish that no job was started. Handling both here
    rather than in two targets is deliberate: which shape a run turned out to be is
    something the account answers, not something the operator should have to know before
    asking.
    """
    account_id = account_identity(profile=profile, region=region).account_id
    attestation, bodies = capture_run_lineage(
        run_id, profile=profile, region=region, observed_at=observed_at
    )
    if not binding_exists(run_id, profile=profile, region=region):
        refusal = capture_refused_run(
            run_id,
            profile=profile,
            region=region,
            account_id=account_id,
            decision=decision_body_for(run_id, profile=profile, region=region),
            observed_at=observed_at,
        )
        return (
            [
                (f"runs/{run_id}/refusal.sanitized.json", refusal),
                (f"runs/{run_id}/lineage-attestation.sanitized.json", attestation),
                (
                    f"runs/{run_id}/admission-execution.sanitized.json",
                    capture_admission_execution(
                        run_id,
                        profile=profile,
                        region=region,
                        account_id=account_id,
                        observed_at=observed_at,
                    ),
                ),
            ],
            bodies,
        )
    binding = binding_body_for(run_id, profile=profile, region=region)
    job = capture_batch_job(
        run_id,
        batch_job_id=str(binding["batch_job_id"]),
        profile=profile,
        region=region,
        observed_at=observed_at,
    )
    submitted_at = datetime.fromisoformat(str(binding["submitted_at"]))
    records: list[tuple[str, Any]] = [
        (f"runs/{run_id}/batch-job.sanitized.json", job),
        (f"runs/{run_id}/lineage-attestation.sanitized.json", attestation),
        (
            f"runs/{run_id}/admission-execution.sanitized.json",
            capture_admission_execution(
                run_id,
                profile=profile,
                region=region,
                account_id=account_id,
                observed_at=observed_at,
            ),
        ),
        (
            f"runs/{run_id}/oidc-session.sanitized.json",
            capture_oidc_session(
                run_id,
                profile=profile,
                region=region,
                submitted_at=submitted_at,
                observed_at=observed_at,
            ),
        ),
    ]
    if job.log_stream_name is not None:
        records.append(
            (
                f"runs/{run_id}/log-stream.sanitized.json",
                capture_log_stream(
                    run_id,
                    log_group=str(binding["log_group"]),
                    log_stream=job.log_stream_name,
                    profile=profile,
                    region=region,
                    observed_at=observed_at,
                ),
            )
        )
    return records, bodies


#: What the account target needs and the roles target does not. Checked after parsing
#: rather than by ``required=True``, so a capture of the roles alone does not have to name
#: a VPC that has nothing to do with them -- the same split the Phase 1 tool draws between
#: its standing-fact and run targets.
ACCOUNT_TARGET_ARGUMENTS: Final = ("home_vpc", "home_subnet", "second_vpc", "second_subnet")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture the account facts Phase 3 rests on. Read-only."
    )
    parser.add_argument("--aws-profile", required=True)
    parser.add_argument(
        "--target",
        choices=["account", "roles", "run", "compute-environment"],
        required=True,
    )
    parser.add_argument(
        "--run-id",
        action="append",
        default=[],
        help=(
            "repeatable; required by --target run. Named rather than discovered, because "
            "the three runs written before the ASL fix carry bindings that will never load "
            "and choosing what to capture is a judgement somebody makes in writing."
        ),
    )
    parser.add_argument("--home-region", default="us-east-1")
    parser.add_argument("--second-region", default="us-east-2")
    parser.add_argument("--home-vpc")
    parser.add_argument("--home-subnet")
    parser.add_argument("--second-vpc")
    parser.add_argument("--second-subnet")
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


def capture_roles_target(arguments: argparse.Namespace) -> int:
    """The roles half of the CLI, which reports drift the way the Phase 1 tool does.

    Exit 0 when every role matches its template, 1 when one does not. A drifting role is a
    finding rather than a broken capture, so the records are written either way -- what
    drifted is the account, and reading it is how anybody finds out.
    """
    observed_at = observed_now()
    records = capture_roles(
        profile=arguments.aws_profile,
        region=arguments.home_region,
        observed_at=observed_at,
    )
    written: list[str] = []
    for relative_name, record in records:
        path = arguments.output_dir / relative_name
        write_model(path, record)
        written.append(path.name)
    drift_reports = [record for _name, record in records if hasattr(record, "findings")]
    findings = sum(len(one.findings) for one in drift_reports)
    report(
        {
            "targets": ["roles"],
            "written": sorted(written),
            "roles_compared": len(drift_reports),
            "drift_findings": findings,
            "verdict": "role_drift" if findings else "ok",
        }
    )
    for drift_report in drift_reports:
        for finding in drift_report.findings:
            print(
                f"role_drift:{drift_report.role_name}:{finding.direction.value}:"
                f"{finding.element}",
                file=sys.stderr,
            )
    return 1 if findings else 0


def capture_compute_environment_target(arguments: argparse.Namespace) -> int:
    """The compute environment on its own, with no run to hang it off.

    THIS EXISTS BECAUSE THE IDLE CLAIM CANNOT BE CAPTURED WHILE CAPTURING A RUN. The
    environment record is a standing fact and was only ever produced as a side effect of
    ``--target run``, which needs a run id, reads that run's logs, and makes a CloudTrail
    lookup that is slow and rate limited. So the one record whose whole content is "nothing
    is running here" could only be taken by a command about something that ran.

    That is not merely awkward. The criterion wants the environment observed when it is
    quiet, and after the first GPU run the quiet moment arrived about fifteen minutes after
    the job finished -- long after any reason to be capturing that run. Needing a run id to
    take it is how a capture ends up taken at the wrong instant.

    Exit 0 when the environment was read, 2 when it could not be.
    """
    observed_at = observed_now()
    environment = capture_compute_environment(
        profile=arguments.aws_profile,
        region=arguments.home_region,
        observed_at=observed_at,
    )
    path = arguments.output_dir / f"{COMPUTE_ENVIRONMENT_RECORD}{CAPTURE_SUFFIX}"
    write_model(path, environment)
    report(
        {
            "targets": ["compute-environment"],
            "written": [path.name],
            "compute_environment": environment.compute_environment_name,
            "desired_vcpus": environment.desired_vcpus,
            "live_instance_count": environment.live_instance_count,
            # Reported rather than enforced. A capture taken while the environment is
            # busy is a true record of a busy environment, and refusing to write it
            # would leave the only way to record that state unavailable. What must not
            # happen is committing it as evidence for the idle criterion, which is why
            # the verdict says which one this is.
            "verdict": "idle" if environment.idle_and_holding_nothing else "holding",
        }
    )
    return 0


def capture_run_target(arguments: argparse.Namespace) -> int:
    """The run half of the CLI: what named runs left behind, plus the environment they ran on.

    Exit 0 when every named run was captured, 2 when one could not be. A run whose records
    are captured and turn out to be corrupt is a successful capture -- the corruption is
    the finding, recorded in ``loads_as_contract`` -- so it does not fail here. What fails
    is being unable to look.
    """
    if not arguments.run_id:
        raise CaptureFailedError("run_target_needs:--run-id")
    observed_at = observed_now()
    written: list[str] = []
    unloadable: list[str] = []
    for run_id in arguments.run_id:
        records, bodies = capture_run(
            run_id,
            profile=arguments.aws_profile,
            region=arguments.home_region,
            observed_at=observed_at,
        )
        for relative_name, record in records:
            path = arguments.output_dir / relative_name
            write_model(path, record)
            written.append(relative_name)
        attestation = next(
            record for name, record in records if name.endswith("lineage-attestation.sanitized.json")
        )
        refused = {record.key for record in attestation.unloadable}
        unloadable.extend(sorted(refused))
        for key, body in bodies.items():
            # A body that does not load as its contract is recorded in the attestation and
            # not committed. The three bindings written before the ASL fix are 26KB of
            # admission payload in the field where a fan-out size belongs, carrying the
            # approver's name and the whole image scan; committing them would publish a
            # person and a CVE dump to establish something the attestation already says.
            # The object is not hidden -- its key, its checksum, its version and the fact
            # that it does not load are all in the record beside this.
            if key in refused:
                continue
            path = arguments.output_dir / "runs" / run_id / "records" / key
            write_sanitized_text(path, body.decode("utf-8"))
            written.append(f"runs/{run_id}/records/{key}")

    environment = capture_compute_environment(
        profile=arguments.aws_profile,
        region=arguments.home_region,
        observed_at=observed_at,
    )
    environment_record = f"{COMPUTE_ENVIRONMENT_RECORD}{CAPTURE_SUFFIX}"
    write_model(arguments.output_dir / environment_record, environment)
    written.append(environment_record)

    report(
        {
            "targets": ["run"],
            "runs": sorted(arguments.run_id),
            "written": sorted(written),
            "objects_that_do_not_load": sorted(unloadable),
            "bodies_withheld_because_they_do_not_load": len(unloadable),
            "compute_environment_desired_vcpus": environment.desired_vcpus,
            "verdict": "ok",
        }
    )
    # Printed rather than returned as a failure. A corrupt record is a fact about the
    # store, and the store is write-once, so there is no version of this capture that
    # would ever succeed if this were an error.
    for key in sorted(unloadable):
        print(f"object_does_not_load_as_its_contract:{key}", file=sys.stderr)
    return 0


def capture_account_target(arguments: argparse.Namespace) -> int:
    """The standing account facts, and whether the classifier still agrees with itself.

    Exit 2 for a disagreeing control rather than 0 with a warning. A control that
    disagrees means the classifier is wrong, which makes the whole authorization matrix
    untrustworthy rather than one row of it.
    """
    missing = [name for name in ACCOUNT_TARGET_ARGUMENTS if not getattr(arguments, name)]
    if missing:
        raise CaptureFailedError(f"account_target_needs:{','.join(sorted(missing))}")
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
        observed_at=observed_now(),
    )
    output = arguments.output_dir / "account-measurements.json"
    write_record(output, record)

    disagreeing = [entry for entry in record["controls"] if not entry["agrees"]]
    for entry in disagreeing:
        print(
            f"control_disagrees:{entry['action']}:{entry['region']}",
            file=sys.stderr,
        )
    print(f"wrote {output}")
    return 2 if disagreeing else 0


CAPTURE_TARGETS: Final[dict[str, Callable[[argparse.Namespace], int]]] = {
    "account": capture_account_target,
    "compute-environment": capture_compute_environment_target,
    "roles": capture_roles_target,
    "run": capture_run_target,
}


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    target = CAPTURE_TARGETS[arguments.target]
    return run_capture(
        lambda: target(arguments),
        output_dir=arguments.output_dir,
        allowed_suffix=ALLOWED_OUTPUT_SUFFIX,
    )


if __name__ == "__main__":
    sys.exit(main())
