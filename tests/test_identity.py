import re
import time
import uuid
from collections.abc import Callable, Sequence

import pytest
from pydantic import ValidationError

from edullm_platform.canonical import canonical_json_bytes, sha256_digest
from edullm_platform.contracts.base import ContractModel
from edullm_platform.contracts.identity import (
    ATTEMPT_ID_PATTERN,
    ATTEMPT_ID_PREFIX,
    MAXIMUM_COUNTER,
    MAXIMUM_UNIX_TS_MS,
    RUN_ID_PATTERN,
    RUN_ID_PREFIX,
    UUID7_VERSION,
    AttemptId,
    RunId,
    Uuid7Generator,
    attempt_id_uuid,
    generate_uuid7,
    new_attempt_id,
    new_run_id,
    run_id_uuid,
    uuid7_timestamp_ms,
)

REFERENCE_UNIX_TS_MS = 1_785_000_000_000
TIMESTAMP_TOLERANCE_MS = 1_000
SAME_MILLISECOND_SAMPLES = 2_000
IDENTIFIER_SAMPLES = 500

SAMPLE_UUID7_TEXT = "01994f2a-1c00-7c3b-8f4d-2a5b6c7d8e9f"
STABLE_RUN_ID = "run_01994f2a-1c00-7c3b-8f4d-2a5b6c7d8e9f"
STABLE_ATTEMPT_ID = "att_01994f2a-1c00-7c3b-9a1b-2c3d4e5f6a7b"
STABLE_RECORD_DIGEST = "sha256:f3dcba3b0d6940c27adc048478a7419984a79d004de1a0e543172f1a2f01e067"


class RunAttempt(ContractModel):
    run_id: RunId
    attempt_id: AttemptId


class FrozenClock:
    def __init__(self, unix_ts_ms: int, *, maximum_reads: int) -> None:
        self.unix_ts_ms = unix_ts_ms
        self.maximum_reads = maximum_reads
        self.reads = 0

    def __call__(self) -> int:
        self.reads += 1
        assert self.reads <= self.maximum_reads, (
            "generator waited for a millisecond that a frozen clock never reaches"
        )
        return self.unix_ts_ms


class ScriptedClock:
    def __init__(self, readings: Sequence[int]) -> None:
        self.readings = tuple(readings)
        self.reads = 0

    def __call__(self) -> int:
        reading = self.readings[min(self.reads, len(self.readings) - 1)]
        self.reads += 1
        return reading


class HoldingClock:
    def __init__(self, unix_ts_ms: int, *, held_reads: int) -> None:
        self.unix_ts_ms = unix_ts_ms
        self.held_reads = held_reads
        self.reads = 0

    def __call__(self) -> int:
        self.reads += 1
        if self.reads > self.held_reads:
            return self.unix_ts_ms + 1
        return self.unix_ts_ms


def zero_entropy(bits: int) -> int:
    return 0


def saturated_entropy(bits: int) -> int:
    return (1 << bits) - 1


def run_attempt_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "run_id": STABLE_RUN_ID,
        "attempt_id": STABLE_ATTEMPT_ID,
    }
    payload.update(overrides)
    return payload


def assert_validation_error(
    error: ValidationError,
    *,
    error_type: str,
    loc: tuple[str | int, ...],
) -> None:
    matching_errors = [
        item for item in error.errors() if item["type"] == error_type and item["loc"] == loc
    ]
    assert matching_errors, (
        f"expected error type {error_type!r} at loc {loc!r}, got {error.errors()}"
    )


def test_generated_uuid7_sets_version_and_variant_bits() -> None:
    value = generate_uuid7()
    assert (value.int >> 76) & 0xF == UUID7_VERSION
    assert (value.int >> 62) & 0b11 == 0b10
    assert value.version == UUID7_VERSION
    assert value.variant == uuid.RFC_4122


def test_generated_uuid7_embeds_current_unix_milliseconds() -> None:
    before = time.time_ns() // 1_000_000
    embedded = uuid7_timestamp_ms(generate_uuid7())
    after = time.time_ns() // 1_000_000
    assert before - TIMESTAMP_TOLERANCE_MS <= embedded <= after + TIMESTAMP_TOLERANCE_MS


