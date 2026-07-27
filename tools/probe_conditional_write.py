"""Find out whether Step Functions really passes ``IfNoneMatch`` through to S3.

**The question.** The Phase 2 state machine writes each result object once and must never
overwrite one. ``IfNoneMatch: "*"`` is how S3 refuses a second write of the same key, and
the state machine's ``Catch`` block has to name whatever Step Functions calls that
refusal — which is why the committed template catches ``States.ALL`` today. ``States.ALL``
catches a permission error, a throttle and a genuine collision alike, so the state machine
cannot currently tell "this key is already written" from "something is broken".

**What is already established, and what is not.** ``IfNoneMatch`` and
``ChecksumAlgorithm`` are accepted at *definition* time: the AWS SDK integration validates
against ``ValidateStateMachineDefinition``, and a control probe using a field name that
does not exist was rejected, so acceptance means the validator recognises these two rather
than that it waves unknown fields through. Definition-time acceptance is not runtime
pass-through. AWS documents that a parameter newly added to an SDK is not immediately
available through the SDK integration, and nothing published says which build of the S3
model the integration carries. The only way to know is to write the same key twice and
look.

**What this run produces.** One record, and the field that matters is the error name Step
Functions reported for the second write. That name is what goes in ``ErrorEquals``. The
cause is recorded beside it, because a name without the message it came with is hard to
recognise again, and the ``ChecksumSHA256`` read back through ``HeadObject`` is recorded
because a checksum requested at write time and not retrievable afterwards is a second way
the same parameter can be silently dropped.

**Nothing is assumed about the answer.** Both outcomes are written down. A second write
that succeeded is the finding that keeps ``States.ALL`` in the template, and it exits 1
rather than 0 because the run did not prove what it set out to prove — but the record is
written either way, because what failed is the integration rather than the probe.

**Everything it creates, it deletes.** A bucket and a state machine, both named
``sbsandbox-intern-edullm-*``, torn down in a ``finally`` so a crash midway does not
strand them. A teardown that could not finish is reported and exits 2 whatever else the
run established, because a stranded bucket in a shared account is not something to
discover later.

**It does not create the role it passes.** A state machine needs an execution role, and
minting an IAM role inside a probe would put role creation on a code path nobody reviews.
``--state-machine-role-name`` names a role that already exists, and it needs
``s3:PutObject`` on ``sbsandbox-intern-edullm-conditional-write-*``. The admission states
role will not do: it can write only to the real lineage bucket, which is not somewhere to
put probe objects. ``infra/README.md`` has the two commands that create the role by hand
and the two that delete it afterwards.

**``--dry-run`` prints the calls and makes none of them.** Three values are not knowable
without a call — the account in the role ARN, the state machine ARN and each execution
ARN — and those print as named placeholders. Every other token is literal. Pass the same
command line without ``--dry-run`` to execute it.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shlex
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from edullm_platform.evidence import (
    AWS_ACCOUNT_ID_PLACEHOLDER,
    redact_aws_account_ids,
    redact_content_digests,
    scan_for_secrets,
)
from edullm_platform.publisher_denials import parse_aws_cli_error

#: Everything this probe creates is named under the prefix this project owns, so a
#: stranded resource is attributable and a sweep can find it by name.
NAME_PREFIX: Final = "sbsandbox-intern-edullm-conditional-write-"

#: The key written twice. One key, because the whole question is what happens on the
#: second write of a key that already exists.
PROBE_OBJECT_KEY: Final = "conditional-write-probe.json"
PROBE_OBJECT_BODY: Final = '{"probe":"conditional-write"}'

#: What ``--name-suffix`` may be. S3 bucket names are lowercase, and the prefix above is
#: already 41 of the 63 characters a bucket name may hold.
NAME_SUFFIX_PATTERN: Final = re.compile(r"[a-z0-9][a-z0-9-]{0,19}")

#: The one region that takes no ``LocationConstraint``. Every other region requires one,
#: and sending it for this one is an error rather than a no-op.
BUCKET_LOCATION_EXEMPT_REGION: Final = "us-east-1"

AWS_CALL_TIMEOUT_SECONDS: Final = 60

#: How long an execution of a one-state machine may take, and how often it is asked. A
#: single ``PutObject`` finishes in well under a second; the bound is here so a probe
#: against a wedged execution ends rather than polls forever.
EXECUTION_POLL_SECONDS: Final = 2
EXECUTION_TIMEOUT_SECONDS: Final = 120

#: How much of a service ``cause`` is recorded. Truncation happens before redaction and
#: the result is re-scanned afterwards, so a cut that split a credential run cannot
#: produce text the scan then accepts.
MAXIMUM_RECORDED_CAUSE: Final = 4096

#: Values discovered at run time, spelled this way in the ``--dry-run`` plan.
STATE_MACHINE_ARN_PLACEHOLDER: Final = "<state-machine-arn>"
EXECUTION_ARN_PLACEHOLDER: Final = "<execution-arn>"

#: The state machine, and it is deliberately one state with no ``Catch``. A caught error
#: is reported as a successful execution, and the error name this probe exists to read
#: would be inside the caught output rather than on the execution. Letting the execution
#: fail puts the name and the cause on ``DescribeExecution``, where they cannot be missed.
#:
#: The bucket, key and body come from the execution input so the definition is a constant;
#: ``IfNoneMatch`` and ``ChecksumAlgorithm`` are literals, because they are the two fields
#: under test and reading them from input would be testing the input path instead.
CONDITIONAL_WRITE_DEFINITION: Final[dict[str, Any]] = {
    "Comment": "Phase 2 probe: does the S3 SDK integration pass IfNoneMatch through?",
    "StartAt": "ConditionalWrite",
    "States": {
        "ConditionalWrite": {
            "Type": "Task",
            "Resource": "arn:aws:states:::aws-sdk:s3:putObject",
            "Parameters": {
                "Bucket.$": "$.bucket",
                "Key.$": "$.key",
                "Body.$": "$.body",
                "IfNoneMatch": "*",
                "ChecksumAlgorithm": "SHA256",
            },
            "End": True,
        }
    },
}

#: S3 stamps an extended request id on its errors. It is an opaque identifier rather than
#: a credential, but it is a long base64 run and ``scan_for_secrets`` cannot tell the
#: difference, so a cause carrying one would be refused whole. It is masked by the field
#: that holds it rather than by shape, so nothing else that merely looks like a credential
#: is hidden by this and a cause carrying a real one is still refused.
S3_EXTENDED_REQUEST_ID: Final = re.compile(
    r"(?i)(?P<label>x-amz-id-2|extended request id)"
    r"(?P<gap>\s*[:=]\s*)"
    r"(?P<value>[A-Za-z0-9/+=]{20,})"
)
EXTENDED_REQUEST_ID_PLACEHOLDER: Final = "<s3-extended-request-id>"

#: Why a piece of captured text is absent from the record rather than present and masked.
WITHHELD_CARRIES_A_TOKEN: Final = "carries_a_token_the_secret_scan_refuses"


class ProbeFailedError(RuntimeError):
    """The probe could not be run, so there is nothing honest to write down.

    Carries a machine-readable reason and never a service message, because the reason is
    printed and an AWS error message names the account.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class PlannedCall:
    """One AWS CLI call, what it is for, and whether it is made more than once."""

    purpose: str
    arguments: tuple[str, ...]
    repeated: bool = False

    def command(self, *, profile: str, region: str) -> list[str]:
        return [
            "aws",
            *self.arguments,
            "--profile",
            profile,
            "--region",
            region,
            "--output",
            "json",
        ]


