"""``tools/capture_phase4_evidence.py``'s own capture functions, stubbed against ``aws``.

Two targets live here, and they are here for the same reason. ``capture_role_scope`` is the
function ``b9cb6d7`` changed to stop folding a grant on some other bucket into the
outputs-bucket prefix tuples; ``capture_corpus_read`` is the one that decides what a run's
saved config is written down as. Neither is called by Phase 4's model-level tests in
``test_phase4_run_evidence.py`` -- those read records back out of a committed capture, which
proves the model but not the tool that populated it, and a recorder that quietly dropped
what it could not explain would leave every one of them passing.

Both exercise the loop against a synthetic input, following the same stubbed-``aws``-on-
``PATH`` pattern the sibling ``test_capture_phaseN_evidence_cli.py`` modules use.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from workflow_support import write_stub

from edullm_platform.capture_tooling import CaptureFailedError, write_record
from edullm_platform.contracts.results import OUTPUTS_BUCKET
from edullm_platform.evidence import scan_object_key
from edullm_platform.phase4_evidence import CheckpointObservation
from tools.capture_phase4_evidence import (
    GPU_WORKLOAD_ROLE,
    _log_lines,
    _summary_object,
    capture_corpus_read,
    capture_role_scope,
)

PROFILE = "sbsandbox"
REGION = "us-east-1"
ACCOUNT_ID = "123456789012"
POLICY_NAME = "workload-scope"
OTHER_BUCKET = "edullm-data"


def install_aws_stub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, policy_document: dict[str, Any]
) -> None:
    responses = {
        "sts get-caller-identity": {
            "Account": ACCOUNT_ID,
            "Arn": f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/somebody/session",
        },
        f"iam list-role-policies {GPU_WORKLOAD_ROLE}": {"PolicyNames": [POLICY_NAME]},
        f"iam get-role-policy {GPU_WORKLOAD_ROLE} {POLICY_NAME}": policy_document,
    }
    branches = []
    for key, answer in responses.items():
        # The heredoc terminator has to own its line, so every branch is written out
        # across several lines and closed by a ";;" of its own.
        body = f"cat <<'RESPONSE'\n{json.dumps(answer)}\nRESPONSE"
        branches.append(f'  "{key}")\n{body}\n    ;;')
    stub_bin = tmp_path / "bin"
    write_stub(
        stub_bin,
        "aws",
        'key="${1-} ${2-}"\n'
        "while [ $# -gt 0 ]; do\n"
        '  case "$1" in\n'
        '    --role-name|--policy-name) key="$key $2"; shift ;;\n'
        "  esac\n"
        "  shift\n"
        "done\n"
        'case "$key" in\n' + "\n".join(branches) + "\n  *) exit 64 ;;\nesac\n",
    )
    monkeypatch.setenv("PATH", f"{stub_bin}{os.pathsep}{os.defpath}")


@pytest.mark.slow
def test_a_grant_on_another_bucket_is_recorded_by_bucket_and_key_never_reaching_the_outputs_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The classification loop, against a policy naming both buckets in one document.

    Two wrong implementations have to go red here, and neither is hypothetical: the first
    is the defect ``b9cb6d7`` removed, the second is the "obvious" way to fix it badly.

    * Fold the other bucket's key into ``readable_prefixes``/``writable_prefixes`` instead
      of discriminating by bucket -- ``other_key`` would then appear in
      ``readable_prefixes``, which the second assertion below refuses.
    * Discriminate by bucket but drop the other bucket's grant instead of recording it --
      ``grants_outside_the_outputs_bucket`` would then be empty, which the third
      assertion below refuses.
    """
    other_key = "train/shard-0001.jsonl"
    install_aws_stub(
        tmp_path,
        monkeypatch,
        policy_document={
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:PutObject"],
                    "Resource": [f"arn:aws:s3:::{OUTPUTS_BUCKET}/teams/platform/runs/*"],
                },
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{OTHER_BUCKET}/{other_key}"],
                },
            ],
        },
    )

    scope = capture_role_scope(profile=PROFILE, region=REGION)

    assert scope.readable_prefixes == ("teams/platform/runs/*",)
    assert scope.writable_prefixes == ("teams/platform/runs/*",)
    assert other_key not in scope.readable_prefixes
    assert scope.grants_outside_the_outputs_bucket == (f"{OTHER_BUCKET}/{other_key}",)


