"""The image-scan gate: what blocks a digest from running, and what lets it through.

This is the answer to the question ``open_decisions.py`` used to carry, now that it has
been answered and moved to where it is enforced. The register's rule is that answering one
means deleting it and putting the answer somewhere enforceable; these tests are what make
"enforceable" true rather than claimed.

Two of them are load-bearing beyond their own assertion.
``test_both_production_callers_evaluate_the_scan_gate`` is the only thing standing between
this gate and a silent opt-out, because ``build_request_facts`` accepts
``image_scan_policy=None`` and reports the fact as reviewed when it gets one -- a fail-open
default that exists for the Phase 0 fixture path and would otherwise be the quiet way to
turn the whole rule off. And
``test_the_shipped_registry_covers_the_only_published_image`` ties the config to reality:
without it, somebody could delete the recorded exception and every test here would still
pass while nothing could run.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path

import pytest

from edullm_platform import admission, submission
from edullm_platform.config import load_yaml
from edullm_platform.contracts.image_scan import (
    ImageScanException,
    ImageScanExceptionRegistry,
    ImageScanPolicy,
    ImageScanSeverity,
    ImageScanStatus,
    ImageScanSummary,
    image_scan_is_reviewed,
    image_scan_summary_from_ecr,
)
from edullm_platform.contracts.policy import (
    ApprovalClass,
    ApprovalPolicy,
    RequestFacts,
    classify_request,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: The digest the platform actually published, and the one the shipped registry excepts.
PUBLISHED_DIGEST = "sha256:4ebdba1ba3b57096efb4f4647ed41ed5ded4ac9e77e8c9038b7ff24db0bc6db8"
OTHER_DIGEST = "sha256:" + "b" * 64

SCANNED_AT = datetime(2026, 7, 26, 22, 5, 49, tzinfo=UTC)


def summary(**counts: int) -> ImageScanSummary:
    return ImageScanSummary(
        schema_version=1,
        status=ImageScanStatus.COMPLETE,
        scanned_at=SCANNED_AT,
        **counts,
    )


def critical_only_policy() -> ImageScanPolicy:
    return ImageScanPolicy(blocking_severities=(ImageScanSeverity.CRITICAL,))


def empty_registry() -> ImageScanExceptionRegistry:
    return ImageScanExceptionRegistry(schema_version=1)


def registry_with(digest: str) -> ImageScanExceptionRegistry:
    return ImageScanExceptionRegistry(
        schema_version=1,
        exceptions=(
            ImageScanException(
                image_digest=digest,
                reason=(
                    "Findings inherited from the pinned base image and unreachable from "
                    "the workload; recorded for this test only."
                ),
                recorded_by="philote",
                recorded_at=SCANNED_AT,
            ),
        ),
    )


def reviewed(
    *,
    digest: str = OTHER_DIGEST,
    scan: ImageScanSummary | None,
    registry: ImageScanExceptionRegistry | None = None,
) -> bool:
    return image_scan_is_reviewed(
        image_digest=digest,
        summary=scan,
        policy=critical_only_policy(),
        registry=registry if registry is not None else empty_registry(),
    )


# ---------------------------------------------------------------------------------------
# What the gate decides
# ---------------------------------------------------------------------------------------


def test_a_clean_scan_needs_no_exception() -> None:
    assert reviewed(scan=summary())


def test_findings_below_the_blocking_severity_do_not_block() -> None:
    """The point of a severity list rather than a total.

    Mutation: block on any finding at all, which would refuse an image with one LOW.
    """
    assert reviewed(scan=summary(high=8, medium=4, low=1))


def test_a_blocking_finding_without_an_exception_is_refused() -> None:
    assert not reviewed(scan=summary(critical=1))


def test_a_blocking_finding_with_a_recorded_exception_runs() -> None:
    assert reviewed(scan=summary(critical=4, high=8), registry=registry_with(OTHER_DIGEST))


def test_an_exception_recorded_against_a_different_digest_does_not_help() -> None:
    """Mutation: match on anything other than the exact digest.

    An exception that covered a repository, a tag, or a prefix would let a rebuilt image
    inherit a review nobody performed on it.
    """
    assert not reviewed(scan=summary(critical=4), registry=registry_with(PUBLISHED_DIGEST))


def test_no_scan_at_all_is_refused_rather_than_assumed_clean() -> None:
    """The fail-closed direction, and the one a careless implementation inverts.

    Mutation: treat a missing summary as no findings. That opens a window in which a
    freshly pushed digest runs because its scan has not finished yet, which is exactly
    when nobody has looked at it.
    """
    assert not reviewed(scan=None)


@pytest.mark.parametrize(
    "status",
    [ImageScanStatus.IN_PROGRESS, ImageScanStatus.FAILED, ImageScanStatus.UNSUPPORTED_IMAGE],
)
def test_a_scan_that_did_not_complete_is_refused(status: ImageScanStatus) -> None:
    """Zero findings and an unfinished scan are the same numbers and opposite facts."""
    unfinished = ImageScanSummary(schema_version=1, status=status, scanned_at=SCANNED_AT)
    assert not reviewed(scan=unfinished)


def test_an_exception_covers_an_image_the_registry_cannot_scan() -> None:
    """A human saying they looked is stronger than a scan result, so it overrides absence."""
    assert reviewed(scan=None, registry=registry_with(OTHER_DIGEST))


# ---------------------------------------------------------------------------------------
# The contracts around it
# ---------------------------------------------------------------------------------------


def test_a_policy_blocking_nothing_is_refused_at_load() -> None:
    """An empty severity list is option one wearing option four's clothes.

    Mutation: allow it. The gate would then be off, and nothing would record that the
    policy had changed.
    """
    with pytest.raises(ValueError):
        ImageScanPolicy(blocking_severities=())


def test_two_exceptions_for_one_digest_are_refused_at_load() -> None:
    """Two reasons for one digest means one of them is not the reason it ran."""
    exception = registry_with(OTHER_DIGEST).exceptions[0]
    with pytest.raises(ValueError):
        ImageScanExceptionRegistry(schema_version=1, exceptions=(exception, exception))


def test_an_exception_needs_a_reason_worth_reading() -> None:
    """Mutation: drop the length floor, and "approved" becomes a valid justification."""
    with pytest.raises(ValueError):
        ImageScanException(
            image_digest=OTHER_DIGEST,
            reason="approved",
            recorded_by="philote",
            recorded_at=SCANNED_AT,
        )


def test_an_unreviewed_image_classifies_as_something_other_than_routine() -> None:
    """The fact reaches classification, not just the denial list.

    Mutation: leave image_scan_reviewed out of classify_request. The denial path would
    still fire, but a caller using classify_request on its own -- which the compile step
    does to pick an environment -- would route an unreviewed image to the lead gate.
    """
    payload = {
        "claimed_team": "memory-split",
        "repository_registered": True,
        "dataset_registered": True,
        "compute_profile_registered": True,
        "immutable_revision": True,
        "immutable_image": True,
        "image_scan_reviewed": True,
        "estimated_cost_usd": "1",
        "maximum_runtime_hours": "1",
        "maximum_attempts": 1,
    }
    thresholds = load_yaml(PROJECT_ROOT / "config" / "policy.yaml", ApprovalPolicy).thresholds
    assert classify_request(RequestFacts.model_validate(payload), thresholds) is ApprovalClass.ROUTINE

    unreviewed = RequestFacts.model_validate({**payload, "image_scan_reviewed": False})
    assert classify_request(unreviewed, thresholds) is ApprovalClass.EXCEPTION


# ---------------------------------------------------------------------------------------
# The ECR adapter
# ---------------------------------------------------------------------------------------


def test_a_describe_result_maps_onto_the_summary() -> None:
    built = image_scan_summary_from_ecr(
        {
            "imageScanStatus": {"status": "COMPLETE"},
            "imageScanFindings": {
                "imageScanCompletedAt": "2026-07-26T22:05:49Z",
                "findingSeverityCounts": {"CRITICAL": 4, "HIGH": 8, "MEDIUM": 4, "LOW": 1},
            },
        }
    )
    assert built is not None
    assert built.critical == 4
    assert built.high == 8
    assert built.total == 17


def test_a_severity_ecr_omits_counts_as_zero() -> None:
    """ECR omits a severity with no findings rather than reporting zero."""
    built = image_scan_summary_from_ecr(
        {
            "imageScanStatus": {"status": "COMPLETE"},
            "imageScanFindings": {
                "imageScanCompletedAt": "2026-07-26T22:05:49Z",
                "findingSeverityCounts": {"LOW": 1},
            },
        }
    )
    assert built is not None
    assert built.critical == 0
    assert built.total == 1


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        "not a mapping",
        {"imageScanStatus": {"status": "COMPLETE"}},
        {"Error": {"Code": "ScanNotFoundException"}},
        {"imageScanStatus": {"status": "COMPLETE"}, "imageScanFindings": {}},
    ],
    ids=["none", "empty", "string", "no-findings-block", "error-shape", "no-timestamp"],
)
def test_an_answer_that_is_not_a_scan_result_reads_as_no_scan(payload: object) -> None:
    """Which fails closed, because image_scan_is_reviewed refuses a None summary."""
    assert image_scan_summary_from_ecr(payload) is None


# ---------------------------------------------------------------------------------------
# The seams
# ---------------------------------------------------------------------------------------


def test_both_production_callers_evaluate_the_scan_gate() -> None:
    """The one test standing between this gate and a silent opt-out.

    ``build_request_facts`` reports the fact as reviewed when ``image_scan_policy`` is
    ``None``, which exists for the Phase 0 fixture path. Both production callers must pass
    the deployed policy. The mutation this catches is either of them dropping the argument:
    the suite would stay green, every submission would classify as reviewed, and the rule
    would be off with nothing recording that it had been turned off.
    """
    for module, function in ((admission, "admit"), (submission, "compile_submission")):
        source = inspect.getsource(getattr(module, function))
        assert "image_scan_policy=policy.image_scan" in source, (
            f"{module.__name__}.{function} no longer passes the deployed image-scan "
            "policy into build_request_facts, so the gate is off for that path"
        )


def test_both_production_callers_take_the_exception_registry() -> None:
    """A registry that cannot be passed in is a registry read from somewhere unreviewed."""
    for module, function in ((admission, "admit"), (submission, "compile_submission")):
        parameters = inspect.signature(getattr(module, function)).parameters
        assert "image_scan_registry" in parameters


def test_the_denial_condition_is_wired_to_the_fact() -> None:
    """A condition named in policy and never checked is silently inert.

    ``admission._CONDITION_FOR_FALSE_FACT`` is the mapping that makes the fact bite. The
    mutation this catches is adding the condition to config/policy.yaml without adding the
    mapping entry, which reads as enforcement and is not.
    """
    assert (
        admission._CONDITION_FOR_FALSE_FACT["image_scan_reviewed"]
        == "image_scan_findings_unreviewed"
    )


# ---------------------------------------------------------------------------------------
# The shipped configuration
# ---------------------------------------------------------------------------------------


def shipped_policy() -> ApprovalPolicy:
    return load_yaml(PROJECT_ROOT / "config" / "policy.yaml", ApprovalPolicy)


def shipped_registry() -> ImageScanExceptionRegistry:
    return load_yaml(
        PROJECT_ROOT / "config" / "image-exceptions.yaml", ImageScanExceptionRegistry
    )


def test_the_shipped_policy_blocks_on_criticals() -> None:
    assert ImageScanSeverity.CRITICAL in shipped_policy().image_scan.blocking_severities


def test_the_shipped_policy_names_the_denial_condition() -> None:
    """Without this the fact is computed, recorded, and never acted on."""
    assert "image_scan_findings_unreviewed" in shipped_policy().denied_outright


def test_the_shipped_registry_covers_the_only_published_image() -> None:
    """Ties the configuration to the account, so deleting the entry fails here.

    The platform has published exactly one image and it carries four criticals. Without a
    recorded exception for that digest nothing can run, and every other test in this file
    would still pass -- which is why this one reads the real config rather than a fixture.
    """
    exception = shipped_registry().exception_for(PUBLISHED_DIGEST)
    assert exception is not None
    assert len(exception.reason) >= 40


def test_the_shipped_registry_excepts_nothing_it_does_not_explain() -> None:
    for exception in shipped_registry().exceptions:
        assert exception.recorded_by
        assert "base" in exception.reason.lower() or "inherited" in exception.reason.lower()
