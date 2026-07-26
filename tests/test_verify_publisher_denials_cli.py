"""The command the publish workflow runs to show the publisher role is still narrow.

Every case here runs the real command against a stub ``aws`` on PATH, so what is under
test is the plumbing as well as the judgement: the argv each probe builds, the exit
status, and above all what does and does not reach the two streams a public runner log
is made of.

The stub answers each service in that service's own words, because the first live run
failed on the assumption that they all answer alike.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from workflow_support import write_stub

from edullm_platform import publisher_denials
from edullm_platform.evidence import AWS_ACCOUNT_ID_PLACEHOLDER, scan_for_secrets
from edullm_platform.publisher_denials import PUBLISHER_DENIED_ACTIONS, denial_probes
from tools.verify_publisher_denials import NOT_PROVEN_EXPLANATION, main

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "repositories.yaml"
REPOSITORY = "OLMo-core"
ECR_REPOSITORY = "sbsandbox-intern-edullm-olmo-core"
REGION = "us-east-1"
ACCOUNT_ID = "123456789012"
ROLE_NAME = "sbsandbox-intern-edullm-ecr-publisher"
SESSION_NAME = "GitHubActions"
CALLER_ARN = f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/{ROLE_NAME}/{SESSION_NAME}"

#: Every command the tool may run against a live account, in order. The workflow step
#: that runs it contains no `aws` word of its own, so this is the enumeration for it.
EXPECTED_CALLS = (
    f"sts get-caller-identity --region {REGION} --query Arn --output text",
    (
        f"batch submit-job --region {REGION} --job-name edullm-denial-probe "
        "--job-queue edullm-denial-probe-absent-queue "
        "--job-definition edullm-denial-probe-absent-job-definition"
    ),
    f"s3api list-buckets --region {REGION}",
    (
        f"iam create-role --region {REGION} --role-name {ROLE_NAME} "
        '--assume-role-policy-document {"Version":"2012-10-17","Statement":'
        '[{"Effect":"Deny","Principal":{"AWS":"*"},"Action":"sts:AssumeRole"}]}'
    ),
    (
        f"batch update-compute-environment --region {REGION} "
        "--compute-environment edullm-denial-probe-absent-compute-environment"
    ),
    (
        f"ecr delete-repository --region {REGION} "
        f"--repository-name {ECR_REPOSITORY}-denial-probe-absent"
    ),
)

#: What the stub reports when every probe is refused, in matrix order. S3's line carries
#: the code from a message that said nothing but "Access Denied".
REFUSED_EVERYTHING = (
    "denied:batch:SubmitJob:AccessDeniedException",
    "denied:s3:ListAllMyBuckets:AccessDenied",
    "denied:iam:CreateRole:AccessDenied",
    "denied:batch:UpdateComputeEnvironment:AccessDeniedException",
    "denied:ecr:DeleteRepository:AccessDeniedException",
)


def probes() -> tuple[publisher_denials.DenialProbe, ...]:
    return denial_probes(region=REGION, ecr_repository=ECR_REPOSITORY, role_name=ROLE_NAME)


def denial_body(probe: publisher_denials.DenialProbe) -> str:
    """A refusal in the words the service behind this probe really uses."""
    service = probe.action.split(":", 1)[0]
    if service == "s3":
        message = "Access Denied"
        code = "AccessDenied"
    else:
        code = "AccessDenied" if service == "iam" else "AccessDeniedException"
        message = (
            f"User: {CALLER_ARN} is not authorized to perform: {probe.action} "
            f"on resource: {probe.resource_name} because no identity-based policy allows "
            f"the {probe.action} action"
        )
    error = f"An error occurred ({code}) when calling the {probe.operation} operation: {message}"
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
        "--registry": str(REGISTRY_PATH),
        "--repository": REPOSITORY,
        "--region": REGION,
        "--output": str(tmp_path / "publisher-denials.json"),
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
    written = (tmp_path / "publisher-denials.json").read_text(encoding="utf-8")
    assert written.endswith("\n")
    assert ", " not in written and '": ' not in written
    matrix = json.loads(written)
    assert matrix["schema_version"] == 1
    assert (
        tuple(attempt["attempted_action"] for attempt in matrix["attempts"])
        == PUBLISHER_DENIED_ACTIONS
    )
    assert recorded(recording) == list(EXPECTED_CALLS)


def test_the_record_says_what_was_attempted_and_who_was_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_aws_stub(tmp_path, monkeypatch)

    assert main(argv(tmp_path)) == 0

    matrix = json.loads((tmp_path / "publisher-denials.json").read_text(encoding="utf-8"))
    submit = matrix["attempts"][0]
    assert submit["attempted_action"] == "batch:SubmitJob"
    assert submit["attempted_resource"] == "edullm-denial-probe-absent-queue"
    assert submit["outcome"] == "denied"
    assert submit["error_code"] == "AccessDeniedException"
    assert submit["event_name"] == "SubmitJob"
    assert submit["event_source"] == "batch.amazonaws.com"
    assert submit["role_name"] == ROLE_NAME
    assert submit["session_name"] == SESSION_NAME
    assert submit["region"] == REGION


def test_a_refusal_that_said_only_access_denied_is_recorded_as_the_refusal_it_is(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # S3 says "Access Denied" and nothing else, and it names no bucket to have been
    # refused about. Both go into the record as they are rather than being embellished.
    install_aws_stub(tmp_path, monkeypatch)

    assert main(argv(tmp_path)) == 0

    matrix = json.loads((tmp_path / "publisher-denials.json").read_text(encoding="utf-8"))
    listing = matrix["attempts"][1]
    assert listing["attempted_action"] == "s3:ListAllMyBuckets"
    assert listing["attempted_resource"] is None
    assert listing["error_code"] == "AccessDenied"
    assert listing["error_message"] == "Access Denied"
    assert listing["event_name"] == "ListBuckets"
    assert listing["event_source"] == "s3.amazonaws.com"


def test_nothing_the_account_said_about_itself_survives_into_the_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The denial message names the account, the role and the resource. Only the account
    # is a secret, and it is the one the scan on every field would refuse.
    install_aws_stub(tmp_path, monkeypatch)

    assert main(argv(tmp_path)) == 0
    captured = capsys.readouterr()

    written = (tmp_path / "publisher-denials.json").read_text(encoding="utf-8")
    assert ACCOUNT_ID not in written
    assert ACCOUNT_ID not in captured.out + captured.err
    assert AWS_ACCOUNT_ID_PLACEHOLDER in written
    assert f"assumed-role/{ROLE_NAME}" in written
    assert scan_for_secrets(written) == written


def test_an_action_that_was_allowed_stops_the_run_instead_of_being_recorded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The whole matrix exists for this case. A role widened in the console answers here,
    # and it must stop the publish rather than be filed as though it had been refused.
    install_aws_stub(tmp_path, monkeypatch, answers={"iam:CreateRole": "exit 0"})

    exit_code = main(argv(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.err.splitlines() == [
        REFUSED_EVERYTHING[0],
        REFUSED_EVERYTHING[1],
        "attempt_permitted:iam:CreateRole",
        REFUSED_EVERYTHING[3],
        REFUSED_EVERYTHING[4],
        NOT_PROVEN_EXPLANATION,
    ]
    assert captured.out == ""
    assert not (tmp_path / "publisher-denials.json").exists()


def test_one_run_reports_every_probe_rather_than_the_first_that_went_wrong(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The first live run stopped at the S3 probe, so four of the five actions were still
    # unverified and every fix bought one more run to learn one more thing. A run that
    # reports everything at once is the difference between one round trip and four.
    recording = install_aws_stub(
        tmp_path,
        monkeypatch,
        answers={
            "batch:SubmitJob": failing_body("SubmitJob", "ClientException", "does not exist"),
            "ecr:DeleteRepository": "exit 0",
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
        "attempt_permitted:ecr:DeleteRepository",
        NOT_PROVEN_EXPLANATION,
    ]
    assert recorded(recording) == list(EXPECTED_CALLS)
    assert not (tmp_path / "publisher-denials.json").exists()


@pytest.mark.parametrize(
    ("code", "message", "reason"),
    [
        (
            "RepositoryNotFoundException",
            "The repository with name does not exist in the registry with id",
            "attempt_failed_for_another_reason:ecr:DeleteRepository:RepositoryNotFoundException",
        ),
        (
            "ValidationException",
            "1 validation error detected: value at repositoryName failed to satisfy",
            "attempt_failed_for_another_reason:ecr:DeleteRepository:ValidationException",
        ),
        (
            "ThrottlingException",
            "Rate exceeded",
            "attempt_failed_for_another_reason:ecr:DeleteRepository:ThrottlingException",
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
    # A not-found says the call was authorized and the resource was absent, a malformed
    # parameter says the service never got as far as deciding, and a throttle says it
    # declined to answer. None is a refusal of this identity.
    canary = f"{message} secret-detail-canary"
    install_aws_stub(
        tmp_path,
        monkeypatch,
        answers={"ecr:DeleteRepository": failing_body("DeleteRepository", code, canary)},
    )

    exit_code = main(argv(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.err.splitlines()[4] == reason
    assert "secret-detail-canary" not in captured.out + captured.err
    assert not (tmp_path / "publisher-denials.json").exists()


def test_a_call_that_never_reached_aws_is_not_a_denial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_aws_stub(
        tmp_path,
        monkeypatch,
        answers={
            "s3:ListAllMyBuckets": (
                "printf '%s\\n' 'Could not connect to the endpoint URL: "
                "https://s3.us-east-1.amazonaws.com/' >&2; exit 255"
            )
        },
    )

    exit_code = main(argv(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.err.splitlines()[1] == (
        "attempt_failed_without_an_aws_error:s3:ListAllMyBuckets"
    )
    assert not (tmp_path / "publisher-denials.json").exists()


def test_a_probe_that_hangs_is_not_a_denial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_aws_stub(tmp_path, monkeypatch)
    real_run = subprocess.run

    def hang_after_the_identity_call(
        arguments: list[str],
        **keywords: object,
    ) -> subprocess.CompletedProcess[str]:
        if arguments[1] == "sts":
            return real_run(arguments, **keywords)  # type: ignore[call-overload,no-any-return]
        raise subprocess.TimeoutExpired(cmd=arguments, timeout=1)

    monkeypatch.setattr(subprocess, "run", hang_after_the_identity_call)

    exit_code = main(argv(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.err.splitlines() == [
        *(f"attempt_timed_out:{action}" for action in PUBLISHER_DENIED_ACTIONS),
        NOT_PROVEN_EXPLANATION,
    ]
    assert not (tmp_path / "publisher-denials.json").exists()


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

    assert exit_code == 1
    assert captured.err.splitlines()[0] == "aws_cli_unavailable"
    assert not (tmp_path / "publisher-denials.json").exists()


@pytest.mark.parametrize(
    ("caller_arn", "caller_status", "reason"),
    [
        (f"arn:aws:iam::{ACCOUNT_ID}:user/somebody", 0, "caller_is_not_an_assumed_role"),
        (CALLER_ARN, 254, "caller_identity_unreadable"),
    ],
)
def test_a_session_the_record_could_not_describe_never_attempts_anything(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caller_arn: str,
    caller_status: int,
    reason: str,
) -> None:
    # Every field of the record describes a role session. Attempting the matrix without
    # one would produce refusals nothing could be written down about, so this is the one
    # failure that is a precondition rather than an outcome to collect.
    recording = install_aws_stub(
        tmp_path,
        monkeypatch,
        caller_arn=caller_arn,
        caller_status=caller_status,
    )

    exit_code = main(argv(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.err.splitlines() == [reason, NOT_PROVEN_EXPLANATION]
    assert ACCOUNT_ID not in captured.out + captured.err
    assert recorded(recording) == [EXPECTED_CALLS[0]]
    assert not (tmp_path / "publisher-denials.json").exists()


def test_the_ecr_probe_never_names_the_repository_the_role_publishes_to(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A permitted delete of the registered repository would take the published images
    # with it, so the probe lands beside it on a name nothing creates.
    recording = install_aws_stub(tmp_path, monkeypatch)

    assert main(argv(tmp_path)) == 0

    delete = next(call for call in recorded(recording) if call.startswith("ecr delete-repository"))
    assert f"--repository-name {ECR_REPOSITORY}-denial-probe-absent" in delete
    assert not delete.endswith(f"--repository-name {ECR_REPOSITORY}")
    assert "--force" not in delete


def test_the_s3_probe_names_no_bucket_at_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A bucket that does not exist is answered NoSuchBucket before anybody is authorized,
    # which is what the first live run hit, and a bucket that does exist in this shared
    # account belongs to somebody else. The probe asks the account-level question.
    recording = install_aws_stub(tmp_path, monkeypatch)

    assert main(argv(tmp_path)) == 0

    assert [call for call in recorded(recording) if call.startswith("s3api")] == [
        f"s3api list-buckets --region {REGION}"
    ]
    assert not any("--bucket" in call for call in recorded(recording))


def test_an_unregistered_repository_is_refused_before_any_credential_is_used(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recording = install_aws_stub(tmp_path, monkeypatch)

    exit_code = main(argv(tmp_path, **{"--repository": "not-registered"}))
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.err.splitlines()[0] == "unregistered_repository"
    assert recorded(recording) == []


def test_a_missing_registry_file_fails_closed_without_a_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_aws_stub(tmp_path, monkeypatch)

    exit_code = main(argv(tmp_path, **{"--registry": str(tmp_path / "absent.yaml")}))

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "registry_unreadable"


def test_a_record_that_cannot_be_written_is_an_environment_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_aws_stub(tmp_path, monkeypatch)

    exit_code = main(argv(tmp_path, **{"--output": str(tmp_path / "absent" / "denials.json")}))

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "output_unwritable"