@dataclass(frozen=True)
class ProbeNames:
    """What this run calls the things it creates."""

    suffix: str

    @property
    def bucket(self) -> str:
        return f"{NAME_PREFIX}{self.suffix}"

    @property
    def state_machine(self) -> str:
        return f"{NAME_PREFIX}{self.suffix}"

    def execution(self, ordinal: str) -> str:
        return f"{ordinal}-write-{self.suffix}"


@dataclass(frozen=True)
class ProbeContext:
    aws_profile: str
    aws_region: str
    names: ProbeNames
    role_name: str
    observed_at: datetime
    #: Read from STS and used only to build the execution role ARN. Never written down.
    account_id: str = ""

    @property
    def role_arn(self) -> str:
        return f"arn:aws:iam::{self.account_id}:role/{self.role_name}"


@dataclass(frozen=True)
class ExecutionOutcome:
    """What one execution of the one-state machine ended as."""

    execution_name: str
    status: str
    error: Mapping[str, str | None]
    cause: Mapping[str, str | None]

    @property
    def succeeded(self) -> bool:
        return self.status == "SUCCEEDED"

    @property
    def failed(self) -> bool:
        return self.status == "FAILED"


# --------------------------------------------------------------------------------------
# The calls, built once and used by both the plan and the run
# --------------------------------------------------------------------------------------


