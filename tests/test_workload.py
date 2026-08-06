from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from edullm_platform.config import load_yaml
from edullm_platform.contracts.policy import ApprovalPolicy
from edullm_platform.contracts.workload import (
    CheckpointContract,
    ComputeProfileResolutionError,
    CostInputs,
    UnprovisionedComputeProfileError,
    UnregisteredComputeProfileError,
    WorkloadCatalog,
    resolve_compute_profile_for_execution,
)
from edullm_platform.execution import CONTAINER_SHAPES, RETRY_ONLY_WHAT_A_RETRY_FIXES

COMPLIANT_DESTINATION_PREFIX = "s3://sbsandbox-intern-edullm-checkpoints/runs/"

#: What one 370M arm of edullm-p1 has cost, in A100 device-hours, read off that repository's
#: own READMEs rather than off this catalog. ``experiments/skill-dag/mixlaw`` records the four
#: MixLaw arms at 44.11, 47.46, 48.39 and 48.78; ``experiments/token-selection`` records seven
#: more at the same architecture and the same one-epoch budget, from 42.99 to 70.5. The
#: heaviest is what ``edullm-p1-train`` has to bound, and it is deliberately not the MixLaw
#: one: the entry names the repository rather than one experiment in it.
EDULLM_P1_HEAVIEST_ARM_A100_HOURS = Decimal("70.5")

#: What this platform adds that a figure measured on FarmShare never paid: a CUDA image with a
#: prebuilt flash-attn wheel to pull, about 40 GB of shards to stage out of edullm-data and
#: concatenate on a 250 MB/s root volume, torch.compile, and twenty ladder checkpoints to push
#: back to S3.
EDULLM_P1_PLATFORM_OVERHEAD_HOURS = Decimal(1)

#: ``ARRAY_ARMS`` in ``experiments/skill-dag/mixlaw/platform_array_entrypoint.py``. Nothing on
#: this side reads it -- the fan-out size arrives with a submission -- so it is written here as
#: the shape of the submission this entry was added for.
EDULLM_P1_ARRAY_ARMS = 7


def catalog_payload() -> dict[str, object]:
    return {
        "compute_profiles": [
            {
                "name": "cpu-32vcpu",
                "instance_type": "c7i.8xlarge",
                "accelerator": "cpu",
                "nodes": 1,
                "hourly_rate_usd": "1.428",
                "pricing_source": "test",
                "pricing_observed_at": "2026-07-24",
                "provisioned": False,
            },
            {
                "name": "gpu-4xa10g",
                "instance_type": "g5.12xlarge",
                "accelerator": "gpu",
                "nodes": 1,
                "hourly_rate_usd": "5.672",
                "pricing_source": "test",
                "pricing_observed_at": "2026-07-24",
                "provisioned": False,
            },
        ],
        "workloads": [
            {
                "name": "dolma-tokenize",
                "repository": "dolma",
                "maximum_runtime_hours": "2",
                "maximum_attempts": 1,
                "checkpoint": None,
            },
            {
                "name": "olmo-core-train",
                "repository": "OLMo-core",
                "maximum_runtime_hours": "1",
                "maximum_attempts": 1,
                "checkpoint": {
                    "interval_minutes": 30,
                    "destination_prefix": COMPLIANT_DESTINATION_PREFIX,
                    "resume_required": False,
                },
            },
        ],
    }


def checkpoint_payload(destination_prefix: str) -> dict[str, object]:
    return {
        "interval_minutes": 30,
        "destination_prefix": destination_prefix,
        "resume_required": False,
    }


def catalog_with_provisioned(*provisioned_names: str) -> WorkloadCatalog:
    payload = catalog_payload()
    payload["compute_profiles"] = [
        {**profile, "provisioned": profile["name"] in provisioned_names}
        for profile in payload["compute_profiles"]  # type: ignore[union-attr]
    ]
    return WorkloadCatalog.model_validate(payload)


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


def test_cost_estimate_is_deterministic() -> None:
    inputs = CostInputs(
        hourly_rate_usd=Decimal("12.25"),
        nodes=2,
        maximum_runtime_hours=Decimal(6),
        maximum_attempts=2,
    )
    assert inputs.maximum_compute_cost_usd == Decimal("294.00")


