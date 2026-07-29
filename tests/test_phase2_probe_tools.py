"""The two Phase 2 laptop probes, run against stubs instead of against an account.

Neither probe can be exercised where these tests run: one needs an AWS session that can
create buckets and state machines, the other needs a GitHub run that passed an environment
gate. What can be exercised without either is everything that decides whether a live run
is worth making — the argv each probe builds, its exit status, what reaches the two
streams, and whether the record it writes could be committed.

The AWS stub answers ``describe-execution`` differently on the first and second call,
because the whole question the conditional-write probe asks is what changes between the
two writes.
"""

from __future__ import annotations

import json
import os
import shlex
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from workflow_support import write_stub

from edullm_platform.evidence import AWS_ACCOUNT_ID_PLACEHOLDER, scan_for_secrets
from tools.probe_approvals_readability import main as approvals_main
from tools.probe_conditional_write import (
    EXECUTION_ARN_PLACEHOLDER,
    NAME_PREFIX,
    PROBE_OBJECT_KEY,
    STATE_MACHINE_ARN_PLACEHOLDER,
    dry_run_plan,
)
from tools.probe_conditional_write import ProbeNames as ConditionalWriteNames
from tools.probe_conditional_write import main as conditional_write_main

PROFILE = "sbsandbox"
REGION = "us-east-1"
SUFFIX = "probe01"
ROLE_NAME = "sbsandbox-intern-edullm-conditional-write-probe"
ACCOUNT_ID = "123456789012"
BUCKET = f"{NAME_PREFIX}{SUFFIX}"
STATE_MACHINE = f"{NAME_PREFIX}{SUFFIX}"
STATE_MACHINE_ARN = f"arn:aws:states:{REGION}:{ACCOUNT_ID}:stateMachine/{STATE_MACHINE}"
EXECUTION_ARN = f"arn:aws:states:{REGION}:{ACCOUNT_ID}:execution/{STATE_MACHINE}/write"

#: Forty-four characters of the base64 alphabet, which is what a stored SHA256 checksum
#: looks like. Deliberately not forty and not sixty, so it is neither of the two shapes
#: ``scan_for_secrets`` calls a credential.
STORED_CHECKSUM = "MEt7l8Xk3Rz9QpVaBnCdEfGhIjKlMnOpQrStUvWxYz0="

#: Seventy-six characters of the same alphabet, which is what S3 stamps on an error as its
#: extended request id and is long enough that the scan refuses it. The probe masks it by
#: the field name in front of it, so the rest of the cause survives.
EXTENDED_REQUEST_ID = "E" * 76

PRECONDITION_CAUSE = (
    "At least one of the pre-conditions you specified did not hold "
    f"(Service: S3, Status Code: 412, Extended Request ID: {EXTENDED_REQUEST_ID})"
)
PRECONDITION_ERROR = "S3.PreconditionFailedException"


def canned(payload: object) -> str:
    """A stub branch that prints one JSON answer and exits cleanly."""
    return f"printf '%s' '{json.dumps(payload)}'"


def aws_error(operation: str, code: str, message: str) -> str:
    """A stub branch that fails the way the AWS CLI renders a service error."""
    rendered = f"An error occurred ({code}) when calling the {operation} operation: {message}"
    return f"printf '%s\\n' '{rendered}' >&2; exit 254"


SUCCEEDED = canned({"status": "SUCCEEDED", "output": "{}"})
REFUSED = canned({"status": "FAILED", "error": PRECONDITION_ERROR, "cause": PRECONDITION_CAUSE})

DEFAULT_AWS_ANSWERS: dict[str, str | list[str]] = {
    "sts get-caller-identity": canned({"Account": ACCOUNT_ID, "UserId": "AIDA", "Arn": "arn"}),
    "s3api create-bucket": canned({"Location": f"/{BUCKET}"}),
    "stepfunctions create-state-machine": canned({"stateMachineArn": STATE_MACHINE_ARN}),
    "stepfunctions start-execution": canned({"executionArn": EXECUTION_ARN}),
    "stepfunctions describe-execution": [SUCCEEDED, REFUSED],
    "s3api head-object": canned({"ChecksumSHA256": STORED_CHECKSUM, "ContentLength": 29}),
    "stepfunctions delete-state-machine": canned({}),
    "s3api delete-object": "exit 0",
    "s3api delete-bucket": "exit 0",
}


