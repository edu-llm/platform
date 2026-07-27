"""Recompute the manifest hash after approval and refuse any difference.

An environment gate releases a job, not its content. Nothing in GitHub prevents the
manifest a job submits from differing from the one a reviewer read, so the value the
compile job published is recomputed here, on the other side of the gate, from the artifact
the submit job actually holds.

This is a tripwire rather than a formality, and it is one only because every input is
pinned: the dispatch inputs are immutable for the life of the run and the checkout is
pinned to ``github.sha``, so the two hashes must agree by construction. A difference
therefore means something moved that should not have, rather than that a default drifted.

Admission recomputes it a third time inside AWS, because this check runs on a machine the
platform does not trust to report its own result honestly. Passing here is convenience —
failing fast with a legible message instead of a state-machine failure — and the
enforcement that counts happens where the caller cannot reach.

Exit codes: 0 the manifest matches, 1 it does not, 2 the inputs could not be read.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from edullm_platform.canonical import sha256_digest
from edullm_platform.contracts.manifest import RunManifest

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_UNUSABLE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submission", required=True, type=Path)
    parser.add_argument(
        "--approved-sha256",
        required=True,
        help="The digest the compile job published, carried across the approval gate.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        document = json.loads(args.submission.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"the compiled submission is unreadable: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE

    if not isinstance(document, dict) or "manifest" not in document:
        print("the compiled submission does not contain a manifest", file=sys.stderr)
        return EXIT_UNUSABLE

    try:
        manifest = RunManifest.model_validate(document["manifest"])
    except ValidationError as exc:
        print(f"the compiled submission does not hold a valid manifest: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE

    recomputed = sha256_digest(manifest)
    recorded = document.get("manifest_sha256")

    if recomputed != args.approved_sha256:
        print(
            "the manifest does not hash to the value that was approved; refusing to submit.\n"
            f"  approved:   {args.approved_sha256}\n"
            f"  recomputed: {recomputed}",
            file=sys.stderr,
        )
        return EXIT_MISMATCH

    if recorded != recomputed:
        print(
            "the compiled submission's own recorded digest disagrees with its manifest; "
            "the document has been edited since it was written.\n"
            f"  recorded:   {recorded}\n"
            f"  recomputed: {recomputed}",
            file=sys.stderr,
        )
        return EXIT_MISMATCH

    print(f"manifest matches the approved digest {recomputed}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