@pytest.mark.parametrize(
    ("probe_name", "payload"),
    [
        (
            "reviewer original",
            {
                "hourly_rate_usd": "5.672",
                "nodes": 10**13,
                "maximum_runtime_hours": "24",
                "maximum_attempts": 10**13,
            },
        ),
        (
            "max under new bounds",
            {
                "hourly_rate_usd": "9" * 28,
                "nodes": 1_000_000,
                "maximum_runtime_hours": "9" * 28,
                "maximum_attempts": 1_000_000,
            },
        ),
        (
            "modest overflow probe",
            {
                "hourly_rate_usd": "9" * 20,
                "nodes": 10**13,
                "maximum_runtime_hours": "9" * 20,
                "maximum_attempts": 10**13,
            },
        ),
    ],
)
def test_cost_inputs_reject_overflow_product_probes(
    probe_name: str,
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        CostInputs.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="maximum compute cost exceeds representable precision",
    )


def test_valid_cost_inputs_canonical_json_bytes_does_not_raise() -> None:
    from edullm_platform.canonical import canonical_json_bytes

    inputs = CostInputs(
        hourly_rate_usd=Decimal("12.25"),
        nodes=2,
        maximum_runtime_hours=Decimal(6),
        maximum_attempts=2,
    )
    encoded = canonical_json_bytes(inputs)
    assert b'"maximum_compute_cost_usd":"294.00"' in encoded


def test_catalog_requires_cpu_and_gpu_compute_profile_representatives() -> None:
    """The rule that moved when a workload profile stopped naming a machine.

    It used to read the accelerator of the profile each workload declared, so a catalog
    whose every workload sat on a CPU profile was refused. There is no such join now, and
    the property the rule was establishing is a fact about the profiles on their own: a
    catalog pricing only one kind of accelerator offers a submission form on which the
    whole of the other kind is unpickable.
    """
    with pytest.raises(ValidationError) as exc_info:
        WorkloadCatalog.model_validate(
            {
                "compute_profiles": [
                    {
                        "name": "cpu-small",
                        "instance_type": "c7i.xlarge",
                        "accelerator": "cpu",
                        "nodes": 1,
                        "hourly_rate_usd": "1.00",
                        "pricing_source": "test",
                        "pricing_observed_at": "2026-07-24",
                        "provisioned": False,
                    },
                    {
                        "name": "cpu-large",
                        "instance_type": "c7i.8xlarge",
                        "accelerator": "cpu",
                        "nodes": 1,
                        "hourly_rate_usd": "2.00",
                        "pricing_source": "test",
                        "pricing_observed_at": "2026-07-24",
                        "provisioned": False,
                    },
                ],
                "workloads": [
                    {
                        "name": "tokenize-smoke",
                        "repository": "dolma",
                        "maximum_runtime_hours": "2",
                        "maximum_attempts": 1,
                        "checkpoint": None,
                    },
                    {
                        "name": "prep-smoke",
                        "repository": "dolma",
                        "maximum_runtime_hours": "1",
                        "maximum_attempts": 1,
                        "checkpoint": None,
                    },
                ],
            }
        )
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="representative CPU and GPU compute profiles are required",
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "cpu_only_profiles",
        "duplicate_profile_name",
        "duplicate_workload_name",
        "workload_naming_a_machine",
        "retryable_without_checkpoint",
        "invalid_destination_prefix",
    ],
)
def test_catalog_rejects_invalid_binding_rules(mutation: str) -> None:
    payload = catalog_payload()
    if mutation == "cpu_only_profiles":
        profiles = list(payload["compute_profiles"])  # type: ignore[arg-type]
        profiles[1] = {**profiles[1], "accelerator": "cpu"}
        payload["compute_profiles"] = profiles
        expected_type = "value_error"
        expected_message = "representative CPU and GPU compute profiles are required"
    elif mutation == "duplicate_profile_name":
        profiles = list(payload["compute_profiles"])  # type: ignore[arg-type]
        profiles[1] = {**profiles[1], "name": profiles[0]["name"]}
        payload["compute_profiles"] = profiles
        expected_type = "value_error"
        expected_message = "compute profile names must be unique"
    elif mutation == "duplicate_workload_name":
        workloads = list(payload["workloads"])  # type: ignore[arg-type]
        workloads[1] = {**workloads[1], "name": workloads[0]["name"]}
        payload["workloads"] = workloads
        expected_type = "value_error"
        expected_message = "workload names must be unique"
    elif mutation == "workload_naming_a_machine":
        # THE ROW THAT USED TO SAY "unknown compute profile" AND NOW SAYS THE FIELD IS GONE.
        # A workload profile declaring a machine was the defect rather than the guard: the
        # submission form overrode whatever it said, so the catalog's answer was read only
        # when nobody supplied one. The contract forbids the key outright, which is what
        # stops a well-meaning edit putting it back and having it silently ignored.
        workloads = list(payload["workloads"])  # type: ignore[arg-type]
        workloads[0] = {**workloads[0], "compute_profile": "cpu-32vcpu"}
        payload["workloads"] = workloads
        expected_type = "extra_forbidden"
        expected_message = None
    elif mutation == "retryable_without_checkpoint":
        workloads = list(payload["workloads"])  # type: ignore[arg-type]
        workloads[0] = {
            **workloads[0],
            "maximum_attempts": 2,
            "checkpoint": None,
        }
        payload["workloads"] = workloads
        expected_type = "value_error"
        expected_message = "retryable workloads require a checkpoint contract"
    else:
        workloads = list(payload["workloads"])  # type: ignore[arg-type]
        checkpoint = dict(workloads[1]["checkpoint"])  # type: ignore[index]
        checkpoint["destination_prefix"] = "s3://edullm-checkpoints"
        workloads[1] = {**workloads[1], "checkpoint": checkpoint}
        payload["workloads"] = workloads
        expected_type = "string_pattern_mismatch"
        expected_message = None

    with pytest.raises(ValidationError) as exc_info:
        WorkloadCatalog.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type=expected_type,
        message_fragment=expected_message,
    )


