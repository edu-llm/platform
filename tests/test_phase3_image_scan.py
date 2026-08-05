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

from edullm_platform import admission, admission_handler, submission
from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import ApprovalEnvironment
from edullm_platform.contracts.image_scan import (
    ImageScanException,
    ImageScanExceptionRegistry,
    ImageScanPolicy,
    ImageScanSeverity,
    ImageScanStatus,
    ImageScanSummary,
    ImageScanVerdict,
    ReviewedVulnerability,
    ScanFinding,
    blocking_findings_from_ecr,
    image_scan_is_reviewed,
    image_scan_summary_from_ecr,
    image_scan_verdict,
    unreviewed_blocking_findings,
)
from edullm_platform.contracts.policy import (
    ApprovalClass,
    ApprovalPolicy,
    RequestFacts,
    classify_request,
)
from tests.policy_support import ROUTINE_RATE

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
    findings: tuple[ScanFinding, ...] | None = None,
) -> bool:
    return image_scan_is_reviewed(
        image_digest=digest,
        summary=scan,
        policy=critical_only_policy(),
        registry=registry if registry is not None else empty_registry(),
        blocking_findings=findings,
    )


#: The four the shipped base actually carries, which is what makes them the worked example.
PERL_CVES = ("CVE-2026-57433", "CVE-2026-12087", "CVE-2026-13221")
GLIBC_CVE = "CVE-2026-5450"


def finding(vulnerability_id: str, package_name: str = "perl") -> ScanFinding:
    return ScanFinding(vulnerability_id=vulnerability_id, package_name=package_name)


def review(vulnerability_id: str, package_name: str = "perl") -> ReviewedVulnerability:
    return ReviewedVulnerability(
        vulnerability_id=vulnerability_id,
        package_name=package_name,
        reason=(
            "Inherited from the digest-pinned base, unfixable from this repository, and in "
            "a package the training entrypoint never invokes. Recorded for this test only."
        ),
        recorded_by="philote",
        recorded_at=SCANNED_AT,
    )


def registry_reviewing(*reviews: ReviewedVulnerability) -> ImageScanExceptionRegistry:
    return ImageScanExceptionRegistry(schema_version=1, reviewed_vulnerabilities=reviews)


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


# ---------------------------------------------------------------------------------------
# Reviewing the vulnerability rather than the image
# ---------------------------------------------------------------------------------------


def test_an_image_carrying_only_reviewed_vulnerabilities_runs_without_an_exception() -> None:
    """THE CASE THE WHOLE CHANGE EXISTS FOR, AND WHY IT IS NOT A LOOSENING.

    Every image this platform builds inherits the same four criticals from the shared
    Debian base -- three in perl, one in glibc -- which are unfixable from this repository
    and for which no patched base exists upstream. Under a per-digest exception the only
    way to run any of them is a reviewed pull request naming that exact image, so a
    researcher cannot iterate without an admin, which is the friction this phase removed
    from the image and would otherwise reintroduce at the scan.

    What a reviewer actually did when they wrote those exceptions was read four CVEs and
    accept them. This lets the sign-off say that. Nothing is waved through: the review is
    still a human, a reason and a date, and it still has to exist before the run.
    """
    findings = tuple(finding(cve) for cve in PERL_CVES) + (finding(GLIBC_CVE, "glibc"),)
    registry = registry_reviewing(
        *(review(cve) for cve in PERL_CVES), review(GLIBC_CVE, "glibc")
    )

    assert reviewed(scan=summary(critical=4, high=8), registry=registry, findings=findings)


def test_one_unreviewed_critical_among_reviewed_ones_still_refuses() -> None:
    """Mutation: pass when *any* finding is reviewed rather than when all are.

    This is the property that keeps the gate worth having. A base whose findings are all
    reviewed says nothing about a critical the project itself pulled in, and the whole point
    of reviewing vulnerabilities rather than images is that a new one still stops the run.
    """
    findings = tuple(finding(cve) for cve in PERL_CVES) + (finding("CVE-2026-99999", "zlib"),)
    registry = registry_reviewing(*(review(cve) for cve in PERL_CVES))

    assert not reviewed(scan=summary(critical=4), registry=registry, findings=findings)


