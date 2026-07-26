"""What one publish run produced, read from the records somebody committed.

Every other Phase 1 test stops at the edge of the AWS call. These read what came back
from one: the digest a pushed commit became, the scan of that digest, the session that
pushed it, the five refusals that session met, and a second push of a different image
under the same tag that the registry turned away.

The cases are in two halves and the second is the point, exactly as in
``test_phase1_deployed_roles.py``.

The first half asks what the committed records say, and asks it in the terms three
acceptance criteria are written in rather than field by field.

The second half asks what happens when they stop being true. Each record is a statement
about one moment, and every claim resting on it has to expire rather than quietly go on
reading as proof. Expiry is exercised with fixtures whose ``observed_at`` this module
writes, on both sides of the window, because waiting thirty days is not a test. The joins
are exercised the same way: a scan of another image, a refusal on another tag, a session
held by another role and a matrix missing an action each have to produce a problem rather
than an exception or a pass.

Criteria 1, 6 and 7 cite tests from this module, so a record that expires takes them red.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.build_tooling import load_registry
from edullm_platform.evidence import (
    FRESHNESS_WINDOW,
    redact_content_digests,
    scan_for_secrets,
)
from edullm_platform.phase1_capture import (
    CAPTURE_SUFFIX,
    RUN_CAPTURE_DIR,
    CommittedRunEvidence,
    read_committed_run_evidence,
)
from edullm_platform.publisher_denials import PUBLISHER_DENIED_ACTIONS
from edullm_platform.role_drift import PUBLISHER_ROLE_NAME

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = load_registry(PROJECT_ROOT / "config" / "repositories.yaml")
REGISTERED = REGISTRY.repository_by_name("OLMo-core")
ONE_SECOND = timedelta(seconds=1)
ONE_MINUTE = timedelta(minutes=1)

#: What criterion 6 says the publisher session must not be able to do, as service
#: prefixes. The matrix attempts one concrete call per claim; this is the claim.
FORBIDDEN_SERVICES = ("batch", "s3", "iam")


@pytest.fixture(scope="module")
def run() -> CommittedRunEvidence:
    return read_committed_run_evidence(PROJECT_ROOT)


def committed_payload(relative: str) -> dict[str, Any]:
    path = PROJECT_ROOT / RUN_CAPTURE_DIR / f"{relative}{CAPTURE_SUFFIX}"
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def copy_committed(directory: Path) -> Path:
    shutil.copytree(PROJECT_ROOT / RUN_CAPTURE_DIR, directory, dirs_exist_ok=True)
    return directory


def write_record(directory: Path, relative: str, payload: dict[str, Any]) -> Path:
    path = directory / f"{relative}{CAPTURE_SUFFIX}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def observed(age: timedelta) -> str:
    return (datetime.now(tz=UTC) - age).isoformat().replace("+00:00", "Z")


def aged(directory: Path, age: timedelta) -> CommittedRunEvidence:
    """The committed records again, as if they had been observed ``age`` ago."""
    copy_committed(directory)
    for path in sorted(directory.rglob(f"*{CAPTURE_SUFFIX}")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["observed_at"] = observed(age)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return read_committed_run_evidence(PROJECT_ROOT, directory=directory)


def reasons(evidence: CommittedRunEvidence) -> set[str]:
    return {problem.reason for problem in evidence.problems}


# --------------------------------------------------------------------------------------
# What the committed records say
# --------------------------------------------------------------------------------------


def test_the_committed_records_of_the_run_all_hold(run: CommittedRunEvidence) -> None:
    assert run.problems == (), [problem.detail for problem in run.problems]
    assert run.holds


def test_a_pushed_branch_commit_produced_a_digest(run: CommittedRunEvidence) -> None:
    # Criterion 1. The tag is the commit's first twelve characters and the contract
    # re-checks that, so the digest beside it belongs to this commit rather than to
    # whatever was last pushed.
    image = run.image
    assert image is not None
    assert image.image_digest.startswith("sha256:")
    assert image.image_tag == image.source_commit_sha[:12]
    assert image.repository_name == REGISTERED.ecr_repository
    # And it was built from the base the platform registered, not from one the Dockerfile
    # chose. A record naming another base would mean the build bypassed the gate.
    assert image.base_image_digest == REGISTERED.base_image_digest
    assert image.image_digest != image.base_image_digest


def test_the_digest_was_pushed_by_a_bounded_publisher_session(run: CommittedRunEvidence) -> None:
    # The other half of criterion 1: a digest exists and a session the platform issued is
    # what put it there. The session is tied to the push by the capture, not by proximity.
    session = run.session
    image = run.image
    assert session is not None
    assert image is not None
    assert session.role_name == PUBLISHER_ROLE_NAME
    assert session.oidc_issuer == "token.actions.githubusercontent.com"
    assert session.oidc_audience == "sts.amazonaws.com"
    assert session.oidc_subject.startswith("repo:")
    assert ":ref:refs/heads/" in session.oidc_subject
    assert session.assumed_at < image.image_pushed_at < session.expires_at


def test_the_publisher_session_was_refused_every_action_the_matrix_attempts(
    run: CommittedRunEvidence,
) -> None:
    # Criterion 6, the half a role capture cannot supply. A policy that grants no Batch
    # action is a policy; this is a session that tried and was told no.
    assert run.denied_actions == PUBLISHER_DENIED_ACTIONS
    for denial in run.denials:
        assert denial.outcome == "denied"
        assert denial.role_name == PUBLISHER_ROLE_NAME
        assert denial.error_code in ("AccessDenied", "AccessDeniedException")
        # Every refusal is a CloudTrail record somebody else can go and read.
        assert denial.event_id


def test_every_service_criterion_six_names_was_refused(run: CommittedRunEvidence) -> None:
    refused = {denial.attempted_action.split(":", 1)[0] for denial in run.denials}

    assert set(FORBIDDEN_SERVICES) <= refused


def test_an_immutable_tag_was_not_overwritten_and_the_original_digest_survived(
    run: CommittedRunEvidence,
) -> None:
    # Criterion 7. The repository setting and the refusal are two different claims and
    # both are here; only the second is what the criterion asserts.
    refusal = run.refusal
    repository = run.repository
    image = run.image
    assert refusal is not None
    assert repository is not None
    assert image is not None
    assert repository.image_tag_mutability == "IMMUTABLE"
    assert refusal.outcome == "refused"
    assert refusal.error_code == "ImageTagAlreadyExistsException"
    assert refusal.image_tag == image.image_tag
    assert refusal.image_digest == image.image_digest
    assert refusal.attempted_at > image.image_pushed_at


def test_the_scan_that_ran_on_push_is_the_scan_of_this_image(run: CommittedRunEvidence) -> None:
    scan = run.scan
    image = run.image
    assert scan is not None
    assert image is not None
    assert scan.image_digest == image.image_digest
    assert scan.scan_reported_findings
    assert scan.finding_counts is not None
    # Recorded, and deliberately not asserted against a threshold. Whether a scan result
    # may block a publish is an open decision; see edullm_platform.open_decisions.
    assert scan.finding_counts.total >= 0


def test_no_committed_run_record_carries_an_account_id() -> None:
    # The contracts refuse one on load, so this is the same claim made against the bytes
    # on disk: what is committed here is reviewable by anybody, not just loadable. Only
    # content digests are masked first, and only because the generic credential patterns
    # cannot tell a sha256 digest from a secret; the contracts pin those two fields by
    # exact pattern instead, which is the stricter check.
    for path in sorted((PROJECT_ROOT / RUN_CAPTURE_DIR).rglob(f"*{CAPTURE_SUFFIX}")):
        masked = redact_content_digests(path.read_text(encoding="utf-8"))
        assert scan_for_secrets(masked) == masked, path.name


def test_no_committed_run_record_names_a_person() -> None:
    # The second push was made from a laptop under somebody's personal sandbox role. The
    # record has to be able to say the publisher role was not the one refused without
    # publishing whose role it was.
    refusal = committed_payload("immutable-tag-refusal")

    assert refusal["attempted_by_publisher_role"] is False
    assert refusal["attempted_by"].startswith("<")


# --------------------------------------------------------------------------------------
# What happens when they stop being true
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age", "expected"),
    [(FRESHNESS_WINDOW - ONE_MINUTE, True), (FRESHNESS_WINDOW + ONE_SECOND, False)],
    ids=["a minute inside the window", "a second outside it"],
)
def test_the_window_is_the_boundary_and_a_second_past_it_is_over(
    tmp_path: Path,
    age: timedelta,
    expected: bool,
) -> None:
    # Probed a second past and a minute short rather than exactly on the boundary: the
    # comparison is against the clock at load time, so an offset of exactly thirty days
    # is over by however long the test took to get there.
    assert aged(tmp_path / "run", age).holds is expected


def test_an_expired_run_record_says_what_to_do_rather_than_going_quiet(tmp_path: Path) -> None:
    expired = aged(tmp_path / "run", FRESHNESS_WINDOW + ONE_MINUTE)

    assert reasons(expired) == {"evidence_stale"}
    assert expired.image is None
    assert expired.denials == ()
    for problem in expired.problems:
        assert "tools/capture_phase1_evidence.py" in problem.detail
        # The run does not need repeating and the guidance has to say so, or somebody
        # will read an expiry as a reason to publish another image.
        assert "does not need repeating" in problem.detail


def test_a_scan_of_another_image_does_not_hold(tmp_path: Path) -> None:
    directory = copy_committed(tmp_path / "run")
    payload = committed_payload("image-scan")
    payload["image_digest"] = "sha256:" + "99" * 32
    write_record(directory, "image-scan", payload)

    evidence = read_committed_run_evidence(PROJECT_ROOT, directory=directory)

    assert not evidence.holds
    assert reasons(evidence) == {"record_describes_another_image"}


def test_a_refusal_on_another_tag_does_not_hold(tmp_path: Path) -> None:
    # A refusal is only evidence about this image if it is a refusal to overwrite this
    # image's tag. Any other tag is a true fact about a different one.
    directory = copy_committed(tmp_path / "run")
    other_commit = "b" * 40
    payload = committed_payload("immutable-tag-refusal")
    payload["source_commit_sha"] = other_commit
    payload["image_tag"] = other_commit[:12]
    write_record(directory, "immutable-tag-refusal", payload)

    evidence = read_committed_run_evidence(PROJECT_ROOT, directory=directory)

    assert not evidence.holds
    assert reasons(evidence) == {"record_describes_another_image"}


def test_a_session_held_by_another_role_does_not_hold(tmp_path: Path) -> None:
    directory = copy_committed(tmp_path / "run")
    payload = committed_payload("publisher-session")
    payload["role_name"] = "sbsandbox-intern-edullm-infra-deployer"
    write_record(directory, "publisher-session", payload)

    evidence = read_committed_run_evidence(PROJECT_ROOT, directory=directory)

    assert not evidence.holds
    assert reasons(evidence) == {"session_is_not_the_publisher_role"}


def test_a_matrix_missing_one_refusal_does_not_hold(tmp_path: Path) -> None:
    # Four refusals prove the criterion for four actions. A set of four read later would
    # look like a run that was refused all five.
    directory = copy_committed(tmp_path / "run")
    (directory / "denials" / f"s3-ListAllMyBuckets{CAPTURE_SUFFIX}").unlink()

    evidence = read_committed_run_evidence(PROJECT_ROOT, directory=directory)

    assert not evidence.holds
    assert reasons(evidence) == {"capture_absent", "denial_matrix_incomplete"}
    assert len(evidence.denials) == len(PUBLISHER_DENIED_ACTIONS) - 1


def test_a_directory_with_no_records_reports_every_one_as_absent(tmp_path: Path) -> None:
    empty = tmp_path / "nothing"
    empty.mkdir()

    evidence = read_committed_run_evidence(PROJECT_ROOT, directory=empty)

    assert not evidence.holds
    assert reasons(evidence) == {"capture_absent"}
    assert evidence.image is None
    # With no image there is nothing for the joins to be against, so they say nothing
    # rather than adding five more failures that all mean "there is no image".
    assert "record_describes_another_image" not in reasons(evidence)


def test_a_file_that_is_not_a_run_record_reads_as_invalid_rather_than_absent(
    tmp_path: Path,
) -> None:
    directory = copy_committed(tmp_path / "run")
    write_record(directory, "ecr-image", {"image_digest": "sha256:" + "1a" * 32})

    evidence = read_committed_run_evidence(PROJECT_ROOT, directory=directory)

    assert not evidence.holds
    assert "evidence_invalid" in reasons(evidence)
    assert evidence.image is None
