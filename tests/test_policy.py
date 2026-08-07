from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from edullm_platform.config import load_yaml
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.image_scan import ImageScanSeverity
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.policy import (
    INPUTS_THAT_MUST_RESOLVE,
    ApprovalClass,
    ApprovalPolicy,
    PolicyThresholds,
    RequestFacts,
    classify_request,
)
from edullm_platform.contracts.repository_registry import RepositoryRegistry
from edullm_platform.contracts.workload import compute_maximum_compute_cost_usd
from edullm_platform.manifest_helpers import compute_manifest_cost_inputs
from edullm_platform.operational_inventory import (
    expected_manifest_classification,
    request_facts_from_manifest,
)
from tests.test_manifest import (
    PROJECT_ROOT,
    REPRESENTATIVE_MANIFEST_FILENAMES,
    compute_manifest_maximum_cost,
    is_workload_profile_registered,
    load_representative_manifest,
    load_workload_catalog,
)


def expected_classification(filename: str) -> ApprovalClass:
    return expected_manifest_classification(filename)


def load_organization_inventory() -> OrganizationInventory:
    return load_yaml(PROJECT_ROOT / "config" / "organization.yaml", OrganizationInventory)


def load_approval_policy() -> ApprovalPolicy:
    return load_yaml(PROJECT_ROOT / "config" / "policy.yaml", ApprovalPolicy)


def load_dataset_registry() -> DatasetRegistry:
    return load_yaml(PROJECT_ROOT / "config" / "datasets.yaml", DatasetRegistry)


def load_repository_registry() -> RepositoryRegistry:
    return load_yaml(PROJECT_ROOT / "config" / "repositories.yaml", RepositoryRegistry)


def rate_of(profile_name: str) -> Decimal:
    """One compute profile's hourly rate, read out of the shipped catalog.

    Read rather than written down, because every figure this module quotes about the v5
    reasoning is a product of a catalog price and a repricing has to move the test with it.
    """
    rates = {
        profile.name: profile.hourly_rate_usd
        for profile in load_workload_catalog().compute_profiles
    }
    return rates[profile_name]


def assert_validation_error(
    error: ValidationError,
    *,
    error_type: str,
    message_fragment: str | None = None,
) -> None:
    matching_errors = [item for item in error.errors() if item["type"] == error_type]
    assert matching_errors, f"expected error type {error_type!r}, got {error.errors()}"
    if message_fragment is not None:
        assert any(message_fragment in item["msg"] for item in matching_errors), (
            f"expected {message_fragment!r} in {error_type!r} messages, "
            f"got {[item['msg'] for item in matching_errors]}"
        )


def thresholds_payload() -> dict[str, object]:
    return {"automatic_below_cost_usd": "500"}


def request_facts_payload(**overrides: object) -> dict[str, object]:
    payload = {
        "claimed_team": "memory-split",
        "repository_registered": True,
        "dataset_registered": True,
        "dataset_is_a_corpus": True,
        "compute_profile_registered": True,
        # ON THE BASELINE THIS IS FALSE, AND EVERY CASE BELOW THAT DOES NOT SAY OTHERWISE IS
        # ABOUT AN ORDINARY ON-DEMAND SHAPE. True short-circuits the whole function to
        # ``exception``, so a baseline carrying it would make the cost bound, the fan-out test
        # and the scan test unreachable and every one of those cases would pass on a mutant.
        "capacity_block_backed": False,
        "immutable_revision": True,
        "immutable_image": True,
        "image_scan_reviewed": True,
        "estimated_cost_usd": "499.99",
        "maximum_runtime_hours": "24",
        "maximum_attempts": 2,
    }
    payload.update(overrides)
    return payload


def thresholds() -> PolicyThresholds:
    return PolicyThresholds(automatic_below_cost_usd=Decimal(500))


def facts(**overrides: object) -> RequestFacts:
    """A submission every input resolves for, under the bound, in one cell.

    Every test below moves exactly one thing about it and asserts what that costs, so a
    failure names what moved rather than the fixture.
    """
    return RequestFacts.model_validate(request_facts_payload(**overrides))