def test_the_unreviewed_ones_are_nameable_so_a_refusal_can_say_which() -> None:
    """A refusal that says "unreviewed scan findings" sends somebody to look at everything.

    The gate answers yes or no; this is what lets the message name the one that stopped it.
    Separate rather than folded into the gate, because a boolean is the right shape for a
    decision and a list is the right shape for a message.
    """
    unknown = finding("CVE-2026-99999", "zlib")
    findings = tuple(finding(cve) for cve in PERL_CVES) + (unknown,)
    registry = registry_reviewing(*(review(cve) for cve in PERL_CVES))

    assert unreviewed_blocking_findings(blocking_findings=findings, registry=registry) == (
        unknown,
    )


def test_a_review_of_one_package_does_not_cover_the_same_id_in_another() -> None:
    """Mutation: match on the vulnerability id alone.

    A review is a statement about a vulnerability *in a package we ship* -- that it is
    unreachable from the entrypoint, or unfixable, or both. The same identifier in a
    different package is a different reachability question that nobody answered.
    """
    registry = registry_reviewing(review(GLIBC_CVE, "glibc"))

    assert not reviewed(
        scan=summary(critical=1), registry=registry, findings=(finding(GLIBC_CVE, "perl"),)
    )


def test_a_summary_saying_there_are_criticals_with_no_findings_supplied_is_refused() -> None:
    """THE VACUOUS PASS, AND IT IS THE ONE THAT WOULD HAVE BEEN EASY TO SHIP.

    "Every blocking finding is reviewed" is trivially true of an empty list. A caller that
    stopped sending the findings -- a mapping that returned nothing on an unfamiliar payload,
    a workflow step that dropped an artifact -- would turn the gate off silently and in the
    open direction. So the count the summary reports is what says how many findings must
    arrive, and a mismatch is refused rather than reconciled.
    """
    assert not reviewed(scan=summary(critical=4), registry=empty_registry(), findings=())
    assert not reviewed(scan=summary(critical=4), registry=empty_registry(), findings=None)


def test_fewer_findings_than_the_summary_counts_is_refused_even_if_all_are_reviewed() -> None:
    """The same guard, in the shape it would actually arrive in.

    Not an empty list but a short one: three of four findings carried through, all three
    reviewed. Every finding present is accounted for and the image still has a critical
    nobody has seen.
    """
    findings = tuple(finding(cve) for cve in PERL_CVES)
    registry = registry_reviewing(*(review(cve) for cve in PERL_CVES))

    assert not reviewed(scan=summary(critical=4), registry=registry, findings=findings)


def test_a_reviewed_vulnerability_below_the_blocking_severity_is_not_needed() -> None:
    """Reviews are only consulted for findings that block.

    Mutation: require a review for every finding. The base carries eight highs and three
    mediums that nobody has reviewed and that the policy does not block on, so demanding a
    review for those would refuse every image while looking like tightening.
    """
    assert reviewed(scan=summary(high=8, medium=3), registry=empty_registry(), findings=())


def test_a_per_digest_exception_still_overrides_everything() -> None:
    """The stronger statement stays available and stays first.

    A human saying "I looked at this image" covers a scan that never completed, a registry
    that cannot scan the image at all, and findings nobody has enumerated. Reviewing
    vulnerabilities is the routine path; this remains the escape hatch.
    """
    assert reviewed(
        scan=None, registry=registry_with(OTHER_DIGEST), findings=None
    )


def test_two_reviews_of_one_vulnerability_in_one_package_are_refused_at_load() -> None:
    """Mutation: allow duplicates.

    Two entries for the same pair means two people reviewed the same thing and at least one
    of the reasons is not the one in force. The reason is the whole value of the record.
    """
    with pytest.raises(ValueError, match="more than one recorded review"):
        registry_reviewing(review(GLIBC_CVE, "glibc"), review(GLIBC_CVE, "glibc"))


