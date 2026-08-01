"""What a run actually cost, derived from the lineage this platform already writes.

**This exists because AWS will not tell us.** Cost allocation tags are the ordinary answer
to "what did this team spend", and they are unavailable here: ``sbsandbox`` is a linked
account, and ``ce:ListCostAllocationTags`` answers ``Linked account doesn't have access to
cost allocation tags``. Activation happens in an organisation's management account, which
is somebody else's, and it is not retroactive even once granted -- so every GPU hour spent
before that request lands would be permanently unattributable.

Nothing needs to wait for it. Every input is already in the lineage bucket. An
``attempt/`` record carries ``started_at``, ``ended_at`` and ``attempt_ordinal``; an
``intent/`` record carries the manifest, and with it the team, the compute profile and the
submitter; ``config/workload-catalog.yaml`` carries the hourly rate and the node count for
every profile. The arithmetic is ``rate x nodes x duration``, summed over attempts.

**Three ways this is better than the tags would have been, which is worth saying so that
nobody treats it as a workaround.** It is retroactive over every run ever recorded, where
tag activation explicitly cannot backfill. It resolves to a single run and a single
attempt rather than to a monthly rollup. And it survives the roughly seven-day expiry of
AWS Batch job history, because the durations were captured into records this platform owns
at the moment they happened rather than read back from a scheduler that forgets.

**What it is not.** This is compute at the catalog's published rate, not the bill. It
excludes the minutes an instance spends starting before a container runs, the time it
stays warm afterwards, EBS, and data transfer. So it answers "what did Memory's training
cost" precisely and "what did AWS charge us" only approximately, and the second question
is answered by reading the ``Amazon EC2 - Compute`` line in Cost Explorer, which this
account can do.

**A spot profile is refused rather than estimated, and the reason is a deliberate lie
elsewhere.** A ``-spot`` compute profile is priced in the catalog at its on-demand rate, so
that the ceiling an approver is shown is one the run cannot exceed. That makes the catalog
rate the right number for a forecast and the wrong number for a bill: the actual spot price
moves continuously and is typically well below it. Reporting the on-demand figure as
*actual* spend would produce a number that is knowably wrong and indistinguishable from a
right one, so these runs are reported with no figure and the reason beside them.

**A team is a string somebody typed until it is reconciled against the roster.** The
``team`` box on the submission form is free text, and the form says as much: any lead may
approve any run, so a misspelling there delays nothing and nothing else refuses it. Grouped
by that string alone a misspelling becomes a team with a spend line of its own, no lead
attached to it and nothing to tell it apart from a group that exists.
:func:`attribute_to_teams` groups against the ``TeamBindingCatalog`` in
``config/organization.yaml`` instead, so spend lands on a team that carries a lead, a GitHub
team and whatever attribution tags were recorded for it. Spend claimed against a name the
catalog does not carry is reported under that name rather than folded into anything,
because an unrecognised claim is a finding about the roster or about the form rather than a
rounding error. Where the catalog binds no teams at all every claim lands there, and that
is the answer rather than a fault.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext

from edullm_platform.contracts.admission import IntentRecord
from edullm_platform.contracts.bindings import AttributionTag, TeamBindingCatalog
from edullm_platform.contracts.lifecycle import SchedulerAttempt
from edullm_platform.contracts.workload import ComputeProfile

__all__ = [
    "SECONDS_PER_HOUR",
    "SPOT_PROFILE_SUFFIX",
    "RunCost",
    "TeamAttribution",
    "TeamSpend",
    "UnboundTeamSpend",
    "aggregate",
    "attribute_to_teams",
    "run_costs",
    "total_priced",
]

SECONDS_PER_HOUR: int = 3600

#: How a compute profile says it buys interruptible capacity. Matched as a suffix rather
#: than held as a list, because the list would have to be edited in step with the catalog
#: and the failure of forgetting is a spot run reported at the on-demand rate as though it
#: were a measurement.
SPOT_PROFILE_SUFFIX: str = "-spot"


@dataclass(frozen=True)
class RunCost:
    """One run's measured duration, and its cost where a cost can honestly be given.

    ``cost_usd`` is ``None`` exactly when ``unpriced_reason`` is set, and the pair is what
    keeps a run visible in the report without a number attached. Dropping unpriceable runs
    instead would make a report that silently describes a subset, which is the shape of
    error this module exists to avoid.
    """

    run_id: str
    team: str
    submitter: str
    workload_profile: str
    compute_profile: str
    attempts: int
    seconds: Decimal
    cost_usd: Decimal | None
    unpriced_reason: str | None

    @property
    def priced(self) -> bool:
        return self.cost_usd is not None


def _attempt_seconds(attempts: Sequence[SchedulerAttempt]) -> Decimal:
    """Wall clock across every attempt of a run, in seconds.

    Summed rather than taken from first start to last end. A retried run is not billed for
    the gap between attempts -- no instance is held during it -- so the span would
    overstate a run that waited, and waiting is the ordinary case on a queue with fewer
    slots than people.
    """
    total = Decimal(0)
    for attempt in attempts:
        elapsed = attempt.ended_at - attempt.started_at
        total += Decimal(str(elapsed.total_seconds()))
    return total


def _cost(profile: ComputeProfile, seconds: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 28
        return (
            profile.hourly_rate_usd * profile.nodes * seconds / Decimal(SECONDS_PER_HOUR)
        ).quantize(Decimal("0.0001"))


def run_costs(
    *,
    intents: Iterable[IntentRecord],
    attempts: Iterable[SchedulerAttempt],
    compute_profiles: Iterable[ComputeProfile],
) -> tuple[RunCost, ...]:
    """Every run that both declared itself and ran, priced where pricing is honest.

    A run with an intent and no attempt never reached an instance and is left out rather
    than reported at zero: zero is a cost, and "was refused" or "is still queued" is not
    the same fact. A run with an attempt and no intent cannot be attributed to a team at
    all, and is also left out -- the caller is told how many, because a growing count
    means the lineage has developed a hole.
    """
    by_profile = {profile.name: profile for profile in compute_profiles}
    attempts_by_run: dict[str, list[SchedulerAttempt]] = {}
    for attempt in attempts:
        attempts_by_run.setdefault(attempt.run_id, []).append(attempt)

    costed: list[RunCost] = []
    for intent in intents:
        run_attempts = attempts_by_run.get(intent.run_id)
        if not run_attempts:
            continue
        manifest = intent.manifest
        seconds = _attempt_seconds(run_attempts)
        profile = by_profile.get(manifest.compute_profile)

        cost: Decimal | None
        reason: str | None
        if profile is None:
            cost, reason = None, (
                f"compute profile {manifest.compute_profile!r} is not in the catalog, so "
                "there is no rate to apply"
            )
        elif manifest.compute_profile.endswith(SPOT_PROFILE_SUFFIX):
            cost, reason = None, (
                f"{manifest.compute_profile!r} buys interruptible capacity and is priced "
                "in the catalog at its on-demand rate, which is the right number for the "
                "ceiling an approver was shown and the wrong number for what was spent"
            )
        else:
            cost, reason = _cost(profile, seconds), None

        costed.append(
            RunCost(
                run_id=intent.run_id,
                team=manifest.team,
                submitter=intent.submitter,
                workload_profile=manifest.workload_profile,
                compute_profile=manifest.compute_profile,
                attempts=len(run_attempts),
                seconds=seconds,
                cost_usd=cost,
                unpriced_reason=reason,
            )
        )
    return tuple(sorted(costed, key=lambda entry: entry.run_id))


def total_priced(costs: Iterable[RunCost]) -> Decimal:
    """The sum of what could be priced, and deliberately not of what could not.

    A total that quietly treated an unpriced run as zero would fall as spot adoption rose,
    which is the opposite of the truth.
    """
    return sum((entry.cost_usd for entry in costs if entry.cost_usd is not None), Decimal(0))


def aggregate(costs: Iterable[RunCost], *, key: str) -> Mapping[str, Decimal]:
    """Priced spend grouped by an attribute of the run, highest first when rendered.

    ``key`` is an attribute name on :class:`RunCost` rather than a callable so that a
    caller cannot group on something the record does not carry and get an empty report
    that looks like an answer.
    """
    grouped: dict[str, Decimal] = {}
    for entry in costs:
        if entry.cost_usd is None:
            continue
        grouped[getattr(entry, key)] = grouped.get(getattr(entry, key), Decimal(0)) + entry.cost_usd
    return grouped


@dataclass(frozen=True)
class TeamSpend:
    """What one bound team spent, with the roster's answer to who owns it.

    ``runs`` counts every run that claimed the team and ``unpriced_runs`` how many of those
    carry no figure, so a team whose whole month was spot work reads as busy with nothing
    priced rather than as idle. ``cost_usd`` is the sum of the priced ones only, for the
    reason :func:`total_priced` gives.
    """

    team_id: str
    github_team_slug: str
    lead_logins: tuple[str, ...]
    attribution_tags: tuple[AttributionTag, ...]
    cost_usd: Decimal
    runs: int
    unpriced_runs: int


@dataclass(frozen=True)
class UnboundTeamSpend:
    """Spend booked against a team name nothing in the binding catalog carries.

    Held apart from :class:`TeamSpend` rather than shaped like it, because there is no lead,
    no GitHub team and no attribution tag to give: the name came out of a free-text box and
    the roster has never heard of it. A caller that wanted to render the two alike would
    have to invent those fields, which is the misattribution this separation prevents.
    """

    claimed_team: str
    cost_usd: Decimal
    runs: int
    unpriced_runs: int


@dataclass(frozen=True)
class TeamAttribution:
    """Every bound team's spend, and every claim that matched none of them.

    Both sequences are ordered by spend, highest first, with the name breaking a tie. That
    leaves the teams which spent nothing together at the end, where they read as a list of
    quiet groups rather than as an interruption.
    """

    bound: tuple[TeamSpend, ...]
    unbound: tuple[UnboundTeamSpend, ...]

    @property
    def unbound_cost_usd(self) -> Decimal:
        return sum((entry.cost_usd for entry in self.unbound), Decimal(0))

    @property
    def unbound_runs(self) -> int:
        return sum(entry.runs for entry in self.unbound)


@dataclass
class _Tally:
    cost_usd: Decimal = Decimal(0)
    runs: int = 0
    unpriced_runs: int = 0

    def add(self, entry: RunCost) -> None:
        self.runs += 1
        if entry.cost_usd is None:
            self.unpriced_runs += 1
        else:
            self.cost_usd += entry.cost_usd


def attribute_to_teams(
    costs: Iterable[RunCost], *, catalog: TeamBindingCatalog
) -> TeamAttribution:
    """Spend per bound team, reconciled against the roster, with every stray claim named.

    A bound team that ran nothing is returned at zero rather than omitted. Spending nothing
    and being a team nobody has heard of are different facts, and a report that dropped the
    first could not tell a reader that a group had gone quiet.

    A claim is matched to a binding by exact team id and by nothing else. Casefolding it or
    matching it loosely would launder the misspellings this reconciliation exists to
    surface, and a run attributed to the wrong team is indistinguishable from a correctly
    attributed one.
    """
    bound_tallies = {team.team_id: _Tally() for team in catalog.teams}
    unbound_tallies: dict[str, _Tally] = {}
    for entry in costs:
        tally = bound_tallies.get(entry.team)
        if tally is None:
            tally = unbound_tallies.setdefault(entry.team, _Tally())
        tally.add(entry)

    bound = tuple(
        TeamSpend(
            team_id=team.team_id,
            github_team_slug=team.github_team_slug,
            lead_logins=team.lead_logins,
            attribution_tags=team.attribution_tags,
            cost_usd=bound_tallies[team.team_id].cost_usd,
            runs=bound_tallies[team.team_id].runs,
            unpriced_runs=bound_tallies[team.team_id].unpriced_runs,
        )
        for team in catalog.teams
    )
    unbound = tuple(
        UnboundTeamSpend(
            claimed_team=claimed,
            cost_usd=tally.cost_usd,
            runs=tally.runs,
            unpriced_runs=tally.unpriced_runs,
        )
        for claimed, tally in unbound_tallies.items()
    )
    return TeamAttribution(
        # Ordered here rather than at each call site, so that two readings of the same spend
        # cannot disagree about who is at the top of it.
        bound=tuple(sorted(bound, key=lambda spend: (-spend.cost_usd, spend.team_id))),
        unbound=tuple(
            sorted(unbound, key=lambda spend: (-spend.cost_usd, spend.claimed_team))
        ),
    )
