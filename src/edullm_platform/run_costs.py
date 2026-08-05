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

**A team is a string in a record until it is reconciled against the roster.** The ``team``
box on the submission form was free text and nothing refused an unrecognised value, so a
misspelling became a team with a spend line of its own, no lead attached to it and nothing to
tell it apart from a group that exists. That box is a dropdown over the declared groups now,
which closes the way new unbound names arrive and does nothing about the ones already
recorded: a decision record is immutable, three of them claim ``tokenizer`` and two claim
``evaluation``, and neither name is a declared group.
:func:`attribute_to_teams` groups against the ``TeamBindingCatalog`` in
``config/organization.yaml`` instead, so spend lands on a team that carries a lead, a GitHub
team and whatever attribution tags were recorded for it. Spend claimed against a name the
catalog does not carry is reported under that name rather than folded into anything,
because an unrecognised claim is a finding about the roster or about the form rather than a
rounding error. Where the catalog binds no teams at all every claim lands there, and that
is the answer rather than a fault.

**AND A CLAIM THAT NAMES A REAL GROUP CAN STILL BE WRONG, WHICH IS NEWER AND IS WHY
:class:`ContradictedClaim` EXISTS.** Until 2026-08-05 ``evaluate_authorization`` refused a
submitter whose recorded group was not the group their manifest claimed, so a run that
reached an attempt record had been checked. #221 removed that refusal, because it fired
inside admission, downstream of the approval gate, where it could spend a lead's signature
and never prevent any spend. What it left behind was ``team_verified`` on the decision
record, and :func:`attribute_to_teams` reads it.

**IT READS THE RECORD AND DOES NOT RE-ASK THE QUESTION, AND THAT IS THE WHOLE OF WHY THIS
SECTION IS TRUSTWORTHY.** It re-derived the contradiction for one release, from the submitter
on the intent record against ``member_logins`` as ``config/organization.yaml`` stood at the
moment the report ran. That answered a different question from the one the record answers.
``member_logins`` was empty for every group until 2026-08-02, so eighteen runs admitted on
2026-08-01 -- when nothing about anybody's membership was knowable and nothing could have
been mis-claimed -- were re-judged against a roster written after they ran and printed as
people charging work to other groups' budgets. A roster is edited; a decision record is
sealed. Only one of the two can say what was true when the money was spent.

**A FALSE FLAG IS NOT ALWAYS A VERDICT, WHICH IS THE ONE SUBTLETY IN READING IT.**
``evaluate_authorization`` computes ``team_verified`` as ``membership_is_knowable and
belongs_to_claimed_team``, so false covers two unlike facts: a claim the roster contradicted,
and a claim nothing was in a position to check. Before a submitter's group was written down,
every record they left carried false and none of them meant anything by it.

:func:`verified_from` answers the second question with the records and with nothing else. It
returns, per submitter, the earliest moment one of their own decision records carried
``team_verified`` true, which is the earliest moment their membership is known to have been
recorded. A false sealed before that is not a verdict. A false sealed after it is, because by
then the flag had demonstrably been able to say the other thing about that person. Somebody
whose records have never once said true has never been placed on a group this platform could
see, and none of their falses are verdicts. Three states reach :class:`RunCost`, not two:
true, false, and ``None`` for a run whose record carries no authorization block, has no
decision record at all, or predates that submitter's first verified claim.

Per submitter and not per moment, which is the part worth being deliberate about.
``member_logins`` was filled in for eight groups in one commit, so a single instant would
give the same answer today; it will not stay that way, because a person joining next month
gets their line written on the day they join and everything they ran before it must go on
reading as unknowable rather than becoming a contradiction the afternoon their lead types
their name. A per-submitter horizon is also the one thing here that cannot be moved by a
roster edit at all, which is the property that was missing.

A run with no verdict is neither named as contradicted nor counted as verified.
:attr:`TeamAttribution.without_verdict` counts them so that the silence is visible, because a
report that quietly dropped eighteen runs out of a finding would be the same shape of error
as the one that quietly invented them.

