"""What image a declared commit resolves to, and every way that question can be refused.

A run's image is derived from the commit the submission declares. It is never supplied
beside it, and that sentence is the whole of this module.

It used not to be true. The form asked for ``commit_sha`` and ``image_digest`` as two
independent required fields, and nothing anywhere held them up against each other: every
one of the six conditions ``config/policy.yaml`` denies outright checks a single field
against a registry or against a shape. So a submitter could declare commit A beside a
digest built from commit B -- both well-formed, both genuinely published, each of them
faultless on its own -- and the immutable S3 lineage record would name commit A for an
image that commit never produced. Nothing downstream rewrites that record, and every
other guarantee the platform makes is read back off it.

The obvious repair is to compare the two fields and refuse when they disagree. Deriving
is the stronger one, for two reasons. A comparison refuses a mismatch, which leaves the
mismatch expressible and leaves the whole guarantee resting on a check continuing to be
run; a derivation leaves nowhere to put the second commit, so the disagreement stops
being a state the form can describe at all. And it takes the hardest field on the form --
seventy-one characters copied by hand out of a build log -- and demotes it from required
to an optional override, which makes this the rare correctness fix that also shortens the
form.

Everything here is pure. No registry call, no file read, no clock: the published images
arrive as an argument. The compile job that will call this holds no AWS credentials of
its own, so any rule that reached for the registry could only ever be exercised by
running a workflow against an account. Keeping the rules pure is what puts all three
refusals inside a unit test.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from edullm_platform.submission import SubmissionRefusedError

__all__ = [
    "PublishedImage",
    "ResolvedImage",
    "resolve_image",
]


# Frozen dataclasses rather than ContractModel subclasses, and deliberately so. Neither
# of these is serialized, stored or sent anywhere -- a ResolvedImage lives between a
# registry lookup and the manifest field it fills, and dies there.
# proof_bundle.discover_contract_models() inventories every ContractModel subclass in the
# package and records its structural digest in every committed proof bundle, so making
# these contract models would move a cell in the Phase 0, 1, 2 and 3 bundles and force a
# regeneration of four committed goldens in order to publish two shapes that never cross
# a boundary.
@dataclass(frozen=True)
class PublishedImage:
    """One image the registry holds for a commit.

    ``pushed_at`` is when the registry accepted the push, not what the image says about
    itself. The two differ and the difference matters: an image's own creation time comes
    from whichever build host produced it, so ordering rebuilds by it would order them by
    a set of unsynchronised clocks rather than by the sequence a single registry observed.
    """

    image_digest: str
    pushed_at: datetime


@dataclass(frozen=True)
class ResolvedImage:
    """The image a submission will run, and enough about the choice to record it.

    ``chosen_from`` and ``was_overridden`` exist so that a reader of the lineage record
    can tell a derived image from a pinned one, and a commit built once from a commit
    built four times. Without them the record says only which digest ran, which reads
    identically whether there was one candidate or a choice made between four.
    """

    image_digest: str
    chosen_from: int
    was_overridden: bool


def resolve_image(
    *,
    commit_sha: str,
    published: Sequence[PublishedImage],
    override: str | None,
) -> ResolvedImage:
    """Resolve the image a submission runs from the commit it declares.

    ``published`` is every image the registry holds for ``commit_sha``, in any order, and
    ``override`` is the digest a submitter named explicitly or ``None`` to derive one.

    Three things this settles. An unbuilt commit is refused here and the refusal names
    the build workflow, because today it is not refused here at all: it travels as far as
    admission and comes back refused for unreviewed image-scan findings, there being no
    scan of an image nobody built. That refusal is true and it points at the wrong thing.
    It sends the submitter to go and look at their image when what they have to go and do
    is build one, and a refusal naming the wrong next step costs more than none at all
    because it gets followed.

    A commit built more than once resolves to its most recently published image. Rebuilds
    are ordinary -- a single commit has been measured built four times in this project --
    and the most recent is the right answer because a rebuild happens when the previous
    build was wrong, so reaching for an older one is a silent revert of whatever the
    rebuild fixed.

    Two images pushed at the same instant are refused rather than picked between. A tie
    means nothing here can say which image the rebuild meant, and the cost of guessing is
    not a wrong answer that shows up now but an unrecorded choice written into a document
    nothing ever rewrites.
    """
    # THE ORDER OF THESE TWO REFUSALS IS THE POINT AND NOT AN ARTEFACT. A submitter who
    # supplies an override against a commit with nothing published trips both rules, and
    # only one of them is worth telling them. There was no digest they could have typed
    # that would have worked, so reporting the override as unrecognised sends somebody to
    # re-check seventy-one characters that were never wrong, while the thing they have to
    # do -- build the commit -- goes unmentioned. The unbuilt commit is checked first so
    # that it is the one that gets said.
    if not published:
        raise SubmissionRefusedError(
            f"commit {commit_sha} has no image published from it, so there is nothing for "
            "this submission to run. Build the commit before submitting it: the "
            "build-research-image.yml workflow publishes an image for the commit it is "
            "called on and prints the digest it published in its step summary."
        )

    if override is not None:
        if override not in {candidate.image_digest for candidate in published}:
            raise SubmissionRefusedError(
                f"image digest {override} was not published from commit {commit_sha}. A "
                "run's image has to be one its own commit produced, or the lineage record "
                "names a commit that did not build the image that ran. Submit the commit "
                "this image was built from, or name a digest this commit published: the "
                "build-research-image.yml run for a commit prints the digest it published "
                "in its step summary."
            )
        return ResolvedImage(
            image_digest=override,
            chosen_from=len(published),
            was_overridden=True,
        )

    latest = max(candidate.pushed_at for candidate in published)
    tied = [candidate for candidate in published if candidate.pushed_at == latest]
    if len(tied) > 1:
        # Sorted rather than left in the order the registry answered in, so that the same
        # tie reads the same way twice. A refusal whose wording depends on argument order
        # is the same defect as a resolution that does, arriving somewhere nobody thinks
        # to check for it.
        candidates = ", ".join(sorted(candidate.image_digest for candidate in tied))
        raise SubmissionRefusedError(
            f"commit {commit_sha} has {len(tied)} images published at the same instant "
            f"({latest.isoformat()}), so which of them this submission means cannot be "
            f"derived: {candidates}. Name the one you want in the image_digest field. A "
            "rebuild happens because the previous build was wrong, so the difference "
            "between them is a real one and not something to be guessed at here."
        )

    return ResolvedImage(
        image_digest=tied[0].image_digest,
        chosen_from=len(published),
        was_overridden=False,
    )
