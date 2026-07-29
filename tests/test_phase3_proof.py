"""The Phase 3 proof bundle, and the two things a mostly-empty bundle must never do.

A bundle exists so a reviewer can decide whether the phase is done without reading the
suite. Phase 3's bundle is unusual in that most of it is empty, and that changes which
failures are worth testing for. The dangerous ones are no longer only "prose that gives a
criterion a status the gate did not reach"; they are also **an empty section quietly
disappearing**, and **an empty section reading as though the thing it describes happened**.

A document omitted because there is nothing to put in it makes the phase look like it has
fewer claims than it has, and nobody counts what is not there. So every hole is generated,
says why it is empty, says what would fill it, and names the criteria waiting on it -- with
each of those statuses read off the definition rather than typed, which is what stops this
bundle disagreeing with its own gate.

This module builds bundles, so it is listed in ``REENTRANT_TEST_MODULES`` and no criterion
may cite it. It is also excluded from the verification run inside every generator, which is
why building a bundle here does not recurse.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from edullm_platform.criteria import CriterionStatus
from edullm_platform.phase3_criteria import phase3_criteria
from edullm_platform.proof_bundle import (
    GoldenDigestDriftError,
    MissingTestNodeError,
    ProofBundleError,
    contradicting_status_claims,
    load_recorded_goldens,
)
from tests.proof_support import skip_unless_reproducing
from tools.build_phase3_proof import (
    BUNDLE_FILENAMES,
    EMPTY_SECTIONS,
    GENERATOR_TEST_PATH,
    NESTED_RUN_ENV,
    Coherence,
    Verification,
    build_bundle,
    compute_goldens,
    establish_coherence,
    goldens_path,
    known_limitations,
    main,
    verify_repository,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
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

    The skip is here rather than on each test so that the cost and the decision to pay
    it are the same thing. See ``tests/proof_support.py`` for why the default is not to.
    """
    skip_unless_reproducing()
    return verify_repository(PROJECT_ROOT)


def shipped_checks() -> tuple:
    return phase3_criteria()


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


@pytest.mark.slow
def test_the_bundle_contains_every_document_it_declares(first_bundle: dict[str, str]) -> None:
    assert set(first_bundle) == set(BUNDLE_FILENAMES)


@pytest.mark.slow
def test_two_runs_at_the_same_instant_are_byte_identical(
    tmp_path_factory: pytest.TempPathFactory,
    verification: Verification,
    first_bundle: dict[str, str],
) -> None:
    again = build_into(tmp_path_factory.mktemp("second"), verification, FIRST_INSTANT)

    assert again == first_bundle


@pytest.mark.slow
def test_a_later_run_differs_only_in_its_generated_at_line(
    tmp_path_factory: pytest.TempPathFactory,
    verification: Verification,
    first_bundle: dict[str, str],
) -> None:
    later = build_into(tmp_path_factory.mktemp("later"), verification, SECOND_INSTANT)

    assert later != first_bundle
    assert strip_generated_at(later) == strip_generated_at(first_bundle)


@pytest.mark.slow
def test_every_document_passes_the_evidence_secret_scan(first_bundle: dict[str, str]) -> None:
    # This bundle quotes IAM policy resources, a quota request id and a set of subnet ids,
    # which is where an account id or a credential-shaped token would arrive.
    from edullm_platform.proof_bundle import assert_secret_free

    for name, text in first_bundle.items():
        assert_secret_free(name, text)


# --------------------------------------------------------------------------------------
# The bundle may not state a status the gate did not reach
# --------------------------------------------------------------------------------------


@pytest.mark.slow
def test_no_prose_in_the_bundle_contradicts_a_recorded_check_status(
    first_bundle: dict[str, str],
) -> None:
    assert contradicting_status_claims(first_bundle, shipped_checks()) == ()


