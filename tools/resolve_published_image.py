"""Ask the registry which image a commit published, and what its scan found in it.

Run by the submission workflow's first job, which holds an OIDC token aimed at
``sbsandbox-intern-edullm-image-resolver`` -- a role that may describe images and their
scan findings and nothing else. ``infra/iam/image-resolver-role.yaml`` carries the argument
for a credentialed job sitting ahead of the approval gate; it is not repeated here.

Two questions, and neither of them was askable before. ``DescribeImages`` answers which
digest the commit's tag points at, so a submitter stops transcribing seventy-one characters
out of another repository's build log. ``DescribeImageScanFindings`` answers what the
registry found in that image, so the scan summary stops being absent and
``image_scan_is_reviewed`` stops failing closed against the two-entry allowlist in
``config/image-exceptions.yaml``.

**This tool judges nothing.** It answers what the registry said, or it fails. Every refusal
a submission can meet about its image belongs to ``resolve_image``, which is pure, runs in
the credential-free job downstream, and has all three of its refusals under a unit test.
The one place that distinction is easy to lose is a commit with no image published from it,
and the comment on that branch says why it is written down as an answer rather than raised
as a failure.

Exit codes: 0 answered, 2 the answer could not be obtained. There is deliberately no exit
1: a refusal on the merits is a judgement, and this tool makes none.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from edullm_platform.build_tooling import RegistryUnreadableError, load_registry
from edullm_platform.capture_tooling import EXIT_UNUSABLE, CaptureFailedError, aws, report
from edullm_platform.config import load_yaml
from edullm_platform.contracts.base import serialize_utc_timestamp
from edullm_platform.contracts.image_scan import (
    ImageScanPolicy,
    ImageScanSummary,
    ScanFinding,
    blocking_findings_from_ecr,
    image_scan_summary_from_ecr,
)
from edullm_platform.contracts.manifest import COMMIT_SHA_PATTERN
from edullm_platform.contracts.policy import ApprovalPolicy
from edullm_platform.contracts.repository_registry import UnknownRepositoryError
from edullm_platform.publisher_denials import parse_aws_cli_error

EXIT_OK: Final = 0

#: What a submission refused on its merits exits with, as against ``EXIT_UNUSABLE``'s "this
#: tool could not find out". The two reach a person differently and the difference is the
#: whole reason this constant exists: the submitting workflow answers a non-zero exit with
#: "This is not a refusal on the merits", which is true of a policy file that will not parse
#: and false of a repository nobody registered. That one is a refusal, and the most
#: actionable this platform issues -- the fix is a pull request against
#: config/repositories.yaml -- so it must not be reported as an outage.
EXIT_REFUSED: Final = 1

#: How much of the commit the published tag carries. ``build-research-image.yml`` publishes
#: ``${COMMIT_SHA:0:12}`` and nothing else, so this is the join between a submission's
#: declared commit and the image the registry holds for it. Any other slice is a tag that
#: has never existed, and the answer to it is indistinguishable from an unbuilt commit.
IMAGE_TAG_LENGTH: Final = 12

#: The two reads this makes, named the way ``infra/iam/image-resolver-role.yaml`` grants
#: them. Exported because ``tests/test_phase2_submit_run_workflow.py`` enumerates every AWS
#: call the submission workflow makes, and a call made by a tool rather than by a shell is
#: invisible to a reader of that file unless the tool says what it does.
RESOLVER_ECR_ACTIONS: Final = ("ecr:DescribeImages", "ecr:DescribeImageScanFindings")

#: A tag that is not published. Not a failure: see :func:`describe_published_image`.
IMAGE_ABSENT_ERROR: Final = "ImageNotFoundException"

#: A digest the registry has not scanned. Not a failure either, and not a clean scan.
SCAN_ABSENT_ERROR: Final = "ScanNotFoundException"

COMMIT_SHA = re.compile(COMMIT_SHA_PATTERN)

__all__ = [
    "IMAGE_TAG_LENGTH",
    "RESOLVER_ECR_ACTIONS",
    "build_parser",
    "describe_published_image",
    "describe_scan_findings",
    "main",
]


def _describe(arguments: Sequence[str], *, region: str, absent: str) -> Any | None:
    """One ECR read, its answer parsed, or ``None`` for the absence ``absent`` names.

    ``aws`` rather than ``aws_json``, and the difference is the whole of this function.
    ``aws_json`` folds every non-zero exit into one ``aws_call_failed`` token by design --
    "an empty record and a failed call look identical in a committed fixture" -- and both
    calls here have exactly one error code that is an answer rather than a failure. So the
    exit is read here instead, through the same ``parse_aws_cli_error`` three other modules
    already read a service error with.

    What that shares with ``aws_json`` is the property that matters on a runner: the CLI's
    stderr is never printed and never returned. An AWS refusal names the caller's ARN and
    an ARN carries the account id, so what leaves this function is the error *code* and the
    operation that produced it, both of which are patterned by ``AWS_CLI_ERROR_PATTERN``.
    """
    completed = aws(arguments, region=region)
    if completed.returncode == 0:
        if not completed.stdout.strip():
            return {}
        try:
            return json.loads(completed.stdout)
        except ValueError as error:
            raise CaptureFailedError(f"aws_answer_unreadable:{arguments[1]}") from error
    reported = parse_aws_cli_error(completed.stderr)
    if reported is None:
        raise CaptureFailedError(f"aws_call_failed:{arguments[0]}:{arguments[1]}")
    if reported.code == absent:
        return None
    raise CaptureFailedError(f"aws_call_failed:{reported.operation}:{reported.code}")


def describe_published_image(
    *, ecr_repository: str, image_tag: str, region: str
) -> tuple[str, datetime] | None:
    """The digest published under ``image_tag`` and when the registry took it, or ``None``.

    **A commit with nothing published is an answer here rather than an error, and that is
    deliberate.** ECR reports it as ``ImageNotFoundException``, which is a refusal a
    submitter can do something about -- but not from this job. ``resolve_image`` refuses it
    downstream with a message that names ``build-research-image.yml`` and says the digest is
    printed in its step summary, and nothing this tool could print from inside a red
    credentialed job would be better than that. Failing here would also make an unbuilt
    commit indistinguishable from a role that lost its permission, which is the one
    confusion worth spending a branch to avoid.

    ``pushed_at`` is when the registry accepted the push rather than what the image says
    about itself: an image's own creation time comes from whichever build host produced it,
    so ordering rebuilds by it would order them by unsynchronised clocks.
    """
    answered = _describe(
        (
            "ecr",
            "describe-images",
            "--repository-name",
            ecr_repository,
            "--image-ids",
            f"imageTag={image_tag}",
        ),
        region=region,
        absent=IMAGE_ABSENT_ERROR,
    )
    if answered is None:
        return None
    details = answered.get("imageDetails") if isinstance(answered, dict) else None
    if not isinstance(details, list) or len(details) != 1 or not isinstance(details[0], dict):
        raise CaptureFailedError("published_image_unreadable")
    digest = details[0].get("imageDigest")
    pushed_at = details[0].get("imagePushedAt")
    if not isinstance(digest, str) or not isinstance(pushed_at, str):
        raise CaptureFailedError("published_image_unreadable")
    try:
        taken = datetime.fromisoformat(pushed_at)
    except ValueError as error:
        raise CaptureFailedError("published_image_unreadable") from error
    if taken.tzinfo is None:
        raise CaptureFailedError("published_image_unreadable")
    return digest, taken


def describe_scan_findings(
    *, ecr_repository: str, image_digest: str, region: str, policy: ImageScanPolicy
) -> tuple[ImageScanSummary | None, tuple[ScanFinding, ...] | None]:
    """What the registry's scan found in this digest, or ``None`` for no scan at all.

    **An unscanned image and a scan that is still running are recorded as themselves, never
    as a clean scan.** ECR scans on push and does it asynchronously, so a digest published
    seconds ago answers ``ScanNotFoundException`` and one published a minute ago answers
    ``IN_PROGRESS`` -- and a zero-count summary would say that somebody looked and found
    nothing, which is a different and stronger claim than either. ``image_scan_is_reviewed``
    reads an absent summary, and an incomplete one, as nobody having looked; that is the
    fail-closed direction and the one this preserves.

    Every other failure is a failure. Folding a refused lookup into the absent branch would
    be safe for the run and silent about the role: a permission this workflow lost would
    surface as every submission suddenly needing a recorded exception, with nothing anywhere
    pointing at the cause.
    """
    answered = _describe(
        (
            "ecr",
            "describe-image-scan-findings",
            "--repository-name",
            ecr_repository,
            "--image-id",
            f"imageDigest={image_digest}",
        ),
        region=region,
        absent=SCAN_ABSENT_ERROR,
    )
    if answered is None:
        return None, None
    # The same two mappings the admission validator applies to the same call, rather than a
    # second pair. Two readings of `findingSeverityCounts` would be two chances to disagree
    # about what an omitted severity means, and admission re-derives this summary and fails
    # closed when the two disagree -- so a disagreement here is a submission that passes the
    # gate and is then refused inside AWS for a reason neither side can explain.
    #
    # The findings travel beside the counts because the gate needs both: the counts say how
    # many blocking findings there are, and the list says which, so a review recorded against
    # a vulnerability can be matched to it. Sending one without the other is what the count
    # guard in image_scan_is_reviewed refuses.
    return image_scan_summary_from_ecr(answered), blocking_findings_from_ecr(
        answered, policy=policy
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True, type=Path)
    # The registry key, never the ECR repository name. This role may describe every
    # sbsandbox-intern-edullm-* repository, so a caller-supplied repository name would be a
    # caller-supplied choice of whose images a submission resolves against -- while the
    # manifest went on naming the repository the form declared. The deleted
    # write_image_provenance.py took the name from the registry for the same reason.
    parser.add_argument("--repository", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--aws-region", required=True)
    # The same policy the compile job and the admission validator read, so that what counts
    # as a blocking finding is decided once. Passed rather than defaulted to a path, because
    # a tool that knew where the reviewed configuration lived could be pointed at a
    # different copy of it by being run from a different directory.
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    if COMMIT_SHA.fullmatch(arguments.commit_sha) is None:
        # Refused before a role is spent on it. The tag is twelve characters of whatever
        # this is handed, so a branch name resolves to a tag nobody published and comes back
        # as a commit with no image -- a refusal that sends the submitter to build something
        # that may already be built. RunManifest holds the same pattern, so nothing refused
        # here would have compiled anyway.
        print("commit_sha_unusable", file=sys.stderr)
        return EXIT_UNUSABLE

    try:
        registry = load_registry(arguments.registry)
    except RegistryUnreadableError as exc:
        print(exc.reason, file=sys.stderr)
        return EXIT_UNUSABLE

    try:
        registered = registry.repository_by_name(arguments.repository)
    except UnknownRepositoryError:
        # The token first, because the workflow greps the first line, and a sentence after
        # it, because the token is the name of a condition the code is in rather than one
        # the submitter is in. Everything a reader needs to act is here: what they asked
        # for, what exists, and the file that decides. Naming the registered repositories
        # also answers the likeliest cause, which is a spelling -- `olmo-core` for
        # `OLMo-core` reaches this line and looks like an unregistered repository.
        registered_names = ", ".join(
            entry.repository for entry in registry.repositories
        )
        print("unregistered_repository", file=sys.stderr)
        print(
            f"No repository named {arguments.repository!r} is registered, so there is "
            "nowhere for its images to have been published and nothing to resolve. The "
            f"registered repositories are {registered_names}. Register this one by adding "
            "it to config/repositories.yaml, or correct the repository on the form.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    try:
        image_scan_policy = load_yaml(arguments.policy, ApprovalPolicy).image_scan
    except (OSError, ValidationError, TypeError):
        # The reason is not echoed. A pydantic error quotes the input it rejected, and the
        # input here is reviewed configuration rather than anything a submitter supplied.
        print("policy_unreadable", file=sys.stderr)
        return EXIT_UNUSABLE

    ecr_repository = registered.ecr_repository
    image_tag = arguments.commit_sha[:IMAGE_TAG_LENGTH]
    try:
        published = describe_published_image(
            ecr_repository=ecr_repository, image_tag=image_tag, region=arguments.aws_region
        )
        scan, blocking = (
            (None, None)
            if published is None
            else describe_scan_findings(
                ecr_repository=ecr_repository,
                image_digest=published[0],
                region=arguments.aws_region,
                policy=image_scan_policy,
            )
        )
    except CaptureFailedError as exc:
        print(exc.reason, file=sys.stderr)
        return EXIT_UNUSABLE

    entries: list[dict[str, str]] = (
        []
        if published is None
        else [{"image_digest": published[0], "pushed_at": serialize_utc_timestamp(published[1])}]
    )
    document: dict[str, Any] = {
        "published": entries,
        "image_scan": None if scan is None else scan.model_dump(mode="json"),
        # Null rather than an empty list when the findings could not be read, and the
        # distinction is the whole guard. An empty list means the registry reported nothing
        # at a blocking severity; a null means nobody knows, and the gate refuses that
        # because the count in the summary will not match a list it does not have.
        "blocking_findings": (
            None
            if blocking is None
            else [found.model_dump(mode="json") for found in blocking]
        ),
    }

    # Written plainly rather than through capture_tooling.write_record, which is the write
    # path for evidence that gets committed under fixtures/. This lands under ${RUNNER_TEMP}
    # on a runner and is read by exactly one job, minutes later; nothing about it is
    # reviewed by a person, kept, or compared against a golden. The scan that path performs
    # would also refuse this document for carrying the sixty-four hexadecimal characters it
    # exists to carry.
    try:
        arguments.output.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError:
        print("output_unwritable", file=sys.stderr)
        return EXIT_UNUSABLE

    report(
        {
            "ecr_repository": ecr_repository,
            "image_tag": image_tag,
            "published_images": len(entries),
            "image_scan_status": "absent" if scan is None else scan.status.value,
        }
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