def test_a_review_needs_a_reason_worth_reading() -> None:
    """The same floor the per-digest exception carries, for the same reason."""
    with pytest.raises(ValueError):
        ReviewedVulnerability(
            vulnerability_id=GLIBC_CVE,
            package_name="glibc",
            reason="accepted",
            recorded_by="philote",
            recorded_at=SCANNED_AT,
        )


def test_the_blocking_findings_are_read_off_the_describe_result() -> None:
    """One mapping in one place, beside the one that builds the summary.

    Two callers read the same answer from two directions -- the state machine hands the
    validator a describe result, and the resolver reads the same call from the compile side
    -- and two copies of this would be two chances to disagree about which findings block.
    """
    payload = {
        "imageScanStatus": {"status": "COMPLETE"},
        "imageScanFindings": {
            "imageScanCompletedAt": "2026-07-26T22:05:49+00:00",
            "findingSeverityCounts": {"CRITICAL": 1, "HIGH": 2},
            "findings": [
                {
                    "name": GLIBC_CVE,
                    "severity": "CRITICAL",
                    "attributes": [{"key": "package_name", "value": "glibc"}],
                },
                {
                    "name": "CVE-2026-1",
                    "severity": "HIGH",
                    "attributes": [{"key": "package_name", "value": "perl"}],
                },
            ],
        },
    }

    assert blocking_findings_from_ecr(payload, policy=critical_only_policy()) == (
        finding(GLIBC_CVE, "glibc"),
    )


#: The same answer as the payload above, in the casing the Step Functions AWS SDK integration
#: actually returns. Copied from a real execution's ReadImageScan output rather than written
#: from the API reference, because the API reference documents the wire shape and the
#: integration re-cases it.
PASCAL_CASE_DESCRIBE_RESULT = {
    "ImageId": {"ImageDigest": OTHER_DIGEST},
    "RegistryId": "example",
    "RepositoryName": "sbsandbox-intern-edullm-olmo-core",
    "ImageScanStatus": {"Status": "COMPLETE", "Description": "The scan was completed."},
    "ImageScanFindings": {
        "ImageScanCompletedAt": "2026-07-30T18:23:46Z",
        "FindingSeverityCounts": {"CRITICAL": 1, "HIGH": 8, "MEDIUM": 3},
        "Findings": [
            {
                "Name": GLIBC_CVE,
                "Severity": "CRITICAL",
                "Attributes": [
                    {"Key": "CVSS3_SCORE", "Value": "9.8"},
                    {"Key": "package_name", "Value": "glibc"},
                ],
            },
            {
                "Name": "CVE-2026-48962",
                "Severity": "HIGH",
                "Attributes": [{"Key": "package_name", "Value": "perl"}],
            },
        ],
    },
}


#: The same answer again, after ``ReadImageScan``'s Output projection has run over it. Not
#: written by hand and not produced by reimplementing the JSONata in Python, either of which
#: would assert this file's idea of the transform rather than the engine's: the fixture above
#: was fed to a Pass state carrying the projection verbatim out of
#: ``infra/admission-state-machine.yaml`` through ``aws stepfunctions test-state``, and this
#: is what came back.
#:
#: Why the state narrows the findings at all is in that file's comment: an unprojected result
#: is about a kilobyte a finding and a full page of them does not fit in a state's 256 KB.
PROJECTED_DESCRIBE_RESULT = {
    "ImageId": {"ImageDigest": OTHER_DIGEST},
    "RegistryId": "example",
    "RepositoryName": "sbsandbox-intern-edullm-olmo-core",
    "ImageScanStatus": {"Status": "COMPLETE", "Description": "The scan was completed."},
    "ImageScanFindings": {
        "ImageScanCompletedAt": "2026-07-30T18:23:46Z",
        "FindingSeverityCounts": {"CRITICAL": 1, "HIGH": 8, "MEDIUM": 3},
        "Findings": [
            {
                "Name": GLIBC_CVE,
                "Severity": "CRITICAL",
                "Attributes": [{"Key": "package_name", "Value": "glibc"}],
            },
            {
                "Name": "CVE-2026-48962",
                "Severity": "HIGH",
                "Attributes": [{"Key": "package_name", "Value": "perl"}],
            },
        ],
    },
}


