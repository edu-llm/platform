"""Which container images a capacity block node is allowed to run a command in.

**THE LIST IS SHORT BECAUSE THE NODE ROLE MAKES IT SHORT, AND THAT IS THE WHOLE POINT.**
``infra/iam/block-fleet-roles.yaml`` grants ``ecr:BatchGetImage``,
``ecr:GetDownloadUrlForLayer`` and ``ecr:BatchCheckLayerAvailability`` against named
repository ARNs and nothing else -- deliberately, because eight people share the fleet,
nothing they run is reviewed, and anything the instance role can do an untrusted training
command can do. A wildcard on ``repository/sbsandbox-intern-edullm-*`` would remove that
property for the convenience of never editing a template again.

**SO THIS MODULE EXISTS TO MAKE THE REFUSAL READABLE RATHER THAN TO MAKE IT EXIST.** IAM
already refuses an image outside the list. What it answers with is an
``AccessDeniedException`` naming a registry path, arriving on the node, inside a Systems
Manager invocation, after a claim has been taken and a repository cloned -- and reading, to
whoever dispatched it, as a broken image rather than as a list they are not on.
:func:`parse_image` answers the same question on a GitHub Actions runner holding no AWS
credential at all, before anything touches the machine, and names what is allowed.

**IT IS ALSO THE ONE PLACE THE LIST IS WRITTEN.** ``tests/test_block_workflows.py`` holds
:data:`PULLABLE_REPOSITORIES` against the ARNs in the node role's own statement, in both
directions, so a repository added here and not to the template is a red pull request rather
than an access denial at 11:31 on a Saturday -- which is the failure the same file already
records against ``ecr:DescribeImages``, from the last time a workflow asked for something
its role had never been told about.

**A REFERENCE HERE IS ``<repository>:<tag>`` AND NOT A WHOLE IMAGE URI.** The registry host
carries the account id, which ``block-launch-fleet.yml`` masks out of its own logs, and the
node already knows it -- it is the host it pre-pulled its training image from. Asking a
person to paste a full URI into a dispatch form would put an account id in a public workflow
log to say something the machine could work out for itself.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

__all__ = [
    "POST_TRAINING_REPOSITORY",
    "PULLABLE_REPOSITORIES",
    "TRAINING_REPOSITORY",
    "BlockImage",
    "ImageIsNotPullableHere",
    "parse_image",
]

#: The image every node pulls once at boot, and the one a run gets when it names none.
TRAINING_REPOSITORY: Final = "sbsandbox-intern-edullm-olmo-core"

#: The second image, which only the downstream node ever asks for. Post-training and the
#: evaluations that follow it run `open-instruct` rather than OLMo-core -- a different
#: trainer, vLLM, DeepSpeed -- so the work cannot be done in the training image, and
#: pre-pulling it on all eight machines would be gigabytes apiece for something one machine
#: needs. It is pulled on demand by the node that names it. See the helper in
#: ``infra/block-node-bootstrap.sh``.
POST_TRAINING_REPOSITORY: Final = "sbsandbox-intern-edullm-open-instruct"

#: Every repository a node may pull from, which is every repository a run may name.
#:
#: Ordered with the pre-pulled one first, because that is the order the refusal below prints
#: them in and the first entry is the answer to "what do I get if I say nothing".
PULLABLE_REPOSITORIES: Final[tuple[str, ...]] = (
    TRAINING_REPOSITORY,
    POST_TRAINING_REPOSITORY,
)

#: What ECR accepts as a repository name, and what Docker accepts as a tag. Narrow on
#: purpose: this string reaches a shell on the node as an argument to ``docker pull``, and
#: the character sets the two registries document are already narrower than anything a
#: quoting mistake could exploit.
REPOSITORY_PATTERN: Final = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
TAG_PATTERN: Final = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}")


class ImageIsNotPullableHere(ValueError):
    """A dispatch named an image no node in this fleet is allowed to fetch.

    A ``ValueError`` rather than a refusal type of its own, because the two callers want
    opposite things from it: the workflow prints ``str(error)`` and exits, and a test asks
    what it says. Both are served by the message, and the message is the product.
    """


@dataclass(frozen=True)
class BlockImage:
    """One image a run may go in, split into the two halves the node needs separately."""

    repository: str
    tag: str

    @property
    def reference(self) -> str:
        """What ``edullm-node run --image`` takes, which is the pair without a registry."""
        return f"{self.repository}:{self.tag}"


def parse_image(
    requested: str, *, allowed: Sequence[str] = PULLABLE_REPOSITORIES
) -> BlockImage | None:
    """Read one ``<repository>:<tag>``, or ``None`` when nothing was asked for.

    ``None`` rather than a default image, because the default is a property of the node
    rather than of this module: what a node pre-pulled is in its own settings file, written
    by the launch that started it, and inventing a reference here would be a second answer
    to a question the machine has already answered for itself.

    Raises :class:`ImageIsNotPullableHere` for anything else, and the message names the
    whole list. A refusal that says only "not allowed" sends somebody back to the dispatch
    form to guess, which on a shared block is a second dispatch spent on a question this
    could have answered the first time.
    """
    wanted = requested.strip()
    if not wanted:
        return None

    permitted = tuple(allowed)
    catalogue = ", ".join(permitted) or "nothing"
    if "/" in wanted:
        raise ImageIsNotPullableHere(
            f"image_is_not_a_repository_and_tag:{wanted!r} looks like a whole image URI. "
            "Name the ECR repository and the tag only -- the node supplies the registry "
            f"host, which carries the account id. The repositories it may pull are: "
            f"{catalogue}."
        )
    repository, separator, tag = wanted.partition(":")
    if not separator or not tag:
        raise ImageIsNotPullableHere(
            f"image_carries_no_tag:{wanted!r}. A run names an image as "
            "`<repository>:<tag>`; there is no `latest` in this registry, because tags "
            "here are immutable and every one of them is a commit."
        )
    if REPOSITORY_PATTERN.fullmatch(repository) is None:
        raise ImageIsNotPullableHere(
            f"image_repository_is_not_usable:{repository!r}. ECR repository names are "
            "lowercase letters, digits, and single dots, dashes or underscores between them."
        )
    if TAG_PATTERN.fullmatch(tag) is None:
        raise ImageIsNotPullableHere(
            f"image_tag_is_not_usable:{tag!r}. A tag is letters, digits, dot, dash and "
            "underscore, up to 128 characters."
        )
    if repository not in permitted:
        raise ImageIsNotPullableHere(
            f"image_is_not_pullable_on_a_node:{repository!r} is not one of the repositories "
            "the block node role may fetch from, so a run in it would be refused by IAM on "
            "the machine, after a claim was taken and a branch cloned, with an "
            f"AccessDeniedException naming a registry path. What a node may pull is: "
            f"{catalogue}. Widening that is an edit to "
            "`infra/iam/block-fleet-roles.yaml` and an IAM stack applied from a laptop."
        )
    return BlockImage(repository=repository, tag=tag)
