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
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from edullm_platform.admission_handler import (
    _REQUIRED_EVENT_FIELDS,
    AdmissionEventError,
    handler,
)
from tools.build_admission_lambda import ADMISSION_CONFIG

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_MACHINE_PATH = PROJECT_ROOT / "infra" / "admission-state-machine.yaml"

#: Twelve digits that are not this account's. The handler reads the account out of its own
#: invocation context and builds queue and job-definition ARNs from it, and a real account
#: id in a committed test file is the thing every capture tool then has to redact.
ACCOUNT_ID = "123456789012"


class InvocationContext:
    """What Lambda hands a function about the invocation, as far as this handler reads it.

    A stand-in rather than a mock: the handler reads one attribute, and a test that passed
    the account in some other way would exercise a path the deployed function does not
    take. ``invoked_function_arn`` is the whole interface.
    """

    invoked_function_arn = (
        f"arn:aws:lambda:us-east-1:{ACCOUNT_ID}:function:sbsandbox-intern-edullm-admission"
    )


#: A submission that admission accepts, in the shape the submitting workflow sends. The
#: commit and digest are the ones Phase 1 actually published, so nothing here is a shape
#: that could not occur. The compute profile is the one Phase 3 promoted, because admission
#: now refuses a profile nothing backs and the eleven unpromoted ones would be refused for
#: a reason none of these tests is about.
ACCEPTED_EVENT: dict[str, Any] = {
    "run_id": "run_019fa439-203e-70c7-bf8a-9ce33bc71f20",
    "submitter": "philote-dev",
    "approver": "philote-dev",
    "approving_environment": "run-approval-lead",
    "approved_manifest_sha256": (
        "sha256:ea122cba141a80662b4a714337c5d02df3a2cb5073976b12b59d9e221982fb67"
    ),
    # Which repository ReadImageScan described against, resolved from the registry by the
    # submitting workflow. Checked against config/repositories.yaml rather than believed:
    # it is the one field here that says what was looked at rather than what is claimed.
    "ecr_repository": "sbsandbox-intern-edullm-olmo-core",
    "manifest": {
        "schema_version": 1,
        "repository": "OLMo-core",
        "commit_sha": "4204375e6db85abc244ec7f626de8d3cc3511402",
        "image_digest": (
            "sha256:4ebdba1ba3b57096efb4f4647ed41ed5ded4ac9e77e8c9038b7ff24db0bc6db8"
        ),
        "workload_profile": "olmo-core-check-cpu",
        "compute_profile": "cpu-32vcpu",
        "dataset_release": "dolma-2026-07",
        "team": "platform",
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
    # WHAT ReadImageScan PUTS HERE, AND THIS EVENT USED TO CARRY NOTHING.
    #
    # It passed anyway, because the digest above was one of the two this repository excepted
    # by hand -- and a per-digest exception overrides a missing scan, so the handler was
    # admitting a run whose findings nobody had supplied. Retiring those two entries in
    # favour of reviewed vulnerabilities took the cover away and left the test asserting
    # that admission accepts an image it knows nothing about.
    #
    # These are the four criticals the registry actually reports for this digest, read from
    # the account rather than invented, and they are the same four the other published image
    # carries because both inherit them from the same pinned base. So this now exercises the
    # path a pilot submission takes: a real scan, findings that block, and reviews recorded
    # against the vulnerabilities rather than against the image.
    "image_scan": {
        "imageScanStatus": {"status": "COMPLETE"},
        "imageScanFindings": {
            "imageScanCompletedAt": "2026-07-29T01:36:04+00:00",
            "findingSeverityCounts": {"CRITICAL": 4, "HIGH": 8, "MEDIUM": 3},
            "findings": [
                {
                    "name": "CVE-2026-57433",
                    "severity": "CRITICAL",
                    "attributes": [{"key": "package_name", "value": "perl"}],
                },
                {
                    "name": "CVE-2026-12087",
                    "severity": "CRITICAL",
                    "attributes": [{"key": "package_name", "value": "perl"}],
                },
                {
                    "name": "CVE-2026-13221",
                    "severity": "CRITICAL",
                    "attributes": [{"key": "package_name", "value": "perl"}],
                },
                {
                    "name": "CVE-2026-5450",
                    "severity": "CRITICAL",
                    "attributes": [{"key": "package_name", "value": "glibc"}],
                },
            ],
        },
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
    result = handler(ACCEPTED_EVENT, InvocationContext())

    assert result["accepted"] is True
    assert result["reason"] == "accepted"
    assert result["run_id"] == ACCEPTED_EVENT["run_id"]


def test_the_handler_admits_the_same_submission_with_only_the_packaged_config_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop a file from ``ADMISSION_CONFIG`` that the handler reads.

    THE SUFFICIENCY HALF OF THE NARROWING, RUN RATHER THAN REASONED ABOUT. Every other
    check on the packaged config set is static -- the builder declares a list, and
    ``tests/test_lambda_package_closure.py`` compares it against the filenames the packaged
    modules name. Static analysis can say the list is consistent with the source; it cannot
    say the function starts.

    So this stages exactly what the zip carries into an empty directory, points
    ``EDULLM_CONFIG_DIR`` at it, and admits a real submission through the real handler.
    Anything the validator needs and the builder does not package is a FileNotFoundError
    here, on a laptop, instead of on whichever invocation first reached the read.

    Deliberately not built from ``config/`` by exclusion. The directory starts empty and
    receives only the declared names, so a file the handler reads and the builder forgot
    cannot be present by accident -- which is precisely how the old glob hid the question.
    """
    staged = tmp_path / "config"
    staged.mkdir()
    for name in sorted(ADMISSION_CONFIG):
        shutil.copyfile(PROJECT_ROOT / "config" / name, staged / name)
    monkeypatch.setenv("EDULLM_CONFIG_DIR", str(staged))

    result = handler(ACCEPTED_EVENT, InvocationContext())

    assert sorted(path.name for path in staged.iterdir()) == sorted(ADMISSION_CONFIG)
    assert result["accepted"] is True
    assert result["reason"] == "accepted"
    # Reaching an execution target means execution-targets.yaml was read as well as merely
    # present, which is the file the narrowing was most likely to get wrong: the handler
    # loads it last and a submission refused earlier would never touch it.
    assert result["execution"]["submit_request"]["JobQueue"]


def test_the_records_are_mappings_rather_than_strings(_packaged_config: None) -> None:
    # A string here is stored by S3 quoted and escaped, because the SDK integration
    # JSON-encodes whatever the Body path yields. That went live once and every record
    # written that day has to be parsed twice to read.
    result = handler(ACCEPTED_EVENT, InvocationContext())

    for field in ("intent", "decision"):
        assert isinstance(result[field], Mapping), f"{field} must not be a string"

    assert result["intent"]["manifest_sha256"] == ACCEPTED_EVENT["approved_manifest_sha256"]
    assert result["decision"]["run_id"] == result["intent"]["run_id"]


def test_the_records_serialize_back_to_the_bytes_they_were_hashed_from(
    _packaged_config: None,
) -> None:
    # What makes the mapping safe to hand to Step Functions. Its keys are already in
    # canonical order, so an encoder that keeps insertion order and compact separators --
    # which is what the S3 integration was measured to do -- reproduces the canonical
    # bytes exactly. Building the mapping any other way loses this silently.
    result = handler(ACCEPTED_EVENT, InvocationContext())

    for field in ("intent", "decision"):
        record = result[field]
        assert list(record) == sorted(record), f"{field} keys are not in canonical order"
        without_sorting = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with_sorting = json.dumps(
            record, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        assert without_sorting == with_sorting


def test_an_accepted_run_is_answered_with_both_of_the_requests_batch_needs(
    _packaged_config: None,
) -> None:
    """Mutation: return ``submit_request`` alone, as this handler did until now.

    ``batch_register_job_definition_request`` existed, was tested field by field, and was
    called by nothing -- so the digest a submitter declared was validated, gated admission
    through the ECR scan, was written immutably into lineage, and selected nothing. The
    container that ran was whichever one ``infra/batch-compute.yaml`` pinned. Nothing failed
    while that was true, which is why the wiring is asserted here rather than left to the
    two requests each being correct on their own.

    ``JobDefinition`` is the definition being registered rather than the deployed one, and
    the two halves of that matter separately. It is not the target's static ARN, because a
    submission that fell back to it would run the wrong image silently; and it is the exact
    string the registration mints, because the state machine replaces it with the revision
    ARN Batch returns and a submission against some third name would be authorized against
    a definition nobody registered.
    """
    execution = handler(ACCEPTED_EVENT, InvocationContext())["execution"]

    assert set(execution) == {"target", "register_request", "submit_request"}
    assert execution["register_request"]["ContainerProperties"]["Image"].endswith(
        f"@{ACCEPTED_EVENT['manifest']['image_digest']}"
    )
    assert execution["submit_request"]["JobDefinition"] == (
        execution["register_request"]["JobDefinitionName"]
    )
    assert execution["submit_request"]["JobDefinition"] != (
        execution["target"]["job_definition_arn"]
    )


def test_a_refused_run_is_answered_with_no_execution_block_at_all(
    _packaged_config: None,
) -> None:
    """Mutation: emit ``execution`` unconditionally, with the key empty on a refusal.

    The state machine branches on ``accepted`` and lifts ``$.admission.payload.execution``
    on the true branch only, so an execution block on a refused run is not read today. It
    would be one ``InputPath`` away from being read, and what it now carries is a request to
    mint a job definition naming two IAM roles -- so the absence is a control rather than
    tidiness, and it is worth a test of its own now that the block has grown a second
    request.
    """
    refused = {**ACCEPTED_EVENT, "approved_manifest_sha256": "sha256:" + "0" * 64}

    result = handler(refused, InvocationContext())

    assert result["accepted"] is False
    assert "execution" not in result


def test_the_decision_cites_the_policy_on_disk_not_anything_in_the_event(
    _packaged_config: None,
) -> None:
    deployed = yaml.safe_load((PROJECT_ROOT / "config" / "policy.yaml").read_text())
    tampered = {**ACCEPTED_EVENT, "policy": {"policy_version": "attacker-supplied"}}

    decision = handler(tampered, InvocationContext())["decision"]

    assert decision["policy_version"] == deployed["policy_version"]
    assert decision["policy_version"] != "attacker-supplied"


def test_a_manifest_that_does_not_hash_to_the_approved_value_is_refused(
    _packaged_config: None,
) -> None:
    tampered = {**ACCEPTED_EVENT, "approved_manifest_sha256": "sha256:" + "0" * 64}

    result = handler(tampered, InvocationContext())

    assert result["accepted"] is False
    assert result["reason"] == "manifest_hash_mismatch"


def test_an_event_missing_a_field_names_all_of_them(_packaged_config: None) -> None:
    with pytest.raises(AdmissionEventError, match="submitter"):
        handler(
            {key: value for key, value in ACCEPTED_EVENT.items() if key != "submitter"},
            InvocationContext(),
        )


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
    returned = set(handler(ACCEPTED_EVENT, InvocationContext()))
    read: set[str] = set()
    _payload_paths(definition, read)

    assert read, "the definition reads nothing out of the Lambda payload"
    assert read <= returned, f"definition reads keys the handler does not return: {read - returned}"


def test_every_event_field_the_handler_reads_is_a_field_this_payload_sends(
    definition: dict[str, Any],
) -> None:
    # The inbound half of the seam, and the half that was missing. The two tests around
    # this one both read the handler's *answer* against the paths the definition follows.
    # Nothing read the definition's *question* against the fields the handler consumes,
    # so a field the workflow assembled and the handler read with `.get` could be absent
    # from this payload block and default to None on every live run. `experiment` did
    # exactly that: the form collected it, the request carried it, the handler read it,
    # and the state machine never forwarded it -- and the handler tests could not see it,
    # because they build their own event and put the field in themselves.
    #
    # Read off the source rather than by calling, because absence is the defect: an event
    # missing a field the handler treats as optional is indistinguishable at runtime from
    # one that genuinely has nothing to say.
    source = (PROJECT_ROOT / "src" / "edullm_platform" / "admission_handler.py").read_text(
        encoding="utf-8"
    )
    consumed = set(re.findall(r'event\.get\(\s*"([A-Za-z_][A-Za-z0-9_]*)"', source))
    consumed |= set(re.findall(r'_require\(\s*event,\s*"([A-Za-z_][A-Za-z0-9_]*)"', source))
    consumed |= set(_REQUIRED_EVENT_FIELDS)

    payload = definition["States"]["ValidateAndDecide"]["Parameters"]["Payload"]
    sent = {key.removesuffix(".$") for key in payload}

    assert consumed, "no event field reads were found in the handler source"
    assert consumed <= sent, (
        "the handler reads event fields this payload never sends, so they arrive absent "
        f"on every live execution: {sorted(consumed - sent)}"
    )


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


def test_the_lineage_bodies_are_written_as_objects_and_never_re_encoded(
    definition: dict[str, Any],
) -> None:
    # States.JsonToString would turn the mapping back into the string this design just
    # stopped producing, and S3 would store it quoted again.
    states = definition["States"]

    for state, field in (("WriteIntent", "intent"), ("WriteDecision", "decision")):
        parameters = states[state]["Parameters"]
        assert parameters["Body.$"] == f"$.admission.{field}"
        assert "JsonToString" not in parameters["Body.$"]
