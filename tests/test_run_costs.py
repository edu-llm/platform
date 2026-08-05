"""That a run's cost is derived honestly, or not given at all, and lands on the right team.

The arithmetic is trivial and is not what these guard. What they guard is the four places
a cost report can be quietly wrong: counting the gap between two attempts as billed time,
totalling an unpriceable run as zero, putting a forecast rate on a spot run as though it
were a measurement, and booking spend against a team the roster does not carry. The form no
longer lets a new run claim one, and the records that already do cannot be edited, so the
last of the four is now about history rather than about typing.

The bindings these build are constructed here rather than read out of
``config/organization.yaml``. What that file happens to bind today is a roster decision that
will change, and a test that depended on it would start asserting the roster instead of the
rollup. Both states matter and both are covered: a catalog with teams in it, and the empty
one the file currently produces.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from edullm_platform.contracts.bindings import TeamBinding, TeamBindingCatalog
from edullm_platform.contracts.workload import ComputeProfile
from edullm_platform.run_costs import (
    RunCost,
    aggregate,
    attribute_to_teams,
    run_costs,
    total_priced,
)

RATE = Decimal("1.0060")


def profile(name: str = "gpu-1xa10g", *, nodes: int = 1) -> ComputeProfile:
    return ComputeProfile(
        name=name,
        instance_type="g5.xlarge",
        accelerator="gpu",
        nodes=nodes,
        hourly_rate_usd=RATE,
        pricing_source="test",
        pricing_observed_at="2026-07-31",
        provisioned=True,
    )


class FakeAttempt:
    """Only the four fields :func:`run_costs` reads.

    A real ``SchedulerAttempt`` needs a UUIDv7-shaped attempt id derived from a digest of
    the run, the job and the ordinal, which is machinery this module does not touch. Faking
    the read surface keeps these tests about the arithmetic rather than about id
    construction, which has its own tests.
    """

    def __init__(self, run_id: str, started: datetime, ended: datetime) -> None:
        self.run_id = run_id
        self.started_at = started
        self.ended_at = ended


class FakeManifest:
    def __init__(self, compute_profile: str, team: str = "memory-split") -> None:
        self.compute_profile = compute_profile
        self.team = team
        self.workload_profile = "olmo-core-train-1gpu"


class FakeIntent:
    def __init__(self, run_id: str, manifest: FakeManifest, submitter: str = "nzhao721") -> None:
        self.run_id = run_id
        self.manifest = manifest
        self.submitter = submitter


START = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def test_an_hour_on_one_node_costs_the_hourly_rate() -> None:
    costs = run_costs(
        intents=[FakeIntent("run_a", FakeManifest("gpu-1xa10g"))],
        attempts=[FakeAttempt("run_a", START, START + timedelta(hours=1))],
        compute_profiles=[profile()],
    )

    assert len(costs) == 1
    assert costs[0].cost_usd == RATE.quantize(Decimal("0.0001"))


def test_the_gap_between_two_attempts_is_not_billed() -> None:
    """Mutation: measure from the first start to the last end.

    A retried run holds no instance while it waits to be retried, so the span overstates
    it. On a queue with fewer slots than people the wait is the ordinary case, not the
    exception, so this is the difference between a report and a fiction.
    """
    first = FakeAttempt("run_a", START, START + timedelta(hours=1))
    second = FakeAttempt("run_a", START + timedelta(hours=5), START + timedelta(hours=6))

    costs = run_costs(
        intents=[FakeIntent("run_a", FakeManifest("gpu-1xa10g"))],
        attempts=[first, second],
        compute_profiles=[profile()],
    )

    assert costs[0].attempts == 2
    assert costs[0].seconds == Decimal("7200.0")
    assert costs[0].cost_usd == (RATE * 2).quantize(Decimal("0.0001"))


def test_nodes_multiply() -> None:
    costs = run_costs(
        intents=[FakeIntent("run_a", FakeManifest("gpu-8xa100"))],
        attempts=[FakeAttempt("run_a", START, START + timedelta(hours=1))],
        compute_profiles=[profile("gpu-8xa100", nodes=4)],
    )

    assert costs[0].cost_usd == (RATE * 4).quantize(Decimal("0.0001"))


def test_a_spot_run_is_reported_with_no_figure_and_a_reason() -> None:
    """Mutation: price it at the catalog rate like everything else.

    A ``-spot`` profile carries its on-demand rate deliberately, so that the ceiling shown
    to an approver is one the run cannot exceed. That makes the catalog number right for a
    forecast and wrong for a bill, and a wrong number here is indistinguishable from a
    right one.
    """
    costs = run_costs(
        intents=[FakeIntent("run_a", FakeManifest("gpu-8xa100-spot"))],
        attempts=[FakeAttempt("run_a", START, START + timedelta(hours=1))],
        compute_profiles=[profile("gpu-8xa100-spot")],
    )

    assert costs[0].cost_usd is None
    assert costs[0].priced is False
    assert "on-demand rate" in (costs[0].unpriced_reason or "")
    assert costs[0].seconds == Decimal("3600.0"), "the duration is still a measurement"


def test_an_unpriced_run_is_not_totalled_as_zero() -> None:
    """Mutation: sum ``cost_usd or 0``.

    A total that treats unpriceable as zero falls as spot adoption rises, which is the
    opposite of the truth and the kind of wrong nobody checks.
    """
    costs = run_costs(
        intents=[
            FakeIntent("run_a", FakeManifest("gpu-1xa10g")),
            FakeIntent("run_b", FakeManifest("gpu-1xa10g-spot"), submitter="katiehehe"),
        ],
        attempts=[
            FakeAttempt("run_a", START, START + timedelta(hours=1)),
            FakeAttempt("run_b", START, START + timedelta(hours=10)),
        ],
        compute_profiles=[profile(), profile("gpu-1xa10g-spot")],
    )

    assert total_priced(costs) == RATE.quantize(Decimal("0.0001"))
    assert aggregate(costs, key="submitter") == {"nzhao721": RATE.quantize(Decimal("0.0001"))}


def test_a_profile_the_catalog_does_not_carry_is_named_rather_than_guessed() -> None:
    costs = run_costs(
        intents=[FakeIntent("run_a", FakeManifest("gpu-99xfuture"))],
        attempts=[FakeAttempt("run_a", START, START + timedelta(hours=1))],
        compute_profiles=[profile()],
    )

    assert costs[0].cost_usd is None
    assert "not in the catalog" in (costs[0].unpriced_reason or "")


def test_a_run_that_never_reached_an_instance_is_left_out_rather_than_reported_as_free() -> None:
    """Mutation: report it at zero.

    Zero is a cost. "Was refused before admission" and "is still queued" are not costs,
    and a report that renders them as $0.00 invites the reading that they ran and were
    free.
    """
    costs = run_costs(
        intents=[FakeIntent("run_a", FakeManifest("gpu-1xa10g"))],
        attempts=[],
        compute_profiles=[profile()],
    )

    assert costs == ()


# ---------------------------------------------------------------------------------------
# Whose spend it is, once the team a record claims is reconciled against the roster
# ---------------------------------------------------------------------------------------


def binding(
    team_id: str, *, leads: tuple[str, ...] = ("ericrcwu001",), **overrides: object
) -> TeamBinding:
    payload: dict[str, object] = {
        "team_id": team_id,
        "github_team_slug": team_id,
        "lead_logins": list(leads),
        "s3_namespace": f"sbsandbox-intern-{team_id}",
        "wandb_entity": f"edu-llm-{team_id}",
    }
    payload.update(overrides)
    return TeamBinding.model_validate(payload)


def spend(
    run_id: str, team: str, *, usd: str | None = "10.0000", submitter: str = "nzhao721"
) -> RunCost:
    """One run's cost, built directly so that these are about the rollup and not the rate."""
    return RunCost(
        run_id=run_id,
        team=team,
        submitter=submitter,
        workload_profile="olmo-core-train-1gpu",
        compute_profile="gpu-1xa10g",
        attempts=1,
        seconds=Decimal("3600.0"),
        cost_usd=None if usd is None else Decimal(usd),
        unpriced_reason=None if usd is not None else "buys interruptible capacity",
    )


