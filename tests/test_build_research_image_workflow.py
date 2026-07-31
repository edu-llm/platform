from __future__ import annotations

import itertools
import json
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from workflow_support import (
    PROJECT_ROOT,
    WORKFLOWS_ROOT,
    aws_commands,
    command_tokens,
    load_workflow,
    run_step_script,
    step,
    unreal_context_references,
    write_stub,
)

from edullm_platform.contracts.image import SANDBOX_REGIONS

WORKFLOW_PATH = WORKFLOWS_ROOT / "build-research-image.yml"
CHECKOUT_ACTION = "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09"
CREDENTIALS_ACTION = (
    "aws-actions/configure-aws-credentials@e6de054238d6b7531b4efff3b6587d9aade6a06c"
)
PLATFORM_REPOSITORY = "edu-llm/platform"
WORKFLOW_PATH_INPUT = ".github/workflows/build-research-image.yml"
CONTRACT_STEP = "Verify the caller contract"
DENIAL_STEP = "Attempt the actions the publisher must not have"
DENIAL_TOOL = "verify_publisher_denials.py"
DENIAL_RECORD_FILE = "publisher-denials.json"
PREFLIGHT_STEP = "Look for an already published image"
RESUME_STEP = "Verify the published image is the one this run would have built"
DECREDENTIAL_STEP = "Remove the source checkout credentials"
BASE_GATE_STEP = "Require the registered base image"
DIGEST_STEP = "Read published digest from the registry"
DIGEST_SUMMARY_STEP = "Publish the digest where a person can copy it"
# Written by whichever of the build step and the resume step ran, read by provenance.
IMAGE_CREATED_FILE = "image-created.txt"
JOB_WORKFLOW_REF = f"{PLATFORM_REPOSITORY}/{WORKFLOW_PATH_INPUT}@refs/heads/main"
PUBLISHED_IMAGE_DIGEST = "sha256:" + "b" * 64
PUBLISHED_CONFIG_DIGEST = "sha256:" + "c" * 64
PUBLISHED_BASE_REFERENCE = "public.ecr.aws/example/base@sha256:" + "e" * 64
PUBLISHED_IMAGE_CREATED = "2026-01-04T03:02:01.026260339Z"
PRESIGNED_URL = "https://example.invalid/blob?X-Amz-Signature=deadbeefcafe"
# Outputs no run body can be read for. The two CLI steps are pinned on the other side by
# the tuples the CLI test modules assert against GITHUB_OUTPUT, so renaming an output in
# the tool fails there and renaming it here fails the expression checker.
DECLARED_OUTPUTS = {
    "identity": ("commit_sha", "ecr_repository"),
    "build_inputs": ("base_reference", "build_context", "dockerfile_path"),
    # Documented output of aws-actions/configure-aws-credentials.
    "credentials": ("aws-account-id",),
}


def _load() -> dict[str, Any]:
    return load_workflow(WORKFLOW_PATH)


def _jobs() -> dict[str, Any]:
    jobs = _load()["jobs"]
    assert isinstance(jobs, dict)
    return jobs


def _job(name: str) -> dict[str, Any]:
    return _jobs()[name]


def _run_bodies(workflow: dict[str, Any]) -> Iterator[tuple[str, str]]:
    for job_name, job in workflow["jobs"].items():
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


def test_workflow_file_name_matches_the_trusted_job_workflow_ref() -> None:
    # The publisher role trust policy pins job_workflow_ref to this exact path, so the
    # file name is a security control rather than a naming preference.
    assert WORKFLOW_PATH.is_file()
    assert f".github/workflows/{WORKFLOW_PATH.name}" == WORKFLOW_PATH_INPUT


def test_the_header_documents_what_a_caller_cannot_discover_from_the_call_site() -> None:
    # A called workflow can only downgrade permissions, and the trust policy matches
    # job_workflow_ref with StringEquals against @refs/heads/main. Both obligations fail
    # far from their cause: a missing id-token grant and a SHA-pinned `uses:` both surface
    # as an AssumeRole denial that reads like a broken role ARN. The third is not an
    # obligation but a trap: the gate sees full history and publish sees one commit, so a
    # Dockerfile deriving its version from the repository passes and then mis-versions.
    header = WORKFLOW_PATH.read_text(encoding="utf-8").split("\non:", 1)[0]

    assert "id-token: write" in header
    assert "@main" in header
    assert "job_workflow_ref" in header
    assert "fetch-depth" in header
    assert "setuptools_scm" in header
    assert "git describe" in header


def test_workflow_is_reusable_with_exact_inputs_and_no_secrets() -> None:
    workflow = _load()

    assert set(workflow["on"]) == {"workflow_call"}
    workflow_call = workflow["on"]["workflow_call"]
    assert "secrets" not in workflow_call
    assert workflow_call["inputs"] == {
        "repository": {
            "description": "Registry key of the research repository, such as OLMo-core.",
            "required": True,
            "type": "string",
        },
        "publisher_role_arn": {
            "description": "ARN of the ECR publisher role assumed through OIDC.",
            "required": True,
            "type": "string",
        },
        "aws_region": {
            "description": "Region hosting the registered ECR repository.",
            "required": False,
            "type": "string",
            "default": "us-east-1",
        },
        "test_command": {
            "description": "Optional shell command running the caller's own tests.",
            "required": False,
            "type": "string",
            "default": "",
        },
    }


def test_workflow_emits_the_published_image_digest_as_its_only_output() -> None:
    workflow_call = _load()["on"]["workflow_call"]

    assert set(workflow_call["outputs"]) == {"image_digest"}
    assert workflow_call["outputs"]["image_digest"]["value"] == (
        "${{ jobs.publish.outputs.image_digest }}"
    )
    assert _job("publish")["outputs"] == {
        "image_digest": "${{ steps.digest.outputs.image_digest }}"
    }


