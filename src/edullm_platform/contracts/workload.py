from decimal import Decimal, InvalidOperation, localcontext
from typing import Annotated, Any, ClassVar, Literal, Self

from pydantic import BeforeValidator, Field, computed_field, model_validator

from .base import (
    MAX_DECIMAL_DIGITS,
    SANDBOX_BUCKET_PREFIX,
    ContractModel,
    PositiveStrictDecimal,
    StrictDecimal,
    require_ordered_sequence,
)
from .validation import require_checkpoint_for_retries

#: One EC2 instance type, held to exactly one dot with a family in front of it and a size
#: behind it.
#:
#: THE FAMILY MAY CARRY HYPHENS AND IT COULD NOT UNTIL 2026-08-07. This read
#: ``^[a-z][a-z0-9]*\.[a-z0-9]+$``, which is every instance type AWS had sold this account and
#: not the ones it now sells: ``p6-b200.48xlarge`` and ``p6-b300.48xlarge`` put the accelerator
#: in the family name, and under the old pattern a catalog naming either was refused at load
#: with a validation error rather than at review with an argument. The hyphen groups are spelled
#: out rather than folded into the character class so that a trailing or doubled hyphen is still
#: refused; ``[a-z0-9-]*`` would have admitted ``p6-.48xlarge``.
#:
#: Still exactly one dot, which is the property :func:`edullm_platform.precision.instance_family`
#: rests on when it splits a family off the front.
INSTANCE_TYPE_PATTERN = r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*\.[a-z0-9]+$"
CHECKPOINT_DESTINATION_PREFIX_PATTERN = (
    rf"^s3://{SANDBOX_BUCKET_PREFIX}[a-z0-9](?:[a-z0-9.-]{{0,44}}[a-z0-9])?/.+/$"
)

#: How a compute profile's ``pricing_source`` says its rate came off the capacity block price
#: list rather than the on-demand one.
#:
#: This exists so that ``capacity_block_backed`` is checked rather than trusted, and the two
#: facts are the same fact: a rate quoted from this price list is a rate you can only pay by
#: reserving a dated window, because that is the only thing AWS sells at it. A profile priced
#: this way and not flagged is a shape whose purchase nobody would be asked about; a profile
#: flagged and priced off the on-demand list is a shape being sent to an admin for a machine
#: anybody can start. :meth:`WorkloadCatalog.validate_block_backing_matches_the_price_list`
#: refuses both rather than preferring one field over the other, because there is no way to
#: tell from here which of the two the editor meant.
CAPACITY_BLOCK_PRICE_LIST = "AWS Capacity Blocks for ML"


def compute_maximum_compute_cost_usd(
    hourly_rate_usd: Decimal,
    nodes: int,
    maximum_runtime_hours: Decimal,
    maximum_attempts: int,
    cells: int = 1,
) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = MAX_DECIMAL_DIGITS * 4
        try:
            product = (
                hourly_rate_usd * nodes * maximum_runtime_hours * maximum_attempts * cells
            )
            quantized = product.quantize(Decimal("0.01"))
        except InvalidOperation as exc:
            raise ValueError("maximum compute cost exceeds representable precision") from exc
    _, digits, _ = quantized.as_tuple()
    if len(digits) > MAX_DECIMAL_DIGITS:
        raise ValueError("maximum compute cost exceeds representable precision")
    return quantized


class CostInputs(ContractModel):
    hourly_rate_usd: StrictDecimal = Field(gt=0)
    nodes: int = Field(gt=0)
    maximum_runtime_hours: StrictDecimal = Field(gt=0)
    maximum_attempts: int = Field(ge=1)
    cells: int = Field(default=1, ge=1)

    @model_validator(mode="before")
    @classmethod
    def reconcile_a_recorded_total(cls, data: Any) -> Any:
        """Accept a serialized total back, and only if it still adds up.

        ``maximum_compute_cost_usd`` is a computed field, so pydantic writes it out and
        then refuses it on the way back in, because ``extra="forbid"``. That made every
        decision record in the lineage store unreadable by the model that wrote it -- an
        immutable store whose contents no longer parse is a store nobody can audit, which
        is the one thing it exists for. Found by reading real records back rather than by
        round-tripping a fixture.

        Dropping the field from the record instead would have been the smaller change and
        the wrong one: a reader without this code should be able to see what a run was
        approved to cost. So it stays written, and it is checked rather than trusted. A
        recorded total that disagrees with the inputs beside it is refused here, which
        makes an edited record fail to load instead of loading with a figure nothing
        supports.
        """
        if not isinstance(data, dict) or "maximum_compute_cost_usd" not in data:
            return data
        remaining = dict(data)
        recorded = remaining.pop("maximum_compute_cost_usd")
        candidate = cls(**remaining)
        if Decimal(str(recorded)) != candidate.maximum_compute_cost_usd:
            raise ValueError(
                "the recorded maximum_compute_cost_usd does not match the inputs recorded "
                f"beside it: {recorded} against {candidate.maximum_compute_cost_usd}"
            )
        return remaining

    @model_validator(mode="after")
    def validate_maximum_compute_cost(self) -> Self:
        compute_maximum_compute_cost_usd(
            self.hourly_rate_usd,
            self.nodes,
            self.maximum_runtime_hours,
            self.maximum_attempts,
            self.cells,
        )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def maximum_compute_cost_usd(self) -> Decimal:
        return compute_maximum_compute_cost_usd(
            self.hourly_rate_usd,
            self.nodes,
            self.maximum_runtime_hours,
            self.maximum_attempts,
            self.cells,
        )