def policy_payload() -> dict[str, object]:
    return {
        "policy_version": "v1",
        "thresholds": {"automatic_below_cost_usd": "500"},
        "image_scan": {"blocking_severities": ["CRITICAL"]},
        "approval_scope": "organization",
        "routine_approver_role": "team_lead",
        "exception_approver_roles": ["platform_admin"],
        "denied_outright": [
            "unregistered_repository",
            "unregistered_dataset",
            "unregistered_compute_profile",
            "mutable_repository_revision",
            "mutable_image_reference",
        ],
    }


# --------------------------------------------------------------------------------------
# The one bound, pinned from both sides
# --------------------------------------------------------------------------------------


def test_a_single_cell_under_the_bound_is_released_by_nobody() -> None:
    """The case the whole automatic class exists for, and the baseline the rest move off."""
    assert classify_request(facts(), thresholds()) is ApprovalClass.AUTOMATIC


@pytest.mark.parametrize(
    ("estimated_cost_usd", "expected"),
    [
        ("499.99", ApprovalClass.AUTOMATIC),
        ("500", ApprovalClass.ROUTINE),
        ("500.01", ApprovalClass.ROUTINE),
    ],
)
def test_the_cost_bound_is_strictly_under(
    estimated_cost_usd: str,
    expected: ApprovalClass,
) -> None:
    """Five hundred dollars exactly goes to a lead.

    Mutation: change ``<`` to ``<=`` in ``classify_request``. The middle row flips to
    automatic and the first and third rows do not move, so a test asserting only the first
    row would pass against the wrong comparison. The direction matters asymmetrically: too
    wide silently enlarges the set of runs no person ever sees, too narrow costs a lead one
    click.
    """
    assert classify_request(facts(estimated_cost_usd=estimated_cost_usd), thresholds()) is expected


def test_the_bound_is_read_from_the_thresholds_and_not_written_into_the_function() -> None:
    """A request that is automatic at one bound is routine at a lower one.

    Mutation: replace ``thresholds.automatic_below_cost_usd`` with a literal in
    ``classify_request``. The second assertion fails, and the failure it catches is a
    deployed validator that goes on classifying against a figure the reviewed file no longer
    carries.
    """
    request = facts(estimated_cost_usd="100")

    assert classify_request(request, thresholds()) is ApprovalClass.AUTOMATIC
    assert (
        classify_request(request, PolicyThresholds(automatic_below_cost_usd=Decimal(50)))
        is ApprovalClass.ROUTINE
    )


# --------------------------------------------------------------------------------------
# The three things that hold a cheap single cell back
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("fanout_size", [2, 5, 64, 1000])
def test_a_fanout_never_auto_approves_however_cheap_it_is(fanout_size: int) -> None:
    """Sixty-four cells is sixty-four machines starting at once, which the total does not say.

    Mutation: delete the ``fanout_size`` test from ``classify_request``. Every row here
    returns automatic, and a thousand-cell sweep starts with nobody having seen it. The cost
    is a hundredth of the bound in each row, so nothing else in the function objects.
    """
    request = facts(estimated_cost_usd="5", fanout_size=fanout_size)

    assert request.estimated_cost_usd < thresholds().automatic_below_cost_usd
    assert classify_request(request, thresholds()) is ApprovalClass.ROUTINE


def test_one_cell_is_the_only_size_that_can_be_automatic() -> None:
    """The boundary of the fan-out test, pinned on the side a mutation would move it to.

    Mutation: change ``facts.fanout_size > 1`` to ``> 2``. The second assertion fails. A
    two-cell sweep is the smallest thing a wrong comparison here lets through and the
    likeliest one to be written by somebody trying a fan-out for the first time.
    """
    assert classify_request(facts(estimated_cost_usd="1"), thresholds()) is ApprovalClass.AUTOMATIC
    assert (
        classify_request(facts(estimated_cost_usd="1", fanout_size=2), thresholds())
        is ApprovalClass.ROUTINE
    )


def test_an_unreviewed_image_scan_reaches_a_lead_and_never_reaches_nobody() -> None:
    """What v5 keeps from the gate it softened, which is the reader rather than the refusal.

    Mutation: delete the ``image_scan_reviewed`` test from ``classify_request``. This
    returns automatic, the run starts with no approver, and the findings
    ``render_approver_context`` prints are printed to nobody. That turns the softening into
    a removal, which is the one thing the v5 ruling said it was not.
    """
    request = facts(estimated_cost_usd="0.50", image_scan_reviewed=False)

    assert request.estimated_cost_usd < thresholds().automatic_below_cost_usd
    assert classify_request(request, thresholds()) is ApprovalClass.ROUTINE


