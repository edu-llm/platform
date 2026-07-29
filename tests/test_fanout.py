import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from edullm_platform.canonical import canonical_json_bytes, sha256_digest
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.policy import (
    ApprovalClass,
    PolicyThresholds,
    RequestFacts,
    classify_request,
)
from edullm_platform.manifest_helpers import (
    REPRESENTATIVE_MANIFEST_COSTS,
    compute_manifest_maximum_cost,
)
from edullm_platform.phase0_gate import (
    expected_manifest_classification,
    request_facts_from_manifest,
)
from tests.test_manifest import (
    MULTISEED_MANIFEST,
    REPRESENTATIVE_MANIFEST_FILENAMES,
    load_representative_manifest,
    load_workload_catalog,
)
from tests.test_policy import (
    load_approval_policy,
    load_dataset_registry,
    load_repository_registry,
    numeric_bound_violations,
)

CPU_HOURLY_RATE_USD = Decimal("1.428")
CPU_NODES = 1


def shipped_thresholds() -> PolicyThresholds:
    return load_approval_policy().thresholds


def fanout_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "size": 20,
        "max_parallel": 8,
        "index_parameter": "shard_index",
    }
    payload.update(overrides)
    return payload


def sweep_manifest_payload(**overrides: object) -> dict[str, object]:
    # A registered repository, and it has to be one. Every classification assertion below
    # expects routine, and an unregistered repository is denied outright before a threshold
    # is consulted -- so a sweep from dolma would classify as an exception for a reason
    # that has nothing to do with fan-out. It named dolma until ``repository_registered``
    # started reading config/repositories.yaml rather than the roster's pilot list.
    payload: dict[str, object] = {
        "schema_version": 1,
        "repository": "OLMo-core",
        "commit_sha": "a" * 40,
        "image_digest": "sha256:" + "b" * 64,
        "dataset_release": "dolma-2026-07",
        "command": ["python", "-m", "olmo_core.data.tokenize", "--shard-index"],
        "team": "data-prep",
        "wandb_project": "olmo-core-sweep",
        "workload_profile": "olmo-core-cpu-smoke",
        "compute_profile": "cpu-32vcpu",
        "maximum_runtime_hours": "2.5",
        "maximum_attempts": 1,
        "checkpoint": None,
        "fanout": fanout_payload(),
    }
    payload.update(overrides)
    return payload


def sweep_manifest(**overrides: object) -> RunManifest:
    return RunManifest.model_validate(sweep_manifest_payload(**overrides))


def manifest_cost(manifest: RunManifest) -> Decimal:
    return compute_manifest_maximum_cost(manifest, load_workload_catalog())


def facts_for(manifest: RunManifest) -> RequestFacts:
    catalog = load_workload_catalog()
    return request_facts_from_manifest(
        manifest,
        repositories=load_repository_registry(),
        catalog=catalog,
        dataset_registry=load_dataset_registry(),
        estimated_cost_usd=compute_manifest_maximum_cost(manifest, catalog),
    )


def assert_validation_error(
    error: ValidationError,
    *,
    error_type: str,
    loc: tuple[str | int, ...],
    message_fragment: str | None = None,
) -> None:
    matching_errors = [
        item for item in error.errors() if item["type"] == error_type and item["loc"] == loc
    ]
    assert matching_errors, (
        f"expected error type {error_type!r} at loc {loc!r}, got {error.errors()}"
    )
    if message_fragment is not None:
        assert any(message_fragment in item["msg"] for item in matching_errors), (
            f"expected {message_fragment!r} in {error_type!r} messages at {loc!r}, "
            f"got {[item['msg'] for item in matching_errors]}"
        )


def test_manifest_accepts_a_fanout_block() -> None:
    manifest = sweep_manifest()
    assert manifest.fanout is not None
    assert manifest.fanout.size == 20
    assert manifest.fanout.max_parallel == 8
    assert manifest.fanout.index_parameter == "shard_index"


def test_manifest_fanout_is_optional() -> None:
    payload = sweep_manifest_payload()
    del payload["fanout"]
    assert RunManifest.model_validate(payload).fanout is None


@pytest.mark.parametrize("size", [0, 1])
def test_fanout_rejects_a_size_below_two(size: int) -> None:
    with pytest.raises(ValidationError) as exc_info:
        sweep_manifest(fanout=fanout_payload(size=size, max_parallel=1))
    assert_validation_error(
        exc_info.value,
        error_type="greater_than_equal",
        loc=("fanout", "size"),
    )


