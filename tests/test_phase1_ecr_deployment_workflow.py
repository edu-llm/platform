import re
from typing import Any

from workflow_support import (
    WORKFLOWS_ROOT,
    aws_commands,
    command_tokens,
    literal_assignment,
    load_workflow,
    only_job,
    step,
)

WORKFLOW_FILE = ".github/workflows/deploy-phase1-ecr.yml"
WORKFLOW_PATH = WORKFLOWS_ROOT / "deploy-phase1-ecr.yml"
TEMPLATE_PATH = "infra/ecr-repositories.yaml"
STACK_NAME = "sbsandbox-intern-edullm-phase1-ecr"
REPOSITORY_NAME = "sbsandbox-intern-edullm-olmo-core"


def _load_workflow() -> dict[str, Any]:
    return load_workflow(WORKFLOW_PATH)


def test_workflow_runs_only_on_dispatch_and_pushes_to_main() -> None:
    # The deployer role trusts job_workflow_ref @refs/heads/main only, so a branch
    # trigger could not reach AWS anyway. Keeping the branch list at main means the
    # workflow fails at the trigger instead of at a confusing AssumeRole denial.
    workflow = _load_workflow()

    assert set(workflow["on"]) == {"workflow_dispatch", "push"}
    assert workflow["on"]["workflow_dispatch"] is None
    assert workflow["on"]["push"] == {
        "branches": ["main"],
        "paths": [WORKFLOW_FILE, TEMPLATE_PATH],
    }
    assert "feature/" not in WORKFLOW_PATH.read_text(encoding="utf-8")


def test_workflow_permissions_concurrency_and_runtime_are_minimal_and_bounded() -> None:
    workflow = _load_workflow()
    job = only_job(workflow)

    assert workflow["permissions"] == {"contents": "read"}
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert workflow["concurrency"] == {
        "group": f"cloudformation-{STACK_NAME}",
        "cancel-in-progress": False,
    }
    assert job["timeout-minutes"] <= 10


def test_workflow_pins_checkout_and_aws_credentials_to_approved_commits() -> None:
    job = only_job(_load_workflow())
    checkout = step(job, "Check out repository")
    credentials = step(job, "Configure AWS credentials")

    assert checkout["uses"] == "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09"
    assert credentials["uses"] == (
        "aws-actions/configure-aws-credentials@"
        "e6de054238d6b7531b4efff3b6587d9aade6a06c"
    )
    assert credentials["with"] == {
        "role-to-assume": "${{ vars.AWS_INFRA_DEPLOYER_ROLE_ARN }}",
        "aws-region": "${{ vars.AWS_REGION }}",
        "role-duration-seconds": 900,
    }


def test_workflow_no_longer_assumes_the_retired_shared_deploy_role() -> None:
    # InternGitHubActionsDeploy-sbsandbox is assumable by any repository in the
    # organization and cannot put ECR lifecycle policies. AWS_INFRA_DEPLOYER_ROLE_ARN is
    # a separate repository variable so that flipping one role back cannot silently
    # restore the other.
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "vars.AWS_DEPLOY_ROLE_ARN" not in workflow_text
    assert "InternGitHubActionsDeploy" not in workflow_text
    assert workflow_text.count("vars.AWS_INFRA_DEPLOYER_ROLE_ARN") == 1


def test_workflow_validates_and_deploys_only_the_approved_non_iam_template() -> None:
    job = only_job(_load_workflow())
    validate_script = step(job, "Validate CloudFormation template")["run"]
    deploy_script = step(job, "Deploy Phase 1 ECR stack")["run"]

    assert validate_script.startswith("set -euo pipefail\n")
    assert deploy_script.startswith("set -euo pipefail\n")
    assert command_tokens(validate_script, "cloudformation", "validate-template") == [
        "aws",
        "cloudformation",
        "validate-template",
        "--template-body",
        f"file://{TEMPLATE_PATH}",
    ]
    assert command_tokens(deploy_script, "cloudformation", "deploy") == [
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
    verify_script = step(only_job(_load_workflow()), "Verify Phase 1 ECR repository")["run"]

    assert verify_script.startswith("set -euo pipefail\n")
    assert aws_commands(verify_script) == [
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
    assert literal_assignment(python_source, "expected_repository") == {
        "repositoryName": REPOSITORY_NAME,
        "encryptionType": "AES256",
        "scanOnPush": True,
        "imageTagMutability": "IMMUTABLE",
    }
    assert literal_assignment(python_source, "expected_policy") == {
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
