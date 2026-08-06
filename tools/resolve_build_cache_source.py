"""Pick the published ancestor image this build imports its layer cache from.

Runs in the publish job under the publisher session, between the ECR login and the build.
The gate job supplied the candidates -- the commits this one descends from -- and this asks
the registry which of them it holds, then names the nearest.

THE PERMISSION QUESTION, ANSWERED BY NOT ASKING FOR ONE. ``ecr:BatchGetImage`` is already
among the publisher role's nine actions on exactly these repositories, and the resume path
in the same workflow already calls it. Importing the cache needs it along with
``GetDownloadUrlForLayer`` and ``BatchCheckLayerAvailability``, which are two more of the
nine. Nothing in ``infra/iam/`` moves, no repository is created, and the denial matrix that
ran before this job is as true afterwards as it was before.

Nothing here fails a publish. Every unhappy answer -- no candidates, none of them
published, a registry that would not say -- is the same outcome as today's builds, which
import nothing at all. The reason is printed because a cache that quietly stopped working
would show up as a bill months later rather than as a line in a log.

Only the machine-readable outcome reaches the streams. An ECR error names the registry id
in its text and the runner log is world readable for any public caller repository, so the
error is read by this process and never echoed by it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from edullm_platform.build_cache import (
    BATCH_GET_IMAGE_LIMIT,
    CacheSourceReason,
    cache_source_reference,
    candidate_batches,
    choose_published_ancestor,
)
from edullm_platform.build_tooling import UnsafeStepOutputError, append_step_outputs

#: One bounded call. A registry that has not answered in half a minute has cost this build
#: more than the cache would save it.
AWS_CALL_TIMEOUT_SECONDS = 30

__all__ = [
    "BATCH_GET_IMAGE_LIMIT",
    "batch_get_image_tags",
    "build_parser",
    "main",
    "published_tags",
    "resolve_cache_source",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--ecr-repository", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    return parser


def batch_get_image_tags(
    batch: Sequence[str],
    *,
    ecr_repository: str,
    region: str,
) -> tuple[frozenset[str], CacheSourceReason | None]:
    """One ``BatchGetImage`` call, answering which of these tags the registry holds.

    ``--accepted-media-types`` is deliberately not passed, unlike the resume path's call in
    the same workflow. That step reads the manifest body and has to name the types it can
    parse; this one reads nothing but the tag, so narrowing the accepted set could only
    turn a published ancestor into a ``failures`` entry and lose a cache hit over a media
    type nobody thought to list. The service default is the widest set it offers.
    """
    try:
        completed = subprocess.run(
            [
                "aws",
                "ecr",
                "batch-get-image",
                "--region",
                region,
                "--repository-name",
                ecr_repository,
                "--image-ids",
                *(f"imageTag={tag}" for tag in batch),
                "--query",
                "images[].imageId.imageTag",
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

    # An absent image is not an error here, which is the whole reason this call and not
    # DescribeImages: it comes back under `failures` with `ImageNotFound` and the call
    # still exits zero. So a non-zero exit is a registry that would not answer, and the
    # text that says which is never read, because it names the account.
    if completed.returncode != 0:
        return frozenset(), CacheSourceReason.REGISTRY_UNREADABLE

    try:
        tags = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return frozenset(), CacheSourceReason.REGISTRY_UNREADABLE
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        return frozenset(), CacheSourceReason.REGISTRY_UNREADABLE
    return frozenset(tags), None


def published_tags(
    candidates: Sequence[str],
    *,
    ecr_repository: str,
    region: str,
) -> tuple[frozenset[str], CacheSourceReason | None]:
    """Ask ECR which of these tags it holds, tolerating the ones it does not.

    THE ANSWER MUST BE THE PUBLISHED SUBSET, NOT ALL-OR-NOTHING. Most candidate lists have
    holes in them: a repository builds the branches somebody pushed, so a commit nobody
    pushed a branch for has no image, and eleven of OLMo-core's twenty-five had none on the
    day this was written. Treating a list with a hole in it as a list with nothing in it is
    the defect this function was rewritten to remove.

    One call per :data:`BATCH_GET_IMAGE_LIMIT` candidates rather than one call each:
    reaching the registry costs the build time the cache exists to save, and twenty-five
    round trips would spend it learning what one call says. At today's ancestor limit that
    is exactly one call.
    """
    found: set[str] = set()
    for batch in candidate_batches(candidates):
        tags, reason = batch_get_image_tags(batch, ecr_repository=ecr_repository, region=region)
        if reason is not None:
            # A batch that could not be read costs the ancestors it held and nothing more.
            # Anything a nearer batch already found is still a published ancestor of this
            # commit, and it is nearer than anything the unread batch could have offered,
            # so it stands and the reason goes unreported.
            if found:
                break
            return frozenset(), reason
        found |= tags
    return frozenset(found), None


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
