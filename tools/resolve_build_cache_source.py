"""Pick the published ancestor image this build imports its layer cache from.

Runs in the publish job under the publisher session, between the ECR login and the build.
The gate job supplied the candidates -- the commits this one descends from -- and this asks
the registry which of them it holds, then names the nearest.

THE PERMISSION QUESTION, ANSWERED BY NOT ASKING FOR ONE. ``ecr:DescribeImages`` is already
among the publisher role's nine actions on exactly these repositories; the pre-flight
lookup two steps above makes the same call. Importing the cache needs ``BatchGetImage``,
``GetDownloadUrlForLayer`` and ``BatchCheckLayerAvailability``, which are three more of the
nine. Nothing in ``infra/iam/`` moves, no repository is created, and the denial matrix that
ran before this job is as true afterwards as it was before.

Nothing here fails a publish. Every unhappy answer -- no candidates, none of them
published, a registry that would not say -- is the same outcome as today's builds, which
import nothing at all. The reason is printed because a cache that quietly stopped working
would show up as a bill months later rather than as a line in a log.

Only the machine-readable outcome reaches the streams. ``DescribeImages`` names the
registry id in its error text and the runner log is world readable for any public caller
repository, so the error is read by this process and never echoed by it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from edullm_platform.build_cache import (
    CacheSourceReason,
    cache_source_reference,
    choose_published_ancestor,
)
from edullm_platform.build_tooling import UnsafeStepOutputError, append_step_outputs

#: One bounded call. A registry that has not answered in half a minute has cost this build
#: more than the cache would save it.
AWS_CALL_TIMEOUT_SECONDS = 30

#: What ECR calls a tag it does not hold. When every candidate is absent the call fails as
#: a whole with this code rather than returning an empty list, which is an ordinary answer
#: -- the first build of a repository reaches it -- and not a fault.
IMAGE_NOT_FOUND = "ImageNotFoundException"

__all__ = ["build_parser", "main", "published_tags", "resolve_cache_source"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--ecr-repository", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    return parser


def published_tags(
    candidates: Sequence[str],
    *,
    ecr_repository: str,
    region: str,
) -> tuple[frozenset[str], CacheSourceReason | None]:
    """Ask ECR which of these tags it holds.

    One call for every candidate rather than one call each: ``DescribeImages`` takes a
    hundred image ids, reaching the registry costs the build time this exists to save, and
    a loop would spend twenty-five round trips to learn what one can say.
    """
    try:
        completed = subprocess.run(
            [
                "aws",
                "ecr",
                "describe-images",
                "--region",
                region,
                "--repository-name",
                ecr_repository,
                "--image-ids",
                *(f"imageTag={tag}" for tag in candidates),
                "--query",
                "imageDetails[].imageTags[]",
                "--output",
                "json",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=AWS_CALL_TIMEOUT_SECONDS,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return frozenset(), CacheSourceReason.REGISTRY_UNREADABLE

    if completed.returncode != 0:
        # Read here and not printed: the distinction matters to this function and the text
        # that carries it names the account.
        if IMAGE_NOT_FOUND in completed.stderr:
            return frozenset(), CacheSourceReason.NO_PUBLISHED_ANCESTOR
        return frozenset(), CacheSourceReason.REGISTRY_UNREADABLE

    try:
        tags = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return frozenset(), CacheSourceReason.REGISTRY_UNREADABLE
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        return frozenset(), CacheSourceReason.REGISTRY_UNREADABLE
    return frozenset(tags), None


def resolve_cache_source(
    candidates: Sequence[str],
    *,
    registry: str,
    ecr_repository: str,
    region: str,
) -> tuple[str, CacheSourceReason | None]:
    """The image reference to import, or an empty string and the reason there is none."""
    if not candidates:
        return "", CacheSourceReason.NO_ANCESTOR_COMMITS

    available, reason = published_tags(
        candidates, ecr_repository=ecr_repository, region=region
    )
    if reason is not None:
        return "", reason

    try:
        chosen = choose_published_ancestor(candidates, available)
    except ValueError:
        return "", CacheSourceReason.REGISTRY_UNREADABLE
    if chosen is None:
        return "", CacheSourceReason.NO_PUBLISHED_ANCESTOR

    try:
        return cache_source_reference(
            registry=registry, ecr_repository=ecr_repository, tag=chosen
        ), None
    except ValueError:
        return "", CacheSourceReason.REGISTRY_UNREADABLE


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    candidates = tuple(arguments.candidates.split())

    reference, reason = resolve_cache_source(
        candidates,
        registry=arguments.registry,
        ecr_repository=arguments.ecr_repository,
        region=arguments.region,
    )

    try:
        append_step_outputs(arguments.github_output, (("cache_from", reference),))
    except UnsafeStepOutputError:
        print("unsafe_step_output", file=sys.stderr)
        return 1
    except OSError:
        print("output_unwritable", file=sys.stderr)
        return 2

    if reason is not None:
        print(reason.value, file=sys.stderr)
        print(
            "No layer cache will be imported, so the dependency install runs in full and "
            "the layer it writes deduplicates against nothing already in the registry.",
            file=sys.stderr,
        )
        return 0

    # The tag and not the reference: the reference carries the registry host, which carries
    # the account id, and this line is world readable.
    print(f"Importing the build cache from the image published for {reference.rsplit(':', 1)[1]}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
