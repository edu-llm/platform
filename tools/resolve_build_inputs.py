"""Emit the registered build inputs for one research repository as step outputs.

The base image reference is the only value here that can silently weaken the supply
chain, so it is re-checked for digest pinning even though the registry contract already
composes it from a validated digest.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from edullm_platform.build_tooling import (
    RegistryUnreadableError,
    UnsafeStepOutputError,
    append_step_outputs,
    load_registry,
    require_output_safe,
)
from edullm_platform.contracts.repository_registry import UnknownRepositoryError

DIGEST_PINNED_PATTERN = re.compile(r"^[^\s:@]+(?:/[^\s:@]+)*@sha256:[0-9a-f]{64}$")

__all__ = [
    "UnsafeStepOutputError",
    "build_parser",
    "main",
    "require_digest_pinned_reference",
    "require_output_safe",
]


def require_digest_pinned_reference(reference: str) -> str:
    if DIGEST_PINNED_PATTERN.fullmatch(reference) is None:
        raise ValueError("base image reference must be pinned to a sha256 digest")
    return reference


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--github-output", type=Path, required=True)
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
        base_reference = require_digest_pinned_reference(registered.immutable_base_reference)
        # The ECR repository name is deliberately absent. The publish job takes it from
        # the gate job's output, so emitting a second copy here would only ever be a
        # second way for the two to disagree.
        pairs = (
            ("base_reference", base_reference),
            ("dockerfile_path", registered.dockerfile_path),
            ("build_context", registered.build_context),
        )
        for name, value in pairs:
            require_output_safe(name, value)
    except ValueError:
        print("invalid_build_inputs", file=sys.stderr)
        return 1

    try:
        append_step_outputs(arguments.github_output, pairs)
    except OSError:
        print("output_unwritable", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
