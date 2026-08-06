import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from infrastructure_support import ACCOUNT_LITERAL
from workflow_support import (
    WORKFLOWS_ROOT,
    aws_commands,
    literal_assignment,
    load_workflow,
    only_job,
    run_step_script,
    step,
    unreal_context_references,
    write_stub,
)

WORKFLOW_FILE = ".github/workflows/deploy-phase2-admission.yml"
WORKFLOW_PATH = WORKFLOWS_ROOT / "deploy-phase2-admission.yml"
PHASE1_WORKFLOW_PATH = WORKFLOWS_ROOT / "deploy-phase1-ecr.yml"

LINEAGE_TEMPLATE = "infra/lineage-bucket.yaml"
ARTIFACTS_TEMPLATE = "infra/artifacts-bucket.yaml"
ADMISSION_TEMPLATE = "infra/admission-state-machine.yaml"
LINEAGE_STACK = "sbsandbox-intern-edullm-phase2-lineage"
ARTIFACTS_STACK = "sbsandbox-intern-edullm-phase2-artifacts"
ADMISSION_STACK = "sbsandbox-intern-edullm-phase2-admission"
CONCURRENCY_GROUP = "cloudformation-sbsandbox-intern-edullm-phase2"

LINEAGE_BUCKET = "sbsandbox-intern-edullm-lineage"
ARTIFACTS_BUCKET = "sbsandbox-intern-edullm-artifacts"
VALIDATOR_FUNCTION = "sbsandbox-intern-edullm-admission-validator"
STATE_MACHINE_NAME = "sbsandbox-intern-edullm-admission"

# Deployment order is a property of this file and of nothing else. No stack exports a
# value another imports, so CloudFormation will not enforce that the buckets exist before
# the function that reads its code from one and the machine that writes into the other.
DEPLOYMENT_ORDER = (
    ("Deploy Phase 2 lineage bucket stack", LINEAGE_STACK, LINEAGE_TEMPLATE),
    ("Deploy Phase 2 artifacts bucket stack", ARTIFACTS_STACK, ARTIFACTS_TEMPLATE),
    ("Deploy Phase 2 admission stack", ADMISSION_STACK, ADMISSION_TEMPLATE),
)
VERIFY_STEP = "Verify Phase 2 admission control plane"


def _load_workflow() -> dict[str, Any]:
    return load_workflow(WORKFLOW_PATH)


def _run_scripts() -> list[str]:
    return [
        candidate["run"] for candidate in only_job(_load_workflow())["steps"] if "run" in candidate
    ]


def test_workflow_runs_only_on_dispatch_and_pushes_to_main_that_touch_phase2() -> None:
    # The deployer role trusts job_workflow_ref @refs/heads/main only, so a branch trigger
    # could not reach AWS anyway. Keeping the branch list at main means the workflow fails
    # at the trigger instead of at a confusing AssumeRole denial.
    workflow = _load_workflow()

    assert set(workflow["on"]) == {"workflow_dispatch", "push"}
    # The dispatch carries exactly one input and it is the release switch. Asserted by name
    # rather than left open, because an input on a workflow that deploys infrastructure is a
    # knob a dispatcher can turn, and the guard step above only decides *who* may dispatch --
    # not what they may ask for. A second input added without a reason lands here.
    #
    # It defaults to false so that a plain dispatch, which is what somebody reconciling a
    # stack presses, still means only "deploy". Releasing is the thing you have to ask for.
    assert set(workflow["on"]["workflow_dispatch"]["inputs"]) == {"release_lambdas"}
    assert workflow["on"]["workflow_dispatch"]["inputs"]["release_lambdas"]["default"] is False
    assert workflow["on"]["push"] == {
        "branches": ["main"],
        "paths": [WORKFLOW_FILE, LINEAGE_TEMPLATE, ARTIFACTS_TEMPLATE, ADMISSION_TEMPLATE],
    }


def test_the_push_paths_are_exactly_the_templates_this_workflow_deploys() -> None:
    # A template that deploys here but is missing from the path filter is a change that
    # merges to main and never reaches AWS, which surfaces later as unexplained drift.
    # infra/iam/ is deliberately absent: those stacks are applied from a laptop because
    # this role holds no iam:CreateRole.
    workflow = _load_workflow()
    deployed = {template for _name, _stack, template in DEPLOYMENT_ORDER}
    watched = set(workflow["on"]["push"]["paths"])

    assert deployed | {WORKFLOW_FILE} == watched
    assert not [path for path in watched if path.startswith("infra/iam/")]


