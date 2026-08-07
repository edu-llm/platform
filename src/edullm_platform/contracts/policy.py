from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import BeforeValidator, Field, model_validator

from .base import (
    ContractModel,
    PositiveStrictDecimal,
    StrictDecimal,
    parse_str_enum,
    require_ordered_sequence,
)
from .bindings import TeamId
from .image_scan import ImageScanPolicy

DeniedOutrightCondition = Literal[
    "unregistered_repository",
    "unregistered_dataset",
    "unregistered_compute_profile",
    "mutable_repository_revision",
    "mutable_image_reference",
    "image_scan_findings_unreviewed",
    # A dataset this platform can resolve and that a run must not train on. Separate from
    # `unregistered_dataset` because the two send a reader to different places: one says the
    # registry has never heard of this, and this one says the registry knows exactly what it
    # is and it is an input to a corpus rather than a corpus. See TRAINABLE_FAMILIES.
    "dataset_is_not_a_corpus",
]


class ApprovalClass(StrEnum):
    #: Released by nobody. A run whose worst case is under
    #: :attr:`PolicyThresholds.automatic_below_cost_usd` and which is not a fan-out; see
    #: :func:`classify_request` for the three things that hold a single cheap cell back.
    AUTOMATIC = "automatic"
    ROUTINE = "routine"
    #: ONE THING CLASSIFIES AS THIS AND IT IS A CAPACITY BLOCK.
    #:
    #: Under v4 :func:`classify_request` returned this for a request over a
    #: ``routine_maximum_`` bound, for an unreviewed image scan, and for a compute profile
    #: priced above a rate ceiling. All three are gone: the first two are a team lead's to
    #: release and the third was withdrawn because rate is the wrong instrument -- it made
    #: who releases a run a function of a price that moves.
    #:
    #: What replaced them is narrower and is a property of the machine rather than of the
    #: request: a compute profile whose only route to hardware is a pre-paid, dated,
    #: non-cancellable window of it. ``RequestFacts.capacity_block_backed`` carries that fact
    #: and ``config/workload-catalog.yaml`` declares it. Between 2026-08-06 and this change
    #: nothing returned this member at all, and the four block-backed shapes added in the
    #: meantime were routing to whichever team lead was nearest -- which is the gap
    #: ``config/policy.yaml`` was already describing as filled when it said
    #: ``exception_approver_roles`` exists for capacity blocks.
    #:
    #: It would have stayed a member even with nothing returning it, because 19 of the first
    #: 158 runs were recorded under it and every one of those records is parsed back through
    #: :class:`ApprovalClassValue`. ``admit`` also labels a manifest-hash mismatch with it,
    #: which is a refusal wearing a class rather than a run taking a route.
    EXCEPTION = "exception"


class ApprovalScope(StrEnum):
    ORGANIZATION = "organization"
    TEAM = "team"


ApprovalScopeValue = Annotated[ApprovalScope, BeforeValidator(parse_str_enum(ApprovalScope))]


class PolicyThresholds(ContractModel):
    """The one number that decides whether anybody is asked about a run.

    IT HELD SEVEN AND IT HOLDS ONE, AND THE SIX THAT WENT ARE NOT A TIDY-UP. Five of them
    named a ceiling above which a run needed an admin, and under v5 no run needs an admin,
    so a ``routine_maximum_`` bound separated routine from a class nothing lands in. The
    sixth, ``automatic_below_runtime_hours``, bounded the automatic class by declared
    runtime, and a declared runtime is a number the submitter typed rather than a fact
    about the run. Worst-case total is the instrument, and it already carries runtime,
    attempts, cells and the price of the machine.

    ``config/policy.yaml`` records why each one went, beside the number that replaced it.
    """

    #: THE ONE BOUND, AND IT IS STRICTLY UNDER.
    #:
    #: A request whose worst case is under this figure and which asks for a single cell is
    #: released by nobody. At this figure exactly, and above it, a team lead releases it.
    #: The exclusion is in the name rather than only in :func:`classify_request`, because a
    #: field called ``automatic_maximum_cost_usd`` that excluded its own value would be the
    #: undocumented strict-versus-non-strict comparison the name exists to avoid.
    #:
    #: Exclusive because the direction of the error is asymmetric. This bound decides when
    #: no human sees a run at all, so one drawn a value too wide silently enlarges the set
    #: of runs nobody looks at, while one drawn too narrow costs a lead a click on a run
    #: that was nearly small enough.
    #: ``PositiveStrictDecimal`` rather than ``StrictDecimal``, which the pair of bounds it
    #: replaced got away with. A ``StrictDecimal`` publishes the non-negative pattern, so a
    #: schema said "0" was a legal bound while ``Field(gt=0)`` refused it. Nothing read the
    #: field's exported shape while there were seven of them, and
    #: ``tests/test_schema_export.py`` reads it now that there is one.
    automatic_below_cost_usd: PositiveStrictDecimal = Field(gt=0)


