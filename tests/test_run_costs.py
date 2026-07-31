"""That a run's cost is derived honestly, or not given at all.

The arithmetic is trivial and is not what these guard. What they guard is the three places
a cost report can be quietly wrong: counting the gap between two attempts as billed time,
totalling an unpriceable run as zero, and putting a forecast rate on a spot run as though
it were a measurement.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from edullm_platform.contracts.workload import ComputeProfile
from edullm_platform.run_costs import aggregate, run_costs, total_priced

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
