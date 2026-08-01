"""The guide, held to the platform it describes.

A guide is a promise in the same way a dropdown is: everything in it works. The difference
is that a dropdown is checked by the tests beside it and prose is checked by nobody, so it
rots quietly and the first person to find out is a researcher following it at two in the
morning.

**These read the guide against the thing rather than against a copy of the thing.** The
workload names come out of the catalog, the environment variables out of
``batch_submit_request``, the form fields out of the workflow. A rename on either side
fails here, which is the only way a document stays true to a system that keeps moving.

What is deliberately *not* tested is the prose. There is no assertion that a paragraph
still reads well or that the six traps are explained convincingly. Those are worth having
and a test cannot hold them; what a test can hold is that every identifier the guide puts
in front of somebody still exists.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from edullm_platform.config import load_yaml
from edullm_platform.contracts.workload import WorkloadCatalog

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GUIDE_PATH = PROJECT_ROOT / "GETTING-STARTED.md"
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "submit-run.yml"


@pytest.fixture(scope="module")
def guide() -> str:
    return GUIDE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    # `on:` is parsed by PyYAML as the boolean True, which is the same trap the other
    # workflow tests document; reading the mapping back by identity avoids arguing with it.
    parsed: dict[str, Any] = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return parsed


def form_inputs(workflow: dict[str, Any]) -> dict[str, Any]:
    triggers = workflow.get("on") or workflow.get(True)
    inputs: dict[str, Any] = triggers["workflow_dispatch"]["inputs"]
    return inputs


def test_every_workload_the_guide_names_is_one_the_form_offers(guide: str) -> None:
    """Mutation: rename a workload and leave the guide saying the old one.

    The guide tells a first-time reader to pick ``olmo-core-check-cpu`` and tells a
    researcher to pick ``olmo-core-train-1gpu``. Both are names, and names have moved on
    this platform once already -- every workload ended in ``-smoke`` until recently.
    """
    catalog = load_yaml(PROJECT_ROOT / "config" / "workload-catalog.yaml", WorkloadCatalog)
    registered = {workload.name for workload in catalog.workloads}

    named = set(re.findall(r"`(olmo-core-[a-z0-9-]+)`", guide))

    assert named, "the guide names no workload at all, which cannot be right"
    assert named <= registered, (
        f"the guide names workloads that are not in the catalog: {sorted(named - registered)}"
    )


def test_every_form_field_the_guide_documents_is_a_field_the_form_has(
    guide: str,
    workflow: dict[str, Any],
) -> None:
    """Mutation: rename a form input without touching the guide.

    ``project`` became ``experiment`` and ``dataset_release`` is still under discussion, so
    this is a live risk rather than a theoretical one. Only fields the guide puts in
    backticks are checked, because the prose also uses ordinary words like "command".
    """
    inputs = form_inputs(workflow)
    documented = set(re.findall(r"`(commit_sha|workload_profile|team|experiment|wandb_project|command|compute_profile|image_digest|dataset_release|maximum_runtime_hours|maximum_attempts)`", guide))

    assert documented, "the guide documents no form field"
    assert documented <= set(inputs), (
        f"the guide documents fields the form does not have: {sorted(documented - set(inputs))}"
    )


def test_every_environment_variable_the_guide_promises_is_one_the_container_gets() -> None:
    """THE ONE THAT MATTERS. Mutation: drop EDULLM_CHECKPOINT_DIR from the submit request.

    The guide's central instruction is ``--save-folder "$EDULLM_CHECKPOINT_DIR"``. If that
    variable stopped being sent, the guide would be telling thirty-four people to write
    their checkpoints to an empty string -- and the failure is the quiet one the guide
    exists to prevent, because the run still exits zero.

    Read out of ``batch_submit_request`` rather than a list, so the two cannot drift.
    """
    from edullm_platform.execution import batch_submit_request
    from tests.test_phase3_execution import RUN_ID, manifest, target

    # Two requests, because three of the variables the guide documents only exist on a run
    # that named a published corpus. A run submitted with `dataset_release: none` is given no
    # EDULLM_DATASET_ID at all rather than an empty one -- an empty value would read as a
    # resolution that was attempted and failed. So the set the guide is checked against is
    # what a container can be given, which is the union, and the guide says which of the two
    # cases each variable belongs to.
    from tests.test_phase4_training_submission import published_reference

    sent: set[str] = set()
    for dataset_reference in (None, published_reference("regmix-10b-v1")):
        request = batch_submit_request(
            manifest=manifest(),
            target=target(),
            run_id=RUN_ID,
            job_definition=target().job_definition_arn,
            dataset_reference=dataset_reference,
        )
        sent |= {entry["Name"] for entry in request["ContainerOverrides"]["Environment"]}

    guide = GUIDE_PATH.read_text(encoding="utf-8")
    promised = set(re.findall(r"`(EDULLM_[A-Z_]+|WANDB_[A-Z_]+)`", guide))

    assert "EDULLM_CHECKPOINT_DIR" in promised, (
        "the guide no longer mentions the variable its central instruction depends on"
    )
    assert promised <= sent, (
        f"the guide promises variables the container is not given: {sorted(promised - sent)}"
    )


def test_the_guide_names_someone_to_tell_when_a_run_breaks(guide: str) -> None:
    """Mutation: soften it to "open an issue" with nobody attached.

    An unowned intake is one people stop using after the first unanswered issue. The name
    is the difference between a queue and a wall, and it is cheap to assert that one is
    there at all.
    """
    assert "@philote-dev" in guide, "the guide names nobody to tell when a run breaks"


def test_the_guide_leads_with_the_save_folder_rather_than_burying_it(guide: str) -> None:
    """The single most expensive mistake available on this platform, placed accordingly.

    A twelve-hour run that took OLMo-core's ``/tmp`` default trains for twelve hours,
    writes checkpoints onto a machine that then disappears, exits zero, and is recorded as
    a success. A guide that mentioned it in passing at the bottom would be technically
    complete and practically useless, so this asserts it appears in the first half.
    """
    position = guide.index("EDULLM_CHECKPOINT_DIR")

    assert position < len(guide) // 2, (
        "the save-folder instruction has drifted into the second half of the guide; it is "
        "the one line that decides whether a long run produces anything"
    )