def test_uuid7_timestamp_round_trips_through_the_injected_clock() -> None:
    generator = Uuid7Generator(clock=FrozenClock(REFERENCE_UNIX_TS_MS, maximum_reads=1))
    assert uuid7_timestamp_ms(generator.generate()) == REFERENCE_UNIX_TS_MS


def test_uuid7_values_sort_chronologically_across_milliseconds() -> None:
    readings = (
        REFERENCE_UNIX_TS_MS,
        REFERENCE_UNIX_TS_MS + 1,
        REFERENCE_UNIX_TS_MS + 7,
        REFERENCE_UNIX_TS_MS + 5_000,
    )
    generator = Uuid7Generator(clock=ScriptedClock(readings), entropy=saturated_entropy)
    values = [generator.generate() for _ in readings]
    hex_values = [value.hex for value in values]
    assert hex_values == sorted(hex_values)
    assert [uuid7_timestamp_ms(value) for value in values] == list(readings)


def test_uuid7_values_in_one_millisecond_are_distinct_and_ordered() -> None:
    clock = FrozenClock(REFERENCE_UNIX_TS_MS, maximum_reads=SAME_MILLISECOND_SAMPLES)
    generator = Uuid7Generator(clock=clock)
    values = [generator.generate() for _ in range(SAME_MILLISECOND_SAMPLES)]
    hex_values = [value.hex for value in values]
    assert len(set(hex_values)) == SAME_MILLISECOND_SAMPLES
    assert hex_values == sorted(hex_values)
    assert {uuid7_timestamp_ms(value) for value in values} == {REFERENCE_UNIX_TS_MS}


def test_counter_seed_leaves_burst_headroom_inside_one_millisecond() -> None:
    clock = FrozenClock(REFERENCE_UNIX_TS_MS, maximum_reads=SAME_MILLISECOND_SAMPLES)
    generator = Uuid7Generator(clock=clock, entropy=saturated_entropy)
    values = [generator.generate() for _ in range(SAME_MILLISECOND_SAMPLES)]
    hex_values = [value.hex for value in values]
    assert hex_values == sorted(set(hex_values))
    assert clock.reads == SAME_MILLISECOND_SAMPLES


def test_counter_overflow_waits_for_the_next_millisecond() -> None:
    burst = MAXIMUM_COUNTER + 1
    clock = HoldingClock(REFERENCE_UNIX_TS_MS, held_reads=burst + 1)
    generator = Uuid7Generator(clock=clock, entropy=zero_entropy)
    values = [generator.generate() for _ in range(burst + 1)]
    timestamps = [uuid7_timestamp_ms(value) for value in values]
    assert timestamps[:burst] == [REFERENCE_UNIX_TS_MS] * burst
    assert timestamps[burst] == REFERENCE_UNIX_TS_MS + 1
    assert clock.reads == burst + 2
    hex_values = [value.hex for value in values]
    assert hex_values == sorted(set(hex_values))


def test_generation_does_not_regress_when_the_clock_moves_backwards() -> None:
    generator = Uuid7Generator(
        clock=ScriptedClock((REFERENCE_UNIX_TS_MS, REFERENCE_UNIX_TS_MS - 5)),
        entropy=zero_entropy,
    )
    first = generator.generate()
    second = generator.generate()
    assert uuid7_timestamp_ms(second) == REFERENCE_UNIX_TS_MS
    assert second.hex > first.hex


@pytest.mark.parametrize("unix_ts_ms", [-1, MAXIMUM_UNIX_TS_MS + 1])
def test_generator_rejects_clock_readings_outside_forty_eight_bits(unix_ts_ms: int) -> None:
    generator = Uuid7Generator(clock=FrozenClock(unix_ts_ms, maximum_reads=1))
    with pytest.raises(ValueError, match="48"):
        generator.generate()


def overflowing_entropy(bits: int) -> int:
    return 1 << bits


def negative_entropy(bits: int) -> int:
    return -1