def test_workflow_has_exactly_three_ordered_jobs_with_exact_permission_maps() -> None:
    workflow = _load()

    assert list(workflow["jobs"]) == ["verify", "deny", "publish"]
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["verify"]["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["deny"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }
    assert workflow["jobs"]["publish"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }
    assert workflow["jobs"]["deny"]["needs"] == "verify"
    assert workflow["jobs"]["publish"]["needs"] == ["verify", "deny"]
    assert "needs" not in workflow["jobs"]["verify"]


def test_nothing_lets_the_publish_job_run_after_a_gate_has_failed() -> None:
    # `needs` alone only orders the jobs. A single `if: always()` on publish would keep
    # both dependencies and still run it after a rejected source identity or a publisher
    # role that turned out to be wider than its template.
    publish = _job("publish")

    assert "if" not in publish
    assert "if" not in _job("deny")
    assert publish["needs"] == ["verify", "deny"]
    assert "continue-on-error" not in publish
    assert "continue-on-error" not in str(_load())


def test_verify_job_never_requests_an_oidc_token_by_any_spelling() -> None:
    verify = _job("verify")

    for text in _strings(verify):
        normalized = text.lower().replace("_", "-")
        assert "id-token" not in normalized, f"verify job must stay credential free: {text!r}"
    assert set(verify["permissions"]) == {"contents"}
    assert CREDENTIALS_ACTION not in set(_strings(verify))
    assert "configure-aws-credentials" not in str(verify)


def test_workflow_uses_only_the_two_approved_commit_pinned_actions() -> None:
    workflow = _load()
    used = [
        candidate["uses"]
        for job in workflow["jobs"].values()
        for candidate in job["steps"]
        if "uses" in candidate
    ]

    assert set(used) == {CHECKOUT_ACTION, CREDENTIALS_ACTION}
    # Twice: the denial matrix and the publish job each assume the publisher role on
    # their own runner, because neither can hand a session to the other.
    assert used.count(CREDENTIALS_ACTION) == 2
    for reference in used:
        assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", reference), reference


def test_both_jobs_check_out_the_caller_and_the_pinned_reusable_workflow_commit() -> None:
    for job_name, expected_ref in (
        ("verify", "${{ github.sha }}"),
        ("publish", "${{ needs.verify.outputs.commit_sha }}"),
    ):
        job = _job(job_name)
        caller = step(job, "Check out research repository")
        platform = step(job, "Check out platform tooling")

        assert caller["uses"] == CHECKOUT_ACTION
        assert "repository" not in caller["with"]
        assert caller["with"]["ref"] == expected_ref
        assert caller["with"]["path"] == "source"

        assert platform["uses"] == CHECKOUT_ACTION
        assert platform["with"]["repository"] == "${{ job.workflow_repository }}"
        assert platform["with"]["ref"] == "${{ job.workflow_sha }}"
        assert platform["with"]["path"] == "platform"
        assert platform["with"]["persist-credentials"] is False
        assert platform["with"]["path"] != caller["with"]["path"]
        assert "fetch-depth" not in platform["with"]


def test_only_the_checkout_handed_to_the_caller_s_own_tests_carries_history() -> None:
    # Verification needs `git status`, `git rev-parse HEAD`, and `git ls-remote`, none of
    # which need history. The gate keeps it anyway because that checkout is also where the
    # caller's test command runs, and version derivation from `git describe` is ordinary.
    # The publish checkout is only re-verified and then handed to `docker build` with a
    # `.` context, where a full .git is weight a layer can copy.
    assert step(_job("verify"), "Check out research repository")["with"]["fetch-depth"] == 0
    assert "fetch-depth" not in step(_job("publish"), "Check out research repository")["with"]


def test_every_expression_names_something_that_actually_exists() -> None:
    # Asserting literal expression strings cannot tell a real property from a plausible
    # typo, because GitHub resolves an unknown property to the empty string rather than
    # failing. That is how `github.job_workflow_sha` survived a green suite while making
    # every run of this workflow impossible to complete.
    assert unreal_context_references(WORKFLOW_PATH, declared_step_outputs=DECLARED_OUTPUTS) == []


def test_the_workflow_never_reaches_for_a_job_workflow_property_of_the_github_context() -> None:
    # `github.job_workflow_sha` does not exist. An unknown property resolves to the empty
    # string rather than failing, which pinned both tooling checkouts to the default
    # branch and passed an empty --workflow-ref to provenance. `github.workflow_sha` does
    # exist but describes the *caller's* workflow, so it is a worse near miss than a typo.
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    expressions = set(re.findall(r"\$\{\{\s*(.+?)\s*\}\}", text))

    assert {name for name in expressions if "workflow_" in name} == {
        "job.workflow_repository",
        "job.workflow_sha",
        "job.workflow_ref",
        "job.workflow_file_path",
    }
    assert "github.job_workflow_sha" not in text
    assert "github.workflow_sha" not in text
    assert "github.workflow_ref" not in text


def test_every_job_checks_the_caller_contract_before_anything_else() -> None:
    # The four job-context workflow identity properties are documented as unavailable on
    # GitHub Enterprise Server, where they resolve to the empty string instead of failing.
    # Empty is the exact silent-degradation mode this guard exists to stop, so it runs
    # before the tooling checkout that consumes them and before any AWS call.
    scripts = set()
    for job_name in ("verify", "deny", "publish"):
        steps = _job(job_name)["steps"]

        assert steps[0]["name"] == CONTRACT_STEP
        contract = steps[0]
        assert contract["env"]["WORKFLOW_REPOSITORY"] == "${{ job.workflow_repository }}"
        assert contract["env"]["WORKFLOW_SHA"] == "${{ job.workflow_sha }}"
        assert contract["env"]["WORKFLOW_REF"] == "${{ job.workflow_ref }}"
        assert contract["env"]["WORKFLOW_FILE_PATH"] == "${{ job.workflow_file_path }}"
        assert contract["env"]["EVENT_NAME"] == "${{ github.event_name }}"
        assert contract["env"]["AWS_REGION"] == "${{ inputs.aws_region }}"
        scripts.add(contract["run"])

    assert len(scripts) == 1, "every job must enforce the identical caller contract"


def _contract_environment(**overrides: str) -> dict[str, str]:
    environment = {
        "WORKFLOW_REPOSITORY": PLATFORM_REPOSITORY,
        "WORKFLOW_SHA": "c" * 40,
        "WORKFLOW_REF": JOB_WORKFLOW_REF,
        "WORKFLOW_FILE_PATH": WORKFLOW_PATH_INPUT,
        "EVENT_NAME": "push",
        "AWS_REGION": "us-east-1",
    }
    environment.update(overrides)
    return environment


@pytest.mark.slow
def test_the_caller_contract_admits_a_complete_job_workflow_identity(tmp_path: Path) -> None:
    contract = step(_job("publish"), CONTRACT_STEP)

    result = run_step_script(contract["run"], cwd=tmp_path, env=_contract_environment())

    assert result.returncode == 0, result.stderr


@pytest.mark.slow
def test_an_empty_job_workflow_identity_fails_closed_with_a_clear_message(
    tmp_path: Path,
) -> None:
    contract = step(_job("publish"), CONTRACT_STEP)

    for name in ("WORKFLOW_REPOSITORY", "WORKFLOW_SHA", "WORKFLOW_REF", "WORKFLOW_FILE_PATH"):
        result = run_step_script(
            contract["run"],
            cwd=tmp_path,
            env=_contract_environment(**{name: ""}),
        )

        assert result.returncode == 1, name
        assert name in result.stderr
        assert "GitHub Enterprise Server" in result.stderr


@pytest.mark.slow
def test_a_job_workflow_ref_without_a_ref_suffix_fails_closed(tmp_path: Path) -> None:
    contract = step(_job("publish"), CONTRACT_STEP)

    result = run_step_script(
        contract["run"],
        cwd=tmp_path,
        env=_contract_environment(WORKFLOW_REF=PLATFORM_REPOSITORY),
    )

    assert result.returncode == 1
    assert "owner/repo/path@ref" in result.stderr


@pytest.mark.slow
@pytest.mark.parametrize("event_name", ["push", "workflow_dispatch"])
def test_the_two_events_that_name_a_reviewed_commit_are_accepted(
    tmp_path: Path,
    event_name: str,
) -> None:
    contract = step(_job("publish"), CONTRACT_STEP)

    result = run_step_script(
        contract["run"],
        cwd=tmp_path,
        env=_contract_environment(EVENT_NAME=event_name),
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.slow
@pytest.mark.parametrize("event_name", ["schedule", "issue_comment", "issues", "release"])
def test_events_whose_github_ref_is_merely_the_default_branch_are_rejected(
    tmp_path: Path,
    event_name: str,
) -> None:
    # On these events github.ref is the default branch rather than a reviewed push, so
    # they would pass the branch gate and mint a `sub` the trust policy accepts.
    contract = step(_job("publish"), CONTRACT_STEP)

    result = run_step_script(
        contract["run"],
        cwd=tmp_path,
        env=_contract_environment(EVENT_NAME=event_name),
    )

    assert result.returncode == 1
    assert "unsupported_caller_event" in result.stderr
    assert event_name in result.stderr


@pytest.mark.slow
@pytest.mark.parametrize("region", sorted(SANDBOX_REGIONS))
def test_the_allowed_sandbox_regions_are_accepted(tmp_path: Path, region: str) -> None:
    contract = step(_job("publish"), CONTRACT_STEP)

    result = run_step_script(
        contract["run"],
        cwd=tmp_path,
        env=_contract_environment(AWS_REGION=region),
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.slow
def test_a_region_outside_the_sandbox_fails_before_it_becomes_an_access_denied(
    tmp_path: Path,
) -> None:
    contract = step(_job("publish"), CONTRACT_STEP)

    result = run_step_script(
        contract["run"],
        cwd=tmp_path,
        env=_contract_environment(AWS_REGION="eu-west-1"),
    )

    assert result.returncode == 1
    assert "unsupported_aws_region" in result.stderr
    assert "eu-west-1" in result.stderr


def test_the_workflow_and_the_contract_model_agree_on_the_allowed_regions() -> None:
    # resolve_image_reference rejects anything outside SANDBOX_REGIONS, so a workflow that
    # accepted a third region would build an image whose reference cannot be composed.
    script = step(_job("publish"), CONTRACT_STEP)["run"]
    allowed = re.search(
        r"^\s*([a-z0-9|-]+)\)\s*;;\s*# allowed sandbox regions$", script, re.MULTILINE
    )

    assert allowed is not None, script
    assert set(allowed.group(1).split("|")) == set(SANDBOX_REGIONS)


def test_verify_job_runs_caller_tests_only_when_requested_and_only_through_env() -> None:
    tests = step(_job("verify"), "Run research repository tests")

    assert tests["if"] == "inputs.test_command != ''"
    assert tests["working-directory"] == "source"
    assert tests["env"] == {"TEST_COMMAND": "${{ inputs.test_command }}"}
    assert "${{" not in tests["run"]
    assert "${TEST_COMMAND}" in tests["run"]


@pytest.mark.slow
@pytest.mark.parametrize(
    ("test_command", "expected_status"),
    [
        ("true; true", 0),
        ("false; true", 1),
        ("true; false", 1),
        ("false | cat", 1),
        ("echo one && echo two", 0),
    ],
)
def test_a_compound_test_command_reports_the_first_failure_not_the_last_status(
    tmp_path: Path,
    test_command: str,
    expected_status: int,
) -> None:
    # The step body runs under `set -euo pipefail`, but the child shell that runs the
    # caller's command is a fresh bash and inherits none of it, so `false; true` would
    # have been reported as a pass.
    script = step(_job("verify"), "Run research repository tests")["run"]

    result = run_step_script(script, cwd=tmp_path, env={"TEST_COMMAND": test_command})

    assert result.returncode == expected_status, result.stderr


def test_the_source_checkout_keeps_its_token_only_until_the_remote_check_is_done() -> None:
    # The re-verification step's `git ls-remote` needs the checkout credentials, so they
    # cannot simply be turned off. build_context is `.`, so a Dockerfile with `COPY . .`
    # would otherwise bake source/.git/config, and the token in it, into a published layer.
    publish = _job("publish")
    names = [candidate.get("name") for candidate in publish["steps"]]
    removal = step(publish, DECREDENTIAL_STEP)

    assert names.index("Re-verify source identity") < names.index(DECREDENTIAL_STEP)
    assert names.index(DECREDENTIAL_STEP) < names.index("Build and push image")
    assert removal["working-directory"] == "source"

    for job_name in ("verify", "publish"):
        caller = step(_job(job_name), "Check out research repository")
        platform = step(_job(job_name), "Check out platform tooling")

        assert "persist-credentials" not in caller["with"]
        assert platform["with"]["persist-credentials"] is False


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout


def _checkout_with_extraheader(tmp_path: Path, *extra_hosts: str) -> Path:
    repository = tmp_path / "source"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    for host in ("https://github.com/", *extra_hosts):
        _git(repository, "config", "--local", f"http.{host}.extraheader", "AUTHORIZATION: basic X")
    return repository


@pytest.mark.slow
def test_the_checkout_token_is_gone_before_anything_can_copy_it(tmp_path: Path) -> None:
    repository = _checkout_with_extraheader(tmp_path)
    script = step(_job("publish"), DECREDENTIAL_STEP)["run"]

    result = run_step_script(
        script,
        cwd=repository,
        env={"RUNNER_TEMP": str(tmp_path), "HOME": str(tmp_path)},
    )

    assert result.returncode == 0, result.stderr
    assert "extraheader" not in (repository / ".git" / "config").read_text(encoding="utf-8")


@pytest.mark.slow
def test_an_extraheader_this_step_does_not_know_how_to_remove_fails_closed(
    tmp_path: Path,
) -> None:
    repository = _checkout_with_extraheader(tmp_path, "https://ghe.example.invalid/")
    script = step(_job("publish"), DECREDENTIAL_STEP)["run"]

    result = run_step_script(
        script,
        cwd=repository,
        env={"RUNNER_TEMP": str(tmp_path), "HOME": str(tmp_path)},
    )

    assert result.returncode == 1
    assert "source_credentials_remain" in result.stderr


def test_verify_job_exposes_the_verified_commit_and_ecr_repository() -> None:
    verify = _job("verify")

    assert verify["outputs"] == {
        "commit_sha": "${{ steps.identity.outputs.commit_sha }}",
        "ecr_repository": "${{ steps.identity.outputs.ecr_repository }}",
    }
    identity = step(verify, "Verify source identity")
    assert identity["id"] == "identity"
    assert "verify_source_identity.py" in identity["run"]
    assert '--github-output "${GITHUB_OUTPUT}"' in identity["run"]

    # The gate does not write a SourceIdentity document. It runs on its own runner and the
    # publish job re-derives the document there, so a file written here is never read.
    assert "--output" not in identity["run"]
    assert "--output" in step(_job("publish"), "Re-verify source identity")["run"]


def test_the_denial_matrix_stands_between_the_gate_and_the_publish() -> None:
    # The plan called for a workflow of its own, and it cannot be one: the publisher role
    # trust policy pins job_workflow_ref to this file with StringEquals, so no other
    # workflow file can assume the role, and widening the trust to a second file would
    # let that file push images without the gate, the clean-tree check, or the
    # registered-base enforcement. job_workflow_ref names the file rather than the job,
    # so a job here needs no trust change at all.
    #
    # It runs before publish rather than after it because a role that has been widened
    # should stop a publish, not be discovered once an image is already in the registry.
    workflow = _load()

    assert list(workflow["jobs"]).index("deny") < list(workflow["jobs"]).index("publish")
    assert _job("deny")["needs"] == "verify"
    assert "deny" in _job("publish")["needs"]


def test_the_denial_matrix_is_a_session_a_tool_and_nothing_else() -> None:
    # The token is the only permission it gains, and the five steps are the whole job:
    # anything else added here would run beside a publisher session.
    deny = _job("deny")

    assert deny["permissions"] == {"contents": "read", "id-token": "write"}
    assert "outputs" not in deny
    assert [candidate["name"] for candidate in deny["steps"]] == [
        CONTRACT_STEP,
        "Check out platform tooling",
        "Install platform tooling",
        "Configure AWS credentials",
        DENIAL_STEP,
    ]


def test_the_denial_matrix_never_checks_out_the_code_it_holds_credentials_beside() -> None:
    # This job assumes the publisher role and has no reason to read the caller's
    # repository, so it does not. The publish job needs a build context; this one needs
    # the platform tooling and a session.
    deny = _job("deny")
    checkouts = [
        candidate for candidate in deny["steps"] if candidate.get("uses") == CHECKOUT_ACTION
    ]

    assert len(checkouts) == 1
    assert checkouts[0]["with"] == {
        "repository": "${{ job.workflow_repository }}",
        "ref": "${{ job.workflow_sha }}",
        "path": "platform",
        "persist-credentials": False,
    }


def test_the_denial_matrix_assumes_the_publisher_role_for_no_longer_than_it_needs() -> None:
    credentials = step(_job("deny"), "Configure AWS credentials")

    assert credentials["uses"] == CREDENTIALS_ACTION
    assert credentials["with"] == {
        "role-to-assume": "${{ inputs.publisher_role_arn }}",
        "aws-region": "${{ inputs.aws_region }}",
        # Six calls and a lockfile install. The publisher role's maximum is an hour and
        # this job has no use for one, so it asks for the shortest session STS issues.
        "role-duration-seconds": 900,
        "mask-aws-account-id": True,
    }


def test_the_denial_matrix_reaches_aws_only_through_the_tool_that_can_judge_a_refusal() -> None:
    # A run body can make the call but cannot tell a refusal from a not-found without
    # reading the error, and reading it in the shell means it is one `echo` away from a
    # world-readable log. So there is no `aws` word in this job at all.
    deny = _job("deny")
    attempt = step(deny, DENIAL_STEP)

    assert aws_commands(attempt["run"]) == []
    assert [name for name, script in _run_bodies(_load()) if aws_commands(script)] == [
        "publish:Look for an already published image",
        f"publish:{RESUME_STEP}",
        "publish:Log in to Amazon ECR",
        "publish:Read published digest from the registry",
    ]
    assert DENIAL_TOOL in attempt["run"]
    assert attempt["env"] == {
        "RESEARCH_REPOSITORY": "${{ inputs.repository }}",
        "AWS_REGION": "${{ inputs.aws_region }}",
    }


def _run_denial_step(
    tmp_path: Path,
    *,
    uv_body: str,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    stub_bin = tmp_path / "bin"
    recorded = tmp_path / "argv.txt"
    write_stub(stub_bin, "uv", f'printf "%s\\n" "$@" > "{recorded}"\n{uv_body}')
    result = run_step_script(
        step(_job("deny"), DENIAL_STEP)["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "RESEARCH_REPOSITORY": "OLMo-core",
            "AWS_REGION": "us-east-1",
        },
        stub_bin=stub_bin,
    )
    arguments = recorded.read_text(encoding="utf-8").splitlines() if recorded.exists() else []
    return result, arguments


@pytest.mark.slow
def test_a_matrix_that_refused_everything_prints_the_record_it_wrote(tmp_path: Path) -> None:
    result, arguments = _run_denial_step(
        tmp_path,
        uv_body=f'printf \'{{"schema_version":1}}\' > "${{RUNNER_TEMP}}/{DENIAL_RECORD_FILE}"\n',
    )

    assert result.returncode == 0, result.stderr
    assert '{"schema_version":1}' in result.stdout
    passed = dict(itertools.pairwise(arguments))
    assert passed["--registry"] == "config/repositories.yaml"
    assert passed["--repository"] == "OLMo-core"
    assert passed["--region"] == "us-east-1"
    assert passed["--output"] == str(tmp_path / DENIAL_RECORD_FILE)


@pytest.mark.slow
def test_a_matrix_that_could_not_prove_a_denial_prints_no_record(tmp_path: Path) -> None:
    # The tool writes nothing when an attempt establishes nothing, and the step must not
    # print a record left over from an earlier attempt on the same runner either.
    (tmp_path / DENIAL_RECORD_FILE).write_text("stale-record-canary", encoding="utf-8")

    result, _ = _run_denial_step(
        tmp_path,
        uv_body='echo "attempt_permitted:iam:CreateRole" >&2\nexit 1\n',
    )

    assert result.returncode == 1
    assert "attempt_permitted:iam:CreateRole" in result.stderr
    assert "stale-record-canary" not in result.stdout


def test_publish_job_logs_in_to_ecr_without_a_third_party_login_action() -> None:
    login = step(_job("publish"), "Log in to Amazon ECR")

    assert command_tokens(login["run"], "ecr", "get-login-password") == [
        "aws",
        "ecr",
        "get-login-password",
        "|",
        "docker",
        "login",
        "--username",
        "AWS",
        "--password-stdin",
        "${registry}",
    ]
    assert "docker/login-action" not in str(_load())


def test_the_registered_base_is_enforced_on_the_dockerfile_before_the_build() -> None:
    # BASE_IMAGE reaches docker build as a --build-arg, which a Dockerfile is free to
    # ignore by hardcoding its own FROM. Phase 1 evidence capture records
    # base_image_digest from the registry either way, so without this gate a committed
    # record would assert something nothing verified, which is worse than omitting it.
    publish = _job("publish")
    names = [candidate.get("name") for candidate in publish["steps"]]
    gate = step(publish, BASE_GATE_STEP)

    assert names.index(BASE_GATE_STEP) < names.index("Configure AWS credentials")
    assert names.index(BASE_GATE_STEP) < names.index("Build and push image")
    assert "verify_dockerfile_base.py" in gate["run"]
    assert gate["env"] == {"RESEARCH_REPOSITORY": "${{ inputs.repository }}"}
    assert '--repository-root "${GITHUB_WORKSPACE}/source"' in gate["run"]


SKIP_CONDITION = "steps.preflight.outputs.image_digest == ''"


def test_an_already_published_tag_skips_the_build_and_the_push() -> None:
    # ECR tags are immutable and the push happens before the read-back, so any failure
    # after the push would make that commit unpublishable forever: the run-URL label
    # guarantees a different manifest digest on the retry, and the tag cannot be
    # rewritten. A pre-flight lookup makes a re-run resume instead of collide.
    publish = _job("publish")
    names = [candidate.get("name") for candidate in publish["steps"]]
    preflight = step(publish, PREFLIGHT_STEP)

    assert preflight["id"] == "preflight"
    assert names.index("Configure AWS credentials") < names.index(PREFLIGHT_STEP)
    assert names.index(PREFLIGHT_STEP) < names.index("Log in to Amazon ECR")
    assert step(publish, "Log in to Amazon ECR")["if"] == SKIP_CONDITION
    assert step(publish, "Build and push image")["if"] == SKIP_CONDITION

    # The read-back is never skipped: the digest that leaves this workflow is the one the
    # registry reports on this run, whether or not this run is what put it there.
    assert "if" not in step(publish, "Read published digest from the registry")
    assert preflight["env"]["COMMIT_SHA"] == "${{ needs.verify.outputs.commit_sha }}"
    assert preflight["env"]["ECR_REPOSITORY"] == "${{ needs.verify.outputs.ecr_repository }}"


def _preflight_environment(tmp_path: Path) -> dict[str, str]:
    return {
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(tmp_path / "step-output.txt"),
        "COMMIT_SHA": "a" * 40,
        "ECR_REPOSITORY": "sbsandbox-intern-edullm-olmo-core",
    }


def _run_preflight(tmp_path: Path, aws_stub: str) -> tuple[Any, dict[str, str]]:
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "aws", aws_stub)
    result = run_step_script(
        step(_job("publish"), PREFLIGHT_STEP)["run"],
        cwd=tmp_path,
        env=_preflight_environment(tmp_path),
        stub_bin=stub_bin,
    )
    lines = (tmp_path / "step-output.txt").read_text(encoding="utf-8").splitlines()
    return result, dict(line.split("=", 1) for line in lines)


@pytest.mark.slow
def test_a_missing_tag_leaves_the_build_to_run(tmp_path: Path) -> None:
    result, outputs = _run_preflight(
        tmp_path,
        'echo "An error occurred (ImageNotFoundException) when calling the DescribeImages'
        ' operation: The image with imageId {"imageTag":"aaaaaaaaaaaa"} does not exist" >&2\n'
        "exit 254\n",
    )

    assert result.returncode == 0, result.stderr
    assert outputs == {"image_tag": "a" * 12}
    assert "not published yet" in result.stdout


@pytest.mark.slow
def test_a_published_tag_short_circuits_to_the_digest_the_registry_already_holds(
    tmp_path: Path,
) -> None:
    digest = "sha256:" + "b" * 64
    result, outputs = _run_preflight(tmp_path, f'echo "{digest}"\n')

    assert result.returncode == 0, result.stderr
    assert outputs == {"image_tag": "a" * 12, "image_digest": digest}
    assert "already published" in result.stdout
    assert "Skipping build and push" in result.stdout


@pytest.mark.slow
def test_a_lookup_that_fails_for_any_other_reason_fails_closed(tmp_path: Path) -> None:
    # DescribeImages names the registry id in its error text, so a failed lookup must not
    # be echoed and must not be mistaken for an absent image.
    result, outputs = _run_preflight(
        tmp_path,
        'echo "An error occurred (AccessDeniedException) when calling the DescribeImages'
        ' operation: registry 123456789012" >&2\nexit 254\n',
    )

    assert result.returncode == 1
    assert outputs == {"image_tag": "a" * 12}
    assert "preflight_lookup_failed" in result.stderr
    assert "123456789012" not in result.stderr + result.stdout


@pytest.mark.slow
def test_a_lookup_that_answers_with_a_non_digest_fails_closed(tmp_path: Path) -> None:
    result, _ = _run_preflight(tmp_path, 'echo "None"\n')

    assert result.returncode == 1
    assert "preflight_digest_unreadable" in result.stderr


def test_a_resumed_run_proves_the_published_image_is_the_one_it_would_have_built() -> None:
    # The tag encodes only the commit, but the provenance record re-derives the rest at
    # write time: base_image_digest is read from config/repositories.yaml as it stands
    # now. Resuming an older commit after the registered base digest changed would record
    # the new base for an image built from the old one, which is the claim
    # verify_dockerfile_base.py exists to prevent arriving by another road.
    publish = _job("publish")
    names = [candidate.get("name") for candidate in publish["steps"]]
    resume = step(publish, RESUME_STEP)

    assert resume["if"] == "steps.preflight.outputs.image_digest != ''"
    assert names.index(PREFLIGHT_STEP) < names.index(RESUME_STEP)
    assert names.index(RESUME_STEP) < names.index("Log in to Amazon ECR")
    assert resume["env"] == {
        "ECR_REPOSITORY": "${{ needs.verify.outputs.ecr_repository }}",
        "IMAGE_DIGEST": "${{ steps.preflight.outputs.image_digest }}",
        "BASE_REFERENCE": "${{ steps.build_inputs.outputs.base_reference }}",
        "COMMIT_SHA": "${{ needs.verify.outputs.commit_sha }}",
    }


def _published_manifest() -> str:
    return json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "config": {
                "mediaType": "application/vnd.docker.container.image.v1+json",
                "size": 7023,
                "digest": PUBLISHED_CONFIG_DIGEST,
            },
            "layers": [],
        }
    )


def _published_config(base_name: str, revision: str) -> str:
    return json.dumps(
        {
            "created": PUBLISHED_IMAGE_CREATED,
            "config": {
                "Labels": {
                    "org.opencontainers.image.base.name": base_name,
                    "org.opencontainers.image.revision": revision,
                    "edullm.workflow.run.url": "https://example.invalid/runs/1",
                }
            },
        }
    )


def _run_resume_check(
    tmp_path: Path,
    *,
    base_name: str = PUBLISHED_BASE_REFERENCE,
    revision: str = "a" * 40,
    download_status: int = 0,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    # `uv` is stubbed only to drop the locked-environment wrapper: the stub execs the real
    # CLI, so both branches below run the tooling the workflow runs rather than a
    # reimplementation of it. `aws` and `curl` are the only things genuinely faked.
    platform_directory = tmp_path / "platform"
    platform_directory.mkdir()
    (platform_directory / "tools").symlink_to(PROJECT_ROOT / "tools")
    (tmp_path / "manifest.json").write_text(_published_manifest(), encoding="utf-8")
    (tmp_path / "config.json").write_text(_published_config(base_name, revision), encoding="utf-8")

    stub_bin = tmp_path / "bin"
    recording = tmp_path / "aws-calls.txt"
    write_stub(
        stub_bin,
        "aws",
        f'printf "%s\\n" "$*" >> "{recording}"\n'
        f'case "${{1-}} ${{2-}}" in\n'
        f'  "ecr batch-get-image") cat "{tmp_path / "manifest.json"}" ;;\n'
        f'  "ecr get-download-url-for-layer") echo "{PRESIGNED_URL}" ;;\n'
        f"  *) exit 64 ;;\n"
        f"esac\n",
    )
    write_stub(
        stub_bin,
        "curl",
        'destination=""\n'
        "while [[ $# -gt 0 ]]; do\n"
        '  if [[ "$1" == "--output" ]]; then shift; destination="$1"; fi\n'
        "  shift\n"
        "done\n"
        f"if [[ {download_status} -ne 0 ]]; then\n"
        f'  echo "curl: (22) The requested URL returned error: 403 for {PRESIGNED_URL}" >&2\n'
        f"  exit {download_status}\n"
        "fi\n"
        f'cat "{tmp_path / "config.json"}" > "${{destination}}"\n',
    )
    write_stub(stub_bin, "uv", 'shift 3\nexec "${PYTHON_EXECUTABLE}" "$@"\n')

    result = run_step_script(
        step(_job("publish"), RESUME_STEP)["run"],
        cwd=platform_directory,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "PYTHON_EXECUTABLE": sys.executable,
            "ECR_REPOSITORY": "sbsandbox-intern-edullm-olmo-core",
            "IMAGE_DIGEST": PUBLISHED_IMAGE_DIGEST,
            "BASE_REFERENCE": PUBLISHED_BASE_REFERENCE,
            "COMMIT_SHA": "a" * 40,
        },
        stub_bin=stub_bin,
    )
    recorded = recording.read_text(encoding="utf-8").splitlines() if recording.exists() else []
    return result, recorded


@pytest.mark.slow
def test_a_published_image_that_matches_this_build_lets_the_run_resume(tmp_path: Path) -> None:
    result, calls = _run_resume_check(tmp_path)

    assert result.returncode == 0, result.stderr
    # The manifest is asked for by the digest the pre-flight read, not by the tag, and the
    # config blob is asked for by the digest that manifest names.
    assert f"imageDigest={PUBLISHED_IMAGE_DIGEST}" in calls[0]
    assert calls[0].startswith("ecr batch-get-image ")
    assert calls[1] == (
        "ecr get-download-url-for-layer --repository-name sbsandbox-intern-edullm-olmo-core"
        f" --layer-digest {PUBLISHED_CONFIG_DIGEST} --query downloadUrl --output text"
    )


@pytest.mark.slow
def test_a_resumed_run_takes_the_build_time_from_the_image_it_resumed_onto(
    tmp_path: Path,
) -> None:
    # The image was built whenever it was built. This run only reads it back, so the only
    # honest built_at is the one the image itself records.
    result, _ = _run_resume_check(tmp_path)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / IMAGE_CREATED_FILE).read_text(encoding="utf-8") == (
        f"{PUBLISHED_IMAGE_CREATED}\n"
    )


