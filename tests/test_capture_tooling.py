"""The write path every capture tool shares, tested where it is defined.

This module owns the rule that matters most in a capture: never write outside the phase's
working directory, and never write a document that looks like it carries a credential. It
had no tests of its own, and the only proof it worked was that five tools it is meant to
serve happened to produce committed evidence nobody objected to. That is proof about the
records, not about the rule -- a record with no secret in it passes a scan that never ran.

The CLI wrappers are exercised against real stub executables on ``PATH`` rather than a
patched ``subprocess.run``, for the reason the Phase 1 CLI tests give: what is being
claimed is about a process being launched with an argument list, and a patched call site
cannot say whether an argument containing a space arrived as one argument or two. The two
paths a stub cannot reach -- a CLI that is not installed, and one that never returns --
are patched, because installing neither is a property of the machine.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from workflow_support import write_stub

from edullm_platform.capture_tooling import (
    EXIT_UNUSABLE,
    AccountIdentity,
    CaptureFailedError,
    account_identity,
    aws,
    aws_json,
    check_output_location,
    observed_now,
    report,
    run_capture,
    write_model,
    write_record,
    write_sanitized_text,
)
from edullm_platform.contracts.base import ContractModel

PROFILE = "sandbox"
REGION = "us-east-1"
ALLOWED_SUFFIX = Path("docs-frank/working/phase-9-evidence")

#: A real twelve-digit number, because what these tests are for is that none of it reaches
#: a file. Reversed rather than written out, matching the tracked-tree tripwire in
#: tests/test_evidence.py.
ACCOUNT_ID = "210987654321"[::-1]

#: Forty hexadecimal characters, which is both a commit SHA and the shape of an AWS secret
#: access key. Which one this module decides it is, is the whole of ``allow_content_digests``.
COMMIT_SHA = "b067a31e4c9d8f2a15e3b7c04d6a89f1e2c3b4a5"

#: A digest holding twelve consecutive decimal characters, which roughly one in six do.
#: Written to contain them on purpose: the masking pass has to consume the digest whole
#: before the account-id pattern can reach inside it.
DIGITS_INSIDE_A_DIGEST = "210987654321"
SHA256_DIGEST = "sha256:" + "a" * 20 + DIGITS_INSIDE_A_DIGEST + "b" * 32


class Recorded(ContractModel):
    """A model with a field that is present and null, which is the case worth writing."""

    name: str
    detail: str | None = None


@pytest.fixture
def stubbed_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A ``PATH`` holding only stubs this test installed."""
    directory = tmp_path / "bin"
    directory.mkdir()
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    return directory


def echoing_aws_stub(directory: Path, answer: str = "{}") -> Path:
    """An ``aws`` that prints a fixed answer and its own argument list, one per line."""
    return write_stub(
        directory,
        "aws",
        'printf "%s\\n" "$@" > "$(dirname "$0")/arguments"\n' f"echo '{answer}'\n",
    )


def invoked_arguments(directory: Path) -> list[str]:
    return (directory / "arguments").read_text(encoding="utf-8").splitlines()


def test_an_aws_call_names_the_profile_and_asks_for_json(stubbed_path: Path) -> None:
    echoing_aws_stub(stubbed_path)

    completed = aws(["sts", "get-caller-identity"], profile=PROFILE)

    assert completed.returncode == 0
    assert invoked_arguments(stubbed_path) == [
        "sts",
        "get-caller-identity",
        "--profile",
        PROFILE,
        "--output",
        "json",
    ]


def test_an_aws_call_names_a_region_only_when_it_was_given_one(stubbed_path: Path) -> None:
    echoing_aws_stub(stubbed_path)

    aws(["ec2", "describe-vpcs"], profile=PROFILE, region=REGION)

    assert invoked_arguments(stubbed_path)[-2:] == ["--region", REGION]


def test_an_argument_holding_a_space_arrives_as_one_argument(stubbed_path: Path) -> None:
    """``shell=False`` and a list, which is what stops a filter becoming two arguments."""
    echoing_aws_stub(stubbed_path)
    query = "Name=instance-state-name,Values=pending running"

    aws(["ec2", "describe-instances", "--filters", query], profile=PROFILE)

    assert query in invoked_arguments(stubbed_path)


def test_a_non_zero_exit_is_returned_whole_rather_than_raised(stubbed_path: Path) -> None:
    """A ``--dry-run`` probe is only readable through the code it printed."""
    write_stub(stubbed_path, "aws", "echo 'UnauthorizedOperation' >&2\nexit 254\n")

    completed = aws(["ec2", "run-instances", "--dry-run"], profile=PROFILE)

    assert completed.returncode == 254
    assert "UnauthorizedOperation" in completed.stderr


def test_an_absent_aws_cli_is_a_capture_failure_naming_the_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> object:
        raise OSError("no such file")

    monkeypatch.setattr(subprocess, "run", refuse)

    with pytest.raises(CaptureFailedError) as raised:
        aws(["sts", "get-caller-identity"], profile=PROFILE)

    assert raised.value.reason == "aws_cli_unavailable"


