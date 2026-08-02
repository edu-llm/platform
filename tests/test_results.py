from collections.abc import Callable

import pytest
from pydantic import ValidationError

from edullm_platform.canonical import canonical_json_bytes, sha256_digest
from edullm_platform.contracts.base import ContractModel
from edullm_platform.contracts.lifecycle import (
    AttemptTerminalState,
    CheckpointRef,
    LogicalRun,
)
from edullm_platform.contracts.results import (
    CheckpointManifest,
    CheckpointNotResumableError,
    ResultManifest,
    WandbRunRef,
)
from edullm_platform.contracts.vocabulary import RetentionClass

STABLE_RUN_ID = "run_01994f2a-1c00-7c3b-8f4d-2a5b6c7d8e9f"
RESUMED_RUN_ID = "run_01994f2c-3e00-7b5d-8c4e-5d6e7f8a9b0c"
STABLE_ATTEMPT_ID = "att_01994f2a-1c00-7c3b-9a1b-2c3d4e5f6a7b"

CHECKPOINT_URI = "s3://sbsandbox-intern-edullm-checkpoints/runs/olmo/step-1000/"
LATER_CHECKPOINT_URI = "s3://sbsandbox-intern-edullm-checkpoints/runs/olmo/step-2000/"
SUCCESS_MARKER_URI = CHECKPOINT_URI + ".checkpoint-complete"
OUTPUT_PREFIX = "s3://sbsandbox-intern-edullm-outputs/runs/olmo/"
CHECKPOINT_DIGEST = "sha256:" + "b" * 64
LATER_CHECKPOINT_DIGEST = "sha256:" + "c" * 64

CHECKPOINT_MANIFEST_DIGEST = (
    "sha256:40895c7e549ac5aa0aa0fc524f0310e9ed567484f6f64482997b4829c717e45d"
)
# Moved when ``checkpoint_survey`` was added, and only this one moved, which is the shape
# of the change: ``CheckpointManifest`` above is untouched, so a checkpoint recorded before
# and after this serializes identically and only the record wrapping it grew a field.
#
# The field is optional and defaults to None, for the reason ``exit_code`` beside it is, so
# every result record already in the lineage store still validates. What it does not do is
# still serialize to the same bytes -- a None is written rather than omitted -- so this
# digest is a new one rather than a preserved one, exactly as it was when ``exit_code``
# arrived.
RESULT_MANIFEST_DIGEST = "sha256:f5992720ad1b68ea30e1d2b147563df71ec55a60d5a703fb08589ac6168a2ba2"

OUTSIDE_SANDBOX_PREFIXES = (
    "s3://edullm-checkpoints/runs/olmo/",
    "s3://sbsandbox-intern/runs/olmo/",
    "s3://not-sbsandbox-intern-checkpoints/runs/olmo/",
    "s3://SBSANDBOX-INTERN-checkpoints/runs/olmo/",
    "s3://sbsandbox-intern-checkpoints/runs/olmo",
    "s3://sbsandbox-intern-checkpoints/",
    "s3://sbsandbox-intern-/runs/olmo/",
    "https://sbsandbox-intern-edullm-outputs.s3.amazonaws.com/runs/olmo/",
)


def checkpoint_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "uri": CHECKPOINT_URI,
        "step": 1000,
        "epoch": 1,
        "created_at": "2026-07-25T12:30:00Z",
        "size_bytes": 2_600_000_000,
        "checksum": CHECKPOINT_DIGEST,
        "success_marker_uri": SUCCESS_MARKER_URI,
    }
    payload.update(overrides)
    return payload


def later_checkpoint_payload(**overrides: object) -> dict[str, object]:
    payload = checkpoint_payload(
        uri=LATER_CHECKPOINT_URI,
        step=2000,
        epoch=2,
        created_at="2026-07-25T13:00:00Z",
        checksum=LATER_CHECKPOINT_DIGEST,
        success_marker_uri=LATER_CHECKPOINT_URI + ".checkpoint-complete",
    )
    payload.update(overrides)
    return payload


