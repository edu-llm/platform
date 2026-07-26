"""The publisher denial matrix: what is attempted, and what counts as a refusal.

The distinction every test here circles is that a call which failed is not a call which
was denied. A not-found, a malformed parameter, a throttle and a timeout are all failures,
and every one of them is what a *permitted* action looks like when it is pointed at
something that is not there. Recording any of them as a denial would let the widening this
matrix exists to catch pass as proof that it did not happen.

The first live run taught this file two things, and both are pinned here rather than left
to a comment. A refusal is not reliably *worded*: S3 answers ``Access Denied`` and nothing
else, while IAM and Batch name the principal, the action and the policy that was silent.
And a question whose subject does not exist is answered before it is authorized, so a
probe aimed at an absent bucket can only ever be told the bucket is absent.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from edullm_platform import publisher_denials
from edullm_platform.evidence import AWS_ACCOUNT_ID_PLACEHOLDER, scan_for_secrets
from edullm_platform.phase1_evidence import DenialEvidence
from edullm_platform.publisher_denials import (
    EVIDENCE_ONLY_FIELDS,
    PUBLISHER_DENIED_ACTIONS,
    AttemptedDenial,
    DenialNotProvenError,
    DenialProbe,
    ProbeOutcome,
    PublisherDenialMatrix,
    PublisherDenialReason,
    assumed_role_identity,
    attempt_denials,
    denial_evidence,
    denial_probes,
    record_denial,
    require_denial,
    run_aws,
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
    ("s3:ListAllMyBuckets", ("s3api", "list-buckets", "--region", REGION)),
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

#: Refusals as each service actually words them. S3's is the whole message: its error
#: table gives ``AccessDenied`` the description "Access Denied" and no service is obliged
#: to say more. IAM and Batch answer with the long form. The classifier has to accept both
#: without accepting either as licence to accept anything.
TERSE_REFUSAL = "Access Denied"


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


def denial_stderr(probe: DenialProbe) -> str:
    """The refusal the service behind this probe would really send back.

    S3 says ``AccessDenied`` and nothing else; IAM says ``AccessDenied`` and spells out
    the principal and the action; Batch and ECR say ``AccessDeniedException`` and spell
    it out too. Using each service's own shape is the point: a matrix that only ever
    sees one of them proves nothing about the other.
    """
    service = probe.action.split(":", 1)[0]
    if service == "s3":
        return cli_error(probe.operation, "AccessDenied", TERSE_REFUSAL)
    code = "AccessDenied" if service == "iam" else "AccessDeniedException"
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
) -> publisher_denials.DenialMatrixRun:
    monkeypatch.setattr(publisher_denials, "run_aws", aws_answering(answers or {}))
    return attempt_denials(region=REGION, ecr_repository=ECR_REPOSITORY)


def test_the_matrix_attempts_every_action_the_criterion_names() -> None:
    # Criterion 6 is "the publisher role cannot submit jobs, read datasets, alter IAM, or
    # modify Batch". The fifth is the one the criterion does not name: the role publishes
    # images and must not be able to delete a repository holding them.
    assert PUBLISHER_DENIED_ACTIONS == (
        "batch:SubmitJob",
        "s3:ListAllMyBuckets",
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
    # resource rather than doing something. The three exceptions are deliberate and each
    # one is inert for its own reason: list-buckets reads and names nothing; create-role
    # names a role that already exists, so a permitted call collides instead of creating;
    # and delete-repository names a repository beside the registered one rather than the
    # registered one itself, because a permitted delete of that would take the images.
    arguments = {probe.action: probe.arguments for probe in probes()}

    assert (
        "absent"
        in arguments["batch:SubmitJob"][arguments["batch:SubmitJob"].index("--job-queue") + 1]
    )
    assert arguments["s3:ListAllMyBuckets"] == ("s3api", "list-buckets", "--region", REGION)
    assert arguments["iam:CreateRole"][arguments["iam:CreateRole"].index("--role-name") + 1] == (
        ROLE_NAME
    )
    assert "absent" in arguments["batch:UpdateComputeEnvironment"][-1]
    assert arguments["ecr:DeleteRepository"][-1] != ECR_REPOSITORY
    assert arguments["ecr:DeleteRepository"][-1].startswith(ECR_REPOSITORY)

    # --force is what turns a delete of a repository that holds images from a refusal
    # into a deletion, and nothing here may carry it.
    assert not any("--force" in probe.arguments for probe in probes())


def test_the_s3_probe_asks_something_that_has_no_not_found_answer() -> None:
    # The first live run reported attempt_failed_for_another_reason:s3:GetObject:
    # NoSuchBucket. The probe was a read of an object in a bucket chosen not to exist, so
    # that a permitted call could not read anything -- but S3 routes a request to a
    # bucket before it authorizes it, so a bucket that is not there is answered with
    # NoSuchBucket and the refusal was never observable. Harmlessness and observability
    # cannot both come from an absent bucket.
    #
    # ListBuckets names no bucket, so there is nothing about it that can be missing: a
    # session holding no S3 permission has only one answer available to it. It is also
    # a read, so a permitted call changes nothing, and its output is never printed.
    s3 = probe_for("s3:ListAllMyBuckets")

    assert s3.operation == "ListBuckets"
    assert s3.resource_name is None
    assert not any(
        argument in {"--bucket", "--key"} for probe in probes() for argument in probe.arguments
    )


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


@pytest.mark.parametrize("action", PUBLISHER_DENIED_ACTIONS)
def test_each_service_refusing_in_its_own_words_is_a_refusal(action: str) -> None:
    # The four services here word a refusal three different ways and the matrix has to
    # read all of them. What they agree on is the error code and the operation, which is
    # what the classifier rests on.
    probe = probe_for(action)

    error = require_denial(probe, returncode=254, stderr=denial_stderr(probe))

    assert error.code in {"AccessDenied", "AccessDeniedException"}
    assert error.operation == probe.operation


def test_a_refusal_that_says_only_access_denied_is_still_a_refusal() -> None:
    # S3's error table gives AccessDenied the description "Access Denied" and that is the
    # whole message. The matrix used to require the text to read "is not authorized to
    # perform: <action>", which no S3 refusal contains, so the one service the criterion
    # is most about was the one service that could never satisfy it.
    probe = probe_for("s3:ListAllMyBuckets")

    error = require_denial(
        probe, returncode=254, stderr=cli_error("ListBuckets", "AccessDenied", TERSE_REFUSAL)
    )

    assert error.code == "AccessDenied"
    assert error.message == TERSE_REFUSAL


def test_a_call_that_was_allowed_is_never_recorded_as_a_denial() -> None:
    probe = probe_for("batch:SubmitJob")

    failure = refused(probe, "", returncode=0)

    assert failure.reason is PublisherDenialReason.ATTEMPT_PERMITTED
    assert failure.action == "batch:SubmitJob"


@pytest.mark.parametrize(
    ("action", "code", "message"),
    [
        # Not-found, in each service's spelling. Every one of these is the answer a
        # permitted call gets when it is pointed at something that is not there.
        ("ecr:DeleteRepository", "RepositoryNotFoundException", "The repository with name"),
        ("batch:SubmitJob", "ClientException", "Job queue does not exist"),
        ("s3:ListAllMyBuckets", "NoSuchBucket", "The specified bucket does not exist"),
        # Malformed: the service never reached the question of who was asking.
        ("iam:CreateRole", "MalformedPolicyDocument", "Syntax errors in policy."),
        ("ecr:DeleteRepository", "ValidationException", "1 validation error detected"),
        ("batch:UpdateComputeEnvironment", "ParamValidationError", "Invalid length for parameter"),
        # Already there: the create-role probe's own inert case, if IAM should turn out
        # to check the name before it checks the caller.
        ("iam:CreateRole", "EntityAlreadyExists", "Role with name already exists."),
        # Throttled: the service refused to answer, which is not the service refusing us.
        ("s3:ListAllMyBuckets", "SlowDown", "Please reduce your request rate."),
        ("batch:SubmitJob", "ThrottlingException", "Rate exceeded"),
        ("iam:CreateRole", "Throttling", "Rate exceeded"),
        ("ecr:DeleteRepository", "TooManyRequestsException", "Rate exceeded"),
        # Credentials, which say nothing about what the credentials were allowed to do.
        ("batch:SubmitJob", "ExpiredToken", "The security token included is expired"),
        ("ecr:DeleteRepository", "UnrecognizedClientException", "The security token is invalid"),
        # A server-side failure is not an answer at all.
        ("batch:SubmitJob", "ServerException", "Internal server error"),
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
    # Operation identity is half of what the classifier rests on, and it is the half that
    # does the work now that a bare "Access Denied" is accepted: without it, any refusal
    # of anything would stand in for a refusal of this.
    probe = probe_for("batch:SubmitJob")
    stderr = cli_error("DescribeJobs", "AccessDeniedException", denial_message("batch:SubmitJob"))

    failure = refused(probe, stderr)

    assert failure.reason is PublisherDenialReason.ATTEMPT_CALLED_ANOTHER_OPERATION


def test_a_refusal_that_names_another_action_is_not_a_refusal_of_this_one() -> None:
    # A message that does name an action is held to it. One that names none is taken on
    # the code and the operation, but a message cannot both speak and be ignored.
    probe = probe_for("s3:ListAllMyBuckets")
    stderr = cli_error("ListBuckets", "AccessDenied", denial_message("s3:ListAllMyBucketsAndMore"))

    failure = refused(probe, stderr)

    assert failure.reason is PublisherDenialReason.DENIAL_NAMED_ANOTHER_ACTION


@pytest.mark.parametrize(
    "message",
    [
        (
            f"User: {CALLER_ARN} is not authorized to perform: ecr:DeleteRepository on "
            'resource: "arn:aws:ecr:us-east-1:123456789012:repository/x" with an explicit '
            "deny in a resource-based policy"
        ),
        "Access Denied by the resource-based policy on the repository",
    ],
)
def test_a_resource_refusing_us_is_not_the_role_being_refused(message: str) -> None:
    # The matrix claims something about the identity's permissions. A resource policy
    # denying this call says nothing about them, and reading it as a denial would report
    # the role as narrow at the moment it had been widened. The second case is the same
    # attribution without the long form, because the check cannot depend on the wording.
    probe = probe_for("ecr:DeleteRepository")

    failure = refused(probe, cli_error("DeleteRepository", "AccessDenied", message))

    assert failure.reason is PublisherDenialReason.DENIAL_CAME_FROM_A_RESOURCE_POLICY


@pytest.mark.parametrize(
    "phrase",
    [
        "because no identity-based policy allows the iam:CreateRole action",
        "with an explicit deny in an identity-based policy",
        "with an explicit deny in a permissions boundary",
        "because no permissions boundary allows the iam:CreateRole action",
        "with an explicit deny in a service control policy",
    ],
)
def test_every_way_the_identity_itself_can_be_refused_is_a_denial(phrase: str) -> None:
    # The publisher role carries a permissions boundary, so a refusal may come from the
    # boundary rather than from the inline policy. Both are the identity being refused.
    probe = probe_for("iam:CreateRole")
    message = f"User: {CALLER_ARN} is not authorized to perform: iam:CreateRole {phrase}"

    assert require_denial(
        probe, returncode=254, stderr=cli_error("CreateRole", "AccessDenied", message)
    )


def test_a_call_that_never_returns_is_not_a_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    # A hung call is the emptiest failure of all: nothing was decided by anybody.
    def hang(*_arguments: object, **_keywords: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["aws"], timeout=1)

    monkeypatch.setattr(subprocess, "run", hang)

    with pytest.raises(DenialNotProvenError) as exc_info:
        run_aws(("s3api", "list-buckets"), action="s3:ListAllMyBuckets")

    assert exc_info.value.reason is PublisherDenialReason.ATTEMPT_TIMED_OUT
    assert exc_info.value.action == "s3:ListAllMyBuckets"


def test_the_recorded_message_carries_no_account_identifier() -> None:
    probe = probe_for("iam:CreateRole")

    record = attempt_record(probe, denial_stderr(probe))

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


def test_a_call_that_named_no_resource_is_recorded_as_having_named_none() -> None:
    # ListBuckets takes no resource, and inventing one for the record would be inventing
    # the only part of the record a reader would use to check the claim.
    probe = probe_for("s3:ListAllMyBuckets")

    record = attempt_record(probe, denial_stderr(probe))

    assert record.attempted_resource is None
    assert record.error_code == "AccessDenied"
    assert record.error_message == TERSE_REFUSAL
    assert record.event_name == "ListBuckets"
    assert record.event_source == "s3.amazonaws.com"


def test_the_recorded_resource_is_a_name_rather_than_an_arn() -> None:
    for probe in probes():
        assert probe.resource_name is None or not probe.resource_name.startswith("arn:"), (
            probe.action
        )


def test_a_message_carrying_a_credential_is_refused_rather_than_laundered() -> None:
    # redact_aws_account_ids will not mask text that holds another credential, because
    # masking twelve digits inside a secret access key would break the run that
    # identifies it and leave a live credential the scan then accepts.
    probe = probe_for("iam:CreateRole")
    leaked = "A" * 20 + "b" * 20
    message = f"{denial_message('iam:CreateRole')} using {leaked}"

    with pytest.raises(DenialNotProvenError) as exc_info:
        record_denial(
            probe,
            require_denial(
                probe, returncode=254, stderr=cli_error("CreateRole", "AccessDenied", message)
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


def test_one_outcome_is_a_refusal_or_a_reason_there_was_none_and_never_both() -> None:
    probe = probe_for("batch:SubmitJob")
    record = attempt_record(probe, denial_stderr(probe))
    unproven = DenialNotProvenError(PublisherDenialReason.ATTEMPT_PERMITTED, action=probe.action)

    with pytest.raises(ValueError, match="either"):
        ProbeOutcome(action=probe.action, denial=record, unproven=unproven)
    with pytest.raises(ValueError, match="either"):
        ProbeOutcome(action=probe.action, denial=None, unproven=None)


def test_a_run_that_refused_everything_is_the_matrix_the_record_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = run_answering(monkeypatch)

    assert run.proven
    assert run.summary == (
        "denied:batch:SubmitJob:AccessDeniedException",
        "denied:s3:ListAllMyBuckets:AccessDenied",
        "denied:iam:CreateRole:AccessDenied",
        "denied:batch:UpdateComputeEnvironment:AccessDeniedException",
        "denied:ecr:DeleteRepository:AccessDeniedException",
    )
    assert (
        tuple(attempt.attempted_action for attempt in run.matrix().attempts)
        == PUBLISHER_DENIED_ACTIONS
    )


def test_every_action_is_attempted_even_after_one_of_them_proves_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # What the first live run cost: it stopped at the S3 probe, so the other four were
    # still unverified and each fix bought one more run to learn one more thing. A run
    # attempts everything and reports everything, and the deciding happens afterwards.
    run = run_answering(
        monkeypatch,
        {
            "s3:ListAllMyBuckets": (0, ""),
            "iam:CreateRole": (
                254,
                cli_error("CreateRole", "EntityAlreadyExists", "Role with name exists."),
            ),
        },
    )

    assert not run.proven
    assert run.summary == (
        "denied:batch:SubmitJob:AccessDeniedException",
        "attempt_permitted:s3:ListAllMyBuckets",
        "attempt_failed_for_another_reason:iam:CreateRole:EntityAlreadyExists",
        "denied:batch:UpdateComputeEnvironment:AccessDeniedException",
        "denied:ecr:DeleteRepository:AccessDeniedException",
    )
    assert tuple(outcome.action for outcome in run.outcomes) == PUBLISHER_DENIED_ACTIONS


def test_a_probe_that_hangs_costs_one_answer_rather_than_the_whole_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answered = aws_answering({})

    def run_or_hang(
        arguments: Sequence[str],
        *,
        action: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if action == "batch:SubmitJob":
            raise DenialNotProvenError(
                PublisherDenialReason.ATTEMPT_TIMED_OUT,
                action=action,
            )
        return answered(arguments, action=action)

    monkeypatch.setattr(publisher_denials, "run_aws", run_or_hang)
    run = attempt_denials(region=REGION, ecr_repository=ECR_REPOSITORY)

    assert not run.proven
    assert run.summary[0] == "attempt_timed_out:batch:SubmitJob"
    assert run.summary[1:] == (
        "denied:s3:ListAllMyBuckets:AccessDenied",
        "denied:iam:CreateRole:AccessDenied",
        "denied:batch:UpdateComputeEnvironment:AccessDeniedException",
        "denied:ecr:DeleteRepository:AccessDeniedException",
    )


def test_a_run_that_did_not_refuse_everything_has_no_matrix_to_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = run_answering(monkeypatch, {"ecr:DeleteRepository": (0, "")})

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
            "batch:SubmitJob": (
                254,
                cli_error(
                    "SubmitJob",
                    "ClientException",
                    f"Job queue not found in account {ACCOUNT_ID} secret-detail-canary",
                ),
            )
        },
    )
    summary = "\n".join(run.summary)

    assert "attempt_failed_for_another_reason:batch:SubmitJob:ClientException" in summary
    assert ACCOUNT_ID not in summary
    assert "secret-detail-canary" not in summary
    assert scan_for_secrets(summary) == summary


def test_a_session_that_cannot_be_described_attempts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every field of a record describes a role session, so this is a precondition rather
    # than an anomaly to collect: there is nothing to write any refusal down in.
    def not_a_role(
        arguments: Sequence[str],
        *,
        action: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert arguments[0] == "sts", "no probe may run without an identity"
        return subprocess.CompletedProcess(
            list(arguments), 0, f"arn:aws:iam::{ACCOUNT_ID}:user/somebody\n", ""
        )

    monkeypatch.setattr(publisher_denials, "run_aws", not_a_role)

    with pytest.raises(DenialNotProvenError) as exc_info:
        attempt_denials(region=REGION, ecr_repository=ECR_REPOSITORY)

    assert exc_info.value.reason is PublisherDenialReason.CALLER_IS_NOT_AN_ASSUMED_ROLE


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