def test_a_projected_describe_result_reads_the_same_as_the_whole_one() -> None:
    """The seam the state machine's projection sits on, asserted from the reading end.

    ``ReadImageScan`` strips ``Description`` and ``Uri`` off every finding and keeps one
    attribute, which is what lets a thousand of them fit in a state. That is a coupling
    between an ASL expression and this mapping, held together by nothing the type system can
    see: an attribute this starts reading, or a field the projection stops keeping, breaks
    admission for every image at once and does it in the fail-closed direction, so the
    symptom is every repository refusing rather than an error anybody can read.

    Both halves of the mapping are run because they read different parts of the payload. The
    summary comes from ``FindingSeverityCounts``, which the projection does not touch and
    must not, since it is the count the gate compares against.
    """
    policy = critical_only_policy()

    assert image_scan_summary_from_ecr(PROJECTED_DESCRIBE_RESULT) == (
        image_scan_summary_from_ecr(PASCAL_CASE_DESCRIBE_RESULT)
    )
    assert blocking_findings_from_ecr(
        PROJECTED_DESCRIBE_RESULT, policy=policy
    ) == blocking_findings_from_ecr(PASCAL_CASE_DESCRIBE_RESULT, policy=policy)


def test_a_projection_that_dropped_the_package_would_refuse_rather_than_pass() -> None:
    """Mutation: narrow the projection to Name and Severity and keep no attributes.

    It is the obvious next saving and it costs the mapping the package name, which every
    ``ScanFinding`` requires. The direction that lands in is the one worth pinning: the whole
    answer becomes unreadable rather than a finding becoming anonymous, so the count guard
    refuses instead of matching a review against a finding it cannot name.
    """
    stripped = {
        **PROJECTED_DESCRIBE_RESULT,
        "ImageScanFindings": {
            **PROJECTED_DESCRIBE_RESULT["ImageScanFindings"],
            "Findings": [
                {"Name": GLIBC_CVE, "Severity": "CRITICAL"},
            ],
        },
    }

    assert blocking_findings_from_ecr(stripped, policy=critical_only_policy()) is None


def test_the_summary_reads_the_casing_the_state_machine_actually_returns() -> None:
    """THIS WAS BROKEN FOR AS LONG AS IT HAS EXISTED AND NOTHING NOTICED.

    ``aws ecr describe-image-scan-findings`` answers in camelCase and the Step Functions AWS
    SDK integration answers in PascalCase, all the way down to ``Key`` and ``Value`` on a
    finding's attributes. This mapping read camelCase, so on the admission side it returned
    ``None`` every time -- which the gate reads as nobody having seen the findings.

    It was invisible because it fails closed and because the only digests that could be
    submitted were covered by a per-digest exception, and an exception is consulted before
    the summary. Retiring those exceptions is what surfaced it: the first submission of an
    image whose findings were reviewed rather than whose digest was blessed was refused by
    admission while compile accepted it, which is the two sides disagreeing -- the one thing
    the shared mapping exists to prevent.

    Measured from a real execution rather than from the API reference, which documents the
    wire shape and not the integration's re-casing.
    """
    summary = image_scan_summary_from_ecr(PASCAL_CASE_DESCRIBE_RESULT)

    assert summary is not None
    assert summary.complete
    assert summary.critical == 1
    assert summary.high == 8
    assert summary.medium == 3