@pytest.mark.slow
def test_the_generator_refuses_to_write_a_bundle_whose_prose_contradicts_the_gate(
    tmp_path: Path,
    verification: Verification,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The one defect a bundle cannot survive. A reviewer who trusts it without reading the
    # suite would come away believing a gap was closed -- which in this phase would mean
    # believing a container had run.
    # A claim in the other direction now that check 1 is covered: a bundle understating a
    # closed check is the same defect as one overstating an open one, because a reviewer
    # cannot tell which way a bundle is wrong without reading the suite it exists to
    # replace.
    monkeypatch.setattr(
        "tools.build_phase3_proof.known_limitations",
        lambda checks, goldens: ("Check 1 is a gap.",),
    )

    with pytest.raises(ProofBundleError, match="did not reach"):
        build_bundle(PROJECT_ROOT, tmp_path, generated_at=FIRST_INSTANT, verification=verification)

    assert not (tmp_path / "README.md").exists()


@pytest.mark.slow
def test_the_generator_refuses_a_bundle_that_miscounts_the_criteria_in_the_aggregate(
    tmp_path: Path,
    verification: Verification,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No check is named, so the numbered reader sees nothing, and the sentence is still a
    # status claim about this phase. With twenty gaps this is the easiest sentence in the
    # bundle to get wrong by one.
    monkeypatch.setattr(
        "tools.build_phase3_proof.known_limitations",
        lambda checks, goldens: ("Nineteen criteria are gaps today.",),
    )

    with pytest.raises(ProofBundleError, match="nineteen criteria are gap"):
        build_bundle(PROJECT_ROOT, tmp_path, generated_at=FIRST_INSTANT, verification=verification)

    assert not (tmp_path / "README.md").exists()


def test_a_limitation_that_names_a_check_takes_its_status_from_the_definition() -> None:
    # The limitations are the one place prose and status meet, so the status word is read
    # off the checks rather than typed. This is what makes the guard above never fire.
    limitations = known_limitations(shipped_checks(), compute_goldens(PROJECT_ROOT))

    assert any(
        "check 1 -- that a valid run reaches succeeded -- is covered" in text.lower()
        for text in limitations
    )
    assert any("check 22 is covered" in text.lower() for text in limitations)


def test_a_limitation_naming_a_check_the_phase_does_not_have_is_refused() -> None:
    with pytest.raises(ProofBundleError, match="does not record"):
        known_limitations(shipped_checks()[:0], compute_goldens(PROJECT_ROOT))


# --------------------------------------------------------------------------------------
# What an empty bundle has to say about being empty
# --------------------------------------------------------------------------------------


@pytest.mark.slow
def test_every_empty_document_is_written_rather_than_omitted(
    first_bundle: dict[str, str],
) -> None:
    """Mutation: skip a section that has nothing in it.

    A document omitted because there was nothing to put in it makes the phase look like it
    has fewer claims than it has, and nobody counts what is not there.

    Seven of the seventeen files existed to hold live evidence and held none. Six now hold
    it, rendered from the captures of four completed runs, and the seventh -- EventBridge
    delivery -- is still empty because the two checks it serves need a redelivered event
    and an inventory of the whole lineage store, neither of which any completed run
    produced. The count is asserted so that a document quietly moving between the two
    groups is visible here rather than only in a diff of the bundle.
    """
    assert len(EMPTY_SECTIONS) == 1
    for section in EMPTY_SECTIONS:
        assert section.filename in first_bundle, section.filename
        assert section.filename in BUNDLE_FILENAMES

    # The six that used to be empty are in the bundle and are no longer empty, which is the
    # half this test would otherwise stop checking.
    for filename in (
        "batch-execution-evidence.md",
        "log-stream-evidence.md",
        "lineage-record-evidence.md",
        "cancellation-and-timeout-evidence.md",
        "deployed-role-drift.md",
        "rollback-evidence.md",
    ):
        assert filename in BUNDLE_FILENAMES
        assert "This document is empty" not in first_bundle[filename], filename


@pytest.mark.slow
def test_every_empty_document_says_why_and_what_would_fill_it(
    first_bundle: dict[str, str],
) -> None:
    """Mutation: leave a heading with nothing under it.

    An empty section that does not say why is indistinguishable from one somebody forgot to
    write, and a reader cannot tell whether the evidence is missing or the document is.
    """
    for section in EMPTY_SECTIONS:
        text = first_bundle[section.filename]
        assert "This document is empty" in text
        # The reason is no longer that the phase is undeployed, and the boilerplate has to
        # say the reason it actually has. A document still blaming a held wave would send a
        # reader looking for a deploy that happened.
        assert "no longer that the phase is undeployed" in text
        assert "Wave 5" not in text
        assert section.records in text
        for filler in section.filled_by:
            assert filler in text
        assert len(section.filled_by) >= 1


@pytest.mark.slow
def test_every_empty_document_names_criteria_the_phase_actually_has(
    first_bundle: dict[str, str],
) -> None:
    """Mutation: point a section at a criterion number nobody defined.

    ``recorded_status`` raises for a number the definition does not record, so this is a
    property of the generator rather than of the text -- but a section pointing at check 23
    would otherwise read as an obligation nothing tracks.
    """
    numbers = {check.number for check in shipped_checks()}

    for section in EMPTY_SECTIONS:
        assert section.closes
        assert set(section.closes) <= numbers, section.filename
        for number in section.closes:
            assert f"| {number} |" in first_bundle[section.filename]


@pytest.mark.slow
def test_no_empty_document_reads_as_though_the_thing_it_describes_happened(
    first_bundle: dict[str, str],
) -> None:
    """The second failure a mostly-empty bundle is prone to, and the subtler one.

    A section describing what it *will* record, written in the present tense, reads at a
    skim exactly like one describing what was recorded. Every criterion these sections serve
    is a gap today, so the status column in each of them must say so -- read off the
    definition, so this cannot drift from the gate.
    """
    for section in EMPTY_SECTIONS:
        text = first_bundle[section.filename]
        for number in section.closes:
            (check,) = [one for one in shipped_checks() if one.number == number]
            assert check.status is CriterionStatus.GAP, (
                f"{section.filename} names check {number}, which is no longer a gap; the "
                "section is either no longer empty or no longer honest"
            )
            assert "| a gap |" in text


@pytest.mark.slow
def test_the_index_reports_the_verdict_the_gate_reaches(first_bundle: dict[str, str]) -> None:
    gaps = [check.number for check in shipped_checks() if check.status is CriterionStatus.GAP]
    index = first_bundle["README.md"]

    if gaps:
        assert (
            "`tools/validate_phase3.py` exits 1 against this tree. Phase 3 is not accepted: "
            f"criteria {', '.join(gaps)} are GAPs." in index
        )
    else:
        assert "`tools/validate_phase3.py` exits 0 against this tree" in index


@pytest.mark.slow
def test_the_index_counts_what_has_not_happened_rather_than_leaving_it_out(
    first_bundle: dict[str, str],
) -> None:
    # A zero somebody wrote down is a claim; an absent row is an oversight. These four are
    # the ones a reader would assume were non-zero if the bundle did not say.
    index = first_bundle["README.md"]

    assert "| Batch jobs run | 0 |" in index
    assert "| lineage records written by this phase | 0 |" in index
    assert "| denial matrices executed | 0 |" in index
    assert "| roles compared to a capture | 0 |" in index


@pytest.mark.slow
def test_the_matrix_reports_the_gaps_before_the_per_check_detail(
    first_bundle: dict[str, str],
) -> None:
    matrix = first_bundle["negative-case-matrix.md"]
    gaps = [check.number for check in shipped_checks() if check.status is CriterionStatus.GAP]

    if gaps:
        assert matrix.index("## Gaps") < matrix.index("## Checks")
    else:
        assert "## Gaps" not in matrix
    for number in gaps:
        assert f"### Check {number} (GAP)" in matrix


# --------------------------------------------------------------------------------------
# The two probes, and the reason this bundle records them at all
# --------------------------------------------------------------------------------------


@pytest.mark.slow
def test_the_method_document_carries_both_probes_and_all_of_their_controls(
    first_bundle: dict[str, str],
) -> None:
    """Mutation: record a probe's result and not its controls.

    A measurement without a control is not evidence. This phase's plan opened with a
    confidently wrong finding produced by a plausible, specific, uncontrolled simulation, and
    the controls are the whole difference between that and the measurements that held.
    """
    from edullm_platform.ec2_authorization import CONTROL_OBSERVATIONS

    method = first_bundle["measurement-method.md"]

    for control in CONTROL_OBSERVATIONS:
        assert control.action in method
        assert control.established_by in method
    assert "cloudformation:ValidateTemplate" in method
    assert "cloudformation:DescribeStacks" in method
    assert "logs:DescribeLogGroups" in method
    assert "OrganizationsDecisionDetail" in method
    # All four verdicts, because a probe that could not tell a quota from a denial is the
    # specific way the first answer was wrong.
    for verdict in ("DryRunOperation", "UnauthorizedOperation", "LimitExceeded"):
        assert verdict in method


@pytest.mark.slow
def test_the_networking_document_records_the_terms_rather_than_only_the_ids(
    first_bundle: dict[str, str],
) -> None:
    # Mutation: record the ids without the ownership, which would make a borrowed VPC
    # indistinguishable from ours a month later.
    networking = first_bundle["networking-evidence.md"]

    assert "L-F678F1CE" in networking
    assert "Whose network this is" in networking
    assert "us-east-2" in networking
    assert "offers the instance type" in networking


@pytest.mark.slow
def test_the_denial_document_separates_the_matrix_that_ran_from_the_one_that_did_not(
    first_bundle: dict[str, str],
) -> None:
    """The two matrices are in different states and the document must not average them.

    Mutation: go back to "neither has ever run", or forward to "both have". The admission
    matrix executes in every submission against a real session; the workload matrix runs
    inside the container and no command has ever run it. A sentence covering both would be
    false about one of them whichever way it was written, and the direction it is false in
    is the direction a reader would over-trust.

    Having run is also not the same as being recorded here, and the document has to say so:
    the admission matrix writes to a GitHub artifact that expires, which is why the check
    resting on it is still open.
    """
    from edullm_platform.batch_denials import (
        ADMISSION_BATCH_DENIED_ACTIONS,
        BATCH_PROBE_LESSONS,
        WORKLOAD_DENIED_ACTIONS,
    )

    document = first_bundle["batch-denial-matrix.md"]

    for action in (*ADMISSION_BATCH_DENIED_ACTIONS, *WORKLOAD_DENIED_ACTIONS):
        assert action in document
    for lesson in BATCH_PROBE_LESSONS:
        assert lesson.rule in document
        assert lesson.learned_from in document
    assert "The admission matrix has run" in document
    assert "The workload matrix has not" in document
    assert "neither has ever run" not in document
    # The artifact retention is why check 12 is still a gap, and the document says it
    # rather than leaving a reader to wonder why a matrix that passed closes nothing.
    assert "thirty-day retention" in document


@pytest.mark.slow
def test_the_open_decisions_document_separates_the_answered_one_from_the_open_ones(
    first_bundle: dict[str, str],
) -> None:
    from edullm_platform.open_decisions import open_decisions

    document = first_bundle["open-decisions.md"]

    assert "The one this phase answered" in document
    assert "config/image-exceptions.yaml" in document
    for decision in open_decisions():
        assert decision.question in document
        assert decision.lands_in in document
        for option in decision.options:
            assert option in document


# --------------------------------------------------------------------------------------
# Golden digests over what a role template grants
# --------------------------------------------------------------------------------------


def test_the_recorded_goldens_cover_every_phase_three_role() -> None:
    goldens = compute_goldens(PROJECT_ROOT)
    recorded = load_recorded_goldens(goldens_path(PROJECT_ROOT / "proof" / "phase-3"))

    assert {record.fixture for record in recorded} == {record.fixture for record in goldens}
    assert {record.digest for record in recorded} == {record.digest for record in goldens}


@pytest.mark.slow
def test_a_widened_template_refuses_the_build_rather_than_being_re_recorded(
    tmp_path: Path,
    verification: Verification,
) -> None:
    # Worth more here than in Phase 1: none of these roles is deployed, so no capture
    # compares them and this digest is the only thing that notices a template widened
    # before the deploy.
    build_bundle(PROJECT_ROOT, tmp_path, generated_at=FIRST_INSTANT, verification=verification)
    recorded = json.loads(goldens_path(tmp_path).read_text(encoding="utf-8"))
    recorded["fixtures"][0]["digest"] = "sha256:" + "0" * 64
    goldens_path(tmp_path).write_text(json.dumps(recorded, indent=2, sort_keys=True) + "\n")

    with pytest.raises(GoldenDigestDriftError, match="--regenerate-goldens"):
        build_bundle(PROJECT_ROOT, tmp_path, generated_at=FIRST_INSTANT, verification=verification)


@pytest.mark.slow
def test_regenerating_records_the_live_digest(tmp_path: Path, verification: Verification) -> None:
    build_bundle(PROJECT_ROOT, tmp_path, generated_at=FIRST_INSTANT, verification=verification)
    recorded = json.loads(goldens_path(tmp_path).read_text(encoding="utf-8"))
    recorded["fixtures"][0]["digest"] = "sha256:" + "0" * 64
    goldens_path(tmp_path).write_text(json.dumps(recorded, indent=2, sort_keys=True) + "\n")

    build_bundle(
        PROJECT_ROOT,
        tmp_path,
        generated_at=FIRST_INSTANT,
        regenerate_goldens=True,
        verification=verification,
    )

    assert load_recorded_goldens(goldens_path(tmp_path)) == compute_goldens(PROJECT_ROOT)


def test_main_reports_a_drifted_digest_as_a_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    goldens_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    goldens_path(tmp_path).write_text(
        json.dumps({"fixtures": [], "phase": "phase-3", "schema_version": 1}) + "\n"
    )

    exit_code = main(["--output-dir", str(tmp_path)])

    assert exit_code == 1
    assert "no longer serializes to its recorded canonical digest" in capsys.readouterr().err


# --------------------------------------------------------------------------------------
# The verification run
# --------------------------------------------------------------------------------------


def test_the_verification_run_never_selects_a_module_that_would_recurse(
    coherence: Coherence,
) -> None:
    assert any(
        node_id.startswith(GENERATOR_TEST_PATH) for node_id in coherence.collected_node_ids
    )
    assert not any(
        node_id.startswith(GENERATOR_TEST_PATH) for node_id in coherence.selected_node_ids
    )
    assert not any(
        node_id.startswith("tests/test_phase3_criteria.py")
        for node_id in coherence.selected_node_ids
    )


def test_the_selection_covers_every_cited_node_id(coherence: Coherence) -> None:
    """Which tests would run, answered from the collection rather than from running them.

    A citation the selection would leave out is a criterion whose proof was never going
    to be executed, and the collection says so. That the selection then runs green is
    the other half, below, and it is reproduced nightly.
    """
    cited = {node_id for check in shipped_checks() for node_id in check.cited_node_ids}

    assert cited
    assert cited <= set(coherence.selected_node_ids)


@pytest.mark.slow
def test_the_selection_ran_green(verification: Verification) -> None:
    assert verification.failed_node_ids == ()
    assert verification.selected.green


@pytest.mark.slow
def test_the_full_suite_ran_green_inside_the_generator(verification: Verification) -> None:
    assert verification.full_suite.green
    assert verification.full_suite.tests > 0


def test_a_citation_pytest_cannot_collect_aborts_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A matrix may not print a citation nothing ran. Writing the bundle anyway is how a
    # renamed test turns into a green tick beside a claim nobody checked.
    #
    # Patched on the shared module rather than on this generator, because the collection
    # call moved there. Patching a name the generator no longer imports would raise
    # rather than substitute, which is the failure that caught this.
    monkeypatch.setattr(
        "edullm_platform.proof_generator.collect_node_ids",
        lambda repo_root, *, nested_env: ("tests/test_manifest.py::test_something_else",),
    )

    with pytest.raises(MissingTestNodeError, match="may not claim coverage it cannot run"):
        establish_coherence(PROJECT_ROOT)


def test_the_generator_refuses_to_run_from_inside_its_own_verification(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(NESTED_RUN_ENV, "1")

    assert main([]) == 2
    assert "would" not in capsys.readouterr().out


@pytest.mark.slow
def test_the_measurements_the_bundle_rests_on_have_to_load(
    tmp_path: Path,
    verification: Verification,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: render the networking document from a capture that no longer loads.

    Two documents are rendered from the committed account measurements, and that record is
    a FreshEvidenceModel: thirty days after it was observed it stops loading, and every
    premise this phase rests on becomes something nobody has confirmed lately. Refusing is
    what makes the expiry visible rather than leaving a half-rendered table.
    """
    monkeypatch.setattr("tools.build_phase3_proof.MEASUREMENTS_PATH", "does/not/exist.json")

    with pytest.raises(ProofBundleError, match="no longer load"):
        build_bundle(PROJECT_ROOT, tmp_path, generated_at=FIRST_INSTANT, verification=verification)
