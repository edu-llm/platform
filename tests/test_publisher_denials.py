"""The publisher denial matrix: what is attempted, and what counts as a refusal.

The distinction every test here circles is that a call which failed is not a call which
was denied. A not-found, a malformed parameter and a timeout are all failures, and every
one of them is what a *permitted* action looks like when it is pointed at something that
is not there. Recording any of them as a denial would let the widening this matrix exists
to catch pass as proof that it did not happen.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from edullm_platform.evidence import AWS_ACCOUNT_ID_PLACEHOLDER, scan_for_secrets
from edullm_platform.phase1_evidence import DenialEvidence
from edullm_platform.publisher_denials import (
    EVIDENCE_ONLY_FIELDS,
    PUBLISHER_DENIED_ACTIONS,
    AttemptedDenial,
    DenialNotProvenError,
    DenialProbe,
    PublisherDenialMatrix,
    PublisherDenialReason,
    assumed_role_identity,
    denial_evidence,
    denial_probes,
    record_denial,
    require_denial,
)

ACCOUNT_ID = "123456789012"
REGION = "us-east-1"
ROLE_NAME = "sbsandbox-intern-edullm-ecr-publisher"
SESSION_NAME = "GitHubActions"
ECR_REPOSITORY = "sbsandbox-intern-edullm-olmo-core"
CALLER_ARN = f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/{ROLE_NAME}/{SESSION_NAME}"
CLOUDTRAIL_EVENT_ID = "8c5a1e5e-0f2a-4b1e-9a3d-2b7c9f0e1d34"

#: The five calls, in the order the matrix makes them, and the exact command each one is.
#: A sixth call, or a changed target, has to be argued for here before it can be made
#: against a live account.
EXPECTED_PROBES = (
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
        "s3:GetObject",
        (
            "s3api",
            "get-object",
            "--region",
            REGION,
            "--bucket",
            "sbsandbox-intern-edullm-denial-probe-absent-bucket",
            "--key",
            "denial-probe/absent-object",
            "/dev/null",
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
    (
        "batch:UpdateComputeEnvironment",
        (
            "batch",
            "update-compute-environment",
            "--region",
            REGION,
            "--compute-environment",
            "edullm-denial-probe-absent-compute-environment",
        ),
    ),
    (
        "ecr:DeleteRepository",
        (
            "ecr",
            "delete-repository",
            "--region",
            REGION,
            "--repository-name",
            f"{ECR_REPOSITORY}-denial-probe-absent",
        ),
    ),
)


def probes() -> tuple[DenialProbe, ...]:
    return denial_probes(region=REGION, ecr_repository=ECR_REPOSITORY, role_name=ROLE_NAME)


def probe_for(action: str) -> DenialProbe:
    return next(probe for probe in probes() if probe.action == action)


def denial_message(action: str, *, resource: str = "an-absent-resource") -> str:
    return (
        f"User: {CALLER_ARN} is not authorized to perform: {action} "
        f"on resource: {resource} because no identity-based policy allows "
        f"the {action} action"
    )


def cli_error(operation: str, code: str, message: str) -> str:
    return f"\nAn error occurred ({code}) when calling the {operation} operation: {message}\n"


def denial_stderr(probe: DenialProbe, *, code: str = "AccessDeniedException") -> str:
    return cli_error(probe.operation, code, denial_message(probe.action))


def refused(probe: DenialProbe, stderr: str, *, returncode: int = 254) -> DenialNotProvenError:
    with pytest.raises(DenialNotProvenError) as exc_info:
        require_denial(probe, returncode=returncode, stderr=stderr)
    return exc_info.value


def attempt_record(probe: DenialProbe, stderr: str) -> AttemptedDenial:
    return record_denial(
        probe,
        require_denial(probe, returncode=254, stderr=stderr),
        region=REGION,
        role_name=ROLE_NAME,
        session_name=SESSION_NAME,
        attempted_at=datetime.now(tz=UTC),
    )


def test_the_matrix_attempts_every_action_the_criterion_names() -> None:
    # Criterion 6 is "the publisher role cannot submit jobs, read datasets, alter IAM, or
    # modify Batch". The fifth is the one the criterion does not name: the role publishes
    # images and must not be able to delete a repository holding them.
    assert PUBLISHER_DENIED_ACTIONS == (
        "batch:SubmitJob",
        "s3:GetObject",
        "iam:CreateRole",
        "batch:UpdateComputeEnvironment",
        "ecr:DeleteRepository",
    )
    assert tuple(probe.action for probe in probes()) == PUBLISHER_DENIED_ACTIONS


def test_each_probe_is_exactly_the_command_it_is_recorded_as_making() -> None:
    # This is the AWS-call enumeration for the matrix. The commands do not appear in a
    # workflow run body, so nothing else in the repository can see what they are.
    assert tuple((probe.action, probe.arguments) for probe in probes()) == EXPECTED_PROBES


def test_no_probe_can_change_anything_if_the_deny_were_missing() -> None:
    # Every target is a resource that is not there, so a permitted call fails on the
    # resource rather than doing something. The two exceptions are deliberate and each
    # one is inert for its own reason: create-role names a role that already exists, so
    # a permitted call collides instead of creating; and delete-repository names a
    # repository beside the registered one rather than the registered one itself,
    # because a permitted delete of that would take the images with it.
    arguments = {probe.action: probe.arguments for probe in probes()}

    assert (
        "absent"
        in arguments["batch:SubmitJob"][arguments["batch:SubmitJob"].index("--job-queue") + 1]
    )
    assert "absent" in arguments["s3:GetObject"][arguments["s3:GetObject"].index("--bucket") + 1]
    assert arguments["iam:CreateRole"][arguments["iam:CreateRole"].index("--role-name") + 1] == (
        ROLE_NAME
    )
    assert "absent" in arguments["batch:UpdateComputeEnvironment"][-1]
    assert arguments["ecr:DeleteRepository"][-1] != ECR_REPOSITORY
    assert arguments["ecr:DeleteRepository"][-1].startswith(ECR_REPOSITORY)

    # --force is what turns a delete of a repository that holds images from a refusal
    # into a deletion, and nothing here may carry it.
    assert not any("--force" in probe.arguments for probe in probes())


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


def test_a_refusal_that_names_the_action_is_the_denial_this_matrix_is_looking_for() -> None:
    probe = probe_for("batch:SubmitJob")

    error = require_denial(probe, returncode=254, stderr=denial_stderr(probe))

    assert error.code == "AccessDeniedException"
    assert error.operation == "SubmitJob"
    assert "is not authorized to perform: batch:SubmitJob" in error.message


@pytest.mark.parametrize("code", ["AccessDenied", "AccessDeniedException"])
def test_both_spellings_of_an_authorization_failure_are_accepted(code: str) -> None:
    # IAM and S3 answer with AccessDenied; Batch and ECR answer with AccessDeniedException.
    probe = probe_for("s3:GetObject")

    assert (
        require_denial(probe, returncode=254, stderr=denial_stderr(probe, code=code)).code == code
    )


def test_a_call_that_was_allowed_is_never_recorded_as_a_denial() -> None:
    probe = probe_for("batch:SubmitJob")

    failure = refused(probe, "", returncode=0)

    assert failure.reason is PublisherDenialReason.ATTEMPT_PERMITTED
    assert failure.action == "batch:SubmitJob"


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("RepositoryNotFoundException", "The repository does not exist in the registry"),
        ("ClientException", "Job queue edullm-denial-probe-absent-queue does not exist"),
        ("NoSuchBucket", "The specified bucket does not exist"),
        ("EntityAlreadyExists", "Role with name sbsandbox-intern-edullm-ecr-publisher exists."),
        ("ValidationException", "1 validation error detected"),
        ("ExpiredToken", "The security token included in the request is expired"),
    ],
)
def test_a_failure_for_any_other_reason_is_a_permitted_action_wearing_a_failure(
    code: str,
    message: str,
) -> None:
    # Every one of these is what an allowed call looks like when it is pointed at
    # something that is not there, or at a request the service would not process.
    probe = probe_for("ecr:DeleteRepository")

    failure = refused(probe, cli_error(probe.operation, code, message))

    assert failure.reason is PublisherDenialReason.ATTEMPT_FAILED_FOR_ANOTHER_REASON
    assert failure.error_code == code


@pytest.mark.parametrize(
    "stderr",
    [
        "",
        "Could not connect to the endpoint URL: https://batch.us-east-1.amazonaws.com/\n",
        "usage: aws [options] <command> <subcommand>\naws: error: the following arguments\n",
        "An error occurred (Access Denied) when calling the SubmitJob operation: no\n",
    ],
)
def test_a_failure_that_is_not_an_aws_api_error_proves_nothing(stderr: str) -> None:
    # A network failure and a CLI usage error both leave the question unanswered, and the
    # fourth case is an error code that could not be one, so the text is not trusted.
    probe = probe_for("batch:SubmitJob")

    failure = refused(probe, stderr)

    assert failure.reason is PublisherDenialReason.ATTEMPT_FAILED_WITHOUT_AN_AWS_ERROR
    assert failure.error_code is None


def test_a_refusal_of_a_different_operation_is_not_a_refusal_of_this_one() -> None:
    # The refusal has to be of the call this probe made. Anything else means the command
    # and the action recorded beside it have drifted apart.
    probe = probe_for("batch:SubmitJob")
    stderr = cli_error("DescribeJobs", "AccessDeniedException", denial_message("batch:SubmitJob"))

    failure = refused(probe, stderr)

    assert failure.reason is PublisherDenialReason.ATTEMPT_CALLED_ANOTHER_OPERATION


def test_an_access_denied_that_names_no_action_is_not_evidence_of_this_action() -> None:
    # S3 answers a cross-account refusal with a bare "Access Denied" that says nothing
    # about which action was refused or who refused it.
    probe = probe_for("s3:GetObject")

    failure = refused(probe, cli_error("GetObject", "AccessDenied", "Access Denied"))

    assert failure.reason is PublisherDenialReason.DENIAL_NAMED_NO_ACTION


def test_a_refusal_of_a_neighbouring_action_is_not_a_refusal_of_this_one() -> None:
    probe = probe_for("s3:GetObject")
    stderr = cli_error("GetObject", "AccessDenied", denial_message("s3:GetObjectVersion"))

    failure = refused(probe, stderr)

    assert failure.reason is PublisherDenialReason.DENIAL_NAMED_NO_ACTION


def test_a_bucket_refusing_us_is_not_the_role_being_refused() -> None:
    # The matrix claims something about the identity's permissions. A bucket in another
    # account denying us says nothing about them, and the probe targets a bucket name
    # that nothing here owns, so this is the shape a false positive would arrive in.
    probe = probe_for("s3:GetObject")
    message = (
        f"User: {CALLER_ARN} is not authorized to perform: s3:GetObject on resource: "
        '"arn:aws:s3:::somebody-elses-bucket/key" with an explicit deny in a '
        "resource-based policy"
    )

    failure = refused(probe, cli_error("GetObject", "AccessDenied", message))

    assert failure.reason is PublisherDenialReason.DENIAL_CAME_FROM_A_RESOURCE_POLICY


@pytest.mark.parametrize(
    "phrase",
    [
        "because no identity-based policy allows the s3:GetObject action",
        "with an explicit deny in an identity-based policy",
        "with an explicit deny in a permissions boundary",
        "because no permissions boundary allows the s3:GetObject action",
        "with an explicit deny in a service control policy",
    ],
)
def test_every_way_the_identity_itself_can_be_refused_is_a_denial(phrase: str) -> None:
    # The publisher role carries a permissions boundary, so a refusal may come from the
    # boundary rather than from the inline policy. Both are the identity being refused.
    probe = probe_for("s3:GetObject")
    message = f"User: {CALLER_ARN} is not authorized to perform: s3:GetObject {phrase}"

    assert require_denial(
        probe, returncode=254, stderr=cli_error("GetObject", "AccessDenied", message)
    )


def test_the_recorded_message_carries_no_account_identifier() -> None:
    probe = probe_for("iam:CreateRole")

    record = attempt_record(probe, denial_stderr(probe, code="AccessDenied"))

    assert ACCOUNT_ID not in record.error_message
    assert AWS_ACCOUNT_ID_PLACEHOLDER in record.error_message
    assert f"assumed-role/{ROLE_NAME}" in record.error_message
    assert scan_for_secrets(record.error_message) == record.error_message


def test_the_record_names_the_action_the_call_and_the_service_that_refused_it() -> None:
    probe = probe_for("batch:UpdateComputeEnvironment")

    record = attempt_record(probe, denial_stderr(probe))

    assert record.attempted_action == "batch:UpdateComputeEnvironment"
    assert record.event_name == "UpdateComputeEnvironment"
    assert record.event_source == "batch.amazonaws.com"
    assert record.outcome == "denied"
    assert record.role_name == ROLE_NAME
    assert record.session_name == SESSION_NAME
    assert record.attempted_resource == "edullm-denial-probe-absent-compute-environment"


def test_the_recorded_resource_is_a_name_rather_than_an_arn() -> None:
    for probe in probes():
        assert not probe.resource_name.startswith("arn:"), probe.action


def test_a_message_carrying_a_credential_is_refused_rather_than_laundered() -> None:
    # redact_aws_account_ids will not mask text that holds another credential, because
    # masking twelve digits inside a secret access key would break the run that
    # identifies it and leave a live credential the scan then accepts.
    probe = probe_for("s3:GetObject")
    leaked = "A" * 20 + "b" * 20
    message = f"{denial_message('s3:GetObject')} using {leaked}"

    with pytest.raises(DenialNotProvenError) as exc_info:
        record_denial(
            probe,
            require_denial(
                probe, returncode=254, stderr=cli_error("GetObject", "AccessDenied", message)
            ),
            region=REGION,
            role_name=ROLE_NAME,
            session_name=SESSION_NAME,
            attempted_at=datetime.now(tz=UTC),
        )

    assert exc_info.value.reason is PublisherDenialReason.DENIAL_MESSAGE_HOLDS_A_CREDENTIAL
    assert leaked not in str(exc_info.value)


def test_the_attempt_record_holds_everything_the_evidence_needs_but_cloudtrail() -> None:
    # The two records are kept in step by derivation rather than by inspection: a field
    # added to DenialEvidence that an attempt could supply fails here rather than
    # silently going unrecorded.
    attempt_fields = set(AttemptedDenial.model_fields)
    evidence_fields = set(DenialEvidence.model_fields)

    assert attempt_fields <= evidence_fields
    assert evidence_fields - attempt_fields == EVIDENCE_ONLY_FIELDS
    assert EVIDENCE_ONLY_FIELDS == frozenset(
        {"source", "environment", "status", "observed_at", "event_id"}
    )


def test_the_evidence_record_is_one_cloudtrail_lookup_away_from_the_attempt() -> None:
    # The publisher session cannot read CloudTrail, so the event ID arrives later, from
    # a capture with credentials that can. Everything else is already recorded.
    probe = probe_for("batch:SubmitJob")
    record = attempt_record(probe, denial_stderr(probe))
    observed_at = datetime.now(tz=UTC)

    evidence = denial_evidence(record, event_id=CLOUDTRAIL_EVENT_ID, observed_at=observed_at)

    assert isinstance(evidence, DenialEvidence)
    assert evidence.event_id == CLOUDTRAIL_EVENT_ID
    assert evidence.attempted_action == record.attempted_action
    assert evidence.error_message == record.error_message
    assert evidence.outcome == "denied"


def test_a_matrix_record_that_is_missing_an_attempt_is_not_a_matrix() -> None:
    # A run that proved four of the five refusals proved the criterion for four of them,
    # and a file that could hold the four would be read as if it had proved all five.
    records = [attempt_record(probe, denial_stderr(probe)) for probe in probes()]
    complete = PublisherDenialMatrix(schema_version=1, attempts=tuple(records))

    assert (
        tuple(attempt.attempted_action for attempt in complete.attempts) == PUBLISHER_DENIED_ACTIONS
    )

    with pytest.raises(ValidationError):
        PublisherDenialMatrix(schema_version=1, attempts=tuple(records[:-1]))
    with pytest.raises(ValidationError):
        PublisherDenialMatrix(schema_version=1, attempts=tuple(reversed(records)))


def test_the_caller_identity_is_read_off_the_assumed_role_arn() -> None:
    assert assumed_role_identity(CALLER_ARN) == (ROLE_NAME, SESSION_NAME)


@pytest.mark.parametrize(
    "arn",
    [
        f"arn:aws:iam::{ACCOUNT_ID}:user/somebody",
        f"arn:aws:sts::{ACCOUNT_ID}:federated-user/somebody",
        f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/{ROLE_NAME}",
        "",
        "not-an-arn",
    ],
)
def test_an_identity_that_is_not_an_assumed_role_stops_the_matrix(arn: str) -> None:
    # Every field of the record describes a role session. An identity that is not one
    # cannot be written down, and guessing at a role name would put a name in an
    # evidence record that nothing established.
    with pytest.raises(DenialNotProvenError) as exc_info:
        assumed_role_identity(arn)

    assert exc_info.value.reason is PublisherDenialReason.CALLER_IS_NOT_AN_ASSUMED_ROLE
    assert ACCOUNT_ID not in str(exc_info.value)