def wandb_run_payload() -> dict[str, object]:
    return {"entity": "edullm", "project": "olmo-core", "run_id": "a1b2c3d4"}


def result_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "run_id": STABLE_RUN_ID,
        "attempt_id": STABLE_ATTEMPT_ID,
        "outcome": "succeeded",
        "output_prefixes": [OUTPUT_PREFIX],
        "checkpoints": [checkpoint_payload(), later_checkpoint_payload()],
        "wandb_run": wandb_run_payload(),
        "retention_class": "long_lived",
        "completed_at": "2026-07-25T14:00:00Z",
    }
    payload.update(overrides)
    return payload


def reverse_mapping_order(value: object) -> object:
    if isinstance(value, dict):
        items = reversed(list(value.items()))
        return {key: reverse_mapping_order(item) for key, item in items}
    if isinstance(value, list):
        return [reverse_mapping_order(item) for item in value]
    return value


def assert_validation_error(
    error: ValidationError,
    *,
    error_type: str,
    loc: tuple[str | int, ...],
    message_fragment: str | None = None,
) -> None:
    matching_errors = [
        item for item in error.errors() if item["type"] == error_type and item["loc"] == loc
    ]
    assert matching_errors, (
        f"expected error type {error_type!r} at loc {loc!r}, got {error.errors()}"
    )
    if message_fragment is not None:
        assert any(message_fragment in item["msg"] for item in matching_errors), (
            f"expected {message_fragment!r} in {error_type!r} messages at {loc!r}, "
            f"got {[item['msg'] for item in matching_errors]}"
        )


def test_checkpoint_with_a_success_marker_is_resumable() -> None:
    checkpoint = CheckpointManifest.model_validate(checkpoint_payload())
    assert checkpoint.success_marker_uri == SUCCESS_MARKER_URI
    assert checkpoint.is_resumable is True
    assert checkpoint.resume_reference() == CheckpointRef(
        uri=CHECKPOINT_URI,
        checksum=CHECKPOINT_DIGEST,
    )


def test_checkpoint_without_a_success_marker_is_not_resumable() -> None:
    checkpoint = CheckpointManifest.model_validate(checkpoint_payload(success_marker_uri=None))
    assert checkpoint.success_marker_uri is None
    assert checkpoint.is_resumable is False
    with pytest.raises(CheckpointNotResumableError) as exc_info:
        checkpoint.resume_reference()
    assert CHECKPOINT_URI in str(exc_info.value)
    assert isinstance(exc_info.value, ValueError)


def test_checkpoint_must_state_its_success_marker_explicitly() -> None:
    payload = checkpoint_payload()
    del payload["success_marker_uri"]
    with pytest.raises(ValidationError) as exc_info:
        CheckpointManifest.model_validate(payload)
    assert_validation_error(exc_info.value, error_type="missing", loc=("success_marker_uri",))


@pytest.mark.parametrize(
    ("success_marker_uri", "message_fragment"),
    [
        (
            LATER_CHECKPOINT_URI + ".checkpoint-complete",
            "inside its own checkpoint prefix",
        ),
        (
            "s3://sbsandbox-intern-edullm-checkpoints/.checkpoint-complete",
            "inside its own checkpoint prefix",
        ),
        (CHECKPOINT_URI, "must name an object"),
        (CHECKPOINT_URI + "shards/", "must name an object"),
    ],
    ids=["other-checkpoint", "bucket-root", "prefix-itself", "nested-prefix"],
)
def test_success_marker_must_live_inside_its_own_checkpoint(
    success_marker_uri: str,
    message_fragment: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        CheckpointManifest.model_validate(
            checkpoint_payload(success_marker_uri=success_marker_uri)
        )
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=(),
        message_fragment=message_fragment,
    )