@pytest.mark.slow
def test_a_grant_of_an_s3_action_that_is_not_a_read_or_a_write_is_recorded_rather_than_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: keep the Get/Put filter that the outputs-bucket branch needs.

    The two branches ask different questions and the same filter cannot serve both. The
    prefix tuples answer "what does this role read and write in the outputs bucket", so
    they read s3:GetObject and s3:PutObject and nothing else. This field answers "what else
    does this role touch", and under a Get/Put filter the answer was silently no for every
    other S3 action -- a grant recorded as absent rather than as a grant.

    The actions here are not invented. s3:GetObjectAttributes and s3:AbortMultipartUpload
    are both in an inline policy this platform carries on a shared role, read live
    2026-07-31, alongside s3:PutObjectTagging.
    """
    other_key = "train/shard-0001.jsonl"
    install_aws_stub(
        tmp_path,
        monkeypatch,
        policy_document={
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObjectAttributes", "s3:AbortMultipartUpload"],
                    "Resource": [f"arn:aws:s3:::{OTHER_BUCKET}/{other_key}"],
                },
            ],
        },
    )

    scope = capture_role_scope(profile=PROFILE, region=REGION)

    assert scope.grants_outside_the_outputs_bucket == (f"{OTHER_BUCKET}/{other_key}",)
    assert scope.readable_prefixes == ()
    assert scope.writable_prefixes == ()


@pytest.mark.slow
def test_a_grant_on_another_buckets_own_arn_is_recorded_although_it_names_no_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: match only ARNs that carry a key, which is what the object pattern did.

    s3:ListBucket and s3:GetBucketLocation cannot be granted on an object ARN -- they are
    bucket-level actions and IAM requires the bucket's own ARN -- so a recorder that only
    matched ``bucket/key`` could never see the grant that lets this role enumerate somebody
    else's corpus. Both actions are in the shared-role inline policy read live 2026-07-31.

    Recorded as the bare bucket name, which is unambiguous rather than merely short: an
    object grant always carries a non-empty key and a bucket name cannot contain a slash,
    so a recorded value with no slash in it is a grant on the bucket itself and nothing
    else.
    """
    install_aws_stub(
        tmp_path,
        monkeypatch,
        policy_document={
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                    "Resource": [f"arn:aws:s3:::{OTHER_BUCKET}"],
                },
            ],
        },
    )

    scope = capture_role_scope(profile=PROFILE, region=REGION)

    assert scope.grants_outside_the_outputs_bucket == (OTHER_BUCKET,)


@pytest.mark.slow
def test_the_outputs_bucket_prefixes_are_read_from_object_reads_and_writes_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The load-bearing half, asserted so that widening the other half cannot reach it.

    Criteria 4, 7 and 12 rest on these two tuples, so the wider rule the two tests above
    ask for has to stay on the other side of the bucket comparison. Two ways it could leak
    across and both are refused here: a tagging or attributes action on the outputs bucket
    becoming a read or a write, and the outputs bucket's own ARN being read as a grant on
    the prefix '' -- which fnmatch would then match against nothing, but which would still
    put an empty string in a tuple whose every entry is supposed to be a key pattern.

    This passed before the widening and passes after it, which is the entire point of it.
    """
    install_aws_stub(
        tmp_path,
        monkeypatch,
        policy_document={
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:PutObject"],
                    "Resource": [f"arn:aws:s3:::{OUTPUTS_BUCKET}/teams/platform/runs/*"],
                },
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObjectAttributes", "s3:PutObjectTagging"],
                    "Resource": [f"arn:aws:s3:::{OUTPUTS_BUCKET}/teams/platform/scratch/*"],
                },
                {
                    "Effect": "Allow",
                    "Action": ["s3:ListBucket"],
                    "Resource": [f"arn:aws:s3:::{OUTPUTS_BUCKET}"],
                },
            ],
        },
    )

    scope = capture_role_scope(profile=PROFILE, region=REGION)

    assert scope.readable_prefixes == ("teams/platform/runs/*",)
    assert scope.writable_prefixes == ("teams/platform/runs/*",)
    assert scope.grants_outside_the_outputs_bucket == ()


#: The one twelve-digit literal this tree allows, because it is AWS's documentation example.
#: Everything below is built from it rather than typed, since the tracked-tree scan refuses
#: any other run of twelve digits -- including one inside a float.
EXAMPLE_ACCOUNT_ID = "123456789012"


def test_the_summary_is_found_at_the_end_of_a_log_and_not_in_the_middle() -> None:
    """A config dump and a W&B teardown both put a bare brace on a line of their own.

    Reading forward from the first one finds a fragment of whichever came first, so what is
    read is the last balanced object rather than the first.
    """
    lines = [
        "building config",
        "{",
        '  "dataset": "not the summary",',
        "}",
        "[step=1/20] train/CE loss=9.1",
        "Training complete",
        "{",
        '  "run_id": "run_1",',
        '  "checkpoint_uri": "s3://bucket/prefix/",',
        '  "steps": 20',
        "}",
    ]

    assert _summary_object(lines) == {
        "run_id": "run_1",
        "checkpoint_uri": "s3://bucket/prefix/",
        "steps": 20,
    }


def test_an_unbalanced_object_at_the_end_does_not_hide_the_summary_before_it() -> None:
    """A truncated log window can cut an object in half, and the summary is still there."""
    lines = [
        "{",
        '  "run_id": "run_1",',
        '  "checkpoint_uri": "s3://bucket/prefix/"',
        "}",
        "{",
        '  "half": "an object the window cut",',
    ]

    summary = _summary_object(lines)

    assert summary is not None
    assert summary["run_id"] == "run_1"


def test_a_log_with_no_object_in_it_is_a_run_that_printed_no_summary() -> None:
    assert _summary_object(["starting", "[step=1/20]", "Training complete"]) is None


def test_the_log_window_is_taken_from_the_end_rather_than_the_beginning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The summary is printed last, after thousands of lines of config, model and metrics.

    Pinned because ``--start-from-head`` reads the other end, and the failure it produces is
    a capture that reports every real run as having printed nothing.
    """
    stub_bin = tmp_path / "bin"
    recorded = tmp_path / "arguments.json"
    write_stub(stub_bin, "aws", f'printf "%s\\n" "$@" > {recorded}\necho "[]"\n')
    monkeypatch.setenv("PATH", f"{stub_bin}{os.pathsep}{os.environ['PATH']}")

    _log_lines("a-stream", profile="p", region="r")

    arguments = recorded.read_text().split("\n")
    assert "--start-from-head" not in arguments
    assert "get-log-events" in arguments


