"""The laptop command that reads the account and writes down what it found.

Every case runs the real command against a stub ``aws`` on PATH, and the stub's answers
are generated from the committed CloudFormation templates rather than typed out. That is
deliberate: an account that matches its templates has to produce a capture with no drift
findings, and the only way to know the two halves agree is to derive one from the other.

The account ID the stub returns is a real twelve-digit number, because the thing most
worth proving here is that none of it reaches a file.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest
from workflow_support import write_stub

from edullm_platform.evidence import AWS_ACCOUNT_ID_PLACEHOLDER, scan_for_secrets
from edullm_platform.phase1_evidence import IamPermissionStatement, IamTrustStatement
from edullm_platform.role_drift import (
    COMMITTED_ROLE_TEMPLATES,
    FOREIGN_ACCOUNT_PLACEHOLDER,
    TemplateRole,
    load_template_roles,
)
from tools.capture_phase1_evidence import (
    CAPTURE_TARGET_NAMES,
    capture_phase1_evidence,
    main,
    resolve_output_dir,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGION = "us-east-1"
ACCOUNT_ID = "123456789012"
# Reversed rather than written out; see the tracked-tree tripwire in tests/test_evidence.py.
OTHER_ACCOUNT_ID = ACCOUNT_ID[::-1]
PROFILE = "sandbox"
REPOSITORY = "OLMo-core"
ECR_REPOSITORY = "sbsandbox-intern-edullm-olmo-core"
PUBLISHER_ROLE = "sbsandbox-intern-edullm-ecr-publisher"
DEPLOYER_ROLE = "sbsandbox-intern-edullm-infra-deployer"
OUTPUT_SUFFIX = Path("docs-frank/working/phase-1-evidence")

LIFECYCLE_POLICY_TEXT = json.dumps(
    {
        "rules": [
            {
                "rulePriority": 1,
                "description": "Expire untagged images older than 7 days",
                "selection": {
                    "tagStatus": "untagged",
                    "countType": "sinceImagePushed",
                    "countUnit": "days",
                    "countNumber": 7,
                },
                "action": {"type": "expire"},
            },
            {
                "rulePriority": 2,
                "description": "Retain at most 50 tagged images",
                "selection": {
                    "tagStatus": "tagged",
                    "tagPatternList": ["*"],
                    "countType": "imageCountMoreThan",
                    "countNumber": 50,
                },
                "action": {"type": "expire"},
            },
        ]
    }
)


def expand(value: object, *, account: str = ACCOUNT_ID) -> Any:
    if isinstance(value, str):
        return (
            value.replace("${AWS::Partition}", "aws")
            .replace("${AWS::Region}", REGION)
            .replace("${AWS::AccountId}", account)
        )
    if isinstance(value, dict):
        return {key: expand(nested, account=account) for key, nested in value.items()}
    if isinstance(value, list):
        return [expand(nested, account=account) for nested in value]
    return value


def as_conditions(statement: IamTrustStatement | IamPermissionStatement) -> dict[str, Any]:
    condition: dict[str, Any] = {}
    for entry in statement.conditions:
        condition.setdefault(entry.operator, {})[entry.condition_key] = list(entry.values)
    return {"Condition": condition} if condition else {}


def as_iam_statement(statement: IamTrustStatement | IamPermissionStatement) -> dict[str, Any]:
    """One statement as IAM returns it, from the projection of the template that made it."""
    rendered: dict[str, Any] = {
        "Effect": statement.effect,
        statement.action_match.element: list(statement.action_match.actions),
    }
    if isinstance(statement, IamPermissionStatement):
        rendered[statement.resource_match.element] = list(statement.resource_match.resources)
    else:
        principals: dict[str, Any] = {}
        for principal in statement.principal_match.principals:
            principals[principal.principal_type] = principal.identifier
        rendered[statement.principal_match.element] = principals
    rendered.update(as_conditions(statement))
    return rendered


def role_answers(template: TemplateRole, *, account: str = ACCOUNT_ID) -> dict[str, Any]:
    """The three IAM responses that describe one role, exactly as its template declares it."""
    boundary = template.permissions_boundary_policy_name
    role: dict[str, Any] = {
        "RoleName": template.role_name,
        "Arn": f"arn:aws:iam::{account}:role/{template.role_name}",
        "MaxSessionDuration": template.max_session_duration_seconds,
        "AssumeRolePolicyDocument": {
            "Version": template.trust_policy_version,
            "Statement": [as_iam_statement(one) for one in template.trust_statements],
        },
    }
    if boundary is not None:
        role["PermissionsBoundary"] = {
            "PermissionsBoundaryType": "Policy",
            "PermissionsBoundaryArn": f"arn:aws:iam::{account}:policy/{boundary}",
        }
    answers: dict[str, Any] = {
        f"iam get-role {template.role_name}": {"Role": role},
        f"iam list-role-policies {template.role_name}": {
            "PolicyNames": [policy.policy_name for policy in template.inline_policies]
        },
        f"iam list-attached-role-policies {template.role_name}": {
            "AttachedPolicies": [
                {
                    "PolicyName": policy.policy_name,
                    "PolicyArn": f"arn:aws:iam::{account}:policy/{policy.policy_name}",
                }
                for policy in template.attached_managed_policies
            ]
        },
    }
    for policy in template.inline_policies:
        answers[f"iam get-role-policy {template.role_name} {policy.policy_name}"] = {
            "RoleName": template.role_name,
            "PolicyName": policy.policy_name,
            "PolicyDocument": {
                "Version": policy.policy_version,
                "Statement": [as_iam_statement(one) for one in policy.statements],
            },
        }
    return expand(answers, account=account)


def account_answers(*, account: str = ACCOUNT_ID) -> dict[str, Any]:
    answers: dict[str, Any] = {
        "sts get-caller-identity": {
            "Account": account,
            "Arn": f"arn:aws:iam::{account}:user/somebody",
            "UserId": "AIDA" + "EXAMPLEUSERID12",
        },
        "ecr describe-repositories": {
            "repositories": [
                {
                    "repositoryName": ECR_REPOSITORY,
                    "registryId": account,
                    "repositoryArn": (
                        f"arn:aws:ecr:{REGION}:{account}:repository/{ECR_REPOSITORY}"
                    ),
                    "repositoryUri": (f"{account}.dkr.ecr.{REGION}.amazonaws.com/{ECR_REPOSITORY}"),
                    "createdAt": "2026-07-26T01:29:00+00:00",
                    "imageTagMutability": "IMMUTABLE",
                    "imageScanningConfiguration": {"scanOnPush": True},
                    "encryptionConfiguration": {"encryptionType": "AES256"},
                }
            ]
        },
        "ecr get-lifecycle-policy": {
            "registryId": account,
            "repositoryName": ECR_REPOSITORY,
            "lifecyclePolicyText": LIFECYCLE_POLICY_TEXT,
        },
    }
    for role_name, relative_path in COMMITTED_ROLE_TEMPLATES:
        template = next(
            role
            for role in load_template_roles(PROJECT_ROOT / relative_path)
            if role.role_name == role_name
        )
        answers.update(role_answers(template, account=account))
    return answers


def stub_key(arguments: list[str]) -> str:
    """How the stub recognises one call: the service, the operation and the names given."""
    service, operation = arguments[0], arguments[1]
    key = f"{service} {operation}"
    for flag in ("--role-name", "--policy-name"):
        if flag in arguments:
            key += f" {arguments[arguments.index(flag) + 1]}"
    return key


def install_aws_stub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    answers: dict[str, Any] | None = None,
    failures: dict[str, tuple[int, str]] | None = None,
) -> Path:
    responses = account_answers() if answers is None else answers
    refusals = failures or {}
    recording = tmp_path / "aws-calls.txt"
    branches = []
    for key in sorted(set(responses) | set(refusals)):
        if key in refusals:
            status, message = refusals[key]
            body = f"printf '%s\\n' {json.dumps(message)} >&2; exit {status}"
        else:
            # The heredoc terminator has to own its line, so every branch is written out
            # across several lines and closed by a ";;" of its own.
            body = f"cat <<'RESPONSE'\n{json.dumps(responses[key])}\nRESPONSE"
        branches.append(f'  "{key}")\n{body}\n    ;;')
    stub_bin = tmp_path / "bin"
    write_stub(
        stub_bin,
        "aws",
        f"printf '%s\\n' \"$*\" >> '{recording}'\n"
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
    return recording


def output_dir(tmp_path: Path) -> Path:
    return tmp_path / OUTPUT_SUFFIX / "run"


def capture(tmp_path: Path, **overrides: str) -> int:
    arguments: dict[str, str] = {
        "--aws-profile": PROFILE,
        "--aws-region": REGION,
        "--environment": "sandbox",
        "--repository": REPOSITORY,
        "--output-dir": str(output_dir(tmp_path)),
    }
    arguments.update(overrides)
    return main([token for pair in arguments.items() for token in pair], base_dir=tmp_path)


def written(tmp_path: Path) -> dict[str, str]:
    root = output_dir(tmp_path)
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def loaded(tmp_path: Path, relative: str) -> Any:
    return json.loads((output_dir(tmp_path) / relative).read_text(encoding="utf-8"))


def mutated_answers(key: str, mutate: Any) -> dict[str, Any]:
    answers = copy.deepcopy(account_answers())
    mutate(answers[key])
    return answers


# --------------------------------------------------------------------------------------
# What one capture writes
# --------------------------------------------------------------------------------------


def test_a_capture_writes_both_roles_the_repository_and_a_drift_report_for_each_role(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_aws_stub(tmp_path, monkeypatch)

    assert capture(tmp_path) == 0

    assert set(written(tmp_path)) == {
        f"sanitized/roles/{PUBLISHER_ROLE}.sanitized.json",
        f"sanitized/roles/{DEPLOYER_ROLE}.sanitized.json",
        "sanitized/ecr-repository.sanitized.json",
        f"drift/{PUBLISHER_ROLE}.json",
        f"drift/{DEPLOYER_ROLE}.json",
    }


def test_an_account_that_matches_its_templates_captures_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The stub's answers are generated from the committed templates, so this is the round
    # trip: template to IAM response to captured record to comparison, and back to
    # agreement. A break anywhere in that chain shows up here.
    install_aws_stub(tmp_path, monkeypatch)

    exit_code = capture(tmp_path)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    for role_name, _path in COMMITTED_ROLE_TEMPLATES:
        report = loaded(tmp_path, f"drift/{role_name}.json")
        assert report["matches"] is True, report["findings"]
        assert report["findings"] == []
    assert json.loads(captured.out)["drift_findings"] == 0


def test_the_captured_role_is_the_role_the_template_declares(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_aws_stub(tmp_path, monkeypatch)

    assert capture(tmp_path) == 0

    role = loaded(tmp_path, f"sanitized/roles/{PUBLISHER_ROLE}.sanitized.json")
    assert role["role_name"] == PUBLISHER_ROLE
    assert role["permissions_boundary_policy_name"] == "InternSandboxBoundary"
    assert role["max_session_duration_seconds"] == 3600
    assert role["attached_managed_policies"] == []
    assert [policy["policy_name"] for policy in role["inline_policies"]] == [
        "publish-olmo-core-images"
    ]
    assert role["source"] == "aws"
    assert role["environment"] == "sandbox"
    assert role["status"] == "ok"


def test_the_captured_repository_is_the_repository_the_template_declares(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_aws_stub(tmp_path, monkeypatch)

    assert capture(tmp_path) == 0

    repository = loaded(tmp_path, "sanitized/ecr-repository.sanitized.json")
    assert repository["repository_name"] == ECR_REPOSITORY
    assert repository["image_tag_mutability"] == "IMMUTABLE"
    assert repository["scan_on_push"] is True
    assert repository["encryption_type"] == "AES256"
    assert [rule["rule_priority"] for rule in repository["lifecycle_rules"]] == [1, 2]
    assert repository["lifecycle_rules"][1]["tag_patterns"] == ["*"]


def test_a_repository_with_no_lifecycle_policy_is_captured_as_having_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_aws_stub(
        tmp_path,
        monkeypatch,
        failures={
            "ecr get-lifecycle-policy": (
                254,
                (
                    "An error occurred (LifecyclePolicyNotFoundException) when calling the "
                    "GetLifecyclePolicy operation: Lifecycle policy does not exist for the "
                    f"repository with name '{ECR_REPOSITORY}'"
                ),
            )
        },
    )

    assert capture(tmp_path) == 0

    assert loaded(tmp_path, "sanitized/ecr-repository.sanitized.json")["lifecycle_rules"] is None


# --------------------------------------------------------------------------------------
# Nothing the account said about itself survives into a file
# --------------------------------------------------------------------------------------


def test_no_account_id_reaches_any_written_file_or_either_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_aws_stub(tmp_path, monkeypatch)

    assert capture(tmp_path) == 0
    captured = capsys.readouterr()

    for name, text in written(tmp_path).items():
        assert ACCOUNT_ID not in text, name
        assert scan_for_secrets(text) == text, name
    assert ACCOUNT_ID not in captured.out + captured.err
    role = loaded(tmp_path, f"sanitized/roles/{PUBLISHER_ROLE}.sanitized.json")
    resources = role["inline_policies"][0]["statements"][1]["resource_match"]["resources"]
    assert AWS_ACCOUNT_ID_PLACEHOLDER in resources[0]


def test_a_grant_pointed_at_another_account_is_masked_so_it_stays_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Masking every account to one placeholder would let this fold away against the
    # template's ${AWS::AccountId} and report as no drift at all.
    def repoint(answer: dict[str, Any]) -> None:
        statement = answer["PolicyDocument"]["Statement"][1]
        statement["Resource"] = [
            f"arn:aws:ecr:{REGION}:{OTHER_ACCOUNT_ID}:repository/{ECR_REPOSITORY}"
        ]

    install_aws_stub(
        tmp_path,
        monkeypatch,
        answers=mutated_answers(
            f"iam get-role-policy {PUBLISHER_ROLE} publish-olmo-core-images", repoint
        ),
    )

    assert capture(tmp_path) == 1

    role = loaded(tmp_path, f"sanitized/roles/{PUBLISHER_ROLE}.sanitized.json")
    resources = role["inline_policies"][0]["statements"][1]["resource_match"]["resources"]
    assert FOREIGN_ACCOUNT_PLACEHOLDER in resources[0]
    assert OTHER_ACCOUNT_ID not in json.dumps(role)
    report = loaded(tmp_path, f"drift/{PUBLISHER_ROLE}.json")
    assert report["matches"] is False


# --------------------------------------------------------------------------------------
# Drift stops the capture reporting success
# --------------------------------------------------------------------------------------


def test_a_role_widened_in_the_console_is_reported_and_fails_the_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_aws_stub(
        tmp_path,
        monkeypatch,
        answers=mutated_answers(
            f"iam get-role-policy {PUBLISHER_ROLE} publish-olmo-core-images",
            lambda answer: answer["PolicyDocument"]["Statement"][1]["Action"].append(
                "ecr:DeleteRepository"
            ),
        ),
    )

    exit_code = capture(tmp_path)
    captured = capsys.readouterr()

    assert exit_code == 1
    report = loaded(tmp_path, f"drift/{PUBLISHER_ROLE}.json")
    assert [finding["direction"] for finding in report["findings"]] == ["wider"]
    assert "ecr:DeleteRepository" in json.dumps(report)
    assert PUBLISHER_ROLE in captured.err
    assert "wider" in captured.err
    # The evidence is still written. What drifted is the account, not the capture.
    assert f"sanitized/roles/{PUBLISHER_ROLE}.sanitized.json" in written(tmp_path)


def test_a_role_narrowed_in_the_console_also_fails_the_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Narrower is not a security problem and is still a capture that cannot claim the
    # committed template describes the account.
    install_aws_stub(
        tmp_path,
        monkeypatch,
        answers=mutated_answers(
            f"iam get-role-policy {PUBLISHER_ROLE} publish-olmo-core-images",
            lambda answer: answer["PolicyDocument"]["Statement"][1]["Action"].remove(
                "ecr:PutImage"
            ),
        ),
    )

    assert capture(tmp_path) == 1

    report = loaded(tmp_path, f"drift/{PUBLISHER_ROLE}.json")
    assert [finding["direction"] for finding in report["findings"]] == ["narrower"]


def test_a_boundary_removed_in_the_console_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_aws_stub(
        tmp_path,
        monkeypatch,
        answers=mutated_answers(
            f"iam get-role {PUBLISHER_ROLE}",
            lambda answer: answer["Role"].pop("PermissionsBoundary"),
        ),
    )

    assert capture(tmp_path) == 1

    report = loaded(tmp_path, f"drift/{PUBLISHER_ROLE}.json")
    assert [finding["direction"] for finding in report["findings"]] == ["wider"]
    assert "permissions boundary" in json.dumps(report)


def test_a_managed_policy_attached_in_the_console_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_aws_stub(
        tmp_path,
        monkeypatch,
        answers=mutated_answers(
            f"iam list-attached-role-policies {PUBLISHER_ROLE}",
            lambda answer: answer["AttachedPolicies"].append(
                {
                    "PolicyName": "AdministratorAccess",
                    "PolicyArn": "arn:aws:iam::aws:policy/AdministratorAccess",
                }
            ),
        ),
    )

    assert capture(tmp_path) == 1

    role = loaded(tmp_path, f"sanitized/roles/{PUBLISHER_ROLE}.sanitized.json")
    assert role["attached_managed_policies"] == [
        {"policy_name": "AdministratorAccess", "scope": "aws"}
    ]
    assert "AdministratorAccess" in json.dumps(loaded(tmp_path, f"drift/{PUBLISHER_ROLE}.json"))


# --------------------------------------------------------------------------------------
# The calls the tool makes, and what it does when one does not answer
# --------------------------------------------------------------------------------------


def test_the_tool_asks_for_exactly_what_it_records_and_nothing_else(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = install_aws_stub(tmp_path, monkeypatch)

    assert capture(tmp_path) == 0

    calls = recording.read_text(encoding="utf-8").splitlines()
    operations = [" ".join(call.split()[:2]) for call in calls]
    assert operations == [
        "sts get-caller-identity",
        "iam get-role",
        "iam list-role-policies",
        "iam get-role-policy",
        "iam list-attached-role-policies",
        "iam get-role",
        "iam list-role-policies",
        "iam get-role-policy",
        "iam list-attached-role-policies",
        "ecr describe-repositories",
        "ecr get-lifecycle-policy",
    ]
    assert all(f"--profile {PROFILE}" in call for call in calls)
    assert all("--output json" in call for call in calls)


def test_capturing_one_target_makes_only_that_target_s_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = install_aws_stub(tmp_path, monkeypatch)

    assert capture(tmp_path, **{"--target": "repository"}) == 0

    operations = {
        " ".join(call.split()[:2]) for call in recording.read_text(encoding="utf-8").splitlines()
    }
    assert operations == {
        "sts get-caller-identity",
        "ecr describe-repositories",
        "ecr get-lifecycle-policy",
    }
    assert set(written(tmp_path)) == {"sanitized/ecr-repository.sanitized.json"}


def test_every_target_the_command_offers_is_one_the_registry_knows_how_to_capture() -> None:
    # The registry is the extension point: an image, a scan and a session are captured by
    # adding an entry to it rather than by reworking the command around them.
    assert CAPTURE_TARGET_NAMES == ("roles", "repository")


@pytest.mark.parametrize(
    ("failing_call", "expected"),
    [
        (f"iam get-role {PUBLISHER_ROLE}", "aws_call_failed:GetRole:AccessDenied"),
        ("ecr describe-repositories", "aws_call_failed:DescribeRepositories:AccessDenied"),
    ],
)
def test_a_call_that_was_refused_stops_the_capture_without_echoing_the_account(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failing_call: str,
    expected: str,
) -> None:
    operation = expected.split(":")[1]
    install_aws_stub(
        tmp_path,
        monkeypatch,
        failures={
            failing_call: (
                254,
                (
                    f"An error occurred (AccessDenied) when calling the {operation} "
                    f"operation: User: arn:aws:iam::{ACCOUNT_ID}:user/somebody is not authorized"
                ),
            )
        },
    )

    exit_code = capture(tmp_path)
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err.splitlines()[0] == expected
    assert ACCOUNT_ID not in captured.out + captured.err


def test_a_runner_without_the_aws_cli_captures_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    exit_code = capture(tmp_path)

    assert exit_code == 2
    assert capsys.readouterr().err.splitlines()[0] == "aws_cli_unavailable"
    assert written(tmp_path) == {}


def test_an_unregistered_repository_is_refused_before_any_call_is_made(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recording = install_aws_stub(tmp_path, monkeypatch)

    exit_code = capture(tmp_path, **{"--repository": "not-registered"})

    assert exit_code == 2
    assert capsys.readouterr().err.splitlines()[0] == "unregistered_repository"
    assert not recording.exists()


# --------------------------------------------------------------------------------------
# Where a capture may be written
# --------------------------------------------------------------------------------------


def test_the_capture_refuses_to_write_outside_the_working_directory(tmp_path: Path) -> None:
    # Captured evidence is local-only until somebody reviews it and copies the part they
    # want into fixtures. A tool that could write anywhere would make that a choice
    # nobody had to take.
    with pytest.raises(ValueError, match="phase-1-evidence"):
        resolve_output_dir(tmp_path / "somewhere-else", base_dir=tmp_path)


def test_the_capture_accepts_a_directory_under_the_working_directory(tmp_path: Path) -> None:
    allowed = tmp_path / OUTPUT_SUFFIX / "2026-07-26"

    assert resolve_output_dir(allowed, base_dir=tmp_path) == allowed.resolve()


def test_a_capture_function_returns_the_records_it_wrote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_aws_stub(tmp_path, monkeypatch)

    captured = capture_phase1_evidence(
        aws_profile=PROFILE,
        aws_region=REGION,
        environment="sandbox",
        ecr_repository=ECR_REPOSITORY,
        targets=CAPTURE_TARGET_NAMES,
        output_dir=output_dir(tmp_path),
        base_dir=tmp_path,
    )

    assert [role.role_name for role in captured.roles] == [PUBLISHER_ROLE, DEPLOYER_ROLE]
    assert captured.repository is not None
    assert captured.repository.repository_name == ECR_REPOSITORY
    assert [report.role_name for report in captured.drift] == [PUBLISHER_ROLE, DEPLOYER_ROLE]
    assert all(report.matches for report in captured.drift)
