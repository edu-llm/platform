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
#: The workflow this one is the fallback for. Both search the Batch queues for a run, so
#: both read the same file to find out which queues those are.
CANCEL_WORKFLOW_PATH = WORKFLOWS_ROOT / "cancel-run.yml"

ENUMERATION_STEP = "List every queue a run could be on"
QUEUE_VIEW_STEP = "Say what is on the queues"
RUN_REPORT_STEP = "Say what one run is doing"
QUEUE_LIST_FILE = "${RUNNER_TEMP}/queues.txt"

OUTPUTS_TEMPLATE = "infra/outputs-bucket.yaml"
NETWORK_TEMPLATE = "infra/batch-network.yaml"
COMPUTE_TEMPLATE = "infra/batch-compute.yaml"
GPU_COMPUTE_TEMPLATE = "infra/batch-compute-gpu.yaml"
GPU_SHAPES_TEMPLATE = "infra/batch-compute-gpu-shapes.yaml"
EVENTS_TEMPLATE = "infra/batch-events.yaml"
JANITOR_TEMPLATE = "infra/expiry-janitor.yaml"
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
    # Nothing to do with Batch. It sweeps EC2 instances a person launched through
    # edullm-researcher, and it is a step on this workflow because the deployer role's trust
    # pins job_workflow_ref to three files -- a workflow of its own would need that trust
    # amended by the very access the amendment grants. Anywhere in this list would do for
    # ordering, since it depends on nothing here; it sits second-to-last because the state
    # machine has to stay last.
    ("Deploy the expiry janitor stack", "sbsandbox-intern-edullm-janitor", JANITOR_TEMPLATE),
    ("Deploy the amended admission state machine", ADMISSION_STACK, ADMISSION_TEMPLATE),
)
VALIDATE_STEP = "Upload and validate the CloudFormation templates"
VERIFY_STEP = "Verify CPU and GPU batch execution"
SHAPES_VERIFY_STEP = "Verify the remaining GPU shapes"

#: CloudFormation refuses a template carried in the request body above this, on
#: ValidateTemplate as well as on CreateChangeSet, so it is a limit that fails a workflow
#: rather than a stack. A template read from S3 is allowed 1 MB instead.
INLINE_TEMPLATE_LIMIT_BYTES = 51_200
S3_TEMPLATE_LIMIT_BYTES = 1_000_000

#: The bucket the deployer already holds ``s3:PutObject`` on, and the third top-level name
#: in it after ``admission-validator/`` and ``lifecycle-recorder/``. Two prefixes because
#: two things write here: this workflow uploads one readable object per template for the
#: ``--template-url`` validation, and ``deploy --s3-bucket`` writes its own copies named for
#: the checksum of the file.
ARTIFACTS_BUCKET = "sbsandbox-intern-edullm-artifacts"
TEMPLATE_PREFIX = "cloudformation-templates"
CHECKSUMMED_PREFIX = f"{TEMPLATE_PREFIX}/checksummed"


def uploaded_key(template: str) -> str:
    return f"{TEMPLATE_PREFIX}/{Path(template).name}"


def template_url(template: str) -> str:
    # Path style, which is what the CLI itself builds when --s3-bucket hands a TemplateURL
    # to CloudFormation. The region comes from the environment the credential action sets.
    return f"https://s3.${{AWS_REGION}}.amazonaws.com/{ARTIFACTS_BUCKET}/{uploaded_key(template)}"

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


def test_every_template_is_uploaded_and_validated_before_any_of_them_is_deployed() -> None:
    """Mutation: validate after the first deploy.

    A malformed fourth template would then be discovered with three stacks already changed,
    which is the state that needs a person with laptop credentials rather than a re-run.

    The upload is in the same step and has to be, because ``validate-template`` reaches a
    template over 51,200 bytes only through ``--template-url``. It takes no ``--s3-bucket``
    of its own, so the object has to exist before the validation rather than as a side
    effect of the deploy that follows.
    """
    job = only_job(workflow())
    validate_script = step(job, VALIDATE_STEP)["run"]
    step_names = [candidate.get("name") for candidate in job["steps"]]

    expected: list[list[str]] = []
    for _name, _stack, template in DEPLOYMENT_ORDER:
        expected.append(
            [
                "aws",
                "s3api",
                "put-object",
                "--bucket",
                ARTIFACTS_BUCKET,
                "--key",
                uploaded_key(template),
                "--body",
                template,
            ]
        )
        expected.append(
            ["aws", "cloudformation", "validate-template", "--template-url", template_url(template)]
        )

    assert aws_commands(validate_script) == expected
    assert step_names.index(VALIDATE_STEP) < min(
        step_names.index(name) for name, _stack, _template in DEPLOYMENT_ORDER
    )