@pytest.mark.parametrize(
    "entropy",
    [overflowing_entropy, negative_entropy],
    ids=["too-wide", "negative"],
)
def test_generator_rejects_entropy_outside_the_requested_width(
    entropy: Callable[[int], int],
) -> None:
    generator = Uuid7Generator(
        clock=FrozenClock(REFERENCE_UNIX_TS_MS, maximum_reads=1),
        entropy=entropy,
    )
    with pytest.raises(ValueError, match="entropy"):
        generator.generate()


@pytest.mark.parametrize(
    "value",
    [
        uuid.UUID(int=0),
        uuid.uuid4(),
        uuid.UUID(int=(4 << 76) | (0b10 << 62)),
        uuid.UUID(int=(7 << 76) | (0b11 << 62)),
    ],
    ids=["nil", "random", "version-four", "microsoft-variant"],
)
def test_timestamp_extraction_rejects_values_that_are_not_uuid7(value: uuid.UUID) -> None:
    with pytest.raises(ValueError, match="UUIDv7"):
        uuid7_timestamp_ms(value)


def test_new_identifiers_match_their_declared_patterns() -> None:
    run_id = new_run_id()
    attempt_id = new_attempt_id()
    assert run_id.startswith(RUN_ID_PREFIX)
    assert attempt_id.startswith(ATTEMPT_ID_PREFIX)
    assert re.fullmatch(RUN_ID_PATTERN, run_id) is not None
    assert re.fullmatch(ATTEMPT_ID_PATTERN, attempt_id) is not None
    assert run_id_uuid(run_id).version == UUID7_VERSION
    assert attempt_id_uuid(attempt_id).version == UUID7_VERSION
    record = RunAttempt.model_validate({"run_id": run_id, "attempt_id": attempt_id})
    assert record.run_id == run_id
    assert record.attempt_id == attempt_id


def test_new_identifiers_are_unique_and_time_ordered() -> None:
    run_ids = [new_run_id() for _ in range(IDENTIFIER_SAMPLES)]
    attempt_ids = [new_attempt_id() for _ in range(IDENTIFIER_SAMPLES)]
    assert len(set(run_ids)) == IDENTIFIER_SAMPLES
    assert len(set(attempt_ids)) == IDENTIFIER_SAMPLES
    assert run_ids == sorted(run_ids)
    assert attempt_ids == sorted(attempt_ids)


def test_attempt_id_is_rejected_where_a_run_id_is_required() -> None:
    with pytest.raises(ValidationError) as exc_info:
        RunAttempt.model_validate(run_attempt_payload(run_id=STABLE_ATTEMPT_ID))
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("run_id",),
    )


def test_run_id_is_rejected_where_an_attempt_id_is_required() -> None:
    with pytest.raises(ValidationError) as exc_info:
        RunAttempt.model_validate(run_attempt_payload(attempt_id=STABLE_RUN_ID))
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("attempt_id",),
    )


def test_swapped_identifier_pair_is_rejected_on_both_fields() -> None:
    with pytest.raises(ValidationError) as exc_info:
        RunAttempt.model_validate(
            run_attempt_payload(run_id=STABLE_ATTEMPT_ID, attempt_id=STABLE_RUN_ID)
        )
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("run_id",),
    )
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("attempt_id",),
    )


def test_generated_identifier_kinds_are_never_interchangeable() -> None:
    run_id = new_run_id()
    attempt_id = new_attempt_id()
    with pytest.raises(ValidationError):
        RunAttempt.model_validate({"run_id": attempt_id, "attempt_id": run_id})


