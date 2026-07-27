"""Record several local builds of one commit side by side, for Phase 1 criterion 2.

The publish workflow cannot produce this comparison and is not meant to: its pre-flight
tag lookup makes a re-run of the same commit resume to the digest already published
rather than build again. So the comparison is made deliberately, outside the shipped
path, and this is what writes it down in a form a test can re-check without Docker.

**What to hand it.** One image configuration blob per build, as JSON, plus a label and a
one-line description of what that build varied. The configuration is what a registry
stores beside the layers; ``docker image save`` writes it into the tarball and the
registry serves it through ``GetDownloadUrlForLayer``, so a locally built image and a
published one can both be recorded here and compared to each other.

**What it refuses.** A build whose configuration is not a JSON object, and a record that
would carry a credential. It does not refuse a build that differs from another in an
unexplained field: deciding that is the test's job, because a difference nobody has
explained is exactly the thing this exercise exists to surface, and a tool that refused
to write it down would make it invisible instead.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from edullm_platform.rebuild_comparison import (
    ConfigurationField,
    LocalRebuildComparison,
    RebuiltImage,
)

__all__ = [
    "InputUnreadableError",
    "flatten",
    "main",
    "read_build",
]


class InputUnreadableError(ValueError):
    """One of the inputs is not something this tool can record."""


def flatten(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    """One leaf per dotted path, values JSON-encoded, in the document's own order.

    Lists are indexed rather than joined, because a layer that moved from position four
    to position five is a different image and a joined list would hide it.
    """
    if isinstance(value, dict):
        return [
            leaf
            for key in sorted(value)
            for leaf in flatten(value[key], f"{prefix}.{key}" if prefix else str(key))
        ]
    if isinstance(value, list):
        return [
            leaf for index, item in enumerate(value) for leaf in flatten(item, f"{prefix}[{index}]")
        ]
    return [(prefix, json.dumps(value))]


def read_build(specification: str) -> RebuiltImage:
    """One ``label=description=path`` triple, read into a record.

    The digest is computed over the file's bytes rather than taken from anywhere, because
    that is exactly what a registry does: the configuration blob's digest is the sha256 of
    the bytes it stores, and recomputing it here means the recorded digest is checkable
    against the registry's without trusting either side's report of it.
    """
    parts = specification.split("=", 2)
    if len(parts) != 3:
        raise InputUnreadableError("a build must be given as label=description=path")
    label, description, raw_path = parts
    path = Path(raw_path)
    try:
        raw = path.read_bytes()
        document = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise InputUnreadableError(f"build_configuration_unreadable:{label}") from exc
    if not isinstance(document, dict):
        raise InputUnreadableError(f"build_configuration_is_not_an_object:{label}")
    return RebuiltImage(
        build=label,
        description=description,
        config_digest="sha256:" + hashlib.sha256(raw).hexdigest(),
        fields=tuple(
            ConfigurationField(path=path_, value=value) for path_, value in flatten(document)
        ),
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record local rebuilds of one commit.")
    parser.add_argument("--source-commit-sha", required=True)
    parser.add_argument("--base-image-digest", required=True)
    parser.add_argument("--dockerfile-path", required=True)
    parser.add_argument("--build-context", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--builder", required=True)
    parser.add_argument(
        "--build",
        action="append",
        required=True,
        metavar="LABEL=DESCRIPTION=PATH",
        help="one image configuration blob, its label and what it varied. Repeatable.",
    )
    parser.add_argument("--performed-at", default=None)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    try:
        comparison = LocalRebuildComparison(
            schema_version=1,
            source_commit_sha=arguments.source_commit_sha,
            base_image_digest=arguments.base_image_digest,
            dockerfile_path=arguments.dockerfile_path,
            build_context=arguments.build_context,
            platform=arguments.platform,
            builder=arguments.builder,
            performed_at=(
                datetime.now(tz=UTC).replace(microsecond=0)
                if arguments.performed_at is None
                else datetime.fromisoformat(arguments.performed_at)
            ),
            builds=tuple(read_build(specification) for specification in arguments.build),
        )
    except InputUnreadableError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (ValidationError, ValueError) as exc:
        print(f"comparison_unrecordable:{type(exc).__name__}", file=sys.stderr)
        return 2
    payload = comparison.model_dump(mode="json", by_alias=True, exclude_none=False)
    try:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError:
        print("comparison_unwritable", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "builds": [build.build for build in comparison.builds],
                "fields": {build.build: len(build.fields) for build in comparison.builds},
                "output": str(arguments.output),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
