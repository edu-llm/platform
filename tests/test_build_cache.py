from __future__ import annotations

import pytest

from edullm_platform.build_cache import (
    ANCESTOR_LIMIT,
    IMAGE_TAG_LENGTH,
    ancestor_tags,
    cache_source_reference,
    choose_published_ancestor,
    image_tag_for_commit,
)

REGISTRY = "123456789012.dkr.ecr.us-east-1.amazonaws.com"
ECR_REPOSITORY = "sbsandbox-intern-edullm-olmo-core"


def commit(marker: str) -> str:
    return (marker * 40)[:40]


def tag(marker: str) -> str:
    return (marker * IMAGE_TAG_LENGTH)[:IMAGE_TAG_LENGTH]


def test_the_tag_is_the_same_twelve_characters_the_publish_workflow_writes() -> None:
    # Composed here rather than passed in, so an ancestor cannot be offered under a tag
    # its own publish would never have used.
    assert image_tag_for_commit("fc2c4745e377752f4b55684819a58510c9f8b453") == "fc2c4745e377"


@pytest.mark.parametrize(
    "value",
    ["", "fc2c4745e377", "F" * 40, "g" * 40, "a" * 39, "a" * 41, " " + "a" * 40],
)
def test_a_value_that_is_not_a_commit_sha_has_no_tag(value: str) -> None:
    with pytest.raises(ValueError):
        image_tag_for_commit(value)


def test_ancestors_keep_the_order_rev_list_gave_them() -> None:
    # Order is the whole value of the list. The nearest ancestor is the one most likely to
    # carry the same pyproject.toml, so it is the one whose dependency layer this build can
    # actually reuse; sorting or de-ordering would silently pick a worse cache.
    output = f"{commit('a')}\n{commit('b')}\n{commit('c')}\n"

    assert ancestor_tags(output) == (tag("a"), tag("b"), tag("c"))


def test_blank_lines_and_trailing_newlines_are_not_commits() -> None:
    assert ancestor_tags(f"\n{commit('a')}\n\n{commit('b')}\n\n") == (tag("a"), tag("b"))


def test_an_empty_history_yields_no_candidates() -> None:
    assert ancestor_tags("") == ()
    assert ancestor_tags("\n\n") == ()


def test_the_limit_stops_the_walk_rather_than_truncating_afterwards() -> None:
    output = "\n".join(commit(chr(ord("a") + index)) for index in range(10))

    assert len(ancestor_tags(output, limit=4)) == 4
    assert ancestor_tags(output, limit=0) == ()


def test_two_ancestors_sharing_a_twelve_character_prefix_are_offered_once() -> None:
    # Twelve hex characters of a commit can collide, and the same tag offered twice would
    # spend a second registry lookup to learn what the first one said.
    near = "abcdefabcdef" + "1" * 28
    far = "abcdefabcdef" + "2" * 28

    assert ancestor_tags(f"{near}\n{far}\n") == ("abcdefabcdef",)


def test_output_that_is_not_a_history_stops_the_read_rather_than_being_skipped() -> None:
    # A rev-list that has started emitting something else is not a history this can reason
    # about, and quietly dropping the line would offer a shorter list as if it were whole.
    with pytest.raises(ValueError):
        ancestor_tags(f"{commit('a')}\nfatal: bad revision\n")


def test_a_negative_limit_is_refused() -> None:
    with pytest.raises(ValueError):
        ancestor_tags(f"{commit('a')}\n", limit=-1)


def test_the_nearest_published_ancestor_wins() -> None:
    candidates = (tag("a"), tag("b"), tag("c"))

    assert choose_published_ancestor(candidates, {tag("b"), tag("c")}) == tag("b")


def test_registry_order_says_nothing_about_ancestry() -> None:
    # DescribeImages returns what it found in its own order. Following that order would
    # pick whichever image the registry happened to name first, which is not the nearest
    # ancestor and may be many dependency changes away from this tree.
    candidates = (tag("a"), tag("b"), tag("c"))

    assert choose_published_ancestor(candidates, [tag("c"), tag("b")]) == tag("b")


def test_a_published_tag_that_is_not_an_ancestor_is_never_chosen() -> None:
    # THE SECURITY PROPERTY, AND THE ONLY REASON THIS CACHE IS SAFE TO IMPORT. On a hit
    # BuildKit reuses the recorded layer without running the command, so an image from a
    # branch this tree does not descend from would put bytes into a reviewed commit that
    # the reviewed commit never produced -- with every other gate in the publish path
    # still green, because they inspect the checkout rather than the layers.
    assert choose_published_ancestor((tag("a"),), {tag("f")}) is None


def test_no_candidates_and_no_published_images_both_answer_none() -> None:
    assert choose_published_ancestor((), {tag("a")}) is None
    assert choose_published_ancestor((tag("a"),), set()) is None


def test_a_registry_answer_that_is_not_a_tag_is_ignored_rather_than_trusted() -> None:
    assert choose_published_ancestor((tag("a"),), {"latest", "", tag("a")}) == tag("a")
    assert choose_published_ancestor((tag("a"),), {"latest"}) is None


@pytest.mark.parametrize("bad", ["", "latest", "a" * 11, "a" * 13, "A" * 12, "g" * 12])
def test_a_candidate_that_is_not_a_tag_is_a_defect_rather_than_a_miss(bad: str) -> None:
    with pytest.raises(ValueError):
        choose_published_ancestor((bad,), set())


def test_the_cache_reference_is_the_registry_the_repository_and_the_tag() -> None:
    assert cache_source_reference(
        registry=REGISTRY, ecr_repository=ECR_REPOSITORY, tag=tag("a")
    ) == f"{REGISTRY}/{ECR_REPOSITORY}:{tag('a')}"


@pytest.mark.parametrize(
    ("registry", "repository", "value"),
    [
        ("", ECR_REPOSITORY, tag("a")),
        (REGISTRY, "", tag("a")),
        (REGISTRY, ECR_REPOSITORY, "latest"),
        (REGISTRY, ECR_REPOSITORY, ""),
        (f"{REGISTRY} --push", ECR_REPOSITORY, tag("a")),
        (REGISTRY, "repo name", tag("a")),
    ],
)
def test_a_reference_that_could_be_read_as_more_than_one_argument_is_refused(
    registry: str, repository: str, value: str
) -> None:
    with pytest.raises(ValueError):
        cache_source_reference(registry=registry, ecr_repository=repository, tag=value)


def test_the_ancestor_limit_stays_well_inside_what_one_describe_images_call_takes() -> None:
    # DescribeImages accepts a hundred image ids in one call, and the publish job makes
    # exactly one. A limit above that would silently become several calls or an error.
    assert 0 < ANCESTOR_LIMIT <= 100
