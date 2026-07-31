"""The Phase 5 proof bundle, and the failures a bundle about people can have.

Phases 0 to 4 built bundles arguing that a mechanism works, and the dangerous failure in one
of those is prose giving a criterion a status the gate did not reach. That failure is
possible here too and is checked the same way. What is new is that this bundle's central
claim is about **two named people**, and a claim about people can go wrong in ways a claim
about a state machine cannot: it can read as though more people were involved than were, it
can quietly drop the runs that failed, and it can describe an approval as though somebody
other than the submitter gave it when the record says otherwise.

So the tests below check the things a reader of this particular bundle would be misled by.
That the runs which failed are still described, because a phase whose evidence is only its
successes has not been tested. That the bundle refuses to be built at all when no run is
committed, rather than rendering a confident document about nobody. And that the approver
and the submitter it prints are the ones in the record.

This module builds bundles, so it is listed in ``REENTRANT_TEST_MODULES`` and no criterion
may cite it. It is also excluded from the verification run inside every generator, which is
why building a bundle here does not recurse.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from edullm_platform.criteria import CriterionStatus
from edullm_platform.phase5_criteria import phase5_criteria
from edullm_platform.proof_bundle import (
    ProofBundleError,
    contradicting_status_claims,
    load_recorded_goldens,
)
from edullm_platform.proof_generator import standing
from tests.proof_support import skip_unless_reproducing
from tools.build_phase5_proof import (
    BUNDLE_FILENAMES,
    GENERATOR_COMMAND,
    Coherence,
    Verification,
    build_bundle,
    compute_goldens,
    default_output_dir,
    establish_coherence,
    goldens_path,
    known_limitations,
    read_runs,
    render_second_person,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDENS_PATH = goldens_path(default_output_dir(PROJECT_ROOT))
FIRST_INSTANT = datetime(2026, 1, 1, tzinfo=UTC)
SECOND_INSTANT = datetime(2026, 6, 30, 12, 34, 56, tzinfo=UTC)
GENERATED_AT_PREFIX = "Generated: "


@pytest.fixture(scope="session")
def coherence() -> Coherence:
    """One collection child, and every question that can be answered from it."""
    return establish_coherence(PROJECT_ROOT)


@pytest.fixture(scope="session")
def verification() -> Verification:
    """The nested runs. Requesting this is what opts a test into the expensive half.

    The skip is here rather than on each test so that the cost and the decision to pay it
    are the same thing. See ``tests/proof_support.py`` for why the default is not to.
    """
    skip_unless_reproducing()
    return verify_repository_for_tests()


def verify_repository_for_tests() -> Verification:
    from tools.build_phase5_proof import verify_repository

    return verify_repository(PROJECT_ROOT)


def read_bundle(directory: Path) -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(directory.iterdir())
        if path.is_file()
    }


def build_into(directory: Path, verification: Verification, moment: datetime) -> dict[str, str]:
    build_bundle(PROJECT_ROOT, directory, generated_at=moment, verification=verification)
    return read_bundle(directory)


def strip_generated_at(documents: dict[str, str]) -> dict[str, str]:
    return {
        name: "\n".join(
            line for line in text.splitlines() if not line.startswith(GENERATED_AT_PREFIX)
        )
        for name, text in documents.items()
    }


@pytest.fixture(scope="session")
def first_bundle(
    tmp_path_factory: pytest.TempPathFactory,
    verification: Verification,
) -> dict[str, str]:
    return build_into(tmp_path_factory.mktemp("first"), verification, FIRST_INSTANT)


# ---------------------------------------------------------------------------------------
# The cheap half: everything answerable from one collection child
# ---------------------------------------------------------------------------------------


def test_every_criterion_citation_resolves_before_a_bundle_is_written(
    coherence: Coherence,
) -> None:
    """Mutation: report an unresolvable citation instead of refusing.

    A matrix claiming coverage it cannot run is wrong before anybody runs anything, and the
    only useful moment to say so is before the bundle exists to be believed. The generator
    raises rather than printing, which is what this establishes.
    """
    assert coherence.selected_node_ids
    cited = {node_id for check in phase5_criteria() for node_id in check.cited_node_ids}

    assert cited <= set(coherence.collected_node_ids)


def test_the_generator_refuses_a_tree_with_no_pilot_run_committed(tmp_path: Path) -> None:
    """Mutation: render an empty bundle when no run is captured.

    This is the failure specific to a bundle about people. Every other document here would
    render perfectly well against a tree with no runs -- the criteria, the schema table, the
    branch protection -- and the result would be a confident bundle whose central claim is
    about nobody. Refusing is the only honest answer, because there is no wording for
    "two people used this" that is true of zero.
    """
    empty = tmp_path / "tree"
    (empty / "fixtures" / "evidence" / "phase-5").mkdir(parents=True)

    with pytest.raises(ProofBundleError) as refusal:
        read_runs(empty)

    assert "no pilot run" in str(refusal.value)


def test_the_opening_sentence_does_not_call_a_deferred_criterion_covered() -> None:
    """Mutation: open a green bundle by saying every criterion is covered.

    The shared opening was written for a phase that closes with nothing outstanding, and it
    says so in those words. Phase 5 was the first bundle to reach a green gate while carrying
    a deferral, so that sentence would have printed "every criterion is covered" over a
    criterion nobody has observed -- on the one page most reviewers read, and past the guard
    that reads status claims, which understands "check 6 is deferred" and not "every".

    **Asserted against a synthetic deferral rather than against this phase, since
    2026-07-31.** Phase 5's deferral was withdrawn and re-granted inside that one day, and for
    the hours it was withdrawn the phase exercised this branch not at all. Keying the test on
    ``phase5_criteria()`` would have left it passing while measuring nothing for exactly that
    window -- the same class of quiet failure the branch itself exists to prevent -- so it
    stays keyed on a deferral it constructs, whatever the phase happens to record.
    """
    opening = standing([], ["6"])

    assert "It is not done" not in opening
    assert "Every criterion is covered" not in opening
    assert "deferred" in opening
    assert "6" in opening, "the opening does not say which criterion is outstanding"


def test_the_opening_sentence_reports_this_phase_as_green_but_not_complete() -> None:
    """Mutation: open the bundle as though the phase had closed, or as though it had failed.

    **Both wrong openings are available here and they are wrong in opposite directions,
    which is why this is asserted rather than left to the generator.** As of 2026-07-31
    Phase 5 records no gaps and one deferral: criterion 6, the GPU checkpoint under a team
    other than ``platform``. "Every criterion is covered" is the first wrong opening -- one
    is not, it is deferred, and a deferral is a decision to accept something untrue for now
    rather than a small kind of covered. "It is not done" is the second, and it is what this
    file asserted while the same criterion was a gap; printing it now would report a green
    gate as a red one.

    Read off the definition rather than written down, because the wording and the statuses
    drifting apart is exactly what ``contradicting_status_claims`` is asserted here to catch
    -- and that drift is not hypothetical: the statuses moved twice on 2026-07-31.
    """
    numbers = {
        status: [check.number for check in phase5_criteria() if check.status is status]
        for status in CriterionStatus
    }
    opening = standing(numbers[CriterionStatus.GAP], numbers[CriterionStatus.DEFERRED])

    assert "No criterion is a gap and the gate is green" in opening
    assert "criterion 6 is deferred rather than covered" in opening
    assert "It is not done" not in opening
    assert contradicting_status_claims({"README.md": opening}, phase5_criteria()) == ()


def test_the_opening_sentence_is_unchanged_for_a_phase_that_defers_nothing() -> None:
    """Mutation: reword the settled branch while adding the deferral one.

    Phases 1 and 3 print this same sentence, and Phase 1's bundle is committed with the
    original wording. Changing it there would be an unrelated diff in a committed bundle
    inside a pull request about Phase 5, which is how a bundle stops being reviewable by
    diff.
    """
    assert "Every criterion is covered and the gate is green" in standing([], [])
    assert "It is not done" in standing(["6"], [])


def test_a_limitation_that_names_a_check_takes_its_status_from_the_definition() -> None:
    """Mutation: write the status word into a limitation by hand.

    A limitation saying "check 6 is covered" while the gate calls it a gap is the exact
    thing a reviewer who trusts this bundle would be misled by, and it is the easiest
    sentence in the repository to leave behind after a criterion moves.
    """
    limitations = known_limitations(phase5_criteria(), read_runs(PROJECT_ROOT))

    assert contradicting_status_claims({"README.md": "\n".join(limitations)}, phase5_criteria()) == ()


def test_a_limitation_naming_a_check_the_phase_does_not_have_is_refused() -> None:
    """Mutation: name a criterion number that was renumbered away.

    Criterion 14 was rewritten and criteria 12 to 15 were appended rather than interleaved
    precisely so that nothing renumbered. A limitation naming a criterion the phase does not
    have would survive that decision going wrong, and this is what notices.
    """
    numbers = {check.number for check in phase5_criteria()}
    invented = {"16", "17"}

    assert not (invented & numbers)
    contradictions = contradicting_status_claims(
        {"README.md": "Check 16 is covered."}, phase5_criteria()
    )

    assert contradictions


def test_the_second_person_document_names_the_approver_the_record_carries() -> None:
    """Mutation: print the submitter twice, or a constant, in the approver column.

    The one comparison this entire phase rests on is submitter against approver on the same
    record. A document that rendered the submitter into both columns would look correct,
    read correct, and assert the opposite of the phase's claim.
    """
    runs = read_runs(PROJECT_ROOT)
    document = render_second_person(PROJECT_ROOT, phase5_criteria())

    released = [run for run in runs if run.record.released_by_another_person]
    assert released
    for run in released:
        assert run.record.authorization.approver in document
        assert run.record.submitter in document
        assert run.record.authorization.approver != run.record.submitter


def test_the_runs_that_failed_are_described_rather_than_quietly_dropped() -> None:
    """Mutation: render only the runs whose outcome was success.

    A phase whose evidence is only its successes is a phase that has not been tested, and
    the two failures here each say something the successful run cannot: one that a container
    which never started leaves no result record, and one that no CPU run can reach Weights
    and Biases.
    """
    runs = read_runs(PROJECT_ROOT)
    document = render_second_person(PROJECT_ROOT, phase5_criteria())

    unsuccessful = [run for run in runs if run.record.result_outcome != "succeeded"]
    assert unsuccessful, "no committed run failed, so this check is measuring nothing"
    for run in unsuccessful:
        assert run.run_id in document


def test_every_committed_capture_carries_a_recorded_digest() -> None:
    """Mutation: record digests for some captures and not others.

    The goldens are the only thing standing between a capture being re-taken after the
    account moved on and nobody noticing. A capture with no recorded digest is one that can
    be replaced silently, and it would be the newest one -- the one most likely to matter.

    IT DOES NOT CATCH A CAPTURE BEING DELETED, AND THE TEST BELOW IS WHY THAT MATTERS. Both
    sides here are computed live from the same directory scan, so they shrink together.
    """
    runs = read_runs(PROJECT_ROOT)
    goldens = compute_goldens(PROJECT_ROOT)

    assert {golden.fixture for golden in goldens} == {run.run_id for run in runs}


def test_every_digest_the_bundle_recorded_still_has_a_capture_behind_it() -> None:
    """Mutation: compare the live goldens against the live runs and call it covered.

    That mutation is what this file did until a deletion audit tried it. Removing
    ``fixtures/evidence/phase-5/runs/run_019fb505-9b0f-70cc-b890-2c60037cfe41`` passed the
    Phase 5 tests, passed the gate at exit 0, and passed the full nightly reproduction --
    thirty-seven, zero and thirty-one green respectively. Two live sets derived from one
    directory scan cannot detect that the directory lost an entry.

    So this one reads the *committed* ``serialization-goldens.json`` instead, which is the
    protection phases 0 through 3 have had all along and Phase 5 did not. It matters more
    here than there: these captures are the only evidence that two people who did not build
    the platform used it, they cannot be re-taken once the account moves on, and the bundle
    goes on asserting a second person either way.
    """
    recorded = load_recorded_goldens(GOLDENS_PATH)

    assert recorded, (
        f"{GOLDENS_PATH} records no digests at all, so nothing below can fail. Run "
        f"`{GENERATOR_COMMAND}` to write them."
    )
    live = {golden.fixture for golden in compute_goldens(PROJECT_ROOT)}
    vanished = {record.fixture for record in recorded} - live
    assert not vanished, (
        f"the bundle records a digest for {sorted(vanished)}, and no capture under "
        "fixtures/evidence/phase-5/runs/ answers to it. Either a capture was deleted, in "
        f"which case restore it, or it was retired deliberately, in which case run "
        f"`{GENERATOR_COMMAND}` so the bundle stops claiming evidence it no longer holds."
    )


# ---------------------------------------------------------------------------------------
# The expensive half: only under EDULLM_REPRODUCE_PROOFS
# ---------------------------------------------------------------------------------------


@pytest.mark.slow
def test_the_bundle_contains_every_document_it_declares(first_bundle: dict[str, str]) -> None:
    assert set(first_bundle) == set(BUNDLE_FILENAMES)


@pytest.mark.slow
def test_a_later_run_differs_only_in_its_generated_at_line(
    tmp_path: Path,
    verification: Verification,
    first_bundle: dict[str, str],
) -> None:
    """Mutation: interpolate anything else that moves with the clock.

    A bundle that differs between two runs of the same tree cannot be reviewed by diff, and
    a reviewer who cannot diff it reads none of it the second time.
    """
    later = build_into(tmp_path / "later", verification, SECOND_INSTANT)

    assert strip_generated_at(later) == strip_generated_at(first_bundle)


@pytest.mark.slow
def test_no_prose_in_the_bundle_contradicts_a_recorded_check_status(
    first_bundle: dict[str, str],
) -> None:
    assert contradicting_status_claims(first_bundle, phase5_criteria()) == ()


@pytest.mark.slow
def test_the_index_reports_the_verdict_the_gate_reaches(first_bundle: dict[str, str]) -> None:
    """Mutation: print a green verdict while a criterion is a gap.

    The index is the only page most reviewers read, so the one sentence saying whether the
    gate passes has to be derived from the same definition the gate executes.
    """
    gaps = [
        check.number for check in phase5_criteria() if check.status is CriterionStatus.GAP
    ]
    index = first_bundle["README.md"]

    if gaps:
        assert "exits 1 against this tree" in index
        for number in gaps:
            assert number in index
    else:
        assert "exits 0 against this tree" in index
