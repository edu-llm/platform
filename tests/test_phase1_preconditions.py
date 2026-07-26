import re
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODEOWNERS_PATH = PROJECT_ROOT / ".github" / "CODEOWNERS"
PROBE_WORKFLOW_PATH = (
    PROJECT_ROOT / ".github" / "workflows" / "probe-phase1-ecr-capability.yml"
)
FULL_SHA_ACTION = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
AWS_ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")


def _read_required(path: Path) -> str:
    assert path.is_file(), f"required file is missing: {path.relative_to(PROJECT_ROOT)}"
    return path.read_text(encoding="utf-8")


def _load_workflow() -> tuple[str, dict[str, Any]]:
    text = _read_required(PROBE_WORKFLOW_PATH)
    document = yaml.load(text, Loader=yaml.BaseLoader)
    assert isinstance(document, dict)
    return text, document


def _credentials_step(job: dict[str, Any]) -> dict[str, Any]:
    return next(
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("aws-actions/configure-aws-credentials@")
    )


def _probe_template(workflow: dict[str, Any]) -> dict[str, Any]:
    create_step = next(
        step
        for step in workflow["jobs"]["probe"]["steps"]
        if step.get("name") == "Create probe stack"
    )
    template_match = re.search(
        r"cat > probe-template\.yml <<EOF\n(?P<template>.*?)\nEOF",
        create_step["run"],
        flags=re.DOTALL,
    )
    assert template_match is not None
    template = yaml.safe_load(template_match.group("template"))
    assert isinstance(template, dict)
    return template


def test_codeowners_protects_phase1_infrastructure_surfaces() -> None:
    lines = {
        line.strip()
        for line in _read_required(CODEOWNERS_PATH).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    expected_owners = "@philote-dev @BritishAmericqn"
    assert f"/.github/CODEOWNERS {expected_owners}" in lines
    assert f"/.github/workflows/** {expected_owners}" in lines
    assert f"/infra/** {expected_owners}" in lines


def test_probe_workflow_has_restricted_trigger_permissions_and_execution() -> None:
    _, workflow = _load_workflow()

    assert workflow["on"] == {
        "push": {"branches": ["feature/phase-1-branch-to-image"]}
    }
    assert workflow["permissions"] == {"contents": "read", "id-token": "write"}
    assert workflow["concurrency"]["cancel-in-progress"] == "false"

    jobs = workflow["jobs"]
    assert list(jobs) == ["probe", "cleanup"]
    probe = jobs["probe"]
    assert probe["timeout-minutes"] == "10"

    configure_step = _credentials_step(probe)
    assert configure_step["with"]["role-to-assume"] == "${{ vars.AWS_DEPLOY_ROLE_ARN }}"
    assert configure_step["with"]["aws-region"] == "${{ vars.AWS_REGION }}"
    assert configure_step["with"]["role-duration-seconds"] == "900"


def test_probe_workflow_pins_actions_and_excludes_account_ids_and_iam() -> None:
    text, workflow = _load_workflow()

    uses = [
        step["uses"]
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if "uses" in step
    ]
    assert uses
    assert all(FULL_SHA_ACTION.fullmatch(action) for action in uses)
    assert AWS_ACCOUNT_ID.search(text) is None
    assert "AWS::IAM::" not in text


def test_probe_workflow_creates_verifies_and_always_deletes_stack() -> None:
    _, workflow = _load_workflow()
    probe_scripts = "\n".join(
        step.get("run", "") for step in workflow["jobs"]["probe"]["steps"]
    )

    assert "aws cloudformation create-stack" in probe_scripts
    assert "aws cloudformation wait stack-create-complete" in probe_scripts
    assert "aws ecr describe-repositories" in probe_scripts

    cleanup = workflow["jobs"]["cleanup"]
    assert cleanup["needs"] == "probe"
    assert cleanup["if"] == "${{ always() }}"
    assert cleanup["timeout-minutes"] == "5"
    assert cleanup["env"] == workflow["jobs"]["probe"]["env"]

    cleanup_credentials = _credentials_step(cleanup)
    assert cleanup_credentials["with"]["role-to-assume"] == "${{ vars.AWS_DEPLOY_ROLE_ARN }}"
    assert cleanup_credentials["with"]["aws-region"] == "${{ vars.AWS_REGION }}"
    assert cleanup_credentials["with"]["role-duration-seconds"] == "900"

    cleanup_step = next(
        step for step in cleanup["steps"] if step.get("name") == "Delete probe stack"
    )
    assert "aws cloudformation delete-stack" in cleanup_step["run"]
    assert "aws cloudformation wait stack-delete-complete" in cleanup_step["run"]
    assert "cleanup_failed=0" in cleanup_step["run"]
    assert cleanup_step["run"].count("|| cleanup_failed=1") == 2
    assert 'exit "$cleanup_failed"' in cleanup_step["run"]


def test_probe_inline_template_declares_named_ecr_repository() -> None:
    _, workflow = _load_workflow()
    template = _probe_template(workflow)
    repository = template["Resources"]["ProbeRepository"]
    assert repository["Type"] == "AWS::ECR::Repository"
    assert repository["Properties"]["RepositoryName"] == "${REPOSITORY_NAME}"


def test_probe_inline_template_retains_repository() -> None:
    _, workflow = _load_workflow()
    template = _probe_template(workflow)

    assert template["Resources"]["ProbeRepository"]["DeletionPolicy"] == "Retain"


def test_probe_resource_names_use_sandbox_prefix() -> None:
    _, workflow = _load_workflow()
    environment = workflow["jobs"]["probe"]["env"]

    assert environment["STACK_NAME"] == (
        "sbsandbox-intern-edullm-phase1-ecr-probe-${{ github.run_id }}"
    )
    assert environment["REPOSITORY_NAME"].startswith("sbsandbox-intern-")
