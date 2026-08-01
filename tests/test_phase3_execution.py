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

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from infrastructure_support import INFRA_ROOT, load_template

from edullm_platform.admission import AdmissionOutcome, admit
from edullm_platform.canonical import sha256_digest
from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import AdmissionReason, ApprovalEnvironment
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.execution import (
    SANDBOX_RESOURCE_PREFIX,
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
from edullm_platform.contracts.repository_registry import RepositoryRegistry
from edullm_platform.contracts.results import output_prefix
from edullm_platform.contracts.workload import (
    UnprovisionedComputeProfileError,
    UnregisteredComputeProfileError,
    WorkloadCatalog,
)
from edullm_platform.execution import (
    CONTAINER_SHAPES,
    MAXIMUM_CONTAINER_OVERRIDES_BYTES,
    MINIMUM_ATTEMPT_DURATION_SECONDS,
    PUBLISHED_IMAGE_REPOSITORY,
    WANDB_ENTITY,
    ContainerOverridesTooLargeError,
    UnshapedComputeProfileError,
    attempt_duration_seconds,
    batch_register_job_definition_request,
    batch_submit_request,
    resolve_execution_target,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
MANIFEST_FIXTURES_DIR = PROJECT_ROOT / "fixtures" / "manifests"

#: Every template that registers a job definition, so the comparison below is against the
#: whole set of deployed shapes rather than against the CPU one -- which is the half of
#: this seam that would go green while the GPU definition was wrong.
COMPUTE_TEMPLATE_PATHS = (
    INFRA_ROOT / "batch-compute.yaml",
    INFRA_ROOT / "batch-compute-gpu.yaml",
)
STATE_MACHINE_TEMPLATE_PATH = INFRA_ROOT / "admission-state-machine.yaml"

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
        "workload_profile": "olmo-core-check-cpu",
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
        image_scan_findings=None,
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
        repositories=load_yaml(CONFIG_DIR / "repositories.yaml", RepositoryRegistry),
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
    return batch_submit_request(
        manifest=run_manifest,
        target=target(),
        run_id=RUN_ID,
        job_definition=target().job_definition_arn,
    )


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
    request = batch_submit_request(
        manifest=manifest(),
        target=resolved,
        run_id=RUN_ID,
        job_definition=resolved.job_definition_arn,
    )

    assert request["JobQueue"] == resolved.job_queue_arn
    assert request["JobDefinition"] == resolved.job_definition_arn


def test_the_container_environment_is_exactly_these_eight_variables() -> None:
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
    request = batch_submit_request(
        manifest=manifest(),
        target=target(),
        run_id=RUN_ID,
        job_definition=target().job_definition_arn,
    )
    environment = request["ContainerOverrides"]["Environment"]

    assert [entry["Name"] for entry in environment] == [
        "EDULLM_RUN_ID",
        "EDULLM_TEAM",
        "EDULLM_DATASET_RELEASE",
        "EDULLM_COMMIT_SHA",
        "EDULLM_OUTPUT_PREFIX",
        # The suffix is not a secret, and this is still worth its own variable: OLMo-core's
        # example defaults its save folder to /tmp, so a long run that took the default
        # trains for hours, writes checkpoints onto an instance that is about to disappear,
        # and exits zero. This is the one line a submitter copies to avoid that.
        "EDULLM_CHECKPOINT_DIR",
        "EDULLM_WANDB_PROJECT",
        # W&B's own names rather than EDULLM_ ones, because the wandb client reads these
        # itself and a prefixed copy would need the workload to forward it. WANDB_USERNAME
        # is absent here and only here: this manifest was submitted with no recorded W&B
        # account, and an empty attribution is worse than none.
        #
        # WANDB_PROJECT duplicates the prefixed spelling above deliberately. It is the one
        # that reaches the client without a workload forwarding it, and nothing in any
        # research repository forwards the prefixed one.
        "WANDB_PROJECT",
        "WANDB_ENTITY",
    ]


def test_the_wandb_project_comes_from_the_manifest_and_not_from_the_command() -> None:
    """Mutation: drop the variable and let the training command name its own project.

    The key in the container authenticates a shared platform-owned W&B account; it does not
    attribute. What a run is labelled with is this platform's assertion, derived from the
    admission record that was approved -- so a submitter who wrote a different project into
    their own argv would attribute their spend somewhere the decision record does not say,
    and nothing downstream would notice, because the write would succeed.

    This cannot force a container to use the value. What it removes is the need to supply
    one, which is the difference between a submitter choosing an attribution and a submitter
    overriding one. It is the same reasoning that has the state machine read the image scan
    itself rather than accept findings from a caller.
    """
    declared = manifest()
    request = batch_submit_request(
        manifest=declared,
        target=target(),
        run_id=RUN_ID,
        job_definition=target().job_definition_arn,
    )
    environment = {entry["Name"]: entry["Value"] for entry in request["ContainerOverrides"]["Environment"]}

    assert environment["EDULLM_WANDB_PROJECT"] == declared.wandb_project
    # Not assembled from anything the command carries, and not defaulted when the manifest
    # is silent -- the field is required on a manifest, so there is no silent case.
    assert declared.wandb_project not in " ".join(declared.command)


def test_the_declared_wandb_project_reaches_wandb_without_the_workload_forwarding_it() -> None:
    """Mutation: send only the prefixed name and rely on the training code to read it.

    That was the state this closes, and it made the form's required `wandb_project` box
    decorative. `EDULLM_WANDB_PROJECT` is not a name the wandb client knows, and a search of
    `OLMo-core`, `edullm-data` and `olmo-eval-full` finds nothing reading it -- so the project
    a run landed in was whatever its own training config said, and the value the approver read
    on the submission had no bearing on it.

    `WANDB_ENTITY` and `WANDB_RUN_GROUP` were already sent under W&B's own names for exactly
    this reason. The project was the one that was not, and the inconsistency was the bug.

    This does not take the choice away from a workload. wandb's `init` applies an explicit
    argument over the environment -- `if project is not None: init_settings.project = project`
    -- and OLMo-core's `WandBCallback` defaults `project` to `None`, so a run that names its
    own project still wins and a run that does not now lands where the submission said.
    """
    declared = manifest()
    request = batch_submit_request(
        manifest=declared,
        target=target(),
        run_id=RUN_ID,
        job_definition=target().job_definition_arn,
    )
    environment = {entry["Name"]: entry["Value"] for entry in request["ContainerOverrides"]["Environment"]}

    assert environment["WANDB_PROJECT"] == declared.wandb_project
    # Both spellings, because the prefixed one is the platform's own record of what it
    # asserted and a workload may prefer to read it deliberately.
    assert environment["EDULLM_WANDB_PROJECT"] == environment["WANDB_PROJECT"]


def test_a_run_carries_the_wandb_account_of_the_person_who_submitted_it() -> None:
    """Mutation: drop WANDB_USERNAME and let every run log as the platform.

    The container authenticates with a team service account, so without this every run in
    W&B is authored by the platform and the submitter recorded on the decision record is
    thrown away at the container boundary. W&B's own remedy is this variable.
    """
    request = batch_submit_request(
        manifest=manifest(),
        target=target(),
        run_id=RUN_ID,
        job_definition=target().job_definition_arn,
        wandb_username="liumaizi",
    )
    environment = {
        entry["Name"]: entry["Value"] for entry in request["ContainerOverrides"]["Environment"]
    }

    assert environment["WANDB_USERNAME"] == "liumaizi"


def test_a_submitter_with_no_wandb_account_sends_no_attribution_rather_than_an_empty_one() -> None:
    """THE VARIABLE IS ABSENT, NEVER EMPTY, AND THE DIFFERENCE IS NOT COSMETIC.

    W&B reads an empty ``WANDB_USERNAME`` as an attribution that failed rather than as one
    that was never attempted, and most of the roster has no recorded account. Sending an
    empty string on their behalf would turn an ordinary unattributed run into a run that
    looks like a broken attribution.
    """
    request = batch_submit_request(
        manifest=manifest(),
        target=target(),
        run_id=RUN_ID,
        job_definition=target().job_definition_arn,
        wandb_username=None,
    )

    assert "WANDB_USERNAME" not in [
        entry["Name"] for entry in request["ContainerOverrides"]["Environment"]
    ]


def test_every_run_names_the_entity_the_service_account_belongs_to() -> None:
    """Mutation: leave WANDB_ENTITY unset and rely on the service account's default.

    W&B's documented failure for a team service account with no entity is that runs land in
    the parent team's project anyway -- until they do not, at which point the run is
    somewhere nobody looks. Naming it is one variable and removes the question.
    """
    request = batch_submit_request(
        manifest=manifest(),
        target=target(),
        run_id=RUN_ID,
        job_definition=target().job_definition_arn,
        wandb_username=None,
    )
    environment = {
        entry["Name"]: entry["Value"] for entry in request["ContainerOverrides"]["Environment"]
    }

    assert environment["WANDB_ENTITY"] == WANDB_ENTITY


def test_the_cpu_profile_can_reach_wandb_the_way_the_gpu_profile_can() -> None:
    """A PILOT RUN FOUND THIS. Mutation: take the secret back off the CPU shape.

    The submission form accepts ``wandb_project`` on every profile and the container is told
    it on every profile, but only the GPU shape carried the key -- so a CPU workload that
    tried to log died on ``No API key configured``, having been admitted, approved by a lead
    and given an instance first. The omission was never a decision: W&B was wired up during
    the GPU training work and this shape was left behind.
    """
    cpu = CONTAINER_SHAPES["cpu-32vcpu"]
    gpu = CONTAINER_SHAPES["gpu-1xa10g"]

    assert dict(cpu.secrets) == dict(gpu.secrets)
    assert "WANDB_API_KEY" in dict(cpu.secrets)


def test_the_prefix_the_container_is_told_is_the_one_the_shared_function_builds() -> None:
    """Mutation: assemble the prefix here from the run id and the team.

    Three places used to answer "where does a run write" and two of them agreed, which is
    why the answer now has one author. Rebuilding it here would restore the arrangement
    this test exists to prevent -- a literal that matches until somebody changes the other
    one.
    """
    subject = manifest()
    request = batch_submit_request(
        manifest=subject,
        target=target(),
        run_id=RUN_ID,
        job_definition=target().job_definition_arn,
    )
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
# The job definition a run registers for itself
# ---------------------------------------------------------------------------------------


def registration_for(
    compute_profile: str = PROMOTED_PROFILE, **overrides: Any
) -> dict[str, Any]:
    return batch_register_job_definition_request(
        manifest=manifest(compute_profile=compute_profile, **overrides),
        target=target(compute_profile),
        run_id=RUN_ID,
    )


def deployed_job_definition(name: str) -> dict[str, Any]:
    """The job definition the compute templates register under this name.

    Read from ``infra/`` rather than restated here, because the whole claim a registered
    definition makes is that it is the deployed one with the image swapped -- and a
    restated copy of the deployed shape would be a third statement of it that agrees with
    the template only until somebody edits one of them.
    """
    matching = [
        resource["Properties"]
        for path in COMPUTE_TEMPLATE_PATHS
        for resource in load_template(path)["Resources"].values()
        if isinstance(resource, dict)
        and resource.get("Type") == "AWS::Batch::JobDefinition"
        and resource["Properties"]["JobDefinitionName"] == name
    ]
    assert len(matching) == 1, f"expected exactly one deployed job definition named {name}"
    return matching[0]


def deployed_container_properties(resolved: ExecutionTarget) -> dict[str, Any]:
    name = resolved.job_definition_arn.rsplit("/", maxsplit=1)[1]
    container = deployed_job_definition(name)["ContainerProperties"]
    assert isinstance(container, dict)
    return container


def test_the_registered_definition_runs_the_image_the_manifest_declares() -> None:
    """Mutation: register the digest the template pins instead of the declared one.

    THIS IS THE WHOLE REASON THE FUNCTION EXISTS. Batch has no submit-time image override,
    so a submission whose job definition is the deployed one runs whatever that definition
    pins -- while the digest the submitter declared is validated, gates admission through
    the ECR scan, and is written immutably into the S3 lineage record. The two coincide
    today only because ``config/image-exceptions.yaml`` happens to hold exactly the two
    digests the templates pin, which makes the lineage record's image provenance true by
    convention rather than by mechanism, and every other guarantee this platform makes is
    read back off that record.

    The digest is asserted as the end of the reference rather than as the whole of it, so
    that publishing a new image is a manifest edit and not a test failure.
    """
    declared = manifest()
    request = batch_register_job_definition_request(
        manifest=declared, target=target(), run_id=RUN_ID
    )
    image = request["ContainerProperties"]["Image"]

    assert image.endswith(f"@{declared.image_digest}")
    # A reference may carry a tag and a digest at once and only the digest decides which
    # bytes run, so a tag here would be decoration that reads as the source of truth.
    assert ":" not in image.split("@", maxsplit=1)[0].rsplit("/", maxsplit=1)[1]


def test_the_registered_definition_carries_both_of_the_containers_identities() -> None:
    """Mutation: drop either role, or point both at the same one.

    A container has two identities and they are fixed at registration rather than at
    submission: the execution role pulls the image and opens the log stream, and the
    workload role is what the container's own process runs as. Omitting either gives the
    job the account's defaults instead of the reviewed roles, and the run then fails on its
    first S3 write in a way that names the bucket rather than the role -- which sends
    whoever reads it to go and look at bucket policy.
    """
    resolved = target()
    container = registration_for()["ContainerProperties"]

    assert container["ExecutionRoleArn"] == resolved.execution_role_arn
    assert container["JobRoleArn"] == resolved.workload_role_arn
    assert container["ExecutionRoleArn"] != container["JobRoleArn"]


def test_the_registered_definition_is_named_for_the_run_that_asked_for_it() -> None:
    """Mutation: mint a name inside AWS, or reuse the deployed definition's name.

    The run id is the S3 key, the Step Functions execution name and the Batch job name, and
    this makes it the job definition name too -- so a definition registered for a run that
    then vanished is findable the way everything else in this platform is. Reusing the
    deployed name would be worse than a random one: it registers a new revision of the
    shared definition, so one run's image becomes the default every hand-submitted job
    afterwards picks up.
    """
    request = registration_for()
    name = request["JobDefinitionName"]

    assert RUN_ID in name
    assert name.startswith(SANDBOX_RESOURCE_PREFIX)
    # Batch's own bound on the field, and what it accepts in it.
    assert len(name) <= 128
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name) is not None


def test_a_submission_goes_to_the_definition_just_registered_and_not_the_static_one() -> None:
    """Mutation: keep reading ``target.job_definition_arn``.

    That is today's behaviour and it is what makes the declared digest decorative: the run
    is admitted on one image and executed on another, and nothing anywhere reports a
    disagreement. The submitted ARN has to be the revision the registration returned, which
    is why the argument is required rather than defaulted -- a default would let this
    mutation survive as an omission at a call site rather than a change to this function.
    """
    resolved = target()
    registered = (
        f"arn:aws:batch:{resolved.region}:{ACCOUNT_ID}:job-definition/"
        f"{registration_for()['JobDefinitionName']}:1"
    )
    request = batch_submit_request(
        manifest=manifest(),
        target=resolved,
        run_id=RUN_ID,
        job_definition=registered,
    )

    assert request["JobDefinition"] == registered
    assert request["JobDefinition"] != resolved.job_definition_arn
    # The queue is still the resolved one. Only the definition moves.
    assert request["JobQueue"] == resolved.job_queue_arn


@pytest.mark.parametrize("compute_profile", PROMOTED_PROFILES)
def test_the_registered_definition_asks_for_the_shape_its_profile_was_priced_at(
    compute_profile: str,
) -> None:
    """Mutation: register one shape for every profile.

    A registered definition that contradicts the compute profile it was resolved for is the
    expensive silent failure of this change. Without a GPU entry in the resource
    requirements ECS does not select the NVIDIA runtime for the task, so the container sees
    no device and trains on the CPU at GPU prices, reporting nothing wrong -- and a CPU
    shape asking for 32 vCPU on a g5.xlarge is a job that stays in RUNNABLE rather than one
    that errors.

    Compared against the deployed definition rather than against numbers written out here,
    because a restated expectation is regenerated from whatever the code does.
    """
    resolved = target(compute_profile)
    container = registration_for(compute_profile)["ContainerProperties"]

    assert container["ResourceRequirements"] == deployed_container_properties(resolved)[
        "ResourceRequirements"
    ]
    wants_a_gpu = any(
        entry["Type"] == "GPU" and int(entry["Value"]) > 0
        for entry in container["ResourceRequirements"]
    )
    assert wants_a_gpu == (compute_profile.startswith("gpu-"))


@pytest.mark.parametrize("compute_profile", PROMOTED_PROFILES)
def test_the_registered_definition_is_the_deployed_one_with_the_image_swapped(
    compute_profile: str,
) -> None:
    """Mutation: omit a field the deployed definition carries.

    This is the failure the whole design guards against, and it is why the request is built
    in tested Python rather than reconstructed by a ``Parameters`` block in a template
    nobody unit-tests. Omission is silent on every axis that matters here: a GPU definition
    with no ``Secrets`` block starts and cannot reach W&B, one with ECS's default 64 MiB of
    ``/dev/shm`` dies partway into training with a DataLoader bus error that names neither
    shared memory nor the setting that fixes it, and one that declares no ``Command`` has no
    key for the submission's own command override to replace.

    The key set is compared rather than the values, because the values that differ are
    exactly the ones this function is for -- the image -- and the ones a template writes as
    ``Fn::Sub`` expressions over pseudo-parameters that no committed file can spell.
    """
    resolved = target(compute_profile)
    deployed = deployed_job_definition(resolved.job_definition_arn.rsplit("/", maxsplit=1)[1])
    request = registration_for(compute_profile)
    container = request["ContainerProperties"]

    assert set(container) == set(deployed["ContainerProperties"])
    assert set(request) == set(deployed)
    assert container["Command"] == deployed["ContainerProperties"]["Command"]
    assert container["Environment"] == deployed["ContainerProperties"]["Environment"]
    assert container["Privileged"] is False
    assert request["Type"] == "container"
    assert request["PlatformCapabilities"] == ["EC2"]
    # Both floors, which every real submission overrides. They are here so a job submitted
    # by hand against this definition during an incident still cannot run unbounded.
    assert request["RetryStrategy"] == deployed["RetryStrategy"]
    assert request["Timeout"] == deployed["Timeout"]
    assert request["PropagateTags"] == deployed["PropagateTags"]
    options = container["LogConfiguration"]["Options"]
    assert container["LogConfiguration"]["LogDriver"] == "awslogs"
    assert options["awslogs-group"] == resolved.log_group
    assert options["awslogs-region"] == resolved.region
    deployed_options = deployed["ContainerProperties"]["LogConfiguration"]["Options"]
    assert options["awslogs-stream-prefix"] == deployed_options["awslogs-stream-prefix"]


def test_the_image_is_pulled_from_the_repository_whose_scan_admission_read() -> None:
    """Reads the state machine and the Python. Mutation: name a different repository here.

    The state machine's ``ReadImageScan`` state asks ECR for the findings on the declared
    digest in one named repository, and admission refuses a digest whose findings nobody
    reviewed. If this function pulled the same digest from a different repository, the
    image that ran would be one whose scan was never read -- an admission gate passed
    against a different image, which is the failure this whole change exists to close,
    reintroduced one field along.

    A digest identifies bytes and a repository is where those bytes are indexed, so the two
    references are only the same image because the repository is the same. The agreement
    used to be between a Python constant and a literal in an ASL template with nothing
    connecting them, which is why it was asserted rather than assumed. Phase 6 connected one
    end: the scan is read from ``$.ecr_repository``, which the submitting workflow fills out
    of the registry. So the comparison moves to the registry, which is now the thing both
    sides have to agree with, and it is made for every submittable repository rather than
    for the one this constant happens to name.

    ``PUBLISHED_IMAGE_REPOSITORY`` is the end still unconnected. It is correct only while one
    repository is submittable, and the test below this one is the coverage check that fires
    when a second becomes so.
    """
    definition = json.loads(
        load_template(STATE_MACHINE_TEMPLATE_PATH)["Resources"]["AdmissionStateMachine"][
            "Properties"
        ]["DefinitionString"]["Fn::Sub"]
    )
    parameters = definition["States"]["ReadImageScan"]["Parameters"]
    image = registration_for()["ContainerProperties"]["Image"]

    # The scan is read from whatever the request carries, and what the request carries is
    # the registered name -- asserted against the submitting workflow in
    # tests/test_phase6_infrastructure.py and against the registry in the handler. So the
    # repository this pulls from has to be a registered one for the seam to hold.
    assert parameters["RepositoryName.$"] == "$.ecr_repository"
    assert PUBLISHED_IMAGE_REPOSITORY in set(submittable_ecr_repositories().values())
    assert image.split("@", maxsplit=1)[0].endswith(f"/{PUBLISHED_IMAGE_REPOSITORY}")


def submittable_ecr_repositories() -> dict[str, str]:
    """Every repository a submission can actually name, mapped to where its images live.

    Submittable is the intersection of two files, because it takes both to reach Batch.
    ``config/repositories.yaml`` is what gives a repository an ECR repository at all, and
    ``config/workload-catalog.yaml`` is what gives it a profile a manifest can name -- a
    submission naming a repository with no workload profile cannot be compiled. Two
    repositories are registered and one of them has a profile, and that one-member set is
    the only reason the repository name hardcoded in four places has never been wrong.

    Duplicated verbatim in ``tests/test_phase3_infrastructure.py`` rather than lifted into
    ``tests/infrastructure_support.py``. Both modules already load both of these files, and
    three lines repeated once is a smaller thing to keep true than a shared support module
    that neither of them owns.
    """
    registry = load_yaml(CONFIG_DIR / "repositories.yaml", RepositoryRegistry)
    named = {workload.repository for workload in workload_catalog().workloads}
    submittable = {
        entry.repository: entry.ecr_repository
        for entry in registry.repositories
        if entry.repository in named
    }
    assert submittable, "no registered repository has a workload profile to be named by"
    return submittable


def test_admission_can_read_a_scan_for_every_submittable_repository() -> None:
    """Reads the state machine against the registry. Mutation: pin any submittable
    repository's ECR repository back as a literal ``RepositoryName``.

    THIS TEST ASKED FOR A FIX AND PHASE 6 MADE IT, so what it checks has changed shape and
    the history is worth keeping. It used to compare a literal against the set of
    repositories a submission can name, and it stayed green only because that set had one
    member. The literal is gone: ``RepositoryName.$`` reads ``$.ecr_repository``, which the
    submitting workflow fills from ``config/repositories.yaml`` and the validator re-derives
    and refuses on disagreement. Coverage is now structural rather than arithmetic, so the
    question this test asks is no longer "does the one name cover every repository" but
    "has a name been pinned again".

    Where it lands if nothing catches it, which is why the coverage half still matters. The
    ``resolve`` job reads the scan from the right repository, because
    ``tools/resolve_published_image.py`` takes ``ecr_repository`` out of the registry, so
    compile evaluates the image-scan gate against a real answer, passes, and a lead
    approves. Only then does this state ask the wrong repository, get
    ``ImageNotFoundException``, and fall into its ``States.ALL`` catch -- and
    ``image_scan_is_reviewed`` consults ``config/image-exceptions.yaml`` before it looks at
    the summary, so a digest with an exception is admitted anyway, after the approval and
    into the lineage record. An exception is the ordinary case rather than the exotic one:
    the registry is on BASIC scanning and both registrations pin the same base digest,
    whose four critical findings are what the two entries in that file exist to accept.
    """
    definition = json.loads(
        load_template(STATE_MACHINE_TEMPLATE_PATH)["Resources"]["AdmissionStateMachine"][
            "Properties"
        ]["DefinitionString"]["Fn::Sub"]
    )
    parameters = definition["States"]["ReadImageScan"]["Parameters"]
    pinned = sorted(
        f"{repository} (images in {ecr_repository})"
        for repository, ecr_repository in submittable_ecr_repositories().items()
        if parameters.get("RepositoryName") == ecr_repository
    )

    assert not pinned, (
        "ReadImageScan reads scan findings from "
        f"{parameters.get('RepositoryName')} and from nowhere else, so an approved "
        f"submission naming any repository other than {', '.join(pinned)} would be "
        "admitted against findings for an image that repository never published. "
        "infra/admission-state-machine.yaml, the ReadImageScan state's "
        "Parameters.RepositoryName, is what would have to change."
    )
    assert parameters["RepositoryName.$"] == "$.ecr_repository"


def test_a_run_can_pin_an_image_from_every_submittable_repository() -> None:
    """Reads the Python against the registry. Mutation: give a second registered repository
    a workload profile and leave this constant naming the first.

    The other half of the seam test above, asked as coverage rather than as consistency.
    That test proves this constant and the state machine name the same repository, which
    they would still do if that repository were the wrong one for the submission in hand;
    this proves the name is right for every submission that can be made.

    ``PUBLISHED_IMAGE_REPOSITORY`` is not a fact about the platform. It is a fact about the
    submission's source repository -- ``config/repositories.yaml`` maps each registration to
    an ``ecr_repository`` and the mapping is not derivable from the name -- so the constant
    is only correct while one repository is submittable. The digest a second repository's
    manifest declares does not exist under this name, so the reference composed here is a
    reference to nothing, and Batch validates no image at registration: the run is accepted,
    the definition registers, the job is submitted and an instance scales before anything
    notices.
    """
    unreachable = sorted(
        f"{repository} (images in {ecr_repository})"
        for repository, ecr_repository in submittable_ecr_repositories().items()
        if ecr_repository != PUBLISHED_IMAGE_REPOSITORY
    )

    assert not unreachable, (
        "the job definition an accepted run registers for itself composes its image "
        f"reference against {PUBLISHED_IMAGE_REPOSITORY}, so a submission naming "
        f"{', '.join(unreachable)} would pin a digest that repository does not hold. "
        "src/edullm_platform/execution.py, the PUBLISHED_IMAGE_REPOSITORY constant, is "
        "what would have to change."
    )


def test_a_profile_with_a_target_and_no_container_shape_is_refused_rather_than_guessed() -> None:
    """Mutation: fall back to some default shape.

    A third way for two files to disagree, alongside the two ``resolve_execution_target``
    already separates: a profile that has somewhere to run and no statement of what its
    container asks for. Guessing would register a definition whose shape nobody chose,
    which on the GPU side is a job that trains on the CPU at GPU prices. The refusal names
    the profile, because the fix is an edit in a specific place.
    """
    unshaped = target().model_copy(update={"compute_profile": "cpu-1024vcpu"})

    with pytest.raises(UnshapedComputeProfileError, match="cpu-1024vcpu"):
        batch_register_job_definition_request(
            manifest=manifest(), target=unshaped, run_id=RUN_ID
        )


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
            workload_profile="olmo-core-train-4gpu",
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
            workload_profile="olmo-core-train-4gpu",
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
            workload_profile="olmo-core-train-4gpu",
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


# ---------------------------------------------------------------------------------------
# The service limit the submission path has to know about
# ---------------------------------------------------------------------------------------


def test_a_submission_batch_would_reject_for_size_is_refused_before_it_is_sent() -> None:
    """Mutation: drop the check and let Batch answer.

    IT DID, AND THE ANSWER ARRIVED TOO LATE TO BE USEFUL. A 9,121-byte training program was
    compiled, validated locally, dispatched, approved at the environment gate, admitted by
    the state machine, and submitted -- and Batch refused it with "Container Overrides
    length must be at most 8192". Everything before Batch is cheap and reversible; the
    approval is a person's attention, and spending it on a submission that cannot be
    accepted is the one thing this path should never do.

    The message names the limit, the measured size and the command's share of it, because
    the AWS message names none of those and reads like a problem with the job definition.
    """
    with pytest.raises(ContainerOverridesTooLargeError, match="8192"):
        request_for(command=["python", "-c", "x" * 9000])


def test_the_budget_counts_the_environment_and_not_only_the_command() -> None:
    """Mutation: measure the command's length instead of the serialized override.

    The environment, the JSON punctuation and the key names are all inside the same limit,
    and this platform adds six variables to every submission -- one of which is a full S3
    URI containing the run id. A command comfortably under 8,192 can still overrun, and a
    check that only weighed the command would pass it.
    """
    just_under_on_its_own = "y" * (MAXIMUM_CONTAINER_OVERRIDES_BYTES - 120)

    with pytest.raises(ContainerOverridesTooLargeError):
        request_for(command=["python", "-c", just_under_on_its_own])


def test_an_ordinary_submission_is_nowhere_near_the_limit() -> None:
    """Mutation: set the limit to something the normal path trips.

    A guard that fired on ordinary work would be routed around within a week, so the
    headroom is asserted rather than assumed.
    """
    request = request_for(command=["python", "-c", "print('hello')"])
    serialized = len(
        json.dumps(request["ContainerOverrides"], separators=(",", ":")).encode("utf-8")
    )

    assert serialized < MAXIMUM_CONTAINER_OVERRIDES_BYTES // 4