def test_a_call_that_never_returns_names_the_call_that_hung(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def hang(*_args: object, **_kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd="aws", timeout=90)

    monkeypatch.setattr(subprocess, "run", hang)

    with pytest.raises(CaptureFailedError) as raised:
        aws(["cloudtrail", "lookup-events"], profile=PROFILE)

    assert raised.value.reason == "aws_call_timed_out:cloudtrail:lookup-events"


def test_a_required_answer_is_parsed(stubbed_path: Path) -> None:
    echoing_aws_stub(stubbed_path, '{"Account": "unread"}')

    assert aws_json(["sts", "get-caller-identity"], profile=PROFILE) == {"Account": "unread"}


def test_a_failed_call_is_never_reported_as_an_empty_answer(stubbed_path: Path) -> None:
    """The distinction the whole module rests on: an absent record is a claim."""
    write_stub(stubbed_path, "aws", "echo 'AccessDenied' >&2\nexit 255\n")

    with pytest.raises(CaptureFailedError) as raised:
        aws_json(["iam", "get-role", "--role-name", "anything"], profile=PROFILE)

    assert raised.value.reason == "aws_call_failed:iam:get-role"


def test_a_call_that_answered_nothing_at_all_is_an_empty_mapping(stubbed_path: Path) -> None:
    write_stub(stubbed_path, "aws", "true\n")

    assert aws_json(["batch", "list-jobs"], profile=PROFILE) == {}


def test_an_unreadable_answer_names_the_service_that_gave_it(stubbed_path: Path) -> None:
    write_stub(stubbed_path, "aws", "echo 'not json'\n")

    with pytest.raises(CaptureFailedError) as raised:
        aws_json(["s3api", "list-objects-v2"], profile=PROFILE)

    assert raised.value.reason == "aws_answer_unreadable:s3api"


def test_the_caller_identity_carries_the_account_and_the_arn(stubbed_path: Path) -> None:
    arn = f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/somebody/session"
    echoing_aws_stub(stubbed_path, json.dumps({"Account": ACCOUNT_ID, "Arn": arn}))

    assert account_identity(profile=PROFILE, region=REGION) == AccountIdentity(
        account_id=ACCOUNT_ID, arn=arn
    )


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param({"Arn": "arn:aws:sts::x:assumed-role/a/b"}, id="no account"),
        pytest.param({"Account": ACCOUNT_ID}, id="no arn"),
        pytest.param({"Account": "", "Arn": "arn"}, id="empty account"),
    ],
)
def test_an_identity_that_is_not_one_stops_the_capture(
    stubbed_path: Path, answer: dict[str, str]
) -> None:
    echoing_aws_stub(stubbed_path, json.dumps(answer))

    with pytest.raises(CaptureFailedError) as raised:
        account_identity(profile=PROFILE, region=REGION)

    assert raised.value.reason == "caller_identity_unreadable"


def test_an_observation_is_recorded_to_the_second_in_utc() -> None:
    observed = observed_now()

    assert observed.tzinfo is UTC
    assert observed.microsecond == 0


def test_a_path_under_the_phases_working_directory_is_accepted(tmp_path: Path) -> None:
    check_output_location(tmp_path / ALLOWED_SUFFIX / "runs", allowed_suffix=ALLOWED_SUFFIX)


def test_a_path_outside_it_is_refused_by_naming_where_it_must_be(tmp_path: Path) -> None:
    """``fixtures/`` is the destination this exists to keep a live capture out of."""
    with pytest.raises(CaptureFailedError) as raised:
        check_output_location(tmp_path / "fixtures" / "evidence", allowed_suffix=ALLOWED_SUFFIX)

    assert raised.value.reason == f"output_must_be_under:{ALLOWED_SUFFIX.as_posix()}"


def test_a_record_is_written_indented_key_sorted_and_newline_terminated(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "one" / "record.sanitized.json"

    write_record(path, {"second": 2, "first": 1})

    assert path.read_text(encoding="utf-8") == '{\n  "first": 1,\n  "second": 2\n}\n'


def test_a_record_lands_in_a_directory_that_did_not_exist(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b" / "c" / "record.json"

    write_record(path, {"ok": True})

    assert path.is_file()


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(ACCOUNT_ID, id="an account id"),
        pytest.param("ghp_" + "a" * 36, id="a github token"),
        pytest.param("-----BEGIN PRIVATE KEY-----", id="a private key"),
        pytest.param(COMMIT_SHA, id="forty hexadecimal characters"),
    ],
)
def test_a_document_that_looks_like_it_carries_a_credential_is_never_written(
    tmp_path: Path, value: str
) -> None:
    path = tmp_path / "record.json"

    with pytest.raises(CaptureFailedError) as raised:
        write_record(path, {"field": value})

    assert raised.value.reason == "record_holds_a_credential"
    assert not path.exists()


