from decimal import Decimal
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
]


class ApprovalClass(StrEnum):
    #: Released by nobody. A run small enough that asking a person costs more than the run
    #: does; see ``AUTOMATIC_BELOW`` bounds on :class:`PolicyThresholds` for what small
    #: means and :func:`classify_request` for why a fan-out is never this class.
    AUTOMATIC = "automatic"
    ROUTINE = "routine"
    EXCEPTION = "exception"


class ApprovalScope(StrEnum):
    ORGANIZATION = "organization"
    TEAM = "team"


ApprovalScopeValue = Annotated[ApprovalScope, BeforeValidator(parse_str_enum(ApprovalScope))]


class PolicyThresholds(ContractModel):
    routine_maximum_cost_usd: StrictDecimal = Field(ge=0)
    routine_maximum_runtime_hours: PositiveStrictDecimal = Field(gt=0)
    routine_maximum_attempts: int = Field(ge=1)
    routine_maximum_fanout_size: int = Field(ge=1)
    routine_maximum_parallelism: int = Field(ge=1)
    #: THE TWO EXCLUSIVE BOUNDS, AND THE ONLY ONES IN THIS MODEL THAT ARE.
    #:
    #: Every ``routine_maximum_`` bound above is inclusive: a value equal to the bound is
    #: still routine. These two are not, and the difference is in the name rather than only
    #: in :func:`classify_request`, because a field called ``automatic_maximum_cost_usd``
    #: that excluded its own value would be the undocumented strict-versus-non-strict
    #: comparison this pair exists to avoid. A request at exactly five dollars, or at
    #: exactly one hour, goes to a lead.
    #:
    #: Exclusive because the rule was specified as "under five dollars and under one hour",
    #: and because the direction of the error matters asymmetrically. These bounds decide
    #: when no human sees a run at all, so a boundary drawn one value too wide silently
    #: enlarges the set of runs nobody looks at, while one drawn too narrow costs a lead a
    #: click on a run that was nearly small enough.
    automatic_below_cost_usd: StrictDecimal = Field(gt=0)
    automatic_below_runtime_hours: PositiveStrictDecimal = Field(gt=0)


class RequestFacts(ContractModel):
    claimed_team: TeamId
    repository_registered: bool
    dataset_registered: bool
    compute_profile_registered: bool
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


#: The hourly rate above which a compute profile needs an admin rather than a team lead,
#: whatever the run's total cost and runtime are.
#:
#: WHY A RATE AND NOT THE FOUR BOUNDS ABOVE. Those bounds are all about the size of one
#: request, and every one of them is satisfiable by a short run on the largest machine in the
#: account. A one-hour p5.48xlarge is $55.04 against a $500 routine ceiling, one attempt
#: against two, one hour against twelve: routine on every axis, and a team lead releasing it
#: has authorised the most expensive instance type this platform can start. Making the
#: threshold total cost instead would have to be set near $55 to catch it, which would then
#: make an ordinary twelve-hour single-A10G run an exception as well.
#:
#: WHY A RATE AND NOT A LIST OF THE TWO PROFILE NAMES. A list is correct until somebody
#: promotes a tenth shape, and the way it fails is that the new shape is routine by default --
#: which is the wrong default for the only property that matters here. A rate gates the next
#: expensive profile before anybody remembers this file exists.
#:
#: WHY TWENTY. It sits between the most expensive routine shape and the cheapest gated one,
#: with both measured rather than guessed: g5.48xlarge, eight A10G, is $16.288/hour and stays
#: routine, and p4d.24xlarge, eight A100, is $21.958/hour and does not. p5.48xlarge at $55.04
#: is far above it. The gap is narrow, so a profile priced between $16.29 and $21.95 would
#: land on the routine side of a line drawn for a different reason, and adding one is the
#: moment to revisit this number.
#:
#: WHY IT IS HERE AND NOT IN config/policy.yaml BESIDE THE OTHER FOUR, WHICH IS WHERE IT
#: BELONGS. ``PolicyThresholds`` is a contract model, and
#: ``proof_bundle.discover_contract_models`` records every contract model's structural digest
#: in four committed proof bundles, with tests/test_schema_compatibility.py recomputing them.
#: A fifth field changes that digest, which is a proof-bundle regeneration rather than a
#: policy edit. config/policy.yaml carries a comment pointing here so that a reader of the
#: policy does not conclude there are only four bounds.
EXCEPTION_RATE_CEILING_USD_PER_HOUR: Final = Decimal(20)


def classify_request(
    facts: RequestFacts,
    thresholds: PolicyThresholds,
    *,
    # The rate of the profile the request names, which RequestFacts does not carry and cannot
    # be given for the reason recorded above the ceiling. Keyword-only and required, so that
    # a caller who has not decided what to pass gets a TypeError rather than a routine
    # classification: the failure this argument exists to prevent is a submission on the
    # largest instance in the account released by a team lead, and a default of zero would
    # reintroduce it at every call site that was not updated.
    hourly_rate_usd: Decimal,
) -> ApprovalClass:
    """Which of the three approval paths this request takes.

    Ordered as a narrowing rather than as three independent tests, and that ordering is the
    whole safety property. Everything that was an exception before ``automatic`` existed is
    still an exception, because the exception test runs first and unchanged; ``automatic``
    can only ever be carved out of what had already qualified as routine. So the auto-
    approve rule cannot promote a request past a bound, and reading this function top to
    bottom is enough to see that it cannot.

    ``estimated_cost_usd`` is the figure an approver is shown, not a second one derived
    here. Both production callers set it from
    ``compute_manifest_cost_inputs(...).maximum_compute_cost_usd`` -- ``submission.py``
    before rendering that same value into the approver context, and ``admission.py`` before
    re-deriving the class inside AWS. A rule that recomputed its own estimate could route on
    a number no human ever saw.

    **A FAN-OUT IS NEVER AUTOMATIC, WHATEVER IT COSTS.** The arithmetic is not the problem:
    a sixty-four cell sweep at both count ceilings genuinely is $4.57 over 0.05 hours,
    because the estimate already multiplies by cells. What the total does not carry is that
    sixty-four cells is sixty-four machines starting at once. This rule was written to take
    a person out of a twenty-step smoke test, not out of a sweep, and a sweep is exactly the
    shape where somebody should see the total before it starts. Dropping the ``fanout_size``
    test would auto-approve that sweep and nothing else in this function would object.
    """
    if not (
        facts.repository_registered
        and facts.dataset_registered
        and facts.compute_profile_registered
        and facts.immutable_revision
        and facts.immutable_image
        and facts.image_scan_reviewed
        and facts.estimated_cost_usd <= thresholds.routine_maximum_cost_usd
        and facts.maximum_runtime_hours <= thresholds.routine_maximum_runtime_hours
        and facts.maximum_attempts <= thresholds.routine_maximum_attempts
        and facts.fanout_size <= thresholds.routine_maximum_fanout_size
        and facts.fanout_parallelism <= thresholds.routine_maximum_parallelism
        and hourly_rate_usd <= EXCEPTION_RATE_CEILING_USD_PER_HOUR
    ):
        return ApprovalClass.EXCEPTION
    # ``FanOut.size`` is ge=2 and the fact defaults to 1, so this reads "one cell" rather
    # than "a small sweep". There is no fan-out size that auto-approves.
    if (
        facts.fanout_size == 1
        and facts.estimated_cost_usd < thresholds.automatic_below_cost_usd
        and facts.maximum_runtime_hours < thresholds.automatic_below_runtime_hours
    ):
        return ApprovalClass.AUTOMATIC
    return ApprovalClass.ROUTINE