def test_no_template_is_passed_inline_and_none_is_near_the_limit_that_replaced_it() -> None:
    """Mutation: put the one template that fits back on ``--template-body``.

    That is the arrangement this workflow had until 2026-08-02, and it was already broken:
    ``infra/batch-compute-gpu-shapes.yaml`` had grown to 68,715 bytes against a 51,200-byte
    inline limit, so the validation step could not have run. The limit is enforced on the
    request, not on the stack, so nothing rolls back and nothing partially applies -- the
    workflow simply cannot proceed until this file changes.

    All seven go the same way rather than only the one over the line, because
    ``infra/admission-state-machine.yaml`` is 1,568 bytes short of the same cliff and a
    workflow where six templates take one path and the seventh takes another is a trap for
    whoever adds the eighth.
    """
    repository_root = WORKFLOWS_ROOT.parent.parent
    scripts = run_scripts()
    flags = [token for script in scripts for command in aws_commands(script) for token in command]

    assert "--template-body" not in flags
    assert "file://" not in "".join(flags)

    for _name, _stack, template in DEPLOYMENT_ORDER:
        size = (repository_root / template).stat().st_size
        assert size < S3_TEMPLATE_LIMIT_BYTES, (
            f"{template} is {size} bytes, past the ceiling S3 raises this to as well"
        )

    # The measurement the decision rests on, kept here so that a template shrinking back
    # under the inline limit does not read as a reason to undo any of this.
    oversized = [
        template
        for _name, _stack, template in DEPLOYMENT_ORDER
        if (repository_root / template).stat().st_size > INLINE_TEMPLATE_LIMIT_BYTES
    ]
    assert GPU_SHAPES_TEMPLATE in oversized


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
                # Above the inline limit for one of these and within 1,568 bytes of it for a
                # second, so the CLI uploads and passes a TemplateURL instead. Keyed by the
                # checksum of the file, so an unchanged template writes no object.
                "--s3-bucket",
                ARTIFACTS_BUCKET,
                "--s3-prefix",
                CHECKSUMMED_PREFIX,
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
    # One write, named rather than admitted by prefix. The templates are uploaded before
    # they are validated because `validate-template` reaches a template over 51,200 bytes
    # only through --template-url, and a prefix like "put-" would have let a later step put
    # anything anywhere. Where it may write is settled by IAM rather than here: the grant is
    # s3:PutObject on sbsandbox-intern-edullm-artifacts/* and reaches no other bucket.
    permitted_writes = {("s3api", "put-object")}
    assert all(
        command[2].startswith(("describe", "validate", "deploy", "get-", "list-"))
        or tuple(command[1:3]) in permitted_writes
        for command in commands
        if command[1] in ("batch", "events", "stepfunctions", "s3api", "logs")
    )
    assert all(
        command[3:7] == ["--bucket", ARTIFACTS_BUCKET, "--key", uploaded_key(template)]
        for command, (_name, _stack, template) in zip(
            [command for command in commands if tuple(command[1:3]) in permitted_writes],
            DEPLOYMENT_ORDER,
            strict=True,
        )
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

    # The failure diagnostic is the one run body here that executes only when something has
    # already gone wrong, which makes strictness matter more rather than less -- a diagnostic
    # that swallowed its own error would report nothing and still let the job finish
    # reporting the deploy failure, which is indistinguishable from the diagnostic having
    # found nothing to say.
    #
    # Seven deploys, plus: the dispatch gate, validate, the failure diagnostic, the two
    # verifications, the tooling install, the queue enumeration, the queue view, and the
    # per-run report. The second verification arrived with the GPU shapes stack and is its
    # own step rather than thirteen more expectations bolted onto the first. The install and
    # the enumeration arrived together, because the two reports stopped looping over a pair
    # of queue names typed into this file.
    assert len(scripts) == len(DEPLOYMENT_ORDER) + 9
    assert all(script.startswith("set -euo pipefail\n") for script in scripts)


def test_no_step_here_reaches_for_an_interpreter_the_workflow_never_installs() -> None:
    """Mutation: read the queue list with ``uv run`` in a step the install does not cover.

    This workflow installed nothing but the checkout and the credential, and the two reports
    were written in ``python3`` for exactly that reason -- ``uv`` is how the rest of the
    repository runs Python and it was simply not on this runner. A step reaching for it
    exits 127, and because the reports are ``continue-on-error`` the job stays green and the
    operator learns nothing about the run they asked about. That cost a dispatch to find.

    The queue enumeration changed the premise rather than the risk: reading
    ``config/execution-targets.yaml`` means reading a pydantic contract, so ``uv`` is now
    installed here. So the invariant becomes the pairing rather than the absence. Every step
    that reaches for ``uv`` must come after the step that installs it *and* carry the same
    condition, because an install skipped by its own gate provides nothing to a step whose
    gate is wider -- which is the 127 back again, on the dispatch shape nobody tested.
    """
    steps = only_job(workflow())["steps"]
    installed = {candidate.get("uses", "").split("@")[0] for candidate in steps}
    assert not [action for action in installed if "setup-uv" in action or "setup-python" in action]

    def executable(entry: dict[str, Any]) -> str:
        # Comments are stripped first, because the note explaining why the reports still use
        # python3 has to be free to name the thing it is contrasting with.
        return "\n".join(
            line
            for line in str(entry.get("run", "")).splitlines()
            if not line.lstrip().startswith("#")
        )

    provides = [index for index, entry in enumerate(steps) if "pipx install" in executable(entry)]
    assert len(provides) == 1, "one step installs the tooling, and it is the one gated below"
    install = provides[0]
    gate = steps[install].get("if", "")
    assert gate == "inputs.describe_run != ''", (
        "the install is for the report path only, so an ordinary deploy pays nothing for it"
    )

    for index, entry in enumerate(steps):
        if not re.search(r"(?<![\w-])uv(?![\w-])", executable(entry)):
            continue
        assert index >= install, f"{entry.get('name')!r} reaches for uv before it is installed"
        assert entry.get("if", "") == gate, (
            f"{entry.get('name')!r} reaches for uv under a condition the install does not "
            "share, so there is a dispatch that skips the install and runs this"
        )


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
# The fourteen-shape verification, executed against stubbed answers
# --------------------------------------------------------------------------------------

#: One environment, one queue and one definition per shape, answered from the arguments
#: rather than from a variable per shape. The step appends fourteen of each in a loop, so a
#: stub keyed by name would be forty-two variables saying the same thing; this composes the
#: expected answer from the profile name in the call and lets a test drift one field of it.
#:
#: The vCPU and GPU counts come out of the shape name, which is why the names carry them.
#: gpu-8xa10g is 192 and 8, gpu-4xt4 is 48 and 4, and so on, matching CONTAINER_SHAPES. The
#: exceptions are gpu-8xt4 on the g4dn.metal host, which is 96 vCPU rather than 192,
#: gpu-8xa100 on p4d.24xlarge, which is also 96, and gpu-1xh100 on p5.4xlarge, which is one
#: device on 16 vCPU rather than on 4.
SHAPES_STUB = """
profile="$(printf '%s\\n' "$@" | sed -n 's/^sbsandbox-intern-edullm-\\(gpu-[a-z0-9]*\\)\\(-run\\)*$/\\1/p' | head -n 1)"
case "${profile}" in
  gpu-1xt4|gpu-1xl4|gpu-1xl40s) vcpus=4 ; gpus=1 ;;
  gpu-1xh100) vcpus=16 ; gpus=1 ;;
  gpu-4xt4|gpu-4xa10g|gpu-4xl4|gpu-4xl40s) vcpus=48 ; gpus=4 ;;
  gpu-8xt4|gpu-8xa100) vcpus=96 ; gpus=8 ;;
  gpu-8xa10g|gpu-8xl4|gpu-8xl40s|gpu-8xh100) vcpus=192 ; gpus=8 ;;
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


def test_the_fourteen_shape_verification_passes_against_the_estate_the_template_describes(
    tmp_path: Path,
) -> None:
    """The anchor for the two drift cases below.

    It also establishes that the step asks about all fourteen: the stub refuses any name
    outside the list, so a loop that had gained a fifteenth or misspelt one exits 64.

    A loop that had LOST a shape would still pass here, which is not hypothetical -- this
    loop carried thirteen of the fourteen shapes the stack deploys, and the missing one was
    gpu-1xh100. ``test_the_verification_asks_about_every_shape_the_stack_deploys`` below is
    the assertion that closes that direction, because this one structurally cannot.
    """
    result = run_shapes_verification(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "PHASE4_GPU_SHAPE_VERIFICATION_PASSED" in result.stdout


def test_the_verification_asks_about_every_shape_the_stack_deploys() -> None:
    """THE ONE THAT MATTERS HERE. Mutation: leave a shape out of the loop.

    Every other test in this section is written against the stub, and the stub is built
    from the loop -- so a shape dropped from the loop is a shape the stub is never asked
    about, and every one of them goes on passing. That is not a hypothetical shape of
    failure. This loop carried thirteen entries while
    ``infra/batch-compute-gpu-shapes.yaml`` declared fourteen job definitions, and the
    absent one was ``gpu-1xh100``: the newest shape, a p5.4xlarge, and the shape that
    shipped asking 258048 MiB of a host that registers 253952. Nothing about a deploy of it
    was ever verified.

    So the list is compared against the template rather than against itself. The template
    is the authority for what exists, exactly as it is for ``CONTAINER_SHAPES``.

    ``gpu-1xa10g`` is deliberately not expected here. It is declared in
    ``infra/batch-compute-gpu.yaml`` and verified by the earlier
    "Verify CPU and GPU batch execution" step, so requiring it would move a check onto the
    stack that does not deploy it.
    """
    declared = set(
        re.findall(
            r"JobDefinitionName: sbsandbox-intern-edullm-(gpu-[a-z0-9]+)-run",
            (WORKFLOWS_ROOT.parents[1] / "infra" / "batch-compute-gpu-shapes.yaml").read_text(),
        )
    )
    script = step(only_job(workflow()), SHAPES_VERIFY_STEP)["run"]
    verified = set(re.findall(r"\b(gpu-[a-z0-9]+):\d+:\d+", script))

    assert declared, "no job definitions were parsed out of the GPU shapes template"
    assert verified == declared, (
        f"the deploy verifies {sorted(verified)} and the stack declares {sorted(declared)}. "
        f"A shape the stack deploys and the loop skips is deployed and never checked."
    )


def test_a_shape_left_on_the_cpu_ami_fails_the_run(tmp_path: Path) -> None:
    """The drift with no other symptom, now reachable thirteen more ways than before.

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
    # The states a job passes through before it is anybody's answer. Which queues it asks
    # about is no longer written here at all; that seam is held below.
    for state in ("RUNNING", "STARTING", "RUNNABLE"):
        assert state in queues["run"]
    assert QUEUE_LIST_FILE in queues["run"]

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


# --------------------------------------------------------------------------------------
# The admin fallback, and the queues it searches
# --------------------------------------------------------------------------------------
#
# cancel-run.yml is what researchers use to look at a run, and when the run-canceller role
# is missing it sends people here instead. So this workflow is the fallback for the one that
# was fixed, and until now it carried the defect that fix was for: two queue names typed
# into the file, against an account holding eleven and a configuration naming sixteen. An
# admin sent here about a run on a GPU shape queue was told no such job existed.


def configured_queues() -> set[str]:
    from edullm_platform.config import load_yaml
    from edullm_platform.contracts.execution import ExecutionTargetCatalog

    catalog = load_yaml(
        WORKFLOWS_ROOT.parent.parent / "config" / "execution-targets.yaml",
        ExecutionTargetCatalog,
    )
    return {target.job_queue for target in catalog.targets}


def python_body(script: str) -> str:
    return script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


def test_the_fallback_reads_the_queue_list_the_workflow_it_backs_up_reads() -> None:
    """Reads BOTH workflow files. Mutation: write a second reader here.

    Two readers of one configuration file are two answers to where a run can be, and the
    second one stops agreeing at the next promotion -- which is the failure mode that put
    two queue names in this file in the first place. cancel-run.yml already had a step that
    reads ``config/execution-targets.yaml`` through the contract that owns it, so this is
    that step rather than something equivalent to it, and the comparison is byte-for-byte on
    the part that does the reading.
    """
    here = step(only_job(workflow()), ENUMERATION_STEP)["run"]
    there = next(
        candidate
        for candidate in load_workflow(CANCEL_WORKFLOW_PATH)["jobs"]["cancel"]["steps"]
        if candidate.get("name") == ENUMERATION_STEP
    )["run"]

    assert python_body(here) == python_body(there), (
        "the two workflows read the same file, so they must read it with the same code"
    )
    assert "ExecutionTargetCatalog" in python_body(here)
    assert "no_execution_targets_configured" in here, (
        "a checkout naming no queue must refuse rather than search nowhere, which is the "
        "same wrong answer a genuine absence produces"
    )


def test_no_queue_name_is_written_into_either_report_step() -> None:
    """Mutation: leave one of the two loops on the pair it used to name.

    The two report steps answer different questions -- what is on the queues, and what one
    run is doing -- and either one left behind reproduces the defect for half the dispatches.
    Asserted as the absence of every configured name so the mutation cannot be half done.

    The rest of the file is deliberately not covered by this. The verification steps name
    ``sbsandbox-intern-edullm-cpu`` and ``sbsandbox-intern-edullm-gpu`` on purpose: they
    assert the shape of two specific stacks against written-out expectations, which is a
    different job from searching for a run and one where naming the resource is the point.
    """
    job = only_job(workflow())
    queues = configured_queues()

    assert queues, "the configuration names no queue, so this test is measuring nothing"
    for name in (QUEUE_VIEW_STEP, RUN_REPORT_STEP):
        body = step(job, name)["run"]
        assert QUEUE_LIST_FILE in body, f"{name} does not read the enumeration"
        for queue in sorted(queues):
            assert queue not in body, (
                f"{queue} is written into the {name!r} step, so the search is a second "
                "roster that will disagree with config/execution-targets.yaml"
            )


#: The same three behaviours the cancel-run stub models: silence with exit zero for a queue
#: holding nothing -- which is also what a queue absent from the account answers -- a
#: non-zero exit for a refusal, and a row for a hit.
REPORT_STUB = """
queue=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--job-queue" ]]; then
    shift
    queue="$1"
    echo "${queue}" >> "${ASKED_QUEUES}"
  fi
  shift
done
if [[ -n "${REFUSED_QUEUE:-}" && "${queue}" == "${REFUSED_QUEUE}" ]]; then
  echo "An error occurred (TooManyRequestsException) when calling the ListJobs operation" >&2
  exit 254
fi
if [[ -n "${queue}" && "${queue}" == "${HOLDING_QUEUE:-}" ]]; then
  printf '%s\\n' "${HELD_ROW:-}"
fi
"""

THREE_QUEUES = ("queue-one", "queue-two", "queue-three")
REPORTED_RUN = "run_0198f0a1-2b3c-7d4e-8f01-23456789abcd"


def run_report_step(
    tmp_path: Path,
    name: str,
    *,
    queues: tuple[str, ...] = THREE_QUEUES,
    refused_queue: str = "",
    holding_queue: str = "",
    held_row: str = "",
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str], str]:
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "aws", REPORT_STUB)
    (tmp_path / "queues.txt").write_text(
        "".join(f"{queue}\n" for queue in queues), encoding="utf-8"
    )
    asked = tmp_path / "asked.txt"
    asked.touch()
    summary = tmp_path / "summary.md"
    summary.touch()

    result = run_step_script(
        step(only_job(workflow()), name)["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_STEP_SUMMARY": str(summary),
            "ASKED_QUEUES": str(asked),
            "REFUSED_QUEUE": refused_queue,
            "HOLDING_QUEUE": holding_queue,
            "HELD_ROW": held_row,
            **(extra_env or {}),
        },
        stub_bin=stub_bin,
    )
    return result, asked.read_text(encoding="utf-8").split(), summary.read_text(encoding="utf-8")