def test_fanout_rejects_parallelism_above_size() -> None:
    with pytest.raises(ValidationError) as exc_info:
        sweep_manifest(fanout=fanout_payload(size=4, max_parallel=5))
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=("fanout",),
        message_fragment="fan-out parallelism must not exceed fan-out size",
    )


def test_fanout_rejects_zero_parallelism() -> None:
    with pytest.raises(ValidationError) as exc_info:
        sweep_manifest(fanout=fanout_payload(max_parallel=0))
    assert_validation_error(
        exc_info.value,
        error_type="greater_than_equal",
        loc=("fanout", "max_parallel"),
    )


def test_fanout_rejects_an_empty_index_parameter() -> None:
    with pytest.raises(ValidationError) as exc_info:
        sweep_manifest(fanout=fanout_payload(index_parameter=""))
    assert_validation_error(
        exc_info.value,
        error_type="string_too_short",
        loc=("fanout", "index_parameter"),
    )


def test_fanout_allows_parallelism_equal_to_size() -> None:
    manifest = sweep_manifest(fanout=fanout_payload(size=4, max_parallel=4))
    assert manifest.fanout is not None
    assert manifest.fanout.max_parallel == manifest.fanout.size


@pytest.mark.parametrize(
    "field",
    ["compute_profile", "image_digest", "dataset_release"],
)
def test_a_fanout_manifest_cannot_declare_two_of_a_shared_resource(field: str) -> None:
    values = {
        "compute_profile": ["cpu-32vcpu", "gpu-4xa10g"],
        "image_digest": ["sha256:" + "b" * 64, "sha256:" + "c" * 64],
        "dataset_release": ["dolma-2026-07", "dolma-2026-08"],
    }
    with pytest.raises(ValidationError) as exc_info:
        sweep_manifest(**{field: values[field]})
    assert_validation_error(exc_info.value, error_type="string_type", loc=(field,))


@pytest.mark.parametrize(
    "override_key",
    ["compute_profile", "image_digest", "dataset_release", "overrides"],
)
def test_the_fanout_block_cannot_carry_per_cell_resource_overrides(override_key: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        sweep_manifest(fanout=fanout_payload(**{override_key: "gpu-4xa10g"}))
    assert_validation_error(
        exc_info.value,
        error_type="extra_forbidden",
        loc=("fanout", override_key),
    )


def test_a_twenty_cell_fanout_costs_twenty_times_one_cell() -> None:
    one_cell = sweep_manifest(fanout=None)
    sweep = sweep_manifest(fanout=fanout_payload(size=20))
    assert manifest_cost(one_cell) == Decimal("3.57")
    assert manifest_cost(sweep) == manifest_cost(one_cell) * 20
    assert manifest_cost(sweep) == Decimal("71.40")


def test_attempts_multiply_within_a_cell_and_size_multiplies_across_cells() -> None:
    hours = Decimal("2.5")
    single_attempt = sweep_manifest(fanout=fanout_payload(size=20))
    two_attempts = sweep_manifest(
        fanout=fanout_payload(size=20),
        maximum_attempts=2,
        checkpoint={
            "interval_minutes": 30,
            "destination_prefix": "s3://sbsandbox-intern-edullm-checkpoints/runs/",
            "resume_required": True,
        },
    )
    assert manifest_cost(two_attempts) == manifest_cost(single_attempt) * 2
    assert manifest_cost(two_attempts) == CPU_HOURLY_RATE_USD * CPU_NODES * hours * 2 * 20


def test_the_submission_total_is_rounded_once_rather_than_cell_by_cell() -> None:
    one_cell = sweep_manifest(fanout=None, maximum_runtime_hours="1")
    sweep = sweep_manifest(fanout=fanout_payload(size=20), maximum_runtime_hours="1")

    assert manifest_cost(one_cell) == Decimal("1.43")
    assert manifest_cost(sweep) == Decimal("28.56")
    assert manifest_cost(one_cell) * 20 == Decimal("28.60"), (
        "rounding every cell to cents first makes the rounding error scale with the size "
        "of the sweep; the submission total is quantized once instead"
    )


def test_a_manifest_without_a_fanout_prices_exactly_as_before() -> None:
    catalog = load_workload_catalog()
    checked = 0
    for filename, expected_cost_usd in REPRESENTATIVE_MANIFEST_COSTS.items():
        manifest = load_representative_manifest(filename)
        if manifest.fanout is not None:
            continue
        assert compute_manifest_maximum_cost(manifest, catalog) == expected_cost_usd
        checked += 1
    assert checked > 0, "no fan-out-free fixture remained, so this guard passed vacuously"


def multiseed_fixture() -> RunManifest:
    return load_representative_manifest(MULTISEED_MANIFEST)


def test_the_multiseed_fixture_fans_out_over_seeds() -> None:
    manifest = multiseed_fixture()
    assert manifest.fanout is not None
    assert manifest.fanout.index_parameter == "seed"
    assert manifest.fanout.size == 5
    assert manifest.fanout.max_parallel == 5


def test_the_multiseed_fixture_is_priced_across_the_whole_submission() -> None:
    manifest = multiseed_fixture()
    assert manifest.fanout is not None
    profile = next(
        candidate
        for candidate in load_workload_catalog().compute_profiles
        if candidate.name == manifest.compute_profile
    )
    assert manifest_cost(manifest) == (
        profile.hourly_rate_usd
        * profile.nodes
        * manifest.maximum_runtime_hours
        * manifest.maximum_attempts
        * manifest.fanout.size
    )
    assert manifest_cost(manifest) == Decimal("20.12")


def test_the_multiseed_fixture_is_not_priced_one_seed_at_a_time() -> None:
    manifest = multiseed_fixture()
    assert manifest.fanout is not None
    one_seed = manifest.model_copy(update={"fanout": None})
    assert manifest_cost(one_seed) == Decimal("4.02")
    assert manifest_cost(one_seed) * manifest.fanout.size == Decimal("20.10")
    assert manifest_cost(manifest) != manifest_cost(one_seed) * manifest.fanout.size


def test_the_multiseed_fixture_stays_within_the_routine_ceilings() -> None:
    manifest = multiseed_fixture()
    thresholds = shipped_thresholds()
    facts = facts_for(manifest)
    assert facts.fanout_size <= thresholds.routine_maximum_fanout_size
    assert facts.fanout_parallelism <= thresholds.routine_maximum_parallelism
    assert numeric_bound_violations(facts, thresholds) == frozenset()
    assert classify_request(facts, thresholds) is ApprovalClass.ROUTINE


@pytest.mark.parametrize("filename", REPRESENTATIVE_MANIFEST_FILENAMES)
def test_shipped_fixture_manifests_keep_their_classification(filename: str) -> None:
    manifest = load_representative_manifest(filename)
    expected = expected_manifest_classification(filename)
    assert classify_request(facts_for(manifest), shipped_thresholds()) is expected


def test_a_request_without_a_fanout_classifies_exactly_as_before() -> None:
    manifest = sweep_manifest(fanout=None)
    facts = facts_for(manifest)
    assert facts.fanout_size == 1
    assert facts.fanout_parallelism == 1
    assert numeric_bound_violations(facts, shipped_thresholds()) == frozenset()
    assert classify_request(facts, shipped_thresholds()) is ApprovalClass.ROUTINE


def test_request_facts_carry_the_fanout_shape_declared_by_the_manifest() -> None:
    facts = facts_for(sweep_manifest(fanout=fanout_payload(size=12, max_parallel=3)))
    assert facts.fanout_size == 12
    assert facts.fanout_parallelism == 3


def test_a_sweep_is_priced_as_one_submission_so_it_cannot_hide_behind_cheap_cells() -> None:
    """Guards the decomposition bypass that the fan-out contract exists to close.

    Forty ten-hour CPU runs cost $14.28 each and every one of them is routine. The same
    forty runs submitted as one sweep cost $571.20, which is over the routine ceiling, so
    the sweep must be an exception. If policy ever priced the cell instead of the
    submission, an arbitrarily expensive experiment could be waved through as routine
    simply by declaring it as a fan-out.
    """
    thresholds = shipped_thresholds()
    cells = 40
    cell_cost = Decimal("14.28")

    hand_split_manifests = tuple(
        sweep_manifest(
            fanout=None,
            maximum_runtime_hours="10",
            command=["python", "-m", "olmo_core.data.tokenize", "--shard", str(index)],
        )
        for index in range(cells)
    )
    for index, cell in enumerate(hand_split_manifests):
        assert manifest_cost(cell) == cell_cost
        assert classify_request(facts_for(cell), thresholds) is ApprovalClass.ROUTINE, (
            f"hand-split run {index} is individually routine at {cell_cost} USD, which is "
            "exactly why the sweep must not be priced one cell at a time"
        )

    sweep = sweep_manifest(fanout=fanout_payload(size=cells), maximum_runtime_hours="10")
    sweep_facts = facts_for(sweep)
    sweep_cost = manifest_cost(sweep)

    assert sweep_cost == sum(manifest_cost(cell) for cell in hand_split_manifests), (
        "the sweep and the hand-split runs are the same work and the same money; only the "
        "review changes"
    )
    assert sweep_cost == cell_cost * cells == Decimal("571.20")
    assert sweep_cost > thresholds.routine_maximum_cost_usd
    assert numeric_bound_violations(sweep_facts, thresholds) == frozenset({"cost"})
    assert classify_request(sweep_facts, thresholds) is ApprovalClass.EXCEPTION, (
        "forty individually routine runs bundled into one submission cost more than the "
        "routine ceiling and have to be reviewed once, as one thing"
    )


def test_a_hundred_trivial_cells_is_an_exception_on_count_alone() -> None:
    thresholds = shipped_thresholds()
    sweep = sweep_manifest(
        fanout=fanout_payload(size=100),
        maximum_runtime_hours="0.05",
    )
    facts = facts_for(sweep)

    assert facts.estimated_cost_usd == Decimal("7.14")
    assert facts.estimated_cost_usd < thresholds.routine_maximum_cost_usd
    assert numeric_bound_violations(facts, thresholds) == frozenset({"fanout_size"})
    assert classify_request(facts, thresholds) is ApprovalClass.EXCEPTION, (
        "a hundred one-minute jobs are cheap and still an operational event; the count "
        "ceiling bounds what the cost ceiling cannot"
    )


def test_parallelism_above_the_bound_is_an_exception_on_its_own() -> None:
    thresholds = shipped_thresholds()
    sweep = sweep_manifest(
        fanout=fanout_payload(size=32, max_parallel=16),
        maximum_runtime_hours="0.05",
    )
    facts = facts_for(sweep)

    assert facts.fanout_size <= thresholds.routine_maximum_fanout_size
    assert facts.estimated_cost_usd < thresholds.routine_maximum_cost_usd
    assert numeric_bound_violations(facts, thresholds) == frozenset({"parallelism"})
    assert classify_request(facts, thresholds) is ApprovalClass.EXCEPTION


def test_a_fanout_at_both_count_ceilings_stays_routine() -> None:
    thresholds = shipped_thresholds()
    sweep = sweep_manifest(
        fanout=fanout_payload(
            size=thresholds.routine_maximum_fanout_size,
            max_parallel=thresholds.routine_maximum_parallelism,
        ),
        maximum_runtime_hours="0.05",
    )
    facts = facts_for(sweep)

    assert facts.fanout_size == 64
    assert facts.fanout_parallelism == 8
    assert numeric_bound_violations(facts, thresholds) == frozenset()
    assert classify_request(facts, thresholds) is ApprovalClass.ROUTINE


def test_fanout_manifest_round_trips_through_canonical_json() -> None:
    manifest = sweep_manifest()
    encoded = canonical_json_bytes(manifest)
    restored = RunManifest.model_validate(json.loads(encoded))

    assert restored == manifest
    assert sha256_digest(restored) == sha256_digest(manifest)
    assert b'"fanout":{"index_parameter":"shard_index","max_parallel":8,"size":20}' in encoded


def test_fanout_manifest_digest_is_stable_across_field_ordering() -> None:
    payload = sweep_manifest_payload()
    reversed_payload = dict(reversed(list(payload.items())))
    reversed_payload["fanout"] = dict(reversed(list(fanout_payload().items())))

    assert sha256_digest(RunManifest.model_validate(payload)) == sha256_digest(
        RunManifest.model_validate(reversed_payload)
    )


def test_fanout_participates_in_the_manifest_digest() -> None:
    without_fanout = sweep_manifest(fanout=None)
    with_fanout = sweep_manifest()
    wider_fanout = sweep_manifest(fanout=fanout_payload(size=21))

    assert sha256_digest(without_fanout) != sha256_digest(with_fanout)
    assert sha256_digest(with_fanout) != sha256_digest(wider_fanout)
    assert b'"fanout":null' in canonical_json_bytes(without_fanout)
