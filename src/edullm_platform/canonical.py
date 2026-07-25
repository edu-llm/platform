import hashlib
import json

from .contracts.base import ContractModel


def canonical_json_bytes(model: ContractModel) -> bytes:
    payload = model.model_dump(mode="json", by_alias=True, exclude_none=False)
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_digest(model: ContractModel) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(model)).hexdigest()}"