def caller_identity_call() -> PlannedCall:
    return PlannedCall(
        purpose="confirm the session and read the account the role ARN needs",
        arguments=("sts", "get-caller-identity"),
    )


def create_bucket_call(names: ProbeNames, *, region: str) -> PlannedCall:
    arguments = ["s3api", "create-bucket", "--bucket", names.bucket]
    if region != BUCKET_LOCATION_EXEMPT_REGION:
        arguments += ["--create-bucket-configuration", f"LocationConstraint={region}"]
    return PlannedCall(purpose="create the throwaway bucket", arguments=tuple(arguments))


def create_state_machine_call(names: ProbeNames, *, role_arn: str) -> PlannedCall:
    return PlannedCall(
        purpose="create the one-state machine that writes with IfNoneMatch",
        arguments=(
            "stepfunctions",
            "create-state-machine",
            "--name",
            names.state_machine,
            "--type",
            "STANDARD",
            "--role-arn",
            role_arn,
            "--definition",
            json.dumps(CONDITIONAL_WRITE_DEFINITION, separators=(",", ":"), sort_keys=True),
        ),
    )


def start_execution_call(
    names: ProbeNames,
    *,
    state_machine_arn: str,
    ordinal: str,
) -> PlannedCall:
    payload = {"bucket": names.bucket, "key": PROBE_OBJECT_KEY, "body": PROBE_OBJECT_BODY}
    return PlannedCall(
        purpose=f"{ordinal} write of the same key",
        arguments=(
            "stepfunctions",
            "start-execution",
            "--state-machine-arn",
            state_machine_arn,
            "--name",
            names.execution(ordinal),
            "--input",
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
        ),
    )


def describe_execution_call(execution_arn: str, *, ordinal: str) -> PlannedCall:
    return PlannedCall(
        purpose=f"read the {ordinal} execution's status, error and cause",
        arguments=("stepfunctions", "describe-execution", "--execution-arn", execution_arn),
        repeated=True,
    )


def head_object_call(names: ProbeNames) -> PlannedCall:
    return PlannedCall(
        purpose="read the stored checksum back",
        arguments=(
            "s3api",
            "head-object",
            "--bucket",
            names.bucket,
            "--key",
            PROBE_OBJECT_KEY,
            "--checksum-mode",
            "ENABLED",
        ),
    )


def delete_state_machine_call(state_machine_arn: str) -> PlannedCall:
    return PlannedCall(
        purpose="tear down the state machine",
        arguments=(
            "stepfunctions",
            "delete-state-machine",
            "--state-machine-arn",
            state_machine_arn,
        ),
    )


def delete_object_call(names: ProbeNames) -> PlannedCall:
    return PlannedCall(
        purpose="tear down the written object",
        arguments=("s3api", "delete-object", "--bucket", names.bucket, "--key", PROBE_OBJECT_KEY),
    )


def delete_bucket_call(names: ProbeNames) -> PlannedCall:
    return PlannedCall(
        purpose="tear down the bucket",
        arguments=("s3api", "delete-bucket", "--bucket", names.bucket),
    )