It is a report and not a gate: nothing here refuses anything, and a contradicted run is
counted into its claimed team's total, because moving it would be inventing an attribution
nobody recorded.

**AND IT IS A FLOOR, WITH A HOLE THAT IS WORTH STATING RATHER THAN HIDING.** A submitter who
is on a group and has never once claimed the group they are on leaves no true behind, so
their horizon never opens and every one of their contradictions is read as unknowable. That
is the price of a flag that collapses two facts into one bool, paid in the direction of
saying too little. The fix is upstream and not here: a decision record that recorded whether
membership was knowable, separately from whether the claim matched, would make this function
unnecessary. ``tools/report_onboarding_readiness.py`` names the people whose roster line is
missing, which is the other half of the same picture.

The roster is still read, and only to say where it places somebody today. That is printed
beside the verdict as a present-tense aside and is never the verdict, so a roster edit can
change who a reader is told to ask and cannot change who is accused.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext

from edullm_platform.contracts.admission import DecisionRecord, IntentRecord
from edullm_platform.contracts.bindings import AttributionTag, TeamBindingCatalog
from edullm_platform.contracts.lifecycle import SchedulerAttempt
from edullm_platform.contracts.workload import ComputeProfile

__all__ = [
    "SECONDS_PER_HOUR",
    "SPOT_PROFILE_SUFFIX",
    "ContradictedClaim",
    "RunCost",
    "TeamAttribution",
    "TeamSpend",
    "UnboundTeamSpend",
    "aggregate",
    "attribute_to_teams",
    "run_costs",
    "total_priced",
    "verified_from",
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
    #: What this run's decision record recorded about the team it claimed, and ``None``
    #: where that record has no verdict to give. The module docstring argues the three
    #: states at length; the short version is that ``team_verified`` false means two unlike
    #: things and only one of them is a finding, so a run whose record cannot be told apart
    #: gets neither answer rather than the wrong one. Defaulted so that the callers pricing
    #: runs without reading decision records -- ``tools/visibility_board.py`` and
    #: :mod:`edullm_platform.substrate` -- go on saying nothing about a question they do
    #: not ask.
    team_verified: bool | None = None

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


def verified_from(decisions: Iterable[DecisionRecord]) -> Mapping[str, datetime]:
    """When each submitter's membership is first known to have been recorded.

    The earliest ``recorded_at`` among that submitter's own decision records carrying
    ``team_verified`` true. Before it, a false on one of their records says only that
    nothing could check them; from it, a false says the check ran and disagreed. A
    submitter absent from the answer has never left a true behind and so has no moment
    from which their falses mean anything.

    Read off the records and off nothing else, which is the whole point. The same fact is
    in ``config/organization.yaml``'s history, and reading it from there would put a
    report's findings at the mercy of a file somebody edits: the eighteen runs this
    replaced were re-judged against a roster written the day after they ran. A submitter's
    own sealed records cannot be rewritten by a roster edit.

    Monotone by construction, and that is what makes it safe to apply to old runs. A
    membership once recorded is not usually withdrawn, and if it were, this would go on
    reporting from the first moment it was recorded rather than reaching a different verdict
    about a run that already happened.
    """
    earliest: dict[str, datetime] = {}
    for decision in decisions:
        authorization = decision.authorization
        if authorization is None or not authorization.team_verified:
            continue
        submitter = authorization.submitter
        recorded = earliest.get(submitter)
        if recorded is None or decision.recorded_at < recorded:
            earliest[submitter] = decision.recorded_at
    return earliest


def _recorded_verdicts(decisions: Sequence[DecisionRecord]) -> Mapping[str, bool]:
    """Each run's recorded answer about its team claim, for the runs that have one.

    A run missing from the answer has no verdict, and the three ways that happens are
    unlike each other and all real. There is no decision record for it, which is a hole in
    the lineage. Its record carries no authorization block at all, which happens exactly
    when the manifest hash did not match and nothing derived from the manifest was
    evaluated. Or its record predates :func:`verified_from` for that submitter, which is
    the eighteen.
    """
    horizons = verified_from(decisions)
    verdicts: dict[str, bool] = {}
    for decision in decisions:
        authorization = decision.authorization
        if authorization is None:
            continue
        if authorization.team_verified:
            verdicts[decision.run_id] = True
            continue
        opened = horizons.get(authorization.submitter)
        if opened is not None and decision.recorded_at >= opened:
            verdicts[decision.run_id] = False
    return verdicts


def run_costs(
    *,
    intents: Iterable[IntentRecord],
    attempts: Iterable[SchedulerAttempt],
    compute_profiles: Iterable[ComputeProfile],
    decisions: Iterable[DecisionRecord] = (),
) -> tuple[RunCost, ...]:
    """Every run that both declared itself and ran, priced where pricing is honest.

    A run with an intent and no attempt never reached an instance and is left out rather
    than reported at zero: zero is a cost, and "was refused" or "is still queued" is not
    the same fact. A run with an attempt and no intent cannot be attributed to a team at
    all, and is also left out -- the caller is told how many, because a growing count
    means the lineage has developed a hole.

    ``decisions`` carries the recorded answer to whether each run's team claim was
    verified, and defaults to none of them. A caller that does not hand them in gets runs
    whose :attr:`RunCost.team_verified` is ``None``, which is the truthful answer for a
    reading that never opened the ``decision/`` prefix rather than a silent false.
    """
    by_profile = {profile.name: profile for profile in compute_profiles}
    verdicts = _recorded_verdicts(tuple(decisions))
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
                team_verified=verdicts.get(intent.run_id),
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
    #: How many of ``runs`` carry a decision record saying the team they claimed was not
    #: verified. Counted into ``runs`` and ``cost_usd`` as well, because this is a statement
    #: about how reliable those two figures are rather than a subtraction from them.
    contradicted_runs: int = 0
    #: How much of ``cost_usd`` those runs carry. Unpriced ones are in ``contradicted_runs``
    #: and contribute nothing here, for the reason :func:`total_priced` gives.
    contradicted_cost_usd: Decimal = Decimal(0)


@dataclass(frozen=True)
class UnboundTeamSpend:
    """Spend booked against a team name nothing in the binding catalog carries.

    Held apart from :class:`TeamSpend` rather than shaped like it, because there is no lead,
    no GitHub team and no attribution tag to give: the roster has never heard of the name, or
    has been renamed since the record was written. A caller that wanted to render the two
    alike would have to invent those fields, which is the misattribution this separation
    prevents.
    """

    claimed_team: str
    cost_usd: Decimal
    runs: int
    unpriced_runs: int


@dataclass(frozen=True)
class ContradictedClaim:
    """One run whose own decision record says the group it claimed was never verified.

    Named per run rather than only counted, because the count says the split is off and only
    the run says by whose hand and by how much. The verdict comes from the record and the
    record alone; :attr:`recorded_teams` is an aside for the reader deciding who to ask.

    :attr:`recorded_teams` can be empty, unlike the version of this that derived the finding
    from the roster. Nothing about the roster today is load-bearing here any more, so a
    submitter it has stopped placing anywhere keeps the verdict their record carries and
    loses only the aside.
    """

    run_id: str
    submitter: str
    claimed_team: str
    #: Every group the roster records this submitter on **now**, in catalog order, which is
    #: not necessarily where it recorded them when the run was admitted.
    recorded_teams: tuple[str, ...]
    #: ``None`` where the run itself carries no figure, which is a spot run or a profile the
    #: catalog does not price. The claim is still contradicted and still worth naming.
    cost_usd: Decimal | None


@dataclass(frozen=True)
class TeamAttribution:
    """Every bound team's spend, every claim that matched none of them, and what is doubtful.

    ``bound`` and ``unbound`` are ordered by spend, highest first, with the name breaking a
    tie. That leaves the teams which spent nothing together at the end, where they read as a
    list of quiet groups rather than as an interruption.

    ``contradicted`` is ordered by run id, because it is a list somebody works through
    rather than one they read the top of.
    """

    bound: tuple[TeamSpend, ...]
    unbound: tuple[UnboundTeamSpend, ...]
    #: Every run inside ``bound`` whose decision record says its team claim was not verified.
    #: It is the same population the per-team ``contradicted_runs`` counts add up to, held
    #: once so that a renderer wanting the names does not recompute anything.
    contradicted: tuple[ContradictedClaim, ...] = ()
    #: How many runs inside ``bound`` carry no verdict either way. Reported rather than left
    #: implicit: this is the population the previous reading of ``team_verified`` printed as
    #: contradicted, and a report that stopped naming them without saying how many it had
    #: stopped naming would have replaced one silent error with another.
    without_verdict: int = 0

    @property
    def unbound_cost_usd(self) -> Decimal:
        return sum((entry.cost_usd for entry in self.unbound), Decimal(0))

    @property
    def unbound_runs(self) -> int:
        return sum(entry.runs for entry in self.unbound)

    @property
    def contradicted_cost_usd(self) -> Decimal:
        return sum((entry.cost_usd for entry in self.contradicted if entry.cost_usd), Decimal(0))


@dataclass
class _Tally:
    cost_usd: Decimal = Decimal(0)
    runs: int = 0
    unpriced_runs: int = 0
    contradicted_runs: int = 0
    contradicted_cost_usd: Decimal = Decimal(0)

    def add(self, entry: RunCost, *, contradicted: bool = False) -> None:
        self.runs += 1
        if entry.cost_usd is None:
            self.unpriced_runs += 1
        else:
            self.cost_usd += entry.cost_usd
        if not contradicted:
            return
        self.contradicted_runs += 1
        if entry.cost_usd is not None:
            self.contradicted_cost_usd += entry.cost_usd


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

    **WHO MAY CLAIM A GROUP IS ANSWERED FROM THE RECORD AND ASKED NOWHERE**, which the module
    docstring argues at length. :attr:`RunCost.team_verified` is what admission wrote at the
    time; false is a finding, true is not, and ``None`` is a run whose record has no verdict
    to give and which is therefore counted into ``without_verdict`` and named nowhere. The
    roster is consulted for one thing only, and it is not the finding: where it places this
    submitter today, printed as an aside beside a verdict that does not depend on it.
    """
    bound_by_id = {team.team_id: team for team in catalog.teams}
    bound_tallies = {team.team_id: _Tally() for team in catalog.teams}
    unbound_tallies: dict[str, _Tally] = {}
    contradicted: list[ContradictedClaim] = []
    without_verdict = 0
    for entry in costs:
        claimed = bound_by_id.get(entry.team)
        if claimed is None:
            unbound_tallies.setdefault(entry.team, _Tally()).add(entry)
            continue
        disputed = entry.team_verified is False
        if entry.team_verified is None:
            without_verdict += 1
        bound_tallies[entry.team].add(entry, contradicted=disputed)
        if disputed:
            contradicted.append(
                ContradictedClaim(
                    run_id=entry.run_id,
                    submitter=entry.submitter,
                    claimed_team=entry.team,
                    recorded_teams=tuple(
                        team.team_id for team in catalog.teams_for_member(entry.submitter)
                    ),
                    cost_usd=entry.cost_usd,
                )
            )

    bound = tuple(
        TeamSpend(
            team_id=team.team_id,
            github_team_slug=team.github_team_slug,
            lead_logins=team.lead_logins,
            attribution_tags=team.attribution_tags,
            cost_usd=bound_tallies[team.team_id].cost_usd,
            runs=bound_tallies[team.team_id].runs,
            unpriced_runs=bound_tallies[team.team_id].unpriced_runs,
            contradicted_runs=bound_tallies[team.team_id].contradicted_runs,
            contradicted_cost_usd=bound_tallies[team.team_id].contradicted_cost_usd,
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
        contradicted=tuple(sorted(contradicted, key=lambda claim: claim.run_id)),
        without_verdict=without_verdict,
    )