def test_a_reviewed_scan_is_what_makes_the_same_request_automatic() -> None:
    """The other side of the scan test, so the pair says which way the comparison runs.

    Mutation: invert the condition to ``if facts.image_scan_reviewed``. This fails while the
    test above still passes, because a single assertion about an unreviewed digest cannot
    tell a gate that reads the flag from one that ignores it.
    """
    assert (
        classify_request(facts(estimated_cost_usd="0.50", image_scan_reviewed=True), thresholds())
        is ApprovalClass.AUTOMATIC
    )


@pytest.mark.parametrize("fact_name", INPUTS_THAT_MUST_RESOLVE)
def test_an_input_that_does_not_resolve_is_never_automatic(fact_name: str) -> None:
    """Belt and braces over a refusal that has already happened by the time this is read.

    Every one of these is also a ``denied_outright`` condition, so the submission never
    reaches a gate. What this pins is the class on the decision record such a refusal
    writes, and "released by nobody" is the wrong words for a request nobody may release.

    Mutation: delete the ``INPUTS_THAT_MUST_RESOLVE`` test. Every row returns automatic and
    every refused record starts claiming a class that says no person was needed.
    """
    request = facts(estimated_cost_usd="1", **{fact_name: False})

    assert classify_request(request, thresholds()) is ApprovalClass.ROUTINE


def test_every_input_that_must_resolve_is_a_fact_the_request_carries() -> None:
    """A typo in the tuple would silently stop testing one of the five.

    Mutation: misspell any entry of ``INPUTS_THAT_MUST_RESOLVE``. ``getattr`` inside
    ``classify_request`` would raise on every call, so this is really a guard on the tuple
    being edited without the model, and it fails here rather than at admission.
    """
    assert set(INPUTS_THAT_MUST_RESOLVE) <= set(RequestFacts.model_fields)


# --------------------------------------------------------------------------------------
# A capacity block is the one thing that reaches an admin
# --------------------------------------------------------------------------------------


def test_a_capacity_block_is_the_only_thing_that_classifies_as_an_exception() -> None:
    """The whole grid, and the one switch in it that produces the third class.

    THIS ASSERTED THE OPPOSITE UNTIL 2026-08-07 and it was right to, for about a fortnight.
    v5 removed all five ceilings that reached an admin and left nothing routing there, which
    this pinned as ``seen == {AUTOMATIC, ROUTINE}``. Meanwhile ``config/policy.yaml`` went on
    naming ``platform_admin`` in ``exception_approver_roles`` "for the capacity blocks being
    designed separately", four block-backed shapes were priced, and three separate comments
    claimed a withdrawn rate ceiling was still gating them. The gate existed, the shapes
    existed, and nothing joined them.

    So the grid is walked twice over ``capacity_block_backed`` and the two halves say different
    things: with the flag false nothing reaches an admin however expensive, however many cells
    and however unreviewed, and with it true everything does. That is a stronger claim than a
    single case, because it is what makes the branch's position at the top of the function
    testable -- a block-backed sixty-four-cell sweep with an unreviewed scan and an
    unregistered repository is in this grid, and every one of those facts would otherwise send
    it to a lead.

    Mutation: move the ``capacity_block_backed`` branch below any other test. The rows that
    also trip that test come back ``routine`` and the second half of this fails naming the
    combination.
    """
    by_backing: dict[bool, set[ApprovalClass]] = {False: set(), True: set()}
    for block_backed in (False, True):
        for cost in ("0", "499.99", "500", "1000000"):
            for cells in (1, 2, 64):
                for reviewed in (True, False):
                    for resolves in (True, False):
                        overrides: dict[str, object] = {
                            fact: resolves for fact in INPUTS_THAT_MUST_RESOLVE
                        }
                        by_backing[block_backed].add(
                            classify_request(
                                facts(
                                    capacity_block_backed=block_backed,
                                    estimated_cost_usd=cost,
                                    fanout_size=cells,
                                    image_scan_reviewed=reviewed,
                                    **overrides,
                                ),
                                thresholds(),
                            )
                        )

    assert by_backing[False] == {ApprovalClass.AUTOMATIC, ApprovalClass.ROUTINE}
    assert by_backing[True] == {ApprovalClass.EXCEPTION}