def test_every_expression_names_something_that_actually_exists() -> None:
    # This workflow reaches AWS through two repository variables. A typo in either would
    # resolve to the empty string and surface as an unexplained AssumeRole failure.
    assert unreal_context_references(WORKFLOW_PATH) == []


def test_workflow_permissions_concurrency_and_runtime_are_minimal_and_bounded() -> None:
    workflow = _load_workflow()
    job = only_job(workflow)

    assert workflow["permissions"] == {"contents": "read"}
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert workflow["concurrency"] == {"group": CONCURRENCY_GROUP, "cancel-in-progress": False}
    assert job["timeout-minutes"] <= 15


def test_workflow_pins_the_same_action_commits_phase1_already_reviewed() -> None:
    # Both workflows assume the same role against the same account. Pinning them to
    # different commits of the same action would mean two supply chains to review, and
    # reading the SHAs out of the Phase 1 file is what keeps them from drifting apart.
    job = only_job(_load_workflow())
    phase1_job = only_job(load_workflow(PHASE1_WORKFLOW_PATH))

    for name in ("Check out repository", "Configure AWS credentials"):
        pinned = step(job, name)["uses"]
        assert pinned == step(phase1_job, name)["uses"]
        assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", pinned), f"{name} is not pinned to a commit"


def test_workflow_assumes_the_infra_deployer_role_and_masks_the_account_id() -> None:
    credentials = step(only_job(_load_workflow()), "Configure AWS credentials")

    assert credentials["with"] == {
        "role-to-assume": "${{ vars.AWS_INFRA_DEPLOYER_ROLE_ARN }}",
        "aws-region": "${{ vars.AWS_REGION }}",
        "role-duration-seconds": 900,
        "mask-aws-account-id": True,
    }


def test_every_template_is_validated_before_any_of_them_is_deployed() -> None:
    job = only_job(_load_workflow())
    validate_script = step(job, "Validate CloudFormation templates")["run"]
    step_names = [candidate.get("name") for candidate in job["steps"]]

    assert aws_commands(validate_script) == [
        ["aws", "cloudformation", "validate-template", "--template-body", f"file://{template}"]
        for _name, _stack, template in DEPLOYMENT_ORDER
    ]
    assert step_names.index("Validate CloudFormation templates") < min(
        step_names.index(name) for name, _stack, _template in DEPLOYMENT_ORDER
    )


def test_stacks_deploy_in_dependency_order_without_failing_on_an_empty_changeset() -> None:
    job = only_job(_load_workflow())
    step_names = [candidate.get("name") for candidate in job["steps"]]

    for name, stack, template in DEPLOYMENT_ORDER:
        assert aws_commands(step(job, name)["run"]) == [
            [
                "aws",
                "cloudformation",
                "deploy",
                "--stack-name",
                stack,
                "--template-file",
                template,
                "--no-fail-on-empty-changeset",
            ]
        ]

    positions = [step_names.index(name) for name, _stack, _template in DEPLOYMENT_ORDER]
    assert positions == sorted(positions)
    assert max(positions) < step_names.index(VERIFY_STEP)


def test_no_deploy_acknowledges_an_iam_capability_because_no_template_needs_one() -> None:
    # CloudFormation demands CAPABILITY_IAM only for AWS::IAM::AccessKey, Group,
    # InstanceProfile, ManagedPolicy, Policy, Role, User and UserToGroupAddition. None of
    # the three templates here contains one: a bucket policy is a resource policy, not an
    # IAM entity, and passing an existing role to a state machine or a function needs
    # iam:PassRole on the caller rather than a stack capability. Withholding the
    # acknowledgement means a template that grows an IAM resource fails at
    # InsufficientCapabilities, naming the reason, instead of part-way into a rollback.
    #
    # Asserted against the parsed commands rather than the file text, because the file
    # says all of the above in a comment.
    flags = [
        token
        for script in _run_scripts()
        for command in aws_commands(script)
        for token in command
    ]

    assert "--capabilities" not in flags
    assert not [token for token in flags if token.upper().startswith("CAPABILITY_")]