def test_spend_rolls_up_onto_the_team_the_roster_binds() -> None:
    """The team a report names is one the platform can route, not one it was handed."""
    catalog = TeamBindingCatalog(
        teams=(
            binding(
                "memory-split",
                attribution_tags=[{"key": "cost-center", "value": "research"}],
            ),
            binding("curriculum", leads=("alsy7009", "meric233")),
        )
    )

    attribution = attribute_to_teams(
        [
            spend("run_a", "memory-split", usd="4.0000"),
            spend("run_b", "memory-split", usd="2.0000"),
            spend("run_c", "curriculum", usd="1.0000"),
        ],
        catalog=catalog,
    )

    assert attribution.unbound == ()
    memory, curriculum = attribution.bound
    assert (memory.team_id, memory.cost_usd, memory.runs) == (
        "memory-split",
        Decimal("6.0000"),
        2,
    )
    assert memory.github_team_slug == "memory-split"
    assert memory.lead_logins == ("ericrcwu001",)
    assert tuple((tag.key, tag.value) for tag in memory.attribution_tags) == (
        ("cost-center", "research"),
    )
    assert curriculum.lead_logins == ("alsy7009", "meric233"), (
        "a team with two leads keeps both, because either of them can be asked about it"
    )


def test_spend_claimed_against_an_unbound_team_is_named_rather_than_folded_in() -> None:
    """Mutation: fold unbound team spend into the nearest bound team.

    An unrecognised team claim is a finding, not a rounding error: it is either a group the
    roster has not been told about or a misspelling in a form field nothing validates. Added
    to a bound team it becomes that team's spend, which is indistinguishable from spend that
    team actually incurred and is only ever noticed by the lead who did not incur it.
    """
    catalog = TeamBindingCatalog(teams=(binding("memory-split"),))

    attribution = attribute_to_teams(
        [
            spend("run_a", "memory-split", usd="4.0000"),
            spend("run_b", "tokenizer", usd="7.0000"),
            spend("run_c", "tokenizer", usd="1.0000"),
        ],
        catalog=catalog,
    )

    assert [entry.team_id for entry in attribution.bound] == ["memory-split"]
    assert attribution.bound[0].cost_usd == Decimal("4.0000")
    assert [entry.claimed_team for entry in attribution.unbound] == ["tokenizer"]
    assert attribution.unbound[0].cost_usd == Decimal("8.0000")
    assert attribution.unbound[0].runs == 2


