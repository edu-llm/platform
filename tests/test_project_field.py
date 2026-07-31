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
suggest a dropdown; ``test_a_project_is_not_a_dropdown_so_a_new_one_needs_no_pull_request``
is the answer.

**WHY IT IS NOT IN THE MANIFEST, WHICH IS WHERE IT WAS FIRST PUT.** This module used to argue
the opposite -- that a grouping key not in the lineage is not lineage -- and a measurement
retired the argument. ``RunManifest`` is hashed whole and the digest is what an approver
releases, so a field added to it changes the digest of *every manifest ever written*: the
recomputed form carries a key the stored bytes never had. Against a real stored record,
``run_019fa446-8a4e-7094-9e29-d44fffbd2491``, the manifest rehashes to ``819aaf8a`` as
stored and to ``0439d570`` with ``project: null`` present, and
``test_the_manifest_in_every_intent_still_hashes_to_its_recorded_value`` stops agreeing with
records nobody touched.

No serialization setting rescues it. Excluding nulls instead yields ``e75c8f8a``, because the
stored manifests already carry ``fanout: null`` and dropping it moves the digest the other
way. This is schema evolution against content addressing and it is general: **any** field
added to ``RunManifest`` does this, so the seam this module defends is worth more than the
one field that found it.

Nothing is lost. A project groups runs; it does not say what ran. Its consumers -- the W&B
run group, the ``edullm:project`` Batch tag and the cost view -- are all set when the job is
launched, and none of them reads the sealed document.
"""

from __future__ import annotations

import json
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
RUN_ID = "run_019fa439-203e-70c7-bf8a-9ce33bc71f20"
JOB_DEFINITION = "sbsandbox-intern-edullm-olmo-core-cpu"


def environment_of(request: dict[str, Any]) -> dict[str, str]:
    return {
        entry["Name"]: entry["Value"]
        for entry in request["ContainerOverrides"]["Environment"]
    }


def submit_request(project: str | None) -> dict[str, Any]:
    manifest = cpu_manifest()
    return batch_submit_request(
        manifest=manifest,
        target=seam_target(manifest),
        run_id=RUN_ID,
        job_definition=JOB_DEFINITION,
        project=project,
    )


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


def test_the_project_on_the_form_is_carried_beside_the_manifest_rather_than_inside_it() -> (
    None
):
    """Mutation: put it back on ``RunManifest``.

    The mutation that looks like a tidy-up and is the one this module exists to stop. See the
    module docstring for the three measured digests; the short version is that a manifest is
    hashed whole, so a field added to it invalidates every record written before it existed,
    and no serialization flag avoids that.

    ``resolved_image`` is the precedent rather than the exception: ``CompiledSubmission``
    already carries a fact beside the manifest that the manifest has no business holding.
    """
    compiled = compile_payload(cpu_payload(project="context-length-sweep"))

    assert compiled.project == "context-length-sweep"
    assert not hasattr(compiled.manifest, "project")


def test_the_manifest_carries_no_field_the_records_already_written_do_not_have() -> None:
    """Mutation: any future field added directly to ``RunManifest``.

    The general guard, and phrased about the model rather than about ``project`` because what
    broke was not this feature -- it was the assumption that a hashed record can grow a
    field. The next one fails here too, and fails naming itself.

    ``test_the_manifest_in_every_intent_still_hashes_to_its_recorded_value`` already catches
    the same mistake by its consequence. This catches it by its cause, which is the
    difference between a failure that says "a stored record no longer verifies" and one that
    says "you added ``foo`` to a hashed model". Both are worth having; only this one points
    at the line to change.
    """
    stored = json.loads(
        json.loads(
            (
                PROJECT_ROOT
                / "fixtures/evidence/phase-2/lineage/records/intent"
                / "run_019fa446-8a4e-7094-9e29-d44fffbd2491.json"
            ).read_text(encoding="utf-8")
        )
    )["manifest"]

    added = set(RunManifest.model_fields) - set(stored)
    assert not added, (
        f"{sorted(added)} was added to RunManifest, which is hashed whole. Every manifest "
        "written before it existed now recomputes to a different digest, so records nobody "
        "touched stop verifying. Carry it on CompiledSubmission instead -- see this module's "
        "docstring for the three measured digests."
    )


def test_the_project_reaches_the_batch_tags_under_a_key_a_cost_query_can_group_on() -> (
    None
):
    """Mutation: emit it as ``project`` without the prefix, or leave it out of ``Tags``.

    The prefix is the point. ``edullm:`` is what tells this platform's tags apart from every
    other tag in a shared sandbox account, and Cost Explorer groups on the whole key -- so an
    unprefixed ``project`` is a key somebody else's stack may also be writing.
    """
    request = submit_request("context-length-sweep")

    assert request["Tags"]["edullm:project"] == "context-length-sweep"
    assert request["PropagateTags"] is True


def test_the_project_is_the_run_group_the_container_is_told_to_use() -> None:
    """Mutation: send it as ``EDULLM_PROJECT`` and expect the workload to forward it.

    ``WANDB_RUN_GROUP`` is W&B's own name and the wandb client reads it without being asked,
    which is the same reasoning that puts ``WANDB_ENTITY`` and ``WANDB_PROJECT`` beside the
    prefixed ``EDULLM_WANDB_PROJECT``. A prefixed name would need every workload to forward
    it, and a workload that forgot would produce ungrouped runs -- indistinguishable from a
    submitter who left the field blank, except that the field cannot be left blank.

    It does not take the choice away from a workload. wandb applies an explicit ``group=``
    over the environment, and OLMo-core's ``WandBCallback`` defaults it to ``None`` -- so a
    run that names its own group still wins, and one that does not lands where the form said.
    """
    assert environment_of(submit_request("context-length-sweep"))["WANDB_RUN_GROUP"] == (
        "context-length-sweep"
    )


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
    manifest = cpu_manifest()
    first, second = (
        batch_submit_request(
            manifest=manifest,
            target=seam_target(manifest),
            run_id=run_id,
            job_definition=JOB_DEFINITION,
            project=shared,
        )
        for run_id in (RUN_ID, "run_019fa43a-1111-7000-8000-9ce33bc71f21")
    )

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

    The other groupings are dropdowns and should be. Each of them registers something with a
    consequence -- a cost centre, a compute profile, a place images may be pushed. A project
    registers nothing, so there is nothing for a reviewer to check.
    """
    inputs = load_workflow(SUBMIT_WORKFLOW_PATH)["on"]["workflow_dispatch"]["inputs"]

    assert inputs["project"]["type"] == "string"
    assert inputs["project"]["required"] is True
    assert "options" not in inputs["project"]
    # The closed sets, so this fails if project is made to look like them or if one of them
    # is quietly opened up.
    assert {
        inputs[name]["type"]
        for name in ("repository", "workload_profile", "dataset_release")
    } == {"choice"}


def test_a_submission_cannot_be_compiled_without_a_project() -> None:
    """Mutation: give ``SubmissionInputs.project`` a default.

    A default would be a project every run without an opinion joins, which is a group whose
    membership means "nobody said" -- and a cost query cannot tell that apart from a real
    grouping. The field is required at the only place a new run can enter, which is the form,
    and that is where the strictness belongs now that the manifest cannot carry it.
    """
    payload = cpu_payload()
    del payload["project"]
    with pytest.raises(ValueError, match="project"):
        compile_payload(payload)


def test_a_run_admitted_before_the_field_existed_is_left_out_of_the_grouping() -> None:
    """Mutation: send ``WANDB_RUN_GROUP`` and the tag unconditionally.

    Reached by an execution that crossed the approval gate before this shipped, whose event
    carries no project. An empty tag value is not an absence -- Cost Explorer groups on it
    and totals up a project named "". W&B reads an empty run group the same way.

    Both together or neither, because they are one fact told to two systems. A run present in
    the W&B grouping and missing from the cost grouping reads as a billing discrepancy rather
    than as a missing field.
    """
    request = submit_request(None)

    assert "WANDB_RUN_GROUP" not in environment_of(request)
    assert "edullm:project" not in request["Tags"]