def test_the_queue_view_asks_about_every_queue_the_enumeration_listed(tmp_path: Path) -> None:
    """Executed. Mutation: iterate a pair again, which is what this step did.

    Reading the step for the absence of queue names proves nothing was typed in. Running it
    proves the loop reaches the queues the enumeration produced -- and a job on a queue this
    step does not ask about is a job nobody polling the estate can see, which is the whole
    reason the view exists.
    """
    result, asked, summary = run_report_step(
        tmp_path, QUEUE_VIEW_STEP, holding_queue="queue-two", held_row="run_abc\t1754000000000"
    )

    assert result.returncode == 0, result.stderr
    assert set(asked) == set(THREE_QUEUES)
    assert "`run_abc`" in summary


def test_a_queue_the_account_does_not_have_contributes_nothing_to_the_queue_view(
    tmp_path: Path,
) -> None:
    """Executed. The case that looks dangerous and is not, held so nobody "fixes" it.

    The configuration names sixteen queues and the account holds eleven, five shapes having
    been merged ahead of their stack. ``list-jobs`` against a queue Batch has never heard of
    answers an empty list and exits zero -- measured against all five and against a name
    that is not a queue at all -- so the absent queue costs one call and contributes no rows.

    Mutation: enumerate the account instead of the configuration. That trades a queue this
    workflow searches for nothing against a queue this workflow cannot see, which is the
    error that costs.
    """
    result, asked, summary = run_report_step(
        tmp_path,
        QUEUE_VIEW_STEP,
        queues=("queue-absent", "queue-present"),
        holding_queue="queue-present",
        held_row="run_abc\t1754000000000",
    )

    assert result.returncode == 0, result.stderr
    assert "queue-absent" in asked
    assert "`run_abc`" in summary
    assert "were refused" not in summary, "an empty answer is not a refusal"


