import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from edullm_platform.canonical import sha256_digest
from edullm_platform.contracts.manifest import (
    RunInput,
    RunManifest,
    RunManifestV2,
    read_run_manifest,
)
from edullm_platform.contracts.vocabulary import InputRole

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

V2_DOCUMENT = {
    "schema_version": 2,
    "repository": "olmo-eval-full",
    "commit_sha": "a" * 40,
    "image_digest": "sha256:" + "b" * 64,
    "inputs": [{"role": "weights", "reference": "model-smollm2-135m-v1"}],
    "command": ["olmo-eval", "run", "--harness", "default"],
    "team": "eval-inference",
    "wandb_project": "eval-inference",
    "workload_profile": "olmo-eval-sweep",
    "compute_profile": "gpu-1xl4",
    "maximum_runtime_hours": "1",
    "maximum_attempts": 1,
    "checkpoint": None,
    "fanout": None,
}


def intent_records() -> list[Path]:
    return sorted((REPOSITORY_ROOT / "fixtures" / "evidence").rglob("records/intent/run_*.json"))


def read_record(record_path: Path) -> dict[str, Any]:
    """One committed record, whichever of the two encodings it was written in.

    Five of the nine hold a JSON object and one holds that object as a JSON string, which
    is what a recorder writing `json.dumps` twice produces. Both are in the store and a
    reader that understood one would quietly skip the other, so the shape is absorbed here
    rather than by narrowing the glob.
    """
    document = json.loads(record_path.read_text())
    if isinstance(document, str):
        document = json.loads(document)
    assert isinstance(document, dict)
    return document


def test_there_are_committed_version_one_records_to_protect():
    # Without this the digest test below can pass over an empty list, which is the exact
    # shape of a test that cannot fail.
    assert len(intent_records()) >= 5


def test_both_committed_encodings_are_covered():
    # The corpus holds a doubly-encoded record and singly-encoded ones. If a future sweep
    # rewrote them all into one shape, read_record's second json.loads would stop being
    # exercised and this test says so rather than leaving a dead branch behind.
    encodings = {type(json.loads(path.read_text())).__name__ for path in intent_records()}
    assert encodings == {"dict", "str"}


@pytest.mark.parametrize("record_path", intent_records(), ids=lambda path: path.stem)
def test_every_committed_record_still_parses_as_version_one_and_still_hashes(record_path):
    record = read_record(record_path)
    manifest = read_run_manifest(record["manifest"])
    assert isinstance(manifest, RunManifest)
    assert manifest.schema_version == 1
    assert sha256_digest(manifest) == record["manifest_sha256"]


def test_a_version_two_document_reads_as_version_two():
    manifest = read_run_manifest(V2_DOCUMENT)
    assert isinstance(manifest, RunManifestV2)
    assert manifest.inputs == (RunInput(role=InputRole.WEIGHTS, reference="model-smollm2-135m-v1"),)


def test_the_reader_refuses_a_version_it_does_not_know():
    with pytest.raises(ValueError, match="run manifest schema version 3"):
        read_run_manifest({**V2_DOCUMENT, "schema_version": 3})


def test_the_reader_refuses_a_document_that_declares_no_version():
    # A manifest with the key missing is not a version-one manifest. Defaulting to 1 would
    # read a truncated v2 -- one whose `schema_version` was dropped in transit -- as a v1
    # that happens to be missing dataset_release, and the refusal would name the wrong field.
    with pytest.raises(ValueError, match="run manifest schema version None"):
        read_run_manifest(
            {key: value for key, value in V2_DOCUMENT.items() if key != "schema_version"}
        )


def test_a_version_two_document_is_not_accepted_as_version_one():
    # The bug this catches: a reader that falls back to RunManifest when RunManifestV2 fails,
    # which would silently drop every input and record a run as having read nothing.
    with pytest.raises(ValidationError):
        RunManifest.model_validate(V2_DOCUMENT)


def test_a_version_one_document_is_not_accepted_as_version_two():
    # The other direction, and it is the one that keeps the reader honest about the store.
    # If RunManifestV2 accepted a v1 document, `inputs` would be absent from both models on
    # the same bytes and every assertion below comparing the two would be comparing nothing.
    version_one = {
        **{key: value for key, value in V2_DOCUMENT.items() if key != "inputs"},
        "schema_version": 1,
        "dataset_release": "model-smollm2-135m-v1",
    }
    with pytest.raises(ValidationError):
        RunManifestV2.model_validate(version_one)


def test_the_two_versions_carry_different_fields_rather_than_the_same_ones():
    # The bug this catches is the one named in the brief: a schema test that passes because
    # the field it inspects is absent from both sides. Asserting the field sets directly
    # means a RunManifestV2 that forgot `inputs`, or one that kept `dataset_release`, fails
    # here rather than passing every comparison below by symmetry.
    version_one = set(RunManifest.model_fields)
    version_two = set(RunManifestV2.model_fields)
    assert "dataset_release" in version_one and "dataset_release" not in version_two
    assert "inputs" in version_two and "inputs" not in version_one
    assert version_one - {"dataset_release"} == version_two - {"inputs"}


def test_the_two_versions_do_not_hash_alike():
    # Content addressing is the whole reason there are two models. If a v1 and a v2 describing
    # the same run collided, the version would not be part of what an approver released.
    as_v1 = RunManifest.model_validate(
        {
            **{key: value for key, value in V2_DOCUMENT.items() if key != "inputs"},
            "schema_version": 1,
            "dataset_release": "model-smollm2-135m-v1",
        }
    )
    assert sha256_digest(as_v1) != sha256_digest(read_run_manifest(V2_DOCUMENT))


def test_inputs_must_be_named_once_each_in_a_stated_order():
    duplicated = {
        **V2_DOCUMENT,
        "inputs": [
            {"role": "weights", "reference": "model-smollm2-135m-v1"},
            {"role": "weights", "reference": "model-smollm2-135m-v1"},
        ],
    }
    with pytest.raises(ValidationError, match="once each"):
        RunManifestV2.model_validate(duplicated)


def test_inputs_out_of_order_are_refused_as_well_as_repeated_ones():
    # Sorting them silently would make two manifests describing the same run hash alike
    # while a third, written by hand in the other order, hashed differently. The order is
    # part of the content, so it is checked rather than repaired.
    out_of_order = {
        **V2_DOCUMENT,
        "inputs": [
            {"role": "weights", "reference": "model-smollm2-135m-v1"},
            {"role": "corpus", "reference": "pretrain-fineweb-edu-1b-v6"},
        ],
    }
    with pytest.raises(ValidationError, match="once each"):
        RunManifestV2.model_validate(out_of_order)


def test_a_run_names_at_least_one_input():
    with pytest.raises(ValidationError):
        RunManifestV2.model_validate({**V2_DOCUMENT, "inputs": []})


def test_an_input_naming_a_role_the_platform_does_not_have_is_refused():
    with pytest.raises(ValidationError):
        RunManifestV2.model_validate(
            {
                **V2_DOCUMENT,
                "inputs": [{"role": "tokenizer", "reference": "tokenizer-dolma2-bpe-v1"}],
            }
        )