def test_verification_reads_the_live_shape_and_never_prints_an_account_id() -> None:
    verify_script = step(only_job(_load_workflow()), VERIFY_STEP)["run"]

    assert aws_commands(verify_script) == [
        [
            "aws",
            "cloudformation",
            "describe-stacks",
            "--stack-name",
            ADMISSION_STACK,
            "--query",
            "Stacks[0].Outputs[?OutputKey=='StateMachineArn'].OutputValue",
            "--output",
            "text",
            ">",
            "${state_machine_arn_file}",
        ],
        [
            "aws",
            "stepfunctions",
            "describe-state-machine",
            "--state-machine-arn",
            "${state_machine_arn}",
            "--query",
            (
                "{name:name,status:status,type:type,"
                "loggingLevel:loggingConfiguration.level,"
                "includeExecutionData:loggingConfiguration.includeExecutionData}"
            ),
            "--output",
            "json",
            ">",
            "${state_machine_json}",
        ],
        [
            "aws",
            "lambda",
            "get-function-configuration",
            "--function-name",
            VALIDATOR_FUNCTION,
            "--query",
            "{functionName:FunctionName,runtime:Runtime,handler:Handler,packageType:PackageType}",
            "--output",
            "json",
            ">",
            "${function_json}",
        ],
        [
            "aws",
            "s3api",
            "get-object-lock-configuration",
            "--bucket",
            LINEAGE_BUCKET,
            "--query",
            "{objectLockEnabled:ObjectLockConfiguration.ObjectLockEnabled}",
            "--output",
            "json",
            ">",
            "${lineage_json}",
        ],
        [
            "aws",
            "s3api",
            "get-bucket-versioning",
            "--bucket",
            ARTIFACTS_BUCKET,
            "--query",
            "{status:Status}",
            "--output",
            "json",
            ">",
            "${artifacts_json}",
        ],
    ]

    # The state machine ARN is fetched from a stack output rather than assembled from an
    # account id, so nothing here has to know the account number to run.
    assert not ACCOUNT_LITERAL.search(verify_script)
    assert "get-caller-identity" not in verify_script
    assert "roleArn" not in verify_script


def test_verification_pins_the_admission_shape_and_fails_loudly_when_it_drifts() -> None:
    verify_script = step(only_job(_load_workflow()), VERIFY_STEP)["run"]
    python_source = verify_script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]

    assert literal_assignment(python_source, "expected_state_machine") == {
        "name": STATE_MACHINE_NAME,
        "status": "ACTIVE",
        "type": "STANDARD",
        "loggingLevel": "ALL",
        "includeExecutionData": True,
    }
    assert literal_assignment(python_source, "expected_function") == {
        "functionName": VALIDATOR_FUNCTION,
        "runtime": "python3.12",
        "handler": "edullm_platform.admission_handler.handler",
        "packageType": "Zip",
    }
    # The lock only. Enabling it is creation-only and so is what a deploy can get
    # irreversibly wrong; the retention rule is a live setting this phase leaves unset,
    # and pinning its absence here would fail the deploy the day somebody adds it.
    assert literal_assignment(python_source, "expected_lineage_object_lock") == {
        "objectLockEnabled": "Enabled",
    }
    assert literal_assignment(python_source, "expected_artifacts_versioning") == {
        "status": "Enabled"
    }
    # A verification that only printed its findings would go green on a drifted stack.
    assert "raise SystemExit" in python_source
    assert "PHASE2_ADMISSION_VERIFICATION_PASSED" in verify_script


def test_every_run_body_is_strict_about_failures_and_unset_variables() -> None:
    # One per stack, plus validate, plus verify, plus the dispatch guard, plus the release
    # upload. The count is the point rather than scaffolding: a run body added without
    # ``set -euo pipefail`` would otherwise be checked by nothing, so a new step is meant to
    # fail here once and be counted in deliberately.
    #
    # The release step is the fourth, counted in on 2026-08-01. It matters more than most
    # that it is strict: it builds a zip, uploads it, and prints the digest somebody will
    # paste into a release record, so a failure it swallowed would publish a version id
    # beside a digest from a build that did not finish.
    scripts = _run_scripts()

    assert len(scripts) == len(DEPLOYMENT_ORDER) + 4
    assert all(script.startswith("set -euo pipefail\n") for script in scripts)


# Each aws call the verification makes answers from an environment variable, so a test can
# hand it a drifted stack without an AWS account. The state machine ARN is deliberately
# unreadable as an account number: the step reads it from a stack output and passes it
# straight back, and nothing in it needs to look real.
AWS_STUB = """
case "$1 $2" in
  "cloudformation describe-stacks") printf '%s\\n' "${STACK_OUTPUT_ARN}" ;;
  "stepfunctions describe-state-machine") printf '%s' "${STATE_MACHINE_JSON}" ;;
  "lambda get-function-configuration") printf '%s' "${FUNCTION_JSON}" ;;
  "s3api get-object-lock-configuration") printf '%s' "${LINEAGE_JSON}" ;;
  "s3api get-bucket-versioning") printf '%s' "${ARTIFACTS_JSON}" ;;
  *) echo "unexpected aws call: $*" >&2 ; exit 64 ;;
esac
"""
OBSERVED_STATE_MACHINE = {
    "name": STATE_MACHINE_NAME,
    "status": "ACTIVE",
    "type": "STANDARD",
    "loggingLevel": "ALL",
    "includeExecutionData": True,
}
OBSERVED_FUNCTION = {
    "functionName": VALIDATOR_FUNCTION,
    "runtime": "python3.12",
    "handler": "edullm_platform.admission_handler.handler",
    "packageType": "Zip",
}
OBSERVED_LINEAGE = {"objectLockEnabled": "Enabled"}
OBSERVED_ARTIFACTS = {"status": "Enabled"}