def test_a_bound_team_with_no_runs_still_appears_with_zero() -> None:
    """Mutation: return only the teams that spend was found for.

    Spending nothing and being a team nobody has heard of are different facts. A report
    that omitted the first could not tell a reader that a group had gone quiet, which is
    the one thing a per-team rollup is read for besides the total.
    """
    catalog = TeamBindingCatalog(teams=(binding("memory-split"), binding("learning-science")))

    attribution = attribute_to_teams([spend("run_a", "memory-split")], catalog=catalog)

    quiet = attribution.bound[-1]
    assert quiet.team_id == "learning-science"
    assert quiet.cost_usd == Decimal(0)
    assert (quiet.runs, quiet.unpriced_runs) == (0, 0)
    assert quiet.lead_logins == ("ericrcwu001",), (
        "a team that has gone quiet still names who to ask about it"
    )


def test_a_team_claim_that_differs_only_in_spelling_is_not_matched_to_a_bound_team() -> None:
    """Mutation: casefold or fuzzy-match the claim before looking it up.

    Matching loosely would launder exactly the typos this reconciliation exists to surface.
    The form's own help text says a typo in the team box delays nothing, so these arrive.
    """
    catalog = TeamBindingCatalog(teams=(binding("memory-split"),))

    attribution = attribute_to_teams(
        [spend("run_a", "Memory-Split"), spend("run_b", "memory-splt")], catalog=catalog
    )

    assert attribution.bound[0].cost_usd == Decimal(0)
    assert [entry.claimed_team for entry in attribution.unbound] == [
        "Memory-Split",
        "memory-splt",
    ]


def test_an_unpriced_run_is_counted_against_its_team_without_being_summed() -> None:
    """Mutation: sum ``cost_usd or 0``, or drop the run from the team's count.

    Both directions mislead. Summed as zero, a team's figure falls as it moves onto spot;
    dropped, a team doing nothing but spot work reads as idle rather than as busy with
    nothing priced.
    """
    catalog = TeamBindingCatalog(teams=(binding("memory-split"),))

    attribution = attribute_to_teams(
        [
            spend("run_a", "memory-split", usd="3.0000"),
            spend("run_b", "memory-split", usd=None),
        ],
        catalog=catalog,
    )

    only = attribution.bound[0]
    assert only.cost_usd == Decimal("3.0000")
    assert (only.runs, only.unpriced_runs) == (2, 1)


def test_an_empty_catalog_leaves_every_claim_unbound_rather_than_dropping_it() -> None:
    """What ``config/organization.yaml`` produces today, and the right answer for it.

    ``team_bindings`` is absent from the roster, so the catalog binds nothing and every run
    on the platform has claimed a team that cannot be resolved. Reporting all of it as
    unbound is informative. Reporting none of it would be a report that silently described
    an empty subset while still printing a total.
    """
    attribution = attribute_to_teams(
        [spend("run_a", "memory-split", usd="4.0000"), spend("run_b", "tokenizer", usd=None)],
        catalog=TeamBindingCatalog(),
    )

    assert attribution.bound == ()
    assert [entry.claimed_team for entry in attribution.unbound] == ["memory-split", "tokenizer"]
    assert attribution.unbound_cost_usd == Decimal("4.0000")
    assert attribution.unbound_runs == 2


