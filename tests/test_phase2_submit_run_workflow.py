"""Structural tests for the Phase 2 submission workflow.

Every assertion here reads the parsed workflow and asks whether a reference resolves to
something that exists, or runs a ``run`` body against stubs and asks what it did. None of
them compares the text of an expression, because that is the check Phase 1 shipped: a
fully green suite over a workflow that could not complete a single run, in which
``${{ github.job_workflow_sha }}`` was as acceptable as a property GitHub defines.
"""

from __future__ import annotations

import ast
import importlib.util
import itertools
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from workflow_support import (
    EXPRESSION_PATTERN,
    PROJECT_ROOT,
    PROPERTY_CHAIN_PATTERN,
    QUOTED_LITERAL_PATTERN,
    WORKFLOWS_ROOT,
    aws_commands,
    load_workflow,
    run_step_script,
    step,
    unreal_context_references,
    write_stub,
)

from edullm_platform import admission_handler
from edullm_platform.admission_denials import (
    ADMISSION_DENIED_ACTIONS,
    LINEAGE_BUCKET,
    read_state_machine_arn,
)
from edullm_platform.batch_denials import ADMISSION_BATCH_DENIED_ACTIONS
from edullm_platform.build_tooling import load_registry
from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import ApprovalEnvironment
from edullm_platform.contracts.execution import ExecutionTarget, ExecutionTargetCatalog
from edullm_platform.contracts.image import GitHubWorkflowRunReference
from edullm_platform.contracts.policy import ApprovalClass
from edullm_platform.contracts.results import output_prefix
from edullm_platform.execution import CONTAINER_SHAPES, WANDB_ENTITY, batch_submit_request
from edullm_platform.submission import SubmissionInputs
from edullm_platform.wandb_preflight import (
    NIGHTLY_VERDICT_ARTIFACT,
    NIGHTLY_VERDICT_FILENAME,
    NIGHTLY_WORKFLOW,
)
from tests.test_manifest import load_representative_manifest
from tools.resolve_published_image import RESOLVER_ECR_ACTIONS

WORKFLOW_FILE = ".github/workflows/submit-run.yml"
WORKFLOW_PATH = WORKFLOWS_ROOT / "submit-run.yml"
BUILD_WORKFLOW_PATH = WORKFLOWS_ROOT / "build-research-image.yml"

#: The other half of the preflight, and the reason it is read from here rather than only
#: from tests/test_nightly_workflow.py. The property that matters spans the two files: this
#: workflow refuses a submission on a verdict that one publishes, and nothing in GitHub or
#: CloudFormation connects them. A test that only ever looked at one side would let a
#: rename on the other turn the preflight permanently inert without failing.
NIGHTLY_PATH = WORKFLOWS_ROOT / NIGHTLY_WORKFLOW
NIGHTLY_WANDB_JOB = "wandb-credential"
NIGHTLY_CHECK_STEP = "Ask W&B whether it would accept the stored key"
NIGHTLY_UPLOAD_STEP = "Publish what W&B said, for the submission preflight to read"

TRUST_POLICY_PATH = PROJECT_ROOT / "infra" / "iam" / "admission-role.yaml"
LINEAGE_TEMPLATE_PATH = PROJECT_ROOT / "infra" / "lineage-bucket.yaml"

CHECKOUT_ACTION = "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09"
CREDENTIALS_ACTION = (
    "aws-actions/configure-aws-credentials@e6de054238d6b7531b4efff3b6587d9aade6a06c"
)
UPLOAD_ACTION = "actions/upload-artifact@330a01c490aca151604b8cf639adc76d48f6c5d4"
DOWNLOAD_ACTION = "actions/download-artifact@634f93cb2916e3fdff6788551b99b062d0335ce0"

#: GitHub raised the workflow_dispatch input ceiling from ten to twenty-five in December
#: 2025, and a workflow that declares more fails schema validation rather than degrading.
WORKFLOW_DISPATCH_INPUT_CEILING = 25

STATE_MACHINE_NAME = "sbsandbox-intern-edullm-admission"
PLATFORM_REPOSITORY = "edu-llm/platform"
JOB_WORKFLOW_REF = f"{PLATFORM_REPOSITORY}/{WORKFLOW_FILE}@refs/heads/main"
# AWS's documented example account id, which the repository secret scan exempts.
EXAMPLE_ACCOUNT_ID = "123456789012"
ADMISSION_ROLE_ARN = f"arn:aws:iam::{EXAMPLE_ACCOUNT_ID}:role/sbsandbox-intern-edullm-admission"
RUN_ID = "run_0198f0a1-2b3c-7d4e-8f01-23456789abcd"
APPROVED_SHA256 = "sha256:" + "a" * 64
RECORDED_SHA256 = "sha256:" + "f" * 64

RESOLVE_STEP = "Read which image this commit published"
RESOLVE_CREDENTIALS_STEP = "Configure AWS credentials"
RESOLVE_UPLOAD_STEP = "Upload what the registry answered"
RESOLVE_DOWNLOAD_STEP = "Download what the registry answered"
FORM_STEP = "Assemble the submission form"
COMPILE_STEP = "Compile the submission"
APPROVAL_STEP = "Read who released the gate"
VERIFY_STEP = "Recompute the manifest hash after approval"
REQUEST_STEP = "Assemble the admission request"
CREDENTIALS_STEP = "Configure AWS credentials"
DENIALS_STEP = "Attempt the actions the admission session must not have"
DENIALS_UPLOAD_STEP = "Upload the admission denial matrix"
REGISTRY_STEP = "Resolve the registered image repository"
BATCH_DENIALS_STEP = "Attempt the Batch actions the admission session must not have"
BATCH_DENIALS_UPLOAD_STEP = "Upload the Batch denial matrix"
WANDB_PREFLIGHT_STEP = "Refuse a submission the W&B key would not authenticate"
START_STEP = "Start the admission execution"
WAIT_STEP = "Wait for the admission decision"
DENY_STEP = "Attempt the admission role without an approval"
CANCELLED_STEP = "Record that a cancelled workflow stopped no compute"

DENIALS_TOOL = "tools/verify_admission_denials.py"
BATCH_DENIALS_TOOL = "tools/verify_batch_denials.py"
RESOLVER_TOOL = "tools/resolve_published_image.py"

#: The W&B preflight, and the tool it deliberately does not run. The check that reads the
#: secret is `tools/verify_wandb_credential.py`, and it runs in nightly.yml under the one
#: role in this account that holds `secretsmanager:GetSecretValue` and can be assumed from
#: GitHub. This workflow reads the verdict that produces, so the preflight adds no AWS call
#: and no permission -- which is why it contributes nothing to the enumeration below.
WANDB_PREFLIGHT_TOOL = "tools/verify_wandb_preflight.py"
WANDB_CREDENTIAL_TOOL = "tools/verify_wandb_credential.py"

# Outputs no run body can be read for. The compile job's four come out of
# tools/compile_submission.py, and the test below re-derives them from that tool rather
# than trusting this tuple; aws-account-id is a documented output of the credentials
# action.
DECLARED_OUTPUTS = {
    "commit": ("commit_sha",),
    "compile": ("run_id", "approval_class", "environment", "manifest_sha256"),
    "credentials": ("aws-account-id",),
}

TOOL_PATH_PATTERN = re.compile(r"tools/[A-Za-z0-9_./-]+\.py")


def _load() -> dict[str, Any]:
    return load_workflow(WORKFLOW_PATH)


def _job(name: str) -> dict[str, Any]:
    job = _load()["jobs"][name]
    assert isinstance(job, dict)
    return job


def _run_bodies() -> Iterator[tuple[str, str]]:
    for job_name, job in _load()["jobs"].items():
        for candidate in job["steps"]:
            script = candidate.get("run")
            if script is not None:
                yield f"{job_name}:{candidate.get('name')}", script


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    else:
        yield str(value)


def _references(text: str) -> Iterator[str]:
    """Every context reference an expression makes, as a dotted chain.

    Built from the same two patterns the shared expression checker uses, so a reference
    this reads is a reference that checker also sees.
    """
    for expression in EXPRESSION_PATTERN.findall(text):
        stripped = QUOTED_LITERAL_PATTERN.sub(" ", expression)
        for match in PROPERTY_CHAIN_PATTERN.finditer(stripped):
            segments = [segment for segment in match.group(2).split(".") if segment]
            yield ".".join([match.group(1), *segments])


