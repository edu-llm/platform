"""The admission denial matrix: what is attempted, and what counts as a refusal.

The admission role may start one state machine and read that execution back. Every test
here is about the difference between an attempt that was *refused* and an attempt that
merely *failed*, because a not-found, a malformed parameter, a throttle and a timeout are
all failures, and every one of them is what a permitted call looks like when it is pointed
at something that is not there. Recording any of them as a denial would let the widening
this matrix exists to catch pass as proof that it did not happen.

Two Phase 2 cases carry most of the weight, and neither of them exists in Phase 1.

The lineage bucket refuses unconditional writes on its own behalf, to every principal in
the account, and S3's refusal names no policy. A write probe that omitted
``If-None-Match`` would therefore answer "Access Denied" on every run including the runs
where the role could write whatever it wanted, so the probe sends the header and the tests
pin that it does.

EC2 words both answers in its own vocabulary: a refusal is ``UnauthorizedOperation`` and a
*permitted* dry run is ``DryRunOperation`` — an error, with a non-zero exit status, that
means the role can launch instances. The same shape appears in S3's ``PreconditionFailed``.
Both are pinned as the permission being present rather than as a probe that proved nothing.

The CLI cases run the real command against a stub ``aws`` on PATH, so what is under test is
the argv each probe builds and the exit status as well as the judgement, and above all what
does and does not reach the two streams a public runner log is made of. There are no AWS
credentials in this environment, so recorded output is the only thing any of this can be
decided from — which is how the module is arranged.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from workflow_support import write_stub

from edullm_platform import admission_denials
from edullm_platform.admission_denials import (
    ADMISSION_DENIED_ACTIONS,
    ADMISSION_PROBE_LESSONS,
    ADMISSION_ROLE_NAME,
    ADMISSION_STATE_MACHINE_NAME,
    DENIAL_PROBE_KEY_PAIR_NAME,
    EC2_AUTHORIZATION_ERROR_CODES,
    LINEAGE_BUCKET,
    LINEAGE_PROBE_KEY,
    LINEAGE_RECORD_PREFIXES,
    AdmissionDenialMatrix,
    AdmissionDenialProbe,
    AdmissionSetupError,
    AdmissionSetupReason,
    admission_denial_probes,
    attempt_admission_denials,
    caller_identity,
    mask_encoded_authorization_failure,
    read_lineage_bucket,
    read_state_machine_arn,
    record_admission_denial,
    require_denial,
)
from edullm_platform.evidence import AWS_ACCOUNT_ID_PLACEHOLDER, scan_for_secrets
from edullm_platform.phase1_evidence import DenialEvidence
from edullm_platform.publisher_denials import (
    AttemptedDenial,
    DenialNotProvenError,
    PublisherDenialReason,
    denial_evidence,
    record_denial,
    run_aws,
)
from tools.verify_admission_denials import (
    NOT_PROVEN_EXPLANATION,
    NOT_SET_UP_EXPLANATION,
    main,
)

ACCOUNT_ID = "123456789012"
REGION = "us-east-1"
ROLE_NAME = ADMISSION_ROLE_NAME
SESSION_NAME = "GitHubActions"
CALLER_ARN = f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/{ROLE_NAME}/{SESSION_NAME}"
STATE_MACHINE_ARN = (
    f"arn:aws:states:{REGION}:{ACCOUNT_ID}:stateMachine:{ADMISSION_STATE_MACHINE_NAME}"
)
ABSENT_STATE_MACHINE_ARN = f"{STATE_MACHINE_ARN}-denial-probe-absent"
PROBE_EXECUTION_ARN = (
    f"arn:aws:states:{REGION}:{ACCOUNT_ID}:execution:{ADMISSION_STATE_MACHINE_NAME}"
    ":edullm-denial-probe-absent-execution"
)
CLOUDTRAIL_EVENT_ID = "3f7c1a20-8b41-4d0e-9c6a-51d8e2f4b7a1"

#: S3's whole refusal. Its error table gives ``AccessDenied`` the description "Access
#: Denied" and no service is obliged to say more.
TERSE_REFUSAL = "Access Denied"

#: EC2's short form. Note what is not in it: the phrase "is not authorized to perform:",
#: which is what a message uses to name an action. This one names none, so it is read on
#: the code and the operation exactly as S3's is.
EC2_REFUSAL_PREFIX = "You are not authorized to perform this operation."

#: The blob EC2 staples to a refusal. It is not a credential -- only
#: ``sts:DecodeAuthorizationMessage`` can read it, and this session cannot call that --
#: but it is a long base64 run and the secret scan cannot tell the difference.
ENCODED_AUTHORIZATION_BLOB = "3Xj9" + "aBcDeFgH" * 20
EC2_REFUSAL = (
    f"{EC2_REFUSAL_PREFIX} Encoded authorization failure message: {ENCODED_AUTHORIZATION_BLOB}"
)

#: The six calls, in the order the matrix makes them, and the exact command each one is.
#: A seventh call, or a changed target, has to be argued for here before it can be made
#: against a live account.
EXPECTED_PROBES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "batch:SubmitJob",
        (
            "batch",
            "submit-job",
            "--region",
            REGION,
            "--job-name",
            "edullm-denial-probe",
            "--job-queue",
            "edullm-denial-probe-absent-queue",
            "--job-definition",
            "edullm-denial-probe-absent-job-definition",
        ),
    ),
    (
        "ec2:CreateKeyPair",
        (
            "ec2",
            "create-key-pair",
            "--dry-run",
            "--region",
            REGION,
            "--key-name",
            DENIAL_PROBE_KEY_PAIR_NAME,
        ),
    ),
    (
        "s3:PutObject",
        (
            "s3api",
            "put-object",
            "--region",
            REGION,
            "--bucket",
            LINEAGE_BUCKET,
            "--key",
            LINEAGE_PROBE_KEY,
            "--if-none-match",
            "*",
        ),
    ),
    (
        "states:StartExecution",
        (
            "stepfunctions",
            "start-execution",
            "--region",
            REGION,
            "--state-machine-arn",
            ABSENT_STATE_MACHINE_ARN,
            "--name",
            "edullm-denial-probe-must-not-start",
        ),
    ),
    (
        "states:StopExecution",
        (
            "stepfunctions",
            "stop-execution",
            "--region",
            REGION,
            "--execution-arn",
            PROBE_EXECUTION_ARN,
        ),
    ),
    (
        "iam:CreateRole",
        (
            "iam",
            "create-role",
            "--region",
            REGION,
            "--role-name",
            ROLE_NAME,
            "--assume-role-policy-document",
            json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {"Effect": "Deny", "Principal": {"AWS": "*"}, "Action": "sts:AssumeRole"}
                    ],
                },
                separators=(",", ":"),
            ),
        ),
    ),
)

#: What a run reports when every probe was refused, in matrix order. Three services, four
#: spellings: Batch and Step Functions say AccessDeniedException, IAM and S3 say
#: AccessDenied, and EC2 says something neither of the others would recognise.
REFUSED_EVERYTHING = (
    "denied:batch:SubmitJob:AccessDeniedException",
    "denied:ec2:CreateKeyPair:UnauthorizedOperation",
    "denied:s3:PutObject:AccessDenied",
    "denied:states:StartExecution:AccessDeniedException",
    "denied:states:StopExecution:AccessDeniedException",
    "denied:iam:CreateRole:AccessDenied",
)


def probes() -> tuple[AdmissionDenialProbe, ...]:
    return admission_denial_probes(
        region=REGION,
        state_machine=read_state_machine_arn(STATE_MACHINE_ARN, region=REGION),
        lineage_bucket=LINEAGE_BUCKET,
        role_name=ROLE_NAME,
    )


def probe_for(action: str) -> AdmissionDenialProbe:
    return next(probe for probe in probes() if probe.action == action)


def denial_message(action: str, *, resource: str = "an-absent-resource") -> str:
    return (
        f"User: {CALLER_ARN} is not authorized to perform: {action} "
        f"on resource: {resource} because no identity-based policy allows "
        f"the {action} action"
    )


def cli_error(operation: str, code: str, message: str) -> str:
    return f"\nAn error occurred ({code}) when calling the {operation} operation: {message}\n"


def denial_stderr(probe: AdmissionDenialProbe) -> str:
    """The refusal the service behind this probe would really send back.

    Each service in its own words, because a matrix that only ever sees one shape proves
    nothing about the others: S3 answers with two words and no attribution, EC2 answers
    with a code no other service uses and staples an encoded blob to it, and Batch, Step
    Functions and IAM spell the principal and the action out.
    """
    service = probe.action.split(":", 1)[0]
    if service == "s3":
        return cli_error(probe.operation, "AccessDenied", TERSE_REFUSAL)
    if service == "ec2":
        return cli_error(probe.operation, "UnauthorizedOperation", EC2_REFUSAL)
    code = "AccessDenied" if service == "iam" else "AccessDeniedException"
    resource = probe.resource_name or "an-absent-resource"
    return cli_error(probe.operation, code, denial_message(probe.action, resource=resource))


def refused(
    probe: AdmissionDenialProbe,
    stderr: str,
    *,
    returncode: int = 254,
) -> DenialNotProvenError:
    with pytest.raises(DenialNotProvenError) as exc_info:
        require_denial(probe, returncode=returncode, stderr=stderr)
    return exc_info.value


def attempt_record(probe: AdmissionDenialProbe, stderr: str) -> AttemptedDenial:
    return record_admission_denial(
        probe,
        require_denial(probe, returncode=254, stderr=stderr),
        region=REGION,
        role_name=ROLE_NAME,
        session_name=SESSION_NAME,
        attempted_at=datetime.now(tz=UTC),
    )


AwsRunner = Callable[..., "subprocess.CompletedProcess[str]"]


def aws_answering(answers: Mapping[str, tuple[int, str]]) -> AwsRunner:
    """An ``aws`` that names this session and refuses every probe but the ones given."""

    def run(
        arguments: Sequence[str],
        *,
        action: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if arguments[0] == "sts":
            return subprocess.CompletedProcess(list(arguments), 0, f"{CALLER_ARN}\n", "")
        assert action is not None
        returncode, stderr = answers.get(action, (254, denial_stderr(probe_for(action))))
        return subprocess.CompletedProcess(list(arguments), returncode, "", stderr)

    return run


def run_answering(
    monkeypatch: pytest.MonkeyPatch,
    answers: Mapping[str, tuple[int, str]] | None = None,
) -> admission_denials.AdmissionDenialMatrixRun:
    monkeypatch.setattr(admission_denials, "run_aws", aws_answering(answers or {}))
    return attempt_admission_denials(
        region=REGION,
        state_machine_arn=STATE_MACHINE_ARN,
        lineage_bucket=LINEAGE_BUCKET,
    )


# --------------------------------------------------------------------------------------
# What is attempted, and why each attempt could not do anything
# --------------------------------------------------------------------------------------


def test_the_matrix_attempts_every_action_the_role_must_not_hold() -> None:
    # The grant is states:StartExecution on one state machine ARN plus two read-only
    # execution actions. These six are the ways that grant could be wider than it reads:
    # compute through Batch, compute through EC2 directly, writing the lineage record
    # itself, starting some other state machine, aborting an admission mid-flight, and
    # minting a role.
    assert ADMISSION_DENIED_ACTIONS == (
        "batch:SubmitJob",
        "ec2:CreateKeyPair",
        "s3:PutObject",
        "states:StartExecution",
        "states:StopExecution",
        "iam:CreateRole",
    )
    assert tuple(probe.action for probe in probes()) == ADMISSION_DENIED_ACTIONS


def test_each_probe_is_exactly_the_command_it_is_recorded_as_making() -> None:
    # This is the AWS-call enumeration for the matrix. The commands do not appear in a
    # workflow run body, so nothing else in the repository can see what they are.
    assert tuple((probe.action, probe.arguments) for probe in probes()) == EXPECTED_PROBES


def test_no_probe_can_launch_compute_or_start_anything_if_the_deny_were_missing() -> None:
    # Five of the six are inert by construction and each for its own reason: the Batch
    # queue and job definition do not exist; the EC2 call is a dry run; the state machine
    # the start probe names does not exist; the execution the stop probe names was never
    # started; and the role name the create probe asks for is already taken, so a
    # permitted call collides rather than creating.
    arguments = {probe.action: probe.arguments for probe in probes()}

    submit = arguments["batch:SubmitJob"]
    assert "absent" in submit[submit.index("--job-queue") + 1]
    assert "absent" in submit[submit.index("--job-definition") + 1]

    assert "--dry-run" in arguments["ec2:CreateKeyPair"]
    assert "--no-dry-run" not in arguments["ec2:CreateKeyPair"]

    start = arguments["states:StartExecution"]
    assert start[start.index("--state-machine-arn") + 1].endswith("-denial-probe-absent")

    stop = arguments["states:StopExecution"]
    assert stop[stop.index("--execution-arn") + 1].endswith(":edullm-denial-probe-absent-execution")

    create = arguments["iam:CreateRole"]
    assert create[create.index("--role-name") + 1] == ROLE_NAME


def test_the_dry_run_flag_is_where_a_reader_cannot_miss_it() -> None:
    # It is the whole of what makes the one probe that could start a GPU instance
    # harmless, so it is the first argument after the operation rather than the last.
    create_key_pair = probe_for("ec2:CreateKeyPair")

    assert create_key_pair.arguments[:3] == ("ec2", "create-key-pair", "--dry-run")


def test_the_write_probe_sends_the_header_the_bucket_policy_requires() -> None:
    # Without it the lineage bucket's own Deny refuses the call for every principal in
    # the account, and S3 names no policy when it does, so the probe would have answered
    # AccessDenied on every run including the runs where the role could write freely.
    # This is the check that keeps the most important entry in the matrix honest.
    write = probe_for("s3:PutObject")
    index = write.arguments.index("--if-none-match")

    assert write.arguments[index + 1] == "*"
    assert write.arguments[write.arguments.index("--bucket") + 1] == LINEAGE_BUCKET


def test_the_write_probe_targets_the_real_bucket_and_no_lineage_prefix() -> None:
    # The bucket has to be the real one -- a made-up name is answered NoSuchBucket before
    # anybody is authorized -- so the key is what bounds the damage. A permitted write
    # lands under denial-probe/, where nothing reads it, rather than under one of the
    # three prefixes that are the lineage record itself.
    write = probe_for("s3:PutObject")
    key = write.arguments[write.arguments.index("--key") + 1]

    assert key == LINEAGE_PROBE_KEY
    assert key.startswith("denial-probe/")
    assert not any(key.startswith(prefix) for prefix in LINEAGE_RECORD_PREFIXES)
    assert "--body" not in write.arguments


def test_the_start_probe_names_a_state_machine_that_is_not_the_one_the_grant_names() -> None:
    # The grant is scoped to one machine, not to the service, and this is the difference
    # between those two claims. The name lands beside the real one rather than somewhere
    # unrelated, so a refusal is about this project's own resource namespace.
    start = probe_for("states:StartExecution")
    named = start.arguments[start.arguments.index("--state-machine-arn") + 1]

    assert named != STATE_MACHINE_ARN
    assert named.startswith(STATE_MACHINE_ARN)


def test_the_stop_probe_names_an_execution_of_the_admission_machine_that_never_ran() -> None:
    # The claim is that an approved submitter cannot abort an admission that is already
    # recording its decision, so the probe has to be aimed at this machine's executions
    # and not another's. It names one nothing mints, so a permitted stop stops nothing.
    stop = probe_for("states:StopExecution")
    named = stop.arguments[stop.arguments.index("--execution-arn") + 1]

    assert f":execution:{ADMISSION_STATE_MACHINE_NAME}:" in named
    assert ":stateMachine:" not in named
    assert named.endswith(":edullm-denial-probe-absent-execution")


def test_the_role_the_create_probe_asks_for_could_not_be_assumed_by_anyone() -> None:
    # The probe cannot create a role, because the name is taken. The trust document is
    # the second reason it would not matter if it could.
    create = probe_for("iam:CreateRole")
    document = json.loads(
        create.arguments[create.arguments.index("--assume-role-policy-document") + 1]
    )

    assert document["Statement"] == [
        {"Effect": "Deny", "Principal": {"AWS": "*"}, "Action": "sts:AssumeRole"}
    ]


def test_the_recorded_resource_is_a_name_rather_than_an_arn() -> None:
    # An ARN is a name with the account ID attached, and the account ID is the one value
    # the secret scan refuses. The argv carries ARNs because the API needs them; the
    # record carries the names out of them.
    for probe in probes():
        assert probe.resource_name is not None, probe.action
        assert not probe.resource_name.startswith("arn:"), probe.action
        assert ACCOUNT_ID not in probe.resource_name, probe.action


# --------------------------------------------------------------------------------------
# What counts as a refusal
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("action", ADMISSION_DENIED_ACTIONS)
def test_each_service_refusing_in_its_own_words_is_a_refusal(action: str) -> None:
    probe = probe_for(action)

    error = require_denial(probe, returncode=254, stderr=denial_stderr(probe))

    assert error.code in probe.authorization_error_codes
    assert error.operation == probe.operation


def test_a_refusal_that_says_only_access_denied_is_still_a_refusal() -> None:
    # S3's error table gives AccessDenied the description "Access Denied" and that is the
    # whole message. A classifier that required the text to name an action would fail on
    # the one probe the criterion is most about.
    write = probe_for("s3:PutObject")

    error = require_denial(
        write, returncode=254, stderr=cli_error("PutObject", "AccessDenied", TERSE_REFUSAL)
    )

    assert error.code == "AccessDenied"
    assert error.message == TERSE_REFUSAL


def test_ec2_refuses_in_a_word_no_other_service_in_this_matrix_uses() -> None:
    # UnauthorizedOperation is a refusal from EC2 and is not a refusal from anybody else,
    # so the code set belongs to the probe. Read with Phase 1's pair, the one probe that
    # stops CI launching compute directly could never have proved anything.
    create_key_pair = probe_for("ec2:CreateKeyPair")
    stderr = cli_error("CreateKeyPair", "UnauthorizedOperation", EC2_REFUSAL_PREFIX)

    assert require_denial(create_key_pair, returncode=254, stderr=stderr).code == (
        "UnauthorizedOperation"
    )
    assert "UnauthorizedOperation" in EC2_AUTHORIZATION_ERROR_CODES

    submit = probe_for("batch:SubmitJob")
    borrowed = cli_error("SubmitJob", "UnauthorizedOperation", "You are not authorized")

    assert refused(submit, borrowed).reason is (
        PublisherDenialReason.ATTEMPT_FAILED_FOR_ANOTHER_REASON
    )


def test_the_short_ec2_refusal_names_no_action_and_is_not_held_to_naming_one() -> None:
    # "You are not authorized to perform this operation." does not contain the phrase a
    # message uses to name an action, which ends in a colon. A check that matched the
    # phrase loosely would read this as a refusal of some other action.
    create_key_pair = probe_for("ec2:CreateKeyPair")

    error = require_denial(
        create_key_pair,
        returncode=254,
        stderr=cli_error("CreateKeyPair", "UnauthorizedOperation", EC2_REFUSAL_PREFIX),
    )

    assert error.message == EC2_REFUSAL_PREFIX


@pytest.mark.parametrize(
    "phrase",
    [
        "because no identity-based policy allows the states:StopExecution action",
        "with an explicit deny in an identity-based policy",
        "with an explicit deny in a permissions boundary",
        "because no permissions boundary allows the states:StopExecution action",
        "with an explicit deny in a service control policy",
    ],
)
def test_every_way_the_identity_itself_can_be_refused_is_a_denial(phrase: str) -> None:
    # The admission role carries InternSandboxBoundary, so a refusal may come from the
    # boundary rather than from the inline policy. Both are the identity being refused.
    stop = probe_for("states:StopExecution")
    message = f"User: {CALLER_ARN} is not authorized to perform: states:StopExecution {phrase}"

    assert require_denial(
        stop, returncode=254, stderr=cli_error("StopExecution", "AccessDeniedException", message)
    )


# --------------------------------------------------------------------------------------
# What does not count as a refusal
# --------------------------------------------------------------------------------------


def test_a_call_that_was_allowed_is_never_recorded_as_a_denial() -> None:
    write = probe_for("s3:PutObject")

    failure = refused(write, "", returncode=0)

    assert failure.reason is PublisherDenialReason.ATTEMPT_PERMITTED
    assert failure.action == "s3:PutObject"
    assert failure.error_code is None


def test_a_dry_run_that_would_have_succeeded_is_the_permission_being_present() -> None:
    # The one probe whose success does not look like success: --dry-run is what makes it
    # inert, and it is exactly what turns "you may launch instances" into a non-zero exit
    # with an error code. Reading it as a call that failed for some other reason would
    # report the worst outcome in the matrix as an inconclusive probe.
    create_key_pair = probe_for("ec2:CreateKeyPair")
    stderr = cli_error(
        "CreateKeyPair", "DryRunOperation", "Request would have succeeded, but DryRun flag is set"
    )

    failure = refused(create_key_pair, stderr)

    assert failure.reason is PublisherDenialReason.ATTEMPT_PERMITTED
    assert failure.error_code == "DryRunOperation"
    assert str(failure) == "attempt_permitted:ec2:CreateKeyPair:DryRunOperation"


def test_a_conditional_write_refused_by_the_object_already_existing_is_permission() -> None:
    # If-None-Match is evaluated after the request is authorized, so a 412 is a caller
    # who may write meeting an object that is already there. It is the same shape as the
    # dry run: an error that means the permission is present.
    write = probe_for("s3:PutObject")
    stderr = cli_error(
        "PutObject",
        "PreconditionFailed",
        "At least one of the pre-conditions you specified did not hold",
    )

    failure = refused(write, stderr)

    assert failure.reason is PublisherDenialReason.ATTEMPT_PERMITTED
    assert failure.error_code == "PreconditionFailed"


@pytest.mark.parametrize(
    ("action", "code", "message"),
    [
        # A typo in the bucket name is the case this whole check exists for: writing to a
        # bucket that exists but is not writable gives AccessDenied, and a bucket that is
        # not there gives NoSuchBucket, and only the first proves anything.
        ("s3:PutObject", "NoSuchBucket", "The specified bucket does not exist"),
        ("s3:PutObject", "NoSuchKey", "The specified key does not exist"),
        # Not-found, in each service's spelling. Every one of these stays inconclusive,
        # because whether it means the caller was permitted depends on whether the
        # service looks the resource up before or after it authorizes, and nobody has
        # measured that for these three. EC2 is the case that was measured, and it looks
        # up first -- which is why the EC2 probe names no resource at all.
        ("batch:SubmitJob", "ClientException", "Job queue does not exist"),
        ("states:StartExecution", "StateMachineDoesNotExist", "State Machine Does Not Exist"),
        ("states:StopExecution", "ExecutionDoesNotExist", "Execution Does Not Exist"),
        # Malformed: the service never reached the question of who was asking.
        ("iam:CreateRole", "MalformedPolicyDocument", "Syntax errors in policy."),
        ("states:StartExecution", "ValidationException", "1 validation error detected"),
        ("states:StopExecution", "InvalidArn", "Invalid Arn: 'Invalid ARN prefix'"),
        ("batch:SubmitJob", "ParamValidationError", "Invalid length for parameter"),
        # Already there: the create-role probe's own inert case, if IAM should turn out
        # to check the name before it checks the caller.
        ("iam:CreateRole", "EntityAlreadyExists", "Role with name already exists."),
        # Throttled: the service refused to answer, which is not the service refusing us.
        ("s3:PutObject", "SlowDown", "Please reduce your request rate."),
        ("states:StartExecution", "ThrottlingException", "Rate exceeded"),
        ("ec2:CreateKeyPair", "RequestLimitExceeded", "Request limit exceeded."),
        ("iam:CreateRole", "Throttling", "Rate exceeded"),
        # Credentials, which say nothing about what the credentials were allowed to do.
        # AuthFailure is EC2's, and it is the one code a reader might mistake for its
        # authorization failure: it means the credentials were not usable at all.
        ("ec2:CreateKeyPair", "AuthFailure", "AWS was not able to validate the credentials"),
        ("batch:SubmitJob", "ExpiredToken", "The security token included is expired"),
        ("states:StopExecution", "UnrecognizedClientException", "The security token is invalid"),
        # A server-side failure is not an answer at all.
        ("batch:SubmitJob", "ServerException", "Internal server error"),
        ("s3:PutObject", "InternalError", "We encountered an internal error"),
    ],
)
def test_a_failure_for_any_other_reason_is_a_permitted_action_wearing_a_failure(
    action: str,
    code: str,
    message: str,
) -> None:
    # The classifier reads the error code, so the way it keeps a failure out is by only
    # ever letting an authorization code in. Every code here is one an allowed call can
    # come back with, and none of them may be filed as a refusal.
    probe = probe_for(action)

    failure = refused(probe, cli_error(probe.operation, code, message))

    assert failure.reason is PublisherDenialReason.ATTEMPT_FAILED_FOR_ANOTHER_REASON
    assert failure.error_code == code


@pytest.mark.parametrize(
    "stderr",
    [
        "",
        "Could not connect to the endpoint URL: https://states.us-east-1.amazonaws.com/\n",
        "usage: aws [options] <command> <subcommand>\naws: error: the following arguments\n",
        # What a CLI too old to know --if-none-match answers with. The write probe cannot
        # be made without the header, so this has to fail closed rather than quietly.
        "Unknown options: --if-none-match, *\n",
        "An error occurred (Access Denied) when calling the PutObject operation: no\n",
    ],
)
def test_a_failure_that_is_not_an_aws_api_error_proves_nothing(stderr: str) -> None:
    # A network failure and a CLI usage error both leave the question unanswered, and the
    # last case is an error code that could not be one, so the text is not trusted.
    write = probe_for("s3:PutObject")

    failure = refused(write, stderr)

    assert failure.reason is PublisherDenialReason.ATTEMPT_FAILED_WITHOUT_AN_AWS_ERROR
    assert failure.error_code is None


def test_a_refusal_of_a_different_operation_is_not_a_refusal_of_this_one() -> None:
    # Operation identity is half of what the classifier rests on, and it is the half that
    # does the work now that a bare "Access Denied" is accepted: without it, any refusal
    # of anything would stand in for a refusal of this. DescribeExecution is the case
    # that matters here, because this role is allowed to call it.
    stop = probe_for("states:StopExecution")
    stderr = cli_error(
        "DescribeExecution", "AccessDeniedException", denial_message("states:StopExecution")
    )

    failure = refused(stop, stderr)

    assert failure.reason is PublisherDenialReason.ATTEMPT_CALLED_ANOTHER_OPERATION
    assert failure.error_code == "AccessDeniedException"


def test_a_refusal_that_names_another_action_is_not_a_refusal_of_this_one() -> None:
    # A message that does name an action is held to it. One that names none is taken on
    # the code and the operation, but a message cannot both speak and be ignored. The
    # trailing guard matters: StartExecutionAndMore contains StartExecution whole.
    start = probe_for("states:StartExecution")
    stderr = cli_error(
        "StartExecution",
        "AccessDeniedException",
        denial_message("states:StartExecutionAndMore"),
    )

    failure = refused(start, stderr)

    assert failure.reason is PublisherDenialReason.DENIAL_NAMED_ANOTHER_ACTION


@pytest.mark.parametrize(
    "message",
    [
        (
            f"User: {CALLER_ARN} is not authorized to perform: s3:PutObject on resource: "
            f'"arn:aws:s3:::{LINEAGE_BUCKET}/x" with an explicit deny in a resource-based '
            "policy"
        ),
        "Access Denied by the resource-based policy on the bucket",
    ],
)
def test_a_resource_refusing_us_is_not_the_role_being_refused(message: str) -> None:
    # The matrix claims something about the identity's permissions. The lineage bucket
    # has a policy of its own and it denies writes this probe is shaped to avoid, so a
    # refusal that says it came from that policy is the bucket answering rather than the
    # role being narrow. The second case is the same attribution without the long form,
    # because the check cannot depend on the wording.
    write = probe_for("s3:PutObject")

    failure = refused(write, cli_error("PutObject", "AccessDenied", message))

    assert failure.reason is PublisherDenialReason.DENIAL_CAME_FROM_A_RESOURCE_POLICY


def test_one_error_code_cannot_mean_both_refused_and_allowed() -> None:
    # The two sets decide opposite verdicts, so an overlap would leave the order of the
    # checks deciding whether the role is narrow or wide open.
    with pytest.raises(ValueError, match="both refused and allowed"):
        AdmissionDenialProbe(
            action="ec2:CreateKeyPair",
            operation="CreateKeyPair",
            event_source="ec2.amazonaws.com",
            resource_name=DENIAL_PROBE_KEY_PAIR_NAME,
            arguments=("ec2", "create-key-pair", "--dry-run"),
            authorization_error_codes=frozenset({"DryRunOperation"}),
            permitted_error_codes=frozenset({"DryRunOperation"}),
        )


def test_a_call_that_never_returns_is_not_a_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    # A hung call is the emptiest failure of all: nothing was decided by anybody.
    def hang(*_arguments: object, **_keywords: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["aws"], timeout=1)

    monkeypatch.setattr(subprocess, "run", hang)

    with pytest.raises(DenialNotProvenError) as exc_info:
        run_aws(("s3api", "put-object"), action="s3:PutObject")

    assert exc_info.value.reason is PublisherDenialReason.ATTEMPT_TIMED_OUT
    assert exc_info.value.action == "s3:PutObject"


# --------------------------------------------------------------------------------------
# What a refusal is written down as
# --------------------------------------------------------------------------------------


def test_the_recorded_message_carries_no_account_identifier() -> None:
    stop = probe_for("states:StopExecution")

    record = attempt_record(stop, denial_stderr(stop))

    assert ACCOUNT_ID not in record.error_message
    assert AWS_ACCOUNT_ID_PLACEHOLDER in record.error_message
    assert f"assumed-role/{ROLE_NAME}" in record.error_message
    assert scan_for_secrets(record.error_message) == record.error_message


def test_the_encoded_authorization_failure_message_is_masked_rather_than_withheld() -> None:
    # The blob is not a credential, but the scan cannot tell a long base64 run from one,
    # so Phase 1's recording refuses the whole message and the refusal goes unrecorded.
    # Masking it by the label that introduces it keeps the part of the message that says
    # who was refused and for what.
    create_key_pair = probe_for("ec2:CreateKeyPair")
    error = require_denial(create_key_pair, returncode=254, stderr=denial_stderr(create_key_pair))

    with pytest.raises(DenialNotProvenError) as unmasked:
        record_denial(
            create_key_pair,
            error,
            region=REGION,
            role_name=ROLE_NAME,
            session_name=SESSION_NAME,
            attempted_at=datetime.now(tz=UTC),
        )

    assert unmasked.value.reason is PublisherDenialReason.DENIAL_MESSAGE_HOLDS_A_CREDENTIAL

    record = attempt_record(create_key_pair, denial_stderr(create_key_pair))

    assert ENCODED_AUTHORIZATION_BLOB not in record.error_message
    assert "<encoded-authorization-failure-message>" in record.error_message
    assert record.error_message.startswith(EC2_REFUSAL_PREFIX)
    assert scan_for_secrets(record.error_message) == record.error_message


def test_masking_the_encoded_message_leaves_everything_else_alone() -> None:
    assert mask_encoded_authorization_failure("nothing to mask here") == "nothing to mask here"
    assert mask_encoded_authorization_failure(EC2_REFUSAL) == (
        f"{EC2_REFUSAL_PREFIX} Encoded authorization failure message: "
        "<encoded-authorization-failure-message>"
    )


def test_a_message_carrying_a_credential_is_refused_rather_than_laundered() -> None:
    # redact_aws_account_ids will not mask text that holds another credential, because
    # masking twelve digits inside a secret access key would break the run that
    # identifies it and leave a live credential the scan then accepts.
    start = probe_for("states:StartExecution")
    leaked = "A" * 20 + "b" * 20
    message = f"{denial_message('states:StartExecution')} using {leaked}"

    with pytest.raises(DenialNotProvenError) as exc_info:
        record_admission_denial(
            start,
            require_denial(
                start,
                returncode=254,
                stderr=cli_error("StartExecution", "AccessDeniedException", message),
            ),
            region=REGION,
            role_name=ROLE_NAME,
            session_name=SESSION_NAME,
            attempted_at=datetime.now(tz=UTC),
        )

    assert exc_info.value.reason is PublisherDenialReason.DENIAL_MESSAGE_HOLDS_A_CREDENTIAL
    assert leaked not in str(exc_info.value)


def test_the_record_names_the_action_the_call_and_the_service_that_refused_it() -> None:
    write = probe_for("s3:PutObject")

    record = attempt_record(write, denial_stderr(write))

    assert record.attempted_action == "s3:PutObject"
    assert record.attempted_resource == f"{LINEAGE_BUCKET}/{LINEAGE_PROBE_KEY}"
    assert record.outcome == "denied"
    assert record.error_code == "AccessDenied"
    assert record.error_message == TERSE_REFUSAL
    assert record.event_name == "PutObject"
    assert record.event_source == "s3.amazonaws.com"
    assert record.role_name == ROLE_NAME
    assert record.session_name == SESSION_NAME
    assert record.region == REGION


def test_a_phase_two_refusal_is_one_cloudtrail_lookup_away_from_phase_evidence() -> None:
    # The record is Phase 1's unchanged, so the seam that completes an attempt into
    # evidence once a capture with CloudTrail credentials has looked the event up still
    # closes. The admission session cannot read CloudTrail either.
    stop = probe_for("states:StopExecution")
    record = attempt_record(stop, denial_stderr(stop))

    evidence = denial_evidence(
        record, event_id=CLOUDTRAIL_EVENT_ID, observed_at=datetime.now(tz=UTC)
    )

    assert isinstance(evidence, DenialEvidence)
    assert evidence.attempted_action == "states:StopExecution"
    assert evidence.event_id == CLOUDTRAIL_EVENT_ID
    assert evidence.outcome == "denied"


def test_a_matrix_record_that_is_missing_an_attempt_is_not_a_matrix() -> None:
    # A run that proved five of the six refusals proved five of them, and a file that
    # could hold the five would be read as if it had proved all six.
    records = [attempt_record(probe, denial_stderr(probe)) for probe in probes()]
    complete = AdmissionDenialMatrix(schema_version=1, attempts=tuple(records))

    assert (
        tuple(attempt.attempted_action for attempt in complete.attempts) == ADMISSION_DENIED_ACTIONS
    )

    with pytest.raises(ValidationError):
        AdmissionDenialMatrix(schema_version=1, attempts=tuple(records[:-1]))
    with pytest.raises(ValidationError):
        AdmissionDenialMatrix(schema_version=1, attempts=tuple(reversed(records)))


# --------------------------------------------------------------------------------------
# One run says everything that is wrong
# --------------------------------------------------------------------------------------


def test_a_run_that_refused_everything_is_the_matrix_the_record_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = run_answering(monkeypatch)

    assert run.proven
    assert run.summary == REFUSED_EVERYTHING
    assert (
        tuple(attempt.attempted_action for attempt in run.matrix().attempts)
        == ADMISSION_DENIED_ACTIONS
    )


def test_every_action_is_attempted_even_after_one_of_them_proves_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reaching this account costs a workflow run under a real OIDC session, which is not
    # something a developer can do in a loop. A matrix that stopped at its first surprise
    # would turn one run into one fact.
    run = run_answering(
        monkeypatch,
        {
            "ec2:CreateKeyPair": (
                254,
                cli_error("CreateKeyPair", "DryRunOperation", "Request would have succeeded"),
            ),
            "s3:PutObject": (
                254,
                cli_error("PutObject", "NoSuchBucket", "The specified bucket does not exist"),
            ),
        },
    )

    assert not run.proven
    assert run.summary == (
        REFUSED_EVERYTHING[0],
        "attempt_permitted:ec2:CreateKeyPair:DryRunOperation",
        "attempt_failed_for_another_reason:s3:PutObject:NoSuchBucket",
        REFUSED_EVERYTHING[3],
        REFUSED_EVERYTHING[4],
        REFUSED_EVERYTHING[5],
    )
    assert tuple(outcome.action for outcome in run.outcomes) == ADMISSION_DENIED_ACTIONS


def test_a_run_that_did_not_refuse_everything_has_no_matrix_to_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = run_answering(monkeypatch, {"s3:PutObject": (0, "")})

    with pytest.raises(ValueError, match="refuse"):
        run.matrix()


def test_the_summary_of_a_run_repeats_nothing_the_service_said(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The summary is what a public runner log gets. A denial message names the account
    # and often the resource, and the reason token carries the action and the code only.
    run = run_answering(
        monkeypatch,
        {
            "states:StartExecution": (
                254,
                cli_error(
                    "StartExecution",
                    "StateMachineDoesNotExist",
                    f"State machine in account {ACCOUNT_ID} secret-detail-canary",
                ),
            )
        },
    )
    summary = "\n".join(run.summary)

    assert "attempt_failed_for_another_reason:states:StartExecution:StateMachineDoesNotExist" in (
        summary
    )
    assert ACCOUNT_ID not in summary
    assert "secret-detail-canary" not in summary
    assert scan_for_secrets(summary) == summary


# --------------------------------------------------------------------------------------
# What has to be true before anything is attempted
# --------------------------------------------------------------------------------------


def test_the_state_machine_arn_is_read_for_what_the_probes_derive_from_it() -> None:
    machine = read_state_machine_arn(STATE_MACHINE_ARN, region=REGION)

    assert machine.name == ADMISSION_STATE_MACHINE_NAME
    assert machine.region == REGION
    assert machine.another_state_machine_arn == ABSENT_STATE_MACHINE_ARN
    assert machine.execution_arn("edullm-denial-probe-absent-execution") == PROBE_EXECUTION_ARN


@pytest.mark.parametrize(
    ("arn", "reason"),
    [
        ("", AdmissionSetupReason.STATE_MACHINE_ARN_UNUSABLE),
        ("not-an-arn", AdmissionSetupReason.STATE_MACHINE_ARN_UNUSABLE),
        (
            f"arn:aws:states:{REGION}:{ACCOUNT_ID}:execution:{ADMISSION_STATE_MACHINE_NAME}:one",
            AdmissionSetupReason.STATE_MACHINE_ARN_UNUSABLE,
        ),
        (
            f"arn:aws:states:{REGION}:{ACCOUNT_ID}:stateMachine:somebody-elses-machine",
            AdmissionSetupReason.STATE_MACHINE_ARN_NAMES_ANOTHER_MACHINE,
        ),
        (
            f"arn:aws:states:us-west-2:{ACCOUNT_ID}:stateMachine:{ADMISSION_STATE_MACHINE_NAME}",
            AdmissionSetupReason.STATE_MACHINE_ARN_IS_IN_ANOTHER_REGION,
        ),
    ],
)
def test_an_arn_that_is_not_the_deployed_admission_machine_stops_the_run(
    arn: str,
    reason: AdmissionSetupReason,
) -> None:
    # None of these says anything about how wide the role is. An ARN in another region
    # would point every probe at resources that are not there, which is answered by
    # absence rather than by authorization -- the way a matrix passes while proving
    # nothing.
    with pytest.raises(AdmissionSetupError) as exc_info:
        read_state_machine_arn(arn, region=REGION)

    assert exc_info.value.reason is reason
    assert ACCOUNT_ID not in str(exc_info.value)


@pytest.mark.parametrize(
    "bucket",
    ["", "Not A Bucket", "s3://sbsandbox-intern-edullm-lineage", "someone-elses-bucket"],
)
def test_a_write_probe_aimed_anywhere_but_this_project_stops_the_run(bucket: str) -> None:
    # A bucket that is not there is answered NoSuchBucket, and a bucket belonging to
    # another team in this shared sandbox answers out of their policy. Neither is a fact
    # about this role.
    with pytest.raises(AdmissionSetupError) as exc_info:
        read_lineage_bucket(bucket)

    assert exc_info.value.reason is AdmissionSetupReason.LINEAGE_BUCKET_UNUSABLE


def test_the_deployed_lineage_bucket_is_accepted() -> None:
    assert read_lineage_bucket(LINEAGE_BUCKET) == LINEAGE_BUCKET


def test_the_session_is_read_and_checked_before_a_probe_is_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(admission_denials, "run_aws", aws_answering({}))

    assert caller_identity(region=REGION) == (ROLE_NAME, SESSION_NAME)


@pytest.mark.parametrize(
    ("arn", "returncode", "reason"),
    [
        (
            f"arn:aws:iam::{ACCOUNT_ID}:user/somebody",
            0,
            AdmissionSetupReason.CALLER_IS_NOT_AN_ASSUMED_ROLE,
        ),
        (
            (
                f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/"
                f"sbsandbox-intern-edullm-ecr-publisher/{SESSION_NAME}"
            ),
            0,
            AdmissionSetupReason.CALLER_IS_NOT_THE_ADMISSION_ROLE,
        ),
        (CALLER_ARN, 254, AdmissionSetupReason.CALLER_IDENTITY_UNREADABLE),
    ],
)
def test_a_session_that_is_not_this_role_attempts_nothing(
    monkeypatch: pytest.MonkeyPatch,
    arn: str,
    returncode: int,
    reason: AdmissionSetupReason,
) -> None:
    # The matrix is a claim about one named role. Under any other session every probe
    # would be refused and the run would report the admission role as narrow without
    # having tested it once -- and the sandbox is shared, so another session is not a
    # hypothetical. The reason never carries the role name, because per-person roles in
    # this account carry personal names and this text reaches a public log.
    attempted: list[Sequence[str]] = []

    def answer(
        arguments: Sequence[str],
        *,
        action: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        attempted.append(arguments)
        assert arguments[0] == "sts", "no probe may run without an identity"
        return subprocess.CompletedProcess(list(arguments), returncode, f"{arn}\n", "")

    monkeypatch.setattr(admission_denials, "run_aws", answer)

    with pytest.raises(AdmissionSetupError) as exc_info:
        attempt_admission_denials(
            region=REGION,
            state_machine_arn=STATE_MACHINE_ARN,
            lineage_bucket=LINEAGE_BUCKET,
        )

    assert exc_info.value.reason is reason
    assert len(attempted) == 1
    assert "publisher" not in str(exc_info.value)


def test_a_runner_without_the_aws_cli_is_a_setup_failure_rather_than_a_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(
        _arguments: Sequence[str],
        *,
        action: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        raise DenialNotProvenError(PublisherDenialReason.AWS_CLI_UNAVAILABLE, action=action)

    monkeypatch.setattr(admission_denials, "run_aws", missing)

    with pytest.raises(AdmissionSetupError) as exc_info:
        caller_identity(region=REGION)

    assert exc_info.value.reason is AdmissionSetupReason.AWS_CLI_UNAVAILABLE


# --------------------------------------------------------------------------------------
# The command the submission workflow runs
# --------------------------------------------------------------------------------------

#: Every command the tool may run against a live account, in order.
EXPECTED_CALLS = (
    f"sts get-caller-identity --region {REGION} --query Arn --output text",
    *(" ".join(arguments) for _action, arguments in EXPECTED_PROBES),
)


def denial_body(probe: AdmissionDenialProbe) -> str:
    error = denial_stderr(probe).strip()
    return f"printf '%s\\n' '{error}' >&2; exit 254"


def failing_body(operation: str, code: str, message: str) -> str:
    error = f"An error occurred ({code}) when calling the {operation} operation: {message}"
    return f"printf '%s\\n' '{error}' >&2; exit 254"


def install_aws_stub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    answers: dict[str, str] | None = None,
    caller_arn: str = CALLER_ARN,
    caller_status: int = 0,
) -> Path:
    """Put an ``aws`` on PATH that answers each probe, recording every call it is given."""
    overrides = answers or {}
    recording = tmp_path / "aws-calls.txt"
    branches = [
        f"  \"sts get-caller-identity\") printf '%s\\n' '{caller_arn}'; exit {caller_status} ;;"
    ]
    for probe in probes():
        key = f"{probe.arguments[0]} {probe.arguments[1]}"
        branches.append(f'  "{key}") {overrides.get(probe.action, denial_body(probe))} ;;')
    stub_bin = tmp_path / "bin"
    write_stub(
        stub_bin,
        "aws",
        f"printf '%s\\n' \"$*\" >> '{recording}'\n"
        'case "${1-} ${2-}" in\n' + "\n".join(branches) + "\n  *) exit 64 ;;\nesac\n",
    )
    monkeypatch.setenv("PATH", f"{stub_bin}{os.pathsep}{os.defpath}")
    return recording


def argv(tmp_path: Path, **overrides: str) -> list[str]:
    arguments: dict[str, str] = {
        "--region": REGION,
        "--state-machine-arn": STATE_MACHINE_ARN,
        "--output": str(tmp_path / "admission-denials.json"),
    }
    arguments.update(overrides)
    return [token for pair in arguments.items() for token in pair]


def recorded(recording: Path) -> list[str]:
    if not recording.exists():
        return []
    return recording.read_text(encoding="utf-8").splitlines()


def test_a_session_refused_everything_writes_the_matrix_and_says_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recording = install_aws_stub(tmp_path, monkeypatch)

    exit_code = main(argv(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == ""
    assert captured.err == ""
    written = (tmp_path / "admission-denials.json").read_text(encoding="utf-8")
    assert written.endswith("\n")
    assert ", " not in written and '": ' not in written
    matrix = json.loads(written)
    assert matrix["schema_version"] == 1
    assert (
        tuple(attempt["attempted_action"] for attempt in matrix["attempts"])
        == ADMISSION_DENIED_ACTIONS
    )
    assert recorded(recording) == list(EXPECTED_CALLS)


def test_the_write_probe_reaches_the_real_bucket_with_the_header_it_needs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The argv actually run, not the argv the module intended. Everything about this
    # probe being able to prove anything is in this one line.
    recording = install_aws_stub(tmp_path, monkeypatch)

    assert main(argv(tmp_path)) == 0

    expected = (
        f"s3api put-object --region {REGION} --bucket {LINEAGE_BUCKET} "
        f"--key {LINEAGE_PROBE_KEY} --if-none-match *"
    )

    assert [call for call in recorded(recording) if call.startswith("s3api")] == [expected]


def test_nothing_the_account_said_about_itself_survives_into_the_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The denial messages name the account, the role and the resource, and EC2's carries
    # an encoded blob the scan cannot distinguish from a credential. Only the account is
    # a secret; the blob is masked because keeping it would cost the whole message.
    install_aws_stub(tmp_path, monkeypatch)

    assert main(argv(tmp_path)) == 0
    captured = capsys.readouterr()

    written = (tmp_path / "admission-denials.json").read_text(encoding="utf-8")
    assert ACCOUNT_ID not in written
    assert ACCOUNT_ID not in captured.out + captured.err
    assert ENCODED_AUTHORIZATION_BLOB not in written
    assert AWS_ACCOUNT_ID_PLACEHOLDER in written
    assert "<encoded-authorization-failure-message>" in written
    assert f"assumed-role/{ROLE_NAME}" in written
    assert scan_for_secrets(written) == written


def test_an_action_that_was_allowed_stops_the_run_instead_of_being_recorded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The whole matrix exists for this case, and this is the entry it exists for most:
    # a role that can write the lineage record itself turns a statement by the platform
    # into a statement by its caller.
    install_aws_stub(tmp_path, monkeypatch, answers={"s3:PutObject": "exit 0"})

    exit_code = main(argv(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.err.splitlines() == [
        REFUSED_EVERYTHING[0],
        REFUSED_EVERYTHING[1],
        "attempt_permitted:s3:PutObject",
        REFUSED_EVERYTHING[3],
        REFUSED_EVERYTHING[4],
        REFUSED_EVERYTHING[5],
        NOT_PROVEN_EXPLANATION,
    ]
    assert captured.out == ""
    assert not (tmp_path / "admission-denials.json").exists()


def test_a_dry_run_that_would_have_launched_an_instance_stops_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Exit status 254 and an error message, and it means the role can launch GPU
    # instances. The tool has to end at 1 rather than treat it as an unclear answer.
    install_aws_stub(
        tmp_path,
        monkeypatch,
        answers={
            "ec2:CreateKeyPair": failing_body(
                "CreateKeyPair",
                "DryRunOperation",
                "Request would have succeeded, but DryRun flag is set",
            )
        },
    )

    exit_code = main(argv(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.err.splitlines()[1] == "attempt_permitted:ec2:CreateKeyPair:DryRunOperation"
    assert not (tmp_path / "admission-denials.json").exists()


def test_one_run_reports_every_probe_rather_than_the_first_that_went_wrong(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recording = install_aws_stub(
        tmp_path,
        monkeypatch,
        answers={
            "batch:SubmitJob": failing_body("SubmitJob", "ClientException", "does not exist"),
            "iam:CreateRole": "exit 0",
        },
    )

    exit_code = main(argv(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.err.splitlines() == [
        "attempt_failed_for_another_reason:batch:SubmitJob:ClientException",
        REFUSED_EVERYTHING[1],
        REFUSED_EVERYTHING[2],
        REFUSED_EVERYTHING[3],
        REFUSED_EVERYTHING[4],
        "attempt_permitted:iam:CreateRole",
        NOT_PROVEN_EXPLANATION,
    ]
    assert recorded(recording) == list(EXPECTED_CALLS)
    assert not (tmp_path / "admission-denials.json").exists()


@pytest.mark.parametrize(
    ("code", "message", "reason"),
    [
        (
            "NoSuchBucket",
            "The specified bucket does not exist",
            "attempt_failed_for_another_reason:s3:PutObject:NoSuchBucket",
        ),
        (
            "InvalidRequest",
            "1 validation error detected: value at key failed to satisfy",
            "attempt_failed_for_another_reason:s3:PutObject:InvalidRequest",
        ),
        (
            "SlowDown",
            "Please reduce your request rate.",
            "attempt_failed_for_another_reason:s3:PutObject:SlowDown",
        ),
    ],
)
def test_a_failure_for_the_wrong_reason_is_never_filed_as_a_denial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    code: str,
    message: str,
    reason: str,
) -> None:
    # A not-found says the call was authorized and the bucket was absent, a malformed
    # parameter says the service never got as far as deciding, and a throttle says it
    # declined to answer. None is a refusal of this identity, and the first is what a
    # typo in the bucket name looks like.
    canary = f"{message} secret-detail-canary"
    install_aws_stub(
        tmp_path,
        monkeypatch,
        answers={"s3:PutObject": failing_body("PutObject", code, canary)},
    )

    exit_code = main(argv(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.err.splitlines()[2] == reason
    assert "secret-detail-canary" not in captured.out + captured.err
    assert not (tmp_path / "admission-denials.json").exists()


def test_a_bucket_that_is_not_this_project_is_refused_before_any_credential_is_used(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recording = install_aws_stub(tmp_path, monkeypatch)

    exit_code = main(argv(tmp_path, **{"--lineage-bucket": "somebody-elses-lineage"}))
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err.splitlines() == ["lineage_bucket_unusable", NOT_SET_UP_EXPLANATION]
    assert recorded(recording) == []


@pytest.mark.parametrize(
    ("state_machine_arn", "reason"),
    [
        ("arn:aws:states:us-east-1:not-an-account:stateMachine:x", "state_machine_arn_unusable"),
        (
            f"arn:aws:states:{REGION}:{ACCOUNT_ID}:stateMachine:somebody-elses-machine",
            "state_machine_arn_names_another_machine",
        ),
        (
            f"arn:aws:states:eu-west-1:{ACCOUNT_ID}:stateMachine:{ADMISSION_STATE_MACHINE_NAME}",
            "state_machine_arn_is_in_another_region",
        ),
    ],
)
def test_an_argument_that_does_not_describe_the_deployed_machine_is_a_setup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    state_machine_arn: str,
    reason: str,
) -> None:
    recording = install_aws_stub(tmp_path, monkeypatch)

    exit_code = main(argv(tmp_path, **{"--state-machine-arn": state_machine_arn}))
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err.splitlines() == [reason, NOT_SET_UP_EXPLANATION]
    assert ACCOUNT_ID not in captured.err
    assert recorded(recording) == []


def test_a_session_that_is_not_the_admission_role_is_a_setup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recording = install_aws_stub(
        tmp_path,
        monkeypatch,
        caller_arn=(
            f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/"
            f"sbsandbox-intern-edullm-deployer/{SESSION_NAME}"
        ),
    )

    exit_code = main(argv(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err.splitlines() == [
        "caller_is_not_the_admission_role",
        NOT_SET_UP_EXPLANATION,
    ]
    assert "deployer" not in captured.err
    assert recorded(recording) == [EXPECTED_CALLS[0]]
    assert not (tmp_path / "admission-denials.json").exists()


def test_a_runner_without_the_aws_cli_proves_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    exit_code = main(argv(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err.splitlines() == ["aws_cli_unavailable", NOT_SET_UP_EXPLANATION]
    assert not (tmp_path / "admission-denials.json").exists()


def test_a_record_that_cannot_be_written_is_an_environment_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_aws_stub(tmp_path, monkeypatch)

    exit_code = main(argv(tmp_path, **{"--output": str(tmp_path / "absent" / "denials.json")}))

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "output_unwritable"


def test_a_command_line_that_is_missing_an_argument_is_a_usage_error() -> None:
    # argparse exits 2 for a missing required argument, which is the same status this
    # tool uses for every other way a run could not be set up. One number, one meaning.
    with pytest.raises(SystemExit) as exc_info:
        main(["--region", REGION])

    assert exc_info.value.code == 2


def test_the_write_probe_defaults_to_the_deployed_lineage_bucket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The bucket name is a constant in a committed template, unlike the state machine ARN
    # which carries the account, so it defaults rather than being passed in and mistyped.
    recording = install_aws_stub(tmp_path, monkeypatch)

    assert main(argv(tmp_path)) == 0

    assert any(f"--bucket {LINEAGE_BUCKET} " in call for call in recorded(recording))


# --------------------------------------------------------------------------------------
# What choosing a Phase 2 probe has cost, written where the next person will look
# --------------------------------------------------------------------------------------


def test_every_probe_lesson_names_what_taught_it() -> None:
    # A rule with no incident attached reads as caution and gets skipped.
    assert ADMISSION_PROBE_LESSONS
    for lesson in ADMISSION_PROBE_LESSONS:
        assert lesson.rule.strip()
        assert lesson.learned_from.strip()
        assert lesson.detail.strip()


def test_the_resource_policy_lesson_says_which_way_that_probe_would_have_failed() -> None:
    # The half that matters. An unconditional write is refused by the bucket for
    # everybody, so the probe would have reported the role as narrow on the runs where it
    # had been widened -- a flake that always fails towards passing.
    lesson = ADMISSION_PROBE_LESSONS[0]

    assert "if-none-match" in lesson.learned_from
    assert "widened" in lesson.detail
    assert "Access Denied" in lesson.learned_from


def test_the_vocabulary_lesson_records_both_words_ec2_uses() -> None:
    # One code for a refusal that no other service here uses, and one for a permitted
    # call that arrives as an error. Naming both is what lets somebody recognise it again.
    lesson = ADMISSION_PROBE_LESSONS[1]

    assert "UnauthorizedOperation" in lesson.learned_from
    assert "DryRunOperation" in lesson.learned_from
    assert "inert" in lesson.detail


def test_the_inertness_lesson_says_what_it_could_not_bound() -> None:
    # A lesson that recorded only the mitigation would read as though nothing had been
    # traded. What is left is a stray object somebody has to notice and remove.
    lesson = ADMISSION_PROBE_LESSONS[2]

    assert "no default retention rule" in lesson.detail
    assert "It can be removed" in lesson.detail
    assert "rejected" in lesson.detail