def test_workload_catalog_yaml_validates_against_contract() -> None:
    project_root = Path(__file__).resolve().parents[1]
    config_path = project_root / "config" / "workload-catalog.yaml"
    catalog = load_yaml(config_path, WorkloadCatalog)
    # Seventeen: thirteen once gpu-8xa10g priced the g5.48xlarge, then gpu-8xt4, gpu-8xl4 and
    # gpu-8xl40s filling the eight-device row, then gpu-1xh100. Same tripwire role as the
    # workload count below: a profile arriving without a deliberate edit.
    assert len(catalog.compute_profiles) == 17
    # Eleven: nine, plus olmo-eval-sweep and edullm-p1-check. Nine was seven plus
    # open-instruct-scored-rewards-check and open-instruct-scored-rewards-train. The seven
    # were five since the presets collapsed
    # plus edullm-alt-cl-check and edullm-alt-cl-train. It was seven before the collapse
    # too, and the two pairs that merged -- olmo-core-check-cpu with olmo-core-check-gpu,
    # and olmo-core-train-1gpu with olmo-core-train-4gpu -- differed only in a compute
    # profile the submission form overrode. The count is the tripwire for a workload
    # appearing without a deliberate edit, so it moves with the edit and not before.
    #
    # The pair added together rather than the check alone, which is the shape
    # edullm-alt-cl set: a repository registered for training and given only a one-hour
    # check has a dropdown entry and still nowhere to run the work it was registered for.
    #
    # Ten since olmo-eval-sweep, and it is the one entry here added singly rather than as a
    # pair. olmo-eval-full was already registered and already had olmo-eval-check, so the
    # thing edullm-alt-cl was missing -- somewhere to run the work the repository exists for
    # -- is what this adds, on the two-hour GPU shape a real benchmark split needs.
    #
    # ELEVEN SINCE edullm-p1-check, WHICH IS THE SECOND ENTRY ADDED SINGLY AND FOR THE OTHER
    # REASON. olmo-eval-sweep is single because the pair was already complete; this one is
    # single because the pair cannot be written yet. edullm-p1's workload is a seven-arm
    # Batch array over experiments/skill-dag/mixlaw, so a -train entry would have to name a
    # runtime, an attempt count and a checkpoint contract nobody has measured, and the
    # argument above is precisely that a bound written without a measurement is a ceiling
    # pretending to be an estimate. The pre-training team owns those three numbers; the
    # check exists so the path can be proved while they pick.
    #
    # TWELVE SINCE edullm-p1-train COMPLETED THAT PAIR. The three numbers were read off the
    # repository and off the retry policy rather than picked: the measured device-hours of
    # the eleven 370M arms it has run, the 125-step ladder its own checkpoint_ladder.py
    # fixes, and one attempt, because nothing here resumes and Batch's only RETRY rule is a
    # host fault. The entry in config/workload-catalog.yaml argues each of them, and
    # test_edullm_p1_train_bounds_a_real_mixlaw_arm below pins the two that decide what a
    # submission costs.
    assert len(catalog.workloads) == 12
    # The check Phase 3 runs. It names OLMo-core, which was the only registered repository
    # with a published image when this was written; dolma-tokenize is the same shape against
    # a repository that still has neither.
    runnable_check = next(
        workload for workload in catalog.workloads if workload.name == "olmo-core-check"
    )
    assert runnable_check.repository == "OLMo-core"
    # THE MACHINE IS AN ARGUMENT HERE BECAUSE IT IS AN ARGUMENT ON THE FORM. This used to
    # read the profile each workload declared and price it against that. A preset declares
    # no machine now, so pricing one is a question about a submission rather than about the
    # catalog, and these two are the pairings a submitter would actually make.
    profile_by_name = {profile.name: profile for profile in catalog.compute_profiles}
    cpu_workload = next(
        workload for workload in catalog.workloads if workload.name == "dolma-tokenize"
    )
    gpu_workload = next(
        workload for workload in catalog.workloads if workload.name == "olmo-core-train"
    )
    cpu_profile = profile_by_name["cpu-32vcpu"]
    gpu_profile = profile_by_name["gpu-4xa10g"]
    cpu_cost = CostInputs(
        hourly_rate_usd=cpu_profile.hourly_rate_usd,
        nodes=cpu_profile.nodes,
        maximum_runtime_hours=cpu_workload.maximum_runtime_hours,
        maximum_attempts=cpu_workload.maximum_attempts,
    )
    gpu_cost = CostInputs(
        hourly_rate_usd=gpu_profile.hourly_rate_usd,
        nodes=gpu_profile.nodes,
        maximum_runtime_hours=gpu_workload.maximum_runtime_hours,
        maximum_attempts=gpu_workload.maximum_attempts,
    )
    assert cpu_cost.maximum_compute_cost_usd == Decimal("2.86")
    # Was 136.13, which was twelve hours across two attempts at $5.672.
    # routine_maximum_runtime_hours went from 12 to 24 and this entry's bound went with it,
    # so the ceiling a four-GPU training run can reach doubled and is still under $500.
    assert gpu_cost.maximum_compute_cost_usd == Decimal("272.26")