def test_a_refused_call_does_not_end_the_queue_view_and_is_counted_in_it(
    tmp_path: Path,
) -> None:
    """THE REGRESSION GUARD, for this workflow. Executed. Mutation: drop the guard.

    Sixteen queues by five states is eighty sequential calls under ``set -euo pipefail``, and
    ListJobs is throttled per account. One refusal ended the loop over every queue after it,
    and the queues are sorted, so an early refusal hid most of the estate behind a table that
    looked complete.

    Counted rather than swallowed for the same reason it is counted in cancel-run.yml: an
    empty table and a table missing the rows nobody read are indistinguishable, and the
    closing line would otherwise claim the estate is idle on the strength of a search that
    did not happen.
    """
    result, asked, summary = run_report_step(
        tmp_path,
        QUEUE_VIEW_STEP,
        refused_queue="queue-one",
        holding_queue="queue-three",
        held_row="run_abc\t1754000000000",
    )

    assert result.returncode == 0, result.stderr
    assert {"queue-two", "queue-three"} <= set(asked), (
        "a refusal on the first queue ended the search over the rest"
    )
    assert "`run_abc`" in summary
    # Five states on the one refused queue.
    assert "**5 of the queue searches were refused**" in summary
    assert "every queue this platform configures is empty" not in summary


