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
    SourceIdentityReason,
    verify_source_identity,
)

#: What to do about it, for the refusals a person can act on. Not every reason gets one: a
#: registry mismatch or a malformed ref is a defect somebody has to look at, and inventing a
#: remedy for it would send the reader somewhere wrong with confidence.
#:
#: The first entry is here because a pilot user hit it on their first build. They pushed a
#: commit and then re-ran the previous dispatch rather than starting a new one, so the run
#: rebuilt an older commit while the branch had moved -- which is exactly what this guard is
#: for, and the page said only `remote_ref_mismatch`.
REMEDIES: dict[SourceIdentityReason, str] = {
    SourceIdentityReason.REMOTE_REF_MISMATCH: (
        "The branch has moved since this run was started, so this build would publish an "
        "image for a commit that is no longer the branch head. Re-running an earlier "
        "dispatch does this: a re-run replays the commit the original run was given. Start "
        "a new run of this workflow instead of re-running an old one."
    ),
    SourceIdentityReason.REMOTE_REF_MISSING: (
        "The branch this run names does not exist on the remote. Push it before building "
        "from it."
    ),
    SourceIdentityReason.DIRTY_TREE: (
        "The checkout has uncommitted changes, so what would be published is not what any "
        "commit contains. Commit and push them, then build the pushed commit."
    ),
    SourceIdentityReason.HEAD_MISMATCH: (
        "The checkout is not at the commit this run was given. Nothing here can tell which "
        "of the two you meant, so it refuses rather than choosing."
    ),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--github-repository-id", type=int, required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    # Both sinks are optional: the gate job only needs the step outputs, and the publish
    # job only needs the document. Writing a file nobody reads is not free on a runner
    # whose log is world readable for any public caller.
    parser.add_argument("--output", type=Path, default=None)
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
        # THE REASON AND THE DETAIL. Only the reason used to be printed, and a pilot user
        # met `remote_ref_mismatch` on their first build with nothing else on the page --
        # a token that names a condition rather than a cause and gives no next step.
        #
        # The detail is safe to print and always has been: every one of them is a fixed
        # sentence about git state, and the only interpolated value in the set is a remote
        # name. No path, no identifier, no account.
        print(exc.reason.value, file=sys.stderr)
        print(exc.detail, file=sys.stderr)
        remedy = REMEDIES.get(exc.reason)
        if remedy is not None:
            print(remedy, file=sys.stderr)
        return 1

    registered = registry.repository_by_name(identity.repository)
    try:
        if arguments.output is not None:
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
