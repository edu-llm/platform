"""Structural tests for the Phase 2 submission workflow.

Every assertion here reads the parsed workflow and asks whether a reference resolves to
something that exists, or runs a ``run`` body against stubs and asks what it did. None of
them compares the text of an expression, because that is the check Phase 1 shipped: a
fully green suite over a workflow that could not complete a single run, in which
``${{ github.job_workflow_sha }}`` was as acceptable as a property GitHub defines.
"""

from __future__ import annotations

import ast
import itertools
import json
import re
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
from edullm_platform.contracts.admission import ApprovalEnvironment
from edullm_platform.contracts.image import GitHubWorkflowRunReference
from edullm_platform.submission import SubmissionInputs

#: How a dropdown spells 'leave this empty'. A choice option cannot be blank.
INHERIT_SENTINEL = "inherit"

WORKFLOW_FILE = ".github/workflows/submit-run.yml"
WORKFLOW_PATH = WORKFLOWS_ROOT / "submit-run.yml"
BUILD_WORKFLOW_PATH = WORKFLOWS_ROOT / "build-research-image.yml"
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
START_STEP = "Start the admission execution"
WAIT_STEP = "Wait for the admission decision"
DENY_STEP = "Attempt the admission role without an approval"
CANCELLED_STEP = "Record that a cancelled workflow stopped no compute"

DENIALS_TOOL = "tools/verify_admission_denials.py"
BATCH_DENIALS_TOOL = "tools/verify_batch_denials.py"

# Outputs no run body can be read for. The compile job's four come out of
# tools/compile_submission.py, and the test below re-derives them from that tool rather
# than trusting this tuple; aws-account-id is a documented output of the credentials
# action.
DECLARED_OUTPUTS = {
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

    This used to require the default be exactly empty, and that was the right invariant
    written too literally. A ``choice`` input cannot offer a blank option, so an override
    rendered as a dropdown has to spell absence as a word. ``inherit`` is that word, and it
    is only safe because the workflow translates it back to nothing before assembling the
    form -- which is asserted separately, because a sentinel that reached admission would be
    the name of a compute profile nothing has registered.
    """
    declared = _load()["on"]["workflow_dispatch"]["inputs"]
    optional = [name for name, field in SubmissionInputs.model_fields.items() if not field.is_required()]

    assert len(optional) == 6
    for name in optional:
        default = declared[name]["default"]
        assert default in ("", INHERIT_SENTINEL), name
        if default == INHERIT_SENTINEL:
            assert declared[name]["type"] == "choice", (
                f"{name} spells absence as a word without being a dropdown, which is the "
                "one situation that needs no sentinel at all"
            )


def test_the_three_jobs_carry_exactly_these_permission_maps() -> None:
    workflow = _load()

    assert list(workflow["jobs"]) == ["compile", "deny-unapproved", "submit"]
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
    assert "needs" not in workflow["jobs"]["compile"]
    assert "needs" not in workflow["jobs"]["deny-unapproved"]


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
        "needs.compile.outputs.environment",
        "needs.compile.outputs.manifest_sha256",
        "needs.compile.outputs.run_id",
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
    referenced = sorted({match for _name, script in _run_bodies() for match in TOOL_PATH_PATTERN.findall(script)})

    assert referenced == [
        "tools/compile_submission.py",
        DENIALS_TOOL,
        "tools/verify_approved_manifest.py",
        BATCH_DENIALS_TOOL,
    ]
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

    assert len(bodies) == 15
    for name, script in bodies:
        assert script.startswith("set -euo pipefail\n"), name


def _aws_reaching_calls() -> list[tuple[str, tuple[str, ...]]]:
    """Everything this workflow makes AWS answer, in the order the runner reaches it.

    A run body that calls the CLI directly contributes the call it makes. Each denial
    matrix contributes the actions it attempts, because they are made by a tool rather
    than by a shell and a reader of this file would otherwise see the submit job reach
    AWS twice when it reaches it a dozen times. Each matrix's own
    ``sts:get-caller-identity`` is left out: it requires no permission and cannot be
    denied by a policy, so it is not part of the surface this enumeration is about.
    """
    calls: list[tuple[str, tuple[str, ...]]] = []
    for name, script in _run_bodies():
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
    # Both sets of refused attempts are read out of the matrices the tools define, so
    # adding a probe or renaming an action changes this list rather than slipping past it.
    assert _aws_reaching_calls() == [
        (
            f"deny-unapproved:{DENY_STEP}",
            ("aws", "sts", "assume-role-with-web-identity"),
        ),
        *[(f"submit:{DENIALS_STEP}", ("denial-probe", action)) for action in ADMISSION_DENIED_ACTIONS],
        *[
            (f"submit:{BATCH_DENIALS_STEP}", ("denial-probe", action))
            for action in ADMISSION_BATCH_DENIED_ACTIONS
        ],
        (f"submit:{START_STEP}", ("aws", "stepfunctions", "start-execution")),
        (f"submit:{WAIT_STEP}", ("aws", "stepfunctions", "describe-execution")),
    ]


def test_both_denial_matrices_are_attempted_before_the_state_machine_is_started() -> None:
    # The ordering is the property, so it is computed from the step list rather than
    # assumed of it. Attempted after StartExecution a matrix would report on a role that
    # had already been used; attempted before the credentials step it would run under no
    # session at all. What they have to sit between is the moment the session is issued
    # and the moment it is spent.
    names = [candidate.get("name") for candidate in _job("submit")["steps"]]

    assert names.index(CREDENTIALS_STEP) < names.index(DENIALS_STEP) < names.index(START_STEP)
    assert names.index(DENIALS_STEP) < names.index(BATCH_DENIALS_STEP) < names.index(START_STEP)
    # And nothing else reaches AWS in between: the probes are the only thing this session
    # does before StartExecution, which is what makes them a statement about these
    # credentials rather than about the template they were issued from.
    reaching = [name for name, _call in _aws_reaching_calls()]
    assert reaching.index(f"submit:{START_STEP}") == len(reaching) - 2
    assert reaching[reaching.index(f"submit:{START_STEP}") - 1] == f"submit:{BATCH_DENIALS_STEP}"
    assert set(reaching[1:-2]) == {f"submit:{DENIALS_STEP}", f"submit:{BATCH_DENIALS_STEP}"}


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
    assert names.index("Publish the approver context") < names.index(
        "Upload the approver context"
    )


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
    assert [candidate.get("name") for candidate in submit["steps"] if "if" in candidate] == [
        CANCELLED_STEP
    ]


def test_the_cancellation_step_neither_claims_to_stop_a_job_nor_can() -> None:
    """Reads the workflow and the admission role. Mutation: give the role TerminateJob.

    The honest content of this step depends on a fact about the deployed role, so it is
    read rather than restated: the admission role holds no Batch action at all, which is
    why a cancelled workflow can record what is still running and cannot stop it. The day
    somebody grants ``batch:TerminateJob`` -- to build the cancellation path the plan
    describes -- this fails, and the prose has to be rewritten in the same change.
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
            [statement["Action"]]
            if isinstance(statement["Action"], str)
            else statement["Action"]
        )
    ]

    assert [action for action in granted if action.startswith("batch:")] == []
    assert "batch:TerminateJob" in cancelled["run"]
    assert "does not stop AWS compute" in cancelled["run"]
    assert "infra/README.md" in cancelled["run"]
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
    assert "Stopping a job a cancelled workflow left running" in written
    # The prose is prose. A shell that expanded something here would have swallowed the
    # backticks around the action name, which is how it would first be noticed.
    assert "`batch:TerminateJob`" in written


