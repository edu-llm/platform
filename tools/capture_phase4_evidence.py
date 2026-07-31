"""Capture what the GPU runs left behind. Read-only, and costs nothing but API calls.

**This is the fifth capture tool and it is not a fifth copy.** The standing rule is to
consolidate before one gets written, and the mechanics -- the CLI wrappers, the
working-directory refusal, the credential scan on every write, the exit-code mapping -- now
live in :mod:`edullm_platform.capture_tooling`. What is left here is the part that is
genuinely about Phase 4: which facts to gather, and how to read a GPU job's answer.

Six targets, and the split between them is by *what makes the record go stale* rather than
by convenience:

``run``
    A job that ran, the log it printed, the checkpoint it wrote and where its output went.
    None of it expires; the run happened.
``compute-environment``, ``offerings``, ``secret-delivery``, ``role-scope``
    Statements about how the account is configured, all of which are one console click from
    being false and therefore carry the freshness window.
``all``
    Every target, for the ordinary case.

**The checkpoint target runs the platform's own reader against live S3.** That is the point
of it: everything else in this evidence is the run describing itself, and a checkpoint that
cannot be resumed from is the one failure a run cannot detect about its own output.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from edullm_platform.capture_tooling import (
    CaptureFailedError,
    account_identity,
    aws_json,
    observed_now,
    report,
    run_capture,
    write_model,
)
from edullm_platform.checkpoints import inspect_checkpoint
from edullm_platform.contracts.results import OUTPUTS_BUCKET
from edullm_platform.evidence import (
    CAPTURE_SUFFIX,
    NON_ACCOUNT_SECRET_PATTERNS,
    redact_content_digests,
)
from edullm_platform.phase4_evidence import (
    GPU_RESOURCE_TYPE,
    WANDB_SECRET_VARIABLE,
    CheckpointObservation,
    GpuCapabilityEvidence,
    GpuComputeEnvironmentEvidence,
    GpuJobEvidence,
    InstanceTypeOffering,
    InstanceTypeOfferingEvidence,
    IsolationEvidence,
    OutputObject,
    OutputPrefixEvidence,
    ResumeEvidence,
    SecretDeliveryEvidence,
    TrainingSummaryEvidence,
    WorkloadRoleScopeEvidence,
)

ALLOWED_OUTPUT_SUFFIX: Final = Path("docs-frank/working/phase-4-evidence")

#: The deployed names. Written here rather than discovered, so a capture aimed at somebody
#: else's queue fails on the name instead of quietly recording their jobs as this project's
#: GPU evidence. The same rule, and the same reason, as the Phase 2 and Phase 3 tools.
GPU_QUEUE_NAME: Final = "sbsandbox-intern-edullm-gpu"
GPU_JOB_DEFINITION_NAME: Final = "sbsandbox-intern-edullm-gpu-run"
GPU_COMPUTE_ENVIRONMENT_NAME: Final = "sbsandbox-intern-edullm-gpu"
GPU_LOG_GROUP: Final = "/aws/batch/sbsandbox-intern-edullm-gpu"
GPU_WORKLOAD_ROLE: Final = "sbsandbox-intern-edullm-batch-gpu-workload"
LINEAGE_BUCKET: Final = "sbsandbox-intern-edullm-lineage"
GPU_INSTANCE_TYPE: Final = "g5.xlarge"

#: Where a run is permitted to write. One entry today because one team exists; the shape is
#: what makes cross-team isolation expressible at all.
AUTHORIZED_PREFIXES: Final = ("teams/platform/runs/",)

#: What Batch reports for a job that reached a terminal state. Anything else is a job still
#: going, and capturing one would record a window that has not closed.
TERMINAL_STATUSES: Final = ("SUCCEEDED", "FAILED")

#: How many log lines one capture reads. A stream this platform writes holds a handful; the
#: cap stops a runaway container turning a capture into a megabyte of committed fixture.
MAXIMUM_LOG_LINES: Final = 400

#: The metric names the training program logs. Read out of the log's own W&B summary rather
#: than asserted, but listed here so a capture that found none can say which it looked for.
EXPECTED_METRIC_KEYS: Final = ("train/ce_loss", "train/step")

#: What a freshly initialised olmo2_190M reported on its first step, measured on the run
#: that had nothing to resume from. Carried here so a resume record can say what the number
#: is being compared against rather than leaving it to a reader's memory: a model starting
#: below this loaded weights, and nothing else explains it.
COLD_START_FIRST_LOSS: Final = 11.009


def _epoch_millis(value: object) -> datetime | None:
    if not isinstance(value, int):
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC).replace(microsecond=0)


def _digest_from_image(image: str) -> str:
    _repository, _, digest = image.partition("@")
    if not digest.startswith("sha256:"):
        raise CaptureFailedError(f"job_image_is_not_digest_pinned:{image[:80]}")
    return digest


def capture_gpu_job(run_id: str, *, profile: str, region: str) -> GpuJobEvidence:
    """One GPU job, found by the run id it was named after.

    Found by listing the queue rather than by asking for a job id, because the run id is the
    only identifier a reader has and the mapping from one to the other lives in a lineage
    record that a refused run never gets.
    """
    observed_at = observed_now()
    found: str | None = None
    for status in TERMINAL_STATUSES:
        summaries = aws_json(
            [
                "batch", "list-jobs",
                "--job-queue", GPU_QUEUE_NAME,
                "--job-status", status,
                "--query", f"jobSummaryList[?jobName=='{run_id}'].jobId",
            ],
            profile=profile,
            region=region,
        )
        if summaries:
            found = str(summaries[0])
            break
    if found is None:
        raise CaptureFailedError(f"no_terminal_gpu_job_named:{run_id}")

    described = aws_json(
        ["batch", "describe-jobs", "--jobs", found, "--query", "jobs[0]"],
        profile=profile,
        region=region,
    )
    container = described.get("container") or {}
    requirements = {
        str(entry["type"]): int(entry["value"])
        for entry in container.get("resourceRequirements") or []
    }
    return GpuJobEvidence.model_validate(
        {
            "observed_at": observed_at,
            "source": "aws",
            "environment": "sandbox",
            "region": region,
            "run_id": run_id,
            "batch_job_id": found,
            "job_queue_name": GPU_QUEUE_NAME,
            "job_definition_name": str(described["jobDefinition"]).rsplit("/", 1)[-1],
            "status": str(described["status"]),
            "status_reason": described.get("statusReason") or None,
            "image_digest": _digest_from_image(str(container.get("image", ""))),
            "vcpus": requirements.get("VCPU", 0),
            "memory_mib": requirements.get("MEMORY", 0),
            "gpu_count": requirements.get(GPU_RESOURCE_TYPE, 0),
            "log_stream_name": container.get("logStreamName") or None,
            "started_at": _epoch_millis(described.get("startedAt")),
            "stopped_at": _epoch_millis(described.get("stoppedAt")),
            # A tuple, and sorted. The contract models are strict, so a list is refused
            # rather than coerced -- and the order Batch answers in is the order the job
            # definition happened to merge overrides in, which would make two captures of
            # one job differ for no reason a reader could act on.
            "container_environment": tuple(
                sorted(
                    (
                        {"name": str(entry["name"]), "value": str(entry["value"])}
                        for entry in container.get("environment") or []
                    ),
                    key=lambda entry: entry["name"],
                )
            ),
        }
    )


def _log_lines(log_stream: str, *, profile: str, region: str) -> list[str]:
    events = aws_json(
        [
            "logs", "get-log-events",
            "--log-group-name", GPU_LOG_GROUP,
            "--log-stream-name", log_stream,
            "--start-from-head",
            "--limit", str(MAXIMUM_LOG_LINES),
            "--query", "events[].message",
        ],
        profile=profile,
        region=region,
    )
    return [str(line) for line in events or []]


def _summary_object(lines: Sequence[str]) -> Mapping[str, Any] | None:
    """The JSON summary a program printed, reassembled from the log, or None if it printed none.

    CloudWatch splits a multi-line print into one event per line, so the object arrives as
    a run of lines starting at ``{`` and ending at the matching ``}``. Reassembled by
    brace depth rather than by looking for the closing line, because the object contains a
    nested one and a naive search would stop at the wrong brace.

    None rather than a failure for a log with no summary in it, because a job that failed
    before it printed anything is a real run whose Batch record is still worth capturing --
    and the alternative would make one unparseable log abandon a capture of three runs.
    """
    depth = 0
    collected: list[str] = []
    for line in lines:
        if depth == 0 and line.strip() != "{":
            continue
        collected.append(line)
        depth += line.count("{") - line.count("}")
        if depth == 0:
            break
    if not collected:
        return None
    try:
        parsed = json.loads("\n".join(collected))
    except ValueError as error:
        raise CaptureFailedError("run_summary_unreadable") from error
    if not isinstance(parsed, Mapping):
        raise CaptureFailedError("run_summary_is_not_an_object")
    return parsed


def capture_run_summary(
    run_id: str, *, log_stream: str, profile: str, region: str
) -> tuple[TrainingSummaryEvidence | GpuCapabilityEvidence | None, Mapping[str, Any]]:
    """Whichever of the two summaries this run printed, chosen by what is in it.

    Two programs have run on this queue and they report different things: the capability
    probe says what device nodes it was given and what the driver sees, and the training
    run says what torch did with them. Forked on a field each carries and the other does
    not, rather than on the run id, because which program a run executed is a fact about
    the log and not something a capture should have to be told.
    """
    lines = _log_lines(log_stream, profile=profile, region=region)
    summary = _summary_object(lines)
    if summary is None:
        return None, {}
    observed_at = observed_now()
    common = {
        "observed_at": observed_at,
        "source": "aws",
        "environment": "sandbox",
        "run_id": run_id,
        "log_group": GPU_LOG_GROUP,
        "log_stream": log_stream,
    }

    if "device_nodes" in summary:
        return (
            GpuCapabilityEvidence.model_validate(
                {
                    **common,
                    "device_nodes": tuple(str(node) for node in summary["device_nodes"]),
                    "nvidia_smi": summary["nvidia_smi"],
                    "nvidia_visible_devices": summary.get("nvidia_visible_devices", ""),
                    "output_prefix": summary["output_prefix"],
                    "team": summary["team"],
                    "wandb_key_injected": bool(summary.get("wandb_key_injected")),
                }
            ),
            summary,
        )

    if "checkpoint_uri" not in summary:
        return None, summary

    logged = tuple(key for key in EXPECTED_METRIC_KEYS if any(key in line for line in lines))
    return (
        TrainingSummaryEvidence.model_validate(
            {
                **common,
                "gpu_name": summary["gpu"],
                "torch_version": summary["torch"],
                "cuda_version": summary.get("cuda") or None,
                "parameters": summary["parameters"],
                "steps": summary["steps"],
                "first_loss": summary["first_loss"],
                "last_loss": summary["last_loss"],
                "seconds": summary["seconds"],
                "peak_memory_gib": summary["peak_memory_gib"],
                "checkpoint_uri": summary["checkpoint_uri"],
                "wandb_project": summary["wandb_project"],
                "wandb_run_url": summary["wandb_url"],
                "metric_keys": logged,
            }
        ),
        summary,
    )


class _CliObjectStore:
    """The four S3 calls :mod:`edullm_platform.checkpoints` makes, over the AWS CLI.

    Written here rather than with boto3 because boto3 is not a project dependency, and the
    reader was given a Protocol precisely so the store could be anything that answers. This
    is that Protocol's second implementation, which is also the first evidence that the
    seam was worth having.

    Only ``head_object`` and ``get_object`` and ``list_objects_v2`` are reachable from a
    capture. ``put_object`` raises: a tool that reads a live account has no business writing
    to it, and the Protocol is structural so the method has to exist to satisfy it.
    """

    def __init__(self, *, profile: str, region: str) -> None:
        self._profile = profile
        self._region = region

    def _call(self, arguments: Sequence[str]) -> Any:
        return aws_json(arguments, profile=self._profile, region=self._region)

    def put_object(self, **arguments: Any) -> Any:
        raise CaptureFailedError("a capture must not write to the store it is reading")

    def head_object(self, **arguments: Any) -> Any:
        answer = self._call(
            [
                "s3api", "head-object",
                "--bucket", arguments["Bucket"],
                "--key", arguments["Key"],
                "--checksum-mode", arguments.get("ChecksumMode", "ENABLED"),
            ]
        )
        modified = answer.get("LastModified")
        if isinstance(modified, str):
            answer["LastModified"] = datetime.fromisoformat(modified)
        return answer

    def get_object(self, **arguments: Any) -> Any:
        import io
        import subprocess
        import tempfile

        # Downloaded to a file rather than read from stdout: get-object writes its metadata
        # response to stdout as well as the body, so a pipe yields the object followed by a
        # JSON summary of itself.
        with tempfile.TemporaryDirectory() as directory:
            downloaded = Path(directory) / "object"
            completed = subprocess.run(
                [
                    "aws", "s3api", "get-object",
                    "--bucket", arguments["Bucket"],
                    "--key", arguments["Key"],
                    str(downloaded),
                    "--profile", self._profile,
                    "--region", self._region,
                    "--output", "json",
                ],
                capture_output=True,
                text=True,
                check=False,
                shell=False,
            )
            if completed.returncode != 0:
                raise self._missing(completed.stderr)
            return {"Body": io.BytesIO(downloaded.read_bytes())}

    def list_objects_v2(self, **arguments: Any) -> Any:
        return self._call(
            [
                "s3api", "list-objects-v2",
                "--bucket", arguments["Bucket"],
                "--prefix", arguments["Prefix"],
            ]
        )

    @staticmethod
    def _missing(stderr: str) -> Exception:
        """The CLI's absence answer, reshaped into the one the reader recognises.

        The reader identifies a missing object by ``error.response["Error"]["Code"]``,
        because botocore is not importable there. The CLI says the same thing on stderr, so
        the shape is rebuilt rather than the reader taught a second dialect.
        """
        error = RuntimeError(stderr.strip()[:200] or "get-object failed")
        code = "NoSuchKey" if "NoSuchKey" in stderr or "Not Found" in stderr else "Unknown"
        error.response = {"Error": {"Code": code}}  # type: ignore[attr-defined]
        return error


def capture_checkpoint(
    run_id: str, *, checkpoint_uri: str, claimed: str | None, profile: str, region: str
) -> CheckpointObservation:
    inspected = inspect_checkpoint(
        _CliObjectStore(profile=profile, region=region), prefix=checkpoint_uri
    )
    manifest = inspected.manifest
    return CheckpointObservation.model_validate(
        {
            "observed_at": observed_now(),
            "source": "aws",
            "environment": "sandbox",
            "run_id": run_id,
            "prefix": inspected.prefix,
            "state": inspected.state.value,
            "detail": inspected.detail,
            "step": manifest.step if manifest else None,
            "size_bytes": manifest.size_bytes if manifest else None,
            "checksum": manifest.checksum if manifest else None,
            "success_marker_uri": manifest.success_marker_uri if manifest else None,
            "container_claimed_checksum": claimed,
        }
    )


def summary_isolation(run_id: str, summary: Mapping[str, Any]) -> IsolationEvidence | None:
    """The four refusal codes the container recorded, if this run probed for them.

    None for a run that predates the probes, rather than a record of four empty strings. A
    run that did not ask has nothing to say about isolation, and a record saying so in a
    field-shaped way would read as an answer.
    """
    probes = summary.get("isolation")
    if not isinstance(probes, Mapping) or not probes:
        return None
    return IsolationEvidence.model_validate(
        {
            "observed_at": observed_now(),
            "source": "aws",
            "environment": "sandbox",
            "run_id": run_id,
            **{str(name): str(code) for name, code in probes.items()},
        }
    )


def summary_resume(run_id: str, summary: Mapping[str, Any]) -> ResumeEvidence | None:
    """What this run loaded before it trained, if it loaded anything."""
    resumed = summary.get("resumed")
    if not isinstance(resumed, Mapping) or not resumed:
        return None
    uri = str(resumed["uri"])
    # The run whose checkpoint this was, read out of the URI rather than passed in. The
    # prefix carries the run id by construction -- that is what D5's runs/{run_id} segment
    # is for -- so the provenance of a resume needs no second source to be recorded.
    predecessor = uri.split("/runs/", 1)[1].split("/", 1)[0]
    return ResumeEvidence.model_validate(
        {
            "observed_at": observed_now(),
            "source": "aws",
            "environment": "sandbox",
            "run_id": run_id,
            "resumed_from_run_id": predecessor,
            "uri": uri,
            "checksum": _claimed_digest(resumed.get("sha256")),
            "size_bytes": resumed["bytes"],
            "step": resumed["step"],
            "tensors": resumed["tensors"],
            "first_loss": summary["first_loss"],
            "cold_start_first_loss": COLD_START_FIRST_LOSS,
        }
    )


def _claimed_digest(claimed: object) -> str | None:
    """The digest the container printed, normalised to the ``sha256:`` form.

    The first GPU training run printed bare hex, because its program predated
    ``commit_checkpoint`` and rolled its own marker. Recorded in the canonical form rather
    than as it was printed, so the comparison against what the store attests is between two
    values of the same shape -- the alternative is a record where the two disagree because
    one has a prefix.
    """
    if not isinstance(claimed, str) or not claimed:
        return None
    text = claimed.removeprefix("sha256:").strip().lower()
    return f"sha256:{text}" if len(text) == 64 else None


def _hex_from_base64(encoded: object) -> str | None:
    if not isinstance(encoded, str) or not encoded:
        return None
    try:
        return f"sha256:{base64.b64decode(encoded, validate=True).hex()}"
    except (binascii.Error, ValueError):
        return None


def capture_output_prefixes(*, profile: str, region: str) -> OutputPrefixEvidence:
    """Every object in the outputs bucket, so the claim can be about what is *not* there."""
    listing = aws_json(
        ["s3api", "list-objects-v2", "--bucket", OUTPUTS_BUCKET], profile=profile, region=region
    )
    objects = []
    for entry in sorted(listing.get("Contents") or [], key=lambda item: str(item["Key"])):
        key = str(entry["Key"])
        head = aws_json(
            [
                "s3api", "head-object",
                "--bucket", OUTPUTS_BUCKET,
                "--key", key,
                "--checksum-mode", "ENABLED",
            ],
            profile=profile,
            region=region,
        )
        objects.append(
            OutputObject(
                key=key,
                size_bytes=int(entry.get("Size", 0)),
                checksum_sha256=_hex_from_base64(head.get("ChecksumSHA256")),
            )
        )
    return OutputPrefixEvidence(
        observed_at=observed_now(),
        source="aws",
        environment="sandbox",
        bucket=OUTPUTS_BUCKET,
        authorized_prefixes=AUTHORIZED_PREFIXES,
        objects=tuple(objects),
    )


def capture_compute_environment(*, profile: str, region: str) -> GpuComputeEnvironmentEvidence:
    described = aws_json(
        [
            "batch", "describe-compute-environments",
            "--compute-environments", GPU_COMPUTE_ENVIRONMENT_NAME,
            "--query", "computeEnvironments[0]",
        ],
        profile=profile,
        region=region,
    )
    resources = described.get("computeResources") or {}
    configuration = resources.get("ec2Configuration") or [{}]
    live = aws_json(
        [
            "ec2", "describe-instances",
            "--filters",
            f"Name=tag:AWSBatchComputeEnvironment,Values={GPU_COMPUTE_ENVIRONMENT_NAME}",
            "Name=instance-state-name,Values=pending,running,shutting-down,stopping",
            "--query", "Reservations[].Instances[].InstanceId",
        ],
        profile=profile,
        region=region,
    )
    return GpuComputeEnvironmentEvidence(
        observed_at=observed_now(),
        source="aws",
        environment="sandbox",
        region=region,
        compute_environment_name=GPU_COMPUTE_ENVIRONMENT_NAME,
        state=str(described["state"]),
        status=str(described["status"]),
        image_type=str(configuration[0].get("imageType", "unset")),
        instance_types=tuple(sorted(str(name) for name in resources.get("instanceTypes") or [])),
        subnet_count=len(resources.get("subnets") or []),
        minimum_vcpus=int(resources.get("minvCpus", 0)),
        maximum_vcpus=int(resources.get("maxvCpus", 0)),
        desired_vcpus=int(resources.get("desiredvCpus", 0)),
        live_instance_count=len(live or []),
    )


def capture_offerings(*, profile: str, region: str) -> InstanceTypeOfferingEvidence:
    """Which zones offer the GPU shape, and which of our subnets sit in them.

    Every zone in the region is recorded rather than only the ones that offer it, because
    the check is about placement and a list of the zones that work cannot say which one was
    excluded.
    """
    zones = aws_json(
        ["ec2", "describe-availability-zones", "--query", "AvailabilityZones[].ZoneName"],
        profile=profile,
        region=region,
    )
    offered = set(
        aws_json(
            [
                "ec2", "describe-instance-type-offerings",
                "--location-type", "availability-zone",
                "--filters", f"Name=instance-type,Values={GPU_INSTANCE_TYPE}",
                "--query", "InstanceTypeOfferings[].Location",
            ],
            profile=profile,
            region=region,
        )
        or []
    )
    return InstanceTypeOfferingEvidence(
        observed_at=observed_now(),
        source="aws",
        environment="sandbox",
        region=region,
        instance_type=GPU_INSTANCE_TYPE,
        offerings=tuple(
            InstanceTypeOffering(availability_zone=str(zone), offered=str(zone) in offered)
            for zone in sorted(zones or [])
        ),
    )


def _without_known_digests(line: str, exempted: Sequence[str]) -> str:
    """One log line with the prefixed digests masked, and the verified bare ones removed.

    ``redact_content_digests`` handles everything written with a ``sha256:`` prefix. What
    it cannot handle is a bare sixty-four-character digest, which is indistinguishable
    from a credential by shape alone -- so those are removed only when the caller has a
    specific value it verified against the store, never by pattern.
    """
    masked = redact_content_digests(line)
    for digest in exempted:
        masked = masked.replace(digest.removeprefix("sha256:"), "<verified-content-digest>")
    return masked


def capture_secret_delivery(
    *, profile: str, region: str, log_stream: str | None, exempted: Sequence[str] = ()
) -> SecretDeliveryEvidence:
    """How the W&B key reaches the container, and whether it turned up in the log.

    The job definition is read as deployed rather than from the template, because the
    template is what we asked for and this is the check that we got it.
    """
    definition = aws_json(
        [
            "batch", "describe-job-definitions",
            "--job-definition-name", GPU_JOB_DEFINITION_NAME,
            "--status", "ACTIVE",
            "--query", "jobDefinitions[-1]",
        ],
        profile=profile,
        region=region,
    )
    container = definition.get("containerProperties") or {}
    secrets = {str(entry["name"]): str(entry["valueFrom"]) for entry in container.get("secrets") or []}
    plain = tuple(sorted(str(entry["name"]) for entry in container.get("environment") or []))

    reference = secrets.get(WANDB_SECRET_VARIABLE, "")
    lines = _log_lines(log_stream, profile=profile, region=region) if log_stream else []
    # Only the digests the log actually carries in bare form, deduplicated. Recording every
    # digest that was *offered* as an exemption would overstate what had to be excused: this
    # run prints the sha256:-prefixed form, which the ordinary digest mask already handles,
    # so the honest answer for it is an empty list.
    # Measured against the *masked* line, which is the whole subtlety. A bare digest is one
    # that survives redact_content_digests; searching the raw line for the hex would match
    # the sha256:-prefixed form too, and report an exemption as needed when the ordinary
    # mask had already dealt with it.
    masked = [redact_content_digests(line) for line in lines]
    needed = sorted(
        {
            digest
            for digest in exempted
            if any(digest.removeprefix("sha256:") in line for line in masked)
        }
    )
    leaked = any(
        pattern.search(_without_known_digests(line, needed))
        for line in lines
        for pattern in NON_ACCOUNT_SECRET_PATTERNS
    )
    return SecretDeliveryEvidence(
        observed_at=observed_now(),
        source="aws",
        environment="sandbox",
        job_definition_name=GPU_JOB_DEFINITION_NAME,
        variable_name=WANDB_SECRET_VARIABLE,
        delivered_by_reference=bool(reference),
        # The last six characters of the secret's name, which is the unpredictable suffix
        # Secrets Manager appends. Recorded instead of the ARN because the ARN carries the
        # account id and the suffix is what identifies the secret.
        secret_arn_suffix=reference.rsplit("-", 1)[-1] if reference else "none",
        value_appears_in_environment=WANDB_SECRET_VARIABLE in plain,
        plain_environment_names=plain,
        log_lines_scanned=len(lines),
        log_holds_a_credential_shape=leaked,
        exempted_content_digests=tuple(needed),
    )


#: How an IAM resource ARN for an S3 object is split into the prefix it grants. Written as a
#: pattern rather than by slicing, because ``arn:aws:s3:::bucket`` with no key part is a
#: grant on the bucket itself and must not be read as a grant on the prefix ``''``.
S3_OBJECT_ARN = re.compile(r"^arn:aws[a-z-]*:s3:::(?P<bucket>[^/]+)/(?P<key>.+)$")


def capture_role_scope(*, profile: str, region: str) -> WorkloadRoleScopeEvidence:
    account = account_identity(profile=profile, region=region)
    listed = aws_json(
        ["iam", "list-role-policies", "--role-name", GPU_WORKLOAD_ROLE],
        profile=profile,
        region=region,
    )
    writable: set[str] = set()
    readable: set[str] = set()
    elsewhere: set[str] = set()
    deletes = False
    lineage = False
    for policy_name in listed.get("PolicyNames") or []:
        document = aws_json(
            [
                "iam", "get-role-policy",
                "--role-name", GPU_WORKLOAD_ROLE,
                "--policy-name", str(policy_name),
                "--query", "PolicyDocument",
            ],
            profile=profile,
            region=region,
        )
        for statement in document.get("Statement") or []:
            if statement.get("Effect") != "Allow":
                continue
            actions = statement.get("Action")
            actions = [actions] if isinstance(actions, str) else list(actions or [])
            resources = statement.get("Resource")
            resources = [resources] if isinstance(resources, str) else list(resources or [])
            deletes = deletes or any("Delete" in action for action in actions)
            for resource in resources:
                lineage = lineage or LINEAGE_BUCKET in str(resource)
                matched = S3_OBJECT_ARN.match(str(resource))
                if matched is None:
                    continue
                bucket = matched.group("bucket")
                key = matched.group("key")
                reads = any(action in ("s3:GetObject", "s3:*") for action in actions)
                writes = any(action in ("s3:PutObject", "s3:*") for action in actions)
                if bucket != OUTPUTS_BUCKET:
                    # Recorded whole rather than discarded. The key portion alone is what
                    # made a dataset read look like an outputs-bucket wildcard.
                    if reads or writes:
                        elsewhere.add(f"{bucket}/{key}")
                    continue
                if writes:
                    writable.add(key)
                if reads:
                    readable.add(key)
    del account  # read to prove the caller is who it thinks it is; never recorded
    return WorkloadRoleScopeEvidence(
        observed_at=observed_now(),
        source="aws",
        environment="sandbox",
        role_name=GPU_WORKLOAD_ROLE,
        writable_prefixes=tuple(sorted(writable)),
        readable_prefixes=tuple(sorted(readable)),
        grants_outside_the_outputs_bucket=tuple(sorted(elsewhere)),
        grants_delete=deletes,
        reaches_the_lineage_bucket=lineage,
    )


def _run_target(arguments: argparse.Namespace) -> int:
    if not arguments.run_id:
        raise CaptureFailedError("run_target_needs:--run-id")
    written: list[str] = []
    silent: list[str] = []
    for run_id in arguments.run_id:
        directory = arguments.output_dir / "runs" / run_id
        job = capture_gpu_job(run_id, profile=arguments.aws_profile, region=arguments.aws_region)
        write_model(directory / f"batch-job{CAPTURE_SUFFIX}", job, allow_content_digests=True)
        written.append(f"runs/{run_id}/batch-job{CAPTURE_SUFFIX}")

        if job.log_stream_name is None:
            # A job that never started a container has no stream, which is a fact about the
            # run rather than a failed capture. Its Batch record is written and says so.
            silent.append(run_id)
            continue
        summary, raw = capture_run_summary(
            run_id,
            log_stream=job.log_stream_name,
            profile=arguments.aws_profile,
            region=arguments.aws_region,
        )
        if summary is None:
            silent.append(run_id)
            continue
        name = (
            "training-summary"
            if isinstance(summary, TrainingSummaryEvidence)
            else "gpu-capability"
        )
        write_model(directory / f"{name}{CAPTURE_SUFFIX}", summary, allow_content_digests=True)
        written.append(f"runs/{run_id}/{name}{CAPTURE_SUFFIX}")

        if not isinstance(summary, TrainingSummaryEvidence):
            continue
        isolation = summary_isolation(run_id, raw)
        if isolation is not None:
            write_model(directory / f"isolation{CAPTURE_SUFFIX}", isolation, allow_content_digests=True)
            written.append(f"runs/{run_id}/isolation{CAPTURE_SUFFIX}")
        resumed = summary_resume(run_id, raw)
        if resumed is not None:
            write_model(directory / f"resume{CAPTURE_SUFFIX}", resumed, allow_content_digests=True)
            written.append(f"runs/{run_id}/resume{CAPTURE_SUFFIX}")

        checkpoint = capture_checkpoint(
            run_id,
            checkpoint_uri=summary.checkpoint_uri,
            claimed=_claimed_digest(raw.get("checkpoint_sha256")),
            profile=arguments.aws_profile,
            region=arguments.aws_region,
        )
        write_model(directory / f"checkpoint{CAPTURE_SUFFIX}", checkpoint, allow_content_digests=True)
        written.append(f"runs/{run_id}/checkpoint{CAPTURE_SUFFIX}")

    outputs = capture_output_prefixes(
        profile=arguments.aws_profile, region=arguments.aws_region
    )
    write_model(arguments.output_dir / f"outputs{CAPTURE_SUFFIX}", outputs, allow_content_digests=True)
    written.append(f"outputs{CAPTURE_SUFFIX}")

    report(
        {
            "targets": ["run"],
            "runs": sorted(arguments.run_id),
            "written": sorted(written),
            "runs_that_printed_no_summary": sorted(silent),
            "objects_outside_an_authorized_prefix": list(outputs.stray_keys),
            "verdict": "stray_output" if outputs.stray_keys else "ok",
        }
    )
    return 0


def _configuration_target(arguments: argparse.Namespace, targets: Sequence[str]) -> int:
    written: list[str] = []
    log_stream: str | None = None
    verified: list[str] = []
    if arguments.run_id:
        log_stream = capture_gpu_job(
            arguments.run_id[0], profile=arguments.aws_profile, region=arguments.aws_region
        ).log_stream_name
        # Read back from the run capture rather than recomputed. The exemption is only
        # sound because the digest is one the store attested for an object this run wrote,
        # so it has to come from the record that established that.
        committed = (
            arguments.output_dir / "runs" / arguments.run_id[0] / f"checkpoint{CAPTURE_SUFFIX}"
        )
        if committed.is_file():
            observation = json.loads(committed.read_text())
            verified = [
                digest
                for digest in (
                    observation.get("checksum"),
                    observation.get("container_claimed_checksum"),
                )
                if isinstance(digest, str)
            ]

    summary: dict[str, Any] = {"targets": list(targets)}
    if "compute-environment" in targets:
        record: Any = capture_compute_environment(
            profile=arguments.aws_profile, region=arguments.aws_region
        )
        write_model(arguments.output_dir / f"gpu-compute-environment{CAPTURE_SUFFIX}", record, allow_content_digests=True)
        written.append(f"gpu-compute-environment{CAPTURE_SUFFIX}")
        # Reported rather than enforced. A capture taken while the environment is busy is a
        # true record of a busy environment; what must not happen is committing it as
        # evidence for the idle criterion, which is why the verdict says which one it is.
        summary["compute_environment"] = (
            "idle" if record.idle_and_holding_nothing else "holding"
        )
        summary["can_fall_back_to_another_shape"] = record.can_fall_back_to_another_shape
    if "offerings" in targets:
        offerings = capture_offerings(
            profile=arguments.aws_profile, region=arguments.aws_region
        )
        write_model(arguments.output_dir / f"instance-offerings{CAPTURE_SUFFIX}", offerings, allow_content_digests=True)
        written.append(f"instance-offerings{CAPTURE_SUFFIX}")
        summary["zones_offering_the_shape"] = list(offerings.offering_zones)
    if "secret-delivery" in targets:
        delivery = capture_secret_delivery(
            profile=arguments.aws_profile,
            region=arguments.aws_region,
            log_stream=log_stream,
            exempted=verified,
        )
        write_model(arguments.output_dir / f"secret-delivery{CAPTURE_SUFFIX}", delivery, allow_content_digests=True)
        written.append(f"secret-delivery{CAPTURE_SUFFIX}")
        summary["secret_stayed_out_of_every_record"] = delivery.stayed_out_of_every_record
    if "role-scope" in targets:
        scope = capture_role_scope(profile=arguments.aws_profile, region=arguments.aws_region)
        write_model(arguments.output_dir / f"workload-role-scope{CAPTURE_SUFFIX}", scope, allow_content_digests=True)
        written.append(f"workload-role-scope{CAPTURE_SUFFIX}")
        summary["writable_prefixes"] = list(scope.writable_prefixes)

    summary["written"] = sorted(written)
    report(summary)
    return 0


CONFIGURATION_TARGETS: Final = ("compute-environment", "offerings", "secret-delivery", "role-scope")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture what Phase 4's GPU runs left behind. Read-only."
    )
    parser.add_argument("--aws-profile", default="sbsandbox")
    parser.add_argument("--aws-region", default="us-east-1")
    parser.add_argument(
        "--target",
        choices=["run", *CONFIGURATION_TARGETS, "all"],
        required=True,
    )
    parser.add_argument(
        "--run-id",
        action="append",
        default=[],
        help=(
            "repeatable; required by --target run, and used by --target secret-delivery to "
            "choose which log stream to scan. Named rather than discovered, because which "
            "runs are worth committing as evidence is a judgement somebody makes in writing."
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    def target() -> int:
        if arguments.target == "run":
            return _run_target(arguments)
        if arguments.target == "all":
            outcome = _run_target(arguments)
            return outcome or _configuration_target(arguments, CONFIGURATION_TARGETS)
        return _configuration_target(arguments, [arguments.target])

    return run_capture(
        target, output_dir=arguments.output_dir, allowed_suffix=ALLOWED_OUTPUT_SUFFIX
    )


if __name__ == "__main__":
    sys.exit(main())