def test_the_per_run_report_searches_every_queue_rather_than_two_of_them(
    tmp_path: Path,
) -> None:
    """Executed. Mutation: leave this loop on the pair while fixing the other.

    This is the step ``cancel-run.yml`` sends an admin to when the run-canceller role is not
    deployed, and the question it answers is the one the researcher could not get an answer
    to. Answering it about two queues out of sixteen is the original defect, one workflow
    over.
    """
    result, asked, summary = run_report_step(
        tmp_path, RUN_REPORT_STEP, extra_env={"RUN_ID": REPORTED_RUN}
    )

    assert result.returncode == 0, result.stderr
    assert set(asked) == set(THREE_QUEUES)
    assert "on any configured queue" in summary


def test_a_refused_call_does_not_end_the_per_run_search_or_pass_for_an_absent_run(
    tmp_path: Path,
) -> None:
    """Executed. Mutation: guard the call with a bare ``|| true``.

    The guard alone keeps the search alive, which is the important half, and then hands the
    reader the ordinary sentence: no job under this run id. That is the confident wrong
    answer the two-queue bug produced, reintroduced by the fix for the abort. So a refusal
    is counted and the step says the search was incomplete instead of pronouncing on a run
    it never reached.
    """
    result, asked, summary = run_report_step(
        tmp_path,
        RUN_REPORT_STEP,
        refused_queue="queue-one",
        extra_env={"RUN_ID": REPORTED_RUN},
    )

    assert result.returncode == 0, result.stderr
    assert {"queue-two", "queue-three"} <= set(asked)
    assert "queue_search_incomplete" in result.stderr
    assert "7 of the queue searches were refused" in summary
    assert "on any configured queue" not in summary, (
        "an incomplete search must not borrow the sentence a complete one uses"
    )