def planned_calls(names: ProbeNames, *, region: str, role_arn: str) -> tuple[PlannedCall, ...]:
    """Every call one run makes, in order, with run-time values left as placeholders."""
    return (
        caller_identity_call(),
        create_bucket_call(names, region=region),
        create_state_machine_call(names, role_arn=role_arn),
        start_execution_call(
            names, state_machine_arn=STATE_MACHINE_ARN_PLACEHOLDER, ordinal="first"
        ),
        describe_execution_call(EXECUTION_ARN_PLACEHOLDER, ordinal="first"),
        start_execution_call(
            names, state_machine_arn=STATE_MACHINE_ARN_PLACEHOLDER, ordinal="second"
        ),
        describe_execution_call(EXECUTION_ARN_PLACEHOLDER, ordinal="second"),
        head_object_call(names),
        delete_state_machine_call(STATE_MACHINE_ARN_PLACEHOLDER),
        delete_object_call(names),
        delete_bucket_call(names),
    )


# --------------------------------------------------------------------------------------
# Talking to the account
# --------------------------------------------------------------------------------------


def run_aws(context: ProbeContext, call: PlannedCall) -> subprocess.CompletedProcess[str]:
    command = call.command(profile=context.aws_profile, region=context.aws_region)
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
        raise ProbeFailedError(f"aws_call_timed_out:{call.arguments[1]}") from exc
    except OSError as exc:
        raise ProbeFailedError("aws_cli_unavailable") from exc


def call_failure_reason(call: PlannedCall, stderr: str) -> str:
    """A reason token for a failed call, quoting the operation but not the message."""
    error = parse_aws_cli_error(stderr)
    if error is None:
        return f"aws_call_failed:{call.arguments[1]}"
    return f"aws_call_failed:{error.operation}:{error.code}"


def aws_json(context: ProbeContext, call: PlannedCall) -> dict[str, Any]:
    """Run one call and return its JSON answer, or stop the probe."""
    completed = run_aws(context, call)
    if completed.returncode != 0:
        raise ProbeFailedError(call_failure_reason(call, completed.stderr))
    if not completed.stdout.strip():
        # A delete answers with nothing at all, which is an answer rather than a failure.
        return {}
    try:
        payload = json.loads(completed.stdout)
    except ValueError as exc:
        raise ProbeFailedError(f"aws_answer_unreadable:{call.arguments[1]}") from exc
    if not isinstance(payload, dict):
        raise ProbeFailedError(f"aws_answer_was_not_an_object:{call.arguments[1]}")
    return payload


def read_account_id(context: ProbeContext) -> str:
    """Ask STS for the account, which is what the execution role ARN needs.

    Used inside this process and never written down. ``GetCallerIdentity`` needs no
    permission, so a failure here means the session itself is not usable — which is worth
    finding out before anything has been created. The reason a failed call raises is left
    as :func:`aws_json` phrased it, because "there is no aws on PATH" and "the session was
    refused" are different problems and only one of them is about credentials.
    """
    answer = aws_json(context, caller_identity_call())
    account_id = answer.get("Account")
    if not isinstance(account_id, str) or not account_id:
        raise ProbeFailedError("caller_identity_unreadable")
    return account_id


# --------------------------------------------------------------------------------------
# Writing down what a service said
# --------------------------------------------------------------------------------------


def mask_extended_request_ids(text: str) -> str:
    return S3_EXTENDED_REQUEST_ID.sub(
        lambda match: f"{match['label']}{match['gap']}{EXTENDED_REQUEST_ID_PLACEHOLDER}",
        text,
    )


def record_service_text(text: str | None) -> dict[str, str | None]:
    """Mask a service's own words, or withhold them and say that is what happened.

    Account IDs first, then content digests, which is the order the rest of this
    repository uses and the only one that works: masking a digest first leaves twelve of
    its characters looking like an account ID. Text that still carries something the scan
    refuses is withheld rather than laundered — masking inside a credential breaks the run
    that identifies it and leaves the credential in text the scan then accepts. Withholding
    is recorded, and the operator can read the original from
    ``aws stepfunctions describe-execution`` while the execution history survives.
    """
    if text is None:
        return {"text": None, "withheld_reason": None}
    truncated = mask_extended_request_ids(text)[:MAXIMUM_RECORDED_CAUSE]
    try:
        masked = redact_content_digests(redact_aws_account_ids(truncated))
        scan_for_secrets(masked)
    except ValueError:
        return {"text": None, "withheld_reason": WITHHELD_CARRIES_A_TOKEN}
    return {"text": masked, "withheld_reason": None}