def test_the_findings_read_the_casing_the_state_machine_actually_returns() -> None:
    """The same defect one field further in, and it would have been the next one.

    Attributes carry ``Key`` and ``Value`` rather than ``key`` and ``value``, so a mapping
    that handled the outer casing and not this one would find every finding and know the
    package of none -- and refuse the whole answer, because a finding it cannot name a
    package for makes the result unreadable rather than shorter.
    """
    findings = blocking_findings_from_ecr(
        PASCAL_CASE_DESCRIBE_RESULT, policy=critical_only_policy()
    )

    assert findings == (finding(GLIBC_CVE, "glibc"),)


def test_both_casings_of_one_answer_read_the_same() -> None:
    """One mapping, two wire shapes, and the point is that they cannot diverge.

    The compile side reads the CLI and the admission side reads the integration, and
    admission refuses a run whose findings disagree with what compile saw. Two mappings would
    make that disagreement reachable; this asserts it is not.
    """
    camel = {
        "imageScanStatus": {"status": "COMPLETE"},
        "imageScanFindings": {
            "imageScanCompletedAt": "2026-07-30T18:23:46Z",
            "findingSeverityCounts": {"CRITICAL": 1, "HIGH": 8, "MEDIUM": 3},
            "findings": [
                {
                    "name": GLIBC_CVE,
                    "severity": "CRITICAL",
                    "attributes": [{"key": "package_name", "value": "glibc"}],
                },
                {
                    "name": "CVE-2026-48962",
                    "severity": "HIGH",
                    "attributes": [{"key": "package_name", "value": "perl"}],
                },
            ],
        },
    }
    policy = critical_only_policy()

    assert image_scan_summary_from_ecr(camel) == image_scan_summary_from_ecr(
        PASCAL_CASE_DESCRIBE_RESULT
    )
    assert blocking_findings_from_ecr(camel, policy=policy) == blocking_findings_from_ecr(
        PASCAL_CASE_DESCRIBE_RESULT, policy=policy
    )


def test_an_unreadable_describe_result_yields_no_findings_rather_than_an_empty_pass() -> None:
    """``None``, not ``()``, and the difference is the whole guard above.

    An empty tuple means "the registry found nothing that blocks", which is a pass. A
    payload this cannot read means "nobody knows", and the gate reads that as a refusal
    because the count from the summary will not match.
    """
    assert blocking_findings_from_ecr({"nonsense": True}, policy=critical_only_policy()) is None


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
# Which kind of no, and why the difference is worth a type
# ---------------------------------------------------------------------------------------


def verdict(
    *,
    digest: str = OTHER_DIGEST,
    scan: ImageScanSummary | None,
    registry: ImageScanExceptionRegistry | None = None,
    findings: tuple[ScanFinding, ...] | None = None,
) -> ImageScanVerdict:
    return image_scan_verdict(
        image_digest=digest,
        summary=scan,
        policy=critical_only_policy(),
        registry=registry if registry is not None else empty_registry(),
        blocking_findings=findings,
    )


def test_one_call_carrying_every_finding_the_registry_counts_admits_the_image() -> None:
    """The whole answer in hand, every blocking finding reviewed, so the gate lets it run.

    This is the case ``olmo-eval-full`` could not reach. The registry counted thirteen
    criticals and the read returned four of them, because ECR pages its findings and the
    request asked for a default page. The image had nothing wrong with it.
    """
    findings = tuple(finding(cve) for cve in PERL_CVES) + (finding(GLIBC_CVE, "glibc"),)
    registry = registry_reviewing(
        *(review(cve) for cve in PERL_CVES), review(GLIBC_CVE, "glibc")
    )

    assert verdict(scan=summary(critical=4), registry=registry, findings=findings) is (
        ImageScanVerdict.REVIEWED
    )


