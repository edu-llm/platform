"""The tripwire on every bundle's contract table, and the property it is there to hold.

A bundle carries two kinds of recorded digest and until now only one of them was checked.
``serialization-goldens.json`` had ``tests/test_phaseN_golden.py`` recomputing it and
failing on drift; ``schema-compatibility.md`` had nothing. Only the generators read it, so
it was written on regeneration and verified by nobody afterwards -- and both halves of that
went wrong at once. Phase 1's table went on naming ``phase1_evidence`` for nine models that
had moved to ``iam_documents``, and went on recording a ``DeployedRoleEvidence`` digest the
tree had stopped computing. Nothing failed. Phase 0's table had the same defect for
``Phase3GateReport``, and what caught it was somebody reading the file.

So the first half of this module is the check the goldens already had, applied to the other
recorded artifact: recompute every row of every table against the tree, in both directions,
and fail with the drift written out.

The second half is the property that made the tables worth checking at all. They used to be
introduced as *the contract models Phase N added*, which the generator answered by looking
at which module each model was in. That is a published claim about history computed from
where a file sits today, so moving a model falsified it -- and moving nine of them turned
"Phase 1 added twenty-eight models" into "Phase 1 added nineteen" without any phase having
delivered anything different. The claim is gone. What replaced it is a view that says only
what it can see, and the tests below pin the difference: relocating a model changes the
module recorded for it and changes nothing else any bundle says.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Literal

import pytest

from edullm_platform.contracts.base import ContractModel
from edullm_platform.proof_bundle import (
    InventoryDrift,
    ModelRecord,
    describe_inventory_drift,
    inventory_drift,
    model_records,
    recorded_models,
    recorded_schema_files,
    schema_file_drift,
    schema_file_records,
    structural_digest,
)
from tools import build_phase0_proof as phase0
from tools import build_phase1_proof as phase1
from tools import build_phase2_proof as phase2
from tools import build_phase3_proof as phase3

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: What all four generators call the document. Written out rather than imported because
#: each generator owns its own filenames; a generator that renames it fails the first test
#: below, which says where to look.
SCHEMA_TABLE = "schema-compatibility.md"

#: Every module path in the repository, which is the one thing a relocation may change.
MODULE_PATH = re.compile(r"edullm_platform(?:\.[A-Za-z_][A-Za-z0-9_]*)+")

#: Ways of saying a phase authored a contract model. None of them is a claim a bundle can
#: support; see :data:`edullm_platform.proof_bundle.SCOPE_IS_NOT_AUTHORSHIP`.
AUTHORSHIP_CLAIMS = (
    "models Phase",
    "models added by this phase",
    "contract models this phase",
)

TABLE_MISSING = (
    "no contract table was found at {path}. A bundle's schema-compatibility document is "
    "what this tripwire reads; generate it with `{command}` and commit the result."
)


@dataclass(frozen=True)
class Bundle:
    """One bundle's contract table, the models it answers for, and how to re-record it.

    ``select`` is the generator's own selector rather than a reimplementation of it, so a
    phase that changes which modules its bundle covers changes what this checks in one
    edit, and a relocation reaches these tests by the same path it reaches a regeneration.
    """

    generator: ModuleType
    phase: str
    command: str
    document: Path
    select: Callable[[Path], tuple[ModelRecord, ...]]
    render: Callable[[Sequence[ModelRecord]], str]

    @property
    def scope(self) -> tuple[ModelRecord, ...]:
        return self.select(PROJECT_ROOT)


def phase0_schema_report(models: Sequence[ModelRecord]) -> str:
    """Phase 0's renderer also takes the exported schema files, which nothing here varies."""
    return phase0.render_schema_report(models, schema_file_records(PROJECT_ROOT))


def bundle(
    generator: ModuleType,
    select: Callable[[Path], tuple[ModelRecord, ...]],
    render: Callable[[Sequence[ModelRecord]], str],
) -> Bundle:
    return Bundle(
        generator=generator,
        phase=generator.PHASE,
        command=generator.GENERATOR_COMMAND,
        document=generator.default_output_dir(PROJECT_ROOT) / SCHEMA_TABLE,
        select=select,
        render=render,
    )


BUNDLES = (
    bundle(phase0, model_records, phase0_schema_report),
    bundle(phase1, phase1.phase1_models, phase1.render_schema_report),
    bundle(phase2, phase2.phase2_models, phase2.render_schema_report),
    bundle(phase3, phase3.phase3_models, phase3.render_schema_report),
)
BUNDLE_IDS = [bundle.phase for bundle in BUNDLES]
COMPLETE_INVENTORY = BUNDLES[0]


def recorded(bundle: Bundle) -> str:
    return bundle.document.read_text(encoding="utf-8")


def report(drift: Sequence[InventoryDrift], bundle: Bundle) -> str:
    return describe_inventory_drift(drift, command=bundle.command)


# --------------------------------------------------------------------------------------
# The tripwire the goldens already had
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("bundle", BUNDLES, ids=BUNDLE_IDS)
def test_the_bundle_records_a_contract_table(bundle: Bundle) -> None:
    missing = TABLE_MISSING.format(path=bundle.document, command=bundle.command)

    assert bundle.document.exists(), missing
    assert recorded_models(recorded(bundle)), missing


@pytest.mark.parametrize("bundle", BUNDLES, ids=BUNDLE_IDS)
def test_every_recorded_contract_still_matches_the_live_model(bundle: Bundle) -> None:
    """Both directions, because both go wrong.

    A recorded row the tree no longer computes is a bundle describing a repository that
    does not exist. A model in scope that no row records is a shape nobody is watching.
    """
    drift = inventory_drift(recorded_models(recorded(bundle)), bundle.scope)

    assert drift == (), report(drift, bundle)


