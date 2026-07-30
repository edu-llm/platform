"""Whether a published image's scan findings have been looked at, and by whom.

This is the answer to the one question ``open_decisions.py`` carried: *should the result of
the registry image scan be able to block a publish, and if so on what?* It is answered here
rather than there because the register's own rule is that an answer moves to where it is
enforced.

**What was decided.** Block unless an exception is recorded against the digest -- the fourth
of the four options that register listed -- and enforce it at admission rather than at
publish.

**Why not a severity threshold on its own.** The only image the platform has published
carries four critical and eight high findings. Every one of them is a Debian base-OS
package: ``perl`` accounts for three of the four criticals, and the rest are ``glibc``,
``sqlite3``, ``util-linux`` and ``coreutils``. The Dockerfile installs nothing, so none of
them came from this project, and none can be fixed here -- only by upstream Debian shipping
a patch and the base being re-pinned. A bare threshold would have refused this phase's own
workload on findings nobody here introduced, with no way to proceed.

**Why not record-and-never-block.** A four-critical image would then route to
``run-approval-lead`` exactly like a clean one. A number with no consequence attached is the
rubber-stamping failure the approver context already had to be designed against for cost.

**Why not the delta against the base**, which is the most precise answer and would correctly
report zero for this image: the base lives on Docker Hub and ECR only scans what is in ECR.
Phase 1 deliberately deferred mirroring to Phase 4. It becomes nearly free once the base is
mirrored and it is the right target state; it is not implementable now.

**Why admission rather than publish.** The register says the enforcement point is the
publish workflow. It should not be, and the reason is mechanical: ECR scans an image after
it is pushed, Phase 1 tags are immutable, and the publish job's pre-flight tag lookup
short-circuits every retry to the digest already published. A publish-time block would push
the image, tag it, fail, and leave that commit permanently unpublishable. Admission is where
a digest is authorized to *run*, which is the question a scan bears on.

**What an exception is.** A reviewed entry in ``config/image-exceptions.yaml`` naming the
digest, why the findings are acceptable, and who said so. It is a pull request, so it
carries a reviewer and a date without needing machinery of its own. Absent one, an image
whose scan carries a blocking severity is refused outright -- not classified as an exception
for an admin to wave through in the moment, because the point is that somebody looked at the
findings before the run was submitted rather than while it was waiting.

**Failing closed is deliberate and has a cost worth naming.** No scan, an incomplete scan, or
a scan nobody has reviewed all read as not-reviewed. That means a freshly published image
cannot run until either its scan is clean of blocking severities or somebody records an
exception. That is the intended friction; the alternative is a window in which an unscanned
digest runs because the scan had not finished yet.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, ValidationError, model_validator

from .base import (
    ContractModel,
    Sha256Digest,
    UtcTimestamp,
    parse_str_enum,
    require_ordered_sequence,
)
from .bindings import GitHubLogin

__all__ = [
    "VULNERABILITY_ID_PATTERN",
    "ImageScanException",
    "ImageScanExceptionRegistry",
    "ImageScanPolicy",
    "ImageScanSeverity",
    "ImageScanStatus",
    "ImageScanSummary",
    "ReviewedVulnerability",
    "ScanFinding",
    "VulnerabilityId",
    "blocking_findings_from_ecr",
    "image_scan_is_reviewed",
    "image_scan_summary_from_ecr",
    "unreviewed_blocking_findings",
]


class ImageScanSeverity(StrEnum):
    """The severities ECR basic scanning reports, in the order it ranks them."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFORMATIONAL = "INFORMATIONAL"
    UNDEFINED = "UNDEFINED"


class ImageScanStatus(StrEnum):
    COMPLETE = "COMPLETE"
    IN_PROGRESS = "IN_PROGRESS"
    FAILED = "FAILED"
    UNSUPPORTED_IMAGE = "UNSUPPORTED_IMAGE"


ImageScanSeverityValue = Annotated[
    ImageScanSeverity, BeforeValidator(parse_str_enum(ImageScanSeverity))
]
ImageScanStatusValue = Annotated[
    ImageScanStatus, BeforeValidator(parse_str_enum(ImageScanStatus))
]