def test_an_answer_that_stops_short_of_the_count_says_it_was_not_read() -> None:
    """THE DEFECT THIS TYPE EXISTS FOR, AND IT IS THE MESSAGE RATHER THAN THE OUTCOME.

    Four of thirteen findings arrived and all four are reviewed. Refusing is right and was
    always right. Calling it unreviewed is what sent an operator to write reviews for
    findings that had already been reviewed, against an image that was refused because
    nobody had fetched them.
    """
    findings = tuple(finding(cve) for cve in PERL_CVES)
    registry = registry_reviewing(*(review(cve) for cve in PERL_CVES))

    assert verdict(scan=summary(critical=13), registry=registry, findings=findings) is (
        ImageScanVerdict.FINDINGS_UNREAD
    )


def test_no_findings_at_all_against_a_count_is_also_a_read_that_did_not_happen() -> None:
    """``None`` from the mapping and a short list are the same fact: they are not in hand.

    Kept separate from the unreviewed verdict rather than folded into it, because a caller
    that stopped sending findings is a platform failure and an unreviewed finding is a
    request for a human. Only one of the two can be answered by a person.
    """
    assert verdict(scan=summary(critical=4), findings=None) is ImageScanVerdict.FINDINGS_UNREAD
    assert verdict(scan=summary(critical=4), findings=()) is ImageScanVerdict.FINDINGS_UNREAD


def test_an_empty_result_on_a_scan_that_found_nothing_is_a_pass() -> None:
    """No findings and no count is a clean image, not an unread one.

    The distinction the verdict has to get right: an empty list is a refusal when the
    summary counts something and a pass when it counts nothing.
    """
    assert verdict(scan=summary(), findings=()) is ImageScanVerdict.REVIEWED
    assert verdict(scan=summary(high=8, medium=3), findings=()) is ImageScanVerdict.REVIEWED


def test_every_finding_read_with_one_unreviewed_is_the_case_a_person_can_clear() -> None:
    """The genuine unreviewed case, which must stay refused and must keep its own words.

    Every finding the registry counts arrived, so the platform knows exactly which one has
    no review against it. That is the only outcome where recording a review changes the
    answer.
    """
    findings = tuple(finding(cve) for cve in PERL_CVES) + (finding("CVE-2026-99999", "zlib"),)
    registry = registry_reviewing(*(review(cve) for cve in PERL_CVES))

    assert verdict(scan=summary(critical=4), registry=registry, findings=findings) is (
        ImageScanVerdict.FINDINGS_UNREVIEWED
    )


def test_a_truncated_read_and_an_unreviewed_finding_are_not_the_same_verdict() -> None:
    """Stated as one assertion because the two being equal is the defect.

    Both refuse. They ask different things of whoever reads the refusal, and the platform
    told somebody the wrong one of the two for as long as the read was paged.
    """
    reviews = registry_reviewing(*(review(cve) for cve in PERL_CVES))
    short = verdict(
        scan=summary(critical=13),
        registry=reviews,
        findings=tuple(finding(cve) for cve in PERL_CVES),
    )
    unreviewed = verdict(
        scan=summary(critical=4),
        registry=reviews,
        findings=tuple(finding(cve) for cve in PERL_CVES) + (finding("CVE-2026-99999", "zlib"),),
    )

    assert short is not unreviewed


def test_no_scan_result_and_an_unfinished_scan_are_told_apart_too() -> None:
    """Neither is a review question either, and neither is the other.

    A payload that is not a describe result means the read failed; a scan reporting
    anything but COMPLETE means the registry has not finished. Both were reported as
    unreviewed findings before this type existed.
    """
    unfinished = ImageScanSummary(
        schema_version=1, status=ImageScanStatus.IN_PROGRESS, scanned_at=SCANNED_AT
    )

    assert verdict(scan=None) is ImageScanVerdict.SCAN_UNREADABLE
    assert verdict(scan=unfinished) is ImageScanVerdict.SCAN_INCOMPLETE


def test_a_recorded_exception_is_still_the_first_thing_consulted() -> None:
    """A human saying they looked outranks every kind of not-knowing, as it always did."""
    assert verdict(scan=None, registry=registry_with(OTHER_DIGEST)) is ImageScanVerdict.REVIEWED