# --------------------------------------------------------------------------------------
# The two catalog figures the rate ceiling was removed over
# --------------------------------------------------------------------------------------


def test_the_two_shapes_the_rate_ceiling_ranked_backwards_now_rank_by_what_they_commit() -> None:
    """Why rate went, priced against the shipped catalog rather than quoted from a document.

    A full day of eight A10G is $390.91 and a single hour of eight A100 is $21.96, and the
    rate ceiling sent the second to an admin and the first to a lead. Under v5 both are
    under the one bound and neither needs a person, which is the ranking a total gives and
    the rate could not.

    Mutation: put the rate comparison back into ``classify_request``. The A100 row becomes
    an exception while the A10G row stays automatic, and the assertion that they classify
    the same way fails. Both figures are recomputed from ``config/workload-catalog.yaml``,
    so a repricing that invalidates the reasoning fails here rather than in a comment.
    """
    a_full_day_of_eight_a10g = compute_maximum_compute_cost_usd(
        rate_of("gpu-8xa10g"), 1, Decimal(24), 1
    )
    one_hour_of_eight_a100 = compute_maximum_compute_cost_usd(
        rate_of("gpu-8xa100"), 1, Decimal(1), 1
    )

    assert a_full_day_of_eight_a10g == Decimal("390.91")
    assert one_hour_of_eight_a100 == Decimal("21.96")
    assert a_full_day_of_eight_a10g > one_hour_of_eight_a100 * 17

    limits = thresholds()
    assert (
        classify_request(facts(estimated_cost_usd=a_full_day_of_eight_a10g), limits)
        is ApprovalClass.AUTOMATIC
    )
    assert (
        classify_request(facts(estimated_cost_usd=one_hour_of_eight_a100), limits)
        is ApprovalClass.AUTOMATIC
    )


def test_two_full_days_of_eight_a10g_is_the_shape_a_lead_still_sees() -> None:
    """The boundary the representative manifests do not reach, priced off the catalog.

    Twenty-four hours at two attempts on ``gpu-8xa10g`` is $781.82, which is over the bound
    and is therefore a team lead's to release. It is here rather than in a fixture because a
    fixture would have to be repriced by hand every time that rate moved.

    Mutation: change ``<`` to ``>`` in the cost test. This row stays routine, so it is
    asserted beside the automatic row above it: the pair is what pins the direction.
    """
    two_days = compute_maximum_compute_cost_usd(rate_of("gpu-8xa10g"), 1, Decimal(24), 2)

    assert two_days == Decimal("781.82")
    assert classify_request(facts(estimated_cost_usd=two_days), thresholds()) is (
        ApprovalClass.ROUTINE
    )


def test_the_dearest_hour_in_the_catalog_is_held_back_by_the_machine_and_not_by_its_price() -> None:
    """What removing the rate ceiling admits, and what replaced it, in one pair.

    ``p6-b300.48xlarge`` is the dearest shape ``config/workload-catalog.yaml`` prices, and one
    hour of it at one attempt is $112.32. Under v4 the rate ceiling made that an admin's call.
    That ceiling is gone and $112.32 is under the one remaining bound, so **cost alone releases
    it to nobody** -- the first assertion, and the consequence the owner accepted when the
    ceiling came out.

    It does not reach nobody, because the shape is block-backed. The second assertion is the
    same money with the one fact about the machine restored, and it goes to an admin. Asserting
    the pair rather than either half is what distinguishes the two rules: a test on the cost
    alone would pass under a reinstated rate ceiling, and a test on the real profile alone would
    pass under one too. Only the disagreement between them says the instrument is
    reversibility rather than price.

    ``capacity_block_backed`` is read off the catalog rather than written here, so a repricing
    or a demotion moves this test with the file.

    Mutation: reintroduce any per-hour rule. The first assertion fails, which is the point of
    writing the accepted consequence down: the next person to find $112.32 alarming has to
    change the policy rather than quietly reinstate a bound. Mutation: drop the
    ``capacity_block_backed`` branch. The second fails.
    """
    profiles = load_workload_catalog().compute_profiles
    dearest = max(profiles, key=lambda profile: profile.hourly_rate_usd)
    one_hour = compute_maximum_compute_cost_usd(dearest.hourly_rate_usd, 1, Decimal(1), 1)

    assert one_hour == Decimal("112.32")
    assert dearest.capacity_block_backed is True
    assert classify_request(facts(estimated_cost_usd=one_hour), thresholds()) is (
        ApprovalClass.AUTOMATIC
    )
    assert (
        classify_request(
            facts(
                estimated_cost_usd=one_hour,
                capacity_block_backed=dearest.capacity_block_backed,
            ),
            thresholds(),
        )
        is ApprovalClass.EXCEPTION
    )