@pytest.mark.slow
def test_a_published_image_built_from_another_base_stops_the_run(tmp_path: Path) -> None:
    result, _ = _run_resume_check(
        tmp_path,
        base_name="public.ecr.aws/example/base@sha256:" + "0" * 64,
    )

    assert result.returncode == 1
    assert "published_base_image_mismatch" in result.stderr


@pytest.mark.slow
def test_a_published_image_built_from_another_commit_stops_the_run(tmp_path: Path) -> None:
    # Twelve hex characters of a commit can collide. Before the resume path existed the
    # immutable push rejected a collision loudly; now only the revision label does.
    result, _ = _run_resume_check(tmp_path, revision="b" * 40)

    assert result.returncode == 1
    assert "published_revision_mismatch" in result.stderr


@pytest.mark.slow
def test_a_config_blob_that_cannot_be_fetched_never_echoes_the_presigned_url(
    tmp_path: Path,
) -> None:
    # The download URL carries an S3 signature, which mask-aws-account-id cannot redact
    # because it is not the account id, so curl's own diagnostics have to stay unprinted.
    result, _ = _run_resume_check(tmp_path, download_status=22)

    assert result.returncode == 1
    assert "published_config_unreachable" in result.stderr
    assert "X-Amz-Signature" not in result.stderr + result.stdout


