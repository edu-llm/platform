"""Assemble and write the immutable provenance record for one published image.

The ECR repository and base image digest are taken from the registry rather than from
the workflow, so a caller cannot describe its image as something the platform never
registered. No registry host or account identifier is persisted: callers compose the
pullable reference with ``resolve_image_reference`` at point of use.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from edullm_platform.build_tooling import RegistryUnreadableError, load_registry
from edullm_platform.canonical import canonical_json_bytes
from edullm_platform.contracts.base import serialize_utc_timestamp
from edullm_platform.contracts.image import ImageProvenance
from edullm_platform.contracts.repository_registry import UnknownRepositoryError
from edullm_platform.contracts.source_identity import SourceIdentity


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-identity", type=Path, required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--run-repository", required=True)
    parser.add_argument("--workflow-repository", required=True)
    parser.add_argument("--workflow-path", required=True)
    parser.add_argument("--workflow-ref", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--built-at", default=None)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    try:
        registry = load_registry(arguments.registry)
    except RegistryUnreadableError as exc:
        print(exc.reason, file=sys.stderr)
        return 2

    try:
        registered = registry.repository_by_name(arguments.repository)
    except UnknownRepositoryError:
        print("unregistered_repository", file=sys.stderr)
        return 1

    try:
        identity_payload = json.loads(arguments.source_identity.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("source_identity_unreadable", file=sys.stderr)
        return 2

    try:
        identity = SourceIdentity.model_validate(identity_payload)
    except ValidationError:
        print("invalid_source_identity", file=sys.stderr)
        return 1

    if identity.repository != arguments.repository:
        print("repository_mismatch", file=sys.stderr)
        return 1

    built_at = arguments.built_at or serialize_utc_timestamp(datetime.now(tz=UTC))
    try:
        provenance = ImageProvenance.model_validate(
            {
                "schema_version": 1,
                "ecr_repository": registered.ecr_repository,
                "image_digest": arguments.image_digest,
                "base_image_digest": registered.base_image_digest,
                "source": identity.model_dump(mode="json"),
                "workflow_run": {
                    "run_repository": arguments.run_repository,
                    "workflow_repository": arguments.workflow_repository,
                    "workflow_path": arguments.workflow_path,
                    "workflow_ref": arguments.workflow_ref,
                    "run_id": arguments.run_id,
                    "run_attempt": arguments.run_attempt,
                },
                "built_at": built_at,
            }
        )
    except ValidationError:
        print("invalid_provenance", file=sys.stderr)
        return 1

    try:
        arguments.output.write_bytes(canonical_json_bytes(provenance) + b"\n")
    except OSError:
        print("output_unwritable", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