# --------------------------------------------------------------------------------------
# The shipped policy file
# --------------------------------------------------------------------------------------


def test_policy_yaml_validates_against_contract() -> None:
    project_root = Path(__file__).resolve().parents[1]
    config_path = project_root / "config" / "policy.yaml"
    policy = load_yaml(config_path, ApprovalPolicy)
    # v5 since the exception class stopped carrying runs. Pinned rather than merely
    # pattern-checked so that a policy change without a version bump fails here, which is
    # the whole reason a decision record carries the version.
    assert policy.policy_version == "v5"
    assert policy.thresholds.automatic_below_cost_usd == Decimal(500)
    assert policy.routine_approver_role == "team_lead"
    assert policy.exception_approver_roles == ("platform_admin",)
    assert policy.image_scan.blocking_severities == (ImageScanSeverity.CRITICAL,)
    # image_scan_findings_unreviewed came off this list in v4 and is deliberately absent
    # rather than reordered. It is still a legal value of the field and still derived as a
    # fact; what changed in v5 is that a team lead releases it rather than an admin.
    assert policy.denied_outright == (
        "unregistered_repository",
        "unregistered_dataset",
        "unregistered_compute_profile",
        "mutable_repository_revision",
        "mutable_image_reference",
        "dataset_is_not_a_corpus",
    )
    assert "image_scan_findings_unreviewed" not in policy.denied_outright


@pytest.mark.parametrize(
    "retired",
    [
        "routine_maximum_cost_usd",
        "routine_maximum_runtime_hours",
        "routine_maximum_attempts",
        "routine_maximum_fanout_size",
        "routine_maximum_parallelism",
        "automatic_below_runtime_hours",
    ],
)
def test_a_threshold_v5_retired_cannot_be_put_back_by_editing_the_file(retired: str) -> None:
    """A half-applied revert is refused rather than half-obeyed.

    ``ContractModel`` sets ``extra="forbid"``, so a ``policy.yaml`` that still names one of
    the six retired bounds fails to load rather than loading with a field nothing reads.
    That is the failure mode this pins: a bound written back into the file by somebody
    expecting it to gate something would otherwise sit there gating nothing, which is the
    exact defect ``routine_maximum_parallelism`` was for two months.

    Mutation: give ``PolicyThresholds`` ``extra="allow"``, or add any of these back as a
    field. The row for it stops raising.
    """
    with pytest.raises(ValidationError):
        PolicyThresholds.model_validate({**thresholds_payload(), retired: "1"})


def test_approval_policy_requires_the_version_that_produced_a_decision() -> None:
    payload = policy_payload()
    del payload["policy_version"]
    with pytest.raises(ValidationError) as exc_info:
        ApprovalPolicy.model_validate(payload)
    assert_validation_error(exc_info.value, error_type="missing")
    assert exc_info.value.errors()[0]["loc"] == ("policy_version",), (
        "a decision record that named only the outcome could not be reread once the "
        "thresholds moved, so the version cannot default"
    )


@pytest.mark.parametrize("policy_version", ["", "1", "v0", "v01", "v1.1", "V1", "v-1", "vnext"])
def test_approval_policy_rejects_a_version_that_is_not_monotonic(policy_version: str) -> None:
    payload = policy_payload()
    payload["policy_version"] = policy_version
    with pytest.raises(ValidationError) as exc_info:
        ApprovalPolicy.model_validate(payload)
    assert exc_info.value.errors()[0]["loc"] == ("policy_version",)


