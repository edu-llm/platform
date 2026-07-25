import re
from decimal import Decimal, localcontext
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, PlainSerializer, WithJsonSchema

DECIMAL_PATTERN = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
POSITIVE_DECIMAL_PATTERN = re.compile(
    r"^(?:[1-9][0-9]*(?:\.[0-9]+)?|0\.[0-9]*[1-9][0-9]*)$"
)
MAX_DECIMAL_DIGITS = 28


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
