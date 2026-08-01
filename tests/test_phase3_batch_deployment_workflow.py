"""The Phase 3 deploy workflow, and whether its verification would actually fail.

A deploy step that reports success says the CloudFormation API accepted a template. It does
not say the account holds what the template describes, and after any console edit it will
not. The verification step is what closes that, and a verification that only printed its
findings would go green on a drifted stack -- so the second half of this module runs the
step's own script against stubbed AWS answers and checks that each kind of drift ends the
job.

Asserted against parsed YAML and resolved references throughout, never against the literal
text of an expression. That is Phase 1's lesson restated, because it is the specific way a
green suite covers a path that cannot work.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
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

WORKFLOW_FILE = ".github/workflows/deploy-phase3-batch.yml"
WORKFLOW_PATH = WORKFLOWS_ROOT / "deploy-phase3-batch.yml"
PHASE1_WORKFLOW_PATH = WORKFLOWS_ROOT / "deploy-phase1-ecr.yml"
PHASE2_WORKFLOW_PATH = WORKFLOWS_ROOT / "deploy-phase2-admission.yml"

OUTPUTS_TEMPLATE = "infra/outputs-bucket.yaml"
NETWORK_TEMPLATE = "infra/batch-network.yaml"
COMPUTE_TEMPLATE = "infra/batch-compute.yaml"
GPU_COMPUTE_TEMPLATE = "infra/batch-compute-gpu.yaml"
GPU_SHAPES_TEMPLATE = "infra/batch-compute-gpu-shapes.yaml"
EVENTS_TEMPLATE = "infra/batch-events.yaml"
ADMISSION_TEMPLATE = "infra/admission-state-machine.yaml"

ADMISSION_STACK = "sbsandbox-intern-edullm-phase2-admission"

# The order is a property of this file and of nothing else, for three of the four
# dependencies. Only the compute stack's Fn::ImportValue of the network stack's exports is
# something CloudFormation refuses to get wrong; the rule naming a queue ARN, and the state
# machine naming a queue and a job definition, are strings that deploy perfectly against
# resources that do not exist.
#
# The GPU compute stack sits between the CPU one and the events stack, and it is in this
# workflow rather than a Phase 4 one of its own for a reason this order makes visible: the
# single EventBridge rule now names both queues. Split across two workflows, the GPU queue's
# creation and the rule that matches it would be in separate runs with nothing ordering
# them, which is this list's whole subject matter reintroduced across a boundary no test can
# reach.
DEPLOYMENT_ORDER = (
    ("Deploy Phase 3 outputs bucket stack", "sbsandbox-intern-edullm-phase3-outputs", OUTPUTS_TEMPLATE),
    ("Deploy Phase 3 network stack", "sbsandbox-intern-edullm-phase3-network", NETWORK_TEMPLATE),
    ("Deploy Phase 3 batch compute stack", "sbsandbox-intern-edullm-phase3-batch", COMPUTE_TEMPLATE),
    ("Deploy Phase 4 GPU batch compute stack", "sbsandbox-intern-edullm-phase4-gpu", GPU_COMPUTE_TEMPLATE),
    # After the stack above and not beside it, because this template creates no log group.
    # It names the one that stack creates, and the awslogs driver takes a string, so
    # CloudFormation enforces nothing about the order.
    (
        "Deploy the remaining GPU shapes stack",
        "sbsandbox-intern-edullm-phase4-gpu-shapes",
        GPU_SHAPES_TEMPLATE,
    ),
    ("Deploy Phase 3 batch events stack", "sbsandbox-intern-edullm-phase3-events", EVENTS_TEMPLATE),
    ("Deploy the amended admission state machine", ADMISSION_STACK, ADMISSION_TEMPLATE),
)
VERIFY_STEP = "Verify CPU and GPU batch execution"
SHAPES_VERIFY_STEP = "Verify the remaining GPU shapes"

#: The same group the Phase 2 workflow declares. The two deploy one stack in common, so a
#: distinct group would let them race into a mid-update stack.
CONCURRENCY_GROUP = "cloudformation-sbsandbox-intern-edullm-phase2"


def workflow() -> dict[str, Any]:
    return load_workflow(WORKFLOW_PATH)


def run_scripts() -> list[str]:
    return [
        candidate["run"] for candidate in only_job(workflow())["steps"] if "run" in candidate
    ]


def test_the_workflow_runs_only_on_dispatch_and_pushes_to_main_that_touch_phase3() -> None:
    """Mutation: add a branch other than main.

    The deployer role trusts ``job_workflow_ref`` at ``@refs/heads/main`` only, so a branch
    trigger could not reach AWS anyway -- it would fail at AssumeRole with nothing pointing
    at the branch. Keeping the list at main means the workflow declines at the trigger.
    """
    parsed = workflow()

    assert set(parsed["on"]) == {"workflow_dispatch", "push"}
    assert parsed["on"]["push"]["branches"] == ["main"]

    # The dispatch carries one optional input and it must stay optional with an empty
    # default: a required one would make every hand-started deploy answer a question about a
    # run, and a non-empty default would make every deploy try to report on a run that does
    # not exist.
    dispatch = parsed["on"]["workflow_dispatch"]
    assert set(dispatch["inputs"]) == {"describe_run"}
    assert dispatch["inputs"]["describe_run"]["required"] is False
    assert dispatch["inputs"]["describe_run"]["default"] == ""


def test_the_push_paths_are_exactly_the_templates_this_workflow_deploys() -> None:
    """Mutation: deploy a template that is not in the path filter.

    A template that deploys here but is missing from the filter is a change that merges to
    main and never reaches AWS, which surfaces weeks later as unexplained drift. infra/iam/
    is deliberately absent: those stacks are applied from a laptop because this role holds
    no ``iam:CreateRole``.
    """
    watched = set(workflow()["on"]["push"]["paths"])
    deployed = {template for _name, _stack, template in DEPLOYMENT_ORDER}

    assert watched == deployed | {WORKFLOW_FILE}
    assert not [path for path in watched if path.startswith("infra/iam/")]


def test_the_workflow_shares_the_concurrency_group_of_the_workflow_it_overlaps() -> None:
    """Reads BOTH workflow files. Mutation: give this one a group of its own.

    ``infra/admission-state-machine.yaml`` is in both path filters and both workflows deploy
    the stack that holds it, so a push touching that file starts both runs. With separate
    groups the second reaches ``cloudformation deploy`` while the first is mid-update, and
    the failure is a change set conflict that reads as a transient.

    The group is an expression because a dispatch that only asks about a run deploys
    nothing, and queueing a question behind a deploy -- or a deploy behind a question --
    buys nothing. What matters here is the branch taken when there is no run id: that one
    has to still be Phase 2's group, spelled the same way.
    """
    parsed = workflow()
    phase2 = load_workflow(PHASE2_WORKFLOW_PATH)
    phase2_paths = set(phase2["on"]["push"]["paths"])

    group = parsed["concurrency"]["group"]
    assert parsed["concurrency"]["cancel-in-progress"] is False
    assert f"'{phase2['concurrency']['group']}'" in group, (
        "the deploying branch of the group must be the group Phase 2 named"
    )
    assert f"'{CONCURRENCY_GROUP}'" in group
    assert "describe" in group, "a report should not take the deploy lock"
    assert ADMISSION_TEMPLATE in phase2_paths & set(parsed["on"]["push"]["paths"])


def test_workflow_permissions_are_minimal_and_the_token_is_scoped_to_the_job() -> None:
    """Mutation: move ``id-token: write`` to the top level.

    Every job in the file would then be able to mint an OIDC token for the deploy role. There
    is one job today, so nothing would break and nothing would say so; the next job added to
    this file would silently inherit a credential nobody meant it to have.
    """
    parsed = workflow()
    job = only_job(parsed)

    assert parsed["permissions"] == {"contents": "read"}
    assert "id-token" not in parsed["permissions"]
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert job["timeout-minutes"] <= 30


def test_the_workflow_pins_the_same_action_commits_the_earlier_phases_reviewed() -> None:
    """Reads THREE workflow files. Mutation: bump one action here and not the others.

    All three workflows assume the same role against the same account. Pinning them to
    different commits of the same action would be three supply chains to review instead of
    one, and reading the SHAs out of the earlier files is what keeps them together.
    """
    job = only_job(workflow())
    phase1_job = only_job(load_workflow(PHASE1_WORKFLOW_PATH))
    phase2_job = only_job(load_workflow(PHASE2_WORKFLOW_PATH))

    for name in ("Check out repository", "Configure AWS credentials"):
        pinned = step(job, name)["uses"]
        assert pinned == step(phase1_job, name)["uses"]
        assert pinned == step(phase2_job, name)["uses"]
        assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", pinned), f"{name} is not pinned to a commit"


def test_every_expression_names_something_that_actually_exists() -> None:
    """Mutation: misspell ``vars.AWS_INFRA_DEPLOYER_ROLE_ARN``.

    GitHub resolves an unknown property on a known context to the empty string rather than
    failing the run, so a plausible typo surfaces as an unexplained AssumeRole failure.
    """
    assert unreal_context_references(WORKFLOW_PATH) == []


def test_the_workflow_assumes_the_deployer_role_and_masks_the_account_id() -> None:
    """Mutation: drop ``mask-aws-account-id``.

    The verification reads a state machine ARN out of a stack output and hands it back to
    the CLI. Masking is what keeps a public run log from being a free lookup of the sandbox
    account number.
    """
    credentials = step(only_job(workflow()), "Configure AWS credentials")

    assert credentials["with"] == {
        "role-to-assume": "${{ vars.AWS_INFRA_DEPLOYER_ROLE_ARN }}",
        "aws-region": "${{ vars.AWS_REGION }}",
        "role-duration-seconds": 900,
        "mask-aws-account-id": True,
    }


def test_every_template_is_validated_before_any_of_them_is_deployed() -> None:
    """Mutation: validate after the first deploy.

    A malformed fourth template would then be discovered with three stacks already changed,
    which is the state that needs a person with laptop credentials rather than a re-run.
    """
    job = only_job(workflow())
    validate_script = step(job, "Validate CloudFormation templates")["run"]
    step_names = [candidate.get("name") for candidate in job["steps"]]

    assert aws_commands(validate_script) == [
        ["aws", "cloudformation", "validate-template", "--template-body", f"file://{template}"]
        for _name, _stack, template in DEPLOYMENT_ORDER
    ]
    assert step_names.index("Validate CloudFormation templates") < min(
        step_names.index(name) for name, _stack, _template in DEPLOYMENT_ORDER
    )


def test_the_stacks_deploy_in_dependency_order_and_the_state_machine_is_last() -> None:
    """Mutation: move the events stack before the compute stack.

    The rule's pattern names the job queue ARN as a string, so a rule deployed before the
    queue exists deploys perfectly and matches nothing forever. Nothing in CloudFormation
    checks it; this order is the only thing that does.
    """
    job = only_job(workflow())
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
    # The amended state machine goes last and under the Phase 2 stack's name, because that
    # is the stack that already holds it.
    assert DEPLOYMENT_ORDER[-1][1] == ADMISSION_STACK


def test_no_deploy_acknowledges_an_iam_capability_because_no_template_needs_one() -> None:
    """Mutation: add ``--capabilities CAPABILITY_NAMED_IAM`` to be safe.

    CloudFormation demands it only for a template containing an IAM entity, and none of these
    five does -- an SQS queue policy is a resource policy, and passing an existing role to a
    function or a compute environment needs ``iam:PassRole`` on the caller rather than a
    stack capability. Withholding it means a template that grows a role fails at
    InsufficientCapabilities, naming the reason, instead of part-way into a rollback.
    """
    flags = [
        token for script in run_scripts() for command in aws_commands(script) for token in command
    ]

    assert "--capabilities" not in flags
    assert not [token for token in flags if token.upper().startswith("CAPABILITY_")]


def test_the_workflow_never_submits_a_job_and_never_asks_to() -> None:
    """Mutation: add a ``batch submit-job`` smoke test to the verification.

    It would be denied -- the deployer holds no ``batch:SubmitJob`` -- and the reason it must
    stay denied is that the admission state machine is the only principal in this account
    that may start compute. A deploy pipeline that could submit would be a compute path with
    no approval gate in front of it.
    """
    commands = [command for script in run_scripts() for command in aws_commands(script)]
    verbs = {tuple(command[1:3]) for command in commands}

    assert ("batch", "submit-job") not in verbs
    assert ("batch", "terminate-job") not in verbs
    # `list-` joined the allowed prefixes with the run report, which finds a job by searching
    # each queue for one whose name is the run id -- the run id is the job *name*, and only a
    # list can search by it. `logs` joined the constrained services at the same time: the
    # report reads a log stream, and a service left off this list is one whose verbs nothing
    # here checks.
    assert all(
        command[2].startswith(("describe", "validate", "deploy", "get-", "list-"))
        for command in commands
        if command[1] in ("batch", "events", "stepfunctions", "s3api", "logs")
    )


def test_the_verification_reads_the_live_shape_and_never_prints_an_account_id() -> None:
    """Mutation: query the job definition's image string instead of a digest-pinned boolean.

    The image reference begins with the registry host, which is the account id with a
    suffix, so a mismatch would print it into a public log. Asking CloudFormation for a
    boolean gives the same answer and cannot leak.
    """
    verify_script = step(only_job(workflow()), VERIFY_STEP)["run"]
    commands = aws_commands(verify_script)
    services = [tuple(command[1:3]) for command in commands]

    assert services == [
        ("cloudformation", "describe-stacks"),
        ("batch", "describe-compute-environments"),
        ("batch", "describe-job-queues"),
        ("batch", "describe-job-definitions"),
        ("batch", "describe-compute-environments"),
        ("batch", "describe-job-queues"),
        ("batch", "describe-job-definitions"),
        ("events", "describe-rule"),
        ("stepfunctions", "describe-state-machine"),
        ("s3api", "get-bucket-versioning"),
    ]
    # The state machine ARN comes from a stack output rather than being assembled, so
    # nothing here has to know the account number to run.
    assert not re.search(r"(?<!\d)\d{12}(?!\d)", verify_script)
    assert "get-caller-identity" not in verify_script
    assert "containerProperties.image}" not in verify_script


def test_the_verification_pins_the_shape_the_templates_describe() -> None:
    """Mutation: drop ``minvCpus`` from the expected compute environment.

    A template test proves what the file says; this proves what the account holds, and
    ``minvCpus`` is the one number a console edit could change that costs money continuously
    and shows up nowhere else. ``subnetCount`` is here for the same reason: five is the
    number of zones that can hold the instance type.
    """
    verify_script = step(only_job(workflow()), VERIFY_STEP)["run"]
    source = verify_script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]

    assert literal_assignment(source, "expected_compute_environment") == {
        "name": "sbsandbox-intern-edullm-cpu",
        "type": "MANAGED",
        "status": "VALID",
        "state": "ENABLED",
        "minvCpus": 0,
        "subnetCount": 5,
    }
    assert literal_assignment(source, "expected_job_queue") == {
        "name": "sbsandbox-intern-edullm-cpu",
        "status": "VALID",
        "state": "ENABLED",
    }
    assert literal_assignment(source, "expected_job_definition") == {
        "name": "sbsandbox-intern-edullm-cpu-run",
        "type": "container",
        "status": "ACTIVE",
        "digestPinned": True,
        "tagged": False,
    }
    # imageType is the one line here whose absence produces no error anywhere. Left to its
    # default the GPU environment runs the CPU AMI, which has no NVIDIA driver, so the job
    # runs on the CPU at GPU prices and every other value in this dictionary still matches.
    assert literal_assignment(source, "expected_gpu_compute_environment") == {
        "name": "sbsandbox-intern-edullm-gpu",
        "type": "MANAGED",
        "status": "VALID",
        "state": "ENABLED",
        "minvCpus": 0,
        "subnetCount": 5,
        "imageType": "ECS_AL2023_NVIDIA",
    }
    assert literal_assignment(source, "expected_gpu_job_queue") == {
        "name": "sbsandbox-intern-edullm-gpu",
        "status": "VALID",
        "state": "ENABLED",
    }
    assert literal_assignment(source, "expected_gpu_job_definition") == {
        "name": "sbsandbox-intern-edullm-gpu-run",
        "type": "container",
        "status": "ACTIVE",
        "digestPinned": True,
        "tagged": False,
        "requestsAGpu": True,
        "injectsTheWandbKey": True,
    }
    assert literal_assignment(source, "expected_rule") == {
        "name": "sbsandbox-intern-edullm-batch-lifecycle",
        "state": "ENABLED",
        "scopedToOurQueue": True,
        "scopedToTheGpuQueue": True,
    }
    assert literal_assignment(source, "expected_state_machine") == {
        "status": "ACTIVE",
        "submitsToBatch": True,
        "writesTheBinding": True,
        "readsTheImageScan": True,
    }
    assert literal_assignment(source, "expected_outputs_versioning") == {"status": "Enabled"}
    # A verification that only printed its findings would go green on a drifted stack.
    assert "raise SystemExit" in source
    assert "PHASE3_BATCH_VERIFICATION_PASSED" in verify_script


def test_every_run_body_is_strict_about_failures_and_unset_variables() -> None:
    """Mutation: drop ``set -euo pipefail`` from one step.

    A run body without it continues past a failed command and exits on the last one, so a
    deploy that failed in the middle reports the exit status of the step's final line.
    """
    # One per stack, plus validate, plus verify, plus the dispatch guard. The count is the
    # point rather than scaffolding: a run body added without ``set -euo pipefail`` would
    # otherwise be checked by nothing, so a new step is meant to fail here once and be
    # counted in deliberately.
    scripts = run_scripts()

    # Five since the run report joined the failure diagnostic, both added 2026-08-01. It is
    # the one run body here that executes only when something has already gone wrong, which
    # makes strictness matter more rather than less -- a diagnostic that swallowed its own
    # error would report nothing and still let the job finish reporting the deploy failure,
    # which is indistinguishable from the diagnostic having found nothing to say.
        # Seven deploys, plus: the dispatch gate, validate, the failure diagnostic, the two
        # verifications, the queue view, and the per-run report. The second verification
        # arrived with the nine GPU shapes and is its own step rather than nine more
        # expectations bolted onto the first.
    assert len(scripts) == len(DEPLOYMENT_ORDER) + 7
    assert all(script.startswith("set -euo pipefail\n") for script in scripts)


def test_no_step_here_reaches_for_an_interpreter_the_workflow_never_installs() -> None:
    """Mutation: write the run report in ``uv run python``.

    That is how the rest of this repository runs Python, and it is wrong here: this workflow
    installs nothing but the checkout and the credential, so ``uv`` is not on the runner.
    The report is ``continue-on-error`` -- a report that cannot be produced must not turn a
    successful deploy red -- so the failure is a step that exits 127, a job that stays green,
    and an operator who learns nothing about the run they asked about. It cost a dispatch to
    find. This makes the next one cost a test run instead.
    """
    installed = {
        candidate.get("uses", "").split("@")[0] for candidate in only_job(workflow())["steps"]
    }
    assert not [action for action in installed if "setup-uv" in action or "setup-python" in action]

    for script in run_scripts():
        # Comments are stripped first, because the note explaining why this workflow uses
        # python3 has to be free to name the thing it is warning about.
        executable = "\n".join(
            line for line in script.splitlines() if not line.lstrip().startswith("#")
        )
        assert not re.search(r"(?<![\w-])uv(?![\w-])", executable)


def test_the_workflow_does_not_reach_for_the_retired_shared_deploy_role() -> None:
    """Mutation: switch to ``vars.AWS_DEPLOY_ROLE_ARN``.

    ``InternGitHubActionsDeploy-sbsandbox`` is assumable by any repository in the
    organization. The deployer ARN is a separate repository variable so that restoring one
    cannot silently restore the other.
    """
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "vars.AWS_DEPLOY_ROLE_ARN" not in text
    assert "InternGitHubActionsDeploy" not in text
    assert text.count("vars.AWS_INFRA_DEPLOYER_ROLE_ARN") == 1


# --------------------------------------------------------------------------------------
# The verification, executed against stubbed answers
# --------------------------------------------------------------------------------------

# Each aws call the verification makes answers from an environment variable, so a test can
# hand it a drifted account without an AWS session. The state machine ARN is deliberately
# unreadable as an account number: the step reads it from a stack output and passes it
# straight back, and nothing in it needs to look real.
#
# The three Batch verbs are each called twice now, once per queue, so the inner case
# discriminates on the resource name in the arguments rather than on the verb alone. A stub
# that answered both calls from one variable would hand the GPU expectation the CPU
# account's shape, and every drift case below would pass while proving nothing about the
# half that is new. The GPU names all contain ``-gpu``; none of the CPU calls does.
AWS_STUB = """
case "$1 $2" in
  "cloudformation describe-stacks") printf '%s\\n' "${STACK_OUTPUT_ARN}" ;;
  "batch describe-compute-environments")
    case "$*" in
      *sbsandbox-intern-edullm-gpu*) printf '%s' "${GPU_COMPUTE_ENVIRONMENT_JSON}" ;;
      *) printf '%s' "${COMPUTE_ENVIRONMENT_JSON}" ;;
    esac ;;
  "batch describe-job-queues")
    case "$*" in
      *sbsandbox-intern-edullm-gpu*) printf '%s' "${GPU_JOB_QUEUE_JSON}" ;;
      *) printf '%s' "${JOB_QUEUE_JSON}" ;;
    esac ;;
  "batch describe-job-definitions")
    case "$*" in
      *sbsandbox-intern-edullm-gpu*) printf '%s' "${GPU_JOB_DEFINITION_JSON}" ;;
      *) printf '%s' "${JOB_DEFINITION_JSON}" ;;
    esac ;;
  "events describe-rule") printf '%s' "${RULE_JSON}" ;;
  "stepfunctions describe-state-machine") printf '%s' "${STATE_MACHINE_JSON}" ;;
  "s3api get-bucket-versioning") printf '%s' "${OUTPUTS_JSON}" ;;
  *) echo "unexpected aws call: $*" >&2 ; exit 64 ;;
