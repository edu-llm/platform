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
    "ImageScanException",
    "ImageScanExceptionRegistry",
    "ImageScanPolicy",
    "ImageScanSeverity",
    "ImageScanStatus",
    "ImageScanSummary",
    "image_scan_is_reviewed",
    "image_scan_summary_from_ecr",
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


class ImageScanExceptionRegistry(ContractModel):
    """The reviewed set of digests that may run despite carrying blocking findings."""

    schema_version: Literal[1]
    exceptions: Annotated[
        tuple[ImageScanException, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(default=(), strict=False)

    @model_validator(mode="after")
    def validate_one_exception_per_digest(self) -> Self:
        digests = [exception.image_digest for exception in self.exceptions]
        if len(set(digests)) != len(digests):
            raise ValueError("a digest must not carry more than one recorded exception")
        return self

    def exception_for(self, image_digest: str) -> ImageScanException | None:
        for exception in self.exceptions:
            if exception.image_digest == image_digest:
                return exception
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
    status_block = payload.get("imageScanStatus")
    findings = payload.get("imageScanFindings")
    if not isinstance(status_block, dict) or not isinstance(findings, dict):
        return None
    status = status_block.get("status")
    completed_at = findings.get("imageScanCompletedAt")
    if not isinstance(status, str) or not isinstance(completed_at, str):
        return None
    counts = findings.get("findingSeverityCounts")
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


def image_scan_is_reviewed(
    *,
    image_digest: str,
    summary: ImageScanSummary | None,
    policy: ImageScanPolicy,
    registry: ImageScanExceptionRegistry,
) -> bool:
    """Whether this digest may run: clean of blocking findings, or excepted by somebody.

    Fails closed on every kind of not-knowing. A missing summary, a scan that has not
    finished, and a scan that failed are all "nobody has seen the findings", and none of
    them is a reason to proceed. A recorded exception overrides all of it, including a
    missing scan, because the exception is a human saying they looked -- which is a stronger
    statement than a scan result, and the only one that can cover an image the registry
    cannot scan at all.
    """
    if registry.exception_for(image_digest) is not None:
        return True
    if summary is None or not summary.complete:
        return False
    return policy.blocking_findings(summary) == 0