@pytest.mark.slow
def test_the_build_records_the_images_own_creation_time_before_it_pushes(
    tmp_path: Path,
) -> None:
    # built_at is a claim about the image, so it is read out of the image rather than off
    # the clock. Reading it before the push keeps a failure here cheap: the immutable tag
    # is not published yet, so the commit stays publishable.
    script = step(_job("publish"), "Build and push image")["run"]
    stub_bin = tmp_path / "bin"
    write_stub(
        stub_bin,
        "docker",
        'if [[ "${1-} ${2-}" == "image inspect" ]]; then\n'
        f'  echo "{PUBLISHED_IMAGE_CREATED}"\n'
        "fi\n",
    )

    result = run_step_script(
        script,
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "REGISTRY": "123456789012.dkr.ecr.us-east-1.amazonaws.com",
            "ECR_REPOSITORY": "sbsandbox-intern-edullm-olmo-core",
            "IMAGE_TAG": "a" * 12,
            "BASE_REFERENCE": PUBLISHED_BASE_REFERENCE,
            "DOCKERFILE_PATH": ".edullm/Dockerfile",
            "BUILD_CONTEXT": ".",
            "COMMIT_SHA": "a" * 40,
            "SOURCE_URL": "https://example.invalid/edu-llm/OLMo-core",
            "RUN_URL": "https://example.invalid/runs/1",
        },
        stub_bin=stub_bin,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / IMAGE_CREATED_FILE).read_text(encoding="utf-8") == (
        f"{PUBLISHED_IMAGE_CREATED}\n"
    )
    assert script.index("docker image inspect") < script.index("docker push")