def test_a_record_whose_shape_holds_digests_may_say_so_and_still_be_refused_a_secret(
    tmp_path: Path,
) -> None:
    """The exemption is for identifiers, and gives up only the forty-character key."""
    write_record(
        tmp_path / "allowed.json",
        {"commit": COMMIT_SHA, "digest": SHA256_DIGEST},
        allow_content_digests=True,
    )

    with pytest.raises(CaptureFailedError):
        write_record(
            tmp_path / "refused.json",
            {"commit": COMMIT_SHA, "account": ACCOUNT_ID},
            allow_content_digests=True,
        )


def test_the_exemption_masks_only_what_is_scanned_and_not_what_is_committed(
    tmp_path: Path,
) -> None:
    """A record exists to carry its digests; masking them for the scan must not keep them out."""
    path = tmp_path / "record.json"

    write_record(path, {"commit": COMMIT_SHA}, allow_content_digests=True)

    assert json.loads(path.read_text(encoding="utf-8")) == {"commit": COMMIT_SHA}


def test_a_field_that_is_present_and_null_is_written_as_null(tmp_path: Path) -> None:
    """"Not applicable here" and "we looked and there was nothing" are different answers."""
    path = tmp_path / "record.json"

    write_model(path, Recorded(name="one"))

    assert json.loads(path.read_text(encoding="utf-8")) == {"name": "one", "detail": None}


def test_a_model_carrying_a_credential_is_refused_like_any_other_document(
    tmp_path: Path,
) -> None:
    with pytest.raises(CaptureFailedError):
        write_model(tmp_path / "record.json", Recorded(name="one", detail=ACCOUNT_ID))


def test_captured_text_is_committed_with_the_account_id_masked(tmp_path: Path) -> None:
    path = tmp_path / "message.json"

    write_sanitized_text(path, f'{{"message": "denied for {ACCOUNT_ID}"}}')

    written = path.read_text(encoding="utf-8")
    assert ACCOUNT_ID not in written
    assert "<aws-account-id>" in written


def test_a_digest_holding_twelve_decimal_digits_is_kept_rather_than_read_as_an_account(
    tmp_path: Path,
) -> None:
    """Three Phase 3 runs were captured before one turned up whose digest tripped this."""
    path = tmp_path / "record.json"

    write_sanitized_text(path, f'{{"checksum": "{SHA256_DIGEST}"}}')

    assert SHA256_DIGEST in path.read_text(encoding="utf-8")


def test_text_carrying_another_credential_is_refused_rather_than_laundered(
    tmp_path: Path,
) -> None:
    path = tmp_path / "record.json"

    with pytest.raises(ValueError):
        write_sanitized_text(path, f'{{"token": "ghp_{"a" * 36}"}}')

    assert not path.exists()


def test_a_capture_that_answered_passes_its_own_verdict_through(tmp_path: Path) -> None:
    """"Captured, and found drift" is neither success nor an unusable capture."""
    assert (
        run_capture(
            lambda: 1, output_dir=tmp_path / ALLOWED_SUFFIX, allowed_suffix=ALLOWED_SUFFIX
        )
        == 1
    )


def test_a_capture_aimed_outside_the_working_directory_never_starts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    started = False

    def target() -> int:
        nonlocal started
        started = True
        return 0

    outcome = run_capture(
        target, output_dir=tmp_path / "fixtures", allowed_suffix=ALLOWED_SUFFIX
    )

    assert outcome == EXIT_UNUSABLE
    assert started is False
    assert capsys.readouterr().err.strip() == f"output_must_be_under:{ALLOWED_SUFFIX.as_posix()}"


def test_a_failed_capture_prints_the_reason_an_operator_greps_for(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def target() -> int:
        raise CaptureFailedError("aws_call_failed:iam:get-role")

    outcome = run_capture(
        target, output_dir=tmp_path / ALLOWED_SUFFIX, allowed_suffix=ALLOWED_SUFFIX
    )

    assert outcome == EXIT_UNUSABLE
    assert capsys.readouterr().err.strip() == "aws_call_failed:iam:get-role"


def test_a_capture_that_could_not_be_written_down_says_so_without_quoting_the_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def target() -> int:
        raise OSError("Read-only file system: '/somewhere/private'")

    outcome = run_capture(
        target, output_dir=tmp_path / ALLOWED_SUFFIX, allowed_suffix=ALLOWED_SUFFIX
    )

    assert outcome == EXIT_UNUSABLE
    assert capsys.readouterr().err.strip() == "output_unwritable"


def test_a_summary_is_printed_in_the_shape_every_tool_already_prints_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    summary: dict[str, Any] = {"written": ["b", "a"], "targets": ["run"]}

    report(summary)

    assert capsys.readouterr().out == (
        '{\n  "targets": [\n    "run"\n  ],\n  "written": [\n    "b",\n    "a"\n  ]\n}\n'
    )


def test_an_observation_is_not_in_the_future_of_the_clock_that_made_it() -> None:
    assert observed_now() <= datetime.now(tz=UTC)
