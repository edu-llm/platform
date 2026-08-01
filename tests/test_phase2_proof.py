"""The Phase 2 proof bundle, and the failures a bundle for a failing phase is prone to.

A bundle exists so a reviewer can decide whether the phase is done without reading the
suite. Phase 2's gate exits 1, so the failures worth testing are the ones that would leave
that reader believing otherwise: prose that gives a criterion a status the gate did not
reach, an index that does not name the open checks, an empty document quietly disappearing,
and a recorded role digest re-recorded after the template it describes was widened.

There is one more that is particular to this phase and does not arise in Phase 1 or Phase
3. Ten criteria are covered on committed captures of a live account, and those captures
expire. A bundle built after they lapse would print ten covered checks the gate reports
as failing, so the generator refuses instead, and that refusal is tested here in the three
ways a capture can stop holding -- for every capture the generator reads, rather than for
one of them, because the defect this caught was a committed capture that no test noticed
was never being loaded.

This module builds bundles, so it is listed in ``REENTRANT_TEST_MODULES`` and no criterion
may cite it. It is also excluded from the verification run inside every generator, which is
why building a bundle here does not recurse.
"""

from __future__ import annotations

import base64
import json
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.admission_denials import ADMISSION_DENIED_ACTIONS, ADMISSION_PROBE_LESSONS
from edullm_platform.criteria import CriterionSpec, CriterionStatus
from edullm_platform.evidence import FRESHNESS_WINDOW
from edullm_platform.open_decisions import open_decisions
from edullm_platform.phase2_evidence import PHASE2_ROLE_TEMPLATES
from edullm_platform.proof_bundle import (
    GoldenDigestDriftError,
    MissingTestNodeError,
    ProofBundleError,
    contradicting_status_claims,
    load_recorded_goldens,
)
from tests.proof_support import skip_unless_reproducing
from tools.build_phase2_proof import (
    BUNDLE_FILENAMES,
    CAPTURE_SOURCES,
    EMPTY_SECTIONS,
    EVIDENCE_DIR,
    GENERATOR_TEST_PATH,
    NESTED_RUN_ENV,
    Coherence,
    Verification,
    build_bundle,
    compute_goldens,
    establish_coherence,
    goldens_path,
    hex_checksum,
    known_limitations,
    main,
    phase2_criteria,
    read_captures,
    verify_repository,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIRST_INSTANT = datetime(2026, 1, 1, tzinfo=UTC)
SECOND_INSTANT = datetime(2026, 6, 30, 12, 34, 56, tzinfo=UTC)
GENERATED_AT_PREFIX = "Generated: "

#: Read off the generator's own source list rather than repeated here. A path written out
#: in this module is a second list that can fall behind the first, which is the shape of
#: the defect that made the lead-team capture invisible: it was committed and measured in
#: the digest table, and the one list that decides whether a capture is loaded at all did
#: not have it.
CAPTURE_PATHS = tuple(path for path, _model in CAPTURE_SOURCES)


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


def shipped_checks() -> tuple[CriterionSpec, ...]:
    return phase2_criteria()


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
    # This bundle quotes S3 version ids, object checksums, IAM policy resources and GitHub
    # logins, which is where a credential-shaped token or an account id would arrive.
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


@pytest.mark.parametrize(
    ("prose", "expected"),
    [
        ("Check 13 remains a gap.", "gap"),
        ("Criterion 17 remains a gap.", "gap"),
        ("Check 7 is covered.", "covered"),
    ],
    ids=["covered called a gap", "another covered called a gap", "gap called covered"],
)
def test_a_sentence_that_disagrees_with_the_definition_is_caught(
    prose: str,
    expected: str,
) -> None:
    problems = contradicting_status_claims({"README.md": prose}, shipped_checks())

    assert len(problems) == 1
    assert expected in problems[0]


@pytest.mark.slow
def test_the_generator_refuses_to_write_a_bundle_whose_prose_contradicts_the_gate(
    tmp_path: Path,
    verification: Verification,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The one defect a bundle cannot survive. A reviewer who trusts it without reading the
    # suite would come away believing a gap was closed -- which in this phase would mean
    # believing somebody had captured a run that nobody captured.
    monkeypatch.setattr(
        "tools.build_phase2_proof.known_limitations",
        lambda checks, evidence, goldens: ("Check 7 is covered.",),
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
    # The shape the Phase 1 gate note went stale in: no check is named, so the numbered
    # reader sees nothing, and the sentence is still a status claim about this phase. With
    # eight gaps this is the easiest sentence in the bundle to get wrong by one, and the
    # sentence below is what getting it wrong by one looks like.
    monkeypatch.setattr(
        "tools.build_phase2_proof.known_limitations",
        lambda checks, evidence, goldens: ("Nine criteria are gaps today.",),
    )

    with pytest.raises(ProofBundleError, match="nine criteria are gap"):
        build_bundle(PROJECT_ROOT, tmp_path, generated_at=FIRST_INSTANT, verification=verification)

    assert not (tmp_path / "README.md").exists()


def test_a_limitation_that_names_a_check_takes_its_status_from_the_definition() -> None:
    # The limitations are the one place prose and status meet, so the status word is read
    # off the checks rather than typed. This is what makes the guard above never fire.
    limitations = known_limitations(
        shipped_checks(), read_captures(PROJECT_ROOT), compute_goldens(PROJECT_ROOT)
    )

    assert any("check 7" in text.lower() and "is a gap" in text.lower() for text in limitations)
    assert any("check 21" in text.lower() and "covered" in text.lower() for text in limitations)


def test_a_limitation_naming_a_check_the_phase_does_not_have_is_refused() -> None:
    with pytest.raises(ProofBundleError, match="does not record"):
        known_limitations(
            shipped_checks()[:0], read_captures(PROJECT_ROOT), compute_goldens(PROJECT_ROOT)
        )


# --------------------------------------------------------------------------------------
# The index has to say, on its first screen, that the phase is not accepted
# --------------------------------------------------------------------------------------


@pytest.mark.slow
def test_the_index_reports_the_verdict_the_gate_reaches(first_bundle: dict[str, str]) -> None:
    gaps = [check.number for check in shipped_checks() if check.status is CriterionStatus.GAP]
    index = first_bundle["README.md"]

    if gaps:
        assert (
            "`tools/validate_phase2.py` exits 1 against this tree. Phase 2 is not accepted: "
            f"criteria {', '.join(gaps)} are GAPs." in index
        )
    else:
        assert "`tools/validate_phase2.py` exits 0 against this tree" in index


@pytest.mark.slow
def test_the_index_names_every_open_check_before_it_names_anything_else(
    first_bundle: dict[str, str],
) -> None:
    """Mutation: report the count and leave a reader to go and find which ones.

    The index opens with the verdict, the derived summary sentence and one row per open
    check, all of them above the contents list. A reviewer who reads nothing else has to
    come away knowing the phase is not accepted and which statements are unsatisfied.
    """
    index = first_bundle["README.md"]
    gaps = [check for check in shipped_checks() if check.status is CriterionStatus.GAP]

    assert index.index("## Read this first") < index.index("## Contents")
    for check in gaps:
        row = f"| {check.number} | {check.statement} |"
        assert row in index, row
        assert index.index(row) < index.index("## Contents")


@pytest.mark.slow
def test_the_index_summarises_the_criteria_from_what_was_computed(
    first_bundle: dict[str, str],
) -> None:
    # The same derived sentence the gate emits beside its own verdict, so the two cannot
    # disagree, and read back by the guard above so it cannot go stale.
    from edullm_platform.status_prose import status_summary_sentence

    assert status_summary_sentence(shipped_checks()) in first_bundle["README.md"]


@pytest.mark.slow
def test_the_index_does_not_open_by_calling_a_finished_phase_unfinished(
    first_bundle: dict[str, str],
) -> None:
    gaps = [check.number for check in shipped_checks() if check.status is CriterionStatus.GAP]
    opening = first_bundle["README.md"]

    if gaps:
        assert "It is not done" in opening
    else:
        assert "It is not done" not in opening
        assert "Every criterion is covered and the gate is green" in opening


@pytest.mark.slow
def test_the_index_counts_what_was_never_captured_rather_than_leaving_it_out(
    first_bundle: dict[str, str],
) -> None:
    # A zero somebody wrote down is a claim; an absent row is an oversight. These three
    # are the ones a reader would assume were non-zero, because the runs behind them all
    # happened.
    index = first_bundle["README.md"]

    assert "| denial matrices captured | 0 |" in index
    assert "| CloudTrail records captured | 0 |" in index
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
# What an empty document has to say about being empty
# --------------------------------------------------------------------------------------


@pytest.mark.slow
def test_every_empty_document_is_written_rather_than_omitted(
    first_bundle: dict[str, str],
) -> None:
    """Mutation: skip a section that has nothing in it.

    A document omitted because there was nothing to put in it makes the phase look like it
    has fewer claims than it has, and nobody counts what is not there.
    """
    assert EMPTY_SECTIONS
    for section in EMPTY_SECTIONS:
        assert section.filename in first_bundle, section.filename
        assert section.filename in BUNDLE_FILENAMES


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
        assert "happened, and nothing captured it" in text
        assert section.records in text
        assert section.filled_by
        for filler in section.filled_by:
            assert filler in text


@pytest.mark.slow
def test_no_empty_document_reads_as_though_its_evidence_had_been_captured(
    first_bundle: dict[str, str],
) -> None:
    """The subtler of the two failures a mostly-absent document is prone to.

    A section describing what it *will* record, written in the present tense, reads at a
    skim exactly like one describing what was recorded. Every criterion these sections
    serve is open today, so the status column in each must say so -- read off the
    definition, so it cannot drift from the gate.
    """
    numbers = {check.number for check in shipped_checks()}

    for section in EMPTY_SECTIONS:
        text = first_bundle[section.filename]
        assert section.closes
        assert set(section.closes) <= numbers, section.filename
        for number in section.closes:
            (check,) = [one for one in shipped_checks() if one.number == number]
            assert check.status is CriterionStatus.GAP, (
                f"{section.filename} names check {number}, which is no longer a gap; the "
                "section is either no longer empty or no longer honest"
            )
            assert f"| {number} | a gap |" in text


# --------------------------------------------------------------------------------------
# The committed captures, and the refusal when one stops holding
# --------------------------------------------------------------------------------------


def long_ago() -> str:
    """An observation timestamp far enough past the window to be stale on any machine."""
    return (datetime.now(tz=UTC) - FRESHNESS_WINDOW - timedelta(days=1)).isoformat()


def expire(payload: dict[str, Any]) -> None:
    payload["observed_at"] = long_ago()


def empty_it(payload: dict[str, Any]) -> None:
    payload.clear()


def drop_a_required_field(payload: dict[str, Any]) -> None:
    """Remove ``source``, which is the one field every capture model declares.

    A mutation aimed at one capture's own shape -- dropping the environments' reviewer
    lists, which is what this replaced -- is a no-op against the other four, so a test
    parametrized over it would report the captures it did nothing to as guarded.
    """
    payload.pop("source", None)


@pytest.mark.slow
@pytest.mark.parametrize(
    "relative_path",
    [path for path, _model in CAPTURE_SOURCES],
    ids=[Path(path).name for path, _model in CAPTURE_SOURCES],
)
@pytest.mark.parametrize(
    "break_the_capture",
    [expire, empty_it, drop_a_required_field],
    ids=["expired", "unreadable", "missing a field"],
)
def test_the_generator_refuses_to_build_on_a_capture_that_no_longer_holds(
    tmp_path: Path,
    verification: Verification,
    monkeypatch: pytest.MonkeyPatch,
    break_the_capture: Callable[[dict[str, Any]], None],
    relative_path: str,
) -> None:
    """Every capture the generator declares, not the first one somebody thought of.

    The matrix prints the status the definition records, and ten criteria are covered on
    the strength of these captures. Once one stops holding the gate disagrees with that,
    and writing the bundle anyway would hand a reviewer a document whose prose the gate
    contradicts.

    Parametrized over ``CAPTURE_SOURCES`` because that is the list a sixth capture would
    have to join, and the fifth one did not: ``lead-team.sanitized.json`` was committed,
    cited by two criteria and never loaded, and a guard aimed at the environments capture
    alone had nothing to say about it. The whole evidence directory is copied and exactly
    one file is broken, so the refusal has to name the file under test rather than trip
    over a capture that is merely absent.
    """
    broken = tmp_path / "tree"
    shutil.copytree(PROJECT_ROOT / EVIDENCE_DIR, broken / EVIDENCE_DIR)
    payload: dict[str, Any] = json.loads((broken / relative_path).read_text(encoding="utf-8"))
    break_the_capture(payload)
    (broken / relative_path).write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        "tools.build_phase2_proof.read_captures",
        lambda repo_root: read_captures(broken),
    )

    with pytest.raises(ProofBundleError, match="no longer loads") as raised:
        build_bundle(
            PROJECT_ROOT,
            tmp_path / "bundle",
            generated_at=FIRST_INSTANT,
            verification=verification,
        )

    assert relative_path in str(raised.value)
    assert not (tmp_path / "bundle" / "README.md").exists()


@pytest.mark.slow
def test_the_refusal_says_what_to_do_about_it(
    tmp_path: Path,
    verification: Verification,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A refusal an operator cannot act on is a wall. Both honest responses are named,
    # including the one that removes the citations rather than renewing them.
    monkeypatch.setattr(
        "tools.build_phase2_proof.read_captures",
        lambda repo_root: read_captures(tmp_path / "empty"),
    )
    (tmp_path / "empty").mkdir()

    with pytest.raises(ProofBundleError) as raised:
        build_bundle(
            PROJECT_ROOT,
            tmp_path / "bundle",
            generated_at=FIRST_INSTANT,
            verification=verification,
        )

    assert "tools/capture_phase2_evidence.py" in str(raised.value)
    assert "phase2_criteria.py" in str(raised.value)


@pytest.mark.slow
def test_the_lineage_document_reports_every_captured_object_and_when_it_expires(
    first_bundle: dict[str, str],
) -> None:
    document = first_bundle["lineage-record-evidence.md"]
    evidence = read_captures(PROJECT_ROOT)

    assert evidence.lineage.objects
    for stored in evidence.lineage.objects:
        assert stored.key in document
        assert stored.version_id in document
    assert evidence.expires_on in document


def test_the_checksum_is_re_encoded_rather_than_masked() -> None:
    """The base64 S3 returns is the shape the secret scan refuses, and it is not a secret.

    Rewriting it as hex keeps every one of the thirty-two bytes and makes it the same
    spelling as every other digest here. Masking it would throw away the field a reviewer
    needs in order to check an object; widening the scanner to admit forty-four-character
    base64 runs would weaken the check everywhere to admit one field.
    """
    evidence = read_captures(PROJECT_ROOT)
    stored = evidence.lineage.objects[0]

    rewritten = hex_checksum(stored.checksum_sha256)

    assert rewritten.startswith("sha256:")
    assert base64.b64encode(bytes.fromhex(rewritten.removeprefix("sha256:"))).decode() == (
        stored.checksum_sha256
    )


def test_a_checksum_that_is_not_a_sha256_is_refused() -> None:
    with pytest.raises(ProofBundleError, match="thirty-two bytes"):
        hex_checksum(base64.b64encode(b"too short").decode())


@pytest.mark.slow
def test_the_execution_document_accounts_for_every_captured_execution(
    first_bundle: dict[str, str],
) -> None:
    document = first_bundle["admission-execution-evidence.md"]
    evidence = read_captures(PROJECT_ROOT)

    assert evidence.executions.executions
    for execution in evidence.executions.executions:
        assert execution.name in document
    assert "AdmissionRejected" in document


@pytest.mark.slow
def test_the_approval_gate_document_lists_every_environment_the_capture_found(
    first_bundle: dict[str, str],
) -> None:
    # Every environment, not only the two expected. An unprotected third one auto-created
    # by naming it in a workflow file is exactly what a two-name reader would miss.
    document = first_bundle["approval-gate-evidence.md"]
    evidence = read_captures(PROJECT_ROOT)

    for environment in evidence.environments.environments:
        assert environment.name in document
        for reviewer in environment.reviewers:
            assert reviewer.name in document


@pytest.mark.slow
def test_the_secret_document_records_names_and_could_not_record_a_value(
    first_bundle: dict[str, str],
) -> None:
    document = first_bundle["approval-gate-evidence.md"]
    evidence = read_captures(PROJECT_ROOT)

    assert evidence.secrets.repository_secret_names == ()
    assert "| repository secrets | none |" in document
    for name in evidence.secrets.repository_variable_names:
        assert name in document


# --------------------------------------------------------------------------------------
# The authorization matrix and the denial matrix
# --------------------------------------------------------------------------------------


@pytest.mark.slow
def test_the_authorization_matrix_is_evaluated_rather_than_transcribed(
    first_bundle: dict[str, str],
) -> None:
    """Mutation: write the outcomes into the document by hand.

    Every committed scenario carries the outcome it expects, and the document compares
    that expectation to what ``evaluate_authorization`` returned while the bundle was being
    written. A transcribed table would agree with the fixtures forever, including after the
    function stopped agreeing with them.
    """
    document = first_bundle["authorization-matrix.md"]

    assert "matches its recorded expectation" in document
    assert "**no**" not in document
    for reason in (
        "routine_self_authorized",
        "routine_approved_by_lead_or_admin",
        "exception_approved_by_admin",
        "self_approval_not_permitted_for_member",
        "approver_lacks_lead_or_admin_role",
        "approver_lacks_admin_role",
        "approver_not_in_roster",
        "submitter_not_in_roster",
    ):
        assert reason in document, reason


@pytest.mark.slow
def test_the_authorization_matrix_shows_the_deferral_rather_than_hiding_it(
    first_bundle: dict[str, str],
) -> None:
    # Criterion 4 is deferred because team membership is unverifiable, and what keeps that
    # visible is that every row reports team_verified false rather than the matrix simply
    # omitting the case.
    document = first_bundle["authorization-matrix.md"]

    assert "attributing the run to another team" in document
    assert "team verified" in document
    assert "team_bindings.teams" in document


@pytest.mark.slow
def test_the_denial_document_names_every_probe_and_says_none_was_captured(
    first_bundle: dict[str, str],
) -> None:
    document = first_bundle["admission-denial-matrix.md"]

    for action in ADMISSION_DENIED_ACTIONS:
        assert action in document
    for lesson in ADMISSION_PROBE_LESSONS:
        assert lesson.rule in document
        assert lesson.learned_from in document
    assert "It ran, and nothing captured it" in document


@pytest.mark.slow
def test_the_open_decisions_document_records_the_questions_without_answering_them(
    first_bundle: dict[str, str],
) -> None:
    document = first_bundle["open-decisions.md"]

    for decision in open_decisions():
        assert decision.question in document
        assert decision.lands_in in document
        for option in decision.options:
            assert option in document


# --------------------------------------------------------------------------------------
# Golden digests over what a role template grants
# --------------------------------------------------------------------------------------


def test_the_recorded_goldens_cover_every_phase_two_role() -> None:
    goldens = compute_goldens(PROJECT_ROOT)
    recorded = load_recorded_goldens(goldens_path(PROJECT_ROOT / "proof" / "phase-2"))

    assert {record.fixture for record in goldens} == {
        role_name for role_name, _template in PHASE2_ROLE_TEMPLATES
    }
    assert {record.fixture for record in recorded} == {record.fixture for record in goldens}
    assert {record.digest for record in recorded} == {record.digest for record in goldens}


@pytest.mark.slow
def test_a_widened_template_refuses_the_build_rather_than_being_re_recorded(
    tmp_path: Path,
    verification: Verification,
) -> None:
    # The digest is over what the role grants, so a drift here is a template that gained
    # an action. It matters more than usual for these three: all of them are deployed and
    # none is captured, so nothing else in this repository would notice.
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
        json.dumps({"fixtures": [], "phase": "phase-2", "schema_version": 1}) + "\n"
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
        node_id.startswith("tests/test_phase2_criteria.py")
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
def test_the_index_measures_every_committed_capture(first_bundle: dict[str, str]) -> None:
    # Read off the tree rather than listed, so a record added to the evidence directory is
    # measured without a second edit to the generator.
    index = first_bundle["README.md"]
    committed = sorted(
        str(path.relative_to(PROJECT_ROOT))
        for path in (PROJECT_ROOT / "fixtures" / "evidence" / "phase-2").rglob("*.json")
    )

    assert committed
    for path in committed:
        assert path in index
    for path in CAPTURE_PATHS:
        assert path in index
