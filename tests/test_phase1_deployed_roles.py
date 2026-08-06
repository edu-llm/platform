"""The roles the account actually holds, read from a capture somebody committed.

Every other test of these roles reads a CloudFormation template, which is a claim about
the account rather than a description of it: both roles were created once from a laptop
and neither is redeployed by CI. A capture is what closes that distance, and
``edullm_platform.phase1_capture`` is what a test can ask about one without credentials.

The cases below are in two halves, and the second half is the point.

The first half asks what the committed capture says: that one exists for each role, that
it is inside its window, that it matches the template, and that the publisher role in the
account grants what the template says it grants and nothing else.

"Matches the template" has one recorded exception, and
:class:`~edullm_platform.pending_amendments.PendingAmendment` is it. A template is amended
before the stack is applied, and the stack that creates these roles is applied from a
laptop, so between the two commits the deployed role is genuinely behind the template and
the comparison is genuinely right to say so. What is written down is which difference is
expected, why, and what removes the record — the same three things a ``DEFERRED`` criterion
carries in :mod:`edullm_platform.criteria`. The findings are recorded verbatim and compared
for equality, so a difference the record does not name fails, and so does the day the
amendment is deployed and the recorded findings stop being reported. That second direction
is the point: it is what stops the record outliving the deploy it is waiting for.

**The record used to live in this module and now lives in the library.** While it was here
it reached these cases and nothing else, so ``phase1_capture`` reported the role as
``DRIFTED`` — the verdict a role widened in the console gets — and every consumer
downstream had to re-derive whether the difference was the expected one. One of them did
not: the proof generator treated *any* capture that had stopped holding as a pending
deploy, so an expired capture skipped the same cases an undeployed amendment did.
:mod:`edullm_platform.pending_amendments` is now where the record lives, and the capture
reader gives the state its own verdict, ``PENDING_DEPLOY``.

Naming the state changed nothing about what it costs. The capture still does not hold, and
the proof generator still refuses to build a bundle on it. That is the right split: a
difference somebody has written down and is waiting on is not a reason to stop testing, and
it is also not something a bundle may certify.

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

from edullm_platform.evidence import (
    CAPTURE_SUFFIX,
    FRESHNESS_WINDOW,
    CaptureLoadVerdict,
    scan_for_secrets,
)
from edullm_platform.pending_amendments import (
    PENDING_AMENDMENTS,
    PendingAmendment,
    PendingAmendmentError,
    declared_role_templates,
    pending_for,
)
from edullm_platform.phase1_capture import (
    ROLE_CAPTURE_DIR,
    CaptureVerdict,
    CommittedRoleCapture,
    read_committed_role_captures,
)
from edullm_platform.role_drift import (
    COMMITTED_ROLE_TEMPLATES,
    DriftDirection,
    RoleDriftFinding,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLISHER_ROLE = "sbsandbox-intern-edullm-ecr-publisher"
DEPLOYER_ROLE = "sbsandbox-intern-edullm-infra-deployer"
ONE_SECOND = timedelta(seconds=1)
ONE_MINUTE = timedelta(minutes=1)

#: What criterion 6 says the publisher role must not be able to do. Written as service
#: prefixes, because the claim is about whole services rather than particular calls.
FORBIDDEN_SERVICES = ("batch", "s3", "iam", "ec2", "sts")


# --------------------------------------------------------------------------------------
# The one difference between a template and the account that is allowed to be expected
# --------------------------------------------------------------------------------------


def expected_findings(role_name: str) -> tuple[RoleDriftFinding, ...]:
    """What the comparison must report for this role: nothing, unless one is pending."""
    pending = pending_for(role_name)
    return () if pending is None else pending.findings


def expected_verdict(role_name: str) -> CaptureVerdict:
    return CaptureVerdict.OK if pending_for(role_name) is None else CaptureVerdict.PENDING_DEPLOY


def unexpected(capture: CommittedRoleCapture) -> str:
    """What to print when a capture is not in the state recorded for its role."""
    return capture.detail


def test_a_pending_amendment_must_say_why_and_what_ends_it() -> None:
    # The two fields that distinguish a record somebody is waiting on from an exemption.
    # Checked here rather than trusted, because an entry with neither reads identically
    # to one with both from every consumer's side.
    for amendment in PENDING_AMENDMENTS:
        assert amendment.reason.strip()
        assert amendment.cleared_by.strip()
        assert amendment.findings
        assert amendment.role_name in declared_role_templates()


@pytest.mark.parametrize(
    ("broken", "expected"),
    [
        (
            {"findings": ()},
            "nothing to clear",
        ),
        (
            {"reason": "   "},
            "does not say reason",
        ),
        (
            {"cleared_by": ""},
            "does not say cleared by",
        ),
        (
            {
                "findings": (
                    RoleDriftFinding(
                        direction=DriftDirection.WIDER,
                        element="inline policy 'x'",
                        detail="the deployed role carries an inline policy the template does not",
                    ),
                )
            },
            "not narrower",
        ),
    ],
    ids=["no findings", "no reason", "no trigger", "the account is ahead"],
)
def test_a_pending_amendment_a_reader_could_not_act_on_is_refused(
    broken: dict[str, Any],
    expected: str,
) -> None:
    # The last case is the one worth reading. A pending deploy can only leave the account
    # behind the template; a role granting more than its template is a security finding,
    # and a record able to absorb one would be the most valuable place to hide it.
    fields: dict[str, Any] = {
        "role_name": DEPLOYER_ROLE,
        "reason": "a reason",
        "cleared_by": "a trigger",
        "findings": (
            RoleDriftFinding(
                direction=DriftDirection.NARROWER,
                element="inline policy 'x'",
                detail="the template declares an inline policy the deployed role does not carry",
            ),
        ),
        **broken,
    }

    with pytest.raises(PendingAmendmentError, match=expected):
        PendingAmendment(**fields)


def test_a_pending_amendment_explains_only_the_exact_findings_it_records() -> None:
    # Equality in both directions, which is what makes the record self-clearing. A second
    # difference arriving while this one is open must not read as explained, and neither
    # must a partial deploy that removed only one of the recorded findings.
    #
    # Built here rather than read from PENDING_AMENDMENTS, which is what this case used to
    # do. The registry is empty whenever no amendment is outstanding -- its ordinary state,
    # and the one the Phase 3 deploy restored on 2026-07-27 -- so indexing it made the case
    # that proves the self-clearing rule the one case a clearing broke.
    amendment = PendingAmendment(
        role_name=DEPLOYER_ROLE,
        reason="a template amendment that has not been deployed yet",
        cleared_by="deploying it and re-capturing",
        findings=(
            RoleDriftFinding(
                direction=DriftDirection.NARROWER,
                element="trust policy statement 1 conditions",
                detail="the deployed role does not accept a value the template does",
            ),
            RoleDriftFinding(
                direction=DriftDirection.NARROWER,
                element="inline policy 'deploy-some-stacks'",
                detail="the template declares an inline policy the deployed role does not carry",
            ),
        ),
    )
    extra = RoleDriftFinding(
        direction=DriftDirection.NARROWER,
        element="inline policy 'something-else'",
        detail="the template declares an inline policy the deployed role does not carry",
    )

    assert amendment.explains(amendment.findings)
    assert not amendment.explains(())
    assert not amendment.explains(amendment.findings[:1])
    assert not amendment.explains((*amendment.findings, extra))


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
    absent = [one.role_name for one in captures if one.verdict is CaptureLoadVerdict.ABSENT]

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
        assert capture.verdict is not CaptureLoadVerdict.STALE, capture.detail
        assert capture.expires_at is not None
        assert capture.expires_at > datetime.now(tz=UTC), capture.detail


def test_every_committed_capture_matches_the_template_that_declares_it(
    captures: tuple[CommittedRoleCapture, ...],
) -> None:
    # Equality against what is recorded, rather than against nothing, and equality in
    # both directions. A finding no pending amendment names fails here whichever way it
    # points, and so does a recorded finding the comparison has stopped reporting, which
    # is what a deployed amendment looks like and what forces the record to be deleted.
    for capture in captures:
        assert capture.report is not None, capture.detail
        assert capture.report.findings == expected_findings(capture.role_name), unexpected(capture)
        assert capture.verdict is expected_verdict(capture.role_name), unexpected(capture)
        assert capture.holds == (pending_for(capture.role_name) is None)


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
    ("age", "stale"),
    [
        (FRESHNESS_WINDOW - ONE_MINUTE, False),
        (FRESHNESS_WINDOW + ONE_SECOND, True),
    ],
    ids=["a minute inside the window", "a second outside it"],
)
def test_the_window_is_the_boundary_and_a_second_past_it_is_over(
    tmp_path: Path,
    age: timedelta,
    stale: bool,
) -> None:
    # Probed a second past and a minute short rather than exactly on the boundary: the
    # comparison is against the clock at load time, so an offset of exactly thirty days
    # is over by however long the test took to get there. A minute of slack on the fresh
    # side is what keeps this from failing on a slow machine instead of a real change.
    #
    # Past the window every role reads the same, because the record does not load and
    # nothing else about it is reached. Inside it each role reads as whatever it is: OK
    # with nothing pending, DRIFTED for one carrying a recorded amendment. Naming both
    # per role rather than collapsing them to a set is what keeps this case able to see
    # a role that drifted for a reason nobody recorded.
    verdicts = {capture.role_name: capture.verdict for capture in captures_aged(tmp_path, age)}

    assert verdicts == {
        role_name: CaptureLoadVerdict.STALE if stale else expected_verdict(role_name)
        for role_name, _template in COMMITTED_ROLE_TEMPLATES
    }


def test_an_expired_capture_says_when_it_expired_rather_than_going_quiet(
    tmp_path: Path,
) -> None:
    expired = captures_aged(tmp_path, FRESHNESS_WINDOW + ONE_MINUTE)

    for capture in expired:
        assert not capture.holds
        assert capture.verdict is CaptureLoadVerdict.STALE
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
    # Exactly one widening, and everything else exactly what the open pending amendment on
    # this role already accounts for. Asserting the whole list is `["wider"]` was right only
    # while nothing was pending here, and it would have read as this case breaking on the
    # day somebody amended the template -- which is the ordinary state this role spends time
    # in, since every registration widens it and the stack is applied from a laptop.
    findings = capture.report.findings
    assert [one.direction.value for one in findings if one.direction is DriftDirection.WIDER] == [
        "wider"
    ]
    assert tuple(
        one for one in findings if one.direction is not DriftDirection.WIDER
    ) == expected_findings(PUBLISHER_ROLE)
    assert "ecr:DeleteRepository" in capture.detail


def test_a_second_difference_does_not_hide_behind_a_pending_amendment(tmp_path: Path) -> None:
    # The failure this prevents is the reason PENDING_DEPLOY is a verdict rather than a
    # flag somebody sets. The deployer's committed capture already reports exactly the
    # recorded findings, so a role widened in the console while the amendment is
    # outstanding would arrive as one more finding on a capture already being tolerated.
    # It has to read as ordinary drift, and the tolerating has to stop.
    payload = committed_payload(DEPLOYER_ROLE)
    payload["inline_policies"][0]["statements"][0]["action_match"]["actions"].append(
        "iam:CreateRole"
    )
    write_capture(tmp_path, DEPLOYER_ROLE, payload)

    capture = one(read_committed_role_captures(PROJECT_ROOT, capture_dir=tmp_path), DEPLOYER_ROLE)

    assert capture.verdict is CaptureVerdict.DRIFTED
    assert not capture.holds
    assert capture.report is not None
    assert DriftDirection.WIDER in {finding.direction for finding in capture.report.findings}
    assert "iam:CreateRole" in capture.detail


def test_a_capture_matching_a_pending_amendment_says_what_would_end_it(
    captures: tuple[CommittedRoleCapture, ...],
) -> None:
    # Only meaningful while an amendment is outstanding, and it must not silently become
    # meaningless: a reader who meets this verdict needs the two things the record carries
    # rather than a bare "does not match", which is what the verdict replaced.
    #
    # SCOPED TO THE ROLES THESE CAPTURES COVER, AND IT WAS NOT UNTIL AN AMENDMENT LANDED
    # OUTSIDE THEM. `PENDING_AMENDMENTS` spans all three registries -- that is the whole
    # point of `declared_role_templates` merging them -- while this fixture reads Phase 1's
    # two roles. Held against the unscoped registry, this compared a list of Phase 1
    # captures against a list that may name a Phase 3 role, so the first amendment recorded
    # for one failed here with nothing wrong. It could only ever have held while the
    # registry was empty or happened to contain Phase 1 roles alone, which is what it did
    # contain when it was written.
    #
    # Sorted on both sides rather than compared in registry order. Nothing resolves an
    # amendment by position, and coupling this to the order somebody wrote them in would
    # fail on a reordering that grants nothing.
    examined = {capture.role_name for capture in captures}
    pending = [capture for capture in captures if capture.verdict is CaptureVerdict.PENDING_DEPLOY]

    assert sorted(capture.role_name for capture in pending) == sorted(
        amendment.role_name
        for amendment in PENDING_AMENDMENTS
        if amendment.role_name in examined
    )
    for capture in pending:
        amendment = pending_for(capture.role_name)
        assert amendment is not None
        assert amendment.reason in capture.detail
        assert amendment.cleared_by in capture.detail
        assert not capture.holds


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
        CaptureLoadVerdict.ABSENT
    }


def test_a_file_that_is_not_a_role_capture_reads_as_invalid_rather_than_absent(
    tmp_path: Path,
) -> None:
    # Invalid and absent are different facts. A record that fails its contract means
    # somebody wrote something wrong; nothing there means nobody has looked yet.
    write_capture(tmp_path, PUBLISHER_ROLE, {"role_name": PUBLISHER_ROLE})

    capture = one(read_committed_role_captures(PROJECT_ROOT, capture_dir=tmp_path), PUBLISHER_ROLE)

    assert capture.verdict is CaptureLoadVerdict.INVALID
    assert not capture.holds


def test_a_directory_with_no_captures_reports_every_role_as_absent(tmp_path: Path) -> None:
    captures = read_committed_role_captures(PROJECT_ROOT, capture_dir=tmp_path)

    assert [capture.verdict for capture in captures] == [CaptureLoadVerdict.ABSENT] * len(
        COMMITTED_ROLE_TEMPLATES
    )
    assert all(capture.template_path is not None for capture in captures)
    assert all("tools/capture_phase1_evidence.py" in capture.detail for capture in captures)