def test_bound_and_unbound_spend_add_up_to_what_was_priced() -> None:
    """Mutation: drop a claim that matches nothing instead of tallying it.

    The rollup and the total are read side by side, so the two disagreeing is the shape of
    error that gets argued about rather than found. Nothing may go missing between them.
    """
    costs = [
        spend("run_a", "memory-split", usd="4.0000"),
        spend("run_b", "tokenizer", usd="7.5000"),
        spend("run_c", "memory-split", usd=None),
    ]
    catalog = TeamBindingCatalog(teams=(binding("memory-split"), binding("learning-science")))

    attribution = attribute_to_teams(costs, catalog=catalog)

    tallied = sum((entry.cost_usd for entry in attribution.bound), Decimal(0))
    assert tallied + attribution.unbound_cost_usd == total_priced(costs)


# ---------------------------------------------------------------------------------------
# How much of the split was claimed by somebody the roster records on another group
# ---------------------------------------------------------------------------------------
#
# #221 removed the only thing that compared a claimed group against the roster inside AWS.
# It fired past the approval gate, so it never prevented spend and only ever wasted a
# lead's signature, and what it left behind was `team_verified` on the decision record with
# nothing reading it. These are about the split saying how far it can be trusted, and about
# the three ways that measurement could quietly become either a gate or a fiction.


def rostered(team_id: str, *, members: tuple[str, ...]) -> TeamBinding:
    return binding(team_id, member_logins=list(members))


def test_a_claim_the_roster_contradicts_is_counted_and_the_run_is_named() -> None:
    """The whole of what replaced the refusal, and it has to carry the run.

    A count says the split is off. Only the run says by whose hand, on which group, and by
    how much, and those are what somebody fixes a roster line or a habit from.
    """
    catalog = TeamBindingCatalog(
        teams=(
            rostered("memory-split", members=("katiehehe",)),
            rostered("curriculum", members=("nzhao721",)),
        )
    )

    attribution = attribute_to_teams(
        [spend("run_a", "memory-split", usd="4.0000", submitter="nzhao721")],
        catalog=catalog,
    )

    memory = next(entry for entry in attribution.bound if entry.team_id == "memory-split")
    assert (memory.contradicted_runs, memory.contradicted_cost_usd) == (1, Decimal("4.0000"))
    claim = attribution.contradicted[0]
    assert (claim.run_id, claim.submitter, claim.claimed_team) == (
        "run_a",
        "nzhao721",
        "memory-split",
    )
    assert claim.recorded_teams == ("curriculum",)
    assert claim.cost_usd == Decimal("4.0000")


def test_a_contradicted_run_stays_in_the_total_it_was_claimed_against() -> None:
    """Mutation: deduct the contradicted spend, or move it onto the submitter's own group.

    Both invent an attribution no record supports. The run was charged to the group its
    manifest named and nothing since has said otherwise, so moving it would put spend on a
    lead's line that their group did not ask for. This reports how far the figure can be
    trusted and changes the figure by nothing.
    """
    catalog = TeamBindingCatalog(
        teams=(
            rostered("memory-split", members=("katiehehe",)),
            rostered("curriculum", members=("nzhao721",)),
        )
    )

    attribution = attribute_to_teams(
        [
            spend("run_a", "memory-split", usd="4.0000", submitter="nzhao721"),
            spend("run_b", "memory-split", usd="1.0000", submitter="katiehehe"),
        ],
        catalog=catalog,
    )

    memory = next(entry for entry in attribution.bound if entry.team_id == "memory-split")
    curriculum = next(entry for entry in attribution.bound if entry.team_id == "curriculum")
    assert (memory.cost_usd, memory.runs) == (Decimal("5.0000"), 2)
    assert (curriculum.cost_usd, curriculum.runs) == (Decimal(0), 0)


