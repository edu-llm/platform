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
