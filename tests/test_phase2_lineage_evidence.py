"""What the lineage store actually holds, read from the committed capture.

These are the criteria that could only ever close with evidence. Every test in this
repository stops at the edge of the AWS call, so nothing here substitutes for a run having
happened; what the cited tests prove is that the committed records say what they are read
as saying, and that they validate against the same models the Lambda used to write them.

The store holds five submissions from four different paths: two accepted routine runs, one
accepted admin exception, and two refusals of a deliberately tampered manifest hash. The
refusals matter as much as the acceptances -- a decision record with ``accepted: false`` is
what makes a refused submission attributable rather than merely absent.

**One pair is stored in an older shape and is captured rather than hidden.** Records
written before 2026-07-27 are a JSON string rather than an object, because the S3 SDK
integration encodes whatever the Body path yields and the handler was returning canonical
strings. The capture records ``canonical`` per object, so the store's history is visible.
A capture that quietly omitted the older shape would make it look more uniform than it is,
and the first person to read one of those objects would meet a surprise nobody wrote down.

The two digests are kept apart throughout. ``checksum_sha256`` is what S3 computed over the
bytes it holds; ``manifest_sha256`` is what the platform computed over the manifest's
canonical serialization and is the value an approval was taken against.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edullm_platform.canonical import canonical_json_bytes, sha256_digest
from edullm_platform.contracts.admission import DecisionRecord, IntentRecord
from edullm_platform.phase2_evidence import AdmissionExecutionInventory, LineageInventory

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CAPTURE_DIR = PROJECT_ROOT / "fixtures" / "evidence" / "phase-2"
RECORDS_DIR = CAPTURE_DIR / "lineage" / "records"


@pytest.fixture(scope="module")
def lineage() -> LineageInventory:
    return LineageInventory.model_validate(
        json.loads((CAPTURE_DIR / "lineage.sanitized.json").read_text(encoding="utf-8"))
    )


@pytest.fixture(scope="module")
def executions() -> AdmissionExecutionInventory:
    return AdmissionExecutionInventory.model_validate(
        json.loads((CAPTURE_DIR / "executions.sanitized.json").read_text(encoding="utf-8"))
    )


def _decoded(body: bytes) -> dict[str, object]:
    """The record inside these bytes, whichever shape they were written in.

    Two shapes exist in the store and both are real history. Records written after
    2026-07-27 are the canonical object. Records written before are a JSON string that
    contains the object, because the S3 SDK integration encodes whatever the Body path
    yields and the handler was returning canonical strings. A reader that handled only one
    shape would either skip the older records or crash on them; both would misreport what
    the lineage store holds.
    """
    parsed = json.loads(body)
    if isinstance(parsed, str):
        parsed = json.loads(parsed)
    assert isinstance(parsed, dict)
    return parsed


def _records(kind: str) -> dict[str, bytes]:
    return {path.stem: path.read_bytes() for path in sorted((RECORDS_DIR / kind).glob("*.json"))}


def test_every_stored_object_carries_a_checksum_and_a_version(
    lineage: LineageInventory,
) -> None:
    # S3-attested, not computed by us. The bucket is versioned and every write asks for a
    # SHA-256, so an object missing either would mean a write took a path the template
    # does not describe.
    assert lineage.objects
    for stored in lineage.objects:
        assert stored.checksum_sha256, stored.key
        assert stored.version_id, stored.key
        assert stored.content_length > 0, stored.key


def test_the_object_checksum_is_not_the_manifest_hash(lineage: LineageInventory) -> None:
    # Distinct on purpose. Conflating them would be a lineage error: one attests that the
    # bytes arrived intact, the other that this is the manifest somebody approved.
    intents = _records("intent")
    assert intents
    for name, body in intents.items():
        record = IntentRecord.model_validate(_decoded(body))
        stored = next(o for o in lineage.objects if o.key == f"intent/{name}.json")
        assert stored.checksum_sha256 != record.manifest_sha256.removeprefix("sha256:")


def test_every_intent_and_decision_join_by_run_id(lineage: LineageInventory) -> None:
    intents = _records("intent")
    decisions = _records("decision")

    assert set(intents) == set(decisions), "an intent without its decision, or the reverse"
    for name in intents:
        intent = IntentRecord.model_validate(_decoded(intents[name]))
        decision = DecisionRecord.model_validate(_decoded(decisions[name]))
        assert intent.run_id == decision.run_id == name
        assert intent.manifest_sha256 == decision.manifest_sha256


def test_the_manifest_in_every_intent_still_hashes_to_its_recorded_value() -> None:
    # The property the whole approval gate rests on: the hash recorded beside a manifest is
    # the hash of that manifest. Recomputed here from the stored bytes, so a record whose
    # manifest was edited after the fact would fail rather than read as intact.
    for name, body in _records("intent").items():
        record = IntentRecord.model_validate(_decoded(body))
        assert sha256_digest(record.manifest) == record.manifest_sha256, name


def test_records_written_after_the_encoding_fix_are_the_canonical_bytes(
    lineage: LineageInventory,
) -> None:
    # The claim the design makes about itself: what S3 stores is the canonical
    # serialization rather than a re-encoding of it. True of every object written after
    # 2026-07-27, and the capture records which those are rather than assuming all of them.
    canonical = [stored for stored in lineage.objects if stored.canonical]
    assert canonical, "no object in the store is canonical, which the design requires"

    for stored in canonical:
        kind, _, name = stored.key.partition("/")
        body = (RECORDS_DIR / kind / name).read_bytes()
        model = IntentRecord if kind == "intent" else DecisionRecord
        assert body == canonical_json_bytes(model.model_validate(_decoded(body))), stored.key


def test_the_older_shape_is_recorded_rather_than_hidden(lineage: LineageInventory) -> None:
    # A capture that dropped these would make the store look uniform and leave the first
    # person to read one of these objects meeting a surprise nobody wrote down. They parse
    # as a JSON string that itself contains the record.
    older = [stored for stored in lineage.objects if not stored.canonical]
    for stored in older:
        kind, _, name = stored.key.partition("/")
        # Deliberately the raw parse rather than _decoded, which is the thing under test.
        parsed = json.loads((RECORDS_DIR / kind / name).read_bytes())
        assert isinstance(parsed, str), stored.key
        assert isinstance(json.loads(parsed), dict), stored.key


def test_a_refused_submission_still_earns_an_attributable_decision() -> None:
    # Admission failing must not mean admission being silent. A tampered manifest hash is
    # refused, and the refusal is recorded with its reason against the run id that was
    # attempted -- which is what makes a rejected path attributable rather than absent.
    refused = [
        DecisionRecord.model_validate(_decoded(body))
        for body in _records("decision").values()
        if not DecisionRecord.model_validate(_decoded(body)).accepted
    ]

    assert refused, "no refusal is captured, so the rejected path is unproved"
    for decision in refused:
        assert decision.reason.value == "manifest_hash_mismatch"
        assert decision.run_id
        assert decision.policy_version


def test_every_decision_carries_the_five_fields_the_master_plan_names() -> None:
    # Actor, manifest hash, policy version, decision and run id. Named explicitly in the
    # master plan, so a record missing one is a gate failure rather than a cosmetic gap.
    # The actor is absent only where no authorization was evaluated at all, which is the
    # one case the model permits and only for a hash mismatch.
    decisions = [
        DecisionRecord.model_validate(_decoded(body))
        for body in _records("decision").values()
    ]

    assert decisions
    for decision in decisions:
        assert decision.run_id
        assert decision.manifest_sha256.startswith("sha256:")
        assert decision.policy_version
        assert decision.reason.value
        if decision.authorization is not None:
            assert decision.authorization.approver or decision.authorization.submitter


def test_an_accepted_routine_run_was_released_by_the_lead_gate() -> None:
    accepted = [
        DecisionRecord.model_validate(_decoded(body))
        for body in _records("decision").values()
        if DecisionRecord.model_validate(_decoded(body)).accepted
    ]
    routine = [d for d in accepted if d.approval_class.value == "routine"]

    assert routine, "no accepted routine run is captured"
    for decision in routine:
        assert decision.approving_environment.value == "run-approval-lead"
        assert decision.authorization is not None
        assert decision.authorization.granted is True


def test_an_accepted_exception_was_released_by_the_admin_gate_and_priced() -> None:
    # The exception path, and the cost that made it one. The classification is re-derived
    # inside AWS and compared against the gate that released it, so a record showing an
    # exception released by run-approval-admin is that comparison having passed.
    accepted = [
        DecisionRecord.model_validate(_decoded(body))
        for body in _records("decision").values()
        if DecisionRecord.model_validate(_decoded(body)).accepted
    ]
    exceptions = [d for d in accepted if d.approval_class.value == "exception"]

    assert exceptions, "no accepted exception is captured"
    for decision in exceptions:
        assert decision.approving_environment.value == "run-approval-admin"
        assert decision.cost is not None
        assert decision.cost.maximum_compute_cost_usd > 0


def test_no_run_id_appears_twice_in_the_store(lineage: LineageInventory) -> None:
    # The consequent of the duplicate-name guarantee. Each run id owns exactly one intent
    # and one decision; a second execution under a name that has closed is refused before
    # it could write either.
    keys = [stored.key for stored in lineage.objects]

    assert len(keys) == len(set(keys))
    assert len([k for k in keys if k.startswith("intent/")]) == len(
        [k for k in keys if k.startswith("decision/")]
    )


def test_the_executions_account_for_every_record_and_their_failures_are_named(
    executions: AdmissionExecutionInventory,
    lineage: LineageInventory,
) -> None:
    # An execution that failed inside the state machine and one that refused a submission
    # are different events, and the error name is what tells them apart. AdmissionRejected
    # is the validator's answer; States.Runtime is the machine itself breaking, which
    # happened once, before the handler and the definition agreed on a payload shape.
    assert executions.executions
    names = {execution.name for execution in executions.executions}
    written = {stored.key.split("/", 1)[1].removesuffix(".json") for stored in lineage.objects}

    assert written <= names, sorted(written - names)
    errors = {e.error for e in executions.executions if e.status == "FAILED"}
    assert "AdmissionRejected" in errors
