"""Name the configuration blob of an image manifest already published to the registry.

``verify_published_image`` needs the labels ``docker build --label`` recorded, and those
live in the image configuration blob rather than in the manifest. The manifest is the only
thing ``BatchGetImage`` returns, so reaching the labels takes two steps: read the config
digest out of the manifest here, then fetch that blob with ``GetDownloadUrlForLayer``.
Both actions are already granted to the publisher role, so the resume path needs neither a
new permission, nor a registry login, nor a full image pull.

Like its siblings it prints only a machine-readable reason: the runner log is world
readable for any public caller repository.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

IMAGE_MANIFEST_MEDIA_TYPES = frozenset(
    {
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
    }
)
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")

__all__ = [
    "IMAGE_MANIFEST_MEDIA_TYPES",
    "ImageManifestError",
    "build_parser",
    "main",
    "read_config_digest",
]


class ImageManifestError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def read_config_digest(payload: object) -> str:
    """Return the configuration blob digest of a single-platform image manifest.

    An index is refused rather than resolved. BatchGetImage is asked for exactly the two
    image-manifest media types, so an index arriving here means the published artifact is
    not the single-platform image this workflow builds, and guessing which entry of it to
    attribute the provenance record to is the sort of assumption this gate exists to stop.
    """
    if not isinstance(payload, dict):
        raise ImageManifestError("manifest_malformed")
    if payload.get("schemaVersion") != 2:
        raise ImageManifestError("manifest_schema_unsupported")

    media_type = payload.get("mediaType")
    # An OCI manifest may omit mediaType; a declared one has to be an image manifest.
    if media_type is not None and media_type not in IMAGE_MANIFEST_MEDIA_TYPES:
        raise ImageManifestError("manifest_media_type_unsupported")
    if "manifests" in payload:
        raise ImageManifestError("manifest_is_a_list")

    config = payload.get("config")
    if not isinstance(config, dict):
        raise ImageManifestError("manifest_without_config")
    digest = config.get("digest")
    if not isinstance(digest, str) or DIGEST_PATTERN.fullmatch(digest) is None:
        raise ImageManifestError("manifest_config_digest_malformed")
    return digest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    try:
        text = arguments.manifest.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print("manifest_undecodable", file=sys.stderr)
        return 2
    except OSError:
        print("manifest_unreadable", file=sys.stderr)
        return 2

    try:
        digest = read_config_digest(json.loads(text))
    except json.JSONDecodeError:
        print("manifest_malformed", file=sys.stderr)
        return 1
    except ImageManifestError as exc:
        print(exc.reason, file=sys.stderr)
        return 1

    arguments.output.write_text(f"{digest}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
