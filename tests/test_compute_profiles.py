from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from edullm_platform.config import load_yaml
from edullm_platform.contracts.policy import (
    ApprovalClass,
    ApprovalPolicy,
    RequestFacts,
    classify_request,
)
from edullm_platform.contracts.workload import (
    ComputeProfile,
    CostInputs,
    UnprovisionedComputeProfileError,
    UnregisteredComputeProfileError,
    WorkloadCatalog,
    resolve_compute_profile_for_execution,
)
from edullm_platform.evidence import (
    INSTANCE_EVIDENCE,
    QuotaRecord,
    ec2_quota_coverage_issues,
    profiles_requiring_capacity_evidence,
)
from edullm_platform.manifest_helpers import (
    REPRESENTATIVE_MANIFEST_COSTS,
    compute_manifest_maximum_cost,
    load_manifests_from_directory,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CENTS = Decimal("0.01")

PRESERVED_PROFILE_RATES = {
    "cpu-32vcpu": Decimal("1.428"),
    "gpu-4xa10g": Decimal("5.672"),
}

PRESERVED_MANIFEST_COSTS = {
    "cpu-routine.yaml": Decimal("2.86"),
    "gpu-routine.yaml": Decimal("5.67"),
    # Was 73.74. The fixture exists to be an exception on runtime alone and nothing else, so
    # when routine_maximum_runtime_hours went from 12 to 24 its bound went from thirteen
    # hours to twenty-five and its ceiling with it. The other two are unmoved, which is what
    # this table is for: a rate or a bound that drifted would show up as a third change here.
    "gpu-exception.yaml": Decimal("141.80"),
}

EXPECTED_PROFILE_RATES = {
    "cpu-32vcpu": ("c7i.8xlarge", Decimal("1.428")),
    "gpu-1xt4": ("g4dn.xlarge", Decimal("0.5260")),
    "gpu-4xt4": ("g4dn.12xlarge", Decimal("3.9120")),
    "gpu-8xt4": ("g4dn.metal", Decimal("7.8240")),
    "gpu-1xa10g": ("g5.xlarge", Decimal("1.0060")),
    "gpu-1xa10g-sagemaker": ("g5.2xlarge", Decimal("1.5150")),
    "gpu-4xa10g": ("g5.12xlarge", Decimal("5.672")),
    "gpu-8xa10g": ("g5.48xlarge", Decimal("16.2880")),
    "gpu-1xl4": ("g6.xlarge", Decimal("0.8048")),
    "gpu-4xl4": ("g6.12xlarge", Decimal("4.6016")),
    "gpu-8xl4": ("g6.48xlarge", Decimal("13.3504")),
    "gpu-1xl40s": ("g6e.xlarge", Decimal("1.8610")),
    "gpu-4xl40s": ("g6e.12xlarge", Decimal("10.4926")),
    "gpu-8xl40s": ("g6e.48xlarge", Decimal("30.1312")),
    "gpu-1xh100": ("p5.4xlarge", Decimal("6.8800")),
    "gpu-8xa100": ("p4d.24xlarge", Decimal("21.9576")),
    "gpu-8xh100": ("p5.48xlarge", Decimal("55.0400")),
    # THE FOUR BLOCK-BACKED SHAPES, WHOSE RATES ARE A DIFFERENT PRODUCT TO THE SEVENTEEN ABOVE.
    # Every rate above is on-demand, out of the Price List API. These four are AWS's published
    # effective hourly reservation rate for a capacity block, because a capacity block is the
    # only way this account can obtain any of them -- config/workload-catalog.yaml argues that
    # at length above the rows themselves.
    #
    # All four are over EXCEPTION_RATE_CEILING_USD_PER_HOUR, so all four are admin-only whatever
    # else a request says. That is the fact this table is really pinning.
    "gpu-8xa100-80gb": ("p4de.24xlarge", Decimal("17.7120")),
    "gpu-8xh200": ("p5en.48xlarge", Decimal("54.9200")),
    "gpu-8xb200": ("p6-b200.48xlarge", Decimal("98.8400")),
    "gpu-8xb300": ("p6-b300.48xlarge", Decimal("112.3200")),
}


def shipped_catalog() -> WorkloadCatalog:
    return load_yaml(PROJECT_ROOT / "config" / "workload-catalog.yaml", WorkloadCatalog)


def shipped_policy() -> ApprovalPolicy:
    return load_yaml(PROJECT_ROOT / "config" / "policy.yaml", ApprovalPolicy)


SHIPPED_PROFILES: tuple[ComputeProfile, ...] = shipped_catalog().compute_profiles
PROFILE_IDS: list[str] = [profile.name for profile in SHIPPED_PROFILES]


def facts_for_profile(
    profile: ComputeProfile,
    *,
    maximum_runtime_hours: Decimal,
    maximum_attempts: int,
) -> RequestFacts:
    estimated_cost = CostInputs(
        hourly_rate_usd=profile.hourly_rate_usd,
        nodes=profile.nodes,
        maximum_runtime_hours=maximum_runtime_hours,
        maximum_attempts=maximum_attempts,
    ).maximum_compute_cost_usd
    return RequestFacts(
        claimed_team="modeling",
        repository_registered=True,
        dataset_registered=True,
        dataset_is_a_corpus=True,
        compute_profile_registered=True,
        immutable_revision=True,
        immutable_image=True,
        image_scan_reviewed=True,
        estimated_cost_usd=estimated_cost,
        maximum_runtime_hours=maximum_runtime_hours,
        maximum_attempts=maximum_attempts,
    )


@pytest.mark.parametrize("profile", SHIPPED_PROFILES, ids=PROFILE_IDS)
def test_shipped_profile_carries_dated_positive_pricing(profile: ComputeProfile) -> None:
    assert profile.hourly_rate_usd > 0
    assert profile.pricing_source.strip()
    assert profile.instance_type in profile.pricing_source
    assert date.fromisoformat(profile.pricing_observed_at).isoformat() == (
        profile.pricing_observed_at
    )


@pytest.mark.parametrize("profile", SHIPPED_PROFILES, ids=PROFILE_IDS)
def test_shipped_profile_cost_is_deterministic_and_quantized_to_cents(
    profile: ComputeProfile,
) -> None:
    def cost(maximum_runtime_hours: Decimal, maximum_attempts: int) -> Decimal:
        return CostInputs(
            hourly_rate_usd=profile.hourly_rate_usd,
            nodes=profile.nodes,
            maximum_runtime_hours=maximum_runtime_hours,
            maximum_attempts=maximum_attempts,
        ).maximum_compute_cost_usd

    single_hour = cost(Decimal(1), 1)
    assert single_hour == cost(Decimal(1), 1)
    assert single_hour == (profile.hourly_rate_usd * profile.nodes).quantize(CENTS)
    assert single_hour.as_tuple().exponent == -2

    rounded = cost(Decimal("0.25"), 3)
    assert rounded == cost(Decimal("0.25"), 3)
    assert rounded.as_tuple().exponent == -2


def test_shipped_catalog_prices_every_reviewed_instance_type() -> None:
    observed = {
        profile.name: (profile.instance_type, profile.hourly_rate_usd)
        for profile in SHIPPED_PROFILES
    }
    assert observed == EXPECTED_PROFILE_RATES


@pytest.mark.parametrize(("name", "rate"), sorted(PRESERVED_PROFILE_RATES.items()))
def test_shipped_catalog_preserves_referenced_profile_rates(name: str, rate: Decimal) -> None:
    profile = next(profile for profile in SHIPPED_PROFILES if profile.name == name)
    assert profile.hourly_rate_usd == rate


def test_shipped_catalog_instance_types_are_unique() -> None:
    instance_types = [profile.instance_type for profile in SHIPPED_PROFILES]
    assert len(set(instance_types)) == len(instance_types)


def test_only_the_deliberately_promoted_profiles_are_provisioned() -> None:
    """Fourteen are promoted, on purpose, and the list is the assertion.

    This test said no profile was provisioned, then one, then two, then eleven, then sixteen,
    and now names fourteen. Each change was a deliberate edit made after the infrastructure
    existed, which is the whole point of writing the names out: it is the tripwire for
    flipping a flag before deploying anything to back it. The catalog would then claim
    capacity that does not exist, and every submission naming that profile would reach Batch
    and sit in RUNNABLE forever rather than being refused at admission.

    THE COUNT WENT DOWN FOR THE FIRST TIME ON 2026-08-04, AND THE FAILURE ABOVE IS EXACTLY
    THE ONE THAT HAPPENED. gpu-1xh100 and gpu-8xh100 were promoted with a compute
    environment, a queue and a job definition all present and all healthy, which is
    everything this tripwire checks for. What no flag records is whether EC2 will sell the
    account the instance: 6,815 launch attempts for p5.48xlarge and 2,530 for p5.4xlarge, all
    InsufficientInstanceCapacity, and zero instance-hours of either type in the billing
    record since the account existed. So a promotion can satisfy every seam in this
    repository and still produce the RUNNABLE-forever outcome the docstring above names.

    Whether a promoted profile is actually backed is a separate question, asserted against
    config/execution-targets.yaml in tests/test_phase3_execution.py. This one is only about
    the flag, which is why it can be read from a single file.
    """
    assert [profile.name for profile in SHIPPED_PROFILES if profile.provisioned] == [
        "cpu-32vcpu",
        "gpu-1xt4",
        "gpu-4xt4",
        "gpu-8xt4",
        "gpu-1xa10g",
        "gpu-4xa10g",
        "gpu-8xa10g",
        "gpu-1xl4",
        "gpu-4xl4",
        "gpu-8xl4",
        "gpu-1xl40s",
        "gpu-4xl40s",
        "gpu-8xl40s",
        "gpu-8xa100",
    ]


def test_a_short_run_on_the_dearest_shape_is_released_by_nobody() -> None:
    """What the rate ceiling used to catch, asked at the size that used to slip past it.

    One hour and one attempt on the most expensive instance this account is priced for is
    $112.32. Under v4 the rate ceiling made that an admin's call while every request bound
    was satisfied. Under v5 the only bound is the total, $112.32 is a nineteenth of it, and
    nobody releases this.

    Asked of the dearest shape by price rather than by name, so a profile promoted above
    p5.48xlarge is covered without an edit here, and so that the assertion is about the
    reasoning rather than about one row of the catalog.

    THE DEAREST SHAPE MOVED ON 2026-08-07 AND THIS IS THE ASSERTION THAT NOTICED. It was
    gpu-8xh100 at $55.04 from the day the rate ceiling came out until gpu-8xb300 was priced at
    $112.32, which is a p6-b300.48xlarge capacity block rather than an on-demand hour -- see
    ``config/workload-catalog.yaml`` on why those four rows are a different product to the
    seventeen above them. Doubling the dearest rate in the catalog did not move this
    classification, which is the point the docstring above was making: under v5 an hour of the
    most expensive machine on the platform is still nobody's decision to release.

    Mutation: put any per-hour rule back into ``classify_request``. This returns exception
    or routine and the test says which shape it was about.
    """
    profile = max(SHIPPED_PROFILES, key=lambda candidate: candidate.hourly_rate_usd)
    facts = facts_for_profile(profile, maximum_runtime_hours=Decimal(1), maximum_attempts=1)

    assert profile.name == "gpu-8xb300"
    assert facts.estimated_cost_usd == Decimal("112.32")
    assert facts.estimated_cost_usd < shipped_policy().thresholds.automatic_below_cost_usd
    assert classify_request(facts, shipped_policy().thresholds) == ApprovalClass.AUTOMATIC


def test_a_long_run_on_a_cheap_eight_card_shape_is_the_one_a_lead_sees() -> None:
    """The ranking v5 produces, and the one the rate ceiling produced backwards.

    Twelve hours at three attempts on ``gpu-8xa10g`` is $586.37, over the bound, so a team
    lead reads it. The same lead was already releasing that shape under v4 at any runtime,
    while the hour of eight A100s above went to an admin at $21.96. The pair is the whole of
    the argument for removing the rate: what a machine costs an hour says nothing about how
    much of the account a request commits.

    Mutation: change ``<`` to ``<=`` in the cost test. This row does not move, which is why
    the strictly-under boundary is pinned in tests/test_policy.py rather than here; what
    this pins is that a real catalog price crossing the real bound reaches a person.
    """
    profile = next(profile for profile in SHIPPED_PROFILES if profile.name == "gpu-8xa10g")
    facts = facts_for_profile(profile, maximum_runtime_hours=Decimal(12), maximum_attempts=3)

    assert facts.estimated_cost_usd == Decimal("586.37")
    assert facts.estimated_cost_usd > shipped_policy().thresholds.automatic_below_cost_usd
    assert classify_request(facts, shipped_policy().thresholds) == ApprovalClass.ROUTINE


@pytest.mark.parametrize("name", ["gpu-8xa100", "gpu-8xh100"])
def test_an_eight_gpu_p_shape_is_no_longer_gated_by_what_it_costs_an_hour(name: str) -> None:
    """The two shapes the rate ceiling existed for, and what v5 does with them instead.

    One hour, one attempt: $21.96 on the A100 node and $55.04 on the H100 node. Both were
    exceptions under v4 for their price per hour and neither is under v5, which is the
    consequence the owner accepted when the ceiling was removed rather than raised.

    THE ``provisioned`` ASSERTION THAT STOOD HERE WAS DROPPED WHEN gpu-8xh100 WAS DEMOTED
    and it stays dropped. What this pins is how a shape classifies when it is offerable,
    which must not drift while it is not.

    Mutation: reintroduce ``EXCEPTION_RATE_CEILING_USD_PER_HOUR`` at any value under $21.96.
    Both rows become exceptions, and the platform goes back to sending the cheaper of two
    requests to the higher approver.
    """
    profile = next(profile for profile in SHIPPED_PROFILES if profile.name == name)
    facts = facts_for_profile(profile, maximum_runtime_hours=Decimal(1), maximum_attempts=1)

    assert facts.estimated_cost_usd < shipped_policy().thresholds.automatic_below_cost_usd
    assert classify_request(facts, shipped_policy().thresholds) == ApprovalClass.AUTOMATIC


def test_a_working_day_on_one_h100_and_an_hour_on_eight_now_route_the_same_way() -> None:
    """The choice a researcher who wants an H100 is making, priced and then classified.

    Eight hours of ``gpu-1xh100`` is $55.04, which is exactly one hour of ``gpu-8xh100``.
    That equality was worth stating while the two routed differently, because the rate
    ceiling sent the eight-card hour to an admin and left the one-card day with a lead. They
    are the same money and they now take the same route, which is what a total-only rule
    means.

    Mutation: reintroduce a per-hour rule. The two classifications stop agreeing and this
    fails on the second one, naming the shape.
    """
    one_card = next(profile for profile in SHIPPED_PROFILES if profile.name == "gpu-1xh100")
    eight_cards = next(profile for profile in SHIPPED_PROFILES if profile.name == "gpu-8xh100")
    a_working_day = facts_for_profile(
        one_card, maximum_runtime_hours=Decimal(8), maximum_attempts=1
    )
    an_hour = facts_for_profile(
        eight_cards, maximum_runtime_hours=Decimal(1), maximum_attempts=1
    )
    thresholds = shipped_policy().thresholds

    assert a_working_day.estimated_cost_usd == an_hour.estimated_cost_usd == Decimal("55.04")
    assert classify_request(a_working_day, thresholds) == classify_request(an_hour, thresholds)
    assert classify_request(a_working_day, thresholds) == ApprovalClass.AUTOMATIC


@pytest.mark.parametrize(
    "profile",
    [profile for profile in SHIPPED_PROFILES if not profile.provisioned],
    ids=[profile.name for profile in SHIPPED_PROFILES if not profile.provisioned],
)
def test_an_unprovisioned_profile_is_refused_at_execution(
    profile: ComputeProfile,
) -> None:
    """Two of the thirteen, and the list is derived rather than written down.

    Deriving it means promoting a profile moves it out of this parametrisation
    automatically, which is what stopped the promotion of nine from failing here for the
    wrong reason. What catches an unbacked promotion is the list above and the target check
    in tests/test_phase3_execution.py, not this.
    """
    with pytest.raises(UnprovisionedComputeProfileError) as exc_info:
        resolve_compute_profile_for_execution(shipped_catalog(), profile.name)
    assert exc_info.value.reason_code == "unprovisioned_compute_profile"


def test_the_provisioned_profile_resolves_for_execution() -> None:
    """The other half of the pair above, and the half that was impossible until Phase 3."""
    profile = resolve_compute_profile_for_execution(shipped_catalog(), "cpu-32vcpu")
    assert profile.provisioned
    assert profile.instance_type == "c7i.8xlarge"


def test_unpriced_profile_is_refused_as_unregistered() -> None:
    with pytest.raises(UnregisteredComputeProfileError):
        resolve_compute_profile_for_execution(shipped_catalog(), "gpu-1024xh200")


#: The profile the pair of coverage tests below moves, and now the only unprovisioned one
#: left. It has to be a profile the shipped catalog does not require evidence for, because
#: what these tests separate is "required and covered" from "required and not", and the
#: records are derived from the shipped catalog rather than written out. It moved here from
#: gpu-1xl40s when that shape was promoted, and there is nothing to move it to next, so
#: promoting or removing this one means building a profile in the test instead of naming a
#: shipped one.
UNPROVISIONED_LEVER = "gpu-1xa10g-sagemaker"

#: The two pools' applied values, read on 2026-08-01 and again on 2026-08-02, matching
#: fixtures/evidence/service-quotas.sanitized.json.
APPLIED_VCPUS = {"L-1216C47A": 1152.0, "L-DB2E81BA": 768.0, "L-417A185B": 768.0}


def catalog_with_provisioned_lever() -> WorkloadCatalog:
    catalog = shipped_catalog()
    return catalog.model_copy(
        update={
            "compute_profiles": tuple(
                profile.model_copy(update={"provisioned": True})
                if profile.name == UNPROVISIONED_LEVER
                else profile
                for profile in catalog.compute_profiles
            )
        }
    )


def representative_quota_records() -> tuple[QuotaRecord, ...]:
    """One record per profile the shipped catalog requires evidence for, derived not written.

    This was three records written out by hand while three profiles needed them. Fifteen do
    now, and copying the shipped list into a literal would make the pair of tests below agree
    with each other by construction rather than say anything.

    Derived from the shipped catalog, which means the coverage test passes because the two
    functions read the same catalog, and the tripwire is the second test: it provisions a
    profile these records were not derived from and requires that the absence is reported.
    A record per profile and not per quota code, because required_vcpus is the profile's and
    several profiles share a code.
    """
    return tuple(
        QuotaRecord.model_validate(
            {
                "service_code": "ec2",
                "quota_code": INSTANCE_EVIDENCE[profile.instance_type]["quota_code"],
                "quota_name": "Running On-Demand instances",
                "applied_value": APPLIED_VCPUS[
                    INSTANCE_EVIDENCE[profile.instance_type]["quota_code"]
                ],
                "unit": "vCPU",
                "quota_applied_at_level": "ACCOUNT",
                "compute_profile": profile.name,
                "required_vcpus": INSTANCE_EVIDENCE[profile.instance_type]["required_vcpus"],
            }
        )
        for profile in profiles_requiring_capacity_evidence(shipped_catalog())
    )


def test_priced_but_unreferenced_profiles_do_not_demand_capacity_evidence() -> None:
    required = {
        profile.name for profile in profiles_requiring_capacity_evidence(shipped_catalog())
    }
    assert UNPROVISIONED_LEVER not in required
    assert required == {
        profile.name for profile in SHIPPED_PROFILES if profile.provisioned
    }
    reason_code, detail = ec2_quota_coverage_issues(
        catalog=shipped_catalog(),
        quotas=representative_quota_records(),
    )
    assert reason_code is None
    assert detail is None


def test_provisioning_a_profile_demands_capacity_evidence_for_it() -> None:
    catalog = catalog_with_provisioned_lever()
    required = {profile.name for profile in profiles_requiring_capacity_evidence(catalog)}
    assert UNPROVISIONED_LEVER in required
    reason_code, detail = ec2_quota_coverage_issues(
        catalog=catalog,
        quotas=representative_quota_records(),
    )
    assert reason_code == "capacity_blocked"
    assert detail is not None
    assert UNPROVISIONED_LEVER in detail


def test_representative_manifest_costs_are_unchanged() -> None:
    catalog = shipped_catalog()
    manifests = load_manifests_from_directory(PROJECT_ROOT / "fixtures" / "manifests")
    observed = {
        filename: compute_manifest_maximum_cost(manifest, catalog)
        for filename, manifest in manifests
    }
    assert observed == dict(REPRESENTATIVE_MANIFEST_COSTS)
    assert {
        filename: cost
        for filename, cost in observed.items()
        if filename in PRESERVED_MANIFEST_COSTS
    } == PRESERVED_MANIFEST_COSTS


def test_pricing_an_unregistered_profile_raises_the_unregistered_error() -> None:
    catalog = shipped_catalog()
    _filename, manifest = load_manifests_from_directory(
        PROJECT_ROOT / "fixtures" / "manifests"
    )[0]
    unknown = manifest.model_copy(update={"compute_profile": "gpu-1024xh200"})
    with pytest.raises(UnregisteredComputeProfileError) as exc_info:
        compute_manifest_maximum_cost(unknown, catalog)
    assert not isinstance(exc_info.value, UnprovisionedComputeProfileError)