def _checkpoint_at(step: int | None) -> CheckpointObservation:
    return CheckpointObservation.model_validate(
        {
            "observed_at": "2026-08-01T15:00:00Z",
            "source": "aws",
            "environment": "sandbox",
            "run_id": "run_1",
            "prefix": f"s3://{OUTPUTS_BUCKET}/teams/platform/runs/run_1/checkpoints/",
            "state": "committed",
            "detail": "a checkpoint OLMo-core's own loader accepts",
            "step": step,
            "size_bytes": 1,
            "checksum": None,
            "success_marker_uri": None,
            "container_claimed_checksum": None,
        }
    )


def install_saved_config_stub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, config: dict[str, Any] | None
) -> None:
    """An ``aws s3api get-object`` that hands back one config, or reports it absent.

    ``$7`` is the destination path, because that is where the CLI puts the file argument and
    the reader downloads to a file rather than reading stdout -- ``get-object`` writes its
    metadata response to stdout as well as the body.
    """
    stub_bin = tmp_path / "bin"
    body = (
        f"cat <<'CONFIG' > \"$7\"\n{json.dumps(config)}\nCONFIG\necho '{{}}'\n"
        if config is not None
        else 'echo "An error occurred (NoSuchKey)" >&2\nexit 1\n'
    )
    write_stub(stub_bin, "aws", f'if [ "${{2-}}" = "get-object" ]; then\n{body}exit 0\nfi\nexit 64\n')
    monkeypatch.setenv("PATH", f"{stub_bin}{os.pathsep}{os.defpath}")