def test_the_runbook_documents_the_procedure_the_cancellation_step_points_at() -> None:
    """Reads BOTH files. Mutation: rename the section, or delete it.

    A notice that sends an operator to a heading nobody wrote is worse than no notice: it
    reads as though the procedure exists. The commands live there rather than in the
    workflow because every laptop procedure in this repository lives there, and because a
    literal ``aws`` line in a run body is indistinguishable from a call the job makes.
    """
    heading = "Stopping a job a cancelled workflow left running"
    runbook = (PROJECT_ROOT / "infra" / "README.md").read_text(encoding="utf-8")

    assert f"### {heading}" in runbook
    assert heading in step(_job("submit"), CANCELLED_STEP)["run"]
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
    "FORM_WORKLOAD_PROFILE": "dolma-tokenize-smoke",
    "FORM_DATASET_RELEASE": "dolma-2026-07",
    "FORM_TEAM": "data-prep",
    "FORM_WANDB_PROJECT": "dolma-tokenize",
    "FORM_COMMAND": "python -m dolma.tokenize --note 'two words'",
    "FORM_COMPUTE_PROFILE": "",
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
        "command",
    }


def test_the_assembled_form_carries_the_overrides_in_the_types_the_contract_demands(
    tmp_path: Path,
) -> None:
    # The bounds are whole numbers and the runtime bound is base-ten text. A runtime
    # bound sent as a JSON number would have gone through binary floating point, which
    # is not the value the approver read.
    result, payload = _run_form_assembly(
        tmp_path,
        FORM_COMPUTE_PROFILE="cpu-32vcpu",
        FORM_MAXIMUM_RUNTIME_HOURS="0.5",
        FORM_MAXIMUM_ATTEMPTS="2",
        FORM_FANOUT_SIZE="4",
        FORM_FANOUT_PARALLELISM="2",
        FORM_FANOUT_INDEX_PARAMETER="seed",
    )

    assert result.returncode == 0, result.stderr
    assert payload["maximum_runtime_hours"] == "0.5"
    assert isinstance(payload["maximum_attempts"], int)
    assert isinstance(payload["fanout_size"], int)
    assert isinstance(payload["fanout_parallelism"], int)
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
        f"An error occurred ({code}) when calling the AssumeRoleWithWebIdentity "
        f"operation: {detail}"
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
        ({"ADMISSION_ROLE_ARN": "sbsandbox-intern-edullm-admission"}, "admission_role_arn_malformed"),
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
}
REQUEST_ENVIRONMENT = {
    "APPROVED_SHA256": APPROVED_SHA256,
    "APPROVER": "team-lead",
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
    # The handler names its required fields; the approver is the one optional field it
    # reads, and this workflow always supplies it.
    assert set(request) == set(admission_handler._REQUIRED_EVENT_FIELDS) | {"approver"}
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
    ["WORKFLOW_REPOSITORY", "WORKFLOW_FILE_PATH", "WORKFLOW_REF", "APPROVER", "APPROVED_SHA256"],
)
def test_an_empty_job_workflow_identity_fails_closed(tmp_path: Path, variable: str) -> None:
    # The job-context workflow properties are documented as unavailable on GitHub
    # Enterprise Server, where they resolve to the empty string rather than failing.
    result, request = _run_request_assembly(tmp_path, **{variable: ""})

    assert result.returncode != 0
    assert variable in result.stderr
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
POLLING_AWS_STUB = """
counter_file="${RUNNER_TEMP}/poll-count.txt"
index=0
if [[ -f "${counter_file}" ]]; then
  index="$(cat "${counter_file}")"
fi
echo "$((index + 1))" > "${counter_file}"
read -r -a statuses <<< "${EXECUTION_STATUSES}"
if [[ "${index}" -lt "${#statuses[@]}" ]]; then
  echo "${statuses[${index}]}"
else
  echo "${statuses[$((${#statuses[@]} - 1))]}"
fi
"""