@pytest.mark.slow
def test_an_image_that_cannot_say_when_it_was_built_stops_before_the_push(
    tmp_path: Path,
) -> None:
    # A build whose stages add no layer of their own can leave `created` unset. Recording
    # the clock instead is the substitution this whole path exists to remove.
    script = step(_job("publish"), "Build and push image")["run"]
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "docker", 'printf "%s\\n" "$*" >> "${RUNNER_TEMP}/docker-calls.txt"\n')

    result = run_step_script(
        script,
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "REGISTRY": "123456789012.dkr.ecr.us-east-1.amazonaws.com",
            "ECR_REPOSITORY": "sbsandbox-intern-edullm-olmo-core",
            "IMAGE_TAG": "a" * 12,
            "BASE_REFERENCE": PUBLISHED_BASE_REFERENCE,
            "DOCKERFILE_PATH": ".edullm/Dockerfile",
            "BUILD_CONTEXT": ".",
            "COMMIT_SHA": "a" * 40,
            "SOURCE_URL": "https://example.invalid/edu-llm/OLMo-core",
            "RUN_URL": "https://example.invalid/runs/1",
        },
        stub_bin=stub_bin,
    )

    assert result.returncode == 1
    assert "image_created_unreadable" in result.stderr
    calls = (tmp_path / "docker-calls.txt").read_text(encoding="utf-8")
    assert "push" not in calls