esac
"""
OBSERVED_COMPUTE_ENVIRONMENT = {
    "name": "sbsandbox-intern-edullm-cpu",
    "type": "MANAGED",
    "status": "VALID",
    "state": "ENABLED",
    "minvCpus": 0,
    "subnetCount": 5,
}
OBSERVED_JOB_QUEUE = {
    "name": "sbsandbox-intern-edullm-cpu",
    "status": "VALID",
    "state": "ENABLED",
}
OBSERVED_JOB_DEFINITION = {
    "name": "sbsandbox-intern-edullm-cpu-run",
    "type": "container",
    "status": "ACTIVE",
    "digestPinned": True,
    "tagged": False,
}
OBSERVED_GPU_COMPUTE_ENVIRONMENT = {
    "name": "sbsandbox-intern-edullm-gpu",
    "type": "MANAGED",
    "status": "VALID",
    "state": "ENABLED",
    "minvCpus": 0,
    "subnetCount": 5,
    "imageType": "ECS_AL2023_NVIDIA",
}
OBSERVED_GPU_JOB_QUEUE = {
    "name": "sbsandbox-intern-edullm-gpu",
    "status": "VALID",
    "state": "ENABLED",
}
OBSERVED_GPU_JOB_DEFINITION = {
    "name": "sbsandbox-intern-edullm-gpu-run",
    "type": "container",
    "status": "ACTIVE",
    "digestPinned": True,
    "tagged": False,
    "requestsAGpu": True,
    "injectsTheWandbKey": True,
}
OBSERVED_RULE = {
    "name": "sbsandbox-intern-edullm-batch-lifecycle",
    "state": "ENABLED",
    "scopedToOurQueue": True,
    "scopedToTheGpuQueue": True,
}
OBSERVED_STATE_MACHINE = {
    "status": "ACTIVE",
    "submitsToBatch": True,
    "writesTheBinding": True,
    "readsTheImageScan": True,
}
OBSERVED_OUTPUTS = {"status": "Enabled"}


def run_verification(tmp_path: Path, **drift: object) -> subprocess.CompletedProcess[str]:
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "aws", AWS_STUB)
    # The runner has `python` on PATH; this sandbox may only have the interpreter running
    # the tests, so the step body is left alone and the name is supplied instead.
    write_stub(stub_bin, "python", f'exec "{sys.executable}" "$@"')

    observed: dict[str, Any] = {
        "COMPUTE_ENVIRONMENT_JSON": dict(OBSERVED_COMPUTE_ENVIRONMENT),
        "JOB_QUEUE_JSON": dict(OBSERVED_JOB_QUEUE),
        "JOB_DEFINITION_JSON": dict(OBSERVED_JOB_DEFINITION),
        "GPU_COMPUTE_ENVIRONMENT_JSON": dict(OBSERVED_GPU_COMPUTE_ENVIRONMENT),
        "GPU_JOB_QUEUE_JSON": dict(OBSERVED_GPU_JOB_QUEUE),
        "GPU_JOB_DEFINITION_JSON": dict(OBSERVED_GPU_JOB_DEFINITION),
        "RULE_JSON": dict(OBSERVED_RULE),
        "STATE_MACHINE_JSON": dict(OBSERVED_STATE_MACHINE),
        "OUTPUTS_JSON": dict(OBSERVED_OUTPUTS),
    }
    observed.update(drift)

    return run_step_script(
        step(only_job(workflow()), VERIFY_STEP)["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "STACK_OUTPUT_ARN": (
                "arn:aws:states:us-east-1:sandbox:stateMachine:sbsandbox-intern-edullm-admission"
            ),
            **{name: json.dumps(payload) for name, payload in observed.items()},
        },
        stub_bin=stub_bin,
    )


def test_the_verification_passes_against_the_account_the_templates_describe(
    tmp_path: Path,
) -> None:
    """Mutation: change any expected value without changing the template it came from.

    This is the anchor for the drift cases below: they only mean something if the undrifted
    run is green, and a verification that failed on everything would pass every one of them.
    """
    result = run_verification(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "PHASE3_BATCH_VERIFICATION_PASSED" in result.stdout


@pytest.mark.parametrize(
    ("name", "drifted", "label"),
    [
        (
            "COMPUTE_ENVIRONMENT_JSON",
            {**OBSERVED_COMPUTE_ENVIRONMENT, "minvCpus": 1},
            "compute environment",
        ),
        (
            "COMPUTE_ENVIRONMENT_JSON",
            {**OBSERVED_COMPUTE_ENVIRONMENT, "subnetCount": 6},
            "compute environment",
        ),
        (
            "COMPUTE_ENVIRONMENT_JSON",
            {**OBSERVED_COMPUTE_ENVIRONMENT, "status": "INVALID"},
            "compute environment",
        ),
        ("JOB_QUEUE_JSON", {**OBSERVED_JOB_QUEUE, "state": "DISABLED"}, "job queue"),
        (
            "JOB_DEFINITION_JSON",
            {**OBSERVED_JOB_DEFINITION, "digestPinned": False, "tagged": True},
            "job definition",
        ),
        # The GPU environment on the CPU AMI. This is the drift with no other symptom: the
        # job runs, exits zero, bills GPU rates and trains on the CPU. If any refactor makes
        # this case pass, the verification has stopped being worth running.
        (
            "GPU_COMPUTE_ENVIRONMENT_JSON",
            {**OBSERVED_GPU_COMPUTE_ENVIRONMENT, "imageType": "ECS_AL2023"},
            "gpu compute environment",
        ),
        (
            "GPU_COMPUTE_ENVIRONMENT_JSON",
            {**OBSERVED_GPU_COMPUTE_ENVIRONMENT, "minvCpus": 1},
            "gpu compute environment",
        ),
        (
            "GPU_JOB_QUEUE_JSON",
            {**OBSERVED_GPU_JOB_QUEUE, "state": "DISABLED"},
            "gpu job queue",
        ),
        # The other half of the AMI drift, reached by a different route: right AMI, no GPU
        # in resourceRequirements, so ECS never selects the NVIDIA runtime for the task.
        (
            "GPU_JOB_DEFINITION_JSON",
            {**OBSERVED_GPU_JOB_DEFINITION, "requestsAGpu": False},
            "gpu job definition",
        ),
        (
            "GPU_JOB_DEFINITION_JSON",
            {**OBSERVED_GPU_JOB_DEFINITION, "injectsTheWandbKey": False},
            "gpu job definition",
        ),
        ("RULE_JSON", {**OBSERVED_RULE, "scopedToOurQueue": False}, "lifecycle rule"),
        (
            "RULE_JSON",
            {**OBSERVED_RULE, "scopedToTheGpuQueue": False},
            "lifecycle rule",
        ),
        (
            "STATE_MACHINE_JSON",
            {**OBSERVED_STATE_MACHINE, "submitsToBatch": False},
            "admission state machine",
        ),
        ("OUTPUTS_JSON", {"status": "Suspended"}, "outputs bucket versioning"),
    ],
)
def test_a_drifted_account_fails_the_run_and_says_which_check_moved(
    tmp_path: Path,
    name: str,
    drifted: dict[str, Any],
    label: str,
) -> None:
    """Mutation: replace ``raise SystemExit`` with a ``print``.

    Every one of these is a state the account can reach without any file in this repository
    changing -- a console edit, a half-applied deploy, a rule renamed by hand. A verification
    that reported them and exited zero would make the green tick mean less than nothing,
    because it would look like the check had been done.
    """
    result = run_verification(tmp_path, **{name: drifted})

    assert result.returncode != 0
    assert "PHASE3_BATCH_VERIFICATION_PASSED" not in result.stdout
    assert "batch verification failed" in result.stderr
    assert label in result.stderr


def test_a_verification_call_the_stub_does_not_recognize_would_be_noticed(
    tmp_path: Path,
) -> None:
    """Mutation: add a seventh call the stub does not answer.

    This anchors the tests above: they pass because the step made exactly the calls the stub
    answers, not because a silent stub let everything through.
    """
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "aws", 'echo "unexpected aws call: $*" >&2 ; exit 64\n')

    result = run_step_script(
        step(only_job(workflow()), VERIFY_STEP)["run"],
        cwd=tmp_path,
        env={"RUNNER_TEMP": str(tmp_path)},
        stub_bin=stub_bin,
    )

    assert result.returncode != 0
    assert "unexpected aws call" in result.stderr


# --------------------------------------------------------------------------------------
# The nine-shape verification, executed against stubbed answers
# --------------------------------------------------------------------------------------

#: One environment, one queue and one definition per shape, answered from the arguments
#: rather than from a variable per shape. The step appends nine of each in a loop, so a stub
#: keyed by name would be twenty-seven variables saying the same thing; this composes the
#: expected answer from the profile name in the call and lets a test drift one field of it.
#:
#: The vCPU and GPU counts come out of the shape name, which is why the names carry them.
#: gpu-8xa10g is 192 and 8, gpu-4xt4 is 48 and 4, and so on, matching CONTAINER_SHAPES.
SHAPES_STUB = """
profile="$(printf '%s\\n' "$@" | sed -n 's/^sbsandbox-intern-edullm-\\(gpu-[a-z0-9]*\\)\\(-run\\)*$/\\1/p' | head -n 1)"
case "${profile}" in
  gpu-1xt4|gpu-1xl4) vcpus=4 ; gpus=1 ;;
  gpu-4xt4|gpu-4xa10g|gpu-4xl4|gpu-4xl40s) vcpus=48 ; gpus=4 ;;
  gpu-8xa100) vcpus=16 ; gpus=8 ;;
  gpu-8xa10g|gpu-8xh100) vcpus=192 ; gpus=8 ;;
  *) echo "unexpected aws call: $*" >&2 ; exit 64 ;;