def stub_branch(key: str, responses: list[str], counter_dir: Path) -> str:
    if len(responses) == 1:
        return f'  "{key}") {responses[0]} ;;'
    counter = counter_dir / f"{key.replace(' ', '_')}.count"
    lines = [
        f'  "{key}")',
        f"    n=$(cat '{counter}' 2>/dev/null || echo 0)",
        f"    n=$((n+1)); printf '%s' \"$n\" > '{counter}'",
    ]
    for ordinal, response in enumerate(responses, start=1):
        if ordinal == len(responses):
            lines.append(f"    else {response}; fi ;;")
        elif ordinal == 1:
            lines.append(f'    if [ "$n" = "1" ]; then {response}')
        else:
            lines.append(f'    elif [ "$n" = "{ordinal}" ]; then {response}')
    return "\n".join(lines)


def install_aws_stub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    answers: dict[str, str | list[str]] | None = None,
) -> Path:
    """Put an ``aws`` on PATH that answers every call this probe makes, recording each."""
    merged: dict[str, str | list[str]] = {**DEFAULT_AWS_ANSWERS, **(answers or {})}
    recording = tmp_path / "aws-calls.txt"
    counter_dir = tmp_path / "counters"
    counter_dir.mkdir(exist_ok=True)
    branches = [
        stub_branch(key, value if isinstance(value, list) else [value], counter_dir)
        for key, value in merged.items()
    ]
    stub_bin = tmp_path / "bin"
    write_stub(
        stub_bin,
        "aws",
        # One argument per record, separated by the unit separator rather than by a space:
        # the state machine definition is one argument containing spaces, so a space-joined
        # line cannot be split back into the argv that was passed.
        f"printf '%s\\037' \"$@\" >> '{recording}'\nprintf '\\n' >> '{recording}'\n"
        'case "${1-} ${2-}" in\n' + "\n".join(branches) + "\n  *) exit 64 ;;\nesac\n",
    )
    monkeypatch.setenv("PATH", f"{stub_bin}{os.pathsep}{os.defpath}")
    return recording


def recorded_argv(recording: Path) -> list[list[str]]:
    if not recording.exists():
        return []
    return [
        line.split("\x1f")[:-1]
        for line in recording.read_text(encoding="utf-8").splitlines()
        if line
    ]


def recorded(recording: Path) -> list[str]:
    """Each call as one readable line, for the assertions that only need a prefix."""
    return [" ".join(arguments) for arguments in recorded_argv(recording)]


def conditional_write_argv(tmp_path: Path, **overrides: str) -> list[str]:
    arguments: dict[str, str] = {
        "--aws-profile": PROFILE,
        "--aws-region": REGION,
        "--state-machine-role-name": ROLE_NAME,
        "--name-suffix": SUFFIX,
        "--output": str(tmp_path / "conditional-write.json"),
    }
    arguments.update(overrides)
    return [token for pair in arguments.items() for token in pair]