MALFORMED_RUN_IDS: tuple[tuple[str, str], ...] = (
    ("missing prefix", SAMPLE_UUID7_TEXT),
    ("wrong prefix", f"att_{SAMPLE_UUID7_TEXT}"),
    ("uppercase prefix", f"RUN_{SAMPLE_UUID7_TEXT}"),
    ("uppercase hex", f"{RUN_ID_PREFIX}{SAMPLE_UUID7_TEXT.upper()}"),
    ("undashed", f"{RUN_ID_PREFIX}{SAMPLE_UUID7_TEXT.replace('-', '')}"),
    ("truncated", f"{RUN_ID_PREFIX}{SAMPLE_UUID7_TEXT[:-1]}"),
    ("overlong", f"{RUN_ID_PREFIX}{SAMPLE_UUID7_TEXT}0"),
    ("non hex", f"{RUN_ID_PREFIX}{SAMPLE_UUID7_TEXT[:-1]}z"),
    ("version four", f"{RUN_ID_PREFIX}01994f2a-1c00-4c3b-8f4d-2a5b6c7d8e9f"),
    ("non rfc variant", f"{RUN_ID_PREFIX}01994f2a-1c00-7c3b-cf4d-2a5b6c7d8e9f"),
    ("braced", f"{RUN_ID_PREFIX}{{{SAMPLE_UUID7_TEXT}}}"),
    ("leading space", f" {RUN_ID_PREFIX}{SAMPLE_UUID7_TEXT}"),
    ("trailing newline", f"{RUN_ID_PREFIX}{SAMPLE_UUID7_TEXT}\n"),
    ("empty", ""),
)


@pytest.mark.parametrize(
    "value",
    [value for _, value in MALFORMED_RUN_IDS],
    ids=[case for case, _ in MALFORMED_RUN_IDS],
)
def test_malformed_run_identifiers_are_rejected(value: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        RunAttempt.model_validate(run_attempt_payload(run_id=value))
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("run_id",),
    )
    with pytest.raises(ValueError, match=RUN_ID_PREFIX):
        run_id_uuid(value)


@pytest.mark.parametrize(
    "value",
    [uuid.UUID(SAMPLE_UUID7_TEXT), 1, None, b"run_" + SAMPLE_UUID7_TEXT.encode("utf-8")],
    ids=["uuid", "integer", "none", "bytes"],
)
def test_identifier_fields_reject_non_string_values(value: object) -> None:
    with pytest.raises(ValidationError) as exc_info:
        RunAttempt.model_validate(run_attempt_payload(run_id=value))
    assert_validation_error(exc_info.value, error_type="string_type", loc=("run_id",))


def test_identifier_parsers_reject_the_other_identifier_kind() -> None:
    with pytest.raises(ValueError, match=RUN_ID_PREFIX):
        run_id_uuid(STABLE_ATTEMPT_ID)
    with pytest.raises(ValueError, match=ATTEMPT_ID_PREFIX):
        attempt_id_uuid(STABLE_RUN_ID)


def test_identifier_parsers_round_trip_generated_identifiers() -> None:
    generator = Uuid7Generator(
        clock=FrozenClock(REFERENCE_UNIX_TS_MS, maximum_reads=1),
        entropy=zero_entropy,
    )
    value = generator.generate()
    run_id = f"{RUN_ID_PREFIX}{value}"
    assert run_id_uuid(run_id) == value
    assert uuid7_timestamp_ms(run_id_uuid(run_id)) == REFERENCE_UNIX_TS_MS


def test_identifiers_serialize_as_plain_strings_in_canonical_json() -> None:
    record = RunAttempt.model_validate(run_attempt_payload())
    encoded = canonical_json_bytes(record)
    assert encoded == (
        b'{"attempt_id":"'
        + STABLE_ATTEMPT_ID.encode("utf-8")
        + b'","run_id":"'
        + STABLE_RUN_ID.encode("utf-8")
        + b'"}'
    )


def test_identifier_digest_is_stable() -> None:
    record = RunAttempt.model_validate(run_attempt_payload())
    repeated = RunAttempt.model_validate(run_attempt_payload())
    assert sha256_digest(record) == STABLE_RECORD_DIGEST
    assert sha256_digest(repeated) == STABLE_RECORD_DIGEST


def test_identifier_digest_changes_when_an_identifier_changes() -> None:
    record = RunAttempt.model_validate(run_attempt_payload())
    mutated = RunAttempt.model_validate(run_attempt_payload(run_id=new_run_id()))
    assert sha256_digest(record) != sha256_digest(mutated)