class RequestFacts(ContractModel):
    claimed_team: TeamId
    repository_registered: bool
    dataset_registered: bool
    #: Whether the dataset named is one a run may train on, as opposed to one this platform
    #: can merely resolve. Required rather than defaulted, for the reason
    #: ``image_scan_reviewed`` is: the answer this fact carries when nobody supplies it would
    #: be "yes, train on it", and the failure it guards is a run that trains on a tokenizer
    #: and reports nothing wrong. Three places in the tree build one of these.
    dataset_is_a_corpus: bool
    compute_profile_registered: bool
    #: Whether the shape this request names is one only a purchased capacity block provides.
    #:
    #: Required rather than defaulted, for the reason ``image_scan_reviewed`` and
    #: ``dataset_is_a_corpus`` are: the answer this fact carries when nobody supplies it is
    #: "no", which routes a four-figure non-cancellable purchase to whichever team lead is
    #: nearest instead of to a platform admin. Three places in the tree build one of these and
    #: only one of them is production, so spelling it is cheap and forgetting it is not.
    #:
    #: A FACT ABOUT THE MACHINE AND NOT ABOUT THE PRICE, which is the whole of why it is here
    #: rather than being derived from ``estimated_cost_usd``. A one-hour block on the cheapest
    #: block shape prices below several routine on-demand runs, and it still commits money that
    #: cannot be got back; a long run on ``gpu-8xl40s`` costs more and commits nothing until it
    #: starts. Cost is what a lead is shown, and reversibility is what an admin is for.
    capacity_block_backed: bool
    immutable_revision: bool
    immutable_image: bool
    #: Whether this image's scan findings have been seen: clean of the severities policy
    #: blocks on, or carrying a recorded exception. Required rather than defaulted, and
    #: deliberately so -- a security fact with a default is a security fact that is true
    #: whenever somebody forgets, and there are only three places in the tree that build
    #: one of these. See ``contracts/image_scan.py`` for what the answer means.
    image_scan_reviewed: bool
    estimated_cost_usd: StrictDecimal = Field(ge=0)
    maximum_runtime_hours: StrictDecimal = Field(gt=0)
    maximum_attempts: int = Field(ge=1)
    fanout_size: int = Field(default=1, ge=1)
    #: RECORDED, AND NOTHING READS IT. The threshold it was compared against,
    #: ``routine_maximum_parallelism``, went in v5 because nothing fed the fact either:
    #: ``FanOut.max_parallel`` was removed when Batch turned out to accept no concurrency
    #: cap, so every manifest arrives declaring one.
    #:
    #: The field stays where the threshold did not, and the two are not the same kind of
    #: thing. A threshold is the written form of who may release a run, so a dead one reads
    #: as a control and had to go. This is an input with a default, it constrains nobody,
    #: and removing it would change this model's structural digest and refuse three
    #: committed authorization scenarios that spell it. Give it a source or delete it in a
    #: change about fan-out, not in one about approvers.
    fanout_parallelism: int = Field(default=1, ge=1)


POLICY_VERSION_PATTERN = r"^v[1-9][0-9]*$"


