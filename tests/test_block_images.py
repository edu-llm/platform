"""What a run on a capacity block node may name as its image, and what it may not.

**THE REFUSALS ARE THE PRODUCT HERE AND THE PARSING IS THE ARRANGEMENT.** IAM already stops
a node fetching an image outside the list ``infra/iam/block-fleet-roles.yaml`` grants it. It
stops it on the machine, inside a Systems Manager invocation, after ``edullm-node run`` has
taken the claim and cloned a branch, with an ``AccessDeniedException`` naming a registry
path -- and to the person who dispatched, that reads as a broken image rather than as a list
they are not on. Everything in this module exists to say the same thing on a runner holding
no AWS credential, before a machine is touched, in a sentence that names what is allowed.

So each test below asserts what a refusal *says* as well as that it refuses. A message that
answered only "not permitted" would pass a check written the obvious way and send somebody
back to a dispatch form to guess, which on a shared block costs a second dispatch and the
minutes between them.

``tests/test_block_workflows.py`` is where the other half lives: that the list here and the
ARNs in the node role's own statement are the same set, in both directions.
"""

from __future__ import annotations

import pytest

from edullm_platform.block_images import (
    POST_TRAINING_REPOSITORY,
    PULLABLE_REPOSITORIES,
    TRAINING_REPOSITORY,
    BlockImage,
    ImageIsNotPullableHere,
    parse_image,
)


def test_naming_nothing_is_not_naming_a_default() -> None:
    """Mutation: return the training image when the input is empty.

    A default resolved here would be this module's opinion about what is on a machine it
    cannot see. What a node pre-pulled is in that node's own settings file, written by the
    launch that started it, and the helper reads it from there -- so an empty input has to
    come back as "nothing was asked for" and let the node answer. Two answers to that
    question is how a fleet relaunched onto a different tag starts running the old one.
    """
    assert parse_image("") is None
    assert parse_image("   ") is None


def test_a_registered_repository_and_a_tag_is_the_whole_grammar() -> None:
    assert parse_image(f"{POST_TRAINING_REPOSITORY}:1cf5f26") == BlockImage(
        repository=POST_TRAINING_REPOSITORY, tag="1cf5f26"
    )
    assert parse_image(f"{TRAINING_REPOSITORY}:0a1b2c3").reference == (
        f"{TRAINING_REPOSITORY}:0a1b2c3"
    )


def test_an_image_outside_the_list_is_refused_and_the_refusal_names_the_list() -> None:
    """THE ONE THIS MODULE IS FOR. Mutation: refuse without saying what is allowed.

    ``sbsandbox-intern-edullm-p1`` is a real repository this platform builds and publishes
    to, which is what makes it the right thing to be refused with: the mistake this catches
    is not somebody inventing a name, it is somebody naming a repository that plainly exists
    and reasonably expecting a node to be able to fetch it.
    """
    with pytest.raises(ImageIsNotPullableHere) as refusal:
        parse_image("sbsandbox-intern-edullm-p1:abc1234")

    said = str(refusal.value)
    assert said.startswith("image_is_not_pullable_on_a_node:")
    for repository in PULLABLE_REPOSITORIES:
        assert repository in said
    assert "block-fleet-roles.yaml" in said, (
        "the refusal does not say where the list is widened, so the only way past it is to "
        "ask somebody"
    )


def test_a_whole_image_uri_is_refused_rather_than_having_its_host_ignored() -> None:
    """Mutation: split on the last slash and check the repository that falls out.

    Then ``docker.io/library/sbsandbox-intern-edullm-open-instruct:x`` passes a check written
    to answer whether this fleet may pull an image, and the answer is about a repository name
    that happens to match rather than about the registry it would come from. The argument is
    a repository and a tag; the node supplies the host, which is the one carrying the account
    id nobody should paste into a dispatch form.
    """
    with pytest.raises(ImageIsNotPullableHere) as refusal:
        parse_image(f"docker.io/library/{POST_TRAINING_REPOSITORY}:1cf5f26")

    assert str(refusal.value).startswith("image_is_not_a_repository_and_tag:")
    assert "account id" in str(refusal.value)


@pytest.mark.parametrize(
    "requested",
    [POST_TRAINING_REPOSITORY, f"{POST_TRAINING_REPOSITORY}:"],
)
def test_an_image_with_no_tag_is_refused_rather_than_given_one(requested: str) -> None:
    """Mutation: default a missing tag to ``latest``.

    There is no ``latest`` in this registry. Tags are immutable and every one of them is a
    commit prefix, so the default would name a manifest that does not exist -- discovered by
    the node, minutes into a pull, as a failure about a manifest rather than about a form.
    """
    with pytest.raises(ImageIsNotPullableHere) as refusal:
        parse_image(requested)

    assert str(refusal.value).startswith("image_carries_no_tag:")
    assert "latest" in str(refusal.value)


@pytest.mark.parametrize(
    "requested",
    [
        "sbsandbox-intern-edullm-olmo-core:tag with a space",
        "sbsandbox-intern-edullm-olmo-core:$(whoami)",
        "SBSANDBOX-INTERN-EDULLM-OLMO-CORE:abc1234",
        "sbsandbox-intern-edullm-olmo-core;rm -rf /:abc1234",
    ],
)
def test_a_reference_that_is_not_one_is_refused_before_it_reaches_a_shell(
    requested: str,
) -> None:
    """The character set is a safety control, the same one the run name already carries.

    This string is handed to ``docker pull`` and ``docker run`` on the node as an argument
    assembled by shell, so what a registry documents as a legal name is the widest thing that
    may pass. It is narrower than anything a quoting mistake could exploit, which is the
    point of checking it here rather than trusting the quoting downstream.
    """
    with pytest.raises(ImageIsNotPullableHere):
        parse_image(requested)


def test_the_allowed_list_is_an_argument_so_a_test_can_ask_about_a_narrower_fleet() -> None:
    """A window deployed with only the training repository is a real configuration.

    ``PostTrainingRepository`` is a template parameter, so a deployer may point it at the
    training repository and get the grant this lane had before a second image existed. The
    refusal has to be right for that fleet too, and a function that read a module constant
    could not be asked.
    """
    with pytest.raises(ImageIsNotPullableHere) as refusal:
        parse_image(f"{POST_TRAINING_REPOSITORY}:1cf5f26", allowed=(TRAINING_REPOSITORY,))

    assert POST_TRAINING_REPOSITORY in str(refusal.value)
    assert TRAINING_REPOSITORY in str(refusal.value)