def _tool_step_output_names(path: Path) -> tuple[str, ...]:
    """The GITHUB_OUTPUT names a tool writes, read out of the call that writes them."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", "")
        if name != "append_step_outputs":
            continue
        for argument in node.args[1:]:
            if isinstance(argument, ast.Tuple | ast.List):
                return tuple(
                    pair.elts[0].value
                    for pair in argument.elts
                    if isinstance(pair, ast.Tuple) and isinstance(pair.elts[0], ast.Constant)
                )
    raise AssertionError(f"no literal append_step_outputs call found in {path}")


def test_the_workflow_file_name_is_the_one_the_trust_policy_pins() -> None:
    # The admission role trust policy matches job_workflow_ref with StringEquals against
    # this exact path, so the file name is a security control rather than a preference.
    assert WORKFLOW_PATH.is_file()
    assert f".github/workflows/{WORKFLOW_PATH.name}" == WORKFLOW_FILE
    assert JOB_WORKFLOW_REF in TRUST_POLICY_PATH.read_text(encoding="utf-8")


def test_the_workflow_is_dispatch_only() -> None:
    # A push or pull_request trigger would mint a subject with no environment segment,
    # and every one of those runs would fail at AssumeRole for a reason that reads like a
    # broken role. There is also nothing to submit that a person did not fill in.
    workflow = _load()

    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}


def test_the_form_is_the_submission_inputs_contract_field_for_field() -> None:
    declared = _load()["on"]["workflow_dispatch"]["inputs"]

    assert set(declared) == set(SubmissionInputs.model_fields)
    assert len(declared) <= WORKFLOW_DISPATCH_INPUT_CEILING
    # Required on the form exactly where the contract has no default, so a submitter is
    # never asked for something the workload profile already fixes.
    for name, field in SubmissionInputs.model_fields.items():
        assert declared[name]["required"] is field.is_required(), name
        # string or choice, and nothing else. Both arrive as a single string, which is what
        # SubmissionInputs reads; boolean and number arrive as their own JSON types and
        # would be refused at parse. A choice is a string with a menu in front of it.
        assert declared[name]["type"] in ("string", "choice"), name
        assert declared[name]["description"].strip(), name


def test_no_optional_field_defaults_to_a_value_somebody_could_have_meant() -> None:
    """Mutation: default an override to a real profile, a real number, a real anything.

    The whole reason the overrides exist is that an override is visible in a way a silently
    different default is not. A default that names something real puts a choice in front of
    an approver that nobody made.

    THE EXACTLY-EMPTY RULE IS BACK, AND ONLY BECAUSE THE ONE EXCEPTION TO IT LEFT. It was
    relaxed to allow a word, because a ``choice`` input cannot offer a blank option and
    ``compute_profile`` was an override rendered as a dropdown. That field is required now,
    since the workload profile it took its default from no longer declares a machine, so no
    optional field on this form is a dropdown and none of them needs a word for absence.
    Requiring the strict form again is what stops one being reintroduced quietly.
    """
    declared = _load()["on"]["workflow_dispatch"]["inputs"]
    optional = [
        name for name, field in SubmissionInputs.model_fields.items() if not field.is_required()
    ]

    # Five, and the last two steps down from seven landed together. It went to seven when
    # ``image_digest`` stopped being required, a run's image now being derived from the
    # commit it declares with an override surviving for a deliberate pin. Then
    # ``fanout_parallelism`` was taken off the form because Batch accepts no such cap, and
    # ``compute_profile`` became required because the workload profile it took its default
    # from no longer declares a machine.
    assert len(optional) == 5
    for name in optional:
        assert declared[name]["default"] == "", name
        assert declared[name]["type"] == "string", (
            f"{name} is optional and a dropdown, which is the one shape that cannot leave "
            "its default blank and so needs a word for absence"
        )


def test_the_three_jobs_carry_exactly_these_permission_maps() -> None:
    """The three the Phase 2 criteria rest on, still asserted as an exact map each.

    Deliberately not renamed when the resolve job arrived, and deliberately not widened to
    cover it. ``phase2_criteria.py`` cites this node id as what *proves* criterion 8, and
    what that criterion rests on is these three maps -- that compiling cannot request a
    token, that the probe job holds one and no environment, and that submitting holds
    exactly three entries. The inventory of jobs is a different property and is asserted
    below, where a fourth job changes a count instead of quietly changing what a recorded
    criterion is understood to have proved.
    """
    workflow = _load()

    assert workflow["jobs"]["compile"]["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["deny-unapproved"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }
    assert workflow["jobs"]["submit"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
        # The approvals endpoint, which is how the submit job learns who released the
        # gate. Verified reachable by a token holding only this.
        "actions": "read",
    }
    assert workflow["jobs"]["submit"]["needs"] == ["compile", "deny-unapproved"]
    assert "needs" not in workflow["jobs"]["deny-unapproved"]


def test_the_workflow_declares_these_five_jobs_and_orders_them_this_way() -> None:
    # The inventory, re-armed at five when the identify job arrived. A job added to this
    # file inherits the two trust policies that pin job_workflow_ref to it, so a new one is
    # a new principal for both the admission role and the image resolver -- which is why
    # the list is exact rather than a floor, and why it is a test rather than a review
    # habit.
    #
    # IDENTIFY IS THE FIRST JOB ADDED SINCE THAT SENTENCE WAS WRITTEN, SO IT IS WORTH
    # ANSWERING DIRECTLY. It is a new principal under both trust policies in the sense the
    # comment means: the policies name this workflow, not a job within it. It cannot use
    # either, because it holds no `id-token` permission and so cannot request the token an
    # AssumeRoleWithWebIdentity call needs -- the same absence the compile job's guarantee
    # rests on, asserted for this job below on the same reasoning.
    workflow = _load()

    assert list(workflow["jobs"]) == [
        "identify",
        "resolve",
        "compile",
        "deny-unapproved",
        "submit",
    ]
    assert workflow["jobs"]["resolve"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }
    assert workflow["jobs"]["compile"]["needs"] == ["identify", "resolve"]
    assert workflow["jobs"]["resolve"]["needs"] == ["identify"]
    assert "needs" not in workflow["jobs"]["identify"]
    assert "environment" not in workflow["jobs"]["resolve"]


def test_the_job_that_turns_a_branch_into_a_commit_cannot_reach_aws_at_all() -> None:
    """Mutation: give it id-token, or let it grow an AWS step.

    It exists so that a submitter can type the only name they know their work by, which
    needs the GitHub API and nothing else. Adding it to this file made it a principal under
    both trust policies -- they pin ``job_workflow_ref`` to the workflow rather than to a
    job -- so what stops it using them is the same thing that stops the compile job: it
    cannot request an OIDC token, and a job that cannot request one cannot leak one.

    Also why it is a job rather than a step at the top of resolve. That job holds the
    token, and ``test_the_registry_answer_crosses_to_the_credential_free_job_as_an_artifact``
    refuses it any outputs at all, so a string could not have left it.
    """
    identify = _job("identify")

    for text in _strings(identify):
        normalized = text.lower().replace("_", "-")
        assert "id-token" not in normalized, f"this job must stay credential free: {text!r}"
    assert set(identify["permissions"]) == {"contents"}
    assert identify["permissions"]["contents"] == "read"
    assert "environment" not in identify
    assert set(identify["outputs"]) == {"commit_sha"}


def test_the_compile_job_cannot_request_a_token_by_any_spelling() -> None:
    # Load bearing, and stated as an absence rather than as a rule: a job that cannot
    # request an OIDC token cannot leak one, which is stronger than trusting it not to.
    # The classification it computes decides which reviewers a submission faces, so it
    # has to be computed before anything in the run can reach AWS.
    compile_job = _job("compile")

    for text in _strings(compile_job):
        normalized = text.lower().replace("_", "-")
        assert "id-token" not in normalized, f"the compile job must stay credential free: {text!r}"
    assert set(compile_job["permissions"]) == {"contents"}
    assert "environment" not in compile_job
    assert CREDENTIALS_ACTION not in set(_strings(compile_job))
    assert "configure-aws-credentials" not in str(compile_job)


def test_the_deny_probe_holds_a_token_and_deliberately_names_no_environment() -> None:
    # The absent environment key is the whole experiment. Without it GitHub mints a
    # ref-scoped subject rather than an environment-scoped one, and the trust policy
    # enumerates only the two environment-scoped subjects.
    deny = _job("deny-unapproved")

    assert deny["permissions"]["id-token"] == "write"
    assert "environment" not in deny
    assert [candidate["name"] for candidate in deny["steps"]] == [DENY_STEP]
    # No checkout: this job holds a token aimed at a production role and has no reason to
    # read the repository beside it.
    assert [candidate for candidate in deny["steps"] if "uses" in candidate] == []


def test_the_submit_job_takes_its_gate_from_needs_and_never_from_the_form() -> None:
    # GitHub is equally happy with either source. Reading it from the form would let a
    # submitter route an exception to the lead gate by typing a name into a text box.
    environment = _job("submit")["environment"]

    assert isinstance(environment, dict)
    assert list(_references(environment["name"])) == ["needs.compile.outputs.environment"]
    assert "inputs" not in environment["name"]
    assert "github.event" not in environment["name"]


def test_the_two_gate_names_are_the_ones_the_contract_and_the_trust_policy_share() -> None:
    # Nothing the runner reads spells either name: the gate is whichever one the compile
    # job computed. The names appear in the header comment only, where they document the
    # trust pin. Hardcoding one in a job would route submissions past their
    # classification, and hardcoding a third would auto-create an environment carrying no
    # protection rules at all.
    values = list(_strings(_load()))
    trust_policy_text = TRUST_POLICY_PATH.read_text(encoding="utf-8")

    for member in ApprovalEnvironment:
        assert not [text for text in values if member.value in text], member
        assert f":environment:{member.value}" in trust_policy_text


def test_every_needs_reference_names_an_output_the_job_actually_declares() -> None:
    # The class of bug a text comparison cannot see. GitHub resolves
    # `needs.compile.outputs.enviroment` to the empty string rather than failing, which
    # would send every submission to an auto-created environment carrying no protection
    # rules at all -- and then fail at AssumeRole for a reason nothing points at.
    workflow = _load()
    declared = {name: set(job.get("outputs") or {}) for name, job in workflow["jobs"].items()}
    found: list[str] = []

    for text in _strings(workflow):
        for reference in _references(text):
            if not reference.startswith("needs."):
                continue
            found.append(reference)
            _context, job_name, *rest = reference.split(".")
            assert job_name in declared, reference
            assert rest[:1] == ["outputs"], reference
            assert rest[1] in declared[job_name], reference

    assert sorted(set(found)) == [
        # Read twice in the submit job, to skip the approval read on the reviewer-less gate
        # and to tell the assembly step whether an approver is owed. Both come from here
        # rather than from `inputs`, because the class that picks the gate and the class the
        # request records have to be the one a credential-free job computed from policy. A
        # submitter who could type either would be choosing their own approval path.
        "needs.compile.outputs.approval_class",
        "needs.compile.outputs.environment",
        "needs.compile.outputs.manifest_sha256",
        "needs.compile.outputs.run_id",
        # Read by two jobs and written by neither of them. The identify job turns whatever
        # the submitter typed into a commit, and both the registry lookup and the compile
        # step take it from here rather than from `inputs`, so that a branch name is
        # resolved exactly once and the manifest and the image agree about which commit
        # they mean.
        "needs.identify.outputs.commit_sha",
    ]


def test_every_expression_names_something_that_actually_exists() -> None:
    assert unreal_context_references(WORKFLOW_PATH, declared_step_outputs=DECLARED_OUTPUTS) == []


def test_the_compile_job_publishes_exactly_the_outputs_its_tool_writes() -> None:
    # The four names are decided in tools/compile_submission.py and read here from the
    # call that writes them, so renaming one on either side fails rather than resolving
    # to the empty string on the other.
    written = _tool_step_output_names(PROJECT_ROOT / "tools" / "compile_submission.py")
    outputs = _job("compile")["outputs"]

    assert set(written) == {"run_id", "approval_class", "environment", "manifest_sha256"}
    assert set(outputs) == set(written)
    for name in written:
        assert outputs[name] == f"${{{{ steps.compile.outputs.{name} }}}}"
    assert set(DECLARED_OUTPUTS["compile"]) == set(written)


def test_both_checkouts_pin_the_commit_the_run_was_dispatched_at() -> None:
    # The hash recomputed after approval is a tripwire only because both sides are
    # pinned. Against a floating ref the two could differ merely because the branch
    # advanced, and a tripwire that fires for an ordinary reason gets routed around.
    for job_name in ("compile", "submit"):
        checkout = step(_job(job_name), "Check out the platform")

        assert checkout["uses"] == CHECKOUT_ACTION
        assert checkout["with"]["ref"] == "${{ github.sha }}"
        assert checkout["with"]["persist-credentials"] is False
        assert "repository" not in checkout["with"]


def test_every_action_is_pinned_to_a_commit() -> None:
    used = [
        candidate["uses"]
        for job in _load()["jobs"].values()
        for candidate in job["steps"]
        if "uses" in candidate
    ]

    assert set(used) == {CHECKOUT_ACTION, CREDENTIALS_ACTION, UPLOAD_ACTION, DOWNLOAD_ACTION}
    for reference in used:
        assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", reference), reference


def test_the_credentials_action_is_the_commit_the_image_build_already_reviewed() -> None:
    # Both workflows assume a role in the same account through the same action. Two
    # different pinned commits would be two supply chains to review.
    build_workflow = load_workflow(BUILD_WORKFLOW_PATH)
    reviewed = {
        candidate["uses"]
        for job in build_workflow["jobs"].values()
        for candidate in job["steps"]
        if str(candidate.get("uses", "")).startswith("aws-actions/configure-aws-credentials@")
    }

    assert reviewed == {CREDENTIALS_ACTION}
    assert step(_job("submit"), CREDENTIALS_STEP)["uses"] == CREDENTIALS_ACTION


def test_the_submit_job_assumes_the_admission_role_and_masks_the_account_id() -> None:
    credentials = step(_job("submit"), CREDENTIALS_STEP)

    assert credentials["id"] == "credentials"
    assert credentials["with"] == {
        "role-to-assume": "${{ vars.AWS_ADMISSION_ROLE_ARN }}",
        "aws-region": "${{ vars.AWS_REGION }}",
        "role-duration-seconds": 900,
        "mask-aws-account-id": True,
    }


def test_the_manifest_is_recomputed_before_the_job_holds_any_credentials() -> None:
    names = [candidate.get("name") for candidate in _job("submit")["steps"]]
    verify = step(_job("submit"), VERIFY_STEP)

    assert names.index(VERIFY_STEP) < names.index(CREDENTIALS_STEP)
    assert names.index(APPROVAL_STEP) < names.index(CREDENTIALS_STEP)
    assert verify["env"] == {"APPROVED_SHA256": "${{ needs.compile.outputs.manifest_sha256 }}"}
    assert "verify_approved_manifest.py" in verify["run"]
    assert '--approved-sha256 "${APPROVED_SHA256}"' in verify["run"]


def test_the_compiled_submission_crosses_the_gate_as_an_artifact() -> None:
    upload = step(_job("compile"), "Upload the compiled submission")
    download = step(_job("submit"), "Download the compiled submission")

    assert upload["with"]["name"] == download["with"]["name"]
    assert upload["with"]["if-no-files-found"] == "error"
    verify = step(_job("submit"), VERIFY_STEP)
    assert download["with"]["path"] == "${{ runner.temp }}/compiled-submission"
    assert "${RUNNER_TEMP}/compiled-submission/compiled-submission.json" in verify["run"]


def test_nothing_lets_the_submit_job_run_after_a_gate_has_failed() -> None:
    # `needs` alone only orders the jobs. A single `if: always()` would keep both
    # dependencies and still submit after a refused compile or a probe that found the
    # admission role assumable without an approval.
    workflow = _load()

    for name, job in workflow["jobs"].items():
        assert "if" not in job, name
    assert "continue-on-error" not in str(workflow)


def test_the_tools_the_run_bodies_reach_for_exist_on_disk() -> None:
    referenced = sorted(
        {match for _name, script in _run_bodies() for match in TOOL_PATH_PATTERN.findall(script)}
    )

    assert referenced == [
        "tools/compile_submission.py",
        RESOLVER_TOOL,
        DENIALS_TOOL,
        "tools/verify_approved_manifest.py",
        BATCH_DENIALS_TOOL,
        WANDB_PREFLIGHT_TOOL,
    ]
    # And the one that reads the secret is not among them, which is the whole shape of the
    # preflight rather than an omission. See its own tests below.
    assert WANDB_CREDENTIAL_TOOL not in referenced
    for relative in referenced:
        assert (PROJECT_ROOT / relative).is_file(), relative


def test_every_python_invocation_comes_from_the_locked_environment() -> None:
    for name, script in _run_bodies():
        if "python" in script:
            assert "uv run --frozen python" in script, name


def test_the_workflow_never_embeds_an_account_identifier_or_a_registry_host() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert not re.search(r"(?<!\d)\d{12}(?!\d)", text)
    assert not re.search(r"\d\.dkr\.ecr\.", text)
    assert "get-caller-identity" not in text
    # All four ARNs are composed from the assumed identity, which is why the account id
    # never has to be written down anywhere in this repository.
    assert text.count("${ADMISSION_ACCOUNT_ID}") == 4
    assert "steps.credentials.outputs.aws-account-id" in text


def test_no_run_body_interpolates_a_github_expression() -> None:
    offenders = [name for name, script in _run_bodies() if "${{" in script]

    assert offenders == [], f"run bodies must read expressions through env: {offenders}"


def test_every_run_body_enables_strict_bash() -> None:
    bodies = list(_run_bodies())

    # Twenty since the W&B preflight arrived. Counted rather than sampled, so a body added
    # without the strict line fails here instead of running past its first error.
    assert len(bodies) == 20
    for name, script in bodies:
        assert script.startswith("set -euo pipefail\n"), name


def _aws_reaching_calls() -> list[tuple[str, tuple[str, ...]]]:
    """Everything this workflow makes AWS answer, in the order the runner reaches it.

    A run body that calls the CLI directly contributes the call it makes. Each denial
    matrix contributes the actions it attempts and the image resolver contributes its two
    reads, because those are made by a tool rather than by a shell and a reader of this file
    would otherwise see the submit job reach AWS twice when it reaches it a dozen times.
    Each matrix's own ``sts:get-caller-identity`` is left out: it requires no permission and
    cannot be denied by a policy, so it is not part of the surface this enumeration is
    about.

    The W&B preflight contributes nothing, and that is a property rather than an oversight.
    It reads a verdict published by nightly.yml through the ``actions: read`` this job
    already holds for the approvals endpoint, so it reaches no AWS API at all.
    """
    calls: list[tuple[str, tuple[str, ...]]] = []
    for name, script in _run_bodies():
        if RESOLVER_TOOL in script:
            calls.extend((name, ("image-resolver", action)) for action in RESOLVER_ECR_ACTIONS)
        if DENIALS_TOOL in script:
            calls.extend((name, ("denial-probe", action)) for action in ADMISSION_DENIED_ACTIONS)
        if BATCH_DENIALS_TOOL in script:
            calls.extend(
                (name, ("denial-probe", action)) for action in ADMISSION_BATCH_DENIED_ACTIONS
            )
        calls.extend((name, tuple(command[:3])) for command in aws_commands(script))
    return calls


def test_the_workflow_makes_exactly_these_aws_calls_in_exactly_this_order() -> None:
    # Enumerated so that a new call has to be argued for in review rather than appearing.
    # Both sets of refused attempts and the resolver's two reads are read out of the tools
    # that define them, so adding a probe or renaming an action changes this list rather
    # than slipping past it.
    assert _aws_reaching_calls() == [
        *[
            (f"resolve:{RESOLVE_STEP}", ("image-resolver", action))
            for action in RESOLVER_ECR_ACTIONS
        ],
        (
            f"deny-unapproved:{DENY_STEP}",
            ("aws", "sts", "assume-role-with-web-identity"),
        ),
        *[
            (f"submit:{DENIALS_STEP}", ("denial-probe", action))
            for action in ADMISSION_DENIED_ACTIONS
        ],
        *[
            (f"submit:{BATCH_DENIALS_STEP}", ("denial-probe", action))
            for action in ADMISSION_BATCH_DENIED_ACTIONS
        ],
        # The W&B preflight sits between the last of those and the first of these, and
        # appears in neither, because it makes no AWS call. That is the decision this list
        # records: the check the preflight acts on is made once a night by the one
        # GitHub-facing role holding `secretsmanager:GetSecretValue`, and the submit path
        # reads its answer rather than the key. infra/iam/admission-role.yaml argues it.
        (f"submit:{START_STEP}", ("aws", "stepfunctions", "start-execution")),
        (f"submit:{WAIT_STEP}", ("aws", "stepfunctions", "describe-execution")),
    ]
    spoken = {word for _name, call in _aws_reaching_calls() for word in call}
    assert not any("secretsmanager" in word for word in spoken), (
        "the submit path reads the W&B verdict, never the secret behind it"
    )


def test_the_only_aws_a_dispatch_reaches_before_an_approval_is_a_read_and_a_refusal() -> None:
    """Mutation: give the resolve job a third call, or point it at another service.

    The invariant this file maintains is not that an unapproved dispatch never reaches AWS
    -- ``deny-unapproved`` mints a token and calls STS on every one. It is that an
    unapproved dispatch obtains nothing that can start, submit or write. So what the two
    jobs ahead of the gate reach is enumerated here as a whole, rather than only the
    resolve job's half, because the property is about the pair.
    """
    before_the_gate = [
        call for name, call in _aws_reaching_calls() if not name.startswith("submit:")
    ]

    assert before_the_gate == [
        *[("image-resolver", action) for action in RESOLVER_ECR_ACTIONS],
        ("aws", "sts", "assume-role-with-web-identity"),
    ]
    assert all(action.startswith("ecr:Describe") for _tool, action in before_the_gate[:-1])


def test_both_denial_matrices_are_attempted_before_the_state_machine_is_started() -> None:
    # The ordering is the property, so it is computed from the step list rather than
    # assumed of it. Attempted after StartExecution a matrix would report on a role that
    # had already been used; attempted before the credentials step it would run under no
    # session at all. What they have to sit between is the moment the session is issued
    # and the moment it is spent.
    names = [candidate.get("name") for candidate in _job("submit")["steps"]]

    assert names.index(CREDENTIALS_STEP) < names.index(DENIALS_STEP) < names.index(START_STEP)
    assert names.index(DENIALS_STEP) < names.index(BATCH_DENIALS_STEP) < names.index(START_STEP)
    # And nothing at all reaches AWS in between. The W&B preflight sits there and makes no
    # call, so the two matrices remain the last statement about this session before it is
    # spent -- and a submission refused for a bad credential is still a dispatch that made
    # them, which is the property that matters. Restricted to this job, because what an
    # earlier job reached under a different role says nothing about the session in hand.
    reaching = [name for name, _call in _aws_reaching_calls() if name.startswith("submit:")]
    assert reaching.index(f"submit:{START_STEP}") == len(reaching) - 2
    assert set(reaching[:-2]) == {f"submit:{DENIALS_STEP}", f"submit:{BATCH_DENIALS_STEP}"}
    assert f"submit:{WANDB_PREFLIGHT_STEP}" not in reaching


def test_the_write_probe_names_the_lineage_bucket_the_template_deploys() -> None:
    # A bucket that is not there is answered NoSuchBucket before anybody is authorized,
    # so a name invented here would report an absent bucket as a refusal by this role --
    # and the write probe is the entry in the matrix that matters most.
    declared = step(_job("submit"), DENIALS_STEP)["env"]["LINEAGE_BUCKET"]
    template = load_workflow(LINEAGE_TEMPLATE_PATH)

    assert declared == template["Resources"]["LineageBucket"]["Properties"]["BucketName"]
    assert declared == LINEAGE_BUCKET


def test_the_denial_matrix_reaches_the_proof_bundle() -> None:
    # Phase 2's counterpart to Phase 1's publisher denial matrix, which is uploaded from
    # the build workflow for the same reason: a refusal nobody kept is a claim.
    upload = step(_job("submit"), DENIALS_UPLOAD_STEP)
    names = [candidate.get("name") for candidate in _job("submit")["steps"]]

    assert upload["uses"] == UPLOAD_ACTION
    assert upload["with"]["name"] == "admission-denials"
    assert upload["with"]["if-no-files-found"] == "error"
    assert names.index(DENIALS_STEP) < names.index(DENIALS_UPLOAD_STEP)
    # No `if:`, because the tool writes the record only when every action was refused.
    # An upload that ran anyway would fail on a missing file and bury the finding under
    # a second, unrelated failure.
    assert "if" not in upload


def test_the_approver_context_survives_the_run_that_showed_it() -> None:
    # A step summary is rendered in the run page and exposed by no REST endpoint, and the
    # public page hides it behind sign-in. Without this upload the only check about what a
    # reviewer was actually shown could be answered only by somebody describing it from
    # memory, which is the kind of evidence this phase exists not to accept.
    #
    # The upload has to come from the same file the summary was written from, or the
    # artifact becomes a re-render that can drift from what the human saw.
    compile_job = _job("compile")
    publish = step(compile_job, "Publish the approver context")
    upload = step(compile_job, "Upload the approver context")
    names = [candidate.get("name") for candidate in compile_job["steps"]]

    assert upload["uses"] == UPLOAD_ACTION
    assert upload["with"]["name"] == "approver-context"
    assert upload["with"]["if-no-files-found"] == "error"
    assert "approver-context.md" in upload["with"]["path"]
    assert "approver-context.md" in publish["run"]
    assert names.index("Publish the approver context") < names.index("Upload the approver context")


def test_the_file_documents_the_three_things_a_reader_will_otherwise_undo() -> None:
    # Each of these is a decision that looks like a mistake until the reason is read: a
    # probe job that belongs in a file of its own, an environment name that could be
    # taken from the form, and an AWS error treated as success.
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    deny_rationale = text.split("  deny-unapproved:", 1)[0].rsplit("\n\n", 1)[-1]
    already_exists = step(_job("submit"), START_STEP)

    assert "job_workflow_ref" in deny_rationale
    assert "environment" in deny_rationale
    assert "refs/heads/main" in deny_rationale
    assert "ExecutionAlreadyExists" in text
    assert "ninety days" in text
    assert "idempotent" in text
    assert "ExecutionAlreadyExists" in already_exists["run"]


def _comment_block_above(step_name: str) -> str:
    """The comment paragraph a step is introduced by, as one line of prose.

    Unwrapped, because where a sentence happens to break is not a property worth pinning
    and a phrase that spans two lines is still a phrase the reader met.
    """
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    block = text.split(f"      - name: {step_name}", 1)[0].rsplit("\n\n", 1)[-1]
    return " ".join(line.strip().removeprefix("#").strip() for line in block.splitlines())


def _comment_block_above_job(job_id: str) -> str:
    """The same, for a job rather than a step, because two jobs here argue for themselves."""
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    block = text.split(f"\n  {job_id}:\n", 1)[0].rsplit("\n\n", 1)[-1]
    return " ".join(line.strip().removeprefix("#").strip() for line in block.splitlines())


def test_the_probe_that_cannot_be_made_inert_says_so_where_it_runs() -> None:
    # The write probe leaves an object behind if the role is ever permitted to write one.
    # A cost that is only written down in the tool is a cost the person reading this
    # workflow does not know they are accepting. The placement is here for the same
    # reason: it looks arbitrary until the reason is read, and moving it after
    # StartExecution would cost nothing visible.
    rationale = _comment_block_above(DENIALS_STEP)

    assert "denial-probe/" in rationale
    assert "--if-none-match" in rationale
    assert "no default retention rule" in rationale
    assert "It can be deleted" in rationale
    assert "credentials actually in hand" in rationale
    assert "template" in rationale


def test_the_upload_names_the_phase_one_matrix_it_is_the_counterpart_of() -> None:
    rationale = _comment_block_above(DENIALS_UPLOAD_STEP)

    assert "publisher denial matrix" in rationale
    assert "Phase 1" in rationale


# --------------------------------------------------------------------------------------
# The job that reads which image the declared commit published
# --------------------------------------------------------------------------------------


def test_the_resolve_job_assumes_the_read_only_image_role_through_the_reviewed_action() -> None:
    """Mutation: name the admission role here, which is already a repository variable.

    The two ARNs are one expression apart and only one of them is safe before an approval.
    The role this names may describe images and their scan findings and nothing else, which
    ``tests/test_phase5_infrastructure.py`` asserts as an exact set rather than a superset
    -- a trust policy cannot tell one job in this file from another, so whatever that role
    holds, every job here can assume.
    """
    resolve = _job("resolve")
    credentials = step(resolve, RESOLVE_CREDENTIALS_STEP)

    assert credentials["uses"] == CREDENTIALS_ACTION
    assert credentials["with"]["role-to-assume"] == "${{ vars.AWS_IMAGE_RESOLVER_ROLE_ARN }}"
    assert credentials["with"]["aws-region"] == "${{ vars.AWS_REGION }}"
    assert credentials["with"]["mask-aws-account-id"] is True
    assumed = {
        reference
        for text in _strings(resolve)
        for reference in _references(text)
        if reference.startswith("vars.")
    }
    assert assumed == {"vars.AWS_IMAGE_RESOLVER_ROLE_ARN", "vars.AWS_REGION"}


def test_the_resolve_job_argues_for_the_credential_it_holds_where_it_holds_it() -> None:
    """The comment is the deliverable, and it points at the template rather than repeating it.

    A credentialed job ahead of the approval gate is the thing in this file a reader is
    most likely to try to undo, and the argument for it is long enough that a copy here
    would be a second copy going stale. ``infra/iam/image-resolver-role.yaml`` carries it
    in full, beside the two actions it is an argument about.
    """
    rationale = _comment_block_above_job("resolve")

    assert "infra/iam/image-resolver-role.yaml" in rationale
    assert "deny-unapproved" in rationale
    assert "admission" in rationale
    assert "starts nothing" in rationale
    assert "tools/compile_submission.py" in rationale
    assert "fails closed" in rationale


def test_the_resolve_step_is_told_the_registry_the_repository_and_the_commit(
    tmp_path: Path,
) -> None:
    # Everything the tool needs arrives through the environment, and the ECR repository is
    # not among them: the tool reads that out of the registry for the reason the deleted
    # provenance writer did, which is that a caller-supplied repository name is a
    # caller-supplied choice of whose images a submission is resolved against.
    resolve_step = step(_job("resolve"), RESOLVE_STEP)
    stub_bin = tmp_path / "bin"
    recorded = tmp_path / "argv.txt"
    write_stub(stub_bin, "uv", f'printf "%s\\n" "$@" > "{recorded}"\nexit 0\n')

    result = run_step_script(
        resolve_step["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "RESEARCH_REPOSITORY": "OLMo-core",
            "COMMIT_SHA": "a" * 40,
            "RESOLVE_AWS_REGION": "us-east-1",
        },
        stub_bin=stub_bin,
    )

    assert result.returncode == 0, result.stderr
    passed = dict(itertools.pairwise(recorded.read_text(encoding="utf-8").splitlines()))
    assert passed["--registry"] == "config/repositories.yaml"
    assert passed["--repository"] == "OLMo-core"
    assert passed["--commit-sha"] == "a" * 40
    assert passed["--aws-region"] == "us-east-1"
    assert passed["--output"] == str(tmp_path / "published-image.json")
    assert "--ecr-repository" not in passed


def test_the_registry_answer_crosses_to_the_credential_free_job_as_an_artifact() -> None:
    """Mutation: make it a job output, or merge the two jobs.

    The compile job cannot request an OIDC token by any spelling, and the classification it
    computes is worth something only because of that -- so the job that can request one has
    to be a different job, and what passes between them has to be a document rather than a
    credential. An artifact is also what lets a reader of a finished run see exactly what
    the registry answered, which a string in the expression context would not.
    """
    upload = step(_job("resolve"), RESOLVE_UPLOAD_STEP)
    download = step(_job("compile"), RESOLVE_DOWNLOAD_STEP)
    names = [candidate.get("name") for candidate in _job("compile")["steps"]]

    assert upload["uses"] == UPLOAD_ACTION
    assert download["uses"] == DOWNLOAD_ACTION
    assert upload["with"]["name"] == download["with"]["name"] == "published-image"
    assert upload["with"]["if-no-files-found"] == "error"
    assert "outputs" not in _job("resolve")
    assert names.index(RESOLVE_DOWNLOAD_STEP) < names.index(COMPILE_STEP)


def test_the_compile_step_is_handed_the_file_the_resolve_job_wrote(tmp_path: Path) -> None:
    # The seam between the two jobs, read off both sides: the artifact lands where the
    # download step puts it, and that is the path the compile step passes on.
    download = step(_job("compile"), RESOLVE_DOWNLOAD_STEP)
    result, arguments = _run_compile_step(tmp_path, uv_body="exit 0\n")

    assert result.returncode == 0, result.stderr
    passed = dict(itertools.pairwise(arguments))
    assert download["with"]["path"] == "${{ runner.temp }}/published-image"
    assert passed["--published-images"] == str(
        tmp_path / "published-image" / "published-image.json"
    )


def test_the_compile_job_gained_no_way_to_reach_aws_by_gaining_an_upstream_that_can() -> None:
    """Mutation: assume the resolver role in the compile job and skip the artifact.

    It would work, and it would end the only claim this file makes that does not rest on
    trusting a job to behave: a job with no ``id-token`` permission cannot request a token,
    so it cannot leak one and cannot reach AWS whatever it is asked to do. ``needs`` orders
    the two jobs and transfers nothing.
    """
    compile_job = _job("compile")

    for text in _strings(compile_job):
        normalized = text.lower().replace("_", "-")
        assert "id-token" not in normalized, text
    assert set(compile_job["permissions"]) == {"contents"}
    assert CREDENTIALS_ACTION not in set(_strings(compile_job))
    assert "AWS_IMAGE_RESOLVER_ROLE_ARN" not in str(compile_job)


def test_the_digest_field_is_offered_as_an_override_and_says_what_leaving_it_blank_does() -> None:
    """Mutation: leave the description saying it is the digest of the published image.

    That description asked for the hardest field on the form and gave no hint that it had
    stopped being required, so a submitter who read it would go and transcribe
    seventy-one characters the workflow was about to derive for them.

    **THE WORD "ADVANCED" WAS PINNED HERE AND IS NOT ANY MORE, AND THE PROPERTY IT STOOD FOR
    IS.** It was one way of saying the field is not for ordinary use, and it was carrying that
    alone. Then the form was rewritten to one clause a field, every description read as a
    plain sentence, and "Advanced — leave blank" was the ugliest line on the page: a marker
    word, a dash, and then the instruction that already said the same thing. What a reader
    needs is that they can skip it and where the image comes from instead, so those two are
    what is asserted. A description satisfying both cannot leave somebody transcribing a
    digest, which is the defect this test was written for.
    """
    declared = _load()["on"]["workflow_dispatch"]["inputs"]["image_digest"]

    assert declared["required"] is False
    assert declared["default"] == ""
    described = declared["description"].lower()
    # Skippable, and what fills it if you skip it. Both, because either alone leaves the
    # reader either transcribing a digest or wondering what runs instead.
    assert "leave blank" in described
    assert "commit" in described


# --------------------------------------------------------------------------------------
# What Phase 3 added, and the one thing it was not allowed to add
# --------------------------------------------------------------------------------------


def test_the_submit_job_gained_no_aws_capability_when_phase_three_arrived() -> None:
    """Mutation: add anything to the submit job's permission map.

    Phase 3 gives the account a queue to submit to and jobs to terminate, and the whole
    architecture rests on the GitHub-facing side reaching none of it. The submit job's map
    is therefore a fixed set rather than a floor, and it is the same three entries Phase 2
    shipped: ``contents: read`` to check out, ``id-token: write`` to mint the OIDC token
    the admission role is assumed with, and ``actions: read`` for the approvals endpoint.

    ``id-token: write`` is the one that matters, and it is not an AWS capability by itself:
    what a token can reach is decided by the trust policies that accept it, and the only
    role that accepts one from this file is the admission role. So the way this job could
    gain AWS reach is a wider role rather than a wider permission map -- which is what the
    two denial matrices, attempted from the real session, are for.
    """
    submit = _job("submit")

    assert submit["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "actions": "read",
    }
    # Nothing here may name a second role, and every ARN is composed from the assumed
    # identity, so a new AWS target would have to arrive as a new repository variable.
    assumed = {
        text
        for text in _strings(submit)
        for reference in _references(text)
        if reference.startswith("vars.")
        for text in [reference]
    }
    assert assumed == {"vars.AWS_ADMISSION_ROLE_ARN", "vars.AWS_REGION"}


def test_the_batch_matrix_attempts_every_action_phase_three_makes_meaningful() -> None:
    """Reads the workflow and the matrix. Mutation: drop an action from either.

    ``batch:SubmitJob`` was probed in Phase 1 against a queue that did not exist. The other
    three were not probed at all, because until there was a queue, a job definition and
    jobs to describe they were hypothetical. The step runs the tool for the admission role,
    so which actions it attempts is decided in ``batch_denials`` rather than here.
    """
    script = step(_job("submit"), BATCH_DENIALS_STEP)["run"]

    assert BATCH_DENIALS_TOOL in script
    assert "--role admission" in script
    assert set(ADMISSION_BATCH_DENIED_ACTIONS) == {
        "batch:SubmitJob",
        "batch:TerminateJob",
        "batch:RegisterJobDefinition",
        "batch:DescribeJobs",
    }


def test_the_batch_matrix_is_aimed_at_the_registered_repository_and_not_a_placeholder() -> None:
    """Reads the workflow and the registry. Mutation: hardcode a repository name.

    The admission matrix carries no image probe, so this value is validated and never used
    -- which is exactly the situation in which somebody writes down whatever satisfies the
    validator. A probe aimed at something that is not there is the mistake this repository
    has now made in three phases, and a placeholder here would be the seed of the next one.
    """
    resolved = step(_job("submit"), REGISTRY_STEP)
    attempt = step(_job("submit"), BATCH_DENIALS_STEP)
    names = [candidate.get("name") for candidate in _job("submit")["steps"]]
    registered = {
        entry.ecr_repository
        for entry in load_registry(PROJECT_ROOT / "config" / "repositories.yaml").repositories
    }

    assert list(_references(attempt["env"]["ECR_REPOSITORY"])) == [
        "steps.registry.outputs.ecr_repository"
    ]
    assert resolved["id"] == "registry"
    assert "config/repositories.yaml" in resolved["run"]
    assert names.index(REGISTRY_STEP) < names.index(BATCH_DENIALS_STEP)
    # And no registered name appears anywhere in the file, so the value can only have come
    # from the lookup.
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert registered
    for name in registered:
        assert name not in text, name


def test_the_batch_denial_matrix_reaches_the_proof_bundle() -> None:
    upload = step(_job("submit"), BATCH_DENIALS_UPLOAD_STEP)
    names = [candidate.get("name") for candidate in _job("submit")["steps"]]

    assert upload["uses"] == UPLOAD_ACTION
    assert upload["with"]["name"] == "batch-denials"
    assert upload["with"]["if-no-files-found"] == "error"
    assert names.index(BATCH_DENIALS_STEP) < names.index(BATCH_DENIALS_UPLOAD_STEP)
    # No `if:`, for the reason the Phase 2 upload has none: the tool writes the record only
    # when every action was refused, so a run that failed above has nothing to upload.
    assert "if" not in upload


# --------------------------------------------------------------------------------------
# The W&B preflight, which is the last read before anything is provisioned
# --------------------------------------------------------------------------------------


def wandb_tool() -> Any:
    """The tool's own module, loaded without registering it under a name anything imports.

    ``tests/test_workflow_tool_arguments.py`` records what registering a freshly built
    module under a tool's own name cost, so this follows the same discipline as
    ``tests/test_wandb_credential_verifier.py`` and leaves ``sys.modules`` alone.
    """
    specification = importlib.util.spec_from_file_location(
        "_submit_run_wandb_tool", PROJECT_ROOT / WANDB_CREDENTIAL_TOOL
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_the_preflight_is_the_last_read_before_the_one_call_that_provisions() -> None:
    """Mutation: move it into the compile job, or below StartExecution.

    Both readings are defensible until the cost is named, which is why the placement is
    asserted rather than left to review. Earlier than the gate, every submission compiling
    refuses -- an unregistered dataset, a commit that published no image -- would first pay
    the verdict lookup for nothing. Later than ``StartExecution``, the state machine has
    already registered a job definition and put the run on a queue, so the check happens
    with an instance coming up.

    The step also has no ``if:``, and that matters here more than it looks. A preflight that
    could be skipped by a condition somebody added later is a preflight that stops running
    on exactly the dispatches nobody is watching.
    """
    submit = _job("submit")
    names = [candidate.get("name") for candidate in submit["steps"]]
    preflight = step(submit, WANDB_PREFLIGHT_STEP)

    assert names.index(CREDENTIALS_STEP) < names.index(WANDB_PREFLIGHT_STEP)
    assert names.index(BATCH_DENIALS_STEP) < names.index(WANDB_PREFLIGHT_STEP)
    assert names.index(WANDB_PREFLIGHT_STEP) == names.index(START_STEP) - 1, (
        "nothing may sit between the preflight and the call that provisions, or the "
        "preflight stops being the last thing a refused submission costs"
    )
    assert "if" not in preflight
    # Nothing from `vars.` and nothing about a region, because there is no AWS call to aim
    # at one. The token is the job's own, which is what `actions: read` is attached to.
    assert preflight["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "PREFLIGHT_REPOSITORY": "${{ github.repository }}",
    }


def test_the_preflight_argues_its_position_and_its_credential_where_the_step_is() -> None:
    """The comment is the deliverable, because both decisions are invisible in the code.

    A reader who moves this step earlier makes the whole path slower for no benefit and
    nothing fails; a reader who moves it later makes it useless and nothing fails either.
    And a reader who decides the obvious repair is one ``secretsmanager:GetSecretValue`` on
    the admission role would be undoing the more important of the two decisions, on a role
    whose entire documented property is that it holds nothing but ``states:StartExecution``.
    So both arguments sit beside the step rather than in a commit message.
    """
    rationale = _comment_block_above(WANDB_PREFLIGHT_STEP)

    assert "ProcessGroup is not registered" in rationale
    assert "CommError" in rationale
    assert "compile job" in rationale
    assert "provisions" in rationale
    # The grant this step declines, named, so the reader meets the argument rather than the
    # idea. Both rejected designs are named too: the widening and the fifth OIDC role.
    assert "secretsmanager:GetSecretValue" in rationale
    assert "admission role" in rationale
    assert "OIDC role" in rationale
    assert "run-approval-automatic" in rationale
    # And what it costs, because a fail-open check whose blind spot is undocumented is a
    # check people believe more than it deserves.
    assert "rotation" in rationale


def test_the_preflight_holds_no_aws_identity_and_needs_no_new_permission() -> None:
    """Mutation: give the step a role, a region, or the tool that reads the secret.

    This is the decision the whole track came down to. Exactly three principals hold
    ``secretsmanager:GetSecretValue`` on the W&B secret and none of them is reachable from
    this file, which is deliberate: ``infra/iam/admission-role.yaml`` argues it beside the
    grant it declines. The preflight therefore has to work from what the submit job already
    has, and what it already has is ``actions: read`` for the approvals endpoint.

    So the assertion is about absence. No AWS call, no ``vars.`` reference, no second role,
    and no widening of the permission map -- which
    ``test_the_submit_job_gained_no_aws_capability_when_phase_three_arrived`` pins as a
    fixed set rather than a floor.
    """
    submit = _job("submit")
    script = step(submit, WANDB_PREFLIGHT_STEP)["run"]

    assert aws_commands(script) == []
    assert "${{" not in script
    assert "vars." not in json.dumps(step(submit, WANDB_PREFLIGHT_STEP))
    assert WANDB_PREFLIGHT_TOOL in script
    assert WANDB_CREDENTIAL_TOOL not in script
    assert submit["permissions"]["actions"] == "read"


def test_the_verdict_the_preflight_reads_is_the_one_the_nightly_publishes() -> None:
    """Reads both workflows and the module they share. Mutation: rename either side.

    Two files and one artifact, connected by a name written in three places and by nothing
    CloudFormation or GitHub will check. A rename on one side alone does not fail: the
    preflight finds no artifact, reports that nothing was established, and lets every
    submission through for ever -- which is the state this change exists to end, restored
    silently. So the names come out of ``edullm_platform.wandb_preflight`` and both
    workflows are held to them here.
    """
    published = step(load_workflow(NIGHTLY_PATH)["jobs"][NIGHTLY_WANDB_JOB], NIGHTLY_UPLOAD_STEP)
    preflight = step(_job("submit"), WANDB_PREFLIGHT_STEP)

    assert published["with"]["name"] == NIGHTLY_VERDICT_ARTIFACT
    assert published["with"]["path"].endswith(NIGHTLY_VERDICT_FILENAME)
    # if: always(), or the refusal -- the one verdict this exists to act on -- is the only
    # one that never gets published, because the step above it exits 1 on exactly that.
    assert published["if"] == "always()"
    assert NIGHTLY_WORKFLOW == NIGHTLY_PATH.name
    # And the preflight names neither, because both come out of the module rather than out
    # of the run body. A literal here would be a fourth place to keep in step.
    assert NIGHTLY_VERDICT_ARTIFACT not in preflight["run"]


def test_the_check_behind_the_verdict_reads_the_key_the_containers_are_given() -> None:
    """Reads the nightly, the tool and the container shapes.

    The preflight is only worth what the check behind it is worth, and a check aimed at a
    different secret from the one ECS injects would pass while every container failed --
    worse than no check, because it moves the diagnosis further away. The nightly passes no
    ``--secret-name``, so the tool default is what is used, and this holds that default
    against every secret the job definitions inject.

    The injected names carry the six-character suffix Secrets Manager assigns, because
    ``ValueFrom`` is a lookup rather than a pattern, so the comparison is a prefix.
    """
    default = wandb_tool().SECRET_NAME
    injected = {
        secret for shape in CONTAINER_SHAPES.values() for _variable, secret in shape.secrets
    }
    nightly = step(load_workflow(NIGHTLY_PATH)["jobs"][NIGHTLY_WANDB_JOB], NIGHTLY_CHECK_STEP)

    assert injected, "no container shape injects a secret, so this test is measuring nothing"
    for secret in sorted(injected):
        assert secret.startswith(default), (
            f"the check reports on {default} and a container is given {secret}, so the "
            "verdict and the container are about two different values"
        )
    assert WANDB_CREDENTIAL_TOOL in nightly["run"]
    assert "--secret-name" not in nightly["run"]


def test_the_entity_the_verdict_is_about_is_the_one_the_containers_are_told() -> None:
    """Reads the nightly, the tool and the submit request. Mutation: pass --expect-entity.

    The nine failures were not a missing key. The log said a key was configured and W&B
    still refused it, which is what a key belonging to another entity looks like -- so the
    check that matters is the entity W&B resolves the key to, and it has to be the entity
    the container is told to log into. The tool defaults to ``execution.WANDB_ENTITY`` and
    the nightly passes no override, so there is one answer rather than two.
    """
    script = step(load_workflow(NIGHTLY_PATH)["jobs"][NIGHTLY_WANDB_JOB], NIGHTLY_CHECK_STEP)["run"]
    told = {
        entry["Name"]: entry["Value"]
        for entry in batch_submit_request(
            manifest=load_representative_manifest("gpu-routine.yaml"),
            target=ExecutionTarget(
                compute_profile="gpu-1xa10g",
                region="us-east-1",
                job_queue_arn=f"arn:aws:batch:us-east-1:{EXAMPLE_ACCOUNT_ID}:job-queue/q",
                job_definition_arn=(
                    f"arn:aws:batch:us-east-1:{EXAMPLE_ACCOUNT_ID}:job-definition/d"
                ),
                execution_role_arn=f"arn:aws:iam::{EXAMPLE_ACCOUNT_ID}:role/e",
                workload_role_arn=f"arn:aws:iam::{EXAMPLE_ACCOUNT_ID}:role/w",
                log_group="/aws/batch/g",
            ),
            run_id=RUN_ID,
            job_definition="d",
        )["ContainerOverrides"]["Environment"]
    }

    assert told["WANDB_ENTITY"] == WANDB_ENTITY
    assert "--expect-entity" not in script


def _run_preflight(
    tmp_path: Path,
    *,
    uv_body: str,
) -> tuple[subprocess.CompletedProcess[str], str]:
    summary = tmp_path / "summary.md"
    summary.touch()
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "uv", uv_body)
    result = run_step_script(
        step(_job("submit"), WANDB_PREFLIGHT_STEP)["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_STEP_SUMMARY": str(summary),
            "GH_TOKEN": "not-a-token",
            "PREFLIGHT_REPOSITORY": PLATFORM_REPOSITORY,
        },
        stub_bin=stub_bin,
    )
    return result, summary.read_text(encoding="utf-8")


def _decision(outcome: str, reason: str, **rest: Any) -> str:
    """What tools/verify_wandb_preflight.py prints, in the shape it prints it."""
    return json.dumps({"outcome": outcome, "reason": reason, "sentence": "...", **rest})


def test_a_verdict_that_accepted_the_key_lets_the_submission_through(tmp_path: Path) -> None:
    result, summary = _run_preflight(
        tmp_path,
        uv_body=(
            f"cat <<'JSON'\n{_decision('proceed', 'wandb_credential_accepted')}\nJSON\nexit 0\n"
        ),
    )

    assert result.returncode == 0, result.stderr
    assert "wandb_credential_accepted" in result.stdout
    # An accepted key earns no summary block. The step that says where the run went writes
    # the summary, and a passing check that shouted would train people to skip reading it.
    assert summary == ""


def test_a_verdict_that_refused_the_key_stops_the_run_before_anything_is_allocated(
    tmp_path: Path,
) -> None:
    """Mutation: warn and continue, which is what every other reading of this did.

    Continuing is what the platform already does, and it costs a GPU allocation, a run, and
    a stack trace that names torch rather than a login. The refusal has to be a refusal, and
    it has to say that re-running will not help -- the key is set from a laptop, so a
    submitter who reads this and presses the button again learns nothing twice.
    """
    refusal = _decision(
        "refuse",
        "wandb_credential_would_be_refused",
        published={"looks_wrong": ["W&B does not recognise this key"]},
    )
    result, summary = _run_preflight(
        tmp_path, uv_body=f"cat <<'JSON'\n{refusal}\nJSON\nexit 1\n"
    )

    assert result.returncode == 1
    assert "wandb_credential_would_be_refused" in result.stderr
    assert "looks_wrong" in result.stdout
    assert "This submission was not started" in summary
    assert "re-running it will not fix it" in summary
    # And it names the symptom it exists to pre-empt, because the reader of this summary is
    # the person who would otherwise be reading a torch traceback tomorrow.
    assert "torch distributed error" in summary
    # And the one thing a submitter can do about a refusal they have already repaired. A
    # measured refusal is honoured at any age, so nothing clears it but a newer measurement.
    assert "dispatch the nightly workflow" in summary


@pytest.mark.parametrize(
    ("reason", "exit_code"),
    [
        # No run has published one yet, which is the state this lands in and stays in until
        # the first nightly after it merges.
        ("wandb_verdict_not_published", 2),
        # The newest was written before the verdict field existed, or by an --offline run.
        ("wandb_verdict_unreadable", 2),
        # W&B was unreachable when the check ran. An outage, never a bad key.
        ("wandb_verdict_inconclusive", 2),
        # The newest acceptance is older than the tool treats as current.
        ("wandb_verdict_stale", 2),
    ],
)
def test_a_question_nobody_answered_is_not_reported_as_a_bad_key(
    tmp_path: Path, reason: str, exit_code: int
) -> None:
    """THE CASE THIS STEP SHIPS IN, AND THE ONE MOST LIKELY TO BE GOT WRONG.

    This file lands before the first nightly that can publish anything at all, so on the
    day it merges every dispatch meets the first row above. Failing then would take the
    platform down in order to add a check to it, which is the hazard *Why IAM is
    laptop-only* in infra/README.md is about in different clothes.

    The other three rows are the same judgement for the same reason. A measured refusal is
    a finding; an outage, a report this cannot read, and a verdict nobody has renewed are
    all the absence of one, and absence of evidence must never arrive looking like
    evidence. The tool separates them in its exit code -- 1 for the finding and 2 for every
    unanswered question -- so this step never has to guess from prose.

    Mutation: branch on non-zero alone. Every submission is refused the day this merges,
    and the refusal says the key is bad when nothing has looked at it.
    """
    result, summary = _run_preflight(
        tmp_path,
        uv_body=(
            f"cat <<'JSON'\n{_decision('not_established', reason)}\nJSON\nexit {exit_code}\n"
        ),
    )

    assert result.returncode == 0
    assert "wandb_preflight_not_attempted" in result.stderr
    assert "not a verdict on the key" in result.stderr
    assert reason in result.stdout
    # It names the grant this path deliberately does not hold, so a reader who arrives here
    # wondering why the check is quiet meets the decision rather than an apparent bug.
    assert "secretsmanager:GetSecretValue" in result.stderr
    assert "wandb_credential_would_be_refused" not in result.stderr
    assert summary == ""


def test_the_cancellation_step_runs_only_on_a_cancellation_and_last() -> None:
    """Mutation: change the condition to ``always()``, or move the step earlier.

    ``always()`` would also run after a failure, where the message is wrong: a failed
    submission is not a job somebody walked away from. ``success()`` never runs on a
    cancellation at all. And the step has to be last, because everything before it is what
    decides whether there is a job to warn about.
    """
    submit = _job("submit")
    names = [candidate.get("name") for candidate in submit["steps"]]
    cancelled = step(submit, CANCELLED_STEP)

    assert cancelled["if"] == "cancelled()"
    assert names[-1] == CANCELLED_STEP
    # TWO CONDITIONAL STEPS IN THIS JOB AND NO MORE, WHICH IS WHY THE LIST IS PINNED RATHER
    # THAN THE ONE STEP CHECKED. A skipped step in a submission job is the quiet failure
    # mode: GitHub reports the job green and whatever the step was meant to establish is
    # simply absent. So each `if:` here has to be one somebody argued for.
    #
    # The approval read is the second and it earns it. The automatic gate has no reviewers,
    # so GitHub releases the job with no approval to read and the endpoint answers with an
    # empty list; run unconditionally the step would refuse exactly the class that asked for
    # no approver. It fails closed in the other direction, which is what makes it safe to
    # skip: if the condition ever misfires on a routine or exception run, APPROVER arrives
    # empty and the assembly step below refuses the submission by name.
    assert [candidate.get("name") for candidate in submit["steps"] if "if" in candidate] == [
        APPROVAL_STEP,
        CANCELLED_STEP,
    ]


def test_the_cancellation_step_neither_claims_to_stop_a_job_nor_can() -> None:
    """Reads the workflow and the admission role. Mutation: give the role TerminateJob.

    The honest content of this step depends on a fact about the deployed role, so it is
    read rather than restated: the admission role holds no Batch action at all, which is
    why a cancelled workflow can record what is still running and cannot stop it. The day
    somebody grants ``batch:TerminateJob`` here this fails, and the prose has to be
    rewritten in the same change.

    The cancellation path exists and is somewhere else -- ``cancel-run.yml``, on a role of
    its own that this job cannot obtain -- which is exactly why the notice has to name it.
    A step that says only what it cannot do leaves the reader with nowhere to go.
    """
    cancelled = step(_job("submit"), CANCELLED_STEP)
    trust = yaml.safe_load(TRUST_POLICY_PATH.read_text(encoding="utf-8"))
    granted = [
        action
        for resource in trust["Resources"].values()
        if resource.get("Type") == "AWS::IAM::Role"
        for policy in resource["Properties"].get("Policies", [])
        for statement in policy["PolicyDocument"]["Statement"]
        for action in (
            [statement["Action"]] if isinstance(statement["Action"], str) else statement["Action"]
        )
    ]

    assert [action for action in granted if action.startswith("batch:")] == []
    assert "batch:TerminateJob" in cancelled["run"]
    assert "does not stop AWS compute" in cancelled["run"]
    assert "Look at a run, or stop it" in cancelled["run"]
    # And nothing about this step can fail the job. A cancelled job is already cancelled.
    assert "exit 1" not in cancelled["run"]


def test_the_cancellation_step_says_what_the_grace_period_does_not_guarantee() -> None:
    """The comment is the deliverable here, so it is checked like one.

    A reader who takes `if: cancelled()` for a guarantee will build something on top of it.
    GitHub gives a cancelled job a bounded, non-configurable window and then kills the
    runner; a cancellation issued while the job is queued, or while the runner is already
    being torn down, may never reach this step at all.
    """
    rationale = _comment_block_above(CANCELLED_STEP)

    assert "grace period" in rationale
    assert "five minutes" in rationale
    assert "not configurable" in rationale
    assert "best-effort" in rationale
    assert "may never reach this step" in rationale
    assert "cancelled workflow is not a cancelled run" in rationale


def test_the_cancellation_notice_is_written_where_a_person_will_find_it(
    tmp_path: Path,
) -> None:
    """Executed rather than read, because a heredoc is easy to get wrong and quiet about it.

    A terminator that does not land in column 0 makes the shell read the rest of the script
    as more notice, and the step still exits 0. Running it is the only way to know the text
    reached the summary at all.
    """
    summary = tmp_path / "summary.md"
    summary.touch()

    result = run_step_script(
        step(_job("submit"), CANCELLED_STEP)["run"],
        cwd=tmp_path,
        env={"RUN_ID": RUN_ID, "GITHUB_STEP_SUMMARY": str(summary)},
    )

    written = summary.read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert RUN_ID in written
    assert "does not stop AWS compute" in written
    assert "Look at a run, or stop it" in written
    # The prose is prose. A shell that expanded something here would have swallowed the
    # backticks around the action name, which is how it would first be noticed.
    assert "`batch:TerminateJob`" in written


def test_the_cancellation_step_points_at_a_workflow_that_exists_and_a_runbook_that_does() -> None:
    """Reads THREE files. Mutation: rename the workflow, or the runbook section.

    A notice that sends somebody to a workflow nobody wrote, or a heading nobody wrote, is
    worse than no notice: it reads as though the way out exists. Neither pointer is a string
    anything else checks, and both are the kind that rots silently.

    The first pointer is what a submitter uses, so it is held against the file it names and
    against the tick it tells them to use. The second is the fallback for when that workflow
    is itself broken; its commands live in the runbook because every laptop procedure here
    does, and because a literal ``aws`` line in a run body is indistinguishable from a call
    the job makes.
    """
    notice = step(_job("submit"), CANCELLED_STEP)["run"]
    cancel_workflow = load_workflow(WORKFLOWS_ROOT / "cancel-run.yml")

    assert cancel_workflow["name"] in notice, (
        "the notice names no way to stop the job it says may still be running"
    )
    assert "stop" in cancel_workflow["on"]["workflow_dispatch"]["inputs"]

    heading = "Stopping a job a cancelled workflow left running"
    runbook = (PROJECT_ROOT / "infra" / "README.md").read_text(encoding="utf-8")

    assert f"### {heading}" in runbook
    procedure = runbook.split(f"### {heading}", 1)[1].split("\n## ", 1)[0]
    assert "aws batch terminate-job" in procedure
    assert "aws batch list-jobs" in procedure
    assert "RUNNABLE" in procedure


# --------------------------------------------------------------------------------------
# The run bodies, executed the way the runner executes them.
# --------------------------------------------------------------------------------------

FORM_ENVIRONMENT = {
    "FORM_REPOSITORY": "dolma",
    "FORM_COMMIT_SHA": "a" * 40,
    "FORM_IMAGE_DIGEST": "sha256:" + "b" * 64,
    "FORM_WORKLOAD_PROFILE": "dolma-tokenize",
    "FORM_DATASET_RELEASE": "dolma-2026-07",
    "FORM_TEAM": "data-prep",
    "FORM_WANDB_PROJECT": "dolma-tokenize",
    "FORM_EXPERIMENT": "dolma-tokenization",
    "FORM_COMMAND": "python -m dolma.tokenize --note 'two words'",
    # Filled in, because the field is required now. It was empty here while a blank
    # compute profile meant "take the workload profile's", and a workload profile no longer
    # has one to take.
    "FORM_COMPUTE_PROFILE": "cpu-32vcpu",
    "FORM_MAXIMUM_RUNTIME_HOURS": "",
    "FORM_MAXIMUM_ATTEMPTS": "",
    "FORM_FANOUT_SIZE": "",
    "FORM_FANOUT_PARALLELISM": "",
    "FORM_FANOUT_INDEX_PARAMETER": "",
}

# `uv` is stubbed only to drop the locked-environment wrapper: the stub execs the real
# interpreter on the same heredoc, so what runs is the workflow's own script.
UV_PASSTHROUGH = 'shift 3\nexec "${PYTHON_EXECUTABLE}" "$@"\n'


def _run_form_assembly(
    tmp_path: Path,
    **overrides: str,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "uv", UV_PASSTHROUGH)
    result = run_step_script(
        step(_job("compile"), FORM_STEP)["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "PYTHON_EXECUTABLE": sys.executable,
            **FORM_ENVIRONMENT,
            **overrides,
        },
        stub_bin=stub_bin,
    )
    written = tmp_path / "submission-form.json"
    payload = json.loads(written.read_text(encoding="utf-8")) if written.exists() else {}
    return result, payload


def test_the_assembled_form_is_a_document_the_contract_accepts(tmp_path: Path) -> None:
    result, payload = _run_form_assembly(tmp_path)

    assert result.returncode == 0, result.stderr
    inputs = SubmissionInputs.model_validate(payload)
    assert inputs.repository == "dolma"
    # The command is one shell command line on the form and an ordered sequence in the
    # manifest, so a quoted argument has to survive as one argument.
    assert inputs.command == ("python", "-m", "dolma.tokenize", "--note", "two words")
    # An override left empty is omitted rather than sent as an empty string, so the
    # workload profile supplies the value instead of the form contradicting it.
    assert payload.keys() == {
        "repository",
        "commit_sha",
        "image_digest",
        "workload_profile",
        "dataset_release",
        "team",
        "wandb_project",
        # The grouping label. Required on the form and absent from the manifest, which is
        # not an inconsistency: it is a label on the runs rather than a statement about
        # what ran, and a hashed record cannot grow a field without invalidating every
        # record written before it.
        "experiment",
        "command",
        "compute_profile",
    }


def test_a_digest_left_blank_is_omitted_rather_than_sent_as_an_empty_string(
    tmp_path: Path,
) -> None:
    """The seam between "leave it blank" on the form and deriving one from the commit.

    An empty string is not the absence of a digest: it is a digest that fails the pattern,
    so the form would be refused as unusable and the submitter would be told their input
    was invalid for having left an optional field alone. The assembly step already drops
    empty text fields, and this is what says that behaviour is now load-bearing for the
    field a submitter is most likely to leave empty.
    """
    _result, payload = _run_form_assembly(tmp_path, FORM_IMAGE_DIGEST="")

    assert "image_digest" not in payload
    assert SubmissionInputs.model_validate(payload).image_digest is None


def test_the_assembled_form_carries_the_overrides_in_the_types_the_contract_demands(
    tmp_path: Path,
) -> None:
    # The bounds are whole numbers and the runtime bound is base-ten text. A runtime
    # bound sent as a JSON number would have gone through binary floating point, which
    # is not the value the approver read.
    result, payload = _run_form_assembly(
        tmp_path,
        FORM_MAXIMUM_RUNTIME_HOURS="0.5",
        FORM_MAXIMUM_ATTEMPTS="2",
        FORM_FANOUT_SIZE="4",
        FORM_FANOUT_INDEX_PARAMETER="seed",
    )

    assert result.returncode == 0, result.stderr
    assert payload["maximum_runtime_hours"] == "0.5"
    assert isinstance(payload["maximum_attempts"], int)
    assert isinstance(payload["fanout_size"], int)
    inputs = SubmissionInputs.model_validate(payload)
    assert inputs.fanout_index_parameter == "seed"


def test_a_bound_that_is_not_a_whole_number_fails_before_the_tool_sees_it(
    tmp_path: Path,
) -> None:
    result, _payload = _run_form_assembly(tmp_path, FORM_MAXIMUM_ATTEMPTS="two")

    assert result.returncode != 0
    assert "maximum_attempts must be a whole number" in result.stderr


def _run_compile_step(
    tmp_path: Path,
    *,
    uv_body: str,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    stub_bin = tmp_path / "bin"
    recorded = tmp_path / "argv.txt"
    write_stub(stub_bin, "uv", f'printf "%s\\n" "$@" > "{recorded}"\n{uv_body}')
    result = run_step_script(
        step(_job("compile"), COMPILE_STEP)["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_OUTPUT": str(tmp_path / "step-output.txt"),
            "SUBMITTER": "caiiris",
            "SERVER_URL": "https://github.com",
            "REPOSITORY_OWNER": "edu-llm",
            "RESEARCH_REPOSITORY": "dolma",
        },
        stub_bin=stub_bin,
    )
    arguments = recorded.read_text(encoding="utf-8").splitlines() if recorded.exists() else []
    return result, arguments


def test_the_compile_step_hands_the_tool_the_reviewed_configuration_and_the_submitter(
    tmp_path: Path,
) -> None:
    result, arguments = _run_compile_step(tmp_path, uv_body="exit 0\n")

    assert result.returncode == 0, result.stderr
    passed = dict(itertools.pairwise(arguments))
    assert passed["--config-dir"] == "config"
    assert passed["--submitter"] == "caiiris"
    assert passed["--repository-url"] == "https://github.com/edu-llm/dolma"
    assert passed["--inputs"] == str(tmp_path / "submission-form.json")
    assert passed["--output"] == str(tmp_path / "compiled-submission.json")
    assert passed["--summary"] == str(tmp_path / "approver-context.md")
    assert passed["--github-output"] == str(tmp_path / "step-output.txt")


def test_a_submission_refused_on_its_merits_says_so_rather_than_reading_as_a_breakage(
    tmp_path: Path,
) -> None:
    result, _arguments = _run_compile_step(
        tmp_path,
        uv_body='echo "submission refused: unregistered dataset" >&2\nexit 1\n',
    )

    assert result.returncode == 1
    assert "submission refused: unregistered dataset" in result.stderr
    assert "submission_refused" in result.stderr
    assert "no reviewer was asked" in result.stderr


def test_a_form_the_tool_could_not_read_is_not_reported_as_a_judgement(
    tmp_path: Path,
) -> None:
    result, _arguments = _run_compile_step(
        tmp_path,
        uv_body='echo "the submission form is not valid" >&2\nexit 2\n',
    )

    assert result.returncode == 1
    assert "submission_form_unusable" in result.stderr
    assert "not a refusal on the merits" in result.stderr
    assert "submission_refused" not in result.stderr


DENY_ENVIRONMENT = {
    "ADMISSION_ROLE_ARN": ADMISSION_ROLE_ARN,
    "PROBE_AWS_REGION": "us-east-1",
    "ACTIONS_ID_TOKEN_REQUEST_URL": "https://example.invalid/token?api-version=2.0",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
}


def _sts_error(code: str, detail: str) -> str:
    """An `aws` stub that answers the way the CLI reports an STS refusal."""
    message = (
        f"An error occurred ({code}) when calling the AssumeRoleWithWebIdentity operation: {detail}"
    )
    return f'echo "{message}" >&2\nexit 254\n'


TRUST_REFUSAL = _sts_error(
    "AccessDenied", "Not authorized to perform sts:AssumeRoleWithWebIdentity"
)


def _run_deny_probe(
    tmp_path: Path,
    *,
    aws_body: str = TRUST_REFUSAL,
    **overrides: str,
) -> tuple[subprocess.CompletedProcess[str], bool]:
    stub_bin = tmp_path / "bin"
    reached = tmp_path / "sts-reached.txt"
    write_stub(
        stub_bin,
        "curl",
        'destination=""\n'
        "while [[ $# -gt 0 ]]; do\n"
        '  if [[ "$1" == "--output" ]]; then shift; destination="$1"; fi\n'
        "  shift\n"
        "done\n"
        'printf \'{"value":"header.payload.signature"}\' > "${destination}"\n',
    )
    write_stub(stub_bin, "aws", f'touch "{reached}"\n{aws_body}')
    result = run_step_script(
        step(_job("deny-unapproved"), DENY_STEP)["run"],
        cwd=tmp_path,
        env={"RUNNER_TEMP": str(tmp_path), **DENY_ENVIRONMENT, **overrides},
        stub_bin=stub_bin,
    )
    return result, reached.exists()


def test_the_probe_passes_when_the_admission_role_refuses_a_subject_without_a_gate(
    tmp_path: Path,
) -> None:
    result, reached = _run_deny_probe(tmp_path)

    assert result.returncode == 0, result.stderr
    assert reached
    assert "refused a subject minted without an environment" in result.stdout


def test_a_probe_whose_assume_unexpectedly_succeeded_fails_the_job_loudly(
    tmp_path: Path,
) -> None:
    result, _reached = _run_deny_probe(tmp_path, aws_body='echo "a session"\nexit 0\n')

    assert result.returncode == 1
    assert "admission_role_assumed_without_an_environment" in result.stderr
    assert "no longer enumerates only the two approval environments" in result.stderr


@pytest.mark.parametrize(
    ("probe", "aws_body", "expected"),
    [
        (
            "network",
            'echo "Could not connect to the endpoint URL" >&2\nexit 255\n',
            "deny_probe_inconclusive:sts_was_not_reached",
        ),
        (
            "token rejected",
            _sts_error("InvalidIdentityToken", "no OpenIDConnect provider"),
            "deny_probe_inconclusive:InvalidIdentityToken",
        ),
        (
            "expired token",
            _sts_error("ExpiredTokenException", "Token expired"),
            "deny_probe_inconclusive:ExpiredTokenException",
        ),
    ],
)
def test_a_probe_that_failed_for_an_unrelated_reason_establishes_nothing(
    tmp_path: Path,
    probe: str,
    aws_body: str,
    expected: str,
) -> None:
    # A probe that goes green because something unrelated broke is worse than no probe:
    # it reports that an unapproved job cannot reach AWS when nothing established that.
    result, _reached = _run_deny_probe(tmp_path, aws_body=aws_body)

    assert result.returncode == 1, probe
    assert expected in result.stderr


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"ADMISSION_ROLE_ARN": ""}, "admission_role_arn_unset"),
        (
            {"ADMISSION_ROLE_ARN": "sbsandbox-intern-edullm-admission"},
            "admission_role_arn_malformed",
        ),
        ({"PROBE_AWS_REGION": ""}, "aws_region_unset"),
        ({"ACTIONS_ID_TOKEN_REQUEST_TOKEN": ""}, "oidc_token_request_unavailable"),
        ({"ACTIONS_ID_TOKEN_REQUEST_URL": ""}, "oidc_token_request_unavailable"),
    ],
)
def test_a_probe_that_is_missing_what_it_needs_says_which_thing_and_never_reaches_sts(
    tmp_path: Path,
    overrides: dict[str, str],
    expected: str,
) -> None:
    result, reached = _run_deny_probe(tmp_path, **overrides)

    assert result.returncode == 1
    assert expected in result.stderr
    assert not reached
    assert ADMISSION_ROLE_ARN not in result.stdout + result.stderr
    assert EXAMPLE_ACCOUNT_ID not in result.stdout + result.stderr


APPROVED_BODY = json.dumps(
    [
        {
            "state": "approved",
            "user": {"login": "team-lead"},
            "environments": [{"name": "run-approval-lead"}],
        }
    ]
)


def _run_approval_step(
    tmp_path: Path,
    *,
    gh_body: str,
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "gh", gh_body)
    result = run_step_script(
        step(_job("submit"), APPROVAL_STEP)["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_OUTPUT": str(tmp_path / "step-output.txt"),
            "GH_TOKEN": "a-token",
            "RUN_REPOSITORY": PLATFORM_REPOSITORY,
            "WORKFLOW_RUN_ID": "1704",
        },
        stub_bin=stub_bin,
    )
    output_file = tmp_path / "step-output.txt"
    lines = output_file.read_text(encoding="utf-8").splitlines() if output_file.exists() else []
    return result, dict(line.split("=", 1) for line in lines)


def test_the_approval_read_is_skipped_only_for_the_gate_that_has_no_reviewers() -> None:
    """The condition, read off the workflow rather than restated.

    Written against the class the compile job computed and not against
    ``needs.compile.outputs.environment``, though either would work today, because the class
    is what policy decides and the environment is what it picks. Written against ``inputs.``
    it would be a submitter's claim, and a submitter who can skip the approval read can
    reach admission naming no approver on a run that needed one.

    Mutation: change ``!=`` to ``==``. Every routine and exception submission skips the read
    and is refused at the assembly step, and the automatic ones fail on an empty approvals
    list. The suite stays green apart from here, because both halves are the same string in
    two files otherwise.
    """
    condition = step(_job("submit"), APPROVAL_STEP)["if"]

    assert condition == "needs.compile.outputs.approval_class != 'automatic'"
    assert ApprovalClass.AUTOMATIC.value in condition
    assert "inputs." not in condition


def test_the_approver_login_is_read_out_of_the_approvals_endpoint(tmp_path: Path) -> None:
    result, outputs = _run_approval_step(tmp_path, gh_body=f"cat <<'JSON'\n{APPROVED_BODY}\nJSON\n")

    assert result.returncode == 0, result.stderr
    assert outputs == {"approver": "team-lead"}
    assert "released by team-lead" in result.stdout


@pytest.mark.parametrize(
    ("probe", "body"),
    [
        ("nobody has approved", "[]"),
        ("the entry names no user", '[{"state": "approved", "user": null}]'),
        ("the only entry is a rejection", '[{"state": "rejected", "user": {"login": "lead"}}]'),
    ],
)
def test_an_approvals_body_that_names_no_approver_stops_before_aws(
    tmp_path: Path,
    probe: str,
    body: str,
) -> None:
    # Admission evaluates authorization against the approver, so submitting with the
    # question unanswered would be refused there for a reason that misdescribes what
    # happened: it would read as though the approver was not permitted.
    result, outputs = _run_approval_step(tmp_path, gh_body=f"cat <<'JSON'\n{body}\nJSON\n")

    assert result.returncode == 1, probe
    assert "approver_login_unreadable" in result.stderr
    assert outputs == {}


def test_an_unreadable_approvals_endpoint_is_reported_as_itself(tmp_path: Path) -> None:
    result, _outputs = _run_approval_step(
        tmp_path,
        gh_body='echo "gh: Not Found (HTTP 404)" >&2\nexit 1\n',
    )

    assert result.returncode == 1
    assert "approvals_endpoint_unreadable" in result.stderr
    assert "approver_login_unreadable" not in result.stderr


def test_a_login_that_is_not_a_login_never_reaches_github_output(tmp_path: Path) -> None:
    # GITHUB_OUTPUT is line oriented, so a value carrying a newline would define a second
    # output. The pattern is the one the record this feeds is validated against.
    smuggled = json.dumps([{"state": "approved", "user": {"login": "lead\napprover=root"}}])
    result, outputs = _run_approval_step(tmp_path, gh_body=f"cat <<'JSON'\n{smuggled}\nJSON\n")

    assert result.returncode == 1
    assert "approver_login_unusable" in result.stderr
    assert outputs == {}


COMPILED_SUBMISSION = {
    "run_id": RUN_ID,
    "submitter": "caiiris",
    "approval_class": "routine",
    "approving_environment": ApprovalEnvironment.LEAD.value,
    # Deliberately not the approved digest. The request must carry the value that crossed
    # the gate through `needs`, not the one written inside the document being judged.
    "manifest_sha256": RECORDED_SHA256,
    "manifest": {"schema_version": 1, "repository": "dolma"},
    # Beside the manifest, never inside it: the digest above is what the approver released,
    # and a grouping key folded into the hashed document would move the digest of every
    # record written before the field existed.
    "experiment": "dolma-tokenization",
}
REQUEST_ENVIRONMENT = {
    "APPROVED_SHA256": APPROVED_SHA256,
    # The routing decision the credential-free compile job made, which the assembly step
    # reads to know whether an approver is owed. Routine here, so these tests exercise the
    # path every submission took before the automatic class existed; the automatic path has
    # its own tests below.
    "APPROVAL_CLASS": "routine",
    "APPROVER": "team-lead",
    # What the registry step resolved for the repository the manifest above names. The
    # assembly step does not consult the registry -- the step before it does, and the
    # validator re-derives the same answer -- so this is that step's output standing in.
    "ECR_REPOSITORY": "sbsandbox-intern-edullm-dolma",
    "RUN_REPOSITORY": PLATFORM_REPOSITORY,
    "WORKFLOW_REPOSITORY": PLATFORM_REPOSITORY,
    "WORKFLOW_FILE_PATH": WORKFLOW_FILE,
    "WORKFLOW_REF": JOB_WORKFLOW_REF,
    "WORKFLOW_RUN_ID": "1704",
    "WORKFLOW_RUN_ATTEMPT": "1",
}


def _run_request_assembly(
    tmp_path: Path,
    **overrides: str,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    downloaded = tmp_path / "compiled-submission"
    downloaded.mkdir(exist_ok=True)
    (downloaded / "compiled-submission.json").write_text(
        json.dumps(COMPILED_SUBMISSION), encoding="utf-8"
    )
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "uv", UV_PASSTHROUGH)
    result = run_step_script(
        step(_job("submit"), REQUEST_STEP)["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "PYTHON_EXECUTABLE": sys.executable,
            **REQUEST_ENVIRONMENT,
            **overrides,
        },
        stub_bin=stub_bin,
    )
    written = tmp_path / "admission-request.json"
    request = json.loads(written.read_text(encoding="utf-8")) if written.exists() else {}
    return result, request


def test_the_admission_request_carries_exactly_what_the_handler_requires(
    tmp_path: Path,
) -> None:
    result, request = _run_request_assembly(tmp_path)

    assert result.returncode == 0, result.stderr
    # The handler names its required fields; `approver` and `project` are the two optional
    # ones it reads, and this workflow always supplies both. `project` is optional to the
    # handler rather than required because an execution already past the approval gate when
    # the field shipped carries no project, and refusing those would fail runs a lead had
    # released for a reason that has nothing to do with them.
    assert set(request) == set(admission_handler._REQUIRED_EVENT_FIELDS) | {
        "approver",
        "experiment",
    }
    assert request["run_id"] == RUN_ID
    assert request["submitter"] == "caiiris"
    assert request["approver"] == "team-lead"
    assert ApprovalEnvironment(request["approving_environment"]) is ApprovalEnvironment.LEAD
    assert request["manifest"] == COMPILED_SUBMISSION["manifest"]


def test_the_digest_in_the_request_is_the_one_that_crossed_the_gate(tmp_path: Path) -> None:
    # Reading it back out of the document would make the check circular: admission would
    # be comparing the document against a claim the same document carries.
    _result, request = _run_request_assembly(tmp_path)

    assert request["approved_manifest_sha256"] == APPROVED_SHA256
    assert request["approved_manifest_sha256"] != COMPILED_SUBMISSION["manifest_sha256"]


def test_the_recorded_workflow_run_is_one_the_contract_accepts(tmp_path: Path) -> None:
    _result, request = _run_request_assembly(tmp_path)

    reference = GitHubWorkflowRunReference.model_validate(request["workflow_run"])

    # The ref half of job.workflow_ref, never a commit SHA: the trust policy matches this
    # composed claim with StringEquals against @refs/heads/main.
    assert reference.workflow_ref == "refs/heads/main"
    assert reference.job_workflow_ref == JOB_WORKFLOW_REF
    assert reference.run_id == 1704
    assert reference.run_attempt == 1


@pytest.mark.parametrize(
    "variable",
    [
        "WORKFLOW_REPOSITORY",
        "WORKFLOW_FILE_PATH",
        "WORKFLOW_REF",
        "APPROVER",
        # Empty only if the compile job's output were renamed or dropped, and then every
        # submission would look automatic to this step and reach admission naming no
        # approver. Refusing an empty one costs nothing and closes that.
        "APPROVAL_CLASS",
        "APPROVED_SHA256",
        # Empty is the shape this one actually fails in. The others go empty on GitHub
        # Enterprise Server; this one goes empty if the registry step is ever moved back
        # below the assembly, because a `steps.` output read before its step has run is the
        # empty string rather than an error. An empty repository name would reach ECR.
        "ECR_REPOSITORY",
    ],
)
def test_an_empty_job_workflow_identity_fails_closed(tmp_path: Path, variable: str) -> None:
    # The job-context workflow properties are documented as unavailable on GitHub
    # Enterprise Server, where they resolve to the empty string rather than failing.
    result, request = _run_request_assembly(tmp_path, **{variable: ""})

    assert result.returncode != 0
    assert variable in result.stderr
    assert request == {}


def test_an_automatic_submission_names_no_approver_in_its_request(tmp_path: Path) -> None:
    """Null rather than the submitter, and null rather than absent.

    The reviewer-less gate releases the job without an approval, so the step that reads who
    released it is skipped and ``APPROVER`` arrives empty. Writing the submitter here would
    manufacture a self-approval nobody performed; writing a lead would name somebody never
    asked. The key is still present because the state machine's payload block resolves
    ``$.approver`` by path and a missing key fails the parameter build.

    Mutation: drop the ``approval_class`` branch and call ``required("APPROVER")``
    unconditionally. Every automatic submission dies here, and the cheap runs this class
    exists for are worse off than before it.
    """
    result, request = _run_request_assembly(
        tmp_path, APPROVAL_CLASS="automatic", APPROVER=""
    )

    assert result.returncode == 0, result.stderr
    assert "approver" in request
    assert request["approver"] is None
    assert request["submitter"] == "caiiris"


def test_an_automatic_submission_that_arrives_with_an_approver_is_refused(
    tmp_path: Path,
) -> None:
    """A routing fault worth stopping for rather than dropping quietly.

    An approver on an automatic run means the reviewer-less gate was not the gate that
    released it, so either the class or the environment is wrong -- and admission's own
    check compares the environment against the class, not against this. Recording the run
    with the approver silently dropped would leave a decision record claiming a class that
    does not match how the run actually got here.
    """
    result, request = _run_request_assembly(
        tmp_path, APPROVAL_CLASS="automatic", APPROVER="team-lead"
    )

    assert result.returncode != 0
    assert "automatic" in result.stderr
    assert request == {}


def _run_denial_matrix(
    tmp_path: Path,
    *,
    uv_body: str,
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    denials_step = step(_job("submit"), DENIALS_STEP)
    stub_bin = tmp_path / "bin"
    recorded = tmp_path / "argv.txt"
    write_stub(stub_bin, "uv", f'printf "%s\\n" "$@" > "{recorded}"\n{uv_body}')
    result = run_step_script(
        denials_step["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "ADMISSION_ACCOUNT_ID": EXAMPLE_ACCOUNT_ID,
            "ADMISSION_REGION": "us-east-1",
            # Read off the step rather than restated, so the bucket the probe is aimed at
            # is the one the workflow names.
            "LINEAGE_BUCKET": denials_step["env"]["LINEAGE_BUCKET"],
        },
        stub_bin=stub_bin,
    )
    arguments = recorded.read_text(encoding="utf-8").splitlines() if recorded.exists() else []
    return result, dict(itertools.pairwise(arguments))


def test_the_probes_are_aimed_at_the_deployed_admission_machine_and_bucket(
    tmp_path: Path,
) -> None:
    result, passed = _run_denial_matrix(tmp_path, uv_body="exit 0\n")

    assert result.returncode == 0, result.stderr
    assert passed["--region"] == "us-east-1"
    assert passed["--lineage-bucket"] == LINEAGE_BUCKET
    assert passed["--output"] == str(tmp_path / "admission-denials.json")
    # The ARN is composed here and validated there, so the two agreeing is the property
    # rather than the spelling. An ARN the tool cannot read is exit 2, which would report
    # a typo in this workflow as though nothing could be established about the role.
    machine = read_state_machine_arn(passed["--state-machine-arn"], region="us-east-1")
    assert machine.arn == (
        f"arn:aws:states:us-east-1:{EXAMPLE_ACCOUNT_ID}:stateMachine:{STATE_MACHINE_NAME}"
    )


def test_the_uploaded_record_is_the_file_the_probes_were_told_to_write(
    tmp_path: Path,
) -> None:
    _result, passed = _run_denial_matrix(tmp_path, uv_body="exit 0\n")
    upload = step(_job("submit"), DENIALS_UPLOAD_STEP)

    written = Path(passed["--output"]).name
    assert upload["with"]["path"] == f"${{{{ runner.temp }}}}/{written}"


def test_a_role_wider_than_its_grant_stops_the_submission(tmp_path: Path) -> None:
    # Exit 1 says something the admission role must not be able to do was permitted, or
    # that a probe established nothing. Either way the session in hand was not shown to
    # be the narrow one, and a submission is exactly what a widened role would be used
    # for, so this must never be a warning.
    result, _passed = _run_denial_matrix(
        tmp_path,
        uv_body='echo "s3:PutObject: permitted" >&2\nexit 1\n',
    )

    assert result.returncode == 1
    assert "s3:PutObject: permitted" in result.stderr
    assert "admission_denial_matrix_not_proved" in result.stderr
    assert "must not submit" in result.stderr


def test_probes_that_could_not_be_set_up_are_not_reported_as_a_finding(
    tmp_path: Path,
) -> None:
    # Exit 2 is the tool saying it attempted nothing. It still fails the job, and it must
    # not read like the security finding above, the same way the compile step separates a
    # refusal on the merits from a form it could not read.
    result, _passed = _run_denial_matrix(
        tmp_path,
        uv_body='echo "state_machine_arn_unusable" >&2\nexit 2\n',
    )

    assert result.returncode == 1
    assert "admission_denial_matrix_not_attempted" in result.stderr
    assert "not a finding about how wide the role is" in result.stderr
    assert "admission_denial_matrix_not_proved" not in result.stderr


def test_no_denial_matrix_failure_echoes_the_account_id(tmp_path: Path) -> None:
    # A refusal names the account, and the state machine ARN this step composes carries
    # it. Neither reaches the log on any path out of this step.
    for status in ("1", "2"):
        result, _passed = _run_denial_matrix(
            tmp_path,
            uv_body=f'echo "denial probe failed" >&2\nexit {status}\n',
        )

        assert result.returncode == 1, status
        assert EXAMPLE_ACCOUNT_ID not in result.stdout + result.stderr, status


def _run_start_execution(
    tmp_path: Path,
    *,
    aws_body: str,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    (tmp_path / "admission-request.json").write_text(
        json.dumps({"run_id": RUN_ID}), encoding="utf-8"
    )
    stub_bin = tmp_path / "bin"
    recorded = tmp_path / "argv.txt"
    write_stub(stub_bin, "aws", f'printf "%s\\n" "$@" > "{recorded}"\n{aws_body}')
    result = run_step_script(
        step(_job("submit"), START_STEP)["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "ADMISSION_ACCOUNT_ID": EXAMPLE_ACCOUNT_ID,
            "ADMISSION_REGION": "us-east-1",
            "RUN_ID": RUN_ID,
        },
        stub_bin=stub_bin,
    )
    arguments = recorded.read_text(encoding="utf-8").splitlines() if recorded.exists() else []
    return result, arguments


def test_the_execution_is_named_for_the_run_id_and_carries_the_assembled_request(
    tmp_path: Path,
) -> None:
    result, arguments = _run_start_execution(tmp_path, aws_body="exit 0\n")

    assert result.returncode == 0, result.stderr
    passed = dict(itertools.pairwise(arguments))
    assert passed["--name"] == RUN_ID
    assert json.loads(passed["--input"]) == {"run_id": RUN_ID}
    assert passed["--state-machine-arn"] == (
        f"arn:aws:states:us-east-1:{EXAMPLE_ACCOUNT_ID}:stateMachine:{STATE_MACHINE_NAME}"
    )


def test_an_execution_that_already_exists_under_this_run_id_is_a_success(
    tmp_path: Path,
) -> None:
    # Step Functions is idempotent for a same-name, same-input StartExecution only while
    # the original is still running. Admission settles in seconds, so a genuine re-run of
    # this job always lands on the 400 instead -- and the only thing it can mean is that
    # this run id was already admitted.
    result, _arguments = _run_start_execution(
        tmp_path,
        aws_body='echo "An error occurred (ExecutionAlreadyExists) when calling the'
        ' StartExecution operation: Execution Already Exists" >&2\nexit 254\n',
    )

    assert result.returncode == 0, result.stderr
    assert "already been admitted" in result.stdout


def test_any_other_start_execution_error_fails_without_echoing_the_account_id(
    tmp_path: Path,
) -> None:
    result, _arguments = _run_start_execution(
        tmp_path,
        aws_body='echo "An error occurred (AccessDeniedException) when calling the'
        " StartExecution operation: User is not authorized on resource"
        f' arn:aws:states:us-east-1:{EXAMPLE_ACCOUNT_ID}:stateMachine:{STATE_MACHINE_NAME}"'
        " >&2\nexit 254\n",
    )

    assert result.returncode == 1
    assert "start_execution_failed" in result.stderr
    assert EXAMPLE_ACCOUNT_ID not in result.stdout + result.stderr


# Successive calls answer successive statuses, so a poll can be shown to poll rather than
# to read once. The last status repeats once the list runs out. That repeat is spelled as
# arithmetic rather than as ${statuses[-1]}: negative subscripts need bash 4.2, and macOS
# ships 3.2, where the expansion yields an empty string instead of failing. The poll would
# then read "" as a terminal status and break after one iteration -- green on a runner,
# failing here, and for a reason that looks like a bug in the workflow rather than the stub.
#
# The answer is the projection the step asks describe-execution for -- status, error and
# cause -- rather than a bare status word, because reading the reason out of the same
# response is the property under test. The two diagnostic fields arrive already JSON
# encoded, so a stub can answer with either a string or a null the way the service does.
POLLING_AWS_STUB = """
counter_file="${RUNNER_TEMP}/poll-count.txt"
index=0
if [[ -f "${counter_file}" ]]; then
  index="$(cat "${counter_file}")"