def test_a_submitter_the_roster_places_nowhere_contradicts_nothing() -> None:
    """Mutation: count every run whose ``team_verified`` would be false.

    ``team_verified`` is false in two unlike cases, and only one of them is a claim anybody
    can dispute. Somebody the roster records on no group is not misattributing spend, they
    are waiting on a lead to write one line, and ``tools/report_onboarding_readiness.py``
    already names them. Counting them here would report most of the pilot as booking spend
    to groups they are not on, which is both false and the fastest way to make the real
    number unreadable.
    """
    catalog = TeamBindingCatalog(teams=(rostered("memory-split", members=("katiehehe",)),))

    attribution = attribute_to_teams(
        [spend("run_a", "memory-split", usd="4.0000", submitter="unrostered-person")],
        catalog=catalog,
    )

    assert attribution.contradicted == ()
    assert attribution.bound[0].contradicted_runs == 0
    assert attribution.bound[0].cost_usd == Decimal("4.0000")


def test_a_lead_of_the_claimed_group_is_on_it_for_this_purpose() -> None:
    """``TeamBinding.includes`` reads leads and members, and that is the shared primitive.

    A lead is not usually in ``member_logins`` and is unmistakably on the group. Asking
    ``member_logins`` alone here would report every lead's own run as misattributed, and it
    would also be a second spelling of a comparison ``cli.preflight._check_team`` and
    ``teams_for_member`` already make one way.
    """
    catalog = TeamBindingCatalog(
        teams=(
            binding("memory-split", leads=("ericrcwu001",), member_logins=["katiehehe"]),
            rostered("curriculum", members=("nzhao721",)),
        )
    )

    attribution = attribute_to_teams(
        [spend("run_a", "memory-split", usd="4.0000", submitter="ericrcwu001")],
        catalog=catalog,
    )

    assert attribution.contradicted == ()


def test_a_contradicted_run_with_no_figure_is_counted_without_adding_money() -> None:
    """Mutation: skip the unpriced ones, or total them as zero into the money.

    A spot run carries no honest figure and the claim on it is contradicted just the same.
    Dropping it understates how much of the split is doubtful. Summing a zero into the money
    is the mistake ``total_priced`` refuses everywhere else, so it is refused here too.
    """
    catalog = TeamBindingCatalog(
        teams=(
            rostered("memory-split", members=("katiehehe",)),
            rostered("curriculum", members=("nzhao721",)),
        )
    )

    attribution = attribute_to_teams(
        [spend("run_a", "memory-split", usd=None, submitter="nzhao721")],
        catalog=catalog,
    )

    memory = next(entry for entry in attribution.bound if entry.team_id == "memory-split")
    assert (memory.contradicted_runs, memory.contradicted_cost_usd) == (1, Decimal(0))
    assert memory.unpriced_runs == 1
    assert attribution.contradicted[0].cost_usd is None
    assert attribution.contradicted_cost_usd == Decimal(0)


def test_a_claim_on_a_group_nothing_binds_is_not_reported_as_a_contradiction() -> None:
    """Mutation: run the membership comparison over the unbound claims too.

    Nobody is on a group the catalog does not carry, so every unbound claim would answer
    yes and the finding would swamp the one this measures. The unbound section already says
    the whole of that spend is unroutable, which is the stronger statement.
    """
    catalog = TeamBindingCatalog(teams=(rostered("curriculum", members=("nzhao721",)),))

    attribution = attribute_to_teams(
        [spend("run_a", "tokenizer", usd="4.0000", submitter="nzhao721")], catalog=catalog
    )

    assert attribution.contradicted == ()
    assert [entry.claimed_team for entry in attribution.unbound] == ["tokenizer"]


def test_the_per_team_counts_and_the_named_runs_describe_one_population() -> None:
    """Mutation: build the list and the counters from two passes that can disagree.

    The section prints both, so two readings of one fact sitting on the same page is the
    shape of error that gets argued about rather than found.
    """
    catalog = TeamBindingCatalog(
        teams=(
            rostered("memory-split", members=("katiehehe",)),
            rostered("curriculum", members=("nzhao721",)),
        )
    )

    attribution = attribute_to_teams(
        [
            spend("run_c", "memory-split", usd="4.0000", submitter="nzhao721"),
            spend("run_a", "curriculum", usd="2.0000", submitter="katiehehe"),
            spend("run_b", "curriculum", usd="1.0000", submitter="nzhao721"),
        ],
        catalog=catalog,
    )

    assert sum(entry.contradicted_runs for entry in attribution.bound) == len(
        attribution.contradicted
    )
    assert sum(
        (entry.contradicted_cost_usd for entry in attribution.bound), Decimal(0)
    ) == attribution.contradicted_cost_usd
    assert [claim.run_id for claim in attribution.contradicted] == ["run_a", "run_c"], (
        "ordered by run id, because it is a list somebody works through"
    )
