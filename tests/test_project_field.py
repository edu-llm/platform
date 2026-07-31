"""``project``: the discovery key, and the one field on the form nobody has to register.

Every other grouping this platform records is a closed set. ``team`` is a roster entry,
``workload_profile`` a catalog entry, ``dataset_release`` a registration, ``repository`` a
registry entry -- and each is a dropdown, so adding one is a pull request against this
repository. That is correct for all four: they carry consequences somebody has to review,
whether that is a cost centre, a compute profile or a place images may be pushed.

Grouping runs carries no consequence. Two people comparing four ablations want a label they
can agree on over lunch, and making them file a pull request to say "these six runs are the
context-length sweep" is a governance cost with nothing on the other side of it. So
``project`` is free text, validated for shape and registered nowhere.

**Free text is also the only thing that works.** ``workflow_dispatch`` ``choice`` options are
static text read from the default branch, so a dropdown could not be extended by anybody
running from a branch even if the review were wanted. A later reviewer will reasonably
suggest a dropdown; the last test here is the answer.

**What it costs, recorded because it is easy to under-price.** ``RunManifest`` is the model
the canonical manifest hash is taken over and the model every proof bundle records a
structural digest for. Adding a field moves the hash and moves a cell in the committed
bundles. The field belongs in the immutable record anyway -- a grouping key that is not in
the lineage is not lineage -- but it is a one-pull-request change rather than a small one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_phase2_submission import compile_payload, cpu_payload
from test_phase3_infrastructure import cpu_manifest, seam_target
from workflow_support import load_workflow

from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.errors import SubmissionRefusedError
from edullm_platform.execution import batch_submit_request

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUBMIT_WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "submit-run.yml"


def environment_of(request: dict[str, Any]) -> dict[str, str]:
    return {
        entry["Name"]: entry["Value"]
        for entry in request["ContainerOverrides"]["Environment"]
    }


def test_a_project_that_is_not_kebab_case_is_refused_with_a_message_naming_project() -> None:
    """Mutation: take ``project`` as any non-empty string.

    The same shape ``team`` is held to, and for a weaker reason honestly stated: nothing
    downstream breaks on a project called ``Context Length Sweep``. What breaks is the
    grouping, quietly -- it becomes a Batch tag value and a W&B run group, and two people
    typing the same words with different capitals get two groups that look like one.
    """
    with pytest.raises(SubmissionRefusedError) as exc_info:
        compile_payload(cpu_payload(project="Context Length Sweep"))

    message = str(exc_info.value)
    assert "project" in message
    assert "Context Length Sweep" in message
    assert "lower-case" in message


def test_the_project_on_the_form_is_the_project_in_the_manifest() -> None:
    """Mutation: derive it, default it, or leave it out of ``RunManifest``.

    It has to be in the manifest rather than only in the tags, because the manifest is what
    is hashed, approved and written to lineage. A grouping key that reached Batch without
    passing through the record would be a label on the spend that the decision record does
    not account for -- the same objection that keeps ``wandb_project`` out of the command.
    """
    compiled = compile_payload(cpu_payload(project="context-length-sweep"))

    assert compiled.manifest.project == "context-length-sweep"


def test_the_project_reaches_the_batch_tags_under_a_key_a_cost_query_can_group_on() -> (
    None
):
    """Mutation: emit it as ``project`` without the prefix, or leave it out of ``Tags``.

    The prefix is the point. ``edullm:`` is what tells this platform's tags apart from every
    other tag in a shared sandbox account, and Cost Explorer groups on the whole key -- so an
    unprefixed ``project`` is a key somebody else's stack may also be writing.
    """
    manifest = cpu_manifest(project="context-length-sweep")
    request = batch_submit_request(
        manifest=manifest,
        target=seam_target(manifest),
        run_id="run_019fa439-203e-70c7-bf8a-9ce33bc71f20",
        job_definition="sbsandbox-intern-edullm-olmo-core-cpu",
    )

    assert request["Tags"]["edullm:project"] == "context-length-sweep"
    assert request["PropagateTags"] is True


def test_the_project_is_the_run_group_the_container_is_told_to_use() -> None:
    """Mutation: send it as ``EDULLM_PROJECT`` and expect the workload to forward it.

    ``WANDB_RUN_GROUP`` is W&B's own name and the wandb client reads it without being asked,
    which is the same reasoning that put ``WANDB_ENTITY`` beside ``EDULLM_WANDB_PROJECT``
    rather than a prefixed copy of it. A prefixed name would need every workload to forward
    it, and a workload that forgot would produce ungrouped runs -- which is indistinguishable
    from a submitter who left the field blank, except that the field cannot be left blank.
    """
    manifest = cpu_manifest(project="context-length-sweep")
    request = batch_submit_request(
        manifest=manifest,
        target=seam_target(manifest),
        run_id="run_019fa439-203e-70c7-bf8a-9ce33bc71f20",
        job_definition="sbsandbox-intern-edullm-olmo-core-cpu",
    )

    assert environment_of(request)["WANDB_RUN_GROUP"] == "context-length-sweep"


def test_two_submissions_sharing_a_project_produce_the_same_run_group_and_the_same_tag() -> (
    None
):
    """Mutation: mix the run id into either value.

    The whole feature in one assertion. A grouping key that varies per run groups nothing,
    and both places it lands have a neighbouring field that does vary per run --
    ``edullm:run-id`` in the tags, and the run id in ``EDULLM_OUTPUT_PREFIX`` -- so deriving
    this one from the run id is a plausible mistake rather than an exotic one.
    """
    shared = "context-length-sweep"
    requests = [
        batch_submit_request(
            manifest=cpu_manifest(project=shared),
            target=seam_target(cpu_manifest(project=shared)),
            run_id=run_id,
            job_definition="sbsandbox-intern-edullm-olmo-core-cpu",
        )
        for run_id in (
            "run_019fa439-203e-70c7-bf8a-9ce33bc71f20",
            "run_019fa43a-1111-7000-8000-9ce33bc71f21",
        )
    ]
    first, second = requests

    assert first["Tags"]["edullm:project"] == second["Tags"]["edullm:project"] == shared
    assert environment_of(first)["WANDB_RUN_GROUP"] == shared
    assert environment_of(second)["WANDB_RUN_GROUP"] == shared
    assert first["Tags"]["edullm:run-id"] != second["Tags"]["edullm:run-id"]


def test_a_project_is_not_a_dropdown_so_a_new_one_needs_no_pull_request() -> None:
    """Mutation: make it ``type: choice`` with the projects in flight today.

    A shape assertion rather than a behaviour one, and it exists because the suggestion to
    close this set is a reasonable one that will be made. The mechanical answer is that
    ``workflow_dispatch`` ``choice`` options are static text read only from the default
    branch: a dropdown could not be extended from a branch, so starting a new project would
    mean a pull request against this repository merged to main before the first run.

    The other four groupings are dropdowns and should be. Each of them registers something
    with a consequence -- a cost centre, a compute profile, a place images may be pushed. A
    project registers nothing, so there is nothing for a reviewer to check.
    """
    inputs = load_workflow(SUBMIT_WORKFLOW_PATH)["on"]["workflow_dispatch"]["inputs"]

    assert inputs["project"]["type"] == "string"
    assert inputs["project"]["required"] is True
    assert "options" not in inputs["project"]
    # The four that are closed sets, so this test fails if project is made to look like them
    # or if one of them is quietly opened up.
    assert {inputs[name]["type"] for name in ("repository", "workload_profile", "dataset_release")} == {
        "choice"
    }


def test_the_manifest_field_is_required_because_a_grouping_key_cannot_be_optional() -> None:
    """Mutation: give ``project`` a default.

    A default would be a project every run without an opinion joins, which is a group whose
    membership means "nobody said" -- and the cost query cannot tell that apart from a real
    grouping. Required on the form and required in the record.
    """
    with pytest.raises(ValueError):
        RunManifest.model_validate(
            {
                "schema_version": 1,
                "repository": "OLMo-core",
                "commit_sha": "4204375e6db85abc244ec7f626de8d3cc3511402",
                "image_digest": (
                    "sha256:4ebdba1ba3b57096efb4f4647ed41ed5ded4ac9e77e8c9038b7ff24db0bc6db8"
                ),
                "dataset_release": "dolma-2026-07",
                "command": ["python", "-m", "olmo_core.train"],
                "team": "memory-split",
                "wandb_project": "olmo-core-memory-split",
                "workload_profile": "olmo-core-cpu-smoke",
                "compute_profile": "cpu-32vcpu",
                "maximum_runtime_hours": "1",
                "maximum_attempts": 1,
                "checkpoint": None,
                "fanout": None,
            }
        )