@pytest.mark.parametrize(
    "scan,findings",
    [
        (None, None),
        (summary(), ()),
        (summary(critical=4), None),
        (summary(critical=4), ()),
        (summary(critical=3), tuple(finding(cve) for cve in PERL_CVES)),
        (summary(critical=4), tuple(finding(cve) for cve in PERL_CVES)),
    ],
)
def test_the_boolean_gate_is_the_verdict_and_cannot_drift_from_it(
    scan: ImageScanSummary | None, findings: tuple[ScanFinding, ...] | None
) -> None:
    """Mutation: reimplement the boolean instead of deriving it.

    ``image_scan_is_reviewed`` is what every caller asks and what the denial condition is
    wired to, and the verdict is what the refusal message reads. Two implementations of one
    question is two chances for the message to describe an outcome the decision did not
    take -- which is the class of defect this whole change is about, arriving one layer up.
    """
    registry = registry_reviewing(*(review(cve) for cve in PERL_CVES))
    both = {
        "scan": scan,
        "registry": registry,
        "findings": findings,
    }

    assert reviewed(**both) is (verdict(**both) is ImageScanVerdict.REVIEWED)


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
        "dataset_is_a_corpus": True,
        "compute_profile_registered": True,
        "immutable_revision": True,
        "immutable_image": True,
        "image_scan_reviewed": True,
        "estimated_cost_usd": "1",
        "maximum_runtime_hours": "1",
        "maximum_attempts": 1,
    }
    thresholds = load_yaml(PROJECT_ROOT / "config" / "policy.yaml", ApprovalPolicy).thresholds
    # A rate under the ceiling on both calls, so the scan review is the only thing that
    # differs between them. This test is about the fact, not about the machine.
    assert (
        classify_request(
            RequestFacts.model_validate(payload), thresholds, hourly_rate_usd=ROUTINE_RATE
        )
        is ApprovalClass.ROUTINE
    )

    unreviewed = RequestFacts.model_validate({**payload, "image_scan_reviewed": False})
    assert (
        classify_request(unreviewed, thresholds, hourly_rate_usd=ROUTINE_RATE)
        is ApprovalClass.EXCEPTION
    )


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


def test_both_production_callers_forward_the_findings_they_were_given() -> None:
    """The sibling of the test above, for the argument that arrived with reviewed CVEs.

    ``blocking_findings`` defaults to ``None`` and ``None`` refuses any image carrying a
    blocking finding, so a caller that dropped it fails closed rather than open -- which is
    the right direction and is also the direction nobody notices, because it looks exactly
    like the base having an unreviewed CVE. The whole pilot would go back to needing a
    hand-written exception per image and the suite would stay green.

    Asserted on the source rather than by calling, because what is being protected is that
    the wiring exists at all. There is no input to ``admit`` that distinguishes "forwarded
    nothing" from "was given nothing".
    """
    for module, function in ((admission, "admit"), (submission, "compile_submission")):
        source = inspect.getsource(getattr(module, function))
        assert "image_scan_findings=image_scan_findings" in source, (
            f"{module.__name__}.{function} no longer forwards the blocking findings into "
            "build_request_facts, so every image with a reviewed CVE is refused as though "
            "nobody had reviewed it"
        )


def test_the_handler_derives_the_findings_from_the_same_call_as_the_summary() -> None:
    """Mutation: read the findings from the execution input instead of the describe result.

    The caller supplies the manifest. Letting it also supply the findings would let a
    submitter declare its own image's vulnerabilities, which is the same hole the summary is
    already guarded against -- and it would be worse here, because a list is easier to
    understate convincingly than a count.
    """
    source = inspect.getsource(admission_handler.handler)

    assert 'blocking_findings_from_ecr(\n            event.get("image_scan")' in source
    assert 'image_scan_summary_from_ecr(event.get("image_scan"))' in source


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