fi
echo "$((index + 1))" > "${counter_file}"
read -r -a statuses <<< "${EXECUTION_STATUSES}"
if [[ "${index}" -lt "${#statuses[@]}" ]]; then
  status="${statuses[${index}]}"
else
  status="${statuses[$((${#statuses[@]} - 1))]}"
fi
printf '{"status": "%s", "error": %s, "cause": %s}\\n' \\
  "${status}" "${EXECUTION_ERROR}" "${EXECUTION_CAUSE}"
"""

#: A cause of the shape the deployed state machine produces. Every Fail state in
#: infra/admission-state-machine.yaml declares a static Cause naming the lineage prefix to
#: read, so this is prose the step has to carry through rather than parse.
REJECTED_CAUSE = (
    "The validator refused this run. The refusal and its reasons are recorded under "
    "decision/ in the lineage bucket."
)


def _run_wait_step(
    tmp_path: Path,
    *,
    statuses: str,
    error: str | None = None,
    cause: str | None = None,
    maximum_attempts: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], int, str]:
    wait_step = step(_job("submit"), WAIT_STEP)
    stub_bin = tmp_path / "bin"
    slept = tmp_path / "sleeps.txt"
    summary = tmp_path / "summary.md"
    summary.touch()
    write_stub(stub_bin, "aws", POLLING_AWS_STUB)
    write_stub(stub_bin, "sleep", f'echo "$1" >> "{slept}"\n')
    write_stub(stub_bin, "uv", UV_PASSTHROUGH)
    result = run_step_script(
        wait_step["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "PYTHON_EXECUTABLE": sys.executable,
            "PYTHONPATH": str(PROJECT_ROOT / "src"),
            "GITHUB_STEP_SUMMARY": str(summary),
            "ADMISSION_ACCOUNT_ID": EXAMPLE_ACCOUNT_ID,
            "ADMISSION_REGION": "us-east-1",
            "RUN_ID": RUN_ID,
            "EXECUTION_STATUSES": statuses,
            "EXECUTION_ERROR": json.dumps(error),
            "EXECUTION_CAUSE": json.dumps(cause),
            "MAXIMUM_POLL_ATTEMPTS": maximum_attempts or wait_step["env"]["MAXIMUM_POLL_ATTEMPTS"],
            "POLL_INTERVAL_SECONDS": wait_step["env"]["POLL_INTERVAL_SECONDS"],
        },
        stub_bin=stub_bin,
    )
    sleeps = len(slept.read_text(encoding="utf-8").splitlines()) if slept.exists() else 0
    return result, sleeps, summary.read_text(encoding="utf-8")


def test_the_job_waits_for_the_execution_to_leave_running(tmp_path: Path) -> None:
    # StartExecution answers as soon as the execution is created, so without this a
    # rejected submission would be indistinguishable from an accepted one.
    result, sleeps, summary = _run_wait_step(tmp_path, statuses="RUNNING RUNNING SUCCEEDED")

    assert result.returncode == 0, result.stderr
    assert sleeps == 2
    assert "Admission accepted this submission" in result.stdout
    # An accepted submission earns no refusal block, and the step that says where the run
    # went writes the summary instead.
    assert summary == ""


@pytest.mark.parametrize("status", ["FAILED", "TIMED_OUT", "ABORTED"])
def test_an_execution_that_did_not_succeed_fails_the_job(tmp_path: Path, status: str) -> None:
    result, _sleeps, _summary = _run_wait_step(tmp_path, statuses=status)

    assert result.returncode == 1
    assert f"admission_execution_{status}" in result.stderr
    assert "recorded under decision/" in result.stderr


def test_the_poll_is_bounded_and_says_so_rather_than_claiming_a_decision(
    tmp_path: Path,
) -> None:
    result, sleeps, _summary = _run_wait_step(tmp_path, statuses="RUNNING", maximum_attempts="3")

    assert result.returncode == 1
    assert sleeps == 3
    assert "admission_execution_did_not_settle" in result.stderr
    assert "Nothing has been decided either way" in result.stderr


def test_a_refused_submission_tells_the_submitter_which_refusal_and_where_to_read_it(
    tmp_path: Path,
) -> None:
    """Mutation: read only ``status`` out of describe-execution, as this step once did.

    ``admission_execution_FAILED`` is what a refused submitter used to be left with, and it
    is the same line an execution that crashed produces. ``error`` is what separates them,
    and it costs nothing: the response the poll already reads carries it.

    Both the log and the step summary get it, and they get the same bytes -- a summary is
    rendered in the run page and exposed by no REST endpoint, so a submitter reading the
    log and a reviewer reading the page have to be reading one refusal rather than two
    renderings of one.
    """
    result, _sleeps, summary = _run_wait_step(
        tmp_path,
        statuses="FAILED",
        error="AdmissionRejected",
        cause=REJECTED_CAUSE,
    )

    assert result.returncode == 1
    for written in (result.stdout, summary):
        assert "Admission did not accept this submission" in written
        assert RUN_ID in written
        assert "AdmissionRejected" in written
        assert REJECTED_CAUSE in written
        assert "decision/" in written
    assert summary in result.stdout


def test_the_cause_of_a_refusal_is_masked_before_it_reaches_the_step_summary(
    tmp_path: Path,
) -> None:
    """Mutation: print the cause as it arrived.

    ``mask-aws-account-id`` masks this job's *log*, and a step summary is not log output.
    The three Fail states this state machine declares carry static prose, but an unmodelled
    task failure puts raw AWS error text in the cause and AWS error text routinely names an
    ARN -- so the account would reach a page anybody can read.
    """
    result, _sleeps, summary = _run_wait_step(
        tmp_path,
        statuses="FAILED",
        error="States.Runtime",
        cause=(
            "An error occurred: User is not authorized to perform states:StartExecution on "
            f"arn:aws:states:us-east-1:{EXAMPLE_ACCOUNT_ID}:stateMachine:{STATE_MACHINE_NAME}"
        ),
    )

    assert result.returncode == 1
    assert EXAMPLE_ACCOUNT_ID not in summary
    assert EXAMPLE_ACCOUNT_ID not in result.stdout + result.stderr
    # Masked rather than dropped: the rest of the sentence is the part worth reading.
    assert "states:StartExecution" in summary
    assert "States.Runtime" in summary


def test_a_cause_that_cannot_be_masked_is_withheld_rather_than_printed(
    tmp_path: Path,
) -> None:
    """Mutation: fall back to the raw text when redaction refuses it.

    ``redact_aws_account_ids`` refuses text carrying anything shaped like another
    credential, because masking a digit run inside a secret access key would break the
    shape that identifies it and launder a live credential into a page anybody can read.
    A refusal to mask has to end in nothing being printed, not in printing it anyway.
    """
    secret_shaped = "AKIA" + "I" * 16 + " wJalrXUtnFEMI0K7MDENG1bPxRfiCYEXAMPLEKEY"
    result, _sleeps, summary = _run_wait_step(
        tmp_path,
        statuses="FAILED",
        error="States.Runtime",
        cause=f"the task failed carrying {secret_shaped}",
    )

    assert result.returncode == 1
    assert secret_shaped not in summary + result.stdout + result.stderr
    assert "could not be masked" in summary


def test_a_refusal_with_no_error_or_cause_says_none_rather_than_printing_null(
    tmp_path: Path,
) -> None:
    # describe-execution omits both on some terminal states, and the CLI projection turns
    # an absent field into a JSON null. `None` in a step summary reads like a value.
    _result, _sleeps, summary = _run_wait_step(tmp_path, statuses="ABORTED")

    assert "Error: `none`" in summary
    assert "None" not in summary
    assert "null" not in summary


SUBMITTED_STEP = "Say where this run went"


def _run_where_it_went(
    tmp_path: Path,
    *,
    compute_profile: str = "gpu-1xa10g",
) -> tuple[subprocess.CompletedProcess[str], str]:
    downloaded = tmp_path / "compiled-submission"
    downloaded.mkdir(exist_ok=True)
    manifest = load_representative_manifest("gpu-routine.yaml").model_copy(
        update={"compute_profile": compute_profile}
    )
    (downloaded / "compiled-submission.json").write_text(
        json.dumps({"run_id": RUN_ID, "manifest": manifest.model_dump(mode="json")}),
        encoding="utf-8",
    )
    summary = tmp_path / "summary.md"
    summary.touch()
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "uv", UV_PASSTHROUGH)
    # The step reads config/execution-targets.yaml relative to the working directory, the
    # way the registry lookup above reads config/repositories.yaml. The committed file is
    # copied beside the script rather than the script being run in the checkout, because a
    # run body writes itself to disk before it executes and would leave a stray file in the
    # repository root every time this ran.
    (tmp_path / "config").mkdir(exist_ok=True)
    shutil.copy2(
        PROJECT_ROOT / "config" / "execution-targets.yaml",
        tmp_path / "config" / "execution-targets.yaml",
    )
    submitted = step(_job("submit"), SUBMITTED_STEP)
    result = run_step_script(
        submitted["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "PYTHON_EXECUTABLE": sys.executable,
            "PYTHONPATH": str(PROJECT_ROOT / "src"),
            "GITHUB_STEP_SUMMARY": str(summary),
            "RUN_ID": RUN_ID,
            "STATE_MACHINE_NAME": submitted["env"]["STATE_MACHINE_NAME"],
        },
        stub_bin=stub_bin,
    )
    return result, summary.read_text(encoding="utf-8")


def test_an_accepted_submission_says_where_every_trace_of_it_will_be(
    tmp_path: Path,
) -> None:
    """Mutation: print one line and let the submitter work the rest out.

    Seven places carry a trace of one run and the run id joins six of them, which is a fact
    about this repository rather than about AWS. A submitter who has to learn it by reading
    the source is a submitter who does not learn it.

    The queue and the log group are read from the execution target rather than written down
    here, so this fails if the two configuration files stop agreeing about where a profile
    goes -- which is the same seam admission resolves against.
    """
    binding = load_yaml(
        PROJECT_ROOT / "config" / "execution-targets.yaml", ExecutionTargetCatalog
    ).binding_for("gpu-1xa10g")
    manifest = load_representative_manifest("gpu-routine.yaml")
    result, summary = _run_where_it_went(tmp_path)

    assert result.returncode == 0, result.stderr
    assert binding is not None
    assert f"| Run id | `{RUN_ID}` |" in summary
    assert f"`{RUN_ID} on {STATE_MACHINE_NAME}`" in summary
    assert f"| Batch job name | `{RUN_ID}` |" in summary
    assert f"| Batch job queue | `{binding.job_queue}` |" in summary
    assert f"| CloudWatch log group | `{binding.log_group}` |" in summary
    assert f"| W&B project | `{manifest.wandb_project}` |" in summary
    assert RUN_ID in result.stdout


def test_the_output_prefix_it_prints_is_the_one_the_container_is_told(
    tmp_path: Path,
) -> None:
    """Reads the workflow and the contract. Mutation: compose the prefix in the workflow.

    ``contracts/results.py::output_prefix`` exists because three places once answered
    "where does a run write" and only two of them agreed, and the one that was wrong was
    the record a reader would have followed. A prefix assembled in this step would be the
    fourth answer and would drift the same way.
    """
    manifest = load_representative_manifest("gpu-routine.yaml")
    _result, summary = _run_where_it_went(tmp_path)

    expected = output_prefix(team=manifest.team, run_id=RUN_ID)
    assert f"| S3 output prefix | `{expected}` |" in summary
    assert expected.endswith(f"/runs/{RUN_ID}/")
    assert "output_prefix" in step(_job("submit"), SUBMITTED_STEP)["run"]


def test_no_weights_and_biases_url_is_invented(tmp_path: Path) -> None:
    """Mutation: write a wandb.ai link.

    A W&B run is named for the run id and addressed by an id W&B generates, so the run URL
    is not derivable. Nor is the entity: it belongs to the API key the GPU job definition
    reads out of Secrets Manager, and no reviewed configuration in this repository names
    one. A plausible link that 404s is worse than the search instruction, because it reads
    as though somebody checked.
    """
    _result, summary = _run_where_it_went(tmp_path)

    assert "wandb.ai" not in summary
    assert "http" not in summary
    assert f"named** `{RUN_ID}`" in summary
    assert "Search the project for that name" in summary


def test_a_profile_this_checkout_cannot_resolve_is_reported_as_unknown(
    tmp_path: Path,
) -> None:
    """Mutation: index the binding and let a KeyError fail the step.

    The deployed validator resolves against the catalog inside its own release zip, and
    this step resolves against the checkout. They are usually the same file and they are
    not guaranteed to be, so a run can legitimately be accepted on a profile this tree has
    no target for. Failing here would report a submitted run as a broken workflow.
    """
    # gpu-1xa10g-sagemaker, because this test needs a profile config/execution-targets.yaml
    # does not name and it is the only one left. gpu-4xa10g gained a target when the nine GPU
    # shapes were promoted and gpu-1xl40s gained one when two teams asked for it.
    result, summary = _run_where_it_went(tmp_path, compute_profile="gpu-1xa10g-sagemaker")

    assert result.returncode == 0, result.stderr
    assert "| Batch job queue | `not resolvable from this checkout` |" in summary
    assert f"| Run id | `{RUN_ID}` |" in summary


def test_the_form_does_not_offer_a_fanout_parallelism_box_at_all() -> None:
    """Mutation: put the field back with a description saying it is not enforced.

    That was the previous state and this test asserted it. Batch's SubmitJob takes a size
    for an array job and no concurrency cap, so the value had nowhere to go and
    `batch_submit_request` correctly never sent it. Wording it carefully was not enough. A
    box on a form is read as a control whatever sits beside it, so a submitter filled it
    in, saw the same number echoed back on the approver page, and sized a fan-out against
    a cap that did not exist.

    The two remaining fan-out fields are asserted present in the same breath, because a
    form that had lost all three would pass an assertion about the absence of one and
    would refuse every sweep anybody tried to submit.
    """
    offered = _load()["on"]["workflow_dispatch"]["inputs"]

    assert "fanout_parallelism" not in offered
    assert {"fanout_size", "fanout_index_parameter"} <= set(offered)
