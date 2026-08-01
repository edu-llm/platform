"""The walk three capture tools share, against a stubbed ``aws``.

``edullm_platform.role_capture`` reads one registry of roles out of the account and compares
each to the template that declares it. Phase 1, Phase 3 and Phase 4 all call it, so a defect
here is a defect in every phase's role evidence at once -- and the two failures worth pinning
are both silent ones: a walk that wrote a record for some roles and stopped, and a registry
entry pointing at a template that does not declare the role it names.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from workflow_support import write_stub

from edullm_platform.capture_tooling import CaptureFailedError
from edullm_platform.phase1_evidence import DeployedRoleEvidence
from edullm_platform.role_capture import capture_roles
from edullm_platform.role_drift import RoleDriftReport

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE = "sbsandbox"
REGION = "us-east-1"
ACCOUNT_ID = "123456789012"
ROLE_NAME = "sbsandbox-intern-edullm-run-canceller"
TEMPLATE_PATH = "infra/iam/run-canceller-role.yaml"
POLICY_NAME = "stop-a-running-job-and-read-enough-to-know-whose-it-is"


def deployed_role() -> dict[str, Any]:
    """What ``iam get-role`` returns for the canceller as the account holds it today."""
    return {
        "RoleName": ROLE_NAME,
        "MaxSessionDuration": 3600,
        "PermissionsBoundary": {
            "PermissionsBoundaryArn": (f"arn:aws:iam::{ACCOUNT_ID}:policy/InternSandboxBoundary")
        },
        "AssumeRolePolicyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {
                        "Federated": (
                            f"arn:aws:iam::{ACCOUNT_ID}:oidc-provider/"
                            "token.actions.githubusercontent.com"
                        )
                    },
                    "Action": "sts:AssumeRoleWithWebIdentity",
                    "Condition": {
                        "StringEquals": {
                            "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                            "token.actions.githubusercontent.com:job_workflow_ref": (
                                "edu-llm/platform/.github/workflows/cancel-run.yml@refs/heads/main"
                            ),
                            "token.actions.githubusercontent.com:repository_owner_id": (
                                "306859726"
                            ),
                            "token.actions.githubusercontent.com:repository_id": "1311508598",
                            "token.actions.githubusercontent.com:sub": (
                                "repo:edu-llm@306859726/platform@1311508598:ref:refs/heads/main"
                            ),
                        }
                    },
                }
            ],
        },
    }


def inline_policy() -> dict[str, Any]:
    return {
        "PolicyName": POLICY_NAME,
        "PolicyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["batch:DescribeJobs", "batch:ListJobs"],
                    "Resource": "*",
                },
                {
                    "Effect": "Allow",
                    "Action": "batch:TerminateJob",
                    "Resource": f"arn:aws:batch:{REGION}:{ACCOUNT_ID}:job/*",
                    "Condition": {"StringLike": {"aws:ResourceTag/edullm:run-id": "run_*"}},
                },
            ],
        },
    }


def install_aws_stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        "sts get-caller-identity": {
            "Account": ACCOUNT_ID,
            "Arn": f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/somebody/session",
        },
        f"iam get-role {ROLE_NAME}": {"Role": deployed_role()},
        f"iam list-role-policies {ROLE_NAME}": {"PolicyNames": [POLICY_NAME]},
        f"iam get-role-policy {ROLE_NAME} {POLICY_NAME}": inline_policy(),
        f"iam list-attached-role-policies {ROLE_NAME}": {"AttachedPolicies": []},
    }
    branches = []
    for key, answer in responses.items():
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
def test_one_registry_entry_produces_a_sanitized_record_and_a_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: write the record and skip the comparison.

    Two records per role rather than one, and the paths they are filed under are the whole
    point of the split: the sanitized record is what a reviewer copies into ``fixtures/``,
    and the report stays in the working directory because it is derived from the record
    beside it. A walk that wrote only the first would commit a role nothing had compared.

    The account here is the canceller as deployed, so the comparison against the committed
    template reports nothing -- which is also what says the stub is faithful rather than
    merely parseable.
    """
    install_aws_stub(tmp_path, monkeypatch)

    records = capture_roles(
        role_templates=((ROLE_NAME, TEMPLATE_PATH),),
        profile=PROFILE,
        region=REGION,
        observed_at=datetime.now(tz=UTC).replace(microsecond=0),
        repo_root=PROJECT_ROOT,
    )

    assert [name for name, _record in records] == [
        f"sanitized/roles/{ROLE_NAME}.sanitized.json",
        f"drift/{ROLE_NAME}.json",
    ]
    evidence, report = records[0][1], records[1][1]
    assert isinstance(evidence, DeployedRoleEvidence)
    assert isinstance(report, RoleDriftReport)
    assert evidence.role_name == ROLE_NAME
    assert report.findings == (), report.findings
    assert report.template_path == TEMPLATE_PATH


@pytest.mark.slow
def test_a_registry_entry_whose_template_does_not_declare_the_role_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: compare against whichever role the template happens to declare first.

    A registry entry is a pair, and the failure of getting the second half wrong is the one
    a capture must not absorb: comparing a role against a document describing a different
    one produces findings that are entirely artefacts, or -- worse, for a template declaring
    one role -- a clean report about the wrong subject.
    """
    install_aws_stub(tmp_path, monkeypatch)

    with pytest.raises(CaptureFailedError, match="template_does_not_declare_the_role"):
        capture_roles(
            role_templates=((ROLE_NAME, "infra/iam/nightly-reader-role.yaml"),),
            profile=PROFILE,
            region=REGION,
            observed_at=datetime.now(tz=UTC).replace(microsecond=0),
            repo_root=PROJECT_ROOT,
        )