@pytest.mark.slow
def test_a_shard_from_outside_the_release_is_recorded_rather_than_filtered_away(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: keep only the paths that sit under the release before recording them.

    The record would then be true of every run and the model's own check would have nothing
    left to find -- a dataset half of somebody else's corpus would read as clean, which is
    the whole failure the corpus record exists to catch. The recorder classifies nothing;
    it writes down the directories it found and the model says which do not belong.
    """
    install_saved_config_stub(
        tmp_path,
        monkeypatch,
        config={
            "dataset_id": "pretrain/regmix-10b",
            "dataset_version": "v1",
            "dataset": {
                "dtype": "uint32",
                "sequence_length": 2048,
                "paths": [
                    f"s3://{OTHER_BUCKET}/pretrain/regmix-10b/v1/tokens/arxiv/train-00000.u32le.bin",
                    f"s3://{OTHER_BUCKET}/pretrain/regmix-10b/v1/tokens/arxiv/train-00001.u32le.bin",
                    f"s3://{OTHER_BUCKET}/pretrain/olmo-127b/v1/tokens/dclm/train-00000.u32le.bin",
                ],
            },
            "data_loader": {"global_batch_size": 262144},
        },
    )

    corpus = capture_corpus_read(
        "run_1", checkpoint=_checkpoint_at(150), profile=PROFILE, region=REGION
    )

    assert corpus is not None
    assert corpus.shard_count == 3
    assert corpus.prefixes_outside_the_release == (
        f"s3://{OTHER_BUCKET}/pretrain/olmo-127b/v1/tokens/dclm/",
    )
    assert not corpus.read_only_the_release_it_named


@pytest.mark.slow
def test_a_run_that_saved_no_config_records_no_corpus_rather_than_a_corpus_of_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: return a record with an empty path list.

    Every run predating the corpus entry point generated its own tokens and saved no config,
    and a record saying "read zero shards of nothing" would read as an answer about what it
    opened rather than as the absence of the question.
    """
    install_saved_config_stub(tmp_path, monkeypatch, config=None)

    assert (
        capture_corpus_read(
            "run_1", checkpoint=_checkpoint_at(150), profile=PROFILE, region=REGION
        )
        is None
    )


def test_a_checkpoint_with_no_step_has_no_directory_a_config_could_be_under() -> None:
    """Mutation: build the URI anyway and let the store answer.

    ``step{None}/config.json`` is a key nothing is stored at, so the capture would reach S3
    to be told what it already knew, and a reader of the code would be left thinking the
    absent step was handled somewhere.
    """
    assert (
        capture_corpus_read(
            "run_1", checkpoint=_checkpoint_at(None), profile=PROFILE, region=REGION
        )
        is None
    )


def test_a_number_that_looks_like_an_account_id_is_not_read_as_one(tmp_path: Path) -> None:
    """A float64 loss printed in full carries twelve consecutive digits often enough.

    Nothing a credential can be is a JSON number, so the backstop reads the strings of a
    record rather than its serialized form. Without this a real training run cannot record
    the loss it reached, nor a parameter count in the hundreds of billions.
    """
    destination = tmp_path / "record.json"
    loss = float(f"8.{EXAMPLE_ACCOUNT_ID}")

    write_record(destination, {"last_loss": loss, "parameters": int(EXAMPLE_ACCOUNT_ID)})

    written = json.loads(destination.read_text())
    assert written["last_loss"] == loss
    assert written["parameters"] == int(EXAMPLE_ACCOUNT_ID)


def test_a_credential_in_a_string_is_still_refused(tmp_path: Path) -> None:
    with pytest.raises(CaptureFailedError, match="record_holds_a_credential"):
        write_record(tmp_path / "record.json", {"note": f"account {EXAMPLE_ACCOUNT_ID} denied it"})

    assert not (tmp_path / "record.json").exists()


def test_a_credential_hiding_in_a_key_or_a_nested_list_is_still_refused(tmp_path: Path) -> None:
    """Keys and list items are as committed as a value, so both are read."""
    with pytest.raises(CaptureFailedError, match="record_holds_a_credential"):
        write_record(tmp_path / "by-key.json", {f"account {EXAMPLE_ACCOUNT_ID}": "denied"})

    with pytest.raises(CaptureFailedError, match="record_holds_a_credential"):
        write_record(
            tmp_path / "nested.json", {"messages": [{"text": f"id {EXAMPLE_ACCOUNT_ID}"}]}
        )


def test_an_ordinary_checkpoint_key_is_not_read_as_a_secret_access_key() -> None:
    """The base64 alphabet contains a slash, so a long enough key matches that pattern.

    Every checkpoint key is long enough: a run id, then a step, then a file name. Without
    reading a segment at a time the capture refuses the evidence for being ordinary.
    """
    key = "teams/platform/runs/run_019fbd3f-8b72-70e9-8ffb/checkpoints/step1000/config.json"

    assert scan_object_key(key) == key


def test_a_credential_inside_one_segment_of_a_key_is_still_refused() -> None:
    """A key smuggled into a name sits inside a segment, which is where this still looks."""
    with pytest.raises(ValueError, match="must not contain credentials"):
        scan_object_key(f"teams/platform/{'A' * 20}{'b' * 20}/model.pt")

    with pytest.raises(ValueError, match="must not contain credentials"):
        scan_object_key(f"teams/{EXAMPLE_ACCOUNT_ID}/model.pt")


def test_the_backstop_allows_the_key_the_field_annotation_allows(tmp_path: Path) -> None:
    """A backstop stricter than the annotation refuses the document, not the field.

    Real checkpoint keys pass forty characters of the base64 alphabet, slashes included, so
    a whole-string read of one refuses a capture of every run that saved a checkpoint.
    """
    destination = tmp_path / "record.json"
    key = "teams/platform/runs/run_019fbd3f-8b72-70e9-8ffb/checkpoints/step1000/config.json"

    write_record(destination, {"objects": [{"key": key}]})

    assert json.loads(destination.read_text())["objects"][0]["key"] == key