def test_every_recorded_schema_file_digest_still_matches_the_file() -> None:
    """The exported-schema digests recorded beside the contract tables, in Phase 0 only.

    ``tests/test_schema_export.py`` already checks each file against the model it was
    rendered from. What nothing checked is the digest the bundle recorded for that file,
    which is a separate claim and goes stale on its own.
    """
    drift = schema_file_drift(
        recorded_schema_files(recorded(COMPLETE_INVENTORY)),
        schema_file_records(PROJECT_ROOT),
    )

    assert drift == (), report(drift, COMPLETE_INVENTORY)


@pytest.mark.parametrize("bundle", BUNDLES[1:], ids=BUNDLE_IDS[1:])
def test_the_phase_table_records_no_digest_the_complete_inventory_disagrees_with(
    bundle: Bundle,
) -> None:
    """A phase table is a view, which is what makes leaving one survivable.

    A model that moves out of a phase's scope leaves that bundle and stays in Phase 0's
    inventory, so no digest stops being watched by the check above. That only holds while
    the two agree, and nothing but this made them.
    """
    inventory = {
        record.name: record.digest for record in recorded_models(recorded(COMPLETE_INVENTORY))
    }

    for record in recorded_models(recorded(bundle)):
        assert inventory.get(record.name) == record.digest, (
            f"{bundle.phase} records {record.name} as {record.digest}, which "
            f"proof/{COMPLETE_INVENTORY.phase}/{SCHEMA_TABLE} does not. A phase table is a "
            "view of the complete inventory and may not disagree with it."
        )


# --------------------------------------------------------------------------------------
# Moving a module changes nothing a bundle claims
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("bundle", BUNDLES, ids=BUNDLE_IDS)
def test_no_bundle_says_which_phase_added_a_contract_model(bundle: Bundle) -> None:
    """The claim a relocation falsified, kept out rather than corrected once.

    This is the whole of defect 1 in one assertion. A bundle may say what it can see -- a
    shape, and the module that shape is in today -- and authorship is not among it. The
    sentence is one line to reintroduce while regenerating for an unrelated reason, which
    is why it is asserted against the published documents rather than reviewed for.
    """
    for path in (bundle.document, bundle.document.parent / "README.md"):
        text = path.read_text(encoding="utf-8")
        for claim in AUTHORSHIP_CLAIMS:
            assert claim not in text, (
                f"{path.relative_to(PROJECT_ROOT)} says {claim!r}. A bundle cannot answer "
                "which phase added a model: it knows only which module the model is in "
                f"today, so moving one makes the claim false. Regenerate with "
                f"`{bundle.command}` once the generator has stopped writing it."
            )


def test_a_models_structural_digest_does_not_depend_on_where_the_model_lives() -> None:
    """The sentence every one of these tables prints, turned into a check.

    Each report says the digest "does not change when unrelated code moves". Nothing
    established it, and it is what everything below rests on.
    """

    class Relocatable(ContractModel):
        schema_version: Literal["1"] = "1"
        name: str

    before = structural_digest(Relocatable)
    Relocatable.__module__ = "edullm_platform.somewhere_else_entirely"

    assert structural_digest(Relocatable) == before


@pytest.mark.parametrize("bundle", BUNDLES, ids=BUNDLE_IDS)
def test_relocating_every_model_changes_only_the_module_recorded_for_it(
    bundle: Bundle,
) -> None:
    """A model that moves and stays in scope takes nothing with it.

    Rename the module of every model, re-render, and blank the module paths out of both
    documents: what is left is byte-identical. No digest, no kind, no declared version, no
    count and no sentence depends on where a contract lives.

    Blanking is applied to the whole document rather than to the parsed table, so a
    generator that started deriving prose from module names is caught here as well.
    """
    relocated = tuple(
        replace(record, module=f"edullm_platform.relocated_{index}")
        for index, record in enumerate(bundle.scope)
    )

    before = bundle.render(bundle.scope)
    after = bundle.render(relocated)

    assert MODULE_PATH.sub("<module>", after) == MODULE_PATH.sub("<module>", before)


@pytest.mark.parametrize("bundle", BUNDLES[1:], ids=BUNDLE_IDS[1:])
def test_a_model_that_moves_out_of_a_phase_stays_in_the_complete_inventory(
    bundle: Bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half, and the reason a scoped table is safe to keep.

    A phase table is scoped by module, so a model that moves to a file the phase does not
    list drops out of it -- which is what happened to nine IAM document types, and is the
    move this whole change is about. What must not happen is the digest going unwatched,
    and it does not: Phase 0's table is scoped by nothing, so the same relocation leaves
    the model there with the same digest, still recomputed by the tripwire above.

    Relocating through the generator's own ``model_records`` rather than through its
    selector is deliberate. It is the same path a real move takes, so a phase that started
    scoping some other way is measured on what it actually does.
    """
    live = model_records(PROJECT_ROOT)
    leaving = bundle.scope[0]
    relocated = tuple(
        replace(record, module="edullm_platform.some_other_file")
        if record.name == leaving.name
        else record
        for record in live
    )
    monkeypatch.setattr(bundle.generator, "model_records", lambda _root: relocated)

    remaining = {record.name for record in bundle.select(PROJECT_ROOT)}
    inventory = {record.name: record.structural_digest for record in relocated}

    assert leaving.name not in remaining
    assert inventory[leaving.name] == leaving.structural_digest
