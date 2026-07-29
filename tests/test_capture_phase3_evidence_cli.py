"""The laptop command that reads one finished run out of the account and writes it down.

Every case runs the real command against a stub ``aws`` on PATH, and the stub's answers
are derived from the committed captures in ``fixtures/evidence/phase-3/`` rather than
typed out. The committed files are the *inputs* here -- the lineage bodies are what S3
would hand back, and the record files say what the services must have answered for those
records to exist -- and they are also what the tool's output is compared against. A break
anywhere between the two shows up as a file that no longer matches.

Nothing is copied into a golden directory. Reconstructing the service answers from the
committed records and then asserting the records back is the whole point: a golden copy
would only prove the tool still agrees with a snapshot of itself.

The account ID the stub returns is a real twelve-digit number, because the thing most
worth proving about a capture is that none of it reaches a file.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from workflow_support import write_stub

from edullm_platform.evidence import AWS_ACCOUNT_ID_PLACEHOLDER, scan_for_secrets
from tools.capture_phase3_evidence import (
    ALLOWED_OUTPUT_SUFFIX,
    COMPUTE_ENVIRONMENT_NAME,
    JOB_QUEUE_NAME,
    LINEAGE_BUCKET,
    RECORD_CONTRACTS,
    STATE_MACHINE_NAME,
    main,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMITTED = PROJECT_ROOT / "fixtures" / "evidence" / "phase-3"
PROFILE = "sbsandbox"
REGION = "us-east-1"
ACCOUNT_ID = "123456789012"

#: The run that was submitted and ran. Fourteen files come out of it: five records about
#: the run, and the nine lineage objects it wrote.
RAN = "run_019fa96f-8f10-705a-a7a9-69c42eafce16"

#: The run admission refused. It has no binding, so the capture takes the other fork --
#: a refusal record, and the obligation to establish that no Batch job exists anywhere.
REFUSED = "run_019fa984-085c-7088-9c94-799e4b5d9126"

CALLER_ARN = f"arn:aws:iam::{ACCOUNT_ID}:user/somebody"
OIDC_PROVIDER_ARN = (
    f"arn:aws:iam::{ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
)

#: What an observation timestamp is replaced with before two records are compared. Every
#: other byte has to match; this one is the instant the capture ran and never will.
OBSERVED_AT_PLACEHOLDER = '"observed_at": "<when-the-capture-ran>"'
OBSERVED_AT_FIELD = re.compile(r'"observed_at": "[^"]*"')


def committed(relative: str) -> Any:
    return json.loads((COMMITTED / relative).read_text(encoding="utf-8"))


def epoch_millis(instant: str) -> int:
    """A Batch timestamp back in the units Batch reports it in."""
    return round(datetime.fromisoformat(instant).timestamp() * 1000)


# --------------------------------------------------------------------------------------
# What the services must have said, for the committed records to be what they are
# --------------------------------------------------------------------------------------


def lineage_answers(run_id: str) -> dict[str, Any]:
    """The three S3 calls, answered out of the attestation and the bodies beside it."""
    attestation = committed(f"runs/{run_id}/lineage-attestation.sanitized.json")
    keys = [str(entry["key"]) for entry in attestation["objects"]]
    answers: dict[str, Any] = {}
    for prefix in RECORD_CONTRACTS:
        matching = [key for key in keys if key.startswith(f"{prefix}/{run_id}")]
        answers[
            f"s3api list-objects-v2 --bucket {LINEAGE_BUCKET} --prefix {prefix}/{run_id}"
        ] = matching or None
    for entry in attestation["objects"]:
        answers[
            f"s3api head-object --bucket {LINEAGE_BUCKET} --key {entry['key']} --checksum-mode"
        ] = {
            "VersionId": entry["version_id"],
            "ChecksumSHA256": entry["checksum_sha256"],
            "ContentLength": entry["content_length"],
        }
    return answers


def stored_bodies(run_id: str) -> dict[str, str]:
    """What S3 holds for each key, which is the committed body with the account put back.

    The committed copy has been through ``write_sanitized_text`` and carries
    ``<aws-account-id>`` where the store carries twelve digits. Handing that back would
    make the stub answer with a document nothing ever wrote, and it would change the one
    field the tool judges on the raw bytes: a binding whose ARNs have been masked no
    longer loads as ``BatchJobBinding``, so a capture fed the masked copy would report
    every binding as corrupt. Putting the account back is what makes the stub's answer a
    plausible object -- and it is what gives the masking on the way out something to do.
    """
    attestation = committed(f"runs/{run_id}/lineage-attestation.sanitized.json")
    return {
        str(entry["key"]): (COMMITTED / "runs" / run_id / "records" / str(entry["key"]))
        .read_text(encoding="utf-8")
        .replace(AWS_ACCOUNT_ID_PLACEHOLDER, ACCOUNT_ID)
        for entry in attestation["objects"]
        if (COMMITTED / "runs" / run_id / "records" / str(entry["key"])).exists()
    }


def batch_job_answer(run_id: str) -> dict[str, Any]:
    """One Batch job as the service describes it, read back off the captured record."""
    job = committed(f"runs/{run_id}/batch-job.sanitized.json")
    described: dict[str, Any] = {
        "jobId": job["batch_job_id"],
        "jobName": job["batch_job_name"],
        "status": job["status"],
        "statusReason": job["status_reason"],
        "jobQueue": (
            f"arn:aws:batch:{REGION}:{ACCOUNT_ID}:job-queue/{job['job_queue_name']}"
        ),
        "jobDefinition": (
            f"arn:aws:batch:{REGION}:{ACCOUNT_ID}:job-definition/{job['job_definition_name']}"
        ),
        "container": {
            "exitCode": job["container_exit_code"],
            "logStreamName": job["log_stream_name"],
        },
        "attempts": [{}] * int(job["attempt_count"]),
    }
    for field, reported in (("started_at", "startedAt"), ("stopped_at", "stoppedAt")):
        if job[field] is not None:
            described[reported] = epoch_millis(job[field])
    return {"jobs": [described]}


def cloudtrail_event(record: dict[str, Any]) -> dict[str, Any]:
    """One CloudTrail entry, which carries its record as a JSON string rather than inline."""
    return {"CloudTrailEvent": json.dumps(record)}


def session_answers(run_id: str) -> dict[str, Any]:
    """The two CloudTrail lookups that join a run to the GitHub session that started it.

    Both are built from the captured session, so the join the tool makes -- the
    ``StartExecution`` naming this run, and the one ``AssumeRoleWithWebIdentity`` whose
    event time is the creation instant that call recorded -- is the join the stub offers.
    """
    session = committed(f"runs/{run_id}/oidc-session.sanitized.json")
    subject = str(session["oidc_subject"])
    issued_at = str(session["assumed_at"])
    return {
        "cloudtrail lookup-events --lookup-attributes "
        "AttributeKey=EventName,AttributeValue=StartExecution": {
            "Events": [
                cloudtrail_event(
                    {
                        "eventName": "StartExecution",
                        "requestParameters": {"name": run_id},
                        "userIdentity": {
                            "sessionContext": {"attributes": {"creationDate": issued_at}}
                        },
                    }
                )
            ]
        },
        "cloudtrail lookup-events --lookup-attributes "
        "AttributeKey=EventName,AttributeValue=AssumeRoleWithWebIdentity": {
            "Events": [
                cloudtrail_event(
                    {
                        "eventID": session["event_id"],
                        "eventName": session["event_name"],
                        "eventSource": session["event_source"],
                        "eventTime": issued_at,
                        "userIdentity": {
                            "identityProvider": OIDC_PROVIDER_ARN,
                            "principalId": (
                                f"{OIDC_PROVIDER_ARN}:{session['oidc_audience']}:{subject}"
                            ),
                            "userName": subject,
                        },
                        "responseElements": {
                            "assumedRoleUser": {
                                "arn": (
                                    f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/"
                                    f"{session['role_name']}/{session['session_name']}"
                                )
                            },
                            "credentials": {"expiration": session["expires_at"]},
                        },
                    }
                )
            ]
        },
    }


def compute_environment_answers() -> dict[str, Any]:
    environment = committed("compute-environment.sanitized.json")
    subnet_ids = [str(subnet) for subnet in environment["subnet_ids"]]
    return {
        f"batch describe-compute-environments --compute-environments {COMPUTE_ENVIRONMENT_NAME}": {
            "computeEnvironments": [
                {
                    "computeEnvironmentName": environment["compute_environment_name"],
                    "status": environment["status"],
                    "state": environment["state"],
                    "computeResources": {
                        "subnets": subnet_ids,
                        "securityGroupIds": environment["security_group_ids"],
                        "instanceTypes": environment["instance_types"],
                        "desiredvCpus": environment["desired_vcpus"],
                        "minvCpus": environment["minimum_vcpus"],
                        "maxvCpus": environment["maximum_vcpus"],
                    },
                }
            ]
        },
        f"ec2 describe-subnets --subnet-ids {' '.join(subnet_ids)}": [
            environment["vpc_id"] for _subnet in subnet_ids
        ],
        "batch describe-job-queues --query": [
            {
                "name": name,
                "order": [
                    {
                        "computeEnvironment": (
                            f"arn:aws:batch:{REGION}:{ACCOUNT_ID}:compute-environment/"
                            f"{COMPUTE_ENVIRONMENT_NAME}"
                        )
                    }
                ],
            }
            for name in environment["job_queue_names"]
        ],
        "ec2 describe-instances --filters Name=tag:": environment["live_instance_count"],
    }


def execution_answer(run_id: str) -> dict[str, Any]:
    execution = committed(f"runs/{run_id}/admission-execution.sanitized.json")
    return {
        "name": execution["name"],
        "status": execution["status"],
        "error": execution["error"],
    }


def log_stream_answer(run_id: str) -> dict[str, Any]:
    stream = committed(f"runs/{run_id}/log-stream.sanitized.json")
    return {"events": [{"message": line} for line in stream["lines"]]}


def ran_run_answers(run_id: str) -> dict[str, Any]:
    job = committed(f"runs/{run_id}/batch-job.sanitized.json")
    stream = committed(f"runs/{run_id}/log-stream.sanitized.json")
    return {
        f"batch describe-jobs --jobs {job['batch_job_id']}": batch_job_answer(run_id),
        f"logs get-log-events --log-group-name {stream['log_group_name']} "
        f"--log-stream-name {stream['log_stream_name']}": log_stream_answer(run_id),
        **session_answers(run_id),
    }


def refused_run_answers() -> dict[str, Any]:
    """One answer for every status the search walks: no job of this name, anywhere."""
    return {f"batch list-jobs --job-queue {JOB_QUEUE_NAME} --job-status": []}


def account_answers(run_id: str) -> dict[str, Any]:
    """Every call ``--target run`` makes for one run, plus the environment it ran on."""
    answers: dict[str, Any] = {
        "sts get-caller-identity": {"Account": ACCOUNT_ID, "Arn": CALLER_ARN},
        f"stepfunctions describe-execution --execution-arn arn:aws:states:{REGION}:"
        f"{ACCOUNT_ID}:execution:{STATE_MACHINE_NAME}:{run_id}": execution_answer(run_id),
        **lineage_answers(run_id),
        **compute_environment_answers(),
    }
    if (COMMITTED / "runs" / run_id / "batch-job.sanitized.json").exists():
        answers.update(ran_run_answers(run_id))
    else:
        answers.update(refused_run_answers())
    return answers


# --------------------------------------------------------------------------------------
# The stub
# --------------------------------------------------------------------------------------


def install_aws_stub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    run_id: str = RAN,
    answers: dict[str, Any] | None = None,
    bodies: dict[str, str] | None = None,
    failures: dict[str, tuple[int, str]] | None = None,
) -> Path:
    """Put an ``aws`` on PATH that answers by the leading words of the call it was given.

    Matched on a prefix of the whole argument list rather than on the service and
    operation alone, because this tool asks ``s3api`` four different questions and
    ``list-objects-v2`` six of them. Every prefix below stops before the first ``--query``,
    which is where the shell's own pattern characters start appearing.
    """
    responses = {**account_answers(run_id), **(answers or {})}
    refusals = failures or {}
    recording = tmp_path / "aws-calls.txt"
    branches = []
    store = tmp_path / "lineage-store"
    for key, text in {**stored_bodies(run_id), **(bodies or {})}.items():
        source = store / key
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(text, encoding="utf-8")
        # get-object names its destination as the last positional argument, which is
        # $7 here: s3api get-object --bucket B --key K <destination>.
        call = f"s3api get-object --bucket {LINEAGE_BUCKET} --key {key} "
        branches.append(f"  \"{call}\"*)\n    cp '{source}' \"$7\"; printf '{{}}'\n    ;;")
    for key, answer in responses.items():
        if key in refusals:
            continue
        # The heredoc terminator has to own its line, so every branch is written out
        # across several lines and closed by a ";;" of its own.
        body = f"cat <<'RESPONSE'\n{json.dumps(answer)}\nRESPONSE"
        branches.append(f'  "{key}"*)\n{body}\n    ;;')
    for key, (status, message) in refusals.items():
        branches.insert(0, f'  "{key}"*)\n    printf \'%s\\n\' {json.dumps(message)} >&2\n'
                           f"    exit {status}\n    ;;")
    stub_bin = tmp_path / "bin"
    write_stub(
        stub_bin,
        "aws",
        f"printf '%s\\n' \"$*\" >> '{recording}'\n"
        'case "$*" in\n' + "\n".join(branches) + "\n  *) exit 64 ;;\nesac\n",
    )
    monkeypatch.setenv("PATH", f"{stub_bin}{os.pathsep}{os.defpath}")
    return recording


def output_dir(tmp_path: Path) -> Path:
    return tmp_path / ALLOWED_OUTPUT_SUFFIX / "capture"


def capture(tmp_path: Path, *arguments: str) -> int:
    return main(
        [
            "--aws-profile",
            PROFILE,
            "--output-dir",
            str(output_dir(tmp_path)),
            *arguments,
        ]
    )


def written(tmp_path: Path) -> dict[str, str]:
    root = output_dir(tmp_path)
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def without_the_observation_instant(text: str) -> str:
    return OBSERVED_AT_FIELD.sub(OBSERVED_AT_PLACEHOLDER, text)


# --------------------------------------------------------------------------------------
# What --target run writes, against what is committed for the same run
# --------------------------------------------------------------------------------------


@pytest.mark.slow
def test_capturing_a_run_writes_its_five_records_and_the_nine_objects_it_wrote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_aws_stub(tmp_path, monkeypatch)

    assert capture(tmp_path, "--target", "run", "--run-id", RAN) == 0

    assert set(written(tmp_path)) == {
        f"runs/{RAN}/admission-execution.sanitized.json",
        f"runs/{RAN}/batch-job.sanitized.json",
        f"runs/{RAN}/lineage-attestation.sanitized.json",
        f"runs/{RAN}/log-stream.sanitized.json",
        f"runs/{RAN}/oidc-session.sanitized.json",
        *(
            f"runs/{RAN}/records/{entry['key']}"
            for entry in committed(f"runs/{RAN}/lineage-attestation.sanitized.json")["objects"]
        ),
        "compute-environment.sanitized.json",
    }


@pytest.mark.slow
def test_every_file_a_run_capture_writes_is_the_one_committed_for_that_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The round trip: committed record to service answer to captured record and back to
    # agreement, byte for byte once the instant the capture ran is taken out. A change to
    # how any of these is projected shows up here as a file that no longer matches.
    install_aws_stub(tmp_path, monkeypatch)

    assert capture(tmp_path, "--target", "run", "--run-id", RAN) == 0

    for name, text in written(tmp_path).items():
        if name == "compute-environment.sanitized.json":
            continue
        expected = (COMMITTED / name).read_text(encoding="utf-8")
        assert without_the_observation_instant(text) == without_the_observation_instant(
            expected
        ), name


@pytest.mark.slow
def test_a_clean_run_capture_reports_every_file_it_wrote_and_nothing_withheld(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_aws_stub(tmp_path, monkeypatch)

    exit_code = capture(tmp_path, "--target", "run", "--run-id", RAN)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    summary = json.loads(captured.out)
    assert summary["targets"] == ["run"]
    assert summary["runs"] == [RAN]
    assert summary["written"] == sorted(written(tmp_path))
    assert len(summary["written"]) == 15
    assert summary["objects_that_do_not_load"] == []
    assert summary["bodies_withheld_because_they_do_not_load"] == 0
    assert summary["verdict"] == "ok"


@pytest.mark.slow
def test_an_object_that_does_not_load_is_recorded_and_its_body_is_not_committed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Three bindings in the store were written before the ASL fix and carry a whole
    # admission payload in the field where a fan-out size belongs. They are attested,
    # versioned and intact, and they are refused by the contract that defines a binding.
    # The capture succeeds -- the store is write-once, so no later run would do better --
    # and the body stays out of the record while its key, digest and version stay in.
    key = f"binding/{RAN}.json"
    corrupt = json.loads(stored_bodies(RAN)[key])
    corrupt["attempts"] = {"approver": "somebody", "image_scan": {"findings": []}}
    install_aws_stub(
        tmp_path,
        monkeypatch,
        bodies={key: json.dumps(corrupt, separators=(",", ":"))},
    )

    exit_code = capture(tmp_path, "--target", "run", "--run-id", RAN)
    captured = capsys.readouterr()

    assert exit_code == 0
    summary = json.loads(captured.out)
    assert summary["objects_that_do_not_load"] == [key]
    assert summary["bodies_withheld_because_they_do_not_load"] == 1
    assert captured.err.splitlines() == [f"object_does_not_load_as_its_contract:{key}"]
    files = written(tmp_path)
    assert f"runs/{RAN}/records/{key}" not in files
    attestation = json.loads(files[f"runs/{RAN}/lineage-attestation.sanitized.json"])
    withheld = next(entry for entry in attestation["objects"] if entry["key"] == key)
    assert withheld["loads_as_contract"] is False
    assert withheld["version_id"] and withheld["checksum_sha256"]


@pytest.mark.slow
def test_a_run_admission_refused_is_captured_as_a_refusal_with_no_job_anywhere(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No binding is what separates the two shapes, and the refused run has to establish
    # something the other one never asks: that no Batch job exists in any status.
    recording = install_aws_stub(tmp_path, monkeypatch, run_id=REFUSED)

    assert capture(tmp_path, "--target", "run", "--run-id", REFUSED) == 0

    files = written(tmp_path)
    assert f"runs/{REFUSED}/refusal.sanitized.json" in files
    assert f"runs/{REFUSED}/batch-job.sanitized.json" not in files
    refusal = json.loads(files[f"runs/{REFUSED}/refusal.sanitized.json"])
    assert without_the_observation_instant(
        files[f"runs/{REFUSED}/refusal.sanitized.json"]
    ) == without_the_observation_instant(
        (COMMITTED / "runs" / REFUSED / "refusal.sanitized.json").read_text(encoding="utf-8")
    )
    assert refusal["matching_batch_job_ids"] == []
    searched = [
        call for call in recording.read_text(encoding="utf-8").splitlines()
        if call.startswith("batch list-jobs")
    ]
    assert len(searched) == len(refusal["searched_job_statuses"])


@pytest.mark.slow
def test_no_account_id_reaches_any_file_a_run_capture_writes_or_either_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_aws_stub(tmp_path, monkeypatch)

    assert capture(tmp_path, "--target", "run", "--run-id", RAN) == 0
    captured = capsys.readouterr()

    for name, text in written(tmp_path).items():
        assert ACCOUNT_ID not in text, name
    assert ACCOUNT_ID not in captured.out + captured.err


# --------------------------------------------------------------------------------------
# The compute environment, which is a standing fact and has its own target
# --------------------------------------------------------------------------------------


@pytest.mark.slow
def test_the_compute_environment_target_writes_the_environment_and_nothing_else(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_aws_stub(tmp_path, monkeypatch)

    exit_code = capture(tmp_path, "--target", "compute-environment")
    captured = capsys.readouterr()

    assert exit_code == 0
    files = written(tmp_path)
    assert set(files) == {"compute-environment.sanitized.json"}
    assert without_the_observation_instant(
        files["compute-environment.sanitized.json"]
    ) == without_the_observation_instant(
        (COMMITTED / "compute-environment.sanitized.json").read_text(encoding="utf-8")
    )
    summary = json.loads(captured.out)
    assert summary["targets"] == ["compute-environment"]
    assert summary["compute_environment"] == COMPUTE_ENVIRONMENT_NAME
    assert summary["verdict"] == "idle"
    assert scan_for_secrets(captured.out) == captured.out


@pytest.mark.slow
def test_an_environment_holding_instances_is_recorded_as_holding_rather_than_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The criterion wants the environment observed when it is quiet. A capture taken while
    # it is busy is a true record of a busy environment, so it is written and the verdict
    # says which one it is -- refusing it would leave that state unrecordable.
    install_aws_stub(
        tmp_path,
        monkeypatch,
        answers={"ec2 describe-instances --filters Name=tag:": 2},
    )

    assert capture(tmp_path, "--target", "compute-environment") == 0

    assert json.loads(capsys.readouterr().out)["verdict"] == "holding"
    assert json.loads(written(tmp_path)["compute-environment.sanitized.json"])[
        "live_instance_count"
    ] == 2


@pytest.mark.slow
def test_an_environment_spanning_two_vpcs_is_refused_rather_than_recorded_by_its_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A record naming one of two VPCs reads as a complete description of a placement this
    # platform cannot actually describe.
    subnet_ids = committed("compute-environment.sanitized.json")["subnet_ids"]
    elsewhere = ["vpc-0622b8d314ff5f800", *["vpc-000000000000000ff"] * (len(subnet_ids) - 1)]
    install_aws_stub(
        tmp_path,
        monkeypatch,
        answers={f"ec2 describe-subnets --subnet-ids {' '.join(subnet_ids)}": elsewhere},
    )

    exit_code = capture(tmp_path, "--target", "compute-environment")

    assert exit_code == 2
    assert capsys.readouterr().err.splitlines()[0] == "compute_environment_spans_vpcs:2"
    assert written(tmp_path) == {}


# --------------------------------------------------------------------------------------
# What the tool refuses, and why each refusal is worth having
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target", ["account", "compute-environment", "roles", "run"]
)
def test_no_target_will_write_outside_the_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    target: str,
) -> None:
    # A capture reads a live account and is local until somebody has read it and copied
    # what they want into fixtures/. Every arm goes through the same check, so an arm
    # somebody adds later cannot skip it and write a live capture straight into fixtures/.
    elsewhere = tmp_path / "somewhere-else"

    exit_code = main(
        [
            "--aws-profile",
            PROFILE,
            "--target",
            target,
            "--output-dir",
            str(elsewhere),
        ]
    )

    assert exit_code == 2
    assert capsys.readouterr().err.splitlines()[0] == (
        f"output_must_be_under:{ALLOWED_OUTPUT_SUFFIX.as_posix()}"
    )
    assert not elsewhere.exists()


def test_capturing_a_run_without_naming_one_is_refused_rather_than_discovering_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Which runs to capture is a judgement somebody makes in writing: three of the runs in
    # the store carry bindings that will never load, and discovery would sweep them in.
    recording = install_aws_stub(tmp_path, monkeypatch)

    exit_code = capture(tmp_path, "--target", "run")

    assert exit_code == 2
    assert capsys.readouterr().err.splitlines()[0] == "run_target_needs:--run-id"
    assert not recording.exists()


def test_the_account_target_says_which_of_its_inputs_are_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recording = install_aws_stub(tmp_path, monkeypatch)

    exit_code = capture(tmp_path, "--target", "account")

    assert exit_code == 2
    assert capsys.readouterr().err.splitlines()[0] == (
        "account_target_needs:home_subnet,home_vpc,second_subnet,second_vpc"
    )
    assert not recording.exists()


def test_recording_a_vpc_this_project_does_not_own_requires_saying_why_we_may_use_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The terms are the record. A capture of a borrowed VPC with nothing said about the
    # arrangement is a measurement whose licence to exist nobody wrote down.
    recording = install_aws_stub(tmp_path, monkeypatch)

    exit_code = capture(
        tmp_path,
        "--target",
        "account",
        "--home-vpc",
        "vpc-1",
        "--home-subnet",
        "subnet-1",
        "--second-vpc",
        "vpc-2",
        "--second-subnet",
        "subnet-2",
    )

    assert exit_code == 2
    assert capsys.readouterr().err.splitlines()[0] == "borrowed_vpc_needs_terms"
    assert not recording.exists()


@pytest.mark.slow
def test_a_run_whose_lineage_cannot_be_listed_stops_the_capture_without_echoing_the_account(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_aws_stub(
        tmp_path,
        monkeypatch,
        failures={
            f"s3api list-objects-v2 --bucket {LINEAGE_BUCKET} --prefix intent/{RAN}": (
                254,
                (
                    "An error occurred (AccessDenied) when calling the ListObjectsV2 "
                    f"operation: User: arn:aws:iam::{ACCOUNT_ID}:user/somebody is not "
                    "authorized"
                ),
            )
        },
    )

    exit_code = capture(tmp_path, "--target", "run", "--run-id", RAN)
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err.splitlines()[0] == "aws_call_failed:s3api:list-objects-v2"
    assert ACCOUNT_ID not in captured.out + captured.err
    assert written(tmp_path) == {}


@pytest.mark.slow
def test_a_laptop_without_the_aws_cli_captures_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    exit_code = capture(tmp_path, "--target", "compute-environment")

    assert exit_code == 2
    assert capsys.readouterr().err.splitlines()[0] == "aws_cli_unavailable"
    assert written(tmp_path) == {}
