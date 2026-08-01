"""A scan read that follows the submission instead of a constant.

Phase 3 built the admission state machine around one repository because one repository was
all there was, and it wrote that repository's name into ``ReadImageScan`` as a literal. The
cost of that shortcut is not paid by the repository it names. It is paid by the second one:
its submission compiles, its digest resolves correctly, and the describe call is pointed at
somebody else's images, where the digest is not found. The fail-closed ``Catch`` then does
exactly what it was built to do and the run is denied for unreviewed findings -- a true
sentence about a scan that was never read, delivered after a lead had already approved the
run.

So the repository name has to travel with the submission. It cannot be computed inside the
state machine: ``ReadImageScan`` is the first state and runs before the validator, and the
ECR repository is not a pure function of the GitHub one -- ``edullm-data`` publishes to
``sbsandbox-intern-edullm-data``, so ``States.Format`` cannot build it and a ``Choice`` state
would be the hand-maintained mapping this arrangement exists to delete.

That leaves the submitting workflow, which already reads the registry, putting the name into
the admission request. **Which makes it a field the caller supplies**, and this module's last
two tests are about the consequence: a hand-started execution could aim the scan read at a
repository whose findings are clean and have the validator judge the wrong image favourably.
The validator therefore re-derives the name from ``manifest.repository`` against the registry
inside its own zip and refuses when the two disagree. The field is a hint about where to
look; the registry remains the authority on where the image lives.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from infrastructure_support import load_template, resource_of_type

# The canonical accepted event and its invocation stand-in, imported rather than copied.
# It is sixty lines describing one real submission -- a published digest, the four criticals
# ECR actually reports for it, the promoted compute profile -- and a second copy would be a
# second thing to keep true. Importing it also means these tests follow it: if the shape the
# submitting workflow sends changes, the copy that changes is the one they read.
from test_phase2_admission_handler import ACCEPTED_EVENT, InvocationContext
from workflow_support import load_workflow, step

from edullm_platform.admission_handler import AdmissionEventError, handler
from edullm_platform.config import load_yaml
from edullm_platform.contracts.repository_registry import RepositoryRegistry

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_MACHINE_PATH = PROJECT_ROOT / "infra" / "admission-state-machine.yaml"
SUBMIT_WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "submit-run.yml"
REPOSITORY_REGISTRY_PATH = PROJECT_ROOT / "config" / "repositories.yaml"

REGISTRY_STEP = "Resolve the registered image repository"
ASSEMBLE_STEP = "Assemble the admission request"


def state_machine_definition() -> dict[str, Any]:
    template = load_template(STATE_MACHINE_PATH)
    _logical_id, machine = resource_of_type(template, "AWS::StepFunctions::StateMachine")
    parsed = json.loads(machine["Properties"]["DefinitionString"]["Fn::Sub"])
    assert isinstance(parsed, dict)
    return parsed


def registered_ecr_repositories() -> dict[str, str]:
    """Every registered repository, mapped to where its images live.

    Every *registered* one rather than every submittable one, unlike the grant tests in
    tests/test_phase3_infrastructure.py. A grant is checked against what can be submitted
    today because an unused grant is surplus permission. A hardcoded name is checked against
    what is registered, because the registration is what makes the name a thing this
    template could wrongly pin tomorrow.
    """
    registry = load_yaml(REPOSITORY_REGISTRY_PATH, RepositoryRegistry)
    return {entry.repository: entry.ecr_repository for entry in registry.repositories}


def event_with(**overrides: Any) -> dict[str, Any]:
    return {**ACCEPTED_EVENT, **overrides}


@pytest.fixture
def _packaged_config(monkeypatch: pytest.MonkeyPatch) -> None:
    # The deployed Lambda reads config from inside its own package; here it reads the
    # repository's, which is the content the packaging tool copies in. That matters more
    # than usual for these two tests: the registry this handler re-derives against is the
    # one in the zip, and pointing them at a fixture registry would prove the check works
    # against a file nothing deploys.
    monkeypatch.setenv("EDULLM_CONFIG_DIR", str(PROJECT_ROOT / "config"))


def test_every_registered_repository_can_have_its_scan_read_because_the_name_is_not_pinned() -> (
    None
):
    """Reads BOTH sides. Mutation: register a third repository and pin its name here.

    The narrow test beside the other ``ReadImageScan`` assertions pins the one expression
    this parameter should hold. This one asks the question that outlives the fix: does the
    definition mention any registered ECR repository by name at all? A ``Choice`` state
    mapping repositories to repositories would pass the narrow test -- ``RepositoryName.$``
    would still be a reference -- and would reintroduce exactly the hand-maintained list
    whose staleness caused the original defect.
    """
    definition = json.dumps(state_machine_definition())
    pinned = sorted(
        f"{repository} (as {ecr_repository})"
        for repository, ecr_repository in registered_ecr_repositories().items()
        if ecr_repository in definition
    )

    assert not pinned, (
        "the admission state machine names "
        f"{', '.join(pinned)} literally. A registered repository written into this "
        "definition is a mapping that has to be edited every time the registry grows, "
        "which is the failure reading the name from the request exists to remove."
    )


def test_the_admission_request_carries_the_ecr_repository_the_registry_records_for_the_submission() -> (
    None
):
    """Mutation: leave the registry lookup below the assembly step, where it starts.

    ``ecr_repository`` has been read out of config/repositories.yaml in this job since
    Phase 3, for the Batch denial probes -- but it is resolved *after* the request is
    assembled and eleven steps after it would be needed. Ordering matters here in a way it
    usually does not: a step output referenced before the step runs is the empty string
    rather than an error, so a request assembled above this step would carry
    ``"ecr_repository": ""`` and the describe would fail on a repository name nobody wrote.
    """
    submit = load_workflow(SUBMIT_WORKFLOW_PATH)["jobs"]["submit"]
    names = [candidate.get("name") for candidate in submit["steps"]]
    assemble = step(submit, ASSEMBLE_STEP)

    assert names.index(REGISTRY_STEP) < names.index(ASSEMBLE_STEP), (
        "the ECR repository is resolved after the request that has to carry it is built"
    )
    assert assemble["env"]["ECR_REPOSITORY"] == "${{ steps.registry.outputs.ecr_repository }}"
    assert '"ecr_repository": required("ECR_REPOSITORY")' in assemble["run"]


@pytest.mark.usefixtures("_packaged_config")
def test_the_validator_refuses_a_request_whose_ecr_repository_is_not_the_one_the_registry_records() -> (
    None
):
    """Mutation: trust the field. Take ``ecr_repository`` and describe against it.

    This is the price of moving the name into the request, and it is worth naming plainly:
    a field the caller supplies is a field the caller chooses. Every other field in this
    event is either the caller's own claim, which policy judges, or read by the state
    machine itself. ``ecr_repository`` is the first that is neither -- a caller's value used
    to decide *what to look at* rather than *what to allow*.

    The registry inside the deployed zip is the authority, so the disagreement is the
    refusal, and it is an event error rather than a decision. A decision record describes a
    submission that was judged; this request was never one the submitting workflow could
    produce, so there is nothing to judge and nothing honest to record about it.
    """
    registered = registered_ecr_repositories()
    claimed = ACCEPTED_EVENT["manifest"]["repository"]
    somebody_elses = next(
        ecr_repository
        for repository, ecr_repository in registered.items()
        if repository != claimed
    )

    with pytest.raises(AdmissionEventError) as refusal:
        handler(event_with(ecr_repository=somebody_elses), InvocationContext())

    assert "ecr_repository" in str(refusal.value)
    assert somebody_elses in str(refusal.value)
    assert registered[claimed] in str(refusal.value)


@pytest.mark.usefixtures("_packaged_config")
def test_a_scan_read_against_the_wrong_repository_is_a_refusal_and_not_a_clean_summary() -> None:
    """Mutation: check the field only when the findings do not already pass.

    The test above proves the disagreement is caught. This one proves it is caught *first*,
    which is the half that matters, because the attack is not pointing the scan somewhere
    broken -- it is pointing it somewhere clean. A COMPLETE scan with no findings at all is
    the most admissible input this handler can be given, and it must not buy a single step
    of progress when the repository it describes is not the repository the manifest names.
    """
    somebody_elses = next(
        ecr_repository
        for repository, ecr_repository in registered_ecr_repositories().items()
        if repository != ACCEPTED_EVENT["manifest"]["repository"]
    )
    spotless = {
        "imageScanStatus": {"status": "COMPLETE"},
        "imageScanFindings": {
            "imageScanCompletedAt": "2026-07-29T01:36:04+00:00",
            "findingSeverityCounts": {},
            "findings": [],
        },
    }

    with pytest.raises(AdmissionEventError):
        handler(
            event_with(ecr_repository=somebody_elses, image_scan=spotless),
            InvocationContext(),
        )
