"""Every contract model in the package, and the structural digest of its shape.

The digest is over the model's JSON schema, so it moves when a field is added, removed,
retyped or reconstrained, and does not move when a docstring or a comment does. That is the
distinction that matters here: every payload already written against the old shape is one
the new shape may now refuse, and a lineage record in S3 cannot be rewritten.

Sixteen of these models are also exported to ``schemas/`` and re-derived on every CI run, so
a change to one of those already fails a build. This covers the rest, which is most of them.

The inventory is discovered by walking the package rather than listed, so a contract added
in a new module is in it without anybody remembering. Recording the digests is
``tools/record_goldens.py`` and reading them back is
``tests/test_serialization_goldens.py``.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import pkgutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, get_args

import edullm_platform
from edullm_platform.contracts.base import ContractModel

__all__ = [
    "INVENTORY_DRIFT_GUIDANCE",
    "RECORDED_RELATIVE_PATH",
    "InventoryDrift",
    "ModelRecord",
    "declared_schema_version",
    "discover_contract_models",
    "inventory_drift",
    "load_recorded_models",
    "model_records",
    "recorded_path",
    "render_inventory_document",
    "structural_digest",
]

INVENTORY_SCHEMA_VERSION: Final = 1

#: Where the recorded inventory is committed, relative to the repository root.
RECORDED_RELATIVE_PATH: Final = Path("fixtures") / "goldens" / "contract-models.json"

RECORD_COMMAND: Final = "uv run python tools/record_goldens.py"

INVENTORY_DRIFT_GUIDANCE: Final = f"""{{subject}}: the recorded {{field}} does not describe this tree.
  recorded: {{recorded}}
  live:     {{live}}

This is a contract-inventory tripwire, not a formatting check.

Which field moved decides what it means, and they do not mean the same thing:

  * a structural digest means a field was added, removed, retyped or reconstrained. Every
    payload already written against the old shape is one the new shape may now refuse, and
    a lineage record in S3 cannot be rewritten;
  * a module means the contract did not change and the code moved. It costs a reviewer
    nothing and still has to be re-recorded, because a table naming a file the model is
    not in is a table nobody can check by hand;
  * a presence means a contract was added to the tree or taken out of it.

Do exactly one of these, deliberately:

  1. The change was intended. Re-record with
       {RECORD_COMMAND} --force
     and review the diff in the same commit as the change that caused it.

  2. The change was not intended. This is a regression: fix it instead of re-recording."""


@dataclass(frozen=True)
class ModelRecord:
    module: str
    name: str
    schema_version: str
    base: bool
    structural_digest: str


def discover_contract_models() -> tuple[type[ContractModel], ...]:
    models: dict[str, type[ContractModel]] = {}
    for module_info in pkgutil.walk_packages(edullm_platform.__path__, prefix="edullm_platform."):
        module = importlib.import_module(module_info.name)
        for attribute in vars(module).values():
            if not isinstance(attribute, type) or not issubclass(attribute, ContractModel):
                continue
            if attribute is ContractModel or attribute.__module__ != module_info.name:
                continue
            models[f"{attribute.__module__}.{attribute.__name__}"] = attribute
    return tuple(models[key] for key in sorted(models))


def structural_digest(model: type[ContractModel]) -> str:
    encoded = json.dumps(
        model.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def declared_schema_version(model: type[ContractModel]) -> str:
    field = model.model_fields.get("schema_version")
    if field is None:
        return "unversioned"
    arguments = get_args(field.annotation)
    if len(arguments) != 1:
        return "unversioned"
    return str(arguments[0])


def model_records() -> tuple[ModelRecord, ...]:
    models = discover_contract_models()
    bases = {
        ancestor
        for model in models
        for ancestor in model.__mro__[1:]
        if ancestor is not ContractModel
    }
    return tuple(
        ModelRecord(
            module=model.__module__,
            name=model.__name__,
            schema_version=declared_schema_version(model),
            base=model in bases,
            structural_digest=structural_digest(model),
        )
        for model in models
    )


def recorded_path(repo_root: Path) -> Path:
    return repo_root / RECORDED_RELATIVE_PATH


def render_inventory_document(records: Sequence[ModelRecord]) -> str:
    payload = {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "subject": "contract-models",
        "models": [
            {
                "module": record.module,
                "name": record.name,
                "schema_version": record.schema_version,
                "base": record.base,
                "structural_digest": record.structural_digest,
            }
            for record in records
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def load_recorded_models(path: Path) -> tuple[ModelRecord, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple(
        ModelRecord(
            module=entry["module"],
            name=entry["name"],
            schema_version=entry["schema_version"],
            base=entry["base"],
            structural_digest=entry["structural_digest"],
        )
        for entry in payload["models"]
    )


@dataclass(frozen=True)
class InventoryDrift:
    subject: str
    field: str
    recorded: str
    live: str


def inventory_drift(
    recorded: Sequence[ModelRecord],
    live: Sequence[ModelRecord],
) -> tuple[InventoryDrift, ...]:
    """What the recorded inventory and the tree disagree about, keyed by model name.

    Keyed by name and not by ``module.name``, which is the point rather than a detail. A
    model that moves between modules is the same contract in a different file; a tripwire
    keyed on where it lives would report one model vanishing and an unrelated one appearing,
    and leave the reviewer to notice for themselves that the two had the same digest. Keyed
    on the name, the same move reports what it is: one relocation, nothing about the shape.
    """
    recorded_by_name = {record.name: record for record in recorded}
    live_by_name = {record.name: record for record in live}
    drift: list[InventoryDrift] = []
    for name in sorted(set(recorded_by_name) | set(live_by_name)):
        before = recorded_by_name.get(name)
        after = live_by_name.get(name)
        if before is not None and after is not None:
            drift.extend(
                InventoryDrift(subject=name, field=field, recorded=was, live=now)
                for field, was, now in (
                    ("module", before.module, after.module),
                    ("kind", "base" if before.base else "record", "base" if after.base else "record"),
                    ("schema_version", before.schema_version, after.schema_version),
                    ("structural digest", before.structural_digest, after.structural_digest),
                )
                if was != now
            )
        elif before is None and after is not None:
            drift.append(
                InventoryDrift(
                    subject=name,
                    field="presence",
                    recorded="not recorded",
                    live=f"{after.module}.{after.name}",
                )
            )
        elif before is not None:
            drift.append(
                InventoryDrift(
                    subject=name,
                    field="presence",
                    recorded=f"{before.module}.{before.name}",
                    live="no longer in the tree",
                )
            )
    return tuple(drift)