def test_the_only_thing_read_out_of_the_local_daemon_is_the_creation_time() -> None:
    # The digest that leaves this workflow is the registry's. Nothing else may be taken
    # from `docker inspect`, or a local build could claim an identity ECR never accepted.
    inspects = re.findall(r"docker (?:image )?inspect[^\n]*", WORKFLOW_PATH.read_text("utf-8"))

    assert inspects == ["docker image inspect --format '{{.Created}}' \"${image_reference}\" \\"]


def test_publish_job_builds_from_the_registered_base_digest_under_an_immutable_tag() -> None:
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    build = step(_job("publish"), "Build and push image")
    script = build["run"]

    assert build["id"] == "image"
    assert build["env"]["IMAGE_TAG"] == "${{ steps.preflight.outputs.image_tag }}"
    assert '--build-arg "BASE_IMAGE=${BASE_REFERENCE}"' in script
    assert '--label "org.opencontainers.image.revision=${COMMIT_SHA}"' in script
    assert '--label "org.opencontainers.image.source=${SOURCE_URL}"' in script
    assert '--label "org.opencontainers.image.base.name=${BASE_REFERENCE}"' in script
    assert '--label "edullm.workflow.run.url=${RUN_URL}"' in script
    assert 'docker push "${image_reference}"' in script
    assert build["env"]["BASE_REFERENCE"] == ("${{ steps.build_inputs.outputs.base_reference }}")

    assert "--force" not in workflow_text
    assert ":latest" not in workflow_text
    assert "docker/build-push-action" not in workflow_text
    assert "buildx" not in workflow_text