def written_record(tmp_path: Path, name: str = "conditional-write.json") -> Any:
    return json.loads((tmp_path / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------------------
# probe_conditional_write: the dry run
# --------------------------------------------------------------------------------------


def test_a_dry_run_prints_the_plan_and_makes_no_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # No aws on PATH at all: a dry run that reached for one would fail here rather than
    # quietly work on a laptop that happens to have credentials.
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    exit_code = conditional_write_main([*conditional_write_argv(tmp_path), "--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    plan = json.loads(captured.out)
    assert plan["dry_run"] is True
    assert plan["bucket_name"] == BUCKET
    assert not (tmp_path / "conditional-write.json").exists()
    operations = [shlex.split(call["command"])[1:3] for call in plan["planned_calls"]]
    assert operations == [
        ["sts", "get-caller-identity"],
        ["s3api", "create-bucket"],
        ["stepfunctions", "create-state-machine"],
        ["stepfunctions", "start-execution"],
        ["stepfunctions", "describe-execution"],
        ["stepfunctions", "start-execution"],
        ["stepfunctions", "describe-execution"],
        ["s3api", "head-object"],
        ["stepfunctions", "delete-state-machine"],
        ["s3api", "delete-object"],
        ["s3api", "delete-bucket"],
    ]


def test_the_plan_names_the_two_fields_under_test_and_no_account(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    assert conditional_write_main([*conditional_write_argv(tmp_path), "--dry-run"]) == 0

    printed = capsys.readouterr().out
    plan = json.loads(printed)
    parameters = plan["state_machine_definition"]["States"]["ConditionalWrite"]["Parameters"]
    assert parameters["IfNoneMatch"] == "*"
    assert parameters["ChecksumAlgorithm"] == "SHA256"
    # The plan cannot know the account without making a call, so it says so rather than
    # printing something that looks like one.
    assert f"arn:aws:iam::{AWS_ACCOUNT_ID_PLACEHOLDER}:role/{ROLE_NAME}" in printed
    assert scan_for_secrets(printed) == printed


def test_the_dry_run_plan_is_the_run_the_probe_makes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A plan that drifted from the run would make the review of a destructive laptop
    # command worthless, which is the only reason --dry-run exists.
    recording = install_aws_stub(tmp_path, monkeypatch)
    assert conditional_write_main(conditional_write_argv(tmp_path)) == 0

    def as_planned(token: str) -> str:
        for real, placeholder in (
            (STATE_MACHINE_ARN, STATE_MACHINE_ARN_PLACEHOLDER),
            (EXECUTION_ARN, EXECUTION_ARN_PLACEHOLDER),
            (ACCOUNT_ID, AWS_ACCOUNT_ID_PLACEHOLDER),
        ):
            token = token.replace(real, placeholder)
        return token

    made = [[as_planned(token) for token in call] for call in recorded_argv(recording)]
    plan = dry_run_plan(
        ConditionalWriteNames(suffix=SUFFIX),
        profile=PROFILE,
        region=REGION,
        role_name=ROLE_NAME,
    )
    planned = [shlex.split(call["command"])[1:] for call in plan["planned_calls"]]
    assert made == planned


# --------------------------------------------------------------------------------------
# probe_conditional_write: the observation
# --------------------------------------------------------------------------------------


def test_a_refused_second_write_records_the_error_step_functions_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The error name is the deliverable: it is what the state machine's Catch has to
    # match, and the template catches States.ALL until this run says what to put there.
    install_aws_stub(tmp_path, monkeypatch)

    exit_code = conditional_write_main(conditional_write_argv(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    record = written_record(tmp_path)
    assert record["verdict"] == "conditional_write_enforced"
    assert record["findings"] == {
        "if_none_match_refused_the_second_write": True,
        "checksum_sha256_retrievable": True,
    }
    first, second = record["writes"]
    assert first["status"] == "SUCCEEDED"
    assert second["status"] == "FAILED"
    assert second["error"]["text"] == PRECONDITION_ERROR
    assert "did not hold" in second["cause"]["text"]
    assert json.loads(captured.out)["second_write_error"] == PRECONDITION_ERROR


def test_the_checksum_read_back_through_head_object_is_recorded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = install_aws_stub(tmp_path, monkeypatch)

    assert conditional_write_main(conditional_write_argv(tmp_path)) == 0

    head = next(call for call in recorded(recording) if call.startswith("s3api head-object"))
    assert f"--key {PROBE_OBJECT_KEY} --checksum-mode ENABLED" in head
    record = written_record(tmp_path)
    assert record["object_key"] == PROBE_OBJECT_KEY
    assert record["head_object"]["readable"] is True
    assert record["head_object"]["checksum_sha256"]["text"] == STORED_CHECKSUM


def test_a_checksum_that_is_not_stored_is_reported_rather_than_assumed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A checksum requested at write time and absent afterwards is the second way the same
    # parameter can be dropped, so the absence has to be a recorded finding.
    install_aws_stub(
        tmp_path,
        monkeypatch,
        answers={"s3api head-object": canned({"ContentLength": 29})},
    )

    assert conditional_write_main(conditional_write_argv(tmp_path)) == 0

    record = written_record(tmp_path)
    assert record["findings"]["checksum_sha256_retrievable"] is False
    assert record["head_object"]["checksum_sha256"]["text"] is None


def test_a_permitted_second_write_is_the_finding_and_still_writes_the_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # If the integration drops IfNoneMatch the probe has not proved what it set out to
    # prove, so it exits 1 — but what failed is the integration, not the run, and the
    # answer is exactly as worth committing as the other one.
    install_aws_stub(
        tmp_path,
        monkeypatch,
        answers={"stepfunctions describe-execution": [SUCCEEDED, SUCCEEDED]},
    )

    exit_code = conditional_write_main(conditional_write_argv(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.err.strip() == (
        "conditional_write_not_proven:conditional_write_not_enforced"
    )
    record = written_record(tmp_path)
    assert record["verdict"] == "conditional_write_not_enforced"
    assert record["findings"]["if_none_match_refused_the_second_write"] is False


def test_a_first_write_that_failed_proves_nothing_about_the_second(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_aws_stub(
        tmp_path,
        monkeypatch,
        answers={"stepfunctions describe-execution": [REFUSED, REFUSED]},
    )

    assert conditional_write_main(conditional_write_argv(tmp_path)) == 1

    assert written_record(tmp_path)["verdict"] == "first_write_did_not_succeed"


# --------------------------------------------------------------------------------------
# probe_conditional_write: what must not leak, and what must not be left behind
# --------------------------------------------------------------------------------------


def test_nothing_the_account_said_about_itself_survives_into_the_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cause = (
        f"User: arn:aws:sts::{ACCOUNT_ID}:assumed-role/{ROLE_NAME}/probe is not authorized "
        "to perform: s3:PutObject"
    )
    install_aws_stub(
        tmp_path,
        monkeypatch,
        answers={
            "stepfunctions describe-execution": [
                SUCCEEDED,
                canned({"status": "FAILED", "error": "S3.S3Exception", "cause": cause}),
            ]
        },
    )

    assert conditional_write_main(conditional_write_argv(tmp_path)) == 0
    captured = capsys.readouterr()

    written = (tmp_path / "conditional-write.json").read_text(encoding="utf-8")
    assert ACCOUNT_ID not in written
    assert ACCOUNT_ID not in captured.out + captured.err
    assert AWS_ACCOUNT_ID_PLACEHOLDER in written
    assert f"assumed-role/{ROLE_NAME}" in written
    assert scan_for_secrets(written) == written


def test_an_extended_request_id_is_masked_rather_than_taking_the_cause_with_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The extended request id is a long base64 run the scan cannot distinguish from a
    # credential. Refusing the whole cause over it would throw away the deliverable, so it
    # is masked by the field name in front of it and the rest of the message survives.
    install_aws_stub(tmp_path, monkeypatch)

    assert conditional_write_main(conditional_write_argv(tmp_path)) == 0

    written = (tmp_path / "conditional-write.json").read_text(encoding="utf-8")
    assert EXTENDED_REQUEST_ID not in written
    assert "<s3-extended-request-id>" in written
    assert "Status Code: 412" in written
    assert scan_for_secrets(written) == written


def test_a_cause_carrying_a_credential_is_withheld_rather_than_laundered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Masking inside a credential breaks the run that identifies it and hands back text
    # the scan then accepts, so text like this is dropped and the record says it was.
    leaked = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    install_aws_stub(
        tmp_path,
        monkeypatch,
        answers={
            "stepfunctions describe-execution": [
                SUCCEEDED,
                canned({"status": "FAILED", "error": "S3.S3Exception", "cause": leaked}),
            ]
        },
    )

    assert conditional_write_main(conditional_write_argv(tmp_path)) == 0

    written = (tmp_path / "conditional-write.json").read_text(encoding="utf-8")
    assert leaked not in written
    record = written_record(tmp_path)
    second = record["writes"][1]
    assert second["cause"]["text"] is None
    assert second["cause"]["withheld_reason"] == "carries_a_token_the_secret_scan_refuses"
    assert scan_for_secrets(written) == written


def test_everything_created_is_deleted_even_when_the_run_fails_partway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recording = install_aws_stub(
        tmp_path,
        monkeypatch,
        answers={
            "stepfunctions start-execution": aws_error(
                "StartExecution", "AccessDeniedException", "not authorized"
            )
        },
    )

    exit_code = conditional_write_main(conditional_write_argv(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err.strip() == "aws_call_failed:StartExecution:AccessDeniedException"
    made = recorded(recording)
    assert any(call.startswith("stepfunctions delete-state-machine") for call in made)
    assert any(call.startswith("s3api delete-object") for call in made)
    assert any(call.startswith(f"s3api delete-bucket --bucket {BUCKET}") for call in made)
    assert not (tmp_path / "conditional-write.json").exists()


def test_a_bucket_left_behind_is_reported_however_the_probe_ended(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The observation succeeded and the account is still dirty. A shared sandbox is not a
    # place to leave a bucket for somebody else to find, so this outranks the finding.
    install_aws_stub(
        tmp_path,
        monkeypatch,
        answers={"s3api delete-bucket": aws_error("DeleteBucket", "BucketNotEmpty", "not empty")},
    )

    exit_code = conditional_write_main(conditional_write_argv(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err.strip() == (
        "teardown_incomplete:bucket:left_behind:aws_call_failed:DeleteBucket:BucketNotEmpty"
    )
    record = written_record(tmp_path)
    assert record["teardown"]["state_machine"] == "deleted"
    assert record["teardown"]["bucket"].startswith("left_behind:")


def test_nothing_is_created_when_the_session_cannot_be_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recording = install_aws_stub(
        tmp_path,
        monkeypatch,
        answers={"sts get-caller-identity": "exit 254"},
    )

    exit_code = conditional_write_main(conditional_write_argv(tmp_path))

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "aws_call_failed:get-caller-identity"
    assert recorded(recording) == [
        f"sts get-caller-identity --profile {PROFILE} --region {REGION} --output json"
    ]


def test_a_laptop_without_the_aws_cli_creates_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    exit_code = conditional_write_main(conditional_write_argv(tmp_path))

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "aws_cli_unavailable"
    assert not (tmp_path / "conditional-write.json").exists()


@pytest.mark.parametrize("suffix", ["Probe01", "probe_01", "probe.01", "a" * 21, ""])
def test_a_suffix_that_would_not_make_a_bucket_name_is_refused_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    suffix: str,
) -> None:
    recording = install_aws_stub(tmp_path, monkeypatch)

    exit_code = conditional_write_main(
        conditional_write_argv(tmp_path, **{"--name-suffix": suffix})
    )

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "name_suffix_unusable"
    assert recorded(recording) == []


def test_a_record_that_cannot_be_written_is_an_environment_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A missing parent directory is no longer the way to make this happen: the shared
    # writer creates one. A parent that exists and is a file cannot be created, which is
    # the same class of failure and is still not the probe's to recover from.
    install_aws_stub(tmp_path, monkeypatch)
    occupied = tmp_path / "occupied"
    occupied.write_text("", encoding="utf-8")

    exit_code = conditional_write_main(
        conditional_write_argv(tmp_path, **{"--output": str(occupied / "record.json")})
    )

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "output_unwritable"


def test_the_probe_only_ever_names_resources_this_project_owns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = install_aws_stub(tmp_path, monkeypatch)

    assert conditional_write_main(conditional_write_argv(tmp_path)) == 0

    calls = recorded_argv(recording)
    buckets = {name for call in calls for flag, name in pairwise(call) if flag == "--bucket"}
    created = next(call for call in calls if call[1] == "create-state-machine")
    assert buckets == {BUCKET}
    assert created[created.index("--name") + 1] == STATE_MACHINE
    assert BUCKET.startswith("sbsandbox-intern-edullm-")
    assert STATE_MACHINE.startswith("sbsandbox-intern-edullm-")


# --------------------------------------------------------------------------------------
# probe_approvals_readability
# --------------------------------------------------------------------------------------

REPOSITORY = "edu-llm/platform"
#: Ten digits rather than the eleven a GitHub run ID carries today. An eleven-digit
#: integer literal is what ``test_evidence`` calls a reconstructible account ID, since
#: zero-padding it to twelve produces one — which is the same collision the probe itself
#: refuses a twelve-digit run ID for.
RUN_ID = 1823456789
ENVIRONMENT = "sandbox-admission"
APPROVER_LOGIN = "philote-dev"

APPROVED_BODY = [
    {
        "state": "approved",
        "comment": "",
        "environments": [{"name": ENVIRONMENT}],
        "user": {"login": APPROVER_LOGIN, "type": "User"},
    }
]
PENDING_BODY = [
    {
        "environment": {"name": ENVIRONMENT},
        "wait_timer": 0,
        "current_user_can_approve": False,
        "reviewers": [],
    }
]


def gh_response(status: str, body: object) -> str:
    payload = f"HTTP/2.0 {status}\\nContent-Type: application/json\\n\\n{json.dumps(body)}"
    return f"printf '%b' '{payload}'"


def install_gh_stub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    approvals: str | None = None,
    pending: str | None = None,
) -> Path:
    recording = tmp_path / "gh-calls.txt"
    stub_bin = tmp_path / "bin"
    write_stub(
        stub_bin,
        "gh",
        f"printf '%s\\037' \"$@\" >> '{recording}'\nprintf '\\n' >> '{recording}'\n"
        'case "$*" in\n'
        f"  *approvals) {approvals or gh_response('200 OK', APPROVED_BODY)} ;;\n"
        f"  *pending_deployments) {pending or gh_response('200 OK', [])} ;;\n"
        "  *) exit 64 ;;\n"
        "esac\n",
    )
    monkeypatch.setenv("PATH", f"{stub_bin}{os.pathsep}{os.defpath}")
    return recording


def approvals_argv(tmp_path: Path, **overrides: str) -> list[str]:
    arguments: dict[str, str] = {
        "--repository": REPOSITORY,
        "--run-id": str(RUN_ID),
        "--environment": ENVIRONMENT,
        "--output": str(tmp_path / "approvals.json"),
    }
    arguments.update(overrides)
    return [token for pair in arguments.items() for token in pair]


def test_a_populated_approvals_body_closes_the_residual(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_gh_stub(tmp_path, monkeypatch)

    exit_code = approvals_main(approvals_argv(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    record = written_record(tmp_path, "approvals.json")
    assert record["verdict"] == "approver_login_readable"
    assert record["findings"] == {
        "approvals_endpoint_reachable": True,
        "approvals_body_populated": True,
        "approver_login_readable": True,
        "environment_under_test_named_in_approvals": True,
    }
    assert record["approvals"]["approver_login_count"] == 1
    assert record["approvals"]["states"] == ["approved"]


def test_the_approver_is_counted_and_never_named(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_gh_stub(tmp_path, monkeypatch)

    assert approvals_main(approvals_argv(tmp_path)) == 0

    written = (tmp_path / "approvals.json").read_text(encoding="utf-8")
    assert APPROVER_LOGIN not in written
    assert ENVIRONMENT in written
    assert scan_for_secrets(written) == written


def test_an_empty_body_is_reported_as_empty_rather_than_as_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # This is the answer the earlier probe already got from a run with no environment, and
    # reading it as "the token cannot see the body" would be exactly the wrong conclusion.
    install_gh_stub(tmp_path, monkeypatch, approvals=gh_response("200 OK", []))

    exit_code = approvals_main(approvals_argv(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.err.strip() == "approver_login_not_readable:approvals_body_empty"
    record = written_record(tmp_path, "approvals.json")
    assert record["findings"]["approvals_endpoint_reachable"] is True
    assert record["findings"]["approvals_body_populated"] is False
    assert record["environment_under_test"] == ENVIRONMENT


def test_a_gate_still_waiting_is_its_own_answer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_gh_stub(
        tmp_path,
        monkeypatch,
        approvals=gh_response("200 OK", []),
        pending=gh_response("200 OK", PENDING_BODY),
    )

    assert approvals_main(approvals_argv(tmp_path)) == 1

    record = written_record(tmp_path, "approvals.json")
    assert record["verdict"] == "environment_gate_still_pending"
    assert record["pending_deployments"]["environments"] == [ENVIRONMENT]


def test_a_forbidden_answer_is_a_finding_rather_than_a_broken_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # gh exits non-zero for a 403 and prints the response anyway. The status is what says
    # whether the endpoint refused this token, and the exit code cannot tell that apart
    # from never having reached GitHub.
    install_gh_stub(
        tmp_path,
        monkeypatch,
        approvals=(
            gh_response("403 Forbidden", {"message": "Resource not accessible by integration"})
            + "; exit 1"
        ),
    )

    assert approvals_main(approvals_argv(tmp_path)) == 1

    record = written_record(tmp_path, "approvals.json")
    assert record["verdict"] == "approvals_forbidden"
    assert record["approvals"]["http_status"] == 403
    assert record["findings"]["approvals_endpoint_reachable"] is False


def test_the_probe_makes_two_reads_and_nothing_else(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = install_gh_stub(tmp_path, monkeypatch)

    assert approvals_main(approvals_argv(tmp_path)) == 0

    made = recorded_argv(recording)
    assert [call[-1] for call in made] == [
        f"repos/{REPOSITORY}/actions/runs/{RUN_ID}/approvals",
        f"repos/{REPOSITORY}/actions/runs/{RUN_ID}/pending_deployments",
    ]
    assert all(call[:2] == ["api", "--include"] for call in made)
    mutating = {"--method", "-X", "--field", "-f", "--raw-field", "-F", "--input"}
    assert not any(mutating & set(call) for call in made)


def test_a_run_id_a_secret_scan_would_call_an_account_is_refused_with_a_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Run IDs are bare decimals and grow. A twelve-digit one is indistinguishable from an
    # AWS account ID to this repository's scan, so it is refused by name rather than
    # producing a record nobody can commit or a masked identifier nobody can use.
    recording = install_gh_stub(tmp_path, monkeypatch)

    exit_code = approvals_main(approvals_argv(tmp_path, **{"--run-id": ACCOUNT_ID}))

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "run_id_indistinguishable_from_an_aws_account_id"
    assert recorded(recording) == []


@pytest.mark.parametrize("repository", ["platform", "edu-llm/platform/extra", "edu-llm/"])
def test_a_repository_that_is_not_owner_and_name_is_refused_before_any_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    repository: str,
) -> None:
    recording = install_gh_stub(tmp_path, monkeypatch)

    exit_code = approvals_main(approvals_argv(tmp_path, **{"--repository": repository}))

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "repository_unusable"
    assert recorded(recording) == []


def test_a_laptop_without_the_github_cli_proves_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    exit_code = approvals_main(approvals_argv(tmp_path))

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "gh_cli_unavailable"
    assert not (tmp_path / "approvals.json").exists()


def test_an_answer_without_a_status_line_is_not_read_as_an_empty_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_gh_stub(tmp_path, monkeypatch, approvals="printf '%s' 'not an http response'")

    exit_code = approvals_main(approvals_argv(tmp_path))

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "gh_response_unreadable"
    assert not (tmp_path / "approvals.json").exists()


def test_an_approvals_record_that_cannot_be_written_is_an_environment_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_gh_stub(tmp_path, monkeypatch)
    occupied = tmp_path / "occupied"
    occupied.write_text("", encoding="utf-8")

    exit_code = approvals_main(
        approvals_argv(tmp_path, **{"--output": str(occupied / "record.json")})
    )

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "output_unwritable"
