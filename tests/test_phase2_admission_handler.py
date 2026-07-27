"""The handler's answer, and whether the state machine can read it.

Nothing called :func:`edullm_platform.admission_handler.handler` before this module
existed. The state machine's shape was asserted, the admission core's decisions were
asserted, and the seam between them was asserted by neither -- so the definition read
``$.Payload.intent`` from a handler that returns ``intent_body`` and every test stayed
green. The first live execution failed at ``States.Runtime`` on that exact path, after the
Lambda had already decided the submission correctly.

That is the failure this repository has met before: assertions that compare the text of an
expression without checking whether it names anything real. The cure is not another
literal. :func:`test_every_payload_path_the_definition_reads_is_a_key_the_handler_returns`
runs the handler, reads the JSONPaths out of the deployed template, and compares the two
sets. A field renamed on either side fails here rather than in an execution.

The handler is also where "policy is what AWS deployed" stops being a claim about
packaging and becomes a testable property, so the decision's ``policy_version`` is checked
against the configuration on disk rather than against anything in the event.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from edullm_platform.admission_handler import (
    AdmissionEventError,
    handler,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_MACHINE_PATH = PROJECT_ROOT / "infra" / "admission-state-machine.yaml"

#: A submission that admission accepts, in the shape the submitting workflow sends. The
#: commit and digest are the ones Phase 1 actually published, so nothing here is a shape
#: that could not occur.
ACCEPTED_EVENT: dict[str, Any] = {
    "run_id": "run_019fa439-203e-70c7-bf8a-9ce33bc71f20",
    "submitter": "philote-dev",
    "approver": "philote-dev",
    "approving_environment": "run-approval-lead",
    "approved_manifest_sha256": (
        "sha256:819aaf8aef45e1ac51efed1924e1455f7fca7356f1c3ee1067fa934558e6075c"
    ),
    "manifest": {
        "schema_version": 1,
        "repository": "OLMo-core",
        "commit_sha": "4204375e6db85abc244ec7f626de8d3cc3511402",
        "image_digest": (
            "sha256:4ebdba1ba3b57096efb4f4647ed41ed5ded4ac9e77e8c9038b7ff24db0bc6db8"
        ),
        "workload_profile": "olmo-core-train-smoke",
        "compute_profile": "gpu-4xa10g",
        "dataset_release": "dolma-2026-07",
        "team": "memory-split",
        "wandb_project": "olmo-core-memory-split",
        "command": ["python", "-m", "olmo_core.train", "--config", "smoke"],
        "maximum_runtime_hours": "1",
        "maximum_attempts": 1,
        "fanout": None,
        "checkpoint": {
            "interval_minutes": 30,
            "destination_prefix": "s3://sbsandbox-intern-edullm-checkpoints/runs/",
            "resume_required": False,
        },
    },
    "workflow_run": {
        # Ten digits, and synthetic. Every real GitHub run id is eleven, and the tree
        # scanner in test_evidence treats any eleven-digit int as an account id whose
        # leading zero was dropped -- which this account's would be. A real run id here
        # fails that check rather than this one, from a file that holds no account id.
        "run_id": 1234567890,
        "run_attempt": 1,
        "run_repository": "edu-llm/platform",
        "workflow_repository": "edu-llm/platform",
        "workflow_path": ".github/workflows/submit-run.yml",
        "workflow_ref": "refs/heads/main",
    },
}


@pytest.fixture(autouse=True)
def _packaged_config(monkeypatch: pytest.MonkeyPatch) -> None:
    # The deployed Lambda reads config from inside its own package. Here it reads the
    # repository's, which is the same content the packaging tool copies in.
    monkeypatch.setenv("EDULLM_CONFIG_DIR", str(PROJECT_ROOT / "config"))


@pytest.fixture(scope="module")
def definition() -> dict[str, Any]:
    class Loader(yaml.SafeLoader):
        pass

    def multi(loader: yaml.Loader, suffix: str, node: yaml.Node) -> Any:
        if isinstance(node, yaml.ScalarNode):
            return {f"Fn::{suffix}": loader.construct_scalar(node)}
        if isinstance(node, yaml.SequenceNode):
            return {f"Fn::{suffix}": loader.construct_sequence(node)}
        return {f"Fn::{suffix}": loader.construct_mapping(node)}

    Loader.add_multi_constructor("!", multi)
    template = yaml.load(STATE_MACHINE_PATH.read_text(encoding="utf-8"), Loader=Loader)
    machine = next(
        resource
        for resource in template["Resources"].values()
        if resource["Type"] == "AWS::StepFunctions::StateMachine"
    )
    body = machine["Properties"]["DefinitionString"]["Fn::Sub"]
    # Only the ARN placeholders differ per account; nothing here reads them.
    body = re.sub(r"\$\{[^}]+\}", "PLACEHOLDER", body)
    parsed: dict[str, Any] = json.loads(body)
    return parsed


def test_the_handler_admits_a_routine_submission_a_lead_released(
    _packaged_config: None,
) -> None:
    result = handler(ACCEPTED_EVENT)

    assert result["accepted"] is True
    assert result["reason"] == "accepted"
    assert result["run_id"] == ACCEPTED_EVENT["run_id"]


def test_the_records_are_canonical_json_strings_rather_than_structures(
    _packaged_config: None,
) -> None:
    # The state machine writes these verbatim, so they have to arrive as the bytes that
    # were hashed. A structure here would be re-serialised on the way to S3 by an encoder
    # nobody chose, and the stored object would stop matching its own digest.
    result = handler(ACCEPTED_EVENT)

    for field in ("intent_body", "decision_body"):
        assert isinstance(result[field], str)
        json.loads(result[field])

    intent = json.loads(result["intent_body"])
    decision = json.loads(result["decision_body"])
    assert intent["manifest_sha256"] == ACCEPTED_EVENT["approved_manifest_sha256"]
    assert decision["run_id"] == intent["run_id"]


def test_the_decision_cites_the_policy_on_disk_not_anything_in_the_event(
    _packaged_config: None,
) -> None:
    deployed = yaml.safe_load((PROJECT_ROOT / "config" / "policy.yaml").read_text())
    tampered = {**ACCEPTED_EVENT, "policy": {"policy_version": "attacker-supplied"}}

    decision = json.loads(handler(tampered)["decision_body"])

    assert decision["policy_version"] == deployed["policy_version"]
    assert decision["policy_version"] != "attacker-supplied"


def test_a_manifest_that_does_not_hash_to_the_approved_value_is_refused(
    _packaged_config: None,
) -> None:
    tampered = {**ACCEPTED_EVENT, "approved_manifest_sha256": "sha256:" + "0" * 64}

    result = handler(tampered)

    assert result["accepted"] is False
    assert result["reason"] == "manifest_hash_mismatch"


def test_an_event_missing_a_field_names_all_of_them(_packaged_config: None) -> None:
    with pytest.raises(AdmissionEventError, match="submitter"):
        handler({key: value for key, value in ACCEPTED_EVENT.items() if key != "submitter"})


def _payload_paths(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        for value in node.values():
            _payload_paths(value, found)
    elif isinstance(node, list):
        for value in node:
            _payload_paths(value, found)
    elif isinstance(node, str):
        found.update(re.findall(r"\$\.Payload\.([A-Za-z_][A-Za-z0-9_]*)", node))


def _admission_paths(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        for value in node.values():
            _admission_paths(value, found)
    elif isinstance(node, list):
        for value in node:
            _admission_paths(value, found)
    elif isinstance(node, str):
        found.update(re.findall(r"\$\.admission\.([A-Za-z_][A-Za-z0-9_]*)", node))


def test_every_payload_path_the_definition_reads_is_a_key_the_handler_returns(
    definition: dict[str, Any],
    _packaged_config: None,
) -> None:
    # The test this module exists for. Both sides are read from the thing itself: the
    # keys by running the handler, the paths by parsing the committed template. A rename
    # on either side fails here rather than at States.Runtime in a live execution, which
    # is where it failed the first time.
    returned = set(handler(ACCEPTED_EVENT))
    read: set[str] = set()
    _payload_paths(definition, read)

    assert read, "the definition reads nothing out of the Lambda payload"
    assert read <= returned, f"definition reads keys the handler does not return: {read - returned}"


def test_every_admission_path_the_definition_reads_is_one_the_result_selector_makes(
    definition: dict[str, Any],
) -> None:
    # The second half of the same seam. ResultSelector renames the payload into
    # $.admission, so a state can also read a field that selector never produced.
    selector = definition["States"]["ValidateAndDecide"]["ResultSelector"]
    produced = {key.removesuffix(".$") for key in selector}
    read: set[str] = set()
    _admission_paths(definition, read)

    assert read
    assert read <= produced, f"states read fields the selector does not make: {read - produced}"


def test_the_lineage_bodies_are_written_without_being_re_encoded(
    definition: dict[str, Any],
) -> None:
    # States.JsonToString here would defeat the reason the handler returns strings.
    states = definition["States"]

    for state, field in (("WriteIntent", "intent_body"), ("WriteDecision", "decision_body")):
        parameters = states[state]["Parameters"]
        assert parameters["Body.$"] == f"$.admission.{field}"
        assert "JsonToString" not in parameters["Body.$"]
