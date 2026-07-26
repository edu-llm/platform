"""Unit tests for the workflow expression checker that guards the workflow test modules.

The checker exists because the 66-test suite that shipped alongside `build-research-image`
passed against a workflow that could not complete a single run: every assertion compared
literal expression strings, so `${{ github.job_workflow_sha }}` was as acceptable as a
property that exists. A checker nobody tests would have the same failure mode, so the
synthetic workflows below pin its behaviour rather than only its output on real files.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from workflow_support import unreal_context_references

REUSABLE_HEADER = """
name: Fixture
on:
  workflow_call:
    inputs:
      repository:
        required: true
        type: string
    outputs:
      digest:
        value: ${{ jobs.publish.outputs.digest }}
jobs:
  verify:
    runs-on: ubuntu-latest
    outputs:
      commit_sha: ${{ steps.identity.outputs.commit_sha }}
    steps:
      - id: identity
        run: echo "commit_sha=abc" >> "${GITHUB_OUTPUT}"
  publish:
    runs-on: ubuntu-latest
    needs: verify
    outputs:
      digest: ${{ steps.digest.outputs.digest }}
    steps:
      - id: opaque
        uses: example/action@v1
      - id: digest
        env:
          PROBE: PLACEHOLDER
        run: echo "digest=sha256:abc" >> "${GITHUB_OUTPUT}"
"""
CONDITION_FIXTURE = """
name: Fixture
on:
  workflow_call:
    inputs:
      repository:
        required: true
        type: string
jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - id: digest
        run: echo "image_digest=sha256:abc" >> "${GITHUB_OUTPUT}"
      - name: Use it
        if: PLACEHOLDER
        run: echo done
