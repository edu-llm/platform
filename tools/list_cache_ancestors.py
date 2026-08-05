"""List the commits whose published image this build is allowed to reuse a layer from.

Runs in the credential-free gate job, which is the only job that can: its checkout is the
one taken at ``fetch-depth: 0``, and the publish job's is deliberately shallow because that
checkout is the ``docker build`` context, where a full ``.git`` is weight a layer copies.
So the ancestry is read here and handed across as a step output, and the publish job only
has to ask the registry which of these commits it already holds.

WHAT IS AND IS NOT BEING TRUSTED. The output is a list of candidates, not a decision. Every
entry is an ancestor of the commit under build, which is the property that makes importing
its cache safe: the code that produced the layer is code this tree already contains. A
caller that tampered with this list could only nominate commits the publish job then fails
to find in the registry, or commits it does find -- which are images this repository
published through this same gated path.

It never fails the build. A repository with no history to read gets a slower build, and a
publish that stopped because an optimisation could not be arranged would be a worse
outcome than the cost it saves. The reason is printed so the slow build is explained rather
than mysterious.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from edullm_platform.build_cache import ANCESTOR_LIMIT, CacheSourceReason, ancestor_tags
from edullm_platform.build_tooling import (
    UnsafeStepOutputError,
    append_step_outputs,
)

#: Long enough for a rev-list bounded to twenty-six commits on any checkout, short enough
#: that a hung git does not hold the gate open. The job timeout is far too coarse to say
#: anything useful about one command.
GIT_TIMEOUT_SECONDS = 30

__all__ = ["build_parser", "list_ancestors", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=ANCESTOR_LIMIT)
    return parser


def list_ancestors(
    repository_root: Path,
    commit_sha: str,
    *,
    limit: int = ANCESTOR_LIMIT,
) -> tuple[tuple[str, ...], CacheSourceReason | None]:
    """The ancestors' image tags, nearest first, or an empty tuple and the reason why.

    ``--first-parent`` rather than the full ancestry: on a merge the first parent is the
    line this branch is actually on, and the second parent's history is a different line
    whose images are no nearer to this tree for being reachable. The walk starts at the
    commit itself and drops it, which is how a root commit is handled without asking git
    for a parent that is not there.
    """
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(repository_root),
                "rev-list",
                "--first-parent",
                f"--max-count={limit + 1}",
                commit_sha,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return (), CacheSourceReason.HISTORY_UNREADABLE
    if completed.returncode != 0:
        return (), CacheSourceReason.HISTORY_UNREADABLE

    try:
        walked = ancestor_tags(completed.stdout, limit=limit + 1)
    except ValueError:
        return (), CacheSourceReason.HISTORY_UNREADABLE

    # The commit under build leads the walk and is not its own ancestor. Its image does not
    # exist yet on the path that reaches here at all -- the pre-flight lookup already
    # established the tag is unpublished -- so offering it would only cost a registry
    # lookup that cannot succeed.
    ancestors = tuple(tag for tag in walked if tag != commit_sha[: len(tag)])[:limit]
    if not ancestors:
        return (), CacheSourceReason.NO_ANCESTOR_COMMITS
    return ancestors, None


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    if arguments.limit < 0:
        print("invalid_ancestor_limit", file=sys.stderr)
        return 2

    ancestors, reason = list_ancestors(
        arguments.repository_root,
        arguments.commit_sha,
        limit=arguments.limit,
    )
    if reason is not None:
        print(reason.value, file=sys.stderr)
        print(
            "This build will install its dependencies from scratch. That is correct and "
            "slow, and it publishes a dependency layer that shares with nothing.",
            file=sys.stderr,
        )

    try:
        append_step_outputs(
            arguments.github_output,
            (("cache_ancestor_tags", " ".join(ancestors)),),
        )
    except UnsafeStepOutputError:
        print("unsafe_step_output", file=sys.stderr)
        return 1
    except OSError:
        print("output_unwritable", file=sys.stderr)
        return 2

    if ancestors:
        print(f"{len(ancestors)} ancestor commits offered as build cache sources.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