class ImageScanSummary(ContractModel):
    """What the registry's scan of one image found, as counts per severity.

    Counts rather than the findings themselves. A finding list is long, changes as the
    vulnerability database moves, and would put CVE detail into an immutable lineage store
    where it cannot be corrected. What a decision needs is how many of each severity there
    were at the moment the decision was taken, which is a fact that does not go stale
    because it is timestamped.

    ``status`` is kept because ``COMPLETE`` with zero findings and ``IN_PROGRESS`` with zero
    findings are the same numbers and opposite facts.
    """

    schema_version: Literal[1]
    status: ImageScanStatusValue
    scanned_at: UtcTimestamp
    critical: int = Field(default=0, ge=0)
    high: int = Field(default=0, ge=0)
    medium: int = Field(default=0, ge=0)
    low: int = Field(default=0, ge=0)
    informational: int = Field(default=0, ge=0)
    undefined: int = Field(default=0, ge=0)

    def count_for(self, severity: ImageScanSeverity) -> int:
        return int(getattr(self, severity.value.lower()))

    @property
    def complete(self) -> bool:
        return self.status is ImageScanStatus.COMPLETE

    @property
    def total(self) -> int:
        return sum(self.count_for(severity) for severity in ImageScanSeverity)


class ImageScanPolicy(ContractModel):
    """Which severities require a recorded exception before an image may run.

    ``blocking_severities`` is a list rather than a single floor because "critical and high"
    and "critical only" are both defensible and the difference is a policy decision somebody
    should be able to make by editing a file. It must not be empty: an empty list would mean
    nothing ever blocks, which is option one wearing option four's clothes, and it would do
    so without anybody recording that the policy had changed.
    """

    blocking_severities: Annotated[
        tuple[ImageScanSeverityValue, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)

    @model_validator(mode="after")
    def validate_severities_are_distinct(self) -> Self:
        if len(set(self.blocking_severities)) != len(self.blocking_severities):
            raise ValueError("blocking severities must be distinct")
        return self

    def blocking_findings(self, summary: ImageScanSummary) -> int:
        return sum(summary.count_for(severity) for severity in self.blocking_severities)


class ImageScanException(ContractModel):
    """One digest somebody looked at and accepted, with their name against it.

    ``reason`` is required and has a floor on its length because "approved" is not a reason,
    and the whole value of this record is that a later reader can tell whether the findings
    were understood or waved through.
    """

    image_digest: Sha256Digest
    reason: str = Field(min_length=40)
    recorded_by: GitHubLogin
    recorded_at: UtcTimestamp


#: What the registry calls a vulnerability. Deliberately wider than ``CVE-…``: ECR reports
#: whatever its source database uses, and a shape this refuses is a finding nobody can ever
#: review -- which would wedge the gate closed rather than fail it safe.
VULNERABILITY_ID_PATTERN = r"^[A-Za-z][A-Za-z0-9]*-[A-Za-z0-9][A-Za-z0-9.-]*$"

VulnerabilityId = Annotated[str, Field(pattern=VULNERABILITY_ID_PATTERN, max_length=128)]


class ScanFinding(ContractModel):
    """One finding at a blocking severity, as the registry reported it.

    Carried between the two places that read a scan and the one place that judges it, which
    is why it is a contract rather than a tuple: the resolver serializes these into the
    artifact the compile job reads, and the state machine hands the equivalent to the
    validator.

    The package is part of the identity. A vulnerability is reviewed as a statement about a
    package this platform ships -- unreachable from the entrypoint, or unfixable upstream,
    or both -- and the same identifier in a different package is a different question.
    """

    vulnerability_id: VulnerabilityId
    package_name: str = Field(min_length=1, max_length=128)


class ReviewedVulnerability(ContractModel):
    """One vulnerability somebody read and accepted, with their name against it.

    THE UNIT OF REVIEW IS THE VULNERABILITY, NOT THE IMAGE, AND THAT IS THE POINT. Every
    image this platform builds inherits the same criticals from the base it shares -- perl
    and glibc, unfixable from this repository, with no patched base published upstream. A
    per-digest exception makes each rebuild a reviewed pull request naming seventy-one
    characters, so a researcher cannot iterate without an admin. That is the friction this
    platform removed from choosing an image, arriving one step to the left.

    What a reviewer does is read a finding and decide it is acceptable. This lets the record
    say that, once, and keeps saying it across rebuilds. It is not a loosening: a finding
    nobody has reviewed still refuses the run, which is what the per-digest form could not
    express, because it could not tell an inherited finding from an introduced one.

    ``reason`` carries the same floor as :class:`ImageScanException` and for the same
    reason: "approved" is not a reason, and the value of the record is that a later reader
    can tell whether the finding was understood or waved through.
    """

    vulnerability_id: VulnerabilityId
    package_name: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=40)
    recorded_by: GitHubLogin
    recorded_at: UtcTimestamp

    @property
    def covers(self) -> tuple[str, str]:
        return (self.vulnerability_id, self.package_name)