def test_publish_job_takes_the_digest_from_an_ecr_read_back_not_the_local_build() -> None:
    digest = step(_job("publish"), "Read published digest from the registry")
    script = digest["run"]

    assert digest["id"] == "digest"
    assert command_tokens(script, "ecr", "describe-images") == [
        "aws",
        "ecr",
        "describe-images",
        "--repository-name",
        "${ECR_REPOSITORY}",
        "--image-ids",
        "imageTag=${IMAGE_TAG}",
        "--query",
        "imageDetails[0].imageDigest",
        "--output",
        "text",
        ">",
        "${digest_file}",
    ]
    assert 'image_digest="$(cat "${digest_file}")"' in script
    assert 'echo "image_digest=${image_digest}" >> "${GITHUB_OUTPUT}"' in script
    assert digest["env"]["IMAGE_TAG"] == "${{ steps.preflight.outputs.image_tag }}"

    # `describe-images --output text` prints "None" for a missing image, so the read-back
    # has to reject anything that is not a digest before it becomes a workflow output.
    # The negation is asserted with the guard: dropping the `!` inverts the meaning while
    # leaving both the pattern and the `exit 1` in place.
    assert 'if [[ ! "${image_digest}" =~ ^sha256:[0-9a-f]{64}$ ]]; then' in script
    assert "exit 1" in script

    # The push has to have happened first, or the read-back describes the previous image.
    names = [candidate.get("name") for candidate in _job("publish")["steps"]]
    assert names.index("Build and push image") < names.index(
        "Read published digest from the registry"
    )

    # The digest that leaves this workflow must be the registry's, never docker's.
    assert "docker inspect" not in script
    assert "RepoDigests" not in script


def test_the_published_digest_is_written_where_a_person_can_copy_it(tmp_path: Path) -> None:
    """Executed rather than read. Mutation: leave the digest in the job log only.

    ``image_digest`` is a ``workflow_call`` output, which serves a caller and serves nobody
    who is about to fill in a submission form: that form takes the digest as free text,
    because a digest is per-build and no dropdown can hold one. Before this step the value
    existed only as a line inside a step whose name does not suggest it holds one.

    Run rather than read because a ``{ ... } >> "${GITHUB_STEP_SUMMARY}"`` block with a
    heredoc in it is easy to get subtly wrong and exits 0 while getting it wrong, which is
    how the cancellation notice one file over is tested for the same reason.
    """
    summary = tmp_path / "summary.md"
    summary.touch()

    result = run_step_script(
        step(_job("publish"), DIGEST_SUMMARY_STEP)["run"],
        cwd=tmp_path,
        env={
            "GITHUB_STEP_SUMMARY": str(summary),
            "RESEARCH_REPOSITORY": "OLMo-core",
            "COMMIT_SHA": "a" * 40,
            "IMAGE_DIGEST": PUBLISHED_IMAGE_DIGEST,
        },
    )

    written = summary.read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert "| Repository | `OLMo-core` |" in written
    assert f"| Commit | `{'a' * 40}` |" in written
    assert f"| Digest | `{PUBLISHED_IMAGE_DIGEST}` |" in written
    # The fenced block is what GitHub renders a copy button on, and the digest has to be
    # the whole of its content: a line with anything else on it copies that too.
    assert f"```\n{PUBLISHED_IMAGE_DIGEST}\n```" in written
    # The prose is prose. A shell that expanded something would have eaten the backticks,
    # which is how it would first be noticed.
    assert "leave image_digest blank and paste only the commit" in written
    # The digest is still printed, because the one case that needs it is a commit built more
    # than once. What changed is that this page no longer sends every reader to a field the
    # submission resolves for them.
    assert "You do not need to copy it" in written


def test_the_digest_summary_comes_after_the_read_back_that_establishes_it() -> None:
    """Mutation: move it above the read-back, make it conditional, or push it down the job.

    Written before the digest is read it would publish the previous image's, and a summary
    is the one artefact a person copies from without checking.

    Immediately after, rather than merely after, and that is the half worth pinning. The
    image is in the registry by this point and its tag can never be rewritten, so any step
    that lands in the gap is a step that can fail between publishing an image and telling
    anybody its digest -- which strands a commit that was published successfully. Adjacency
    is what keeps the gap closed as steps are appended, and appending is how it would go.
    """
    names = [candidate.get("name") for candidate in _job("publish")["steps"]]

    assert names.index(DIGEST_SUMMARY_STEP) == names.index(DIGEST_STEP) + 1
    assert "if" not in step(_job("publish"), DIGEST_SUMMARY_STEP)
    assert step(_job("publish"), DIGEST_SUMMARY_STEP)["env"] == {
        "RESEARCH_REPOSITORY": "${{ inputs.repository }}",
        "COMMIT_SHA": "${{ needs.verify.outputs.commit_sha }}",
        "IMAGE_DIGEST": "${{ steps.digest.outputs.image_digest }}",
    }