@pytest.mark.parametrize("policy_version", ["v1", "v2", "v10", "v137"])
def test_approval_policy_accepts_successive_monotonic_versions(policy_version: str) -> None:
    payload = policy_payload()
    payload["policy_version"] = policy_version
    assert ApprovalPolicy.model_validate(payload).policy_version == policy_version


def test_approval_policy_rejects_routine_role_satisfying_exception() -> None:
    payload = policy_payload()
    payload["exception_approver_roles"] = ["team_lead"]
    with pytest.raises(ValidationError) as exc_info:
        ApprovalPolicy.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="routine approver role must not satisfy exception approval on its own",
    )


def test_approval_policy_rejects_unknown_denied_outright_condition() -> None:
    payload = policy_payload()
    payload["denied_outright"] = ["unregistered_repository", "unknown_condition"]
    with pytest.raises(ValidationError) as exc_info:
        ApprovalPolicy.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="literal_error",
    )


def test_policy_thresholds_reject_non_decimal_cost() -> None:
    with pytest.raises(ValidationError) as exc_info:
        PolicyThresholds.model_validate({"automatic_below_cost_usd": 500})
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="decimal values must be non-negative base-10 strings",
    )


def test_policy_thresholds_reject_a_bound_of_zero() -> None:
    """Zero would make the automatic class empty rather than turn it off.

    Mutation: change ``gt=0`` to ``ge=0``. A policy declaring zero would load, every run
    would go to a lead, and nothing would say the class had been switched off by a value
    rather than by a decision.
    """
    with pytest.raises(ValidationError) as exc_info:
        PolicyThresholds.model_validate({"automatic_below_cost_usd": "0"})
    assert_validation_error(exc_info.value, error_type="greater_than")


def test_request_facts_reject_non_decimal_runtime() -> None:
    with pytest.raises(ValidationError) as exc_info:
        RequestFacts.model_validate(request_facts_payload(maximum_runtime_hours=24))
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="decimal values must be non-negative base-10 strings",
    )


def test_request_facts_require_an_explicit_claimed_team() -> None:
    payload = request_facts_payload()
    del payload["claimed_team"]
    with pytest.raises(ValidationError) as exc_info:
        RequestFacts.model_validate(payload)
    assert_validation_error(exc_info.value, error_type="missing")
    assert exc_info.value.errors()[0]["loc"] == ("claimed_team",), (
        "attribution must be supplied deliberately; a default would let a caller skip it"
    )


@pytest.mark.parametrize("claimed_team", ["", "Memory Split", "memory_split", "-memory-split"])
def test_request_facts_reject_a_claimed_team_that_is_not_a_team_identifier(
    claimed_team: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        RequestFacts.model_validate(request_facts_payload(claimed_team=claimed_team))
    assert exc_info.value.errors()[0]["loc"] == ("claimed_team",)


@pytest.mark.parametrize(
    ("payload_override", "field", "error_type"),
    [
        ({"estimated_cost_usd": Decimal(-5)}, "estimated_cost_usd", "greater_than_equal"),
        ({"maximum_runtime_hours": Decimal(0)}, "maximum_runtime_hours", "greater_than"),
        ({"maximum_attempts": 0}, "maximum_attempts", "greater_than_equal"),
        ({"fanout_size": 0}, "fanout_size", "greater_than_equal"),
        ({"fanout_parallelism": 0}, "fanout_parallelism", "greater_than_equal"),
    ],
)
def test_request_facts_reject_out_of_range_values(
    payload_override: dict[str, object],
    field: str,
    error_type: str,
) -> None:
    payload = request_facts_payload(**payload_override)
    with pytest.raises(ValidationError) as exc_info:
        RequestFacts.model_validate(payload)
    assert_validation_error(exc_info.value, error_type=error_type)
    assert exc_info.value.errors()[0]["loc"] == (field,)


def test_approval_policy_rejects_empty_routine_approver_role() -> None:
    payload = policy_payload()
    payload["routine_approver_role"] = ""
    with pytest.raises(ValidationError) as exc_info:
        ApprovalPolicy.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="string_too_short",
    )
    assert exc_info.value.errors()[0]["loc"] == ("routine_approver_role",)