def test_the_shipped_policy_sends_an_unreviewed_scan_to_the_admin_gate() -> None:
    """The gate after v4 softened it: an exception a person can release, not a wall.

    Mutation: drop ``image_scan_reviewed`` from ``classify_request``'s exception test. It
    came out of ``denied_outright`` on 2026-08-05 and that list is now the only thing left
    holding the gate up, so a policy that names it nowhere and a classifier that ignores it
    look identical from the config file. This asks the classifier.

    Denied outright means refusable by nobody, so a wrong answer costs the whole route. It
    fired twice, both against the owner self-approving, and config/image-exceptions.yaml
    holds zero exceptions against sixteen reviewed findings, so the review process it was
    forcing does not exist. What survives is that no lead can release one and the approver
    is shown the findings by name.
    """
    policy = shipped_policy()
    assert "image_scan_findings_unreviewed" not in policy.denied_outright

    unreviewed = RequestFacts.model_validate(
        {
            "claimed_team": "memory-split",
            "repository_registered": True,
            "dataset_registered": True,
            "dataset_is_a_corpus": True,
            "compute_profile_registered": True,
            "immutable_revision": True,
            "immutable_image": True,
            "image_scan_reviewed": False,
            "estimated_cost_usd": "1",
            "maximum_runtime_hours": "1",
            "maximum_attempts": 1,
        }
    )
    assert (
        classify_request(unreviewed, policy.thresholds, hourly_rate_usd=ROUTINE_RATE)
        is ApprovalClass.EXCEPTION
    )
    assert ApprovalEnvironment.for_approval_class(ApprovalClass.EXCEPTION) is (
        ApprovalEnvironment.ADMIN
    )


def test_the_shipped_registry_covers_what_the_published_images_actually_carry() -> None:
    """Ties the configuration to the account, so deleting a review fails here.

    Every image this platform has published carries the same four criticals, inherited from
    the base both registered repositories build from. Without a review for each of them
    nothing can run at all, and every other test in this file would still pass -- which is
    why this one reads the shipped config rather than a fixture.

    **It asserted a per-digest exception until those were retired**, and the difference is
    the whole change. The old form tied the configuration to one image, so it went stale the
    moment anybody rebuilt; this ties it to what the registry reports, which is a fact about
    the base and stays true across rebuilds. The four are written out rather than derived,
    because a test that read them from the same file it is checking would pass against an
    empty one.
    """
    registry = shipped_registry()
    carried = (
        ScanFinding(vulnerability_id="CVE-2026-57433", package_name="perl"),
        ScanFinding(vulnerability_id="CVE-2026-12087", package_name="perl"),
        ScanFinding(vulnerability_id="CVE-2026-13221", package_name="perl"),
        ScanFinding(vulnerability_id="CVE-2026-5450", package_name="glibc"),
    )

    assert unreviewed_blocking_findings(blocking_findings=carried, registry=registry) == ()
    for found in carried:
        review = registry.review_for(found)
        assert review is not None
        assert len(review.reason) >= 40


def test_the_shipped_registry_no_longer_blesses_whole_images() -> None:
    """Mutation: re-add a per-digest exception to make one submission work.

    That is the move this change exists to remove, and it is a tempting one under time
    pressure -- it is one entry and it unblocks the run in front of you. It also makes every
    finding in that image reviewed, including any the project introduced, and it goes stale
    on the next rebuild so the next person does it again.

    Not a ban: the form survives in the contract for the case it is right for, an image the
    registry cannot scan at all. This says the shipped configuration does not currently need
    it, so re-adding one is a decision somebody takes rather than a habit.
    """
    assert shipped_registry().exceptions == ()


def test_the_shipped_registry_excepts_nothing_it_does_not_explain() -> None:
    for exception in shipped_registry().exceptions:
        assert exception.recorded_by
        assert "base" in exception.reason.lower() or "inherited" in exception.reason.lower()