# --------------------------------------------------------------------------------------
# The observation
# --------------------------------------------------------------------------------------


def await_execution(
    context: ProbeContext,
    execution_arn: str,
    *,
    ordinal: str,
) -> ExecutionOutcome:
    """Poll one execution until it stops, and record how it stopped."""
    call = describe_execution_call(execution_arn, ordinal=ordinal)
    deadline = time.monotonic() + EXECUTION_TIMEOUT_SECONDS
    described: dict[str, Any] = {}
    while True:
        described = aws_json(context, call)
        if described.get("status") != "RUNNING":
            break
        if time.monotonic() >= deadline:
            raise ProbeFailedError(f"execution_did_not_stop:{ordinal}")
        time.sleep(EXECUTION_POLL_SECONDS)
    status = described.get("status")
    if not isinstance(status, str) or not status:
        raise ProbeFailedError(f"execution_status_unreadable:{ordinal}")
    error = described.get("error")
    cause = described.get("cause")
    return ExecutionOutcome(
        execution_name=context.names.execution(ordinal),
        status=status,
        error=record_service_text(error if isinstance(error, str) else None),
        cause=record_service_text(cause if isinstance(cause, str) else None),
    )


def write_once(context: ProbeContext, *, state_machine_arn: str, ordinal: str) -> ExecutionOutcome:
    started = aws_json(
        context,
        start_execution_call(context.names, state_machine_arn=state_machine_arn, ordinal=ordinal),
    )
    execution_arn = started.get("executionArn")
    if not isinstance(execution_arn, str) or not execution_arn:
        raise ProbeFailedError(f"execution_not_started:{ordinal}")
    return await_execution(context, execution_arn, ordinal=ordinal)


def read_stored_checksum(context: ProbeContext) -> dict[str, Any]:
    """What ``HeadObject`` with ``ChecksumMode=ENABLED`` says about the stored object.

    A failure here is recorded rather than raised. The object is absent whenever the first
    write failed, and "there is no object" is part of the answer in that case.
    """
    call = head_object_call(context.names)
    completed = run_aws(context, call)
    if completed.returncode != 0:
        error = parse_aws_cli_error(completed.stderr)
        return {
            "readable": False,
            "error_code": None if error is None else error.code,
            "checksum_sha256": record_service_text(None),
        }
    try:
        head = json.loads(completed.stdout or "{}")
    except ValueError:
        return {"readable": False, "error_code": None, "checksum_sha256": record_service_text(None)}
    checksum = head.get("ChecksumSHA256") if isinstance(head, dict) else None
    return {
        "readable": True,
        "error_code": None,
        "checksum_sha256": record_service_text(checksum if isinstance(checksum, str) else None),
    }