def test_edullm_p1_train_bounds_a_real_mixlaw_arm() -> None:
    """Mutations: put the bound back to the check's ``"1"``, or widen it to ``"24"``.

    Both go red, and they are the two ways this entry gets quietly ruined. One hour is what
    ``edullm-p1-check`` declares and what the repository had before this entry existed, and a
    revert to it re-refuses every real arm with ``runtime_above_the_workload_bound``.
    Twenty-four is the figure the three neighbouring training entries carry, and copying it
    here would inflate the ceiling a lead reads from $1,537 to $3,689 for a run nothing
    measured at more than nine hours.

    **The bound is checked against device-hours measured in the other repository, not against
    itself.** ``7 x hours x rate`` computed from the catalog and asserted against the
    catalog is a test that passes for every value of ``hours``, which is the shape of the six
    assertions edullm-p1's own suite was found echoing its inputs back. So the arithmetic
    starts at :data:`EDULLM_P1_HEAVIEST_ARM_A100_HOURS`, divides by the device count
    ``CONTAINER_SHAPES`` declares for the shape this entry names, and adds the platform's own
    overhead -- three numbers from three files, none of them this one.

    The window is one hour wide, which is what makes the upper mutation fail. A whole-hour
    bound has exactly one value in it.
    """
    project_root = Path(__file__).resolve().parents[1]
    catalog = load_yaml(project_root / "config" / "workload-catalog.yaml", WorkloadCatalog)
    train = next(workload for workload in catalog.workloads if workload.name == "edullm-p1-train")
    assert train.repository == "edullm-p1"

    devices = CONTAINER_SHAPES["gpu-8xa100"].gpus
    needed = EDULLM_P1_HEAVIEST_ARM_A100_HOURS / devices + EDULLM_P1_PLATFORM_OVERHEAD_HOURS
    assert train.maximum_runtime_hours >= needed
    assert train.maximum_runtime_hours < needed + 1

    # ONE ATTEMPT, PINNED TO ITS PREMISE RATHER THAN TO THE NUMBER. A second attempt of this
    # workload re-runs the arm from step 0 -- platform_array_entrypoint.py writes
    # RECOVERY_MODE=fail, each attempt gets an empty /scratch, and resolve_load_path refuses
    # the s3:// path the durable ladder is at -- so two would be two full arms for one result
    # and would double what a lead approves. The premise underneath that is the retry policy:
    # its only RETRY is a host fault, which a container failing on its own merits never
    # produces. A fourth rule that retries what this workload does die of is exactly the
    # change that would make two attempts worth buying, and it goes red here rather than
    # leaving this number sitting on a comment nobody rereads.
    assert train.maximum_attempts == 1
    assert [rule for rule in RETRY_ONLY_WHAT_A_RETRY_FIXES if rule["Action"] == "RETRY"] == [
        {"OnStatusReason": "Host EC2*", "Action": "RETRY"}
    ]

    # The contract stays at one attempt, because what it buys here is durability rather than
    # resume: it is what puts twenty ladder checkpoints in S3 for a person to restore by hand,
    # and what makes require_a_save_folder_a_retry_can_find demand that the command expand
    # $EDULLM_CHECKPOINT_DIR at all. resume_required says which of the two this is, and a true
    # would be the false promise the field exists to expose.
    assert train.checkpoint is not None
    assert train.checkpoint.resume_required is False

    # What the two readers see. A lead reads the array; nobody reads a single cell, and no
    # single cell can run this entrypoint either -- FanOut.size starts at 2.
    profile = next(entry for entry in catalog.compute_profiles if entry.name == "gpu-8xa100")
    assert profile.provisioned
    cost = CostInputs(
        hourly_rate_usd=profile.hourly_rate_usd,
        nodes=profile.nodes,
        maximum_runtime_hours=train.maximum_runtime_hours,
        maximum_attempts=train.maximum_attempts,
    )
    array = cost.model_copy(update={"cells": EDULLM_P1_ARRAY_ARMS})
    automatic_below = load_yaml(
        project_root / "config" / "policy.yaml", ApprovalPolicy
    ).thresholds.automatic_below_cost_usd
    assert cost.maximum_compute_cost_usd < automatic_below
    assert array.maximum_compute_cost_usd >= automatic_below