class CheckpointContract(ContractModel):
    interval_minutes: int = Field(gt=0)
    destination_prefix: str = Field(pattern=CHECKPOINT_DESTINATION_PREFIX_PATTERN)
    resume_required: bool


class ComputeProfile(ContractModel):
    name: str = Field(min_length=1)
    instance_type: str = Field(pattern=INSTANCE_TYPE_PATTERN)
    accelerator: Literal["cpu", "gpu"]
    nodes: int = Field(gt=0)
    hourly_rate_usd: PositiveStrictDecimal = Field(gt=0)
    pricing_source: str = Field(min_length=1)
    pricing_observed_at: str = Field(min_length=1)
    provisioned: bool
    #: Whether the only way to obtain this machine is to pre-pay for a dated window of it.
    #:
    #: **A DECLARED FIELD RATHER THAN A NAME PATTERN, AND RATHER THAN A RATE.** The thing that
    #: needs an admin is a purchase that cannot be undone, and neither of the two cheaper ways
    #: of recognising one is that fact. A name pattern would put the routing rule in a string
    #: a later profile can be spelled outside of, and the four block-backed names share no
    #: substring that the on-demand shapes do not: ``gpu-8xh200`` and ``gpu-8xl40s`` differ by
    #: nothing a regex could use. A rate ceiling was the previous instrument and was withdrawn
    #: in v5 for the reason ``config/policy.yaml`` records -- it made an approval class a
    #: function of a price that moves, so a repricing was a change of who may release a run,
    #: made by whoever edited a number.
    #:
    #: **AND IT IS NOT ``provisioned``, WHICH IS THE FIELD IT LOOKS LIKE.** ``provisioned``
    #: says whether a queue exists for this shape right now, and it is flipped twice per block:
    #: true when the stack goes up and false when the window closes. This says how the machine
    #: is obtained at all, and it does not move. A profile that read the first for the second
    #: would classify a live block as routine on exactly the days it is running, which is every
    #: day the branch matters.
    #:
    #: :func:`~edullm_platform.contracts.policy.classify_request` reads it through
    #: ``RequestFacts.capacity_block_backed``. Defaulted rather than required, which would
    #: normally be the wrong way round for a field whose false value is the weaker approval --
    #: and is safe here only because :meth:`WorkloadCatalog.validate_block_backing_matches_the_price_list`
    #: refuses a catalog where this disagrees with ``pricing_source``. Without that validator
    #: the default would be the hole: a fifth block shape priced by somebody who did not read
    #: this comment would route to a team lead, and nothing would say so.
    capacity_block_backed: bool = False


class WorkloadProfile(ContractModel):
    """A policy preset, which is a repository, two bounds and a checkpoint contract.

    IT NAMED A MACHINE AND THE NAMING WAS A FICTION, WHICH IS WHY THE FIELD IS GONE.
    ``compute_profile`` sat here and ``SubmissionInputs.compute_profile`` overrode it
    unconditionally, so what a run landed on was whatever the form said and this value was
    read only when the form left the field empty. Nothing refused a disagreement between
    the two, and nothing ever could: an override that is refused when it differs is not an
    override. So a researcher reading ``olmo-core-train-1gpu`` believed the name bound the
    run to one A10G while the dropdown beside it outranked the name in silence.

    Removing the field rather than enforcing it is the choice that follows from who owns
    the answer. The machine is the submitter's decision, made per run and priced per run;
    what a workload can honestly fix is the contract the codebase keeps -- how long it may
    take, how many attempts it may have, and whether an interrupted attempt has somewhere
    to resume from. Two entries differing only in a machine were therefore two spellings of
    one policy, and the catalog now carries one of each.

    :class:`~edullm_platform.contracts.manifest.RunManifest` is unaffected and keeps its
    own ``compute_profile``. It records what a run was submitted with, which is a different
    question from what a preset declares, and its serialized shape is content addressed.
    """

    name: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    maximum_runtime_hours: PositiveStrictDecimal = Field(gt=0)
    maximum_attempts: int = Field(ge=1)
    checkpoint: CheckpointContract | None

    @model_validator(mode="after")
    def validate_retry_checkpoint(self) -> Self:
        require_checkpoint_for_retries(
            maximum_attempts=self.maximum_attempts,
            checkpoint=self.checkpoint,
        )
        return self


