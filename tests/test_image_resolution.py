"""What a declared commit resolves to, and every way that question can be refused.

Every case here is one function call with every input passed in. That is the property
worth guarding rather than an accident of how the module reads today: the compile job
that will call ``resolve_image`` holds no AWS credentials, so a rule that reached for the
registry itself could only ever be exercised by running a workflow against an account.
Each of the three refusals below is instead reachable offline, which is why each of them
can be held to what it says as well as to when it fires.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from edullm_platform.errors import SubmissionRefusedError
from edullm_platform.image_resolution import PublishedImage, resolve_image

#: A commit sha from this repository's own history, so the shape is one that has existed.
COMMIT = "8076c077533eb79742f4ed22aade439df123a593"

FIRST_BUILD = "sha256:" + "1a" * 32
SECOND_BUILD = "sha256:" + "2b" * 32
THIRD_BUILD = "sha256:" + "3c" * 32
FOURTH_BUILD = "sha256:" + "4d" * 32

#: Well-formed, published, and not built from ``COMMIT``. This is the shape the surviving
#: ``image_digest`` field can still carry once the commit no longer has to agree with it
#: by hand, and behaviour six is the only thing standing in front of it.
FROM_ANOTHER_COMMIT = "sha256:" + "9f" * 32


def image(digest: str, *, hour: int, minute: int) -> PublishedImage:
    return PublishedImage(
        image_digest=digest,
        pushed_at=datetime(2026, 7, 26, hour, minute, tzinfo=UTC),
    )


def built_four_times() -> list[PublishedImage]:
    """Four builds of one commit, deliberately not in the order they happened.

    A commit built four times is measured rather than imagined, and the shuffle is the
    point: ``FOURTH_BUILD`` is the most recent and sits at neither end, so an
    implementation reaching for ``published[0]`` or ``published[-1]`` answers with a real
    image of the right commit and is wrong anyway.
    """
    return [
        image(SECOND_BUILD, hour=11, minute=47),
        image(FOURTH_BUILD, hour=18, minute=30),
        image(FIRST_BUILD, hour=9, minute=2),
        image(THIRD_BUILD, hour=14, minute=15),
    ]


# ---------------------------------------------------------------------------------------
# What a commit resolves to
# ---------------------------------------------------------------------------------------


def test_a_commit_with_exactly_one_published_image_resolves_to_that_image() -> None:
    """Mutation: return the digest and nothing about how it was arrived at.

    ``chosen_from`` and ``was_overridden`` are what let a reader of the record tell a
    derived image from a pinned one, and the single-image commit is the case where both
    are easiest to leave unpopulated without anything noticing.
    """
    resolved = resolve_image(
        commit_sha=COMMIT,
        published=[image(FIRST_BUILD, hour=9, minute=2)],
        override=None,
    )

    assert resolved.image_digest == FIRST_BUILD
    assert resolved.chosen_from == 1
    assert resolved.was_overridden is False


def test_a_commit_built_more_than_once_resolves_to_the_most_recently_published_image() -> None:
    """Mutation: return ``published[-1]``, or ``published[0]``.

    Both are wrong for the same reason and neither looks wrong: each answers with an
    image genuinely built from this commit, so the run starts, the lineage record is
    internally consistent, and the only symptom is that the researcher's fix is not in
    what ran. A rebuild happens because the previous build was wrong, which is what makes
    the most recent one the right answer and an older one a silent revert.
    """
    resolved = resolve_image(commit_sha=COMMIT, published=built_four_times(), override=None)

    assert resolved.image_digest == FOURTH_BUILD
    assert resolved.chosen_from == 4
    assert resolved.was_overridden is False


def test_the_same_commit_resolves_to_the_same_image_every_time_it_is_asked() -> None:
    """Mutation: break the tie between equal timestamps with set or dict ordering.

    A resolution that is merely usually the same is worse than one that is wrong, because
    the disagreement surfaces as an image digest that changed between a compile step and
    whatever re-derives it, with nothing in either run to say which was the anomaly.
    """
    answers = {
        resolve_image(commit_sha=COMMIT, published=built_four_times(), override=None)
        for _ in range(8)
    }

    assert len(answers) == 1
    assert next(iter(answers)).image_digest == FOURTH_BUILD


# ---------------------------------------------------------------------------------------
# The override that survives the derivation
# ---------------------------------------------------------------------------------------


def test_an_override_naming_a_digest_this_commit_published_is_honoured() -> None:
    """Mutation: derive unconditionally and ignore the override.

    This is the rebuild-and-pin path and it has to survive the change that makes the
    field optional. Pinning an older build of a commit that has been rebuilt is the
    reason the field stays: a submitter reproducing an earlier result needs the image
    that produced it, not the newest one.
    """
    resolved = resolve_image(
        commit_sha=COMMIT,
        published=built_four_times(),
        override=SECOND_BUILD,
    )

    assert resolved.image_digest == SECOND_BUILD
    assert resolved.chosen_from == 4
    assert resolved.was_overridden is True


def test_an_override_naming_a_digest_from_a_different_commit_is_refused() -> None:
    """Mutation: accept any well-formed digest.

    That is exactly today's behaviour and it is what lets a lineage record name a commit
    that did not produce the image. The derivation closes the hole for a submitter who
    leaves the field alone; this is what stops the surviving field reopening it.
    """
    with pytest.raises(SubmissionRefusedError) as refusal:
        resolve_image(
            commit_sha=COMMIT,
            published=built_four_times(),
            override=FROM_ANOTHER_COMMIT,
        )

    message = str(refusal.value)
    assert FROM_ANOTHER_COMMIT in message
    assert COMMIT in message


def test_an_override_against_an_unbuilt_commit_reports_the_unbuilt_commit() -> None:
    """Mutation: validate the override before checking that anything was published.

    Both refusals are true of this submission and only one of them is useful. Told the
    digest is unrecognised, a submitter goes and checks seventy-one characters that were
    never the problem; there was no digest they could have typed, because the commit has
    never been built. The order the two rules compose in is the whole content of this
    test.
    """
    with pytest.raises(SubmissionRefusedError) as refusal:
        resolve_image(commit_sha=COMMIT, published=[], override=FROM_ANOTHER_COMMIT)

    message = str(refusal.value)
    assert "build-research-image.yml" in message
    assert COMMIT in message
    assert FROM_ANOTHER_COMMIT not in message, "the override is not this submission's problem"


# ---------------------------------------------------------------------------------------
# The two refusals
# ---------------------------------------------------------------------------------------


def test_a_commit_with_no_published_image_is_refused_and_the_message_names_the_build_workflow() -> (
    None
):
    """Mutation: let an unbuilt commit through, which is today's behaviour.

    It does not reach a run: it travels as far as admission and comes back refused for
    unreviewed image-scan findings, because there is no scan of an image that was never
    built. That refusal is true and it points at the wrong thing -- it sends somebody to
    go and look at their image when what they have to go and do is build one. A refusal
    naming the wrong next step costs more than none at all, because it gets followed.
    """
    with pytest.raises(SubmissionRefusedError) as refusal:
        resolve_image(commit_sha=COMMIT, published=[], override=None)

    message = str(refusal.value)
    assert "build-research-image.yml" in message
    assert COMMIT in message


def test_two_images_pushed_at_the_same_instant_are_refused_rather_than_picked_between() -> None:
    """Mutation: take either of the tied images, by index or by sort order.

    A tie means nothing here can say which image the rebuild meant, and the cost of
    guessing is not a wrong answer now but an unrecorded choice in a document nothing
    ever rewrites. The refusal has to hand back the digests it could not choose between,
    because the submitter's only way out is to name one of them.
    """
    tied = [
        image(FIRST_BUILD, hour=9, minute=2),
        image(SECOND_BUILD, hour=14, minute=15),
        image(THIRD_BUILD, hour=14, minute=15),
    ]

    with pytest.raises(SubmissionRefusedError) as refusal:
        resolve_image(commit_sha=COMMIT, published=tied, override=None)

    message = str(refusal.value)
    assert "image_digest" in message
    assert SECOND_BUILD in message
    assert THIRD_BUILD in message
    assert COMMIT in message