def test_catalog_rejects_duplicate_profile_name_when_every_other_field_differs() -> None:
    payload = catalog_payload()
    profiles = list(payload["compute_profiles"])  # type: ignore[arg-type]
    profiles.append(
        {
            "name": "gpu-4xa10g",
            "instance_type": "p5.48xlarge",
            "accelerator": "gpu",
            "nodes": 4,
            "hourly_rate_usd": "55.0400",
            "pricing_source": "other",
            "pricing_observed_at": "2026-07-25",
            "provisioned": True,
        }
    )
    payload["compute_profiles"] = profiles
    with pytest.raises(ValidationError) as exc_info:
        WorkloadCatalog.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="compute profile names must be unique",
    )


def test_checkpoint_accepts_sandbox_owned_destination_prefix() -> None:
    checkpoint = CheckpointContract.model_validate(checkpoint_payload(COMPLIANT_DESTINATION_PREFIX))
    assert checkpoint.destination_prefix == COMPLIANT_DESTINATION_PREFIX


@pytest.mark.parametrize(
    "destination_prefix",
    [
        "s3://edullm-checkpoints/runs/",
        "s3://sbsandbox-intern/runs/",
        "s3://not-sbsandbox-intern-checkpoints/runs/",
        "s3://SBSANDBOX-INTERN-checkpoints/runs/",
        "s3://sbsandbox-intern-checkpoints/runs",
        "s3://sbsandbox-intern-checkpoints/",
        "s3://sbsandbox-intern-/runs/",
        "s3://sbsandbox-intern-checkpoints-/runs/",
    ],
)
def test_checkpoint_rejects_destination_prefix_outside_sandbox_bucket_namespace(
    destination_prefix: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        CheckpointContract.model_validate(checkpoint_payload(destination_prefix))
    assert_validation_error(exc_info.value, error_type="string_pattern_mismatch")


def test_catalog_rejects_workload_checkpoint_outside_sandbox_bucket_namespace() -> None:
    payload = catalog_payload()
    workloads = list(payload["workloads"])  # type: ignore[arg-type]
    workloads[1] = {
        **workloads[1],
        "checkpoint": checkpoint_payload("s3://edullm-checkpoints/runs/"),
    }
    payload["workloads"] = workloads
    with pytest.raises(ValidationError) as exc_info:
        WorkloadCatalog.model_validate(payload)
    assert_validation_error(exc_info.value, error_type="string_pattern_mismatch")


def test_provisioned_profile_resolves_for_execution() -> None:
    catalog = catalog_with_provisioned("gpu-4xa10g")
    profile = resolve_compute_profile_for_execution(catalog, "gpu-4xa10g")
    assert profile.name == "gpu-4xa10g"
    assert profile.provisioned is True


def test_resolving_unprovisioned_profile_reports_missing_capacity_not_missing_profile() -> None:
    catalog = catalog_with_provisioned()
    with pytest.raises(UnprovisionedComputeProfileError) as exc_info:
        resolve_compute_profile_for_execution(catalog, "gpu-4xa10g")
    assert exc_info.value.reason_code == "unprovisioned_compute_profile"
    assert isinstance(exc_info.value, ComputeProfileResolutionError)
    assert not isinstance(exc_info.value, UnregisteredComputeProfileError)
    assert "g5.12xlarge" in str(exc_info.value)


def test_resolving_unknown_profile_reports_unregistered_profile() -> None:
    catalog = catalog_with_provisioned("gpu-4xa10g")
    with pytest.raises(UnregisteredComputeProfileError) as exc_info:
        resolve_compute_profile_for_execution(catalog, "gpu-8xh100")
    assert exc_info.value.reason_code == "unregistered_compute_profile"
    assert isinstance(exc_info.value, ComputeProfileResolutionError)
    assert not isinstance(exc_info.value, UnprovisionedComputeProfileError)


def test_unprovisioned_and_unregistered_resolution_failures_are_distinguishable() -> None:
    catalog = catalog_with_provisioned()
    with pytest.raises(UnregisteredComputeProfileError) as unregistered:
        resolve_compute_profile_for_execution(catalog, "gpu-8xh100")
    with pytest.raises(UnprovisionedComputeProfileError) as unprovisioned:
        resolve_compute_profile_for_execution(catalog, "gpu-4xa10g")
    assert type(unregistered.value) is not type(unprovisioned.value)
    assert unregistered.value.reason_code != unprovisioned.value.reason_code
    assert str(unregistered.value) != str(unprovisioned.value)


def test_resolution_failures_remain_value_errors() -> None:
    catalog = catalog_with_provisioned()
    for profile_name in ("gpu-8xh100", "gpu-4xa10g"):
        with pytest.raises(ValueError):
            resolve_compute_profile_for_execution(catalog, profile_name)