class ApprovalPolicy(ContractModel):
    #: Which reviewed policy produced a decision. A decision record that named only the
    #: outcome would be uninterpretable once the thresholds moved: a later reader could not
    #: tell an approval that was routine under the rules of its day from one that would be
    #: an exception under today's. Monotonic rather than a date, because two amendments on
    #: one day are ordinary and two dates that collide are not orderable.
    policy_version: str = Field(pattern=POLICY_VERSION_PATTERN)
    thresholds: PolicyThresholds
    #: Which scan severities require a recorded exception before a digest may run. Part of
    #: the policy rather than of the build, because the answer to "may this image run" is
    #: a policy question and the enforcement point is admission. See
    #: ``contracts/image_scan.py`` for why it is not enforced at publish.
    image_scan: ImageScanPolicy
    approval_scope: ApprovalScopeValue
    routine_approver_role: str = Field(min_length=1)
    exception_approver_roles: Annotated[
        tuple[str, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)
    denied_outright: Annotated[
        tuple[DeniedOutrightCondition, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)

    @model_validator(mode="after")
    def validate_exception_approvals_are_stronger(self) -> Self:
        if self.routine_approver_role in self.exception_approver_roles:
            raise ValueError(
                "routine approver role must not satisfy exception approval on its own"
            )
        return self


#: Every fact that, when false, says an input to this request could not be resolved. Held as
#: a tuple rather than written into the expression below so that
#: :func:`~edullm_platform.admission.denied_outright_conditions` and this function cannot
#: come to disagree about which facts are the registration ones.
INPUTS_THAT_MUST_RESOLVE: Final = (
    "repository_registered",
    "dataset_registered",
    "compute_profile_registered",
    "immutable_revision",
    "immutable_image",
)


def classify_request(
    facts: RequestFacts,
    thresholds: PolicyThresholds,
) -> ApprovalClass:
    """Whether anybody is asked about this request, and which of two gates they stand at.

    **THERE ARE THREE ANSWERS, THERE WERE TWO, AND BEFORE THAT THERE WERE THREE DIFFERENT
    ONES.** Under v4 this returned ``EXCEPTION`` for a request over one of five ceilings, and
    an admin released it. v5 removed all five, because a team lead releases everything that
    turns on how big or how long or how scanned a request is. That left cost as the only
    instrument and no route to an admin at all, which was correct for about a day: the four
    block-backed shapes landed with ``config/policy.yaml`` still saying
    ``exception_approver_roles`` existed for capacity blocks and nothing sending one there.

    ``capacity_block_backed`` is the branch that makes that sentence true, and it is tested
    **first** so that it cannot be reached past. Every test below it describes a request that a
    person releases and answers ``ROUTINE``, so a block-backed fan-out, or a block-backed run
    whose image scan is unreviewed, would take a lead's gate on the strength of a fact that has
    nothing to do with who owns the purchase. Neither of those is hypothetical -- a sweep is
    exactly what somebody buys a block for. What the ordering costs is that a block-backed
    request whose inputs do not resolve is labelled ``exception`` on the record of its own
    refusal, and that is the right label: an admin is who it would have been put to.

    ``estimated_cost_usd`` is the figure an approver is shown, not a second one derived
    here. Both production callers set it from
    ``compute_manifest_cost_inputs(...).maximum_compute_cost_usd`` -- ``submission.py``
    before rendering that same value into the approver context, and ``admission.py`` before
    re-deriving the class inside AWS. A rule that recomputed its own estimate could route on
    a number no human ever saw. It is a ceiling and a pessimistic one, and
    ``edullm_platform.run_history`` is what puts a measured duration beside it for the
    person reading. Nothing in this function reads that measurement, and nothing may:
    routing is on what is being authorised, which is the worst case.

    **A FAN-OUT IS NEVER AUTOMATIC, WHATEVER IT COSTS.** The arithmetic is not the problem:
    a sixty-four cell sweep of twenty-step checks genuinely is a few dollars, because the
    estimate already multiplies by cells. What the total does not carry is that sixty-four
    cells is sixty-four machines starting at once. This rule was written to take a person
    out of a twenty-step smoke test, not out of a sweep, and a sweep is exactly the shape
    where somebody should see the total before it starts. Dropping the ``fanout_size`` test
    would auto-approve that sweep and nothing else here would object.

    **AN UNREVIEWED IMAGE SCAN IS A LEAD'S TO RELEASE AND IS NEVER NOBODY'S.** v5 moved
    ``image_scan_findings_unreviewed`` out of the exception class so that somebody can act
    on what the findings say, and the property the gate was built for is that somebody
    reads them first. A cheap short run whose digest carries unreviewed CRITICAL findings
    would satisfy the cost test, so it is held back here and the findings are printed to
    the lead by ``render_approver_context``. Delete this test and the softening becomes a
    removal, because the reader disappears along with the refusal.

    **AN INPUT THAT DOES NOT RESOLVE IS NEVER AUTOMATIC EITHER, AND THAT IS BELT AND
    BRACES.** Every fact in :data:`INPUTS_THAT_MUST_RESOLVE` is also a ``denied_outright``
    condition, so a submission tripping one is refused by ``compile_submission`` before a
    gate is chosen and by ``admit`` before an environment is compared. It is tested here
    anyway because this function is what names the class on the decision record such a
    refusal writes, and "released by nobody" is the wrong words for a request nobody may
    release. It returns routine rather than exception for them: the record says a person
    would have been asked, and no person was, because the request was refused.
    """
    if facts.capacity_block_backed:
        return ApprovalClass.EXCEPTION
    if facts.fanout_size > 1:
        return ApprovalClass.ROUTINE
    if not facts.image_scan_reviewed:
        return ApprovalClass.ROUTINE
    if not all(getattr(facts, fact) for fact in INPUTS_THAT_MUST_RESOLVE):
        return ApprovalClass.ROUTINE
    if facts.estimated_cost_usd < thresholds.automatic_below_cost_usd:
        return ApprovalClass.AUTOMATIC
    return ApprovalClass.ROUTINE
