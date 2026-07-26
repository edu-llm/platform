"""Verify that a research checkout is a clean, pushed commit on a registered branch.

Runs in the credential-free gate job. It never receives AWS credentials, and it prints
only the machine-readable rejection reason so a public runner log cannot become a
reconnaissance channel.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from edullm_platform.build_tooling import (
    RegistryUnreadableError,
    UnsafeStepOutputError,
    append_step_outputs,
    load_registry,
)
from edullm_platform.canonical import canonical_json_bytes
from edullm_platform.contracts.source_identity import (
    SourceIdentityError,
    verify_source_identity,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--github-repository-id", type=int, required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--github-output", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    try:
        registry = load_registry(arguments.registry)
    except RegistryUnreadableError as exc:
        print(exc.reason, file=sys.stderr)
        return 2

    try:
        identity = verify_source_identity(
            repository=arguments.repository,
            github_repository_id=arguments.github_repository_id,
            ref=arguments.ref,
            commit_sha=arguments.commit_sha,
            repository_root=arguments.repository_root,
            registry=registry,
        )
    except SourceIdentityError as exc:
        print(exc.reason.value, file=sys.stderr)
        return 1

    registered = registry.repository_by_name(identity.repository)
    try:
        arguments.output.write_bytes(canonical_json_bytes(identity) + b"\n")
        if arguments.github_output is not None:
            append_step_outputs(
                arguments.github_output,
                (
                    ("commit_sha", identity.commit_sha),
                    ("ecr_repository", registered.ecr_repository),
                ),
            )
    except UnsafeStepOutputError:
        print("unsafe_step_output", file=sys.stderr)
        return 1
    except OSError:
        print("output_unwritable", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
