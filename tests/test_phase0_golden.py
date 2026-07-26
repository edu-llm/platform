from pathlib import Path

import pytest

from tools.build_phase0_proof import (
    GOLDEN_DRIFT_GUIDANCE,
    GOLDENS_MISSING_GUIDANCE,
    RecordedGolden,
    default_output_dir,
    discover_fixtures,
    fixture_canonical_length,
    fixture_digest,
    goldens_path,
    load_recorded_goldens,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDENS_PATH = goldens_path(default_output_dir(PROJECT_ROOT))

RECORDED_GOLDENS = load_recorded_goldens(GOLDENS_PATH) if GOLDENS_PATH.exists() else ()
RECORDED_IDS = [record.fixture for record in RECORDED_GOLDENS]


def test_the_proof_bundle_records_golden_digests() -> None:
    assert RECORDED_GOLDENS, GOLDENS_MISSING_GUIDANCE.format(path=GOLDENS_PATH)


def test_recorded_goldens_cover_every_fixture_on_disk() -> None:
    on_disk = {reference.fixture for reference in discover_fixtures(PROJECT_ROOT)}
    assert {record.fixture for record in RECORDED_GOLDENS} == on_disk, (
        GOLDENS_MISSING_GUIDANCE.format(path=GOLDENS_PATH)
    )


@pytest.mark.parametrize("record", RECORDED_GOLDENS, ids=RECORDED_IDS)
def test_recorded_fixture_digest_still_matches_the_live_contract(record: RecordedGolden) -> None:
    reference = next(
        candidate
        for candidate in discover_fixtures(PROJECT_ROOT)
        if candidate.fixture == record.fixture
    )
    live_digest = fixture_digest(PROJECT_ROOT, reference)
    assert live_digest == record.digest, GOLDEN_DRIFT_GUIDANCE.format(
        fixture=record.fixture,
        contract=record.contract,
        recorded=record.digest,
        live=live_digest,
    )


@pytest.mark.parametrize("record", RECORDED_GOLDENS, ids=RECORDED_IDS)
def test_recorded_fixture_contract_and_length_still_match(record: RecordedGolden) -> None:
    reference = next(
        candidate
        for candidate in discover_fixtures(PROJECT_ROOT)
        if candidate.fixture == record.fixture
    )
    assert reference.contract == record.contract
    assert fixture_canonical_length(PROJECT_ROOT, reference) == record.canonical_json_bytes