class WorkloadCatalog(ContractModel):
    compute_profiles: Annotated[
        tuple[ComputeProfile, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=2, strict=False)
    workloads: Annotated[
        tuple[WorkloadProfile, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=2, strict=False)

    @model_validator(mode="after")
    def validate_catalog(self) -> Self:
        profile_names = {profile.name for profile in self.compute_profiles}
        if len(profile_names) != len(self.compute_profiles):
            raise ValueError("compute profile names must be unique")
        workload_names = {workload.name for workload in self.workloads}
        if len(workload_names) != len(self.workloads):
            raise ValueError("workload names must be unique")
        # ASKED OF THE PROFILES BECAUSE A WORKLOAD NO LONGER NAMES ONE. This required a
        # workload on a CPU profile and a workload on a GPU profile, through the join
        # WorkloadProfile.compute_profile used to carry. The property it was establishing
        # is that this platform can run both kinds of work, which the profiles answer on
        # their own, so it is asked where the accelerator is declared. A catalog that
        # priced only CPU shapes would offer no GPU on the submission form at all.
        accelerators = {profile.accelerator for profile in self.compute_profiles}
        if accelerators != {"cpu", "gpu"}:
            raise ValueError("representative CPU and GPU compute profiles are required")
        return self

    @model_validator(mode="after")
    def validate_block_backing_matches_the_price_list(self) -> Self:
        """Hold ``capacity_block_backed`` to the price list the row's rate was read off.

        THIS IS WHAT MAKES THE FLAG'S DEFAULT SAFE, and the flag is what decides who releases
        a run on the shape. ``classify_request`` sends a block-backed profile to a platform
        admin because the machine behind it is a non-cancellable four-figure purchase, so a
        flag left at its default on a fifth block shape would route several thousand dollars
        to whichever team lead was nearest. Nothing else in the tree would notice: the shape
        would price, check, admit and run, and the only symptom would be an approval that went
        to the wrong gate.

        Asked of the whole catalog rather than of one profile because that is where the
        comparison lives -- ``ComputeProfile`` could check its own two fields against each
        other, and putting it here keeps every rule about the shape of this file in one place
        and reports every disagreeing row at once instead of the first.
        """
        disagreeing = [
            f"{profile.name} is priced from {profile.pricing_source!r} and declares "
            f"capacity_block_backed: {str(profile.capacity_block_backed).lower()}"
            for profile in self.compute_profiles
            if (CAPACITY_BLOCK_PRICE_LIST in profile.pricing_source)
            != profile.capacity_block_backed
        ]
        if disagreeing:
            raise ValueError(
                "capacity_block_backed must be true for exactly the profiles priced from the "
                f"{CAPACITY_BLOCK_PRICE_LIST} price list, and these disagree: "
                + "; ".join(disagreeing)
            )
        return self


def compute_profile_is_capacity_block_backed(
    catalog: WorkloadCatalog,
    profile_name: str,
) -> bool:
    """Whether the shape this submission names is one only a purchased block provides.

    ``False`` for a name the catalog does not carry, which is the one place here that answers
    rather than raising. An unregistered profile is already a ``denied_outright`` condition and
    already holds a request back from the automatic class, so the submission is refused either
    way; raising would mean a typo in ``--compute`` came back as a traceback out of the
    classifier instead of as ``unregistered_compute_profile`` out of the check that owns that
    word. Compare :func:`resolve_compute_profile_for_execution`, which raises because its
    caller is about to submit a job.
    """
    for profile in catalog.compute_profiles:
        if profile.name == profile_name:
            return profile.capacity_block_backed
    return False


class ComputeProfileResolutionError(ValueError):
    reason_code: ClassVar[str]


class UnregisteredComputeProfileError(ComputeProfileResolutionError):
    reason_code: ClassVar[str] = "unregistered_compute_profile"


class UnprovisionedComputeProfileError(ComputeProfileResolutionError):
    reason_code: ClassVar[str] = "unprovisioned_compute_profile"


def resolve_compute_profile_for_execution(
    catalog: WorkloadCatalog,
    profile_name: str,
) -> ComputeProfile:
    profile_by_name = {profile.name: profile for profile in catalog.compute_profiles}
    profile = profile_by_name.get(profile_name)
    if profile is None:
        raise UnregisteredComputeProfileError(
            f"unregistered compute profile: {profile_name!r}"
        )
    if not profile.provisioned:
        raise UnprovisionedComputeProfileError(
            f"compute profile {profile_name!r} is priced in the catalog but no compute "
            f"environment is provisioned for {profile.instance_type}"
        )
    return profile