class ImageScanExceptionRegistry(ContractModel):
    """What may run despite carrying blocking findings, in two forms.

    ``reviewed_vulnerabilities`` is the routine one: a finding read and accepted, covering
    every image that carries it. ``exceptions`` is the stronger and rarer one: a whole image
    accepted, which covers a scan that never completed and an image the registry cannot scan
    at all, because it is a human saying they looked rather than a claim about a finding.
    """

    schema_version: Literal[1]
    exceptions: Annotated[
        tuple[ImageScanException, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(default=(), strict=False)
    reviewed_vulnerabilities: Annotated[
        tuple[ReviewedVulnerability, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(default=(), strict=False)

    @model_validator(mode="after")
    def validate_one_exception_per_digest(self) -> Self:
        digests = [exception.image_digest for exception in self.exceptions]
        if len(set(digests)) != len(digests):
            raise ValueError("a digest must not carry more than one recorded exception")
        return self

    @model_validator(mode="after")
    def validate_one_review_per_vulnerability(self) -> Self:
        covered = [review.covers for review in self.reviewed_vulnerabilities]
        if len(set(covered)) != len(covered):
            raise ValueError(
                "a vulnerability in a package must not carry more than one recorded review"
            )
        return self

    def exception_for(self, image_digest: str) -> ImageScanException | None:
        for exception in self.exceptions:
            if exception.image_digest == image_digest:
                return exception
        return None

    def review_for(self, finding: ScanFinding) -> ReviewedVulnerability | None:
        for review in self.reviewed_vulnerabilities:
            if review.covers == (finding.vulnerability_id, finding.package_name):
                return review
        return None


def image_scan_summary_from_ecr(payload: object) -> ImageScanSummary | None:
    """Build a summary from what ``ecr:DescribeImageScanFindings`` returned, or ``None``.

    One mapping, in one place, because two callers read the same answer from two directions
    -- the state machine hands the Lambda a describe result, and the capture tooling reads
    the same call -- and two copies of this would be two chances to disagree about what
    ``findingSeverityCounts`` omitting a severity means. It means zero.

    Returns ``None`` for anything that is not a well-formed describe result, including an
    error shape. That is the fail-closed direction: ``image_scan_is_reviewed`` reads a
    ``None`` summary as nobody having seen the findings, so a malformed answer refuses the
    run rather than passing it. A caller that wants to tell "no scan" from "unreadable
    answer" has to look at the payload itself; the distinction does not change the decision.
    """
    if not isinstance(payload, dict):
        return None
    status_block = _field(payload, "imageScanStatus")
    findings = _field(payload, "imageScanFindings")
    if not isinstance(status_block, dict) or not isinstance(findings, dict):
        return None
    status = _field(status_block, "status")
    completed_at = _field(findings, "imageScanCompletedAt")
    if not isinstance(status, str) or not isinstance(completed_at, str):
        return None
    counts = _field(findings, "findingSeverityCounts")
    counts = counts if isinstance(counts, dict) else {}
    # model_validate rather than the constructor, so the BeforeValidators that parse the
    # status string and the timestamp are the ones that run. Calling the constructor with
    # strings type-checks as wrong and works at runtime, which is the worst pair.
    try:
        return ImageScanSummary.model_validate(
            {
                "schema_version": 1,
                "status": status,
                "scanned_at": completed_at,
                **{
                    severity.value.lower(): int(counts.get(severity.value, 0))
                    for severity in ImageScanSeverity
                },
            }
        )
    except (ValidationError, ValueError, TypeError):
        return None


def _field(payload: object, name: str) -> object:
    """One field of a describe result, in whichever casing the caller's transport used.

    TWO TRANSPORTS, TWO CASINGS, AND THIS WAS WRONG FOR AS LONG AS IT EXISTED.
    ``aws ecr describe-image-scan-findings`` answers in camelCase; the Step Functions AWS SDK
    integration answers the same call in PascalCase, down to ``Key`` and ``Value`` on a
    finding's attributes. The mapping below read camelCase only, so on the admission side it
    returned ``None`` every time and the gate read that as nobody having seen the findings.

    It stayed invisible because it fails closed and because a per-digest exception is
    consulted before the summary, and every submittable digest had one. Retiring those
    exceptions surfaced it immediately: compile accepted an image on findings it had read
    and admission refused the same image on findings it could not, which is the two sides
    disagreeing about one answer -- the failure this shared mapping exists to prevent.

    Measured from a real execution's ReadImageScan output rather than taken from the API
    reference, which documents the wire shape and not the integration's re-casing.
    """
    if not isinstance(payload, dict):
        return None
    if name in payload:
        return payload[name]
    return payload.get(name[0].upper() + name[1:])


def blocking_findings_from_ecr(
    payload: object, *, policy: ImageScanPolicy
) -> tuple[ScanFinding, ...] | None:
    """The blocking findings in a ``describe-image-scan-findings`` result, or ``None``.

    Beside :func:`image_scan_summary_from_ecr` and for the same reason: two callers read
    the same answer from two directions -- the state machine hands the validator a describe
    result, and the resolver reads the same call on the credential-free side -- and two
    copies of this would be two chances to disagree about which findings block.

    **``None`` and ``()`` are different answers and the difference is the guard.** An empty
    tuple means the registry reported nothing at a blocking severity, which is a pass. A
    ``None`` means this could not read the payload, and the gate turns that into a refusal
    because the count it compares against will not match. Returning an empty tuple on an
    unreadable answer would be the one bug that opens the gate quietly.

    A finding whose identifier or package this cannot parse makes the whole answer ``None``
    rather than being dropped. Dropping it would produce a shorter list that still satisfies
    every review, which is the vacuous pass arriving through the mapping instead of through
    the caller.
    """
    if not isinstance(payload, dict):
        return None
    findings = _field(payload, "imageScanFindings")
    if not isinstance(findings, dict):
        return None
    raw = _field(findings, "findings")
    if raw is None:
        raw = ()
    if not isinstance(raw, list | tuple):
        return None
    blocking = {severity.value for severity in policy.blocking_severities}
    collected: list[ScanFinding] = []
    for entry in raw:
        if not isinstance(entry, dict):
            return None
        if _field(entry, "severity") not in blocking:
            continue
        attributes = _field(entry, "attributes")
        attributes = attributes if isinstance(attributes, list | tuple) else ()
        package = next(
            (
                _field(attribute, "value")
                for attribute in attributes
                if isinstance(attribute, dict) and _field(attribute, "key") == "package_name"
            ),
            None,
        )
        try:
            collected.append(
                ScanFinding.model_validate(
                    {"vulnerability_id": _field(entry, "name"), "package_name": package}
                )
            )
        except ValidationError:
            return None
    return tuple(collected)


def unreviewed_blocking_findings(
    *,
    blocking_findings: Sequence[ScanFinding],
    registry: ImageScanExceptionRegistry,
) -> tuple[ScanFinding, ...]:
    """The blocking findings nobody has reviewed, so a refusal can name them.

    Separate from the gate because a decision wants a boolean and a message wants a list.
    A refusal reading "unreviewed scan findings" sends a submitter to look at everything
    their image contains; one naming the identifier and the package sends them to the one
    thing that stopped it.
    """
    return tuple(
        finding for finding in blocking_findings if registry.review_for(finding) is None
    )


def image_scan_is_reviewed(
    *,
    image_digest: str,
    summary: ImageScanSummary | None,
    policy: ImageScanPolicy,
    registry: ImageScanExceptionRegistry,
    blocking_findings: Sequence[ScanFinding] | None = None,
) -> bool:
    """Whether this digest may run: clean, every blocking finding reviewed, or excepted.

    Fails closed on every kind of not-knowing. A missing summary, a scan that has not
    finished, and a scan that failed are all "nobody has seen the findings", and none of
    them is a reason to proceed. A recorded exception overrides all of it, including a
    missing scan, because the exception is a human saying they looked -- which is a stronger
    statement than a scan result, and the only one that can cover an image the registry
    cannot scan at all.

    **The count from the summary is what says how many findings must arrive, and that guard
    is load-bearing.** "Every blocking finding is reviewed" is trivially true of an empty
    list, so a caller that stopped sending findings -- a mapping returning nothing on an
    unfamiliar payload, a workflow step dropping an artifact -- would turn the gate off
    silently and in the open direction. A mismatch between the count and the list is refused
    rather than reconciled, which makes the failure loud and closed instead of quiet and
    open. ``blocking_findings`` therefore defaults to ``None`` and that default refuses any
    image with a blocking finding, so a caller cannot reach the permissive branch by
    omission.
    """
    if registry.exception_for(image_digest) is not None:
        return True
    if summary is None or not summary.complete:
        return False
    expected = policy.blocking_findings(summary)
    if expected == 0:
        return True
    if blocking_findings is None or len(blocking_findings) != expected:
        return False
    return not unreviewed_blocking_findings(
        blocking_findings=blocking_findings, registry=registry
    )