def tear_down(
    context: ProbeContext,
    *,
    state_machine_arn: str | None,
    bucket_created: bool,
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Delete everything this run created, and report anything that survived.

    Every deletion is attempted whatever the ones before it answered, because a state
    machine that could not be deleted is no reason to leave the bucket as well. The object
    is deleted whenever the bucket exists rather than only when a write succeeded: S3
    answers a delete of an absent key successfully, and the bucket cannot be deleted while
    anything is still in it.
    """
    record: dict[str, str] = {}
    stranded: list[str] = []

    def attempt(name: str, call: PlannedCall) -> None:
        try:
            completed = run_aws(context, call)
        except ProbeFailedError as exc:
            record[name] = f"left_behind:{exc.reason}"
            stranded.append(name)
            return
        if completed.returncode != 0:
            record[name] = f"left_behind:{call_failure_reason(call, completed.stderr)}"
            stranded.append(name)
            return
        record[name] = "deleted"

    if state_machine_arn is None:
        record["state_machine"] = "not_created"
    else:
        attempt("state_machine", delete_state_machine_call(state_machine_arn))
    if not bucket_created:
        record["object"] = "not_created"
        record["bucket"] = "not_created"
        return record, tuple(stranded)
    attempt("object", delete_object_call(context.names))
    attempt("bucket", delete_bucket_call(context.names))
    return record, tuple(stranded)


@dataclass(frozen=True)
class ProbeObservation:
    first: ExecutionOutcome
    second: ExecutionOutcome
    head_object: Mapping[str, Any]

    @property
    def if_none_match_refused_the_second_write(self) -> bool:
        return self.first.succeeded and self.second.failed

    @property
    def checksum_retrievable(self) -> bool:
        checksum = self.head_object.get("checksum_sha256")
        return isinstance(checksum, Mapping) and checksum.get("text") is not None

    @property
    def verdict(self) -> str:
        if not self.first.succeeded:
            return "first_write_did_not_succeed"
        if self.second.failed:
            return "conditional_write_enforced"
        if self.second.succeeded:
            return "conditional_write_not_enforced"
        return "second_write_outcome_unclear"


@dataclass(frozen=True)
class ProbeRun:
    """Everything one run established, including what it managed to clean up.

    ``observation`` and ``failure`` are exclusive, and the teardown is reported for both.
    A run that failed halfway still created things, and what happened to them is the part
    the operator has to act on.
    """

    observation: ProbeObservation | None
    failure: str | None
    teardown: Mapping[str, str]
    stranded: tuple[str, ...]


def observe(context: ProbeContext) -> ProbeRun:
    """Create, write twice, read back, and tear down whatever happened in between."""
    state_machine_arn: str | None = None
    bucket_created = False
    observation: ProbeObservation | None = None
    failure: str | None = None
    try:
        aws_json(context, create_bucket_call(context.names, region=context.aws_region))
        bucket_created = True
        created = aws_json(
            context, create_state_machine_call(context.names, role_arn=context.role_arn)
        )
        arn = created.get("stateMachineArn")
        if not isinstance(arn, str) or not arn:
            raise ProbeFailedError("state_machine_not_created")
        state_machine_arn = arn
        observation = ProbeObservation(
            first=write_once(context, state_machine_arn=arn, ordinal="first"),
            second=write_once(context, state_machine_arn=arn, ordinal="second"),
            head_object=read_stored_checksum(context),
        )
    except ProbeFailedError as exc:
        failure = exc.reason
    finally:
        teardown, stranded = tear_down(
            context, state_machine_arn=state_machine_arn, bucket_created=bucket_created
        )
    return ProbeRun(
        observation=observation, failure=failure, teardown=teardown, stranded=stranded
    )


# --------------------------------------------------------------------------------------
# The record
# --------------------------------------------------------------------------------------


def build_record(
    context: ProbeContext,
    observation: ProbeObservation,
    teardown: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "probe": "step-functions-conditional-write",
        "environment": "sandbox",
        "observed_at": context.observed_at.isoformat().replace("+00:00", "Z"),
        "region": context.aws_region,
        "bucket_name": context.names.bucket,
        "state_machine_name": context.names.state_machine,
        # The name, not the ARN: the name identifies the role and the ARN is the account
        # ID with a name attached.
        "state_machine_role_name": context.role_name,
        "object_key": PROBE_OBJECT_KEY,
        "state_machine_definition": CONDITIONAL_WRITE_DEFINITION,
        "writes": [
            {
                "ordinal": ordinal,
                "execution_name": outcome.execution_name,
                "status": outcome.status,
                "error": dict(outcome.error),
                "cause": dict(outcome.cause),
            }
            for ordinal, outcome in (("first", observation.first), ("second", observation.second))
        ],
        "head_object": dict(observation.head_object),
        "findings": {
            "if_none_match_refused_the_second_write": (
                observation.if_none_match_refused_the_second_write
            ),
            "checksum_sha256_retrievable": observation.checksum_retrievable,
        },
        "verdict": observation.verdict,
        "teardown": dict(teardown),
    }


def write_record(path: Path, record: Mapping[str, Any]) -> None:
    """Serialize the record, refuse it whole if anything in it would leak, then write."""
    serialized = json.dumps(record, indent=2, sort_keys=True) + "\n"
    try:
        scan_for_secrets(serialized)
    except ValueError as exc:
        raise ProbeFailedError("record_holds_a_credential") from exc
    path.write_text(serialized, encoding="utf-8")


# --------------------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe whether the Step Functions S3 integration enforces IfNoneMatch."
    )
    parser.add_argument("--aws-profile", required=True)
    parser.add_argument("--aws-region", required=True)
    parser.add_argument(
        "--state-machine-role-name",
        required=True,
        help=(
            "an existing role the state machine runs as, holding s3:PutObject on "
            f"{NAME_PREFIX}*. This probe never creates a role; see infra/README.md."
        ),
    )
    parser.add_argument(
        "--name-suffix",
        default=None,
        help=(
            "what the throwaway bucket and state machine are called after the "
            f"{NAME_PREFIX} prefix. Defaults to a fresh random suffix."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help=(
            "where the record is written. Required for --dry-run too, so the command "
            "line that gets reviewed is the one that runs."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the calls this run would make, and make none of them.",
    )
    return parser


def resolve_suffix(requested: str | None) -> str:
    suffix = requested if requested is not None else secrets.token_hex(4)
    if NAME_SUFFIX_PATTERN.fullmatch(suffix) is None:
        raise ProbeFailedError("name_suffix_unusable")
    return suffix


def dry_run_plan(
    names: ProbeNames,
    *,
    profile: str,
    region: str,
    role_name: str,
) -> dict[str, Any]:
    role_arn = f"arn:aws:iam::{AWS_ACCOUNT_ID_PLACEHOLDER}:role/{role_name}"
    return {
        "dry_run": True,
        "bucket_name": names.bucket,
        "state_machine_name": names.state_machine,
        "object_key": PROBE_OBJECT_KEY,
        "state_machine_definition": CONDITIONAL_WRITE_DEFINITION,
        "planned_calls": [
            {
                "purpose": call.purpose,
                "command": shlex.join(call.command(profile=profile, region=region)),
                "repeated_until_the_execution_stops": call.repeated,
            }
            for call in planned_calls(names, region=region, role_arn=role_arn)
        ],
    }


def report_teardown(stranded: Sequence[str], teardown: Mapping[str, str]) -> None:
    for name in stranded:
        print(f"teardown_incomplete:{name}:{teardown[name]}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    try:
        names = ProbeNames(suffix=resolve_suffix(arguments.name_suffix))
    except ProbeFailedError as exc:
        print(exc.reason, file=sys.stderr)
        return 2

    if arguments.dry_run:
        plan = dry_run_plan(
            names,
            profile=arguments.aws_profile,
            region=arguments.aws_region,
            role_name=arguments.state_machine_role_name,
        )
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0

    context = ProbeContext(
        aws_profile=arguments.aws_profile,
        aws_region=arguments.aws_region,
        names=names,
        role_name=arguments.state_machine_role_name,
        observed_at=datetime.now(tz=UTC).replace(microsecond=0),
    )
    try:
        context = replace(context, account_id=read_account_id(context))
    except ProbeFailedError as exc:
        print(exc.reason, file=sys.stderr)
        return 2

    run = observe(context)
    if run.observation is None:
        print(run.failure, file=sys.stderr)
        report_teardown(run.stranded, run.teardown)
        return 2

    record = build_record(context, run.observation, run.teardown)
    try:
        write_record(arguments.output, record)
    except ProbeFailedError as exc:
        print(exc.reason, file=sys.stderr)
        return 2
    except OSError:
        print("output_unwritable", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "verdict": record["verdict"],
                "findings": record["findings"],
                "second_write_error": run.observation.second.error["text"],
                "teardown": record["teardown"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    if run.stranded:
        report_teardown(run.stranded, run.teardown)
        return 2
    if not run.observation.if_none_match_refused_the_second_write:
        print(f"conditional_write_not_proven:{record['verdict']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