@pytest.mark.parametrize("uri", OUTSIDE_SANDBOX_PREFIXES)
def test_checkpoint_outside_the_sandbox_bucket_namespace_is_rejected(uri: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        CheckpointManifest.model_validate(checkpoint_payload(uri=uri, success_marker_uri=None))
    assert_validation_error(exc_info.value, error_type="string_pattern_mismatch", loc=("uri",))


@pytest.mark.parametrize("size_bytes", [0, -1])
def test_checkpoint_rejects_a_non_positive_size(size_bytes: int) -> None:
    with pytest.raises(ValidationError) as exc_info:
        CheckpointManifest.model_validate(checkpoint_payload(size_bytes=size_bytes))
    assert_validation_error(exc_info.value, error_type="greater_than", loc=("size_bytes",))


@pytest.mark.parametrize("checksum", ["", "b" * 64, "sha256:" + "B" * 64, "md5:" + "b" * 32])
def test_checkpoint_rejects_a_checksum_that_is_not_a_sha256_digest(checksum: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        CheckpointManifest.model_validate(checkpoint_payload(checksum=checksum))
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("checksum",),
    )


def test_checkpoint_accepts_a_streaming_corpus_without_epochs() -> None:
    checkpoint = CheckpointManifest.model_validate(checkpoint_payload(epoch=None))
    assert checkpoint.epoch is None
    assert checkpoint.step == 1000


@pytest.mark.parametrize(("field", "value"), [("step", -1), ("epoch", -1)])
def test_checkpoint_rejects_a_negative_training_position(field: str, value: int) -> None:
    with pytest.raises(ValidationError) as exc_info:
        CheckpointManifest.model_validate(checkpoint_payload(**{field: value}))
    assert_validation_error(
        exc_info.value,
        error_type="greater_than_equal",
        loc=(field,),
    )


def test_checkpoint_rejects_an_empty_success_marker() -> None:
    with pytest.raises(ValidationError) as exc_info:
        CheckpointManifest.model_validate(checkpoint_payload(success_marker_uri=""))
    assert_validation_error(
        exc_info.value,
        error_type="string_too_short",
        loc=("success_marker_uri",),
    )


def test_a_resumable_checkpoint_supplies_the_lineage_a_resumed_run_requires() -> None:
    checkpoint = CheckpointManifest.model_validate(checkpoint_payload())
    resumed = LogicalRun.model_validate(
        {
            "schema_version": 1,
            "run_id": RESUMED_RUN_ID,
            "manifest_digest": "sha256:" + "a" * 64,
            "submitted_by": "frank-philote",
            "team_id": "modeling",
            "job_type": "model_pretraining",
            "created_at": "2026-07-25T15:00:00Z",
            "parent_run_id": STABLE_RUN_ID,
            "resumed_from": checkpoint.resume_reference().model_dump(mode="json"),
        }
    )
    assert resumed.is_resumed is True
    assert resumed.resumed_from == checkpoint.resume_reference()
    assert resumed.run_id != resumed.parent_run_id


def test_a_run_cannot_be_resumed_from_a_checkpoint_without_a_success_marker() -> None:
    checkpoint = CheckpointManifest.model_validate(checkpoint_payload(success_marker_uri=None))
    with pytest.raises(CheckpointNotResumableError):
        checkpoint.resume_reference()


def test_result_manifest_validates_a_complete_payload() -> None:
    result = ResultManifest.model_validate(result_payload())
    assert result.run_id == STABLE_RUN_ID
    assert result.attempt_id == STABLE_ATTEMPT_ID
    assert result.outcome is AttemptTerminalState.SUCCEEDED
    assert result.retention_class is RetentionClass.LONG_LIVED
    assert result.output_prefixes == (OUTPUT_PREFIX,)
    assert len(result.checkpoints) == 2
    assert result.wandb_run == WandbRunRef.model_validate(wandb_run_payload())


@pytest.mark.parametrize("field", ["run_id", "attempt_id"])
def test_result_manifest_must_reference_both_the_run_and_its_attempt(field: str) -> None:
    payload = result_payload()
    del payload[field]
    with pytest.raises(ValidationError) as exc_info:
        ResultManifest.model_validate(payload)
    assert_validation_error(exc_info.value, error_type="missing", loc=(field,))


def test_result_manifest_rejects_an_attempt_id_where_a_run_id_belongs() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ResultManifest.model_validate(result_payload(run_id=STABLE_ATTEMPT_ID))
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("run_id",),
    )


