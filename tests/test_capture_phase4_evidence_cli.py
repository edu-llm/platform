"""``tools/capture_phase4_evidence.py``'s own capture functions, stubbed against ``aws``.

Only one target lives here today: ``capture_role_scope``. It is the function
``b9cb6d7`` changed to stop folding a grant on some other bucket into the outputs-bucket
prefix tuples, and none of Phase 4's model-level tests in ``test_phase4_run_evidence.py``
call it -- they read ``WorkloadRoleScopeEvidence`` back out of a committed capture, which
proves the model but not the tool that populated it. This exercises the classification
loop itself, against a synthetic policy naming two buckets, following the same stubbed-
``aws``-on-``PATH`` pattern the sibling ``test_capture_phaseN_evidence_cli.py`` modules use.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from workflow_support import write_stub

from edullm_platform.contracts.results import OUTPUTS_BUCKET
from tools.capture_phase4_evidence import GPU_WORKLOAD_ROLE, capture_role_scope

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