def test_publish_job_reverifies_the_source_before_it_holds_aws_credentials() -> None:
    steps = _job("publish")["steps"]
    names = [candidate.get("name") for candidate in steps]

    assert names.index("Re-verify source identity") < names.index("Configure AWS credentials")
    reverify = step(_job("publish"), "Re-verify source identity")
    assert "verify_source_identity.py" in reverify["run"]
    assert reverify["env"]["RESEARCH_COMMIT_SHA"] == "${{ needs.verify.outputs.commit_sha }}"


def test_platform_tooling_is_installed_from_the_lockfile_under_a_pinned_uv() -> None:
    for job_name in ("verify", "deny", "publish"):
        install = step(_job(job_name), "Install platform tooling")

        assert install["working-directory"] == "platform"
        assert re.fullmatch(r"\d+\.\d+\.\d+", install["env"]["UV_VERSION"])
        assert 'pipx install "uv==${UV_VERSION}"' in install["run"]
        assert "uv sync --locked" in install["run"]

    # Every Python invocation must come from that locked environment.
    for name, script in _run_bodies(_load()):
        if ".py" in script:
            assert "uv run --frozen python" in script, name


def _run_install(tmp_path: Path, reported_version: str) -> Any:
    install = step(_job("publish"), "Install platform tooling")
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "pipx", "exit 0\n")
    write_stub(
        stub_bin,
        "uv",
        f'if [[ "${{1-}}" == "--version" ]]; then echo "{reported_version}"; fi\nexit 0\n',
    )
    return run_step_script(
        install["run"],
        cwd=tmp_path,
        env={"UV_VERSION": install["env"]["UV_VERSION"]},
        stub_bin=stub_bin,
    )


@pytest.mark.slow
def test_the_uv_that_answers_on_path_must_be_the_one_that_was_pinned(tmp_path: Path) -> None:
    # pipx installs the pin, but an earlier uv already on PATH keeps answering, so the
    # lockfile would be resolved by a version nobody chose.
    pinned = step(_job("publish"), "Install platform tooling")["env"]["UV_VERSION"]

    assert _run_install(tmp_path, f"uv {pinned} (abc1234 2026-01-01)").returncode == 0
    assert _run_install(tmp_path, f"uv {pinned}").returncode == 0

    mismatched = _run_install(tmp_path, "uv 0.4.30 (abc1234 2024-01-01)")
    assert mismatched.returncode == 1
    assert "unexpected_uv_version" in mismatched.stderr
    assert pinned in mismatched.stderr

    # A prefix match would let 0.11.320 pass for 0.11.32.
    assert _run_install(tmp_path, f"uv {pinned}0").returncode == 1


def test_publish_job_assumes_the_publisher_role_with_the_account_id_masked() -> None:
    credentials = step(_job("publish"), "Configure AWS credentials")

    assert credentials["id"] == "credentials"
    # mask-aws-account-id defaults to false in v6, and `docker push` prints the registry
    # host, so without this the account id lands in a world-readable log.
    assert credentials["with"] == {
        "role-to-assume": "${{ inputs.publisher_role_arn }}",
        "aws-region": "${{ inputs.aws_region }}",
        "role-duration-seconds": 3600,
        "mask-aws-account-id": True,
    }


def test_the_workflow_makes_exactly_these_aws_calls_in_exactly_this_order() -> None:
    # The publisher role permits nine ECR actions, so an added call is not necessarily an
    # AccessDenied. Enumerating them means a new one has to be argued for in review.
    #
    # The denial matrix contributes nothing here, and deliberately: its calls are made by
    # a tool rather than by a shell, because deciding whether a failure was a refusal is
    # not something a run body can do without printing what it read. Those calls are
    # enumerated the same way one file over, in the tool's own test module.
    calls = [
        (name, tuple(command[:3]))
        for name, script in _run_bodies(_load())
        for command in aws_commands(script)
    ]

    assert calls == [
        ("publish:Look for an already published image", ("aws", "ecr", "describe-images")),
        (f"publish:{RESUME_STEP}", ("aws", "ecr", "batch-get-image")),
        (f"publish:{RESUME_STEP}", ("aws", "ecr", "get-download-url-for-layer")),
        ("publish:Log in to Amazon ECR", ("aws", "ecr", "get-login-password")),
        (
            "publish:Read published digest from the registry",
            ("aws", "ecr", "describe-images"),
        ),
    ]


def test_every_run_step_declares_the_directory_its_tooling_lives_in() -> None:
    # Platform tooling resolves config/repositories.yaml relative to the working
    # directory, and `docker build` resolves the Dockerfile and the context relative to
    # it. A missing or wrong entry here is a run that reads the caller's files as if they
    # were the platform's, or the reverse.
    actual = {
        name: candidate.get("working-directory")
        for job_name, job in _load()["jobs"].items()
        for candidate in job["steps"]
        if "run" in candidate
        for name in [f"{job_name}:{candidate['name']}"]
    }

    assert actual == {
        "verify:Verify the caller contract": None,
        "verify:Install platform tooling": "platform",
        "verify:Verify source identity": "platform",
        "verify:Run research repository tests": "source",
        "deny:Verify the caller contract": None,
        "deny:Install platform tooling": "platform",
        f"deny:{DENIAL_STEP}": "platform",
        "publish:Verify the caller contract": None,
        "publish:Install platform tooling": "platform",
        "publish:Re-verify source identity": "platform",
        f"publish:{DECREDENTIAL_STEP}": "source",
        "publish:Resolve build inputs": "platform",
        f"publish:{BASE_GATE_STEP}": "platform",
        f"publish:{PREFLIGHT_STEP}": None,
        f"publish:{RESUME_STEP}": "platform",
        "publish:Log in to Amazon ECR": None,
        "publish:Build and push image": "source",
        "publish:Read published digest from the registry": None,
        f"publish:{DIGEST_SUMMARY_STEP}": None,
    }


def test_no_run_body_interpolates_a_github_expression() -> None:
    offenders = [name for name, script in _run_bodies(_load()) if "${{" in script]

    assert offenders == [], f"run bodies must read expressions through env: {offenders}"


def test_every_run_body_enables_strict_bash() -> None:
    for name, script in _run_bodies(_load()):
        assert script.startswith("set -euo pipefail\n"), name


def test_workflow_never_embeds_an_aws_account_identifier_or_registry_host() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert not re.search(r"(?<!\d)\d{12}(?!\d)", text)
    assert "repositoryUri" not in text
    assert "registryId" not in text
    assert not re.search(r"\d\.dkr\.ecr\.", text)


def test_concurrency_is_keyed_per_caller_commit_and_never_cancels() -> None:
    workflow = _load()

    assert workflow["concurrency"] == {
        "group": "build-research-image-${{ github.repository }}-${{ github.sha }}",
        "cancel-in-progress": False,
    }


def test_job_runtimes_are_bounded() -> None:
    assert _job("verify")["timeout-minutes"] == 30
    assert _job("deny")["timeout-minutes"] == 15
    assert _job("publish")["timeout-minutes"] == 60
