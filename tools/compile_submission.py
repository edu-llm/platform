"""Compile a dispatch form into the manifest policy judges, and say which gate it needs.

Run by the submission workflow's compile job, which holds no ``id-token`` permission and
reads no secret. That is the point: the classification that decides which approval gate a
submission goes to is computed before this job can reach AWS, and the workflow names the
gate from this tool's output rather than from the form.

Everything the account has to be asked arrives as a file. The resolve job holds a role that
may describe images and their scan findings, writes down what the registry answered for the
declared commit, and hands it over as an artifact -- so this job reads a document rather
than a registry, and keeps the property that makes its verdict worth anything.

Exit codes follow the repository's convention: 0 compiled, 1 the submission was refused,
2 the inputs could not be read.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from edullm_platform.build_tooling import append_step_outputs
from edullm_platform.canonical import canonical_json_bytes
from edullm_platform.config import load_yaml
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.identity import new_run_id
from edullm_platform.contracts.image_scan import (
    ImageScanExceptionRegistry,
    ImageScanSummary,
    ScanFinding,
)
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.policy import ApprovalPolicy
from edullm_platform.contracts.repository_registry import RepositoryRegistry
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.errors import SubmissionRefusedError
from edullm_platform.image_resolution import PublishedImage
from edullm_platform.submission import (
    SubmissionInputs,
    compile_submission,
    render_approver_context,
    require_registered_repository,
    require_submitter_on_the_roster,
)

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_UNUSABLE = 2


class ResolvedImagesUnreadableError(ValueError):
    """The resolve job's answer is not one this job can act on.

    Its own error rather than a bare ``ValueError`` because the caller has to tell it from
    a refusal: an answer nobody could read is not a submission anybody would decline, and
    the workflow prints a different sentence for each.
    """


def read_published_images(
    document: object,
) -> tuple[list[PublishedImage], ImageScanSummary | None, tuple[ScanFinding, ...] | None]:
    """What ``tools/resolve_published_image.py`` wrote, read back into what compiling needs.

    Nothing here is skipped or defaulted on a malformed entry. Dropping one silently turns a
    broken resolve into a commit with fewer published images, and the shortest way for that
    to end is a run resolved onto an older image than the one it should have used -- which
    is invisible in the record afterwards, because a rebuild legitimately looks like that.
    """
    if not isinstance(document, dict):
        raise ResolvedImagesUnreadableError("the resolved-images document is not an object")
    entries = document.get("published")
    if not isinstance(entries, list):
        raise ResolvedImagesUnreadableError("the resolved-images document lists no published key")

    published: list[PublishedImage] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ResolvedImagesUnreadableError("a published entry is not an object")
        digest = entry.get("image_digest")
        pushed_at = entry.get("pushed_at")
        if not isinstance(digest, str) or not isinstance(pushed_at, str):
            raise ResolvedImagesUnreadableError("a published entry names no digest or no instant")
        try:
            taken = datetime.fromisoformat(pushed_at)
        except ValueError as exc:
            raise ResolvedImagesUnreadableError(f"{pushed_at!r} is not an instant") from exc
        # An instant with no offset would be read as local time by whatever compared it,
        # and ordering rebuilds is the one thing this value is for.
        if taken.tzinfo is None:
            raise ResolvedImagesUnreadableError(f"{pushed_at!r} carries no UTC offset")
        published.append(PublishedImage(image_digest=digest, pushed_at=taken))

    scan = document.get("image_scan")
    summary = None
    if scan is not None:
        try:
            summary = ImageScanSummary.model_validate(scan)
        except ValidationError as exc:
            raise ResolvedImagesUnreadableError(
                f"the recorded scan summary is not one: {exc}"
            ) from exc

    # Absent and empty are different answers. A missing key or a null means the findings
    # could not be read, and the gate refuses that because the count in the summary will not
    # match a list it does not have. An empty list means the registry reported nothing at a
    # blocking severity, which is a pass. Reading one as the other is the only way this file
    # could open the gate rather than close it.
    recorded = document.get("blocking_findings")
    if recorded is None:
        return published, summary, None
    if not isinstance(recorded, list):
        raise ResolvedImagesUnreadableError("blocking_findings is not a list")
    try:
        findings = tuple(ScanFinding.model_validate(entry) for entry in recorded)
    except ValidationError as exc:
        raise ResolvedImagesUnreadableError(f"a recorded finding is not one: {exc}") from exc
    return published, summary, findings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--config-dir", required=True, type=Path)
    # Required rather than defaulted to nothing published. Nothing published is a real
    # answer with a refusal attached -- "build the commit before submitting it" -- so a
    # default that spelled "I was not told" the same way would report every submission as
    # an unbuilt commit the first time the workflow forgot to pass the file.
    parser.add_argument("--published-images", required=True, type=Path)
    parser.add_argument("--submitter", required=True)
    parser.add_argument("--repository-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument(
        "--run-id",
        help=(
            "Reuse an existing logical run id instead of minting one. The id keys the S3 "
            "records and names the Step Functions execution, so a re-run that minted a "
            "fresh one would defeat both deduplication mechanisms at once."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        payload = json.loads(args.inputs.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"submission inputs are unreadable: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE

    try:
        inputs = SubmissionInputs.model_validate(payload)
    except ValidationError as exc:
        print(f"the submission form is not valid: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE

    try:
        policy = load_yaml(args.config_dir / "policy.yaml", ApprovalPolicy)
        repositories = load_yaml(args.config_dir / "repositories.yaml", RepositoryRegistry)
        catalog = load_yaml(args.config_dir / "workload-catalog.yaml", WorkloadCatalog)
        registry = load_yaml(args.config_dir / "datasets.yaml", DatasetRegistry)
        image_scan_registry = load_yaml(
            args.config_dir / "image-exceptions.yaml", ImageScanExceptionRegistry
        )
        # Read for two things, and admission resolves both independently from its own copy,
        # because what a run is labelled with must not depend on a file the compile job
        # could be pointed at. Whether this submitter is on the roster at all, which
        # admission answers only after a lead has released the gate. And whether their runs
        # can be attributed in W&B, so the approver context can say before the gate what
        # W&B will not say after it.
        inventory = load_yaml(args.config_dir / "organization.yaml", OrganizationInventory)
    except (OSError, ValidationError, TypeError) as exc:
        print(f"reviewed configuration is unreadable: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE

    try:
        resolved = json.loads(args.published_images.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"the resolved images are unreadable: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE

    try:
        published_images, image_scan_summary, blocking_findings = read_published_images(resolved)
    except ResolvedImagesUnreadableError as exc:
        print(f"the resolved images are unreadable: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE

    # The scan summary is what chooses the gate, and it is read here from a job that holds
    # an ECR role rather than derived here from one that holds nothing. That is a real
    # relaxation of "the gate is chosen before the run reaches AWS", and the compensating
    # control is the one this file already described: admission re-derives the findings
    # from the registry itself and fails closed on disagreement, so an understated summary
    # buys a submitter a gate they still cannot pass. The gate chosen here is the floor and
    # never the ceiling. infra/iam/image-resolver-role.yaml carries the argument in full.
    try:
        # Before compiling rather than after, so that somebody the roster does not name is
        # told that and nothing else. A refusal naming a workload profile would send them
        # to correct a field that was never what stood in the way.
        require_submitter_on_the_roster(args.submitter, inventory=inventory)
        # And for the same reason, one field further along. Compiling refuses an
        # unregistered repository too, through the registry fact policy denies outright,
        # but only once the workload profile has been checked against it -- so for a
        # repository with no profile at all the refusal names the profile instead. Asked
        # here, the submitter is told which field is wrong and where the list lives.
        require_registered_repository(inputs.repository, repositories=repositories)
        submission = compile_submission(
            inputs,
            run_id=args.run_id or new_run_id(),
            policy=policy,
            repositories=repositories,
            catalog=catalog,
            dataset_registry=registry,
            image_scan_registry=image_scan_registry,
            image_scan_summary=image_scan_summary,
            image_scan_findings=blocking_findings,
            published_images=published_images,
        )
    except SubmissionRefusedError as exc:
        print(f"submission refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except ValidationError as exc:
        print(f"the submission does not compile into a valid manifest: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    document = {
        "run_id": submission.run_id,
        "submitter": args.submitter,
        "approval_class": submission.approval_class.value,
        "approving_environment": submission.approving_environment.value,
        "manifest_sha256": submission.manifest_sha256,
        "manifest": json.loads(canonical_json_bytes(submission.manifest)),
        # A sibling of the manifest and deliberately not a key inside it: the digest above
        # is what an approver releases, and a field folded into the hashed document changes
        # the digest of every record written before that field existed.
        "experiment": submission.experiment,
    }
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.summary is not None:
        args.summary.write_text(
            render_approver_context(
                submission,
                submitter=args.submitter,
                policy=policy,
                repository_url=args.repository_url,
                inventory=inventory,
                wandb_username=inventory.wandb_username_for(args.submitter),
            ),
            encoding="utf-8",
        )

    if args.github_output is not None:
        append_step_outputs(
            args.github_output,
            (
                ("run_id", submission.run_id),
                ("approval_class", submission.approval_class.value),
                ("environment", submission.approving_environment.value),
                ("manifest_sha256", submission.manifest_sha256),
            ),
        )

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
