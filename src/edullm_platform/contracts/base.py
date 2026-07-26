import re
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
)

DECIMAL_PATTERN = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
POSITIVE_DECIMAL_PATTERN = re.compile(
    r"^(?:[1-9][0-9]*(?:\.[0-9]+)?|0\.[0-9]*[1-9][0-9]*)$"
)
MAX_DECIMAL_DIGITS = 28

SANDBOX_BUCKET_PREFIX = "sbsandbox-intern-"
SHA256_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
UTC_TIMESTAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"


def _finalize_decimal(parsed: Decimal) -> Decimal:
    if not parsed.is_finite():
        raise ValueError("decimal values must be finite")
    _, digits, _ = parsed.as_tuple()
    if len(digits) > MAX_DECIMAL_DIGITS:
        raise ValueError("decimal values must not exceed 28 digits")
    with localcontext() as ctx:
        ctx.prec = MAX_DECIMAL_DIGITS
        normalized = parsed.normalize()
    if normalized.is_zero():
        return Decimal(0)
    return normalized


def parse_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, str) and DECIMAL_PATTERN.fullmatch(value):
        parsed = Decimal(value)
    else:
        raise ValueError("decimal values must be non-negative base-10 strings")
    return _finalize_decimal(parsed)


def serialize_decimal(value: Decimal) -> str:
    return format(value, "f")


def require_ordered_sequence(value: object) -> object:
    if isinstance(value, (list, tuple)):
        return value
    raise ValueError("ordered sequences must be provided as a list or tuple")


def parse_str_enum[E: StrEnum](enum_type: type[E]) -> Callable[[object], object]:
    def parse(value: object) -> object:
        if isinstance(value, str) and not isinstance(value, enum_type):
            return enum_type(value)
        return value

    return parse


def parse_utc_timestamp(value: object) -> object:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("timestamps must be RFC 3339 date-times") from exc
    elif isinstance(value, datetime):
        parsed = value
    else:
        return value
    if parsed.tzinfo is None:
        raise ValueError("timestamps must carry an explicit UTC offset")
    return parsed.astimezone(UTC)


def serialize_utc_timestamp(value: datetime) -> str:
    normalized = value.astimezone(UTC)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


UTC_TIMESTAMP_JSON_SCHEMA = {
    "type": "string",
    "format": "date-time",
    "pattern": UTC_TIMESTAMP_PATTERN,
}

UtcTimestamp = Annotated[
    datetime,
    BeforeValidator(parse_utc_timestamp),
    PlainSerializer(serialize_utc_timestamp, return_type=str, when_used="json"),
    WithJsonSchema(UTC_TIMESTAMP_JSON_SCHEMA),
]

Sha256Digest = Annotated[str, Field(pattern=SHA256_DIGEST_PATTERN)]

STRICT_DECIMAL_JSON_SCHEMA = {
    "type": "string",
    "pattern": DECIMAL_PATTERN.pattern,
}

POSITIVE_STRICT_DECIMAL_JSON_SCHEMA = {
    "type": "string",
    "pattern": POSITIVE_DECIMAL_PATTERN.pattern,
}

StrictDecimal = Annotated[
    Decimal,
    BeforeValidator(parse_decimal),
    PlainSerializer(serialize_decimal, return_type=str, when_used="json"),
    WithJsonSchema(STRICT_DECIMAL_JSON_SCHEMA),
]

PositiveStrictDecimal = Annotated[
    Decimal,
    BeforeValidator(parse_decimal),
    PlainSerializer(serialize_decimal, return_type=str, when_used="json"),
    WithJsonSchema(POSITIVE_STRICT_DECIMAL_JSON_SCHEMA),
]


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )
