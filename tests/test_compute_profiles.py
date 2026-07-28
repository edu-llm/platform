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
    "gpu-exception.yaml": Decimal("73.74"),
}

EXPECTED_PROFILE_RATES = {
    "cpu-32vcpu": ("c7i.8xlarge", Decimal("1.428")),
    "gpu-1xt4": ("g4dn.xlarge", Decimal("0.5260")),
    "gpu-4xt4": ("g4dn.12xlarge", Decimal("3.9120")),
    "gpu-1xa10g": ("g5.xlarge", Decimal("1.0060")),
    "gpu-1xa10g-sagemaker": ("g5.2xlarge", Decimal("1.5150")),
    "gpu-4xa10g": ("g5.12xlarge", Decimal("5.672")),
    "gpu-1xl4": ("g6.xlarge", Decimal("0.8048")),
    "gpu-4xl4": ("g6.12xlarge", Decimal("4.6016")),
    "gpu-1xl40s": ("g6e.xlarge", Decimal("1.8610")),
    "gpu-4xl40s": ("g6e.12xlarge", Decimal("10.4926")),
    "gpu-8xa100": ("p4d.24xlarge", Decimal("21.9576")),
    "gpu-8xh100": ("p5.48xlarge", Decimal("55.0400")),
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
    """Two are promoted, on purpose, and the list is the assertion.

    This test said no profile was provisioned, then one, and now names two. Each change was
    a deliberate edit made after the infrastructure existed, which is the whole point of
    writing the names out: it is the tripwire for flipping a flag before deploying anything
    to back it. The catalog would then claim capacity that does not exist, and every
    submission naming that profile would reach Batch and sit in RUNNABLE forever rather than
    being refused at admission.

    Whether a promoted profile is actually backed is a separate question, asserted against
    config/execution-targets.yaml in tests/test_phase3_execution.py. This one is only about
    the flag, which is why it can be read from a single file.
    """
    assert [profile.name for profile in SHIPPED_PROFILES if profile.provisioned] == [
        "cpu-32vcpu",
        "gpu-1xa10g",
    ]


def test_unprovisioned_profile_classifies_as_routine_within_policy_thresholds() -> None:
    profile = next(profile for profile in SHIPPED_PROFILES if profile.name == "gpu-8xh100")
    assert profile.provisioned is False
    facts = facts_for_profile(profile, maximum_runtime_hours=Decimal(4), maximum_attempts=1)
    assert facts.estimated_cost_usd == Decimal("220.16")
    assert classify_request(facts, shipped_policy().thresholds) == ApprovalClass.ROUTINE


def test_unprovisioned_profile_classifies_as_exception_above_policy_thresholds() -> None:
    profile = next(profile for profile in SHIPPED_PROFILES if profile.name == "gpu-8xh100")
    assert profile.provisioned is False
    facts = facts_for_profile(profile, maximum_runtime_hours=Decimal(12), maximum_attempts=1)
    assert facts.estimated_cost_usd == Decimal("660.48")
    assert classify_request(facts, shipped_policy().thresholds) == ApprovalClass.EXCEPTION


@pytest.mark.parametrize(
    "profile",
    [profile for profile in SHIPPED_PROFILES if not profile.provisioned],
    ids=[profile.name for profile in SHIPPED_PROFILES if not profile.provisioned],
)
def test_an_unprovisioned_profile_is_refused_at_execution(
    profile: ComputeProfile,
) -> None:
    """Eleven of the twelve, and the list is derived rather than written down.

    Deriving it means promoting a profile moves it out of this parametrisation
    automatically, which is what stops the promotion from failing here for the wrong
    reason. What catches an unbacked promotion is the count above and the target check in
    tests/test_phase3_execution.py, not this.
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


def catalog_with_provisioned_h100() -> WorkloadCatalog:
    catalog = shipped_catalog()
    return catalog.model_copy(
        update={
            "compute_profiles": tuple(
                profile.model_copy(update={"provisioned": True})
                if profile.name == "gpu-8xh100"
                else profile
                for profile in catalog.compute_profiles
            )
        }
    )


def representative_quota_records() -> tuple[QuotaRecord, ...]:
    return (
        QuotaRecord.model_validate(
            {
                "service_code": "ec2",
                "quota_code": "L-1216C47A",
                "quota_name": "Running On-Demand Standard instances",
                "applied_value": 1152.0,
                "unit": "vCPU",
                "quota_applied_at_level": "ACCOUNT",
                "workload_profile": "cpu-32vcpu",
                "required_vcpus": 32,
            }
        ),
        QuotaRecord.model_validate(
            {
                "service_code": "ec2",
                "quota_code": "L-DB2E81BA",
                "quota_name": "Running On-Demand G and VT instances",
                "applied_value": 768.0,
                "unit": "vCPU",
                "quota_applied_at_level": "ACCOUNT",
                "workload_profile": "gpu-4xa10g",
                "required_vcpus": 48,
            }
        ),
        # The same quota code as the entry above, and a second record rather than a wider
        # one, because these records are per profile and not per quota. Both G-family
        # profiles draw on L-DB2E81BA; what differs is how much each needs, and it is
        # required_vcpus that the sufficiency check reads.
        QuotaRecord.model_validate(
            {
                "service_code": "ec2",
                "quota_code": "L-DB2E81BA",
                "quota_name": "Running On-Demand G and VT instances",
                "applied_value": 768.0,
                "unit": "vCPU",
                "quota_applied_at_level": "ACCOUNT",
                "workload_profile": "gpu-1xa10g",
                "required_vcpus": 4,
            }
        ),
    )


def test_priced_but_unreferenced_profiles_do_not_demand_capacity_evidence() -> None:
    required = {
        profile.name for profile in profiles_requiring_capacity_evidence(shipped_catalog())
    }
    assert required == {"cpu-32vcpu", "gpu-1xa10g", "gpu-4xa10g"}
    reason_code, detail = ec2_quota_coverage_issues(
        catalog=shipped_catalog(),
        quotas=representative_quota_records(),
    )
    assert reason_code is None
    assert detail is None


def test_provisioning_a_profile_demands_capacity_evidence_for_it() -> None:
    catalog = catalog_with_provisioned_h100()
    required = {profile.name for profile in profiles_requiring_capacity_evidence(catalog)}
    assert required == {"cpu-32vcpu", "gpu-1xa10g", "gpu-4xa10g", "gpu-8xh100"}
    reason_code, detail = ec2_quota_coverage_issues(
        catalog=catalog,
        quotas=representative_quota_records(),
    )
    assert reason_code == "capacity_blocked"
    assert detail is not None
    assert "gpu-8xh100" in detail


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
