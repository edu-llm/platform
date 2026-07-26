import ast
import re
import shlex
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "deploy-phase1-ecr.yml"
TEMPLATE_PATH = "infra/ecr-repositories.yaml"
STACK_NAME = "sbsandbox-intern-edullm-phase1-ecr"
REPOSITORY_NAME = "sbsandbox-intern-edullm-olmo-core"


class GitHubActionsLoader(yaml.SafeLoader):
    """Parse YAML booleans without treating the GitHub Actions `on` key as true."""


GitHubActionsLoader.yaml_implicit_resolvers = {
    key: list(resolvers) for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
for resolver_key, resolvers in GitHubActionsLoader.yaml_implicit_resolvers.items():
    GitHubActionsLoader.yaml_implicit_resolvers[resolver_key] = [
        resolver for resolver in resolvers if resolver[0] != "tag:yaml.org,2002:bool"
    ]
GitHubActionsLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE),
    list("tTfF"),
)


def _load_workflow() -> dict[str, Any]:
    assert WORKFLOW_PATH.is_file(), f"required file is missing: {WORKFLOW_PATH}"
    loaded = yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=GitHubActionsLoader)
    assert isinstance(loaded, dict)
    return loaded


def _only_job(workflow: dict[str, Any]) -> dict[str, Any]:
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    assert len(jobs) == 1
    job = next(iter(jobs.values()))
    assert isinstance(job, dict)
    return job


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    matching = [step for step in job["steps"] if step.get("name") == name]
    assert len(matching) == 1
    return matching[0]


def _shell_syntax_without_heredoc_bodies(script: str) -> str:
    heredoc_pattern = re.compile(
        r"<<-?\s*(?:'([^']+)'|\"([^\"]+)\"|([a-zA-Z_][a-zA-Z0-9_]*))"
    )
    shell_lines = []
    delimiter: str | None = None

    for line in script.splitlines():
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue

        shell_lines.append(line)
        match = heredoc_pattern.search(line)
        if match is not None:
            delimiter = next(group for group in match.groups() if group is not None)

    assert delimiter is None, "unterminated shell heredoc"
    return "\n".join(shell_lines)


def _aws_word_count(script: str) -> int:
    shell_syntax = _shell_syntax_without_heredoc_bodies(script)
    return len(re.findall(r"(?<![a-zA-Z0-9_-])aws(?=\s)", shell_syntax))


def _aws_commands(script: str) -> list[list[str]]:
    normalized = re.sub(r"\\\s*\n", " ", script)
    commands = []
    for line in normalized.splitlines():
        tokens = shlex.split(line)
        if tokens[:1] == ["aws"]:
            commands.append(tokens)
    assert _aws_word_count(normalized) == len(commands), (
        "every aws invocation must be an explicit top-level command"
    )
    return commands


def _command_tokens(script: str, service: str, operation: str) -> list[str]:
    matching = [
        command for command in _aws_commands(script) if command[:3] == ["aws", service, operation]
    ]
    assert len(matching) == 1, f"expected exactly one aws {service} {operation} command"
    return matching[0]


def _literal_assignment(source: str, name: str) -> object:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"missing literal assignment: {name}")


def test_workflow_has_only_the_approved_dispatch_and_bootstrap_push_triggers() -> None:
    workflow = _load_workflow()

    assert set(workflow["on"]) == {"workflow_dispatch", "push"}
    assert workflow["on"]["workflow_dispatch"] is None
    assert workflow["on"]["push"] == {
        "branches": ["main", "feature/phase-1-branch-to-image"],
        "paths": [".github/workflows/deploy-phase1-ecr.yml", TEMPLATE_PATH],
    }


def test_workflow_permissions_concurrency_and_runtime_are_minimal_and_bounded() -> None:
    workflow = _load_workflow()
    job = _only_job(workflow)

    assert workflow["permissions"] == {"contents": "read"}
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert workflow["concurrency"] == {
        "group": f"cloudformation-{STACK_NAME}",
        "cancel-in-progress": False,
    }
    assert job["timeout-minutes"] <= 10


def test_workflow_pins_checkout_and_aws_credentials_to_approved_commits() -> None:
    job = _only_job(_load_workflow())
    checkout = _step(job, "Check out repository")
    credentials = _step(job, "Configure AWS credentials")

    assert checkout["uses"] == "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09"
    assert credentials["uses"] == (
        "aws-actions/configure-aws-credentials@"
        "e6de054238d6b7531b4efff3b6587d9aade6a06c"
    )
    assert credentials["with"] == {
        "role-to-assume": "${{ vars.AWS_DEPLOY_ROLE_ARN }}",
        "aws-region": "${{ vars.AWS_REGION }}",
        "role-duration-seconds": 900,
    }


def test_workflow_validates_and_deploys_only_the_approved_non_iam_template() -> None:
    job = _only_job(_load_workflow())
    validate_script = _step(job, "Validate CloudFormation template")["run"]
    deploy_script = _step(job, "Deploy Phase 1 ECR stack")["run"]

    assert validate_script.startswith("set -euo pipefail\n")
    assert deploy_script.startswith("set -euo pipefail\n")
    assert _command_tokens(validate_script, "cloudformation", "validate-template") == [
        "aws",
        "cloudformation",
        "validate-template",
        "--template-body",
        f"file://{TEMPLATE_PATH}",
    ]
    assert _command_tokens(deploy_script, "cloudformation", "deploy") == [
        "aws",
        "cloudformation",
        "deploy",
        "--stack-name",
        STACK_NAME,
        "--template-file",
        TEMPLATE_PATH,
        "--no-fail-on-empty-changeset",
    ]

    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8").lower()
    assert "--capabilities" not in workflow_text
    assert "capability_iam" not in workflow_text
    assert "capability_named_iam" not in workflow_text
    assert "aws iam" not in workflow_text
    assert "infra/iam" not in workflow_text


def test_workflow_verifies_exact_repository_and_lifecycle_semantics_without_uri_output() -> None:
    verify_script = _step(_only_job(_load_workflow()), "Verify Phase 1 ECR repository")["run"]

    assert verify_script.startswith("set -euo pipefail\n")
    assert _aws_commands(verify_script) == [
        [
            "aws",
            "ecr",
            "describe-repositories",
            "--repository-names",
            REPOSITORY_NAME,
            "--query",
            (
                "repositories[0].{repositoryName:repositoryName,"
                "encryptionType:encryptionConfiguration.encryptionType,"
                "scanOnPush:imageScanningConfiguration.scanOnPush,"
                "imageTagMutability:imageTagMutability}"
            ),
            "--output",
            "json",
            ">",
            "${repository_json}",
        ],
        [
            "aws",
            "ecr",
            "get-lifecycle-policy",
            "--repository-name",
            REPOSITORY_NAME,
            "--query",
            "{lifecyclePolicyText:lifecyclePolicyText}",
            "--output",
            "json",
            ">",
            "${lifecycle_json}",
        ],
    ]

    assert not re.search(r"(?<!\d)\d{12}(?!\d)", verify_script)
    assert not {"repositoryarn", "repositoryuri", "registryid"} & set(
        re.findall(r"[a-zA-Z][a-zA-Z0-9]*", verify_script.lower())
    )
    assert "PHASE1_ECR_VERIFICATION_PASSED" in verify_script

    python_source = verify_script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    assert _literal_assignment(python_source, "expected_repository") == {
        "repositoryName": REPOSITORY_NAME,
        "encryptionType": "AES256",
        "scanOnPush": True,
        "imageTagMutability": "IMMUTABLE",
    }
    assert _literal_assignment(python_source, "expected_policy") == {
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