"""


def fixture_workflow(tmp_path: Path, probe: str, template: str = REUSABLE_HEADER) -> Path:
    path = tmp_path / "fixture.yml"
    path.write_text(template.replace("PLACEHOLDER", probe), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "probe",
    [
        "${{ job.workflow_sha }}",
        "${{ job.workflow_ref }}",
        "${{ job.workflow_repository }}",
        "${{ job.workflow_file_path }}",
        "${{ job.container.id }}",
        "${{ job.services.postgres.ports }}",
        "${{ github.sha }}",
        "${{ github.workflow_sha }}",
        "${{ github.event.pull_request.number }}",
        "${{ runner.temp }}",
        "${{ inputs.repository }}",
        "${{ needs.verify.outputs.commit_sha }}",
        "${{ needs.verify.result }}",
        "${{ steps.digest.outputs.digest }}",
        "${{ steps.digest.outcome }}",
        "${{ vars.AWS_REGION }}",
        "${{ env.SOME_VALUE }}",
        "${{ secrets.GITHUB_TOKEN }}",
        "${{ format('{0}/{1}', github.server_url, github.repository) }}",
        "${{ github.event_name == 'push' && inputs.repository != '' }}",
        "${{ !cancelled() && success() }}",
        "${{ contains(github.ref, 'refs/heads/') }}",
    ],
)
def test_real_context_references_are_accepted(tmp_path: Path, probe: str) -> None:
    assert unreal_context_references(fixture_workflow(tmp_path, probe)) == []


@pytest.mark.parametrize(
    ("probe", "fragment"),
    [
        ("${{ github.job_workflow_sha }}", "github has no property job_workflow_sha"),
        ("${{ github.workflow_file_path }}", "github has no property workflow_file_path"),
        ("${{ github.repository_name }}", "github has no property repository_name"),
        ("${{ github.sha.short }}", "github.sha is not an object"),
        ("${{ job.workflow_path }}", "job has no property workflow_path"),
        ("${{ job.workflow_sha.value }}", "job.workflow_sha is not an object"),
        ("${{ job.container.image }}", "job.container has no property image"),
        ("${{ runner.tmp }}", "runner has no property tmp"),
        ("${{ inputs.publisher_role_arn }}", "inputs has no declared input"),
        ("${{ needs.verify.outputs.absent }}", "job verify declares no output absent"),
        ("${{ needs.publish.outputs.digest }}", "needs cannot reach job publish"),
        ("${{ steps.absent.outputs.digest }}", "steps has no step id absent"),
        ("${{ steps.digest.output.digest }}", "exposes only outputs, outcome, and conclusion"),
        ("${{ steps.digest.outputs }}", "exposes only outputs, outcome, and conclusion"),
        ("${{ steps.digest.outputs.image_digest }}", "step digest writes no output image_digest"),
        ("${{ steps.opaque.outputs.anything }}", "step opaque writes no output anything"),
        ("${{ jobs.publish.outputs.digest }}", "only available to reusable workflow outputs"),
        ("${{ githu.sha }}", "githu is not a workflow context"),
        ("${{ environment.name }}", "environment is not a workflow context"),
    ],
)
def test_references_to_things_that_do_not_exist_are_rejected(
    tmp_path: Path,
    probe: str,
    fragment: str,
) -> None:
    problems = unreal_context_references(fixture_workflow(tmp_path, probe))

    assert len(problems) == 1, problems
    assert fragment in problems[0]
    assert "job publish" in problems[0]


def test_a_step_id_from_another_job_is_out_of_scope(tmp_path: Path) -> None:
    # `steps` is per job, so a publish step cannot read a verify step even though both
    # ids are visible in the same file. Scoping is what makes that detectable.
    problems = unreal_context_references(fixture_workflow(tmp_path, "${{ steps.identity.sha }}"))

    assert len(problems) == 1
    assert "steps has no step id identity" in problems[0]


def test_an_output_a_step_cannot_be_read_to_write_has_to_be_declared(tmp_path: Path) -> None:
    # An `uses:` step and a step whose outputs come out of a platform CLI write nothing a
    # run body can be read for, so the caller names them and thereby pins them.
    path = fixture_workflow(tmp_path, "${{ steps.opaque.outputs.account_id }}")

    assert unreal_context_references(path) != []
    assert unreal_context_references(path, declared_step_outputs={"opaque": ("account_id",)}) == []


def test_declaring_an_output_does_not_hide_the_rest_of_the_step(tmp_path: Path) -> None:
    path = fixture_workflow(tmp_path, "${{ steps.opaque.outputs.region }}")

    problems = unreal_context_references(path, declared_step_outputs={"opaque": ("account_id",)})

    assert len(problems) == 1
    assert "step opaque writes no output region" in problems[0]


def test_a_run_body_that_writes_an_output_needs_no_declaration(tmp_path: Path) -> None:
    assert (
        unreal_context_references(fixture_workflow(tmp_path, "${{ steps.digest.outputs.digest }}"))
        == []
    )


@pytest.mark.parametrize(
    "condition",
    [
        "steps.digest.outputs.image_digest != ''",
        "${{ steps.digest.outputs.image_digest != '' }}",
        "inputs.repository != '' && steps.digest.outcome == 'success'",
    ],
)
def test_a_condition_is_an_expression_whether_or_not_it_is_braced(
    tmp_path: Path,
    condition: str,
) -> None:
    path = fixture_workflow(tmp_path, condition, template=CONDITION_FIXTURE)

    assert unreal_context_references(path) == []


@pytest.mark.parametrize(
    ("condition", "fragment"),
    [
        ("steps.digest.outputs.digest != ''", "step digest writes no output digest"),
        ("${{ steps.digest.outputs.digest != '' }}", "step digest writes no output digest"),
        ("github.job_workflow_sha != ''", "github has no property job_workflow_sha"),
        ("inputs.absent != ''", "inputs has no declared input absent"),
    ],
)
def test_an_unbraced_condition_is_checked_like_any_other_expression(
    tmp_path: Path,
    condition: str,
    fragment: str,
) -> None:
    # GitHub evaluates `if:` as an expression with or without the braces, so a checker
    # that only reads `${{ }}` leaves every gate in the file unexamined.
    problems = unreal_context_references(fixture_workflow(tmp_path, condition, CONDITION_FIXTURE))

    assert len(problems) == 1, problems
    assert fragment in problems[0]


def test_the_reusable_workflow_output_may_reach_the_jobs_context(tmp_path: Path) -> None:
    path = fixture_workflow(tmp_path, "${{ github.sha }}")
    text = path.read_text(encoding="utf-8").replace(
        "jobs.publish.outputs.digest", "jobs.publish.outputs.absent"
    )
    path.write_text(text, encoding="utf-8")

    problems = unreal_context_references(path)

    assert len(problems) == 1
    assert "job publish declares no output absent" in problems[0]
    assert "(workflow)" in problems[0]