def test_approval_policy_rejects_empty_exception_approver_roles() -> None:
    payload = policy_payload()
    payload["exception_approver_roles"] = []
    with pytest.raises(ValidationError) as exc_info:
        ApprovalPolicy.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="too_short",
    )
    assert exc_info.value.errors()[0]["loc"] == ("exception_approver_roles",)


def test_approval_policy_rejects_empty_denied_outright() -> None:
    payload = policy_payload()
    payload["denied_outright"] = []
    with pytest.raises(ValidationError) as exc_info:
        ApprovalPolicy.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="too_short",
    )
    assert exc_info.value.errors()[0]["loc"] == ("denied_outright",)


@pytest.mark.parametrize("filename", REPRESENTATIVE_MANIFEST_FILENAMES)
def test_representative_manifest_classifies_as_expected(filename: str) -> None:
    manifest = load_representative_manifest(filename)
    catalog = load_workload_catalog()
    policy = load_approval_policy()
    cost = compute_manifest_cost_inputs(manifest, catalog)
    request = request_facts_from_manifest(
        manifest,
        repositories=load_repository_registry(),
        catalog=catalog,
        dataset_registry=load_dataset_registry(),
        estimated_cost_usd=cost.maximum_compute_cost_usd,
    )
    expected = expected_classification(filename)
    assert classify_request(request, policy.thresholds) == expected, (
        f"{filename} classification mismatch for {request=}"
    )


def test_the_only_representative_manifest_a_person_releases_is_the_fanout() -> None:
    """What v5 did to the reviewed fixtures, said once rather than inferred from six rows.

    Mutation: delete the fan-out test from ``classify_request``. Every representative
    manifest becomes automatic, this assertion finds an empty set, and the fixture suite
    stops covering the routine path at all.
    """
    routine = {
        filename
        for filename in REPRESENTATIVE_MANIFEST_FILENAMES
        if expected_classification(filename) is ApprovalClass.ROUTINE
    }

    assert routine == {"multiseed-routine.yaml"}
    assert load_representative_manifest("multiseed-routine.yaml").fanout is not None


def test_gpu_exception_is_registered_throughout_and_is_now_released_by_nobody() -> None:
    """The fixture whose name is a class that no longer exists, and what it proves instead.

    Twenty-five hours on ``gpu-4xa10g`` was an exception on runtime alone under v4, which is
    what the file was built for and where its name came from. There is no runtime ceiling
    now, it is $141.80 in one cell, and it is released by nobody. It keeps the name in this
    change because sixty-eight references across twenty files is a rename of its own.

    Mutation: put a runtime ceiling back into ``classify_request``. The last assertion fails.
    """
    manifest = load_representative_manifest("gpu-exception.yaml")
    catalog = load_workload_catalog()
    policy = load_approval_policy()
    request = request_facts_from_manifest(
        manifest,
        repositories=load_repository_registry(),
        catalog=catalog,
        dataset_registry=load_dataset_registry(),
        estimated_cost_usd=compute_manifest_maximum_cost(manifest, catalog),
    )

    assert request.repository_registered is True
    assert request.dataset_registered is True
    assert request.compute_profile_registered is True
    assert is_workload_profile_registered(manifest, catalog)
    assert request.immutable_revision is True
    assert request.immutable_image is True
    assert manifest.maximum_runtime_hours == Decimal(25)
    assert classify_request(request, policy.thresholds) is ApprovalClass.AUTOMATIC


def test_request_facts_from_manifest_rejects_unregistered_repository() -> None:
    manifest = load_representative_manifest("cpu-routine.yaml")
    catalog = load_workload_catalog()
    broken_manifest = manifest.model_copy(update={"repository": "not-a-registered-repository"})
    request = request_facts_from_manifest(
        broken_manifest,
        repositories=load_repository_registry(),
        catalog=catalog,
        dataset_registry=load_dataset_registry(),
        estimated_cost_usd=Decimal(1),
    )
    assert request.repository_registered is False
    assert classify_request(request, thresholds()) is ApprovalClass.ROUTINE
