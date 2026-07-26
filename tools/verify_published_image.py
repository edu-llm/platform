"""Require an already published image to be the one this run would have built.

The pre-flight lookup makes a re-run resume instead of colliding with an immutable tag,
but the tag encodes only the commit while the provenance record re-derives everything else
at write time: ``base_image_digest`` is read from ``config/repositories.yaml`` as it stands
now. Resuming an older commit after the registered base digest changed would therefore
record the new base for an image built from the old one — the same unverified assertion
``verify_dockerfile_base`` exists to prevent, arriving by another road, and invisible to
that gate because it inspects the checkout rather than the image.

Comparing the labels ``docker build`` recorded also closes the twelve-hex-character tag
collision: before the resume path existed the immutable push rejected a colliding commit
loudly, and the revision label is now what does that instead.

Like its siblings it prints only a machine-readable reason: the runner log is world
readable for any public caller repository.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

BASE_NAME_LABEL = "org.opencontainers.image.base.name"
REVISION_LABEL = "org.opencontainers.image.revision"

__all__ = [
    "BASE_NAME_LABEL",
    "REVISION_LABEL",
    "PublishedImageError",
    "build_parser",
    "main",
    "require_matching_labels",
]


class PublishedImageError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def require_matching_labels(payload: object, *, base_reference: str, commit_sha: str) -> None:
    """Raise unless the published image carries the labels this run would have set.

    Only the two labels this run can predict are compared. ``edullm.workflow.run.url``
    differs by construction — it is why a retry cannot reproduce the manifest digest, and
    so why the resume path exists at all.
    """
    if not isinstance(payload, dict):
        raise PublishedImageError("image_config_malformed")
    config = payload.get("config")
    if not isinstance(config, dict):
        raise PublishedImageError("image_config_malformed")

    labels = config.get("Labels")
    if not isinstance(labels, dict):
        raise PublishedImageError("published_image_unlabelled")

    for label, expected, reason in (
        (BASE_NAME_LABEL, base_reference, "published_base_image_mismatch"),
        (REVISION_LABEL, commit_sha, "published_revision_mismatch"),
    ):
        actual = labels.get(label)
        # An image that cannot be shown to match has to read the same as one that does
        # not match: this gate only ever answers "proven equivalent" or "stop".
        if not isinstance(actual, str):
            raise PublishedImageError("published_image_unlabelled")
        if actual != expected:
            raise PublishedImageError(reason)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-config", type=Path, required=True)
    parser.add_argument("--base-reference", required=True)
    parser.add_argument("--commit-sha", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    try:
        text = arguments.image_config.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print("image_config_undecodable", file=sys.stderr)
        return 2
    except OSError:
        print("image_config_unreadable", file=sys.stderr)
        return 2

    try:
        require_matching_labels(
            json.loads(text),
            base_reference=arguments.base_reference,
            commit_sha=arguments.commit_sha,
        )
    except json.JSONDecodeError:
        print("image_config_malformed", file=sys.stderr)
        return 1
    except PublishedImageError as exc:
        print(exc.reason, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
