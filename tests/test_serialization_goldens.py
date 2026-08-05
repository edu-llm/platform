"""The recorded canonical digests, recomputed against the tree on every run.

Three tripwires, checked the same way and failing for three different reasons.

A **contract fixture** digest moves when field ordering, a serializer, a default value or the
fixture itself changes, and it moves nowhere else in the suite.

An **IAM role template** digest moves when a role gains or loses a permission, because it is
taken over the projection the drift comparison acts on rather than over the file. Seven of
the nine roles have no capture to compare against, so for those this is the only thing
between a template widened in the meantime and nobody noticing.

An **admitted run capture** digest moves when one of the three pilot records is re-taken or
reformatted. Those records name workload profiles that have since been retired and cannot be
captured again, so a moved digest there is evidence being replaced rather than refreshed.

Both directions are checked for each set. Recomputing the live side alone cannot see an
artifact being deleted, because both sides of that comparison come from the same scan; the
committed file is what notices.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from edullm_platform.serialization_goldens import (
    GOLDEN_SETS,
    GOLDENS_MISSING_GUIDANCE,
    ROLE_TEMPLATES,
    GoldenSet,
    RecordedGolden,
    admitted_run_goldens,
    committed_role,
    contract_fixture_goldens,
    discover_fixtures,
    fixture_canonical_length,
    fixture_digest,
    golden_drift_guidance,
    load_recorded_goldens,
    recorded_path,
    role_template_goldens,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DRIFT_GUIDANCE = golden_drift_guidance()

SET_IDS = [golden_set.subject for golden_set in GOLDEN_SETS]


def recorded_for(golden_set: GoldenSet) -> tuple[RecordedGolden, ...]:
    path = recorded_path(PROJECT_ROOT, golden_set)
    return load_recorded_goldens(path) if path.exists() else ()


def every_recorded_digest() -> list[tuple[str, RecordedGolden]]:
    return [
        (golden_set.subject, record)
        for golden_set in GOLDEN_SETS
        for record in recorded_for(golden_set)
    ]


RECORDED_DIGESTS = every_recorded_digest()
RECORDED_IDS = [f"{subject}:{record.fixture}" for subject, record in RECORDED_DIGESTS]


def live_counterpart(subject: str, fixture: str) -> RecordedGolden:
    """The live digest for one recorded artifact, or a failure naming the one that is gone.

    Spelled out rather than left to ``next``, because a deleted artifact is a real case here
    and a bare ``StopIteration`` names neither the artifact nor what happened to it.
    """
    golden_set = next(candidate for candidate in GOLDEN_SETS if candidate.subject == subject)
    for candidate in golden_set.live(PROJECT_ROOT):
        if candidate.fixture == fixture:
            return candidate
    raise AssertionError(
        f"{fixture} has a recorded digest in "
        f"{recorded_path(PROJECT_ROOT, golden_set)} and nothing in the tree answers to it. "
        f"Either it was deleted, in which case restore it, or it was retired deliberately, "
        f"in which case re-record so the file stops claiming an artifact it does not hold."
    )


@pytest.mark.parametrize("golden_set", GOLDEN_SETS, ids=SET_IDS)
def test_every_subject_records_digests(golden_set: GoldenSet) -> None:
    path = recorded_path(PROJECT_ROOT, golden_set)

    assert recorded_for(golden_set), GOLDENS_MISSING_GUIDANCE.format(path=path)


@pytest.mark.parametrize("golden_set", GOLDEN_SETS, ids=SET_IDS)
def test_the_recorded_set_is_the_set_the_tree_holds(golden_set: GoldenSet) -> None:
    """Mutation: record digests for some artifacts and not others.

    Both directions, because they fail for different reasons. An artifact with no recorded
    digest can be replaced silently, and a recorded digest with no artifact behind it is a
    deletion nothing else notices.
    """
    live = {record.fixture for record in golden_set.live(PROJECT_ROOT)}
    recorded = {record.fixture for record in recorded_for(golden_set)}

    assert recorded == live, (
        f"{recorded_path(PROJECT_ROOT, golden_set)} records {sorted(recorded - live)} that "
        f"the tree does not hold, and does not record {sorted(live - recorded)} that it "
        f"does. Either an artifact was deleted, in which case restore it, or the change was "
        f"deliberate, in which case re-record."
    )


@pytest.mark.parametrize(("subject", "record"), RECORDED_DIGESTS, ids=RECORDED_IDS)
def test_the_recorded_digest_still_matches_the_live_artifact(
    subject: str, record: RecordedGolden
) -> None:
    live = live_counterpart(subject, record.fixture)

    assert live.digest == record.digest, GOLDEN_DRIFT_GUIDANCE.format(
        fixture=record.fixture,
        contract=record.contract,
        recorded=record.digest,
        live=live.digest,
    )


@pytest.mark.parametrize(("subject", "record"), RECORDED_DIGESTS, ids=RECORDED_IDS)
def test_the_recorded_contract_and_length_still_match(
    subject: str, record: RecordedGolden
) -> None:
    live = live_counterpart(subject, record.fixture)

    assert live.contract == record.contract
    assert live.canonical_json_bytes == record.canonical_json_bytes
    assert live.relative_path == record.relative_path


# --------------------------------------------------------------------------------------
# What each set is scoped by, which is a different question per set
# --------------------------------------------------------------------------------------


def test_every_fixture_on_disk_carries_a_digest() -> None:
    on_disk = {reference.fixture for reference in discover_fixtures(PROJECT_ROOT)}

    assert {record.fixture for record in contract_fixture_goldens(PROJECT_ROOT)} == on_disk


def test_a_fixture_digest_is_taken_over_the_validated_model() -> None:
    """Mutation: digest the file bytes, so reindenting reads as drift and a value does not."""
    reference = next(
        candidate
        for candidate in discover_fixtures(PROJECT_ROOT)
        if candidate.fixture == "cpu-routine.yaml"
    )
    record = next(
        candidate
        for candidate in contract_fixture_goldens(PROJECT_ROOT)
        if candidate.fixture == "cpu-routine.yaml"
    )

    assert record.digest == fixture_digest(PROJECT_ROOT, reference)
    assert record.canonical_json_bytes == fixture_canonical_length(PROJECT_ROOT, reference)


def test_every_committed_role_template_carries_a_digest() -> None:
    """Mutation: add a role to `infra/iam/` and to no registry, so nothing digests it."""
    assert {record.fixture for record in role_template_goldens(PROJECT_ROOT)} == {
        role_name for role_name, _template in ROLE_TEMPLATES
    }


@pytest.mark.parametrize(
    ("role_name", "relative_path"), ROLE_TEMPLATES, ids=[name for name, _ in ROLE_TEMPLATES]
)
def test_the_recorded_role_is_still_the_role_the_named_template_declares(
    role_name: str, relative_path: str
) -> None:
    projected = committed_role(PROJECT_ROOT, role_name=role_name, relative_path=relative_path)
    record = next(
        candidate
        for candidate in role_template_goldens(PROJECT_ROOT)
        if candidate.fixture == role_name
    )

    assert projected.role_name == role_name
    assert record.contract == "TemplateRole"
    assert record.relative_path == relative_path


def test_every_committed_pilot_capture_carries_a_digest() -> None:
    """Mutation: record digests for some captures and not others.

    The newest capture is both the one most likely to matter and the one a partial recording
    would leave out.
    """
    directory = PROJECT_ROOT / "fixtures" / "evidence" / "phase-5" / "runs"
    on_disk = {entry.name for entry in directory.iterdir() if entry.is_dir()}

    assert {record.fixture for record in admitted_run_goldens(PROJECT_ROOT)} == on_disk
    assert on_disk, "no pilot capture is committed, so this check is measuring nothing"
