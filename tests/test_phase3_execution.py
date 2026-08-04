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
import os
import re
import subprocess
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
from edullm_platform.evidence import INSTANCE_EVIDENCE
from edullm_platform.execution import (
    CONTAINER_SHAPES,
    FANOUT_INDEX_VARIABLE,
    MAXIMUM_CONTAINER_OVERRIDES_BYTES,
    MINIMUM_ATTEMPT_DURATION_SECONDS,
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
    INFRA_ROOT / "batch-compute-gpu-shapes.yaml",
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
#:
#: gpu-1xh100 AND gpu-8xh100 WERE HERE AND CAME OFF ON 2026-08-04. Their compute
#: environments, queues and job definitions are all still deployed and healthy; what they
#: have never had is an instance. EC2 refused 6,815 p5.48xlarge launches and 2,530
#: p5.4xlarge launches with InsufficientInstanceCapacity, and the billing record shows zero
#: instance-hours of either type since the account existed, so a submission routed to either
#: queue waits in RUNNABLE with nothing to read. That is a demotion rather than a teardown,
#: and it is the first one this constant has recorded.
PROMOTED_PROFILES = (
    PROMOTED_PROFILE,
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
)
#: The profile the refusal tests below ask for. It has to be a profile the catalog prices and
#: does not provision, and it moved from gpu-4xa10g to gpu-1xl40s and now to here as each of
#: those was promoted. It is the weaker choice the note here used to explain avoiding, because
#: it is unprovisioned for a second reason as well: it is a different service with no Batch
#: queue to give it, so a reader could think these tests turn on that rather than on the flag.
#: They do not. What they need is a priced profile whose provisioned flag is false, and this
#: file flips that flag itself where a test needs it true.
#:
#: THE NOTE HERE SAID THIS CONSTANT HAD "run out of room" WITH ONE CANDIDATE LEFT. It has
#: three as of 2026-08-04: gpu-1xh100 and gpu-8xh100 were demoted after EC2 turned out never
#: to have sold this account a p5. The sagemaker profile stays named here anyway, because the
#: two H100 shapes are expected back and a constant that follows a capacity shortage around
#: would move again when it lifts. What the paragraph below anticipated is still the answer if
#: this one is ever promoted or removed.
#:
#: Promoting the sagemaker profile or removing it leaves nothing permanent here to name, and
#: the answer then is a profile the catalog does not ship, built in the test rather than read
#: from config/workload-catalog.yaml.
UNPROVISIONED_PROFILE = "gpu-1xa10g-sagemaker"
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


#: Arguments of ``batch_submit_request`` itself rather than of the manifest it carries.
#: Split out because this helper spreads everything else onto ``manifest()``, and a caller
#: passing one of these would otherwise get an unhelpful pydantic error about a manifest
#: field that does not exist.
_SUBMIT_REQUEST_ARGUMENTS = frozenset({"wandb_username", "experiment", "submitter"})


def request_for(**overrides: Any) -> Mapping[str, Any]:
    submit = {key: overrides.pop(key) for key in list(overrides) if key in _SUBMIT_REQUEST_ARGUMENTS}
    run_manifest = manifest(**overrides)
    return batch_submit_request(
        manifest=run_manifest,
        target=target(),
        run_id=RUN_ID,
        job_definition=target().job_definition_arn,
        **submit,
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

    assert request["RetryStrategy"]["Attempts"] == attempts


@pytest.mark.parametrize("attempts", [1, 2, 3])
def test_a_retry_fires_on_a_lost_host_and_on_nothing_else(attempts: int) -> None:
    """Mutation: send Attempts alone, the way this did until it cost two instance starts.

    A twelve-hour submission carried a config override OLMo-core refuses and the program
    died in the first few seconds. With no exit rules, Batch pulled a three-gigabyte image
    onto a second GPU instance and ran the identical command into the identical error.
    Nothing about the first failure could have been different the second time.

    The order is the policy, because Batch takes the first rule that matches: a lost host
    retries, an OOM does not because it will not fit on the identical instance either, and
    the catch-all sits last and covers every exit code including the 1 a traceback
    produces. Asserted as an ordered list rather than a set for exactly that reason -- the
    same three rules with the catch-all first would retry nothing at all, and would look
    right to a reader comparing membership.

    Sent even for a single attempt. It changes no behaviour there, and a rule set that
    appeared only above one attempt would be one nothing exercised until the first
    retryable run.
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

    assert request["RetryStrategy"]["EvaluateOnExit"] == [
        {"OnStatusReason": "Host EC2*", "Action": "RETRY"},
        {"OnReason": "OutOfMemoryError*", "Action": "EXIT"},
        {"OnExitCode": "*", "Action": "EXIT"},
    ]


def test_no_exit_rule_begins_with_an_asterisk_because_batch_refuses_one() -> None:
    """Mutation: write `*OutOfMemoryError*`, which is the natural way to spell it.

    These patterns glob in one direction. The API reference says a pattern "can optionally
    end with an asterisk (*) so that only the start of the string needs to be an exact
    match", and permits no leading one -- Batch answers a leading asterisk with a 400,
    `Evaluate on exit condition contains restricted characters.`

    That 400 lands on SubmitJob, which the state machine reaches only after the intent and
    decision records are written. So the failure mode is a run recorded as admitted whose
    job was refused, which is the same expensive shape as the key-casing mistake this rule
    set already made once. Both were submit-time errors invisible to every test that reads
    the request as data, which is why this one reads it as a pattern.
    """
    rules = request_for(maximum_attempts=1, checkpoint=None)["RetryStrategy"]["EvaluateOnExit"]

    for rule in rules:
        for field, pattern in rule.items():
            if field == "Action":
                continue
            assert not pattern.startswith("*") or pattern == "*", (
                f"{field}={pattern!r} begins with an asterisk, which Batch refuses with "
                "'Evaluate on exit condition contains restricted characters'"
            )


def test_every_key_this_request_sends_is_one_step_functions_will_accept() -> None:
    """THE MISTAKE THIS EXISTS FOR, WHICH BROKE EVERY SUBMISSION FOR TWENTY MINUTES.

    ``EvaluateOnExit`` first shipped with the Batch API's documented spelling --
    ``onStatusReason``, ``onReason``, ``onExitCode``, ``action`` -- which is what anyone
    checking the AWS reference will write. Nothing here makes that request. Step Functions
    does, through its ``aws-sdk:batch:submitJob`` integration, and it requires PascalCase
    and refuses the documented spelling: ``The field "onStatusReason" is not supported by
    Step Functions.``

    The cost is what makes this worth a test rather than a comment. It fails at
    ``SubmitToBatch``, which runs *after* WriteIntent and WriteDecision, so the lineage
    records say the run was admitted and no job ever reaches a queue -- an accepted run
    that does not exist. Every submission, not only retryable ones, because the block is
    sent unconditionally.

    Checked structurally rather than against a fixed list, so a key added later is covered
    by the rule instead of needing to be remembered.
    """
    request = request_for(maximum_attempts=1, checkpoint=None)

    def camel_cased(node: object, path: str = "") -> list[str]:
        offenders: list[str] = []
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(key, str) and key[:1].islower():
                    offenders.append(f"{path}.{key}" if path else key)
                offenders.extend(camel_cased(value, f"{path}.{key}" if path else key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                offenders.extend(camel_cased(value, f"{path}[{index}]"))
        return offenders

    # Tags are the one exception and are the caller's own vocabulary rather than an API's:
    # `edullm:run-id` and its siblings are values Batch stores verbatim and never parses.
    inspected = {key: value for key, value in request.items() if key != "Tags"}

    assert camel_cased(inspected) == [], (
        "Step Functions' aws-sdk integration requires PascalCase for every field it sends "
        "to Batch, and refuses a lower-case key with a States.Runtime error after the "
        "lineage records have already been written"
    )


def test_a_single_container_submits_no_array_properties() -> None:
    """Mutation: always emit ArrayProperties.

    Batch rejects an array job of size one, so an unconditional block would fail every
    non-fan-out submission -- and a test asserting only "present when fanout is set" would
    not catch it, because that assertion is true of a block that is always present.
    """
    assert "ArrayProperties" not in request_for()


def test_a_fan_out_submits_its_size() -> None:
    """Mutation: read the array size from anything but ``fanout.size``.

    ``max_parallel`` used to sit beside it as the obvious wrong field to read, bounded
    above by ``size`` so that a fixture where the two agreed would pass either way. It was
    removed, because Batch accepts no concurrency cap and a recorded one is a control that
    does nothing. Size is the only number ``ArrayProperties`` has ever taken.
    """
    request = request_for(fanout={"size": 4, "index_parameter": "SEED"})

    assert request["ArrayProperties"] == {"Size": 4}


#: A fan-out block of the shape that lost data, kept as one value so the tests below argue
#: about one submission rather than about four unrelated ones. Forty cells over a curriculum
#: matrix is a real submitted run and not a round number chosen for a test.
CURRICULUM_MATRIX = {"size": 40, "index_parameter": "curriculum_shard"}


def container_environment(request: Mapping[str, Any]) -> dict[str, str]:
    return {entry["Name"]: entry["Value"] for entry in request["ContainerOverrides"]["Environment"]}


def start_the_container_command(request: Mapping[str, Any], *, array_index: str) -> str:
    """Run the submitted command the way the container will, and return what it printed.

    THE ONLY ASSERTION IN THIS FILE THAT EXECUTES ANYTHING, AND IT EARNS THE EXCEPTION.
    Every other test here compares a structure against the manifest that produced it, which
    is the right shape for a pure function. This one is about whether a string is expanded
    by a shell, and there is no way to assert that from the structure -- a request carrying
    ``cell-$AWS_BATCH_JOB_ARRAY_INDEX`` somewhere is either exactly right or exactly the
    defect, depending on whether the thing holding it is shell text or an environment
    value, and both look identical to a reader comparing dictionaries.

    The environment is the request's own, plus the index Batch sets per child. PATH is
    carried through because the wrapper resolves ``bash`` and the submitted program against
    it, and passing an empty environment would fail for a reason that has nothing to do
    with what is being tested.
    """
    environment = container_environment(request)
    environment[FANOUT_INDEX_VARIABLE] = array_index
    started = subprocess.run(
        list(request["ContainerOverrides"]["Command"]),
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": os.environ.get("PATH", ""), **environment},
    )
    return started.stdout.strip()


def test_each_cell_of_a_fan_out_writes_under_a_prefix_of_its_own() -> None:
    """THE ONE THAT MATTERS. Mutation: leave the prefix alone and fan the same one out.

    This is the defect the whole change exists for. Every cell of an array job is handed
    one environment block, so forty cells shared one output prefix and one checkpoint
    directory, forty processes wrote into it, the last writer won and nothing said so. A
    forty cell curriculum matrix of exactly this shape had already been submitted.

    Two indices rather than one, because a derivation that ignored the index entirely would
    satisfy any single-cell assertion.
    """
    request = request_for(fanout=CURRICULUM_MATRIX, command=["printenv", "EDULLM_OUTPUT_PREFIX"])
    run_prefix = output_prefix(team="memory-split", run_id=RUN_ID)

    assert start_the_container_command(request, array_index="0") == f"{run_prefix}cell-0/"
    assert start_the_container_command(request, array_index="39") == f"{run_prefix}cell-39/"


def test_a_cell_that_reached_the_container_unexpanded_would_fail_this() -> None:
    """Mutation: set EDULLM_OUTPUT_PREFIX to a literal holding $AWS_BATCH_JOB_ARRAY_INDEX.

    THIS IS THE TRAP THE CHANGE HAD TO GET PAST AND IT PASSES REVIEW EASILY. A job
    definition's environment values are static strings and Batch does not shell-expand
    them, so a prefix written that way produces a container whose ``EDULLM_OUTPUT_PREFIX``
    is the name of the variable rather than a number. Every cell still collides, and the
    collision is now filed under a directory called ``cell-$AWS_BATCH_JOB_ARRAY_INDEX`` --
    which reads as a bug in the researcher's training script rather than in the platform,
    so it costs somebody a day before it is even reported here.

    Asserted twice on purpose. The first half is the structural claim that nothing this
    function sends carries a dollar sign, which is what the mutation above would break.
    The second half is what the container actually receives, because a request could
    satisfy the first and still hand the program an unexpanded string through some other
    route.
    """
    request = request_for(fanout=CURRICULUM_MATRIX)

    for name, value in container_environment(request).items():
        assert "$" not in value, (
            f"{name} carries {value!r}, and Batch does not shell-expand an environment "
            "value, so the container would receive the name of the variable as text"
        )

    printed = start_the_container_command(
        request_for(fanout=CURRICULUM_MATRIX, command=["printenv", "EDULLM_OUTPUT_PREFIX"]),
        array_index="7",
    )
    assert FANOUT_INDEX_VARIABLE not in printed
    assert printed.endswith("cell-7/")


def test_a_cell_checkpoints_under_its_own_prefix_rather_than_the_runs() -> None:
    """Mutation: move the output prefix per cell and leave the checkpoint directory alone.

    This is the half that would have been easy to miss and is the more expensive one. The
    checkpoint directory is defined as the output prefix followed by ``checkpoints/``, and
    a fan-out that moved only the prefix would still have forty trainers writing
    checkpoints over each other -- and unlike scattered artifacts, a corrupted checkpoint
    is what the retry the contract paid for resumes from.
    """
    request = request_for(
        fanout=CURRICULUM_MATRIX, command=["printenv", "EDULLM_CHECKPOINT_DIR"]
    )
    run_prefix = output_prefix(team="memory-split", run_id=RUN_ID)

    assert start_the_container_command(request, array_index="3") == (
        f"{run_prefix}cell-3/checkpoints/"
    )


def test_a_cell_reports_as_its_own_wandb_run_rather_than_all_forty_as_one() -> None:
    """Mutation: set WANDB_RUN_ID in the submit request and leave the prologue alone.

    The third variable that a single environment block fanned out unchanged would ruin,
    and the one that fails least visibly. W&B treats the run id as the run's identity, so
    forty cells initialising with one id do not collide with an error -- they resume into
    a single run, each overwriting the history the last one wrote. What a reader then sees
    is one run whose curves are forty experiments interleaved, which reads as a training
    instability rather than as a platform defect, and the forty real runs do not exist
    anywhere to be compared against.

    The suffix matches the output prefix's, so a cell's W&B run and a cell's checkpoint
    directory name the same cell. Two indices rather than one, because a derivation
    ignoring the index would satisfy any single-cell assertion.
    """
    request = request_for(fanout=CURRICULUM_MATRIX, command=["printenv", "WANDB_RUN_ID"])

    assert start_the_container_command(request, array_index="0") == f"{RUN_ID}-cell-0"
    assert start_the_container_command(request, array_index="39") == f"{RUN_ID}-cell-39"


def test_a_single_run_reports_as_the_run_id_the_platform_minted() -> None:
    """Mutation: suffix every run rather than only a cell, since the derivation exists.

    The join between a platform run and its W&B run is only worth having if it is the
    identity function on an ordinary run. A run id that arrived in W&B with a suffix
    nothing else carries would have to be un-suffixed by every reader, and a reader that
    forgot would find no run and conclude the run never reported.
    """
    request = request_for(command=["printenv", "WANDB_RUN_ID"])

    assert container_environment(request)["WANDB_RUN_ID"] == RUN_ID
    assert start_the_container_command(request, array_index="0") == RUN_ID


def test_a_single_run_keeps_the_command_and_the_prefix_it_has_always_had() -> None:
    """Mutation: give every run a cell segment, since the derivation is already written.

    Every run this platform has recorded wrote under ``teams/{team}/runs/{run_id}/`` and
    every lineage record naming one of those locations is immutable. Adding a segment to
    the single-run case would orphan all of that output while every test about fan-out
    stayed green, which is why this asserts the absence rather than trusting the branch.

    The command is asserted verbatim as well. A wrapper applied to a single run would be
    invisible in the prefix and would still change what a submitter's ``$0`` is, and would
    put a shell in front of a command that had deliberately not asked for one.
    """
    declared = manifest()
    request = request_for()

    assert request["ContainerOverrides"]["Command"] == list(declared.command)
    environment = container_environment(request)
    assert environment["EDULLM_OUTPUT_PREFIX"] == output_prefix(
        team=declared.team, run_id=RUN_ID
    )
    assert "cell-" not in environment["EDULLM_OUTPUT_PREFIX"]
    assert "EDULLM_FANOUT_INDEX_PARAMETER" not in environment


def test_a_cell_is_told_what_its_index_varies() -> None:
    """Mutation: keep fanout.index_parameter on the approver page and stop there.

    The manifest has carried it since fan-out existed and it never reached the container,
    so a cell knew it was number seven of forty and not what seven meant. An entrypoint
    picking a seed and one picking a curriculum shard read the same integer, which left the
    mapping from index to experiment recorded nowhere a later reader could reach it.
    """
    request = request_for(fanout=CURRICULUM_MATRIX)

    assert container_environment(request)["EDULLM_FANOUT_INDEX_PARAMETER"] == "curriculum_shard"


def test_wrapping_a_cell_command_does_not_expand_what_exec_form_would_not() -> None:
    """Mutation: join the submitted words with spaces instead of quoting them.

    ``ContainerOverrides.Command`` is exec form, so a submitted ``$EDULLM_RUN_ID`` reaches
    the program as thirteen literal characters and a submitter who wrote one meant those
    characters. Putting a shell in front of a fan-out is this platform's decision rather
    than the submitter's, so it must not change what any word means. An unquoted join would
    expand the word, and the container would run a command that is not the one the approver
    read or the lineage record sealed.
    """
    request = request_for(
        fanout=CURRICULUM_MATRIX,
        command=["printf", "%s", "$EDULLM_RUN_ID"],
    )

    assert start_the_container_command(request, array_index="1") == "$EDULLM_RUN_ID"


def test_a_submitters_own_shell_still_expands_inside_the_wrapper() -> None:
    """Mutation: quote the whole command as one word rather than word by word.

    The line the guide prints is ``bash -lc 'python train.py --save-folder
    "$EDULLM_CHECKPOINT_DIR"'``, and the checkpoint guard refuses a contracted run whose
    command does not reference that variable. So the inner shell has to keep working and
    has to see the values this cell's prologue exported rather than the run's. A wrapper
    that quoted the command as a single word would hand ``bash`` a program name with spaces
    in it and every fan-out with a real training command would fail to start.
    """
    request = request_for(
        fanout=CURRICULUM_MATRIX,
        command=["bash", "-lc", 'printf %s "$EDULLM_CHECKPOINT_DIR"'],
    )
    run_prefix = output_prefix(team="memory-split", run_id=RUN_ID)

    assert start_the_container_command(request, array_index="12") == (
        f"{run_prefix}cell-12/checkpoints/"
    )


def test_a_wrapped_fan_out_command_is_still_measured_against_the_batch_limit() -> None:
    """Mutation: wrap the command after the oversize check rather than before it.

    The prologue and the quoting are inside the 8,192 bytes Batch accepts for a container
    override, so a submission that fitted as typed can stop fitting once wrapped. Checking
    the unwrapped form would let that submission through to Batch, which refuses it with a
    message naming neither the command nor the wrapper -- after the approval is spent,
    which is the cost this check exists to avoid.
    """
    with pytest.raises(ContainerOverridesTooLargeError) as refusal:
        request_for(
            fanout=CURRICULUM_MATRIX,
            command=["python", "-c", "x" * (MAXIMUM_CONTAINER_OVERRIDES_BYTES - 600)],
        )

    assert str(MAXIMUM_CONTAINER_OVERRIDES_BYTES) in str(refusal.value)


def test_the_job_name_is_the_run_id_so_batch_is_a_third_join() -> None:
    """Mutation: mint a name inside AWS.

    Batch does not enforce unique job names, so this is not idempotency. It is
    join-ability: the run id is the S3 key, the Step Functions execution name and the Batch
    job name, and any two of the three disagreeing is visible.
    """
    request = request_for()

    assert request["JobName"] == RUN_ID
    assert request["Tags"]["edullm:run-id"] == RUN_ID


def test_the_job_carries_who_submitted_it_so_cancelling_needs_no_lineage_read() -> None:
    """Mutation: leave the submitter off, and authorise cancellation from the record.

    Cancellation has to answer "is this your run", and there are two places to learn that.
    The intent record knows, and reading it means granting something outside admission a
    read over the lineage bucket -- the one grant the whole write-once design exists to
    withhold, because a reader of that store sees every team's admission decisions on the
    way past. A tag on the job answers the same question with no new reach.

    Absent rather than empty when there is nobody to name. A run admitted before this field
    existed has no submitter, and an empty tag would assert that somebody submitted it and
    was called "" -- which is precisely the claim the cancel path must not be able to make,
    because an actor comparing their login against it must never match.
    """
    named = request_for(submitter="philote-dev")
    unnamed = request_for()

    assert named["Tags"]["edullm:submitter"] == "philote-dev"
    assert "edullm:submitter" not in unnamed["Tags"]


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


def test_the_container_environment_is_exactly_these_ten_variables() -> None:
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
        # The platform run id under W&B's own name, which is what lets a run here be joined
        # to a run there at all. Unconditional, unlike the two W&B names appended below,
        # because a run id always exists. A fan-out cell rewrites it in FANOUT_PROLOGUE so
        # that N cells do not resume into one W&B run.
        "WANDB_RUN_ID",
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


#: Where OLMo-core's images live, which is what a registration composed for a submission
#: naming OLMo-core pins against. Spelled here rather than imported, because the constant
#: this replaces was the defect: a repository name in the platform's own code is a name that
#: is right for one submission and silently wrong for the next.
OLMO_CORE_ECR_REPOSITORY = f"{SANDBOX_RESOURCE_PREFIX}olmo-core"


def registration_for(
    compute_profile: str = PROMOTED_PROFILE,
    ecr_repository: str = OLMO_CORE_ECR_REPOSITORY,
    **overrides: Any,
) -> dict[str, Any]:
    return batch_register_job_definition_request(
        manifest=manifest(compute_profile=compute_profile, **overrides),
        target=target(compute_profile),
        run_id=RUN_ID,
        ecr_repository=ecr_repository,
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
        manifest=declared,
        target=target(),
        run_id=RUN_ID,
        ecr_repository=OLMO_CORE_ECR_REPOSITORY,
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


def deployed_compute_environment(path: Path) -> dict[str, Any]:
    """The compute environment one template creates, read for its vCPU ceiling.

    Read rather than restated for the same reason the job definition above is: the ceiling
    and the reservation together are what decides how many first runs start at once, and a
    restated ceiling would keep agreeing with a template that had moved.
    """
    matching = [
        resource["Properties"]
        for resource in load_template(path)["Resources"].values()
        if isinstance(resource, dict)
        and resource.get("Type") == "AWS::Batch::ComputeEnvironment"
    ]
    assert len(matching) == 1, f"expected exactly one compute environment in {path.name}"
    return matching[0]


#: What one instance of each promoted profile's instance type offers a container: the
#: hardware's vCPU count, and its memory less the few GiB the ECS agent and the host need.
#: Both templates state the second in prose beside their reservations.
C7I_8XLARGE = (32, 61440)
G5_XLARGE = (4, 15360)


def test_the_cpu_check_reserves_an_eighth_of_a_machine_rather_than_all_of_one() -> None:
    """Mutation: put 32 vCPU and 61440 MiB back, or reduce only one of the two.

    olmo-core-check on cpu-32vcpu is the run guides/the-platform.md sends a new researcher to
    first, and it prints an interpreter version in under a second. Reserving a whole
    c7i.8xlarge for it made the CPU queue four wide, so a group onboarding together waited
    hours for seconds of work.

    Reducing only vCPU does not fix it and looks like it has. Batch places on vCPU and
    memory at once, so a 4 vCPU container still holding 60 GiB is still one job per
    instance: the vCPU arithmetic says thirty-two and the machine gives four. Both axes are
    asserted here, and they are asserted as agreeing with each other, because the smaller of
    the two is what decides concurrency and nothing reports which one that was.
    """
    shape = CONTAINER_SHAPES[PROMOTED_PROFILE]
    instance_vcpus, instance_memory_mib = C7I_8XLARGE
    ceiling = deployed_compute_environment(INFRA_ROOT / "batch-compute.yaml")["ComputeResources"]

    assert shape.vcpus == 4
    assert shape.memory_mib == 7680
    assert instance_vcpus // shape.vcpus == instance_memory_mib // shape.memory_mib == 8
    assert ceiling["MaxvCpus"] // shape.vcpus == 96


def test_the_gpu_shape_still_takes_a_whole_g5_because_the_device_is_the_scarce_thing() -> None:
    """Mutation: shrink the GPU reservation the way the CPU one was shrunk.

    It would change nothing and cost the shape's honesty. A g5.xlarge carries one A10G, a
    container without a GPU entry gets no device at all, and Batch has no way to give two
    jobs a share of one card. So the four vCPU are already the whole machine's four, the
    queue is already 384 / 4 wide, and a smaller reservation would only let Batch place a
    second job on an instance whose one device is taken.
    """
    shape = CONTAINER_SHAPES["gpu-1xa10g"]
    instance_vcpus, instance_memory_mib = G5_XLARGE
    ceiling = deployed_compute_environment(INFRA_ROOT / "batch-compute-gpu.yaml")[
        "ComputeResources"
    ]

    assert (shape.vcpus, shape.memory_mib, shape.gpus) == (instance_vcpus, instance_memory_mib, 1)
    assert ceiling["MaxvCpus"] // shape.vcpus == 96


def test_every_ceiling_is_a_whole_number_of_the_instances_it_would_buy() -> None:
    """Mutation: set any ``MaxvCpus`` to a number that is not a multiple of its vCPU count.

    A remainder smaller than one instance is capacity no job can ever occupy, because Batch
    scales by launching whole instances and every definition here reserves at least one.
    The waste is invisible and grows with the shape: 500 on a g5.48xlarge reads as more than
    two instances and buys exactly two, stranding 116 vCPU inside a ceiling somebody chose
    deliberately.

    Asserted across all three templates rather than the two above, because the sixteen
    environments were last moved together by one factor and the next edit to one of them is
    the one that will not be checked against the others. The vCPU counts come from
    ``INSTANCE_EVIDENCE``, which the capacity capture already keeps true against
    ``describe-instance-types``, so this cannot pass by agreeing with a second guess.

    THIS USED TO ASSERT ``len(instance_types) == 1`` AND THAT WAS A SECOND CLAIM SMUGGLED INTO
    A CEILING TEST. The one-type rule belonged to infra/batch-compute-gpu.yaml, which argued
    it on the premise that capacity was not short; when that stopped being true of the P pool
    on 2026-08-04 the rule had to go there, and a test named for whole instances was the wrong
    place to be enforcing it. What this test is actually for survives intact and gets stricter:
    every type an environment may launch has to divide its ceiling, because Batch may choose
    any of them and a remainder under either one is capacity no job can occupy. A mixed-vCPU
    pair now fails here, which is the check the old assertion was standing in for.
    """
    ceilings = {
        resource["Properties"]["ComputeEnvironmentName"]: resource["Properties"]["ComputeResources"]
        for path in COMPUTE_TEMPLATE_PATHS
        for resource in load_template(path)["Resources"].values()
        if isinstance(resource, dict)
        and resource.get("Type") == "AWS::Batch::ComputeEnvironment"
    }

    assert len(ceilings) == 16
    for name, resources in ceilings.items():
        for instance_type in resources["InstanceTypes"]:
            instance_vcpus = INSTANCE_EVIDENCE[instance_type]["required_vcpus"]
            assert resources["MaxvCpus"] % instance_vcpus == 0, (
                f"{name} ceilings {resources['MaxvCpus']} vCPU of {instance_type}, which is "
                f"{resources['MaxvCpus'] % instance_vcpus} vCPU short of a whole instance"
            )


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
    connecting them, which is why it was asserted rather than assumed. The registry lookup
    connected one end: the scan is read from ``$.ecr_repository``, which the workflow fills out
    of the registry. So the comparison moves to the registry, which is now the thing both
    sides have to agree with, and it is made for every submittable repository rather than
    for the one this constant happens to name.

    Both ends are connected now. The scan is read from ``$.ecr_repository`` and the image is
    composed from an ``ecr_repository`` argument, and the registry is what fills each, so
    what this compares is that they agree for the repository in hand rather than that two
    literals happen to match.
    """
    definition = json.loads(
        load_template(STATE_MACHINE_TEMPLATE_PATH)["Resources"]["AdmissionStateMachine"][
            "Properties"
        ]["DefinitionString"]["Fn::Sub"]
    )
    arguments = definition["States"]["ReadImageScan"]["Arguments"]
    image = registration_for()["ContainerProperties"]["Image"]

    # The scan is read from whatever the request carries, and what the request carries is
    # the registered name -- asserted against the submitting workflow in
    # tests/test_image_scan_repository.py and against the registry in the handler. So the
    # repository this pulls from has to be a registered one for the seam to hold.
    assert arguments["RepositoryName"] == "{% $states.input.ecr_repository %}"
    assert OLMO_CORE_ECR_REPOSITORY in set(submittable_ecr_repositories().values())
    assert image.split("@", maxsplit=1)[0].endswith(f"/{OLMO_CORE_ECR_REPOSITORY}")


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

    THIS TEST ASKED FOR A FIX AND GOT IT, so what it checks has changed shape and
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

    The state reads in JSONata since the paged-read fix, so the reference it is checked
    against is ``$states.input.ecr_repository`` rather than ``$.ecr_repository``. The
    question is unchanged: a pinned name here is a name that does not follow the submission.
    """
    definition = json.loads(
        load_template(STATE_MACHINE_TEMPLATE_PATH)["Resources"]["AdmissionStateMachine"][
            "Properties"
        ]["DefinitionString"]["Fn::Sub"]
    )
    arguments = definition["States"]["ReadImageScan"]["Arguments"]
    named = arguments.get("RepositoryName")
    pinned = sorted(
        f"{repository} (images in {ecr_repository})"
        for repository, ecr_repository in submittable_ecr_repositories().items()
        if ecr_repository in str(named)
    )

    assert not pinned, (
        f"ReadImageScan reads scan findings from {named} and from nowhere else, so an "
        f"approved submission naming any repository other than {', '.join(pinned)} would be "
        "admitted against findings for an image that repository never published. "
        "infra/admission-state-machine.yaml, the ReadImageScan state's "
        "Arguments.RepositoryName, is what would have to change."
    )
    assert named == "{% $states.input.ecr_repository %}"


def test_a_run_can_pin_an_image_from_every_submittable_repository() -> None:
    """Reads the Python against the registry. Mutation: put the repository back in a constant.

    The other half of the seam test above, asked as coverage rather than as consistency.
    That test proves the definition and the state machine name the same repository, which
    they would still do if that repository were the wrong one for the submission in hand;
    this proves the name is right for every submission that can be made.

    ``PUBLISHED_IMAGE_REPOSITORY`` was a constant naming OLMo-core's ECR repository, and it
    is gone. Where an image lives is not a fact about the platform. It is a fact about the
    submission's source repository -- ``config/repositories.yaml`` maps each registration to
    an ``ecr_repository`` and the mapping is not derivable from the name -- so a constant was
    only correct while one repository was submittable. A digest a second repository's
    manifest declares does not exist under the first's name, so the reference composed there
    would have been a reference to nothing, and Batch validates no image at registration: the
    run is accepted, the definition registers, the job is submitted and an instance scales
    before anything notices.

    So this asks the function for a definition per submittable repository and reads the
    repository back out of the image reference. A constant reintroduced anywhere on that path
    fails here for every repository but one.
    """
    registry = load_yaml(CONFIG_DIR / "repositories.yaml", RepositoryRegistry)
    mispinned = sorted(
        f"{repository} pinned a digest in "
        f"{registration_for(ecr_repository=ecr_repository)['ContainerProperties']['Image']}"
        for repository, ecr_repository in submittable_ecr_repositories().items()
        if not registration_for(ecr_repository=ecr_repository)["ContainerProperties"]["Image"]
        .split("@", maxsplit=1)[0]
        .endswith(f"/{ecr_repository}")
    )

    assert not mispinned, (
        "the job definition an accepted run registers for itself composes its image "
        "reference against a repository that is not the one the submission's source "
        f"repository is registered against: {', '.join(mispinned)}. "
        "src/edullm_platform/execution.py, the ecr_repository argument, is what would have "
        "to change."
    )
    # The lookup the handler performs at the call site, asserted here rather than left to
    # the handler's own tests: every submittable repository has an ecr_repository to pass,
    # so the argument can be resolved for any submission that reaches this point.
    assert set(submittable_ecr_repositories()) <= {
        entry.repository for entry in registry.repositories
    }


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
            manifest=manifest(),
            target=unshaped,
            run_id=RUN_ID,
            ecr_repository=OLMO_CORE_ECR_REPOSITORY,
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