def test_result_manifest_rejects_a_run_id_where_an_attempt_id_belongs() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ResultManifest.model_validate(result_payload(attempt_id=STABLE_RUN_ID))
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("attempt_id",),
    )


@pytest.mark.parametrize("output_prefix", OUTSIDE_SANDBOX_PREFIXES)
def test_output_prefix_outside_the_sandbox_bucket_namespace_is_rejected(
    output_prefix: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ResultManifest.model_validate(result_payload(output_prefixes=[output_prefix]))
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("output_prefixes", 0),
    )


def test_result_manifest_rejects_a_checkpoint_outside_the_sandbox_bucket_namespace() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ResultManifest.model_validate(
            result_payload(
                checkpoints=[
                    checkpoint_payload(
                        uri="s3://edullm-checkpoints/runs/olmo/step-1000/",
                        success_marker_uri=None,
                    )
                ]
            )
        )
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("checkpoints", 0, "uri"),
    )


def test_result_manifest_rejects_unordered_collections() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ResultManifest.model_validate(result_payload(output_prefixes={OUTPUT_PREFIX}))
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=("output_prefixes",),
        message_fragment="ordered sequences must be provided as a list or tuple",
    )


def test_result_manifest_rejects_duplicate_output_prefixes() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ResultManifest.model_validate(result_payload(output_prefixes=[OUTPUT_PREFIX] * 2))
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=(),
        message_fragment="output prefixes must be unique",
    )


def test_result_manifest_rejects_checkpoints_out_of_step_order() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ResultManifest.model_validate(
            result_payload(checkpoints=[later_checkpoint_payload(), checkpoint_payload()])
        )
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=(),
        message_fragment="strictly increasing step order",
    )


def test_result_manifest_rejects_two_checkpoints_at_the_same_step() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ResultManifest.model_validate(
            result_payload(
                checkpoints=[checkpoint_payload(), later_checkpoint_payload(step=1000)]
            )
        )
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=(),
        message_fragment="strictly increasing step order",
    )


def test_a_succeeded_run_must_record_where_it_wrote_its_outputs() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ResultManifest.model_validate(result_payload(output_prefixes=[]))
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=(),
        message_fragment="at least one output prefix",
    )


def test_a_cancelled_run_may_record_no_outputs_and_no_wandb_run() -> None:
    result = ResultManifest.model_validate(
        result_payload(
            outcome="cancelled",
            output_prefixes=[],
            checkpoints=[],
            wandb_run=None,
            retention_class="transient",
        )
    )
    assert result.outcome is AttemptTerminalState.CANCELLED
    assert result.output_prefixes == ()
    assert result.wandb_run is None
    assert result.latest_resumable_checkpoint() is None


def test_result_manifest_rejects_a_non_terminal_outcome() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ResultManifest.model_validate(result_payload(outcome="running"))
    assert_validation_error(exc_info.value, error_type="value_error", loc=("outcome",))


def test_result_manifest_rejects_an_unknown_retention_class() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ResultManifest.model_validate(result_payload(retention_class="forever_and_ever"))
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=("retention_class",),
    )


def test_latest_resumable_checkpoint_ignores_checkpoints_without_success_markers() -> None:
    result = ResultManifest.model_validate(
        result_payload(
            outcome="failed",
            checkpoints=[checkpoint_payload(), later_checkpoint_payload(success_marker_uri=None)],
        )
    )
    assert len(result.checkpoints) == 2
    assert result.resumable_checkpoints == (result.checkpoints[0],)
    latest = result.latest_resumable_checkpoint()
    assert latest is not None
    assert latest.uri == CHECKPOINT_URI


