"""Capture what Phase 5's pilot runs left behind. Read-only.

Three targets, and they read three different systems for one reason each.

``run``
    The lineage store and the scheduler, joined on a run id. This is the only capture in the
    repository that spans two systems in one record, and it does so because the central
    Phase 5 claim is a comparison between them: the digest written immutably into lineage
    against the digest the container was actually given. Reading them into two records would
    put the two halves of one assertion in two files that nothing requires to be about the
    same run.

``published-image``
    The registry, for the commit a run declared. The build workflow tags an image with the
    first twelve characters of its commit, so the tag is what ties a digest to a commit and
    is captured rather than assumed.

``branch-protection``
    GitHub, for the containment that had to land in the same change as the write grant.

Everything writes through :mod:`edullm_platform.capture_tooling`, which owns the refusal to
write outside the working directory and the scan that runs before any record lands. Nothing
here writes a file by itself, and nothing here decides whether a criterion passes -- a
capture that judged its own evidence would be a capture nobody could disagree with.

**The account id is never written.** ``account_identity`` is read so that a captured ARN
naming this account can be distinguished from one naming another, and the id itself goes
nowhere near a record: the digests and identifiers these records carry are the point, and
the account is not one of them.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from edullm_platform.capture_tooling import (
    CaptureFailedError,
    account_identity,
    aws_json,
    observed_now,
    report,
    run_capture,
    write_model,
)
from edullm_platform.phase5_capture import (
    ADMITTED_RUN_RECORD,
    BRANCH_PROTECTION_RECORD,
    PUBLISHED_IMAGE_RECORD,
)
from edullm_platform.phase5_evidence import (
    AdmittedRunEvidence,
    BranchProtectionEvidence,
    PublishedImageEvidence,
    RunAuthorizationEvidence,
)

#: The capture must land under the phase's working directory. ``capture_tooling`` enforces
#: it; this is the suffix it enforces against.
ALLOWED_OUTPUT_SUFFIX: Final = Path("phase-5")

LINEAGE_BUCKET: Final = "sbsandbox-intern-edullm-lineage"
REPOSITORY_SLUG: Final = "edu-llm/platform"


def _gh(*arguments: str) -> Any:
    """One ``gh api`` call, parsed. A non-zero exit is a capture failure, never an empty one.

    Not wrapped in ``capture_tooling``, for the reason recorded in that module: what a ``gh``
    failure prints is the service's own stderr, which is the whole value of the message to
    an operator whose call was refused, and a machine-readable token would throw it away.
    """
    completed = subprocess.run(
        ["gh", "api", *arguments], capture_output=True, text=True, check=False, shell=False
    )
    if completed.returncode != 0:
        raise CaptureFailedError(
            f"gh_api_failed:{arguments[0] if arguments else 'no_path'}"
        )
    try:
        return json.loads(completed.stdout)
    except ValueError as error:
        raise CaptureFailedError("gh_answer_unreadable") from error


def _lineage_object(key: str, *, profile: str, region: str) -> Any | None:
    """One lineage record, or None if the store does not hold it.

    Absence is an answer here rather than a failure, and it is load-bearing: a run that was
    admitted and never started has no result record, and reading that as a capture failure
    would make the failed pilot run uncapturable -- which is the one run that establishes
    what a failure looks like.
    """
    completed = subprocess.run(
        [
            "aws", "s3api", "get-object",
            "--bucket", LINEAGE_BUCKET,
            "--key", key,
            "--profile", profile,
            "--region", region,
            "/dev/stdout",
        ],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        if "NoSuchKey" in completed.stderr or "Not Found" in completed.stderr:
            return None
        raise CaptureFailedError(f"lineage_read_failed:{key.split('/')[0]}")
    body = completed.stdout
    # get-object writes the body followed by its own JSON summary of the download. Only the
    # first document is the record.
    decoder = json.JSONDecoder()
    try:
        document, _ = decoder.raw_decode(body.lstrip())
    except ValueError as error:
        raise CaptureFailedError(f"lineage_record_unreadable:{key}") from error
    return document


def _recorded_states(run_id: str, *, profile: str, region: str) -> tuple[str, ...]:
    """Every lifecycle state the store recorded, oldest first.

    Ordered by the event's own ``occurred_at`` rather than by key, because the event ids are
    random and sorting by them would produce a sequence that looks like a lifecycle and is
    not one.
    """
    listing = aws_json(
        [
            "s3api", "list-objects-v2",
            "--bucket", LINEAGE_BUCKET,
            "--prefix", f"events/{run_id}/",
        ],
        profile=profile,
        region=region,
    )
    events = []
    for entry in listing.get("Contents", ()):
        record = _lineage_object(entry["Key"], profile=profile, region=region)
        if record is not None:
            events.append(record)
    events.sort(key=lambda event: event.get("occurred_at", ""))
    return tuple(str(event.get("state", "")) for event in events)


def _batch_job(job_id: str, *, profile: str, region: str) -> dict[str, Any] | None:
    answer = aws_json(["batch", "describe-jobs", "--jobs", job_id], profile=profile, region=region)
    jobs = answer.get("jobs") or []
    if not jobs:
        return None
    job = jobs[0]
    return job if isinstance(job, dict) else None


def _digest_from_image_reference(reference: str | None) -> str | None:
    """The digest out of ``registry/repository@sha256:...``, or None if it is tag-pinned.

    A tag-pinned reference is not a failure to parse; it is the account telling us the
    container was selected by something other than a digest, which is exactly the state
    item 5.3 exists to end. Returning None rather than raising lets the record say so.
    """
    if reference is None or "@" not in reference:
        return None
    digest = reference.rsplit("@", 1)[1]
    return digest if digest.startswith("sha256:") else None


def capture_run(run_id: str, *, profile: str, region: str) -> AdmittedRunEvidence:
    """One pilot run, joined across the lineage store and the scheduler."""
    account_identity(profile=profile, region=region)

    intent = _lineage_object(f"intent/{run_id}.json", profile=profile, region=region)
    decision = _lineage_object(f"decision/{run_id}.json", profile=profile, region=region)
    if intent is None or decision is None:
        raise CaptureFailedError(f"run_not_in_lineage:{run_id}")
    binding = _lineage_object(f"binding/{run_id}.json", profile=profile, region=region)
    result = _lineage_object(f"result/{run_id}.json", profile=profile, region=region)

    manifest = intent["manifest"]
    workflow = intent["workflow_run"]
    authorization = decision["authorization"]

    job: dict[str, Any] | None = None
    batch_job_id = binding.get("batch_job_id") if binding else None
    if batch_job_id:
        job = _batch_job(batch_job_id, profile=profile, region=region)

    container = (job or {}).get("container") or {}
    job_definition = (job or {}).get("jobDefinition")
    if isinstance(job_definition, str) and "/" in job_definition:
        job_definition = job_definition.rsplit("/", 1)[1]

    return AdmittedRunEvidence(
        observed_at=_recorded_at(decision["recorded_at"]),
        source="aws",
        environment="sandbox",
        region=region,
        run_id=run_id,
        submitter=intent["submitter"],
        workflow_run_id=int(workflow["run_id"]),
        workflow_path=workflow["workflow_path"],
        workflow_ref=workflow["workflow_ref"],
        manifest_sha256=intent["manifest_sha256"],
        declared_commit_sha=manifest["commit_sha"],
        declared_image_digest=manifest["image_digest"],
        repository=manifest["repository"],
        team=manifest["team"],
        compute_profile=manifest["compute_profile"],
        workload_profile=manifest["workload_profile"],
        authorization=RunAuthorizationEvidence(
            approval_class=authorization["approval_class"],
            approval_scope=authorization["approval_scope"],
            approver=authorization["approver"],
            claimed_team=authorization["claimed_team"],
            granted=bool(authorization["granted"]),
            reason=authorization["reason"],
            submitter=authorization["submitter"],
            team_verified=bool(authorization["team_verified"]),
        ),
        batch_job_id=batch_job_id,
        job_definition_name=job_definition if isinstance(job_definition, str) else None,
        container_image_digest=_digest_from_image_reference(container.get("image")),
        scheduler_status=(job or {}).get("status"),
        exit_code=container.get("exitCode"),
        recorded_states=_recorded_states(run_id, profile=profile, region=region),
        result_outcome=(result or {}).get("outcome"),
        output_prefixes=tuple((result or {}).get("output_prefixes") or ()),
        wandb_run=(result or {}).get("wandb_run"),
    )


def _recorded_at(value: str) -> datetime:
    """When admission decided, as the record spells it.

    The decision's own timestamp rather than the moment of capture, because this is a
    ``RecordedEventModel`` and what it records is when the run happened. Capturing it a week
    later must not move the date the evidence is about.
    """
    return datetime.fromisoformat(value).astimezone(UTC)


def capture_published_image(
    *, repository_name: str, commit_sha: str, profile: str, region: str
) -> PublishedImageEvidence:
    """What the registry holds for one commit, and every tag it holds beside it."""
    account_identity(profile=profile, region=region)
    tag = commit_sha[:12]
    described = aws_json(
        [
            "ecr", "describe-images",
            "--repository-name", repository_name,
            "--image-ids", f"imageTag={tag}",
        ],
        profile=profile,
        region=region,
    )
    details = described.get("imageDetails") or []
    if not details:
        raise CaptureFailedError(f"no_published_image_for_commit:{tag}")
    image = details[0]

    everything = aws_json(
        ["ecr", "describe-images", "--repository-name", repository_name],
        profile=profile,
        region=region,
    )
    tags = sorted(
        {
            one_tag
            for entry in everything.get("imageDetails") or []
            for one_tag in entry.get("imageTags") or ()
        }
    )
    return PublishedImageEvidence(
        observed_at=observed_now(),
        source="aws",
        environment="sandbox",
        region=region,
        repository_name=repository_name,
        commit_sha=commit_sha,
        image_tag=tag,
        image_digest=image["imageDigest"],
        pushed_at=datetime.fromisoformat(str(image["imagePushedAt"])).astimezone(UTC),
        published_tags=tuple(tags),
    )


def capture_branch_protection(*, branch: str) -> BranchProtectionEvidence:
    """How the default branch is protected, read from GitHub rather than from a document."""
    protection = _gh(f"repos/{REPOSITORY_SLUG}/branches/{branch}/protection")
    reviews = protection.get("required_pull_request_reviews") or {}
    checks = protection.get("required_status_checks") or {}
    organization, repository = REPOSITORY_SLUG.split("/", 1)
    return BranchProtectionEvidence(
        observed_at=observed_now(),
        source="github",
        environment="sandbox",
        organization=organization,
        repository=repository,
        branch=branch,
        required_approving_review_count=int(reviews.get("required_approving_review_count", 0)),
        require_code_owner_reviews=bool(reviews.get("require_code_owner_reviews", False)),
        dismiss_stale_reviews=bool(reviews.get("dismiss_stale_reviews", False)),
        enforce_admins=bool((protection.get("enforce_admins") or {}).get("enabled", False)),
        allow_force_pushes=bool((protection.get("allow_force_pushes") or {}).get("enabled", False)),
        allow_deletions=bool((protection.get("allow_deletions") or {}).get("enabled", False)),
        required_conversation_resolution=bool(
            (protection.get("required_conversation_resolution") or {}).get("enabled", False)
        ),
        required_status_checks=tuple(sorted(checks.get("contexts") or ())),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture what Phase 5's pilot runs left behind. Read-only."
    )
    parser.add_argument("--aws-profile", default="sbsandbox")
    parser.add_argument("--aws-region", default="us-east-1")
    parser.add_argument("--target", choices=["run", "published-image", "branch-protection"], required=True)
    parser.add_argument(
        "--run-id",
        action="append",
        default=[],
        help=(
            "repeatable; required by --target run. Named rather than discovered, because "
            "which runs are worth committing as evidence is a judgement somebody makes in "
            "writing."
        ),
    )
    parser.add_argument("--repository-name", default="sbsandbox-intern-edullm-olmo-core")
    parser.add_argument("--commit-sha", help="required by --target published-image")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def _target(arguments: argparse.Namespace) -> int:
    written: list[str] = []
    summary: dict[str, Any] = {"target": arguments.target}

    if arguments.target == "run":
        if not arguments.run_id:
            raise CaptureFailedError("run_id_required")
        for run_id in arguments.run_id:
            record = capture_run(
                run_id, profile=arguments.aws_profile, region=arguments.aws_region
            )
            destination = arguments.output_dir / "runs" / run_id / ADMITTED_RUN_RECORD
            write_model(destination, record, allow_content_digests=True)
            written.append(f"runs/{run_id}/{ADMITTED_RUN_RECORD}")
            # Reported rather than enforced. A capture of a run whose container never
            # started is a true record of a run that never started, and the tool's job is
            # to say which one it took rather than to refuse the inconvenient one.
            summary[run_id] = {
                "released_by_another_person": record.released_by_another_person,
                "image_that_ran_is_the_image_admitted": record.image_that_ran_is_the_image_admitted,
                "result_outcome": record.result_outcome,
            }
    elif arguments.target == "published-image":
        if not arguments.commit_sha:
            raise CaptureFailedError("commit_sha_required")
        image = capture_published_image(
            repository_name=arguments.repository_name,
            commit_sha=arguments.commit_sha,
            profile=arguments.aws_profile,
            region=arguments.aws_region,
        )
        write_model(
            arguments.output_dir / PUBLISHED_IMAGE_RECORD, image, allow_content_digests=True
        )
        written.append(PUBLISHED_IMAGE_RECORD)
        summary["published_tags"] = len(image.published_tags)
    else:
        protection = capture_branch_protection(branch=arguments.branch)
        write_model(arguments.output_dir / BRANCH_PROTECTION_RECORD, protection)
        written.append(BRANCH_PROTECTION_RECORD)
        summary["enforce_admins"] = protection.enforce_admins

    summary["written"] = sorted(written)
    report(summary)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    return run_capture(
        lambda: _target(arguments),
        output_dir=arguments.output_dir,
        allowed_suffix=ALLOWED_OUTPUT_SUFFIX,
    )


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
