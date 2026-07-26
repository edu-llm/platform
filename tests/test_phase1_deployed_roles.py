"""The roles the account actually holds, read from a capture somebody committed.

Every other test of these roles reads a CloudFormation template, which is a claim about
the account rather than a description of it: both roles were created once from a laptop
and neither is redeployed by CI. A capture is what closes that distance, and
``edullm_platform.phase1_capture`` is what a test can ask about one without credentials.

The cases below are in two halves, and the second half is the point.

The first half asks what the committed capture says: that one exists for each role, that
it is inside its window, that it matches the template, and that the publisher role in the
account grants what the template says it grants and nothing else.

The second half asks what happens when it stops being true. A capture is a statement
about one moment, and every claim resting on it has to expire rather than quietly go on
reading as proof. Expiry is exercised with fixtures whose ``observed_at`` this module
writes, on both sides of the window and a second apart, because waiting thirty days is
not a test. Drift, absence, a record for a role no template declares, and a file that is
not a capture at all are exercised the same way: each has to produce a verdict that does
not hold, rather than an exception or a pass.

Criteria 4 and 5 cite tests from this module, so a capture that expires takes them red.
That is the intended behaviour and ``phase1_criteria`` says so where a reader will meet it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.evidence import FRESHNESS_WINDOW, scan_for_secrets
from edullm_platform.phase1_capture import (
    CAPTURE_SUFFIX,
    ROLE_CAPTURE_DIR,
    CaptureVerdict,
    CommittedRoleCapture,
    read_committed_role_captures,
)
from edullm_platform.role_drift import COMMITTED_ROLE_TEMPLATES

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLISHER_ROLE = "sbsandbox-intern-edullm-ecr-publisher"
DEPLOYER_ROLE = "sbsandbox-intern-edullm-infra-deployer"
ONE_SECOND = timedelta(seconds=1)
ONE_MINUTE = timedelta(minutes=1)

#: What criterion 6 says the publisher role must not be able to do. Written as service
#: prefixes, because the claim is about whole services rather than particular calls.
FORBIDDEN_SERVICES = ("batch", "s3", "iam", "ec2", "sts")


@pytest.fixture(scope="module")
def captures() -> tuple[CommittedRoleCapture, ...]:
    return read_committed_role_captures(PROJECT_ROOT)


def one(captures: tuple[CommittedRoleCapture, ...], role_name: str) -> CommittedRoleCapture:
    return next(capture for capture in captures if capture.role_name == role_name)


def committed_payload(role_name: str) -> dict[str, Any]:
    path = PROJECT_ROOT / ROLE_CAPTURE_DIR / f"{role_name}{CAPTURE_SUFFIX}"
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def write_capture(directory: Path, name: str, payload: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}{CAPTURE_SUFFIX}"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def observed(age: timedelta) -> str:
    return (datetime.now(tz=UTC) - age).isoformat().replace("+00:00", "Z")


def captures_aged(directory: Path, age: timedelta) -> tuple[CommittedRoleCapture, ...]:
    """The committed captures again, as if they had been observed ``age`` ago."""
    for role_name, _template in COMMITTED_ROLE_TEMPLATES:
        payload = committed_payload(role_name)
        payload["observed_at"] = observed(age)
        write_capture(directory, role_name, payload)
    return read_committed_role_captures(PROJECT_ROOT, capture_dir=directory)


# --------------------------------------------------------------------------------------
# What the committed capture says
# --------------------------------------------------------------------------------------


def test_a_capture_is_committed_for_every_role_a_template_declares(
    captures: tuple[CommittedRoleCapture, ...],
) -> None:
    # Read off the template list rather than off the directory: a capture that was
    # deleted has to show up as a role nobody has looked at, not as a shorter list.
    absent = [one.role_name for one in captures if one.verdict is CaptureVerdict.ABSENT]

    assert [capture.role_name for capture in captures] == sorted(
        role_name for role_name, _template in COMMITTED_ROLE_TEMPLATES
    )
    assert absent == []


def test_every_committed_capture_is_inside_its_freshness_window(
    captures: tuple[CommittedRoleCapture, ...],
) -> None:
    # This is the test that expires. When it goes red thirty days after the capture,
    # nothing has gone wrong with the roles: the evidence has stopped being evidence, and
    # criteria 4 and 5 are gaps again until somebody looks at the account.
    for capture in captures:
        assert capture.verdict is not CaptureVerdict.STALE, capture.detail
        assert capture.expires_at is not None
        assert capture.expires_at > datetime.now(tz=UTC), capture.detail


def test_every_committed_capture_matches_the_template_that_declares_it(
    captures: tuple[CommittedRoleCapture, ...],
) -> None:
    for capture in captures:
        assert capture.report is not None, capture.detail
        assert capture.report.findings == (), capture.detail
        assert capture.verdict is CaptureVerdict.OK, capture.detail
        assert capture.holds


def test_the_deployed_publisher_grants_ecr_and_nothing_else(
    captures: tuple[CommittedRoleCapture, ...],
) -> None:
    # The half of criterion 6 a capture can close. Every other test of this role reads
    # the template; this one reads what IAM returned, so the absence of Batch, S3 and IAM
    # actions is a fact about the account rather than about a document.
    evidence = one(captures, PUBLISHER_ROLE).evidence
    assert evidence is not None
    actions = [
        action
        for policy in evidence.inline_policies
        for statement in policy.statements
        for action in statement.action_match.actions
    ]

    assert actions, "a capture with no actions would pass every assertion below"
    assert [action for action in actions if not action.startswith("ecr:")] == []
    assert [action for action in actions if action.split(":", 1)[0] in FORBIDDEN_SERVICES] == []
    assert evidence.attached_managed_policies == ()
    assert evidence.permissions_boundary_policy_name == "InternSandboxBoundary"
    assert evidence.max_session_duration_seconds == 3600
    assert [statement.effect for statement in evidence.trust_statements] == ["Allow"]


def test_no_committed_capture_carries_an_account_id() -> None:
    # The contract refuses one on load, so this is the same claim made against the bytes
    # on disk: what is committed here is reviewable by anybody, not just loadable.
    for role_name, _template in COMMITTED_ROLE_TEMPLATES:
        text = (PROJECT_ROOT / ROLE_CAPTURE_DIR / f"{role_name}{CAPTURE_SUFFIX}").read_text(
            encoding="utf-8"
        )
        assert scan_for_secrets(text) == text, role_name


def test_a_capture_expires_thirty_days_after_it_was_observed(
    captures: tuple[CommittedRoleCapture, ...],
) -> None:
    for capture in captures:
        assert capture.evidence is not None
        assert capture.expires_at == capture.evidence.observed_at + FRESHNESS_WINDOW


# --------------------------------------------------------------------------------------
# What happens when it stops being true
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (FRESHNESS_WINDOW - ONE_MINUTE, CaptureVerdict.OK),
        (FRESHNESS_WINDOW + ONE_SECOND, CaptureVerdict.STALE),
    ],
    ids=["a minute inside the window", "a second outside it"],
)
def test_the_window_is_the_boundary_and_a_second_past_it_is_over(
    tmp_path: Path,
    age: timedelta,
    expected: CaptureVerdict,
) -> None:
    # Probed a second past and a minute short rather than exactly on the boundary: the
    # comparison is against the clock at load time, so an offset of exactly thirty days
    # is over by however long the test took to get there. A minute of slack on the fresh
    # side is what keeps this from failing on a slow machine instead of a real change.
    verdicts = {capture.verdict for capture in captures_aged(tmp_path, age)}

    assert verdicts == {expected}


def test_an_expired_capture_says_when_it_expired_rather_than_going_quiet(
    tmp_path: Path,
) -> None:
    expired = captures_aged(tmp_path, FRESHNESS_WINDOW + ONE_MINUTE)

    for capture in expired:
        assert not capture.holds
        assert capture.verdict is CaptureVerdict.STALE
        assert "tools/capture_phase1_evidence.py" in capture.detail
        # The record did not load, so nothing downstream can read a stale role as a role.
        assert capture.evidence is None
        assert capture.report is None


def test_a_capture_that_no_longer_matches_its_template_does_not_hold(tmp_path: Path) -> None:
    # The comparison is what makes a committed capture worth citing, so a capture that
    # disagrees with the template has to stop the citation rather than be filed beside it.
    payload = committed_payload(PUBLISHER_ROLE)
    payload["inline_policies"][0]["statements"][1]["action_match"]["actions"].append(
        "ecr:DeleteRepository"
    )
    write_capture(tmp_path, PUBLISHER_ROLE, payload)

    capture = one(read_committed_role_captures(PROJECT_ROOT, capture_dir=tmp_path), PUBLISHER_ROLE)

    assert capture.verdict is CaptureVerdict.DRIFTED
    assert not capture.holds
    assert capture.report is not None
    assert [finding.direction.value for finding in capture.report.findings] == ["wider"]
    assert "ecr:DeleteRepository" in capture.detail


def test_a_capture_for_a_role_no_template_declares_is_refused(tmp_path: Path) -> None:
    payload = committed_payload(PUBLISHER_ROLE)
    payload["role_name"] = "sbsandbox-intern-edullm-something-else"
    write_capture(tmp_path, payload["role_name"], payload)

    captures = read_committed_role_captures(PROJECT_ROOT, capture_dir=tmp_path)

    undeclared = one(captures, "sbsandbox-intern-edullm-something-else")
    assert undeclared.verdict is CaptureVerdict.UNDECLARED
    assert undeclared.report is None
    # And the roles that do have templates are still reported as uncaptured.
    assert {capture.verdict for capture in captures if capture is not undeclared} == {
        CaptureVerdict.ABSENT
    }


def test_a_file_that_is_not_a_role_capture_reads_as_invalid_rather_than_absent(
    tmp_path: Path,
) -> None:
    # Invalid and absent are different facts. A record that fails its contract means
    # somebody wrote something wrong; nothing there means nobody has looked yet.
    write_capture(tmp_path, PUBLISHER_ROLE, {"role_name": PUBLISHER_ROLE})

    capture = one(read_committed_role_captures(PROJECT_ROOT, capture_dir=tmp_path), PUBLISHER_ROLE)

    assert capture.verdict is CaptureVerdict.INVALID
    assert not capture.holds


def test_a_directory_with_no_captures_reports_every_role_as_absent(tmp_path: Path) -> None:
    captures = read_committed_role_captures(PROJECT_ROOT, capture_dir=tmp_path)

    assert [capture.verdict for capture in captures] == [CaptureVerdict.ABSENT] * len(
        COMMITTED_ROLE_TEMPLATES
    )
    assert all(capture.template_path is not None for capture in captures)
    assert all("tools/capture_phase1_evidence.py" in capture.detail for capture in captures)
