"""Every contract model's shape, recomputed against the recorded inventory on every run.

The digest is over a model's JSON schema, so it moves when a field is added, removed,
retyped or reconstrained, and stays put when a docstring or a comment moves. What that
buys is the question a reviewer cannot answer by reading a diff: whether a payload already
written against the old shape is one the new shape would refuse. A lineage record in S3
cannot be rewritten, so a widened or narrowed contract is a decision rather than an edit.

Sixteen of these models are exported to ``schemas/`` and re-derived on every CI run, so a
change to one of those already fails a build. This is what covers the other hundred and
thirty-three. Before 2026-08-05 the same inventory lived in a generated markdown table under
`proof/`, and the tripwire over it lived in `tests/test_schema_compatibility.py`.

Both directions, because they mean different things. A model in the tree with no recorded
row is a contract nothing is watching; a recorded row with no model behind it is a table
that describes a repository other than this one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from edullm_platform.contract_inventory import (
    INVENTORY_DRIFT_GUIDANCE,
    ModelRecord,
    inventory_drift,
    load_recorded_models,
    model_records,
    recorded_path,
    structural_digest,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RECORDED_PATH = recorded_path(PROJECT_ROOT)

RECORDED = load_recorded_models(RECORDED_PATH) if RECORDED_PATH.exists() else ()
RECORDED_IDS = [f"{record.module}.{record.name}" for record in RECORDED]


def test_the_inventory_is_recorded_at_all() -> None:
    assert RECORDED, (
        f"{RECORDED_PATH} records no contract models, so nothing below can fail. Run "
        "`uv run python tools/record_goldens.py` to write it."
    )


def test_the_recorded_inventory_still_describes_this_tree() -> None:
    """Mutation: add, remove or reshape a contract model and re-record nothing.

    One assertion over the whole inventory rather than one per model, because the failure
    worth reading is the list of what moved and in which field.
    """
    drift = inventory_drift(RECORDED, model_records())

    assert drift == (), "\n\n".join(
        INVENTORY_DRIFT_GUIDANCE.format(
            subject=entry.subject,
            field=entry.field,
            recorded=entry.recorded,
            live=entry.live,
        )
        for entry in drift
    )


@pytest.mark.parametrize("record", RECORDED, ids=RECORDED_IDS)
def test_every_recorded_model_still_has_the_shape_recorded_for_it(record: ModelRecord) -> None:
    """The per-model half, so a failure names the contract rather than the inventory."""
    live = {f"{item.module}.{item.name}": item for item in model_records()}
    key = f"{record.module}.{record.name}"

    assert key in live, (
        f"{key} has a recorded structural digest and no model in the tree answers to it. "
        f"Either it was deleted, in which case the payloads written against it are now "
        f"unreadable by anything here, or it moved, in which case re-record."
    )
    assert live[key].structural_digest == record.structural_digest


def test_a_models_structural_digest_does_not_depend_on_where_the_model_lives() -> None:
    """Mutation: digest the module path alongside the schema.

    A model that moves files is the same contract, and a digest that moved with it would
    report a relocation as a shape change. The inventory records the module separately and
    reports a move as a move.
    """
    live = {record.name: record for record in model_records()}
    subject = live["RunManifest"]

    from edullm_platform.contracts.manifest import RunManifest

    assert subject.structural_digest == structural_digest(RunManifest)
    assert subject.module == "edullm_platform.contracts.manifest"


def test_the_inventory_covers_more_than_the_exported_schemas() -> None:
    """The reason this file exists rather than leaning on `tools/export_schemas.py`.

    That tool renders sixteen models and CI diffs the result, which is a real tripwire for
    those sixteen. It says nothing about the rest, and the rest is most of them.
    """
    exported = {path.stem for path in (PROJECT_ROOT / "schemas").glob("*.json")}

    assert len(RECORDED) > 4 * len(exported)
