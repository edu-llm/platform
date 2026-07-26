import re
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from typing import Annotated

from pydantic import Field

UUID_BITS = 128
UNIX_TS_MS_BITS = 48
VERSION_BITS = 4
COUNTER_BITS = 12
VARIANT_BITS = 2
TAIL_BITS = UUID_BITS - UNIX_TS_MS_BITS - VERSION_BITS - COUNTER_BITS - VARIANT_BITS

UNIX_TS_MS_SHIFT = UUID_BITS - UNIX_TS_MS_BITS
VERSION_SHIFT = UNIX_TS_MS_SHIFT - VERSION_BITS
COUNTER_SHIFT = VERSION_SHIFT - COUNTER_BITS
VARIANT_SHIFT = COUNTER_SHIFT - VARIANT_BITS

UUID7_VERSION = 7
UUID7_VARIANT = 0b10
COUNTER_SEED_BITS = 8
MAXIMUM_UNIX_TS_MS = (1 << UNIX_TS_MS_BITS) - 1
MAXIMUM_COUNTER = (1 << COUNTER_BITS) - 1

UUID7_TEXT_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
RUN_ID_PREFIX = "run_"
ATTEMPT_ID_PREFIX = "att_"
RUN_ID_PATTERN = f"^{RUN_ID_PREFIX}{UUID7_TEXT_PATTERN}$"
ATTEMPT_ID_PATTERN = f"^{ATTEMPT_ID_PREFIX}{UUID7_TEXT_PATTERN}$"
RUN_ID_REGEX = re.compile(RUN_ID_PATTERN)
ATTEMPT_ID_REGEX = re.compile(ATTEMPT_ID_PATTERN)

RunId = Annotated[str, Field(pattern=RUN_ID_PATTERN)]
AttemptId = Annotated[str, Field(pattern=ATTEMPT_ID_PATTERN)]


def unix_epoch_milliseconds() -> int:
    return time.time_ns() // 1_000_000


def _compose_uuid7(unix_ts_ms: int, counter: int, tail: int) -> uuid.UUID:
    return uuid.UUID(
        int=(unix_ts_ms << UNIX_TS_MS_SHIFT)
        | (UUID7_VERSION << VERSION_SHIFT)
        | (counter << COUNTER_SHIFT)
        | (UUID7_VARIANT << VARIANT_SHIFT)
        | tail
    )


class Uuid7Generator:
    def __init__(
        self,
        *,
        clock: Callable[[], int] = unix_epoch_milliseconds,
        entropy: Callable[[int], int] = secrets.randbits,
    ) -> None:
        self._clock = clock
        self._entropy = entropy
        self._lock = threading.Lock()
        self._unix_ts_ms = -1
        self._counter = 0

    def generate(self) -> uuid.UUID:
        with self._lock:
            unix_ts_ms, counter = self._advance()
            tail = self._read_entropy(TAIL_BITS)
        return _compose_uuid7(unix_ts_ms, counter, tail)

    def _advance(self) -> tuple[int, int]:
        unix_ts_ms = self._read_clock()
        if unix_ts_ms <= self._unix_ts_ms:
            if self._counter < MAXIMUM_COUNTER:
                self._counter += 1
                return self._unix_ts_ms, self._counter
            unix_ts_ms = self._wait_for_next_millisecond()
        self._unix_ts_ms = unix_ts_ms
        self._counter = self._read_entropy(COUNTER_SEED_BITS)
        return unix_ts_ms, self._counter

    def _read_clock(self) -> int:
        unix_ts_ms = self._clock()
        if not 0 <= unix_ts_ms <= MAXIMUM_UNIX_TS_MS:
            raise ValueError("UUIDv7 timestamps must fit in 48 unsigned bits")
        return unix_ts_ms

    def _read_entropy(self, bits: int) -> int:
        value = self._entropy(bits)
        if not 0 <= value < (1 << bits):
            raise ValueError(f"entropy must supply an unsigned value of {bits} bits or fewer")
        return value

    def _wait_for_next_millisecond(self) -> int:
        while True:
            unix_ts_ms = self._read_clock()
            if unix_ts_ms > self._unix_ts_ms:
                return unix_ts_ms


_DEFAULT_GENERATOR = Uuid7Generator()


def generate_uuid7() -> uuid.UUID:
    return _DEFAULT_GENERATOR.generate()


def uuid7_timestamp_ms(value: uuid.UUID) -> int:
    if value.variant != uuid.RFC_4122 or value.version != UUID7_VERSION:
        raise ValueError("timestamps can only be read from UUIDv7 values")
    return value.int >> UNIX_TS_MS_SHIFT


def new_run_id() -> RunId:
    return f"{RUN_ID_PREFIX}{generate_uuid7()}"


def new_attempt_id() -> AttemptId:
    return f"{ATTEMPT_ID_PREFIX}{generate_uuid7()}"


def _identifier_uuid(value: str, regex: re.Pattern[str], prefix: str) -> uuid.UUID:
    if regex.fullmatch(value) is None:
        raise ValueError(f"identifiers must match {regex.pattern}")
    return uuid.UUID(value.removeprefix(prefix))


def run_id_uuid(value: str) -> uuid.UUID:
    return _identifier_uuid(value, RUN_ID_REGEX, RUN_ID_PREFIX)


def attempt_id_uuid(value: str) -> uuid.UUID:
    return _identifier_uuid(value, ATTEMPT_ID_REGEX, ATTEMPT_ID_PREFIX)