esac
name="sbsandbox-intern-edullm-${profile}"
case "$1 $2" in
  "batch describe-compute-environments")
    printf '{"name":"%s","type":"MANAGED","status":"VALID","state":"ENABLED","minvCpus":0,"imageType":"%s"}' \
      "${name}" "${IMAGE_TYPE}" ;;
  "batch describe-job-queues")
    printf '{"name":"%s","status":"VALID","state":"ENABLED"}' "${name}" ;;
  "batch describe-job-definitions")
    if [ "${profile}" = "${DRIFTED_PROFILE}" ]; then
      gpus="${DRIFTED_GPU_COUNT}"
    fi
    printf '{"name":"%s-run","type":"container","status":"ACTIVE","digestPinned":true,"requestsAGpu":true,"gpuCount":"%s","vcpuCount":"%s"}' \
      "${name}" "${gpus}" "${vcpus}" ;;
  *) echo "unexpected aws call: $*" >&2 ; exit 64 ;;
esac
"""


def run_shapes_verification(
    tmp_path: Path,
    *,
    image_type: str = "ECS_AL2023_NVIDIA",
    drifted_profile: str = "",
    drifted_gpu_count: str = "",
) -> subprocess.CompletedProcess[str]:
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "aws", SHAPES_STUB)
    write_stub(stub_bin, "python", f'exec "{sys.executable}" "$@"')

    return run_step_script(
        step(only_job(workflow()), SHAPES_VERIFY_STEP)["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "IMAGE_TYPE": image_type,
            "DRIFTED_PROFILE": drifted_profile,
            "DRIFTED_GPU_COUNT": drifted_gpu_count,
        },
        stub_bin=stub_bin,
    )


def test_the_nine_shape_verification_passes_against_the_estate_the_template_describes(
    tmp_path: Path,
) -> None:
    """The anchor for the two drift cases below.

    It also establishes that the step asks about all nine: the stub refuses any name outside
    the list, so a loop that had lost a shape would still pass here, but a loop that had
    gained a tenth or misspelt one exits 64.
    """
    result = run_shapes_verification(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "PHASE4_GPU_SHAPE_VERIFICATION_PASSED" in result.stdout


def test_a_shape_left_on_the_cpu_ami_fails_the_run(tmp_path: Path) -> None:
    """The drift with no other symptom, now reachable nine more ways than before.

    An environment on the default AMI carries no NVIDIA driver, so an instance launches,
    joins the cluster, takes the job and trains on the CPU at GPU prices. Nothing errors and
    every other field in the check is satisfied.
    """
    result = run_shapes_verification(tmp_path, image_type="ECS_AL2023")

    assert result.returncode != 0
    assert "gpu shape verification failed" in result.stderr
    assert "compute environment" in result.stderr


def test_a_definition_asking_for_the_wrong_number_of_devices_fails_the_run(
    tmp_path: Path,
) -> None:
    """Mutation: check that a GPU is requested and not how many.

    One device on a g5.48xlarge deploys, runs, bills for eight and uses one. ``requestsAGpu``
    is true throughout, which is why the count is asserted separately from its presence.
    """
    result = run_shapes_verification(
        tmp_path, drifted_profile="gpu-8xa10g", drifted_gpu_count="1"
    )

    assert result.returncode != 0
    assert "gpu shape verification failed" in result.stderr
    assert "gpu-8xa10g job definition" in result.stderr


def test_asking_about_a_run_deploys_nothing() -> None:
    """Mutation: leave one deploy step ungated, which is how this started.

    The first version of ``describe_run`` deployed first and reported after, on the
    argument that an empty changeset is cheap. It is, once. But a job sits in RUNNABLE
    until Batch finds it a GPU and the only way to learn it moved is to ask again, so this
    input gets pressed every few minutes for as long as a run takes -- each press
    reconciling six stacks in a shared account another agent is also deploying into.

    So every step that reaches CloudFormation or reads the estate has to carry the
    condition, and enumerating them in the workflow is exactly the "eight chances to gate
    one wrongly" the original comment worried about. This finds the eighth by looking at
    what the step does rather than at a list: any step whose script deploys, validates or
    describes stacks is one that must be skipped when a run id is present.
    """
    steps = workflow()["jobs"]["deploy"]["steps"]

    gated = "inputs.describe_run == ''"
    for entry in steps:
        script = entry.get("run", "")
        touches_the_estate = (
            "cloudformation deploy" in script
            or "cloudformation validate-template" in script
            or "cloudformation describe-stacks" in script
        )
        if not touches_the_estate:
            continue
        assert gated in entry.get("if", ""), (
            f"{entry.get('name')!r} changes or reads the estate, so a dispatch that only "
            f"asks about a run must skip it"
        )

    reporting = next(entry for entry in steps if "Say what one run is doing" in entry.get("name", ""))
    assert "inputs.describe_run != ''" in reporting["if"]


def test_the_queue_view_answers_the_question_a_single_run_report_cannot() -> None:
    """Mutation: leave people to poll their own run id and infer the rest.

    RUNNABLE is the commonest answer this workflow gives and Batch never says why. One A10G
    sits behind the GPU queue, so the usual cause is that another job holds it -- and the
    per-run report cannot show that, because it only looks at the run it was handed. Two
    people polling their own runs both see "queued, no reason" and neither learns they are
    queued behind each other.
    """
    steps = workflow()["jobs"]["deploy"]["steps"]
    queues = next(entry for entry in steps if "what is on the queues" in entry.get("name", ""))

    assert queues["if"] == "inputs.describe_run == 'queues'"
    # Both queues, and the states a job passes through before it is anybody's answer.
    for state in ("RUNNING", "STARTING", "RUNNABLE"):
        assert state in queues["run"]
    assert "sbsandbox-intern-edullm-gpu" in queues["run"]
    assert "sbsandbox-intern-edullm-cpu" in queues["run"]

    # And it reaches the log, not only the step summary. The summary renders in the web UI
    # and there is no API for it, so a bare redirect makes this step read as empty to anybody
    # reading the run through the CLI -- which is how it was read the first time.
    assert ">> \"${GITHUB_STEP_SUMMARY}\"" not in queues["run"], (
        "write through tee so the rows are in the log as well as the summary"
    )
    assert "tee -a" in queues["run"]

    # And the per-run report must not also fire on the sentinel, which is not a run id and
    # would fail its own format check.
    reporting = next(entry for entry in steps if "Say what one run is doing" in entry.get("name", ""))
    assert "inputs.describe_run != 'queues'" in reporting["if"]