def _run_verification(tmp_path: Path, **drift: object) -> subprocess.CompletedProcess[str]:
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "aws", AWS_STUB)
    # The runner has `python` on PATH; this sandbox may only have the interpreter running
    # the tests, so the step body is left alone and the name is supplied instead.
    write_stub(stub_bin, "python", f'exec "{sys.executable}" "$@"')

    observed: dict[str, Any] = {
        "STATE_MACHINE_JSON": dict(OBSERVED_STATE_MACHINE),
        "FUNCTION_JSON": dict(OBSERVED_FUNCTION),
        "LINEAGE_JSON": dict(OBSERVED_LINEAGE),
        "ARTIFACTS_JSON": dict(OBSERVED_ARTIFACTS),
    }
    for name, replacement in drift.items():
        observed[name] = replacement

    return run_step_script(
        step(only_job(_load_workflow()), VERIFY_STEP)["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "STACK_OUTPUT_ARN": (
                f"arn:aws:states:us-east-1:sandbox:stateMachine:{STATE_MACHINE_NAME}"
            ),
            **{name: json.dumps(payload) for name, payload in observed.items()},
        },
        stub_bin=stub_bin,
    )


def test_the_verification_passes_against_the_control_plane_the_templates_describe(
    tmp_path: Path,
) -> None:
    result = _run_verification(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "PHASE2_ADMISSION_VERIFICATION_PASSED" in result.stdout


@pytest.mark.parametrize(
    ("name", "drifted", "label"),
    [
        (
            "STATE_MACHINE_JSON",
            {**OBSERVED_STATE_MACHINE, "loggingLevel": "ERROR"},
            "state machine",
        ),
        (
            "STATE_MACHINE_JSON",
            {**OBSERVED_STATE_MACHINE, "includeExecutionData": False},
            "state machine",
        ),
        ("FUNCTION_JSON", {**OBSERVED_FUNCTION, "handler": "handler.handler"}, "validator"),
        (
            "LINEAGE_JSON",
            {**OBSERVED_LINEAGE, "objectLockEnabled": None},
            "lineage bucket object lock",
        ),
        ("ARTIFACTS_JSON", {"status": "Suspended"}, "artifacts bucket versioning"),
    ],
)
def test_a_drifted_control_plane_fails_the_run_and_says_which_check_moved(
    tmp_path: Path,
    name: str,
    drifted: dict[str, Any],
    label: str,
) -> None:
    # A verification that only printed its findings would go green on a drifted stack, so
    # every one of these has to end the job.
    result = _run_verification(tmp_path, **{name: drifted})

    assert result.returncode != 0
    assert "PHASE2_ADMISSION_VERIFICATION_PASSED" not in result.stdout
    assert "phase 2 admission verification failed" in result.stderr
    assert label in result.stderr


def test_a_verification_call_the_stub_does_not_recognize_would_be_noticed(
    tmp_path: Path,
) -> None:
    # Anchors the tests above: they pass because the step made exactly the five calls the
    # stub answers, not because a silent stub let everything through.
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "aws", 'echo "unexpected aws call: $*" >&2 ; exit 64\n')

    result = run_step_script(
        step(only_job(_load_workflow()), VERIFY_STEP)["run"],
        cwd=tmp_path,
        env={"RUNNER_TEMP": str(tmp_path)},
        stub_bin=stub_bin,
    )

    assert result.returncode != 0
    assert "unexpected aws call" in result.stderr


def test_workflow_does_not_reach_for_the_retired_shared_deploy_role() -> None:
    # InternGitHubActionsDeploy-sbsandbox is assumable by any repository in the
    # organization. AWS_INFRA_DEPLOYER_ROLE_ARN is a separate repository variable so that
    # flipping one role back cannot silently restore the other.
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "vars.AWS_DEPLOY_ROLE_ARN" not in workflow_text
    assert "InternGitHubActionsDeploy" not in workflow_text
    assert workflow_text.count("vars.AWS_INFRA_DEPLOYER_ROLE_ARN") == 1