def _run_wait_step(
    tmp_path: Path,
    *,
    statuses: str,
    maximum_attempts: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], int]:
    wait_step = step(_job("submit"), WAIT_STEP)
    stub_bin = tmp_path / "bin"
    slept = tmp_path / "sleeps.txt"
    write_stub(stub_bin, "aws", POLLING_AWS_STUB)
    write_stub(stub_bin, "sleep", f'echo "$1" >> "{slept}"\n')
    result = run_step_script(
        wait_step["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "ADMISSION_ACCOUNT_ID": EXAMPLE_ACCOUNT_ID,
            "ADMISSION_REGION": "us-east-1",
            "RUN_ID": RUN_ID,
            "EXECUTION_STATUSES": statuses,
            "MAXIMUM_POLL_ATTEMPTS": maximum_attempts or wait_step["env"]["MAXIMUM_POLL_ATTEMPTS"],
            "POLL_INTERVAL_SECONDS": wait_step["env"]["POLL_INTERVAL_SECONDS"],
        },
        stub_bin=stub_bin,
    )
    sleeps = len(slept.read_text(encoding="utf-8").splitlines()) if slept.exists() else 0
    return result, sleeps


def test_the_job_waits_for_the_execution_to_leave_running(tmp_path: Path) -> None:
    # StartExecution answers as soon as the execution is created, so without this a
    # rejected submission would be indistinguishable from an accepted one.
    result, sleeps = _run_wait_step(tmp_path, statuses="RUNNING RUNNING SUCCEEDED")

    assert result.returncode == 0, result.stderr
    assert sleeps == 2
    assert "Admission accepted this submission" in result.stdout


@pytest.mark.parametrize("status", ["FAILED", "TIMED_OUT", "ABORTED"])
def test_an_execution_that_did_not_succeed_fails_the_job(tmp_path: Path, status: str) -> None:
    result, _sleeps = _run_wait_step(tmp_path, statuses=status)

    assert result.returncode == 1
    assert f"admission_execution_{status}" in result.stderr
    assert "recorded under decision/" in result.stderr


def test_the_poll_is_bounded_and_says_so_rather_than_claiming_a_decision(
    tmp_path: Path,
) -> None:
    result, sleeps = _run_wait_step(tmp_path, statuses="RUNNING", maximum_attempts="3")

    assert result.returncode == 1
    assert sleeps == 3
    assert "admission_execution_did_not_settle" in result.stderr
    assert "Nothing has been decided either way" in result.stderr
