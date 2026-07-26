"""The recorded digest of what each committed role template grants, checked on every run.

The digest is over the projection the drift comparison acts on rather than over the file,
so this fails when a role gains or loses a permission and not when somebody rewrites a
comment. Re-recording it is deliberate, and it is also the moment the account needs
re-capturing: a role that compared clean against the old projection has not been compared
against the new one.
"""

from pathlib import Path

import pytest

from edullm_platform.proof_bundle import (
    RecordedGolden,
    golden_drift_guidance,
    load_recorded_goldens,
)
from tools.build_phase1_proof import (
    GENERATOR_COMMAND,
    GOLDENS_MISSING_GUIDANCE,
    committed_role,
    compute_goldens,
    default_output_dir,
    goldens_path,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDENS_PATH = goldens_path(default_output_dir(PROJECT_ROOT))
GOLDEN_DRIFT_GUIDANCE = golden_drift_guidance(command=GENERATOR_COMMAND)

RECORDED_GOLDENS = load_recorded_goldens(GOLDENS_PATH) if GOLDENS_PATH.exists() else ()
RECORDED_IDS = [record.fixture for record in RECORDED_GOLDENS]


def test_the_proof_bundle_records_a_digest_for_every_committed_role() -> None:
    assert RECORDED_GOLDENS, GOLDENS_MISSING_GUIDANCE.format(path=GOLDENS_PATH)
    assert {record.fixture for record in RECORDED_GOLDENS} == {
        record.fixture for record in compute_goldens(PROJECT_ROOT)
    }, GOLDENS_MISSING_GUIDANCE.format(path=GOLDENS_PATH)


@pytest.mark.parametrize("record", RECORDED_GOLDENS, ids=RECORDED_IDS)
def test_the_recorded_role_digest_still_matches_the_committed_template(
    record: RecordedGolden,
) -> None:
    live = next(
        candidate
        for candidate in compute_goldens(PROJECT_ROOT)
        if candidate.fixture == record.fixture
    )

    assert live.digest == record.digest, GOLDEN_DRIFT_GUIDANCE.format(
        fixture=record.fixture,
        contract=record.contract,
        recorded=record.digest,
        live=live.digest,
    )


@pytest.mark.parametrize("record", RECORDED_GOLDENS, ids=RECORDED_IDS)
def test_the_recorded_role_is_still_the_role_the_named_template_declares(
    record: RecordedGolden,
) -> None:
    projected = committed_role(
        PROJECT_ROOT, role_name=record.fixture, relative_path=record.relative_path
    )

    assert projected.role_name == record.fixture
    assert record.contract == "TemplateRole"
