from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from workflow_support import (
    WORKFLOWS_ROOT,
    command_tokens,
    load_workflow,
    step,
)

WORKFLOW_PATH = WORKFLOWS_ROOT / "build-research-image.yml"
CHECKOUT_ACTION = "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09"
CREDENTIALS_ACTION = (
    "aws-actions/configure-aws-credentials@e6de054238d6b7531b4efff3b6587d9aade6a06c"
)
PLATFORM_REPOSITORY = "edu-llm/platform"
WORKFLOW_PATH_INPUT = ".github/workflows/build-research-image.yml"


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


def test_workflow_has_exactly_two_ordered_jobs_with_exact_permission_maps() -> None:
    workflow = _load()

    assert list(workflow["jobs"]) == ["verify", "publish"]
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["verify"]["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["publish"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }
    assert workflow["jobs"]["publish"]["needs"] == "verify"
    assert "needs" not in workflow["jobs"]["verify"]


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
    assert used.count(CREDENTIALS_ACTION) == 1
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
        assert caller["with"]["fetch-depth"] == 0
        assert caller["with"]["path"] == "source"

        assert platform["uses"] == CHECKOUT_ACTION
        assert platform["with"]["repository"] == PLATFORM_REPOSITORY
        assert platform["with"]["ref"] == "${{ github.job_workflow_sha }}"
        assert platform["with"]["path"] == "platform"
        assert platform["with"]["persist-credentials"] is False
        assert platform["with"]["path"] != caller["with"]["path"]


def test_verify_job_runs_caller_tests_only_when_requested_and_only_through_env() -> None:
    tests = step(_job("verify"), "Run research repository tests")

    assert tests["if"] == "inputs.test_command != ''"
    assert tests["working-directory"] == "source"
    assert tests["env"] == {"TEST_COMMAND": "${{ inputs.test_command }}"}
    assert "${{" not in tests["run"]
    assert "${TEST_COMMAND}" in tests["run"]


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


def test_publish_job_builds_from_the_registered_base_digest_under_an_immutable_tag() -> None:
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    build = step(_job("publish"), "Build and push image")
    script = build["run"]

    assert build["id"] == "image"
    assert 'image_tag="${COMMIT_SHA:0:12}"' in script
    assert '--build-arg "BASE_IMAGE=${BASE_REFERENCE}"' in script
    assert '--label "org.opencontainers.image.revision=${COMMIT_SHA}"' in script
    assert '--label "org.opencontainers.image.source=${SOURCE_URL}"' in script
    assert '--label "org.opencontainers.image.base.name=${BASE_REFERENCE}"' in script
    assert '--label "edullm.workflow.run.url=${RUN_URL}"' in script
    assert 'docker push "${image_reference}"' in script
    assert build["env"]["BASE_REFERENCE"] == (
        "${{ steps.build_inputs.outputs.base_reference }}"
    )
    assert 'echo "image_tag=${image_tag}" >> "${GITHUB_OUTPUT}"' in script

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
    assert digest["env"]["IMAGE_TAG"] == "${{ steps.image.outputs.image_tag }}"

    # `describe-images --output text` prints "None" for a missing image, so the read-back
    # has to reject anything that is not a digest before it becomes a workflow output.
    assert '^sha256:[0-9a-f]{64}$' in script
    assert "exit 1" in script

    # The digest that leaves this workflow must be the registry's, never docker's.
    assert "docker inspect" not in script
    assert "RepoDigests" not in script


def test_publish_job_reverifies_the_source_before_it_holds_aws_credentials() -> None:
    steps = _job("publish")["steps"]
    names = [candidate.get("name") for candidate in steps]

    assert names.index("Re-verify source identity") < names.index("Configure AWS credentials")
    reverify = step(_job("publish"), "Re-verify source identity")
    assert "verify_source_identity.py" in reverify["run"]
    assert reverify["env"]["RESEARCH_COMMIT_SHA"] == "${{ needs.verify.outputs.commit_sha }}"


def test_platform_tooling_is_installed_from_the_lockfile_under_a_pinned_uv() -> None:
    for job_name in ("verify", "publish"):
        install = step(_job(job_name), "Install platform tooling")

        assert install["working-directory"] == "platform"
        assert re.search(r"^pipx install uv==\d+\.\d+\.\d+$", install["run"], re.MULTILINE)
        assert "uv sync --locked" in install["run"]

    # Every Python invocation must come from that locked environment.
    for name, script in _run_bodies(_load()):
        if ".py" in script:
            assert "uv run --frozen python" in script, name


def test_publish_job_assumes_the_publisher_role_and_writes_provenance() -> None:
    publish = _job("publish")
    credentials = step(publish, "Configure AWS credentials")
    provenance = step(publish, "Write image provenance")

    assert credentials["id"] == "credentials"
    assert credentials["with"] == {
        "role-to-assume": "${{ inputs.publisher_role_arn }}",
        "aws-region": "${{ inputs.aws_region }}",
        "role-duration-seconds": 3600,
    }
    assert "write_image_provenance.py" in provenance["run"]
    assert provenance["env"]["WORKFLOW_REPOSITORY"] == PLATFORM_REPOSITORY
    assert provenance["env"]["WORKFLOW_PATH"] == WORKFLOW_PATH_INPUT
    assert provenance["env"]["RUN_REPOSITORY"] == "${{ github.repository }}"
    assert provenance["env"]["IMAGE_DIGEST"] == "${{ steps.digest.outputs.image_digest }}"


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
    assert _job("publish")["timeout-minutes"] == 60
