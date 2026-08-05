"""Which already-published image a build may import its layer cache from.

WHY THERE IS A CACHE AT ALL, AND WHY ORDERING THE DOCKERFILE WAS NOT ENOUGH. A research
image is a four-gigabyte dependency install and a few megabytes of source. Read out of
``sbsandbox-intern-edullm-olmo-core`` on 2026-08-05: 63 images, 259.97 GiB billed, and
253.25 GiB of that is 63 *distinct* dependency layers -- one per image, sharing nothing.
Moving the install above the source copy is necessary and is not sufficient, because two
independent uncached builds of an identical, fully pinned ``pip install`` still produce
different blobs: the installed files carry the mtimes of the install that made them, and
BuildKit's ``rewrite-timestamp`` does not close the gap. A layer is shared only when the
step that would have produced it is *not run*, and that is what importing a cache does.

WHY AN ANCESTOR, WHICH IS THE WHOLE SECURITY ARGUMENT. On a cache hit BuildKit reuses the
recorded layer without executing the command, so whoever decides what is in the cache
decides what is in the image. A single shared cache tag would let a push to any branch of
any registered repository place bytes in an image built from a reviewed commit on another
branch, while every gate in the publish path stayed green -- they inspect the checkout and
the Dockerfile, not the layers. The rule here removes that: a build may import a cache only
from an image published for one of its own ancestors, so the code that produced the layer
is code the tree being built already contains. There is no commit an attacker can poison
that is not already a commit they control the content of.

WHY INLINE, RATHER THAN A CACHE OF ITS OWN. The cache lives in the published image's
configuration blob -- measured at 1,940 bytes -- so it needs no repository, no storage, no
lifecycle rule and no permission the publisher role does not already hold. ``BatchGetImage``,
``GetDownloadUrlForLayer`` and ``BatchCheckLayerAvailability`` are three of its nine actions
on exactly the repositories it may already read. Nothing in ``infra/iam/`` changes, and the
denial matrix the publish path runs against the deployed role is untouched.

A cache is an optimisation, so nothing here may fail a publish. Every function answers
"which image, or none, and why" and the caller reports the reason rather than stopping.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from enum import StrEnum
from typing import Final

__all__ = [
    "ANCESTOR_LIMIT",
    "IMAGE_TAG_LENGTH",
    "CacheSourceReason",
    "ancestor_tags",
    "cache_source_reference",
    "choose_published_ancestor",
    "image_tag_for_commit",
]

#: How many ancestors are offered as candidates. ``DescribeImages`` takes a hundred image
#: ids in one call, so this is not an API bound; it is how far back the search is worth
#: going. Every push to a registered branch publishes, so the nearest ancestor is nearly
#: always published and twenty-five is slack for the stretches where it is not -- a branch
#: rebased onto commits that were never built, or a repository onboarded mid-history.
ANCESTOR_LIMIT: Final = 25

#: The publish workflow tags an image with the first twelve characters of its commit. This
#: module composes the same tag rather than being told it, so an ancestor's tag cannot be
#: something other than what that ancestor's own publish would have written.
IMAGE_TAG_LENGTH: Final = 12

COMMIT_SHA_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
IMAGE_TAG_PATTERN: Final = re.compile(rf"^[0-9a-f]{{{IMAGE_TAG_LENGTH}}}$")


class CacheSourceReason(StrEnum):
    """Why a build has no cache to import. Printed; never raised past the caller.

    Each of these is an ordinary state rather than a fault. The first build of a
    repository has no ancestor, the first build after a rebase onto unbuilt history has no
    *published* ancestor, and a registry that cannot be asked is a slow build rather than a
    wrong one.
    """

    NO_ANCESTOR_COMMITS = "no_ancestor_commits"
    NO_PUBLISHED_ANCESTOR = "no_published_ancestor"
    REGISTRY_UNREADABLE = "registry_unreadable"
    HISTORY_UNREADABLE = "history_unreadable"


def image_tag_for_commit(commit_sha: str) -> str:
    """The tag the publish workflow gives a commit, composed the same way it composes it."""
    if COMMIT_SHA_PATTERN.fullmatch(commit_sha) is None:
        raise ValueError("commit SHA must contain exactly 40 lowercase hexadecimal characters")
    return commit_sha[:IMAGE_TAG_LENGTH]


def ancestor_tags(revision_output: str, *, limit: int = ANCESTOR_LIMIT) -> tuple[str, ...]:
    """Read ``git rev-list`` output into candidate image tags, nearest ancestor first.

    Order is the whole value of the result and is preserved exactly: the nearest ancestor
    is the one most likely to carry the same ``pyproject.toml``, and therefore the one whose
    dependency layer this build can actually reuse. A line that is not a commit SHA stops
    the read rather than being skipped, because a rev-list that has started emitting
    something else is not a history this can reason about.
    """
    if limit < 0:
        raise ValueError("limit must not be negative")
    tags: list[str] = []
    for line in revision_output.split("\n"):
        # Tested before the append rather than after it, which is what makes a limit of
        # zero mean none rather than one.
        if len(tags) >= limit:
            break
        candidate = line.strip()
        if not candidate:
            continue
        if COMMIT_SHA_PATTERN.fullmatch(candidate) is None:
            raise ValueError("rev-list output must be one full commit SHA per line")
        tag = candidate[:IMAGE_TAG_LENGTH]
        # A twelve-character prefix is not unique in principle, and two ancestors sharing
        # one would offer the same image twice. Keep the nearer of the two.
        if tag not in tags:
            tags.append(tag)
    return tuple(tags)


def choose_published_ancestor(
    candidates: Sequence[str],
    published: Iterable[str],
) -> str | None:
    """The nearest candidate the registry actually holds, or ``None``.

    ``published`` is what the registry answered, and it is treated as a set to be checked
    against rather than as an order to follow: ``DescribeImages`` returns what it found in
    its own order, which says nothing about ancestry. A tag the registry names that was
    never a candidate is ignored -- the point of this function is that the answer is one of
    the commits this build descends from.
    """
    for tag in candidates:
        if IMAGE_TAG_PATTERN.fullmatch(tag) is None:
            raise ValueError("candidate tags must be twelve lowercase hexadecimal characters")
    available = {tag for tag in published if IMAGE_TAG_PATTERN.fullmatch(tag) is not None}
    for tag in candidates:
        if tag in available:
            return tag
    return None


def cache_source_reference(*, registry: str, ecr_repository: str, tag: str) -> str:
    """Compose the image reference ``docker build --cache-from`` is given.

    A tag rather than a digest, deliberately and safely: ECR holds these repositories with
    ``IMAGE_TAG_MUTABILITY=IMMUTABLE``, so a tag here names one manifest for the life of the
    repository and cannot be moved onto different bytes after this build resolved it.
    """
    if IMAGE_TAG_PATTERN.fullmatch(tag) is None:
        raise ValueError("cache source tag must be twelve lowercase hexadecimal characters")
    if not registry or not ecr_repository:
        raise ValueError("cache source needs both a registry host and a repository name")
    if any(character.isspace() for character in f"{registry}{ecr_repository}"):
        raise ValueError("registry host and repository name must not contain whitespace")
    return f"{registry}/{ecr_repository}:{tag}"
