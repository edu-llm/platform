"""What an accepted manifest turns into, and what a profile with nowhere to run turns into.

Two things this module is careful about, because both are ways a green suite covers a
broken path.

The submit request is checked field by field against the manifest that produced it rather
than against a recorded literal. A golden copy of the parameter block would pass forever
after somebody hardcoded the timeout, because the golden would be regenerated from the
hardcoded value; deriving the expectation from the manifest is what makes the mutation
visible.

And the catalog-to-targets seam is read from both files. Asserting that
``config/execution-targets.yaml`` parses says nothing about whether it backs the profile
``config/workload-catalog.yaml`` promoted, which is the specific way Phase 4 would claim
capacity that does not exist.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.admission import AdmissionOutcome, admit
from edullm_platform.canonical import sha256_digest
from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import AdmissionReason, ApprovalEnvironment
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.execution import (
    ExecutionTarget,
    ExecutionTargetBinding,
    ExecutionTargetCatalog,
    UnbackedComputeProfileError,
)
from edullm_platform.contracts.image import GitHubWorkflowRunReference
from edullm_platform.contracts.image_scan import (
    ImageScanExceptionRegistry,
    ImageScanStatus,
    ImageScanSummary,
)
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.policy import ApprovalPolicy
from edullm_platform.contracts.results import output_prefix
from edullm_platform.contracts.workload import (
    UnprovisionedComputeProfileError,
    UnregisteredComputeProfileError,
    WorkloadCatalog,
)
from edullm_platform.execution import (
    MINIMUM_ATTEMPT_DURATION_SECONDS,
    attempt_duration_seconds,
    batch_submit_request,
    resolve_execution_target,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
MANIFEST_FIXTURES_DIR = PROJECT_ROOT / "fixtures" / "manifests"

#: Twelve digits that are not this account's. Every ARN below is assembled from it, and a
#: real account id in a committed test is the value every capture tool has to redact.
ACCOUNT_ID = "123456789012"

RUN_ID = "run_0198f0a1-2b3c-7d4e-8f01-23456789abcd"
RECORDED_AT = datetime(2026, 7, 27, 9, 15, 30, 123456, tzinfo=UTC)

PROMOTED_PROFILE = "cpu-32vcpu"
#: Every profile the catalog marks provisioned, in the order it promotes them. The seam
#: test below compares this against both config files; the constant exists so that adding
#: a profile is one visible edit rather than a number somebody increments.
PROMOTED_PROFILES = (PROMOTED_PROFILE, "gpu-1xa10g")
UNPROVISIONED_PROFILE = "gpu-4xa10g"
UNREGISTERED_PROFILE = "cpu-1024vcpu"

MEMBER = "caiiris"
LEAD = "ericrcwu001"


def workload_catalog() -> WorkloadCatalog:
    return load_yaml(CONFIG_DIR / "workload-catalog.yaml", WorkloadCatalog)


def execution_targets() -> ExecutionTargetCatalog:
    return load_yaml(CONFIG_DIR / "execution-targets.yaml", ExecutionTargetCatalog)


def manifest(**overrides: Any) -> RunManifest:
    """A manifest that admission accepts, with whatever this test needs changed.

    Built from the deployed CPU workload rather than from a fixture file, because the
    fields these tests are about -- runtime, attempts, fan-out -- are exactly the ones each
    test has to vary, and a fixture per variation is a fixture per assertion.
    """
    payload: dict[str, Any] = {
        "schema_version": 1,
        "repository": "OLMo-core",
        "commit_sha": "4204375e6db85abc244ec7f626de8d3cc3511402",
        "image_digest": (
            "sha256:4ebdba1ba3b57096efb4f4647ed41ed5ded4ac9e77e8c9038b7ff24db0bc6db8"
        ),
        "dataset_release": "dolma-2026-07",
        "command": ["python", "-m", "olmo_core.train", "--config", "smoke"],
        "team": "memory-split",
        "wandb_project": "olmo-core-memory-split",
        "workload_profile": "olmo-core-cpu-smoke",
        "compute_profile": PROMOTED_PROFILE,
        "maximum_runtime_hours": "1",
        "maximum_attempts": 1,
        "checkpoint": None,
        "fanout": None,
    }
    payload.update(overrides)
    return RunManifest.model_validate(payload)


def target(compute_profile: str = PROMOTED_PROFILE) -> ExecutionTarget:
    return resolve_execution_target(
        compute_profile=compute_profile,
        catalog=workload_catalog(),
        targets=execution_targets(),
        account_id=ACCOUNT_ID,
    )


def admit_manifest(
    *,
    run_manifest: RunManifest | None = None,
    catalog: WorkloadCatalog | None = None,
    targets: ExecutionTargetCatalog | None = None,
) -> AdmissionOutcome:
    payload = (run_manifest if run_manifest is not None else manifest()).model_dump(mode="json")
    return admit(
        manifest_payload=payload,
        approved_manifest_sha256=sha256_digest(RunManifest.model_validate(payload)),
        run_id=RUN_ID,
        submitter=MEMBER,
        approver=LEAD,
        approving_environment=ApprovalEnvironment.LEAD,
        workflow_run=GitHubWorkflowRunReference(
            run_repository="edu-llm/platform",
            workflow_repository="edu-llm/platform",
            workflow_path=".github/workflows/submit-run.yml",
            workflow_ref="refs/heads/main",
            run_id=1704,
            run_attempt=1,
        ),
        policy=load_yaml(CONFIG_DIR / "policy.yaml", ApprovalPolicy),
        inventory=load_yaml(CONFIG_DIR / "organization.yaml", OrganizationInventory),
        catalog=catalog if catalog is not None else workload_catalog(),
        execution_targets=targets if targets is not None else execution_targets(),
        account_id=ACCOUNT_ID,
        dataset_registry=load_yaml(CONFIG_DIR / "datasets.yaml", DatasetRegistry),
        image_scan_registry=load_yaml(
            CONFIG_DIR / "image-exceptions.yaml", ImageScanExceptionRegistry
        ),
        image_scan_summary=ImageScanSummary(
            schema_version=1,
            status=ImageScanStatus.COMPLETE,
            scanned_at=datetime(2026, 7, 26, 22, 5, 49, tzinfo=UTC),
        ),
        recorded_at=RECORDED_AT,
    )


# ---------------------------------------------------------------------------------------
# Resolving a target
# ---------------------------------------------------------------------------------------


def test_the_promoted_profile_resolves_to_the_deployed_queue_and_job_definition() -> None:
    resolved = target()
    binding = execution_targets().binding_for(PROMOTED_PROFILE)

    assert binding is not None
    assert resolved.job_queue_arn.endswith(f":job-queue/{binding.job_queue}")
    assert resolved.job_definition_arn.endswith(f":job-definition/{binding.job_definition}")
    assert resolved.region == binding.region
    assert ACCOUNT_ID in resolved.job_queue_arn


def test_a_priced_but_unprovisioned_profile_has_nowhere_to_go() -> None:
    """Mutation: return a target anyway.

    Removing the ``resolve_compute_profile_for_execution`` call would do exactly that, and
    nothing else in the suite would notice: the ARNs would be well formed and would name a
    queue no compute environment backs, so the failure would arrive as a job sitting in
    RUNNABLE forever rather than as a refusal.
    """
    with pytest.raises(UnprovisionedComputeProfileError):
        target(UNPROVISIONED_PROFILE)


def test_a_profile_the_catalog_has_never_heard_of_is_a_different_refusal() -> None:
    """The three failures are separate because two are answerable and one is a deployment.

    Mutation: collapse them into one error type. A submitter told "ask for something else"
    when the real problem is that somebody flipped a flag without deploying anything would
    go looking in the wrong place.
    """
    with pytest.raises(UnregisteredComputeProfileError):
        target(UNREGISTERED_PROFILE)


def test_a_provisioned_profile_with_no_target_is_a_contradiction_rather_than_a_refusal() -> None:
    """Two configuration files disagreeing about whether capacity exists.

    Mutation: fall back to any target in the file, which would send a run to a queue nobody
    said backs its profile.
    """
    catalog = workload_catalog()
    promoted = tuple(
        profile.model_copy(update={"provisioned": True})
        if profile.name == UNPROVISIONED_PROFILE
        else profile
        for profile in catalog.compute_profiles
    )

    with pytest.raises(UnbackedComputeProfileError):
        resolve_execution_target(
            compute_profile=UNPROVISIONED_PROFILE,
            catalog=catalog.model_copy(update={"compute_profiles": promoted}),
            targets=execution_targets(),
            account_id=ACCOUNT_ID,
        )


# ---------------------------------------------------------------------------------------
# The seam between the two configuration files
# ---------------------------------------------------------------------------------------


def test_every_provisioned_profile_is_backed_and_every_target_names_a_provisioned_one() -> None:
    """Seam test 3, read from both files rather than either.

    Mutation: flip a third profile to ``provisioned: true`` without adding a target. That
    is the way a promotion quietly claims capacity that does not exist, and it is invisible
    to any test that reads one file.

    THIS TEST CAUGHT WHAT IT WAS WRITTEN FOR AND THEN HAD TO BE EDITED, which is the
    intended sequence and not a weakening. It said ``provisioned == {PROMOTED_PROFILE}``
    and its docstring named "Phase 4's promotion" as the mutation. Phase 4 promoted
    ``gpu-1xa10g``, this failed, and a person checked that the compute environment, the
    queue, the job definition and the two roles all existed before changing the number.

    The equality above it is the part that must never be relaxed into a subset. Provisioned
    without a target is a manifest accepted and then refused at resolution; a target for a
    profile nobody promoted is a queue the catalog will not route to, which is the same
    disagreement read from the other side.
    """
    catalog = workload_catalog()
    targets = execution_targets()
    provisioned = {profile.name for profile in catalog.compute_profiles if profile.provisioned}

    assert provisioned == set(targets.backed_profiles)
    assert provisioned == set(PROMOTED_PROFILES), (
        "a profile arriving here without its own compute environment is the thing this "
        "seam exists to catch; promoting one is a deliberate edit in both files and in "
        "this list, after the infrastructure it names has been deployed"
    )


def test_every_target_names_infrastructure_this_project_owns() -> None:
    """Mutation: point a target at another team's queue in the shared account.

    An ARN pattern alone would allow it, which is why the catalog holds names under this
    project's prefix and assembles the ARNs itself.
    """
    for binding in execution_targets().targets:
        assert binding.job_queue.startswith("sbsandbox-intern-edullm-")
        assert binding.job_definition.startswith("sbsandbox-intern-edullm-")
        assert binding.execution_role != binding.workload_role


# ---------------------------------------------------------------------------------------
# The submit request
# ---------------------------------------------------------------------------------------


def request_for(**overrides: Any) -> Mapping[str, Any]:
    run_manifest = manifest(**overrides)
    return batch_submit_request(manifest=run_manifest, target=target(), run_id=RUN_ID)


@pytest.mark.parametrize(
    ("runtime_hours", "expected_seconds"),
    [("1", 3600), ("2", 7200), ("13", 46800), ("0.5", 1800)],
)
def test_the_timeout_sent_to_batch_is_the_manifest_runtime_in_seconds(
    runtime_hours: str,
    expected_seconds: int,
) -> None:
    """Mutation: hardcode a constant, or drop the Timeout block.

    Dropping it is how the master plan's "mandatory timeout terminates a runaway job" check
    dies quietly -- nothing else fails, and the first anybody hears of it is a job that ran
    until somebody noticed. Hardcoding passes any single-runtime fixture, which is why this
    is parametrized over four.
    """
    request = request_for(maximum_runtime_hours=runtime_hours)

    assert request["Timeout"] == {"AttemptDurationSeconds": expected_seconds}
    assert expected_seconds == int(Decimal(runtime_hours) * 3600)


def test_every_submit_carries_a_timeout_including_the_shortest_manifest() -> None:
    """Mutation: make the Timeout conditional.

    A conditional block passes every fixture that sets a runtime, which is all of them, and
    omits it for the one that does not. There is no such manifest -- the contract requires
    the field -- so the assertion is that the function has no branch, checked at the one
    place a branch would plausibly be added: a runtime below Batch's own floor.
    """
    request = request_for(maximum_runtime_hours="0.001")

    assert request["Timeout"] == {
        "AttemptDurationSeconds": MINIMUM_ATTEMPT_DURATION_SECONDS
    }, "3.6 seconds is a legal manifest and Batch refuses anything under sixty"


def test_the_runtime_bound_is_rounded_down_rather_than_up() -> None:
    """A job permitted to run longer than the figure on the approval outran it.

    Mutation: round up, which is one character and hands every run a free second.
    """
    assert attempt_duration_seconds(manifest(maximum_runtime_hours="1.00001")) == 3600


@pytest.mark.parametrize("attempts", [1, 2, 3])
def test_the_retry_strategy_is_the_manifest_attempt_count(attempts: int) -> None:
    """Mutation: hardcode 1, which passes every single-attempt fixture.

    Every manifest fixture in this repository asks for one attempt, so a hardcoded 1 is
    invisible everywhere else. Retries above one require a checkpoint contract, which is
    why these manifests carry one.
    """
    checkpoint = (
        None
        if attempts == 1
        else {
            "interval_minutes": 30,
            "destination_prefix": "s3://sbsandbox-intern-edullm-checkpoints/runs/",
            "resume_required": True,
        }
    )
    request = request_for(maximum_attempts=attempts, checkpoint=checkpoint)

    assert request["RetryStrategy"] == {"Attempts": attempts}


def test_a_single_container_submits_no_array_properties() -> None:
    """Mutation: always emit ArrayProperties.

    Batch rejects an array job of size one, so an unconditional block would fail every
    non-fan-out submission -- and a test asserting only "present when fanout is set" would
    not catch it, because that assertion is true of a block that is always present.
    """
    assert "ArrayProperties" not in request_for()


def test_a_fan_out_submits_its_size_and_nothing_else_changes() -> None:
    """Mutation: read the array size from anything but ``fanout.size``.

    ``max_parallel`` is the obvious wrong field and is bounded above by ``size``, so a
    fixture where the two agree would pass either way. Here they differ.
    """
    fanout = {"size": 4, "max_parallel": 2, "index_parameter": "SEED"}
    request = request_for(fanout=fanout)

    assert request["ArrayProperties"] == {"Size": 4}


def test_the_job_name_is_the_run_id_so_batch_is_a_third_join() -> None:
    """Mutation: mint a name inside AWS.

    Batch does not enforce unique job names, so this is not idempotency. It is
    join-ability: the run id is the S3 key, the Step Functions execution name and the Batch
    job name, and any two of the three disagreeing is visible.
    """
    request = request_for()

    assert request["JobName"] == RUN_ID
    assert request["Tags"]["edullm:run-id"] == RUN_ID


def test_the_container_override_carries_the_manifest_command_unaltered() -> None:
    """Mutation: reshape, quote or join the command.

    The command is what a reviewer approved by hashing the manifest, and anything done to
    it here runs something the approval did not cover.
    """
    command = ["python", "-m", "olmo_core.train", "--config", "a config with spaces"]
    request = request_for(command=command)

    assert request["ContainerOverrides"]["Command"] == command


def test_the_submitted_target_is_the_resolved_one_and_not_anything_from_the_manifest() -> None:
    """Mutation: read the queue from the manifest.

    A manifest that named a queue would be a manifest a submitter could point somewhere
    else, which is why the target is resolved from deployed configuration.
    """
    resolved = target()
    request = batch_submit_request(manifest=manifest(), target=resolved, run_id=RUN_ID)

    assert request["JobQueue"] == resolved.job_queue_arn
    assert request["JobDefinition"] == resolved.job_definition_arn


def test_the_container_environment_is_exactly_these_five_variables() -> None:
    """Mutation: add, drop or rename a variable the container reads.

    Nothing else in the repository pins this list, which was measured rather than assumed:
    ``EDULLM_OUTPUT_PREFIX`` was added to it and the entire suite stayed green. The seam
    test that holds the ASL and this function to the same key set cannot see inside
    ``ContainerOverrides``, because ``SubmitToBatch`` carries the request through whole
    with ``InputPath`` and names no field of it -- which is the right design and is exactly
    why this needs its own assertion.

    An exact set rather than a subset. A dropped variable is a container that reads an
    empty string for something it was told it would have, and a variable that arrives
    unannounced is one nothing downstream was written to expect.
    """
    request = batch_submit_request(manifest=manifest(), target=target(), run_id=RUN_ID)
    environment = request["ContainerOverrides"]["Environment"]

    assert [entry["Name"] for entry in environment] == [
        "EDULLM_RUN_ID",
        "EDULLM_TEAM",
        "EDULLM_DATASET_RELEASE",
        "EDULLM_COMMIT_SHA",
        "EDULLM_OUTPUT_PREFIX",
    ]


def test_the_prefix_the_container_is_told_is_the_one_the_shared_function_builds() -> None:
    """Mutation: assemble the prefix here from the run id and the team.

    Three places used to answer "where does a run write" and two of them agreed, which is
    why the answer now has one author. Rebuilding it here would restore the arrangement
    this test exists to prevent -- a literal that matches until somebody changes the other
    one.
    """
    subject = manifest()
    request = batch_submit_request(manifest=subject, target=target(), run_id=RUN_ID)
    told = next(
        entry["Value"]
        for entry in request["ContainerOverrides"]["Environment"]
        if entry["Name"] == "EDULLM_OUTPUT_PREFIX"
    )

    assert told == output_prefix(team=subject.team, run_id=RUN_ID)
    # And it is a location, not a bucket: a prefix that stopped at the bucket would let
    # every run write over every other one and would still satisfy the line above.
    assert told.endswith(f"/runs/{RUN_ID}/")
    assert f"/teams/{subject.team}/" in told


# ---------------------------------------------------------------------------------------
# What admission does with all of it
# ---------------------------------------------------------------------------------------


def test_an_accepted_submission_carries_the_target_it_resolved() -> None:
    outcome = admit_manifest()

    assert outcome.accepted is True
    assert outcome.execution is not None
    assert outcome.execution.compute_profile == PROMOTED_PROFILE


def test_an_unprovisioned_profile_is_a_refusal_rather_than_a_crash() -> None:
    """Mutation: let the resolution error propagate.

    A validator that raised would fail the execution and leave no decision record, so the
    submitter would learn that something broke rather than that the platform has no
    capacity for the profile they asked for -- and the lineage store would hold an intent
    with nothing beside it.
    """
    outcome = admit_manifest(
        run_manifest=manifest(
            compute_profile=UNPROVISIONED_PROFILE,
            workload_profile="olmo-core-train-smoke",
        )
    )

    assert outcome.accepted is False
    assert outcome.decision.reason is AdmissionReason.NO_EXECUTION_TARGET
    assert outcome.execution is None
    assert "unprovisioned_compute_profile" in outcome.decision.detail


def test_the_two_ways_of_having_nowhere_to_run_are_distinguishable_in_the_record() -> None:
    """One reason code, two causes, and the detail says which.

    Mutation: drop the reason code from the detail. "Ask for a different profile" and
    "somebody flipped a flag without deploying anything" need different people to act.
    """
    catalog = workload_catalog()
    promoted = tuple(
        profile.model_copy(update={"provisioned": True})
        if profile.name == UNPROVISIONED_PROFILE
        else profile
        for profile in catalog.compute_profiles
    )
    outcome = admit_manifest(
        run_manifest=manifest(
            compute_profile=UNPROVISIONED_PROFILE,
            workload_profile="olmo-core-train-smoke",
        ),
        catalog=catalog.model_copy(update={"compute_profiles": promoted}),
    )

    assert outcome.decision.reason is AdmissionReason.NO_EXECUTION_TARGET
    assert "unbacked_compute_profile" in outcome.decision.detail


def test_a_refusal_for_want_of_capacity_still_records_the_price_and_the_authorization() -> None:
    """The target is resolved last, so everything else about the submission is on record.

    Mutation: resolve the target before the decision is taken. The refusal would then carry
    no cost and no authorization, and a reader could not tell a submission that was fine
    apart from its profile from one nobody was allowed to make.
    """
    outcome = admit_manifest(
        run_manifest=manifest(
            compute_profile=UNPROVISIONED_PROFILE,
            workload_profile="olmo-core-train-smoke",
        )
    )
    decision = outcome.decision

    assert decision.cost is not None
    assert decision.authorization is not None
    assert decision.authorization.granted is True


def test_an_execution_target_must_name_two_different_roles() -> None:
    """Mutation: let one role do both.

    The execution role pulls the image and writes logs; the workload role is what the
    container runs as. One role doing both hands the workload the registry credentials.
    """
    binding = execution_targets().targets[0]
    collapsed = ExecutionTargetBinding(
        **{
            **binding.model_dump(mode="json"),
            "workload_role": binding.execution_role,
        }
    )

    with pytest.raises(ValueError, match="different roles"):
        resolve_execution_target(
            compute_profile=PROMOTED_PROFILE,
            catalog=workload_catalog(),
            targets=ExecutionTargetCatalog(schema_version=1, targets=(collapsed,)),
            account_id=ACCOUNT_ID,
        )