def test_a_failed_run_with_no_resumable_checkpoint_offers_none() -> None:
    result = ResultManifest.model_validate(
        result_payload(
            outcome="failed",
            checkpoints=[checkpoint_payload(success_marker_uri=None)],
        )
    )
    assert result.resumable_checkpoints == ()
    assert result.latest_resumable_checkpoint() is None


@pytest.mark.parametrize(
    ("model_type", "payload_factory"),
    [
        (CheckpointManifest, checkpoint_payload),
        (ResultManifest, result_payload),
    ],
    ids=["checkpoint-manifest", "result-manifest"],
)
def test_unknown_schema_version_fails_closed(
    model_type: type[ContractModel],
    payload_factory: Callable[..., dict[str, object]],
) -> None:
    for schema_version in (0, 2, "1", None):
        with pytest.raises(ValidationError) as exc_info:
            model_type.model_validate(payload_factory(schema_version=schema_version))
        assert_validation_error(
            exc_info.value,
            error_type="literal_error",
            loc=("schema_version",),
        )


def test_nested_checkpoint_schema_version_fails_closed() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ResultManifest.model_validate(
            result_payload(checkpoints=[checkpoint_payload(schema_version=2)])
        )
    assert_validation_error(
        exc_info.value,
        error_type="literal_error",
        loc=("checkpoints", 0, "schema_version"),
    )


@pytest.mark.parametrize(
    ("model_type", "payload_factory"),
    [
        (CheckpointManifest, checkpoint_payload),
        (ResultManifest, result_payload),
    ],
    ids=["checkpoint-manifest", "result-manifest"],
)
def test_reordering_input_fields_does_not_change_the_digest(
    model_type: type[ContractModel],
    payload_factory: Callable[..., dict[str, object]],
) -> None:
    payload = payload_factory()
    reordered = reverse_mapping_order(payload)
    assert list(reordered) != list(payload)
    baseline = model_type.model_validate(payload)
    shuffled = model_type.model_validate(reordered)
    assert canonical_json_bytes(baseline) == canonical_json_bytes(shuffled)
    assert sha256_digest(baseline) == sha256_digest(shuffled)


@pytest.mark.parametrize(
    ("model_type", "payload_factory", "expected_digest"),
    [
        (CheckpointManifest, checkpoint_payload, CHECKPOINT_MANIFEST_DIGEST),
        (ResultManifest, result_payload, RESULT_MANIFEST_DIGEST),
    ],
    ids=["checkpoint-manifest", "result-manifest"],
)
def test_digest_is_stable_across_repeated_validation(
    model_type: type[ContractModel],
    payload_factory: Callable[..., dict[str, object]],
    expected_digest: str,
) -> None:
    first = model_type.model_validate(payload_factory())
    second = model_type.model_validate(payload_factory())
    assert sha256_digest(first) == sha256_digest(second)
    assert sha256_digest(first) == expected_digest


def test_result_timestamps_serialize_as_utc() -> None:
    result = ResultManifest.model_validate(result_payload(completed_at="2026-07-25T09:00:00-05:00"))
    assert b'"completed_at":"2026-07-25T14:00:00.000000Z"' in canonical_json_bytes(result)
    assert sha256_digest(result) == RESULT_MANIFEST_DIGEST


def test_result_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ResultManifest.model_validate(result_payload(cost_usd="12.00"))
    assert_validation_error(exc_info.value, error_type="extra_forbidden", loc=("cost_usd",))


def test_result_models_are_frozen() -> None:
    result = ResultManifest.model_validate(result_payload())
    with pytest.raises(ValidationError) as exc_info:
        result.outcome = AttemptTerminalState.FAILED
    assert_validation_error(exc_info.value, error_type="frozen_instance", loc=("outcome",))
