"""Compile a dispatch form into the manifest policy judges, and say which gate it needs.

Run by the submission workflow's first job, which holds no ``id-token`` permission and
reads no secret. That is the point: the classification that decides which approval gate a
submission goes to is computed before anything in the run can reach AWS, and the workflow
names the gate from this tool's output rather than from the form.

Exit codes follow the repository's convention: 0 compiled, 1 the submission was refused,
2 the inputs could not be read.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from edullm_platform.build_tooling import append_step_outputs
from edullm_platform.canonical import canonical_json_bytes
from edullm_platform.config import load_yaml
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.identity import new_run_id
from edullm_platform.contracts.image import ImageProvenance
from edullm_platform.contracts.image_scan import ImageScanExceptionRegistry
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.policy import ApprovalPolicy
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.submission import (
    SubmissionInputs,
    SubmissionRefusedError,
    compile_submission,
    render_approver_context,
)

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_UNUSABLE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--config-dir", required=True, type=Path)
    parser.add_argument("--submitter", required=True)
    parser.add_argument("--repository-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument(
        "--image-provenance",
        type=Path,
        default=None,
        help=(
            "the provenance record for the image this submission names, which carries the "
            "registry scan summary. Optional, and its absence fails closed: without it the "
            "scan is unknown, and an unknown scan is not a reviewed one."
        ),
    )
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
        inventory = load_yaml(args.config_dir / "organization.yaml", OrganizationInventory)
        catalog = load_yaml(args.config_dir / "workload-catalog.yaml", WorkloadCatalog)
        registry = load_yaml(args.config_dir / "datasets.yaml", DatasetRegistry)
        image_scan_registry = load_yaml(
            args.config_dir / "image-exceptions.yaml", ImageScanExceptionRegistry
        )
    except (OSError, ValidationError, TypeError) as exc:
        print(f"reviewed configuration is unreadable: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE

    # The scan comes from the provenance record because this job holds no AWS credentials
    # and cannot ask ECR. It chooses the approval environment and nothing else: admission
    # re-derives the findings from the registry and fails closed if they disagree, so a
    # provenance record that understated them buys a submitter a gate they still cannot
    # pass. Absent provenance means no summary, which fails closed here too.
    try:
        image_scan_summary = (
            load_yaml(args.image_provenance, ImageProvenance).image_scan
            if args.image_provenance is not None
            else None
        )
    except (OSError, ValidationError, TypeError) as exc:
        print(f"the image provenance record is unreadable: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE

    try:
        submission = compile_submission(
            inputs,
            run_id=args.run_id or new_run_id(),
            policy=policy,
            inventory=inventory,
            catalog=catalog,
            dataset_registry=registry,
            image_scan_registry=image_scan_registry,
            image_scan_summary=image_scan_summary,
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
    }
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.summary is not None:
        args.summary.write_text(
            render_approver_context(
                submission,
                submitter=args.submitter,
                policy=policy,
                repository_url=args.repository_url,
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
