"""The Phase 3 templates, and the seams between them that no reference connects.

Phase 1 shipped a green suite over a workflow that could not complete a run. Phase 2
shipped one over a state machine that could not complete an execution. Both times the cause
was the same shape: both sides of a seam were asserted, and neither was compared to the
other. Phase 3 has more seams than either, because a job queue name appears in three files,
a subnet list in two, and a set of resource names in a configuration file the Lambda reads
at run time.

So the tests below divide in two. The first kind pins one template against a value a person
decided -- ``minvCpus`` is zero, the image is pinned by digest -- and each names the
mutation it exists to catch. The second kind reads *two or three files* and compares them,
and those are the ones this module exists for. A test asserting only one side of a seam is
worse than no test, because it reports an agreement it never checked.

Everything here parses YAML and asserts against structure. Never against the literal text of
an expression: that is Phase 1's lesson, and it is the specific way a suite goes green over
a path that cannot work.

Two of the seams below cross out of YAML entirely and into Python, which is where the
remaining Phase 3 disagreements were found. The recorder's IAM role is compared against
what ``lifecycle_projection`` actually reads out of an event, because a grant argued for on
a false premise reads exactly like one argued for on a true one. And the event source
mapping's ``FunctionResponseTypes`` is compared against the key ``lifecycle_handler``
actually answers under, because those two files were written independently and agree today
only because the batch size happens to be one.
"""

from __future__ import annotations

import email
import json
import string
from collections.abc import Iterator
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import pytest
import yaml
from infrastructure_support import (
    ACCOUNT_LITERAL,
    BOUNDARY,
    IAM_ROOT,
    INFRA_ROOT,
    PROJECT_ROOT,
    iam_roles,
    load_template,
    resource_of_type,
    statement_actions,
    walk_strings,
)

from edullm_platform.config import load_yaml
from edullm_platform.contracts.execution import BatchJobBinding, ExecutionTarget
from edullm_platform.contracts.lifecycle import SchedulerAttempt
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.repository_registry import RepositoryRegistry
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.execution import batch_submit_request
from edullm_platform.lifecycle_handler import (
    BATCH_ITEM_FAILURES_KEY,
    BATCH_ITEM_FAILURES_RESPONSE_TYPE,
    handler,
)
from edullm_platform.lifecycle_projection import project_batch_event

NETWORK_PATH = INFRA_ROOT / "batch-network.yaml"
COMPUTE_PATH = INFRA_ROOT / "batch-compute.yaml"
GPU_COMPUTE_PATH = INFRA_ROOT / "batch-compute-gpu.yaml"
GPU_SHAPES_PATH = INFRA_ROOT / "batch-compute-gpu-shapes.yaml"
EVENTS_PATH = INFRA_ROOT / "batch-events.yaml"
OUTPUTS_PATH = INFRA_ROOT / "outputs-bucket.yaml"
STATE_MACHINE_PATH = INFRA_ROOT / "admission-state-machine.yaml"
BATCH_ROLES_PATH = IAM_ROOT / "batch-roles.yaml"
LIFECYCLE_ROLE_PATH = IAM_ROOT / "lifecycle-lambda-role.yaml"
SERVICE_ROLES_PATH = IAM_ROOT / "admission-service-roles.yaml"
EXECUTION_TARGETS_PATH = PROJECT_ROOT / "config" / "execution-targets.yaml"
REPOSITORY_REGISTRY_PATH = PROJECT_ROOT / "config" / "repositories.yaml"
WORKLOAD_CATALOG_PATH = PROJECT_ROOT / "config" / "workload-catalog.yaml"
CPU_MANIFEST_PATH = PROJECT_ROOT / "fixtures" / "manifests" / "cpu-routine.yaml"

#: Every template this phase adds or amends, whoever applies it.
PHASE3_TEMPLATE_PATHS = (
    NETWORK_PATH,
    COMPUTE_PATH,
    EVENTS_PATH,
    OUTPUTS_PATH,
    STATE_MACHINE_PATH,
    BATCH_ROLES_PATH,
    LIFECYCLE_ROLE_PATH,
)
#: The subset CI deploys. infra/iam/ is applied from a laptop because the deployer has no
#: iam:CreateRole, so an IAM resource appearing in one of these would fail at deploy time.
CI_DEPLOYED_TEMPLATE_PATHS = (NETWORK_PATH, COMPUTE_PATH, EVENTS_PATH, OUTPUTS_PATH)

COMPUTE_ENVIRONMENT_NAME = "sbsandbox-intern-edullm-cpu"
JOB_QUEUE_NAME = "sbsandbox-intern-edullm-cpu"
JOB_DEFINITION_NAME = "sbsandbox-intern-edullm-cpu-run"
BATCH_LOG_GROUP = "/aws/batch/sbsandbox-intern-edullm-cpu"

#: Every compute template, so a seam test compares a role, a rule or a config against the
#: whole set of queues that exist rather than against one of them. A test that reads only
#: COMPUTE_PATH goes green while the GPU half of the same seam is broken, which is the
#: failure this tuple exists to make impossible: adding a third compute template is the one
#: edit needed to bring every seam below along with it.
COMPUTE_PATHS = (COMPUTE_PATH, GPU_COMPUTE_PATH, GPU_SHAPES_PATH)
GPU_COMPUTE_ENVIRONMENT_NAME = "sbsandbox-intern-edullm-gpu"
GPU_JOB_QUEUE_NAME = "sbsandbox-intern-edullm-gpu"
GPU_JOB_DEFINITION_NAME = "sbsandbox-intern-edullm-gpu-run"

#: The fourteen profiles infra/batch-compute-gpu-shapes.yaml backs, in the order the template
#: declares them. Every seam below that used to compare against a pair of names now compares
#: against the CPU name, the original GPU name and these, so promoting a shape is one edit
#: here rather than a number to increment in each test.
#:
#: THIS IS WHAT THE TEMPLATE CREATES AND NOT WHAT A SUBMISSION MAY ASK FOR. The two were the
#: same list until 2026-08-04. Both H100 shapes still have an environment, a queue and a job
#: definition here and are no longer routable, so the execution-targets seam reads
#: :data:`BACKED_GPU_SHAPE_PROFILES` below and the template seams keep reading this one.
#: Conflating them would make withdrawing a shape from the form look like deleting
#: infrastructure that is still deployed.
GPU_SHAPE_PROFILES = (
    "gpu-1xt4",
    "gpu-4xt4",
    "gpu-8xt4",
    "gpu-4xa10g",
    "gpu-8xa10g",
    "gpu-1xl4",
    "gpu-4xl4",
    "gpu-8xl4",
    "gpu-1xl40s",
    "gpu-4xl40s",
    "gpu-8xl40s",
    "gpu-1xh100",
    "gpu-8xa100",
    "gpu-8xh100",
)
GPU_SHAPE_QUEUE_NAMES = tuple(f"sbsandbox-intern-edullm-{name}" for name in GPU_SHAPE_PROFILES)
GPU_SHAPE_JOB_DEFINITION_NAMES = tuple(f"{name}-run" for name in GPU_SHAPE_QUEUE_NAMES)

#: The subset of the above that config/execution-targets.yaml still binds, which is every
#: shape whose instance type this account can actually obtain. p5.48xlarge and p5.4xlarge
#: were withdrawn on 2026-08-04 after 6,815 and 2,530 launch attempts respectively returned
#: InsufficientInstanceCapacity and the billing record showed zero instance-hours of either
#: type ever. Derived by subtraction so that the template list stays the one place a shape is
#: named, and so re-promoting one is a deletion from this tuple rather than a retyped list.
UNOBTAINABLE_GPU_SHAPE_PROFILES = ("gpu-1xh100", "gpu-8xh100")
BACKED_GPU_SHAPE_PROFILES = tuple(
    profile for profile in GPU_SHAPE_PROFILES if profile not in UNOBTAINABLE_GPU_SHAPE_PROFILES
)

#: The definition an accepted run registers for itself, as the states role's grants name it.
#: No template creates it -- it is minted at admission from the run id, which is why it is a
#: pattern here rather than a name read out of a template like the two above.
PER_RUN_JOB_DEFINITION_NAME = "sbsandbox-intern-edullm-run_*"
GPU_BATCH_LOG_GROUP = "/aws/batch/sbsandbox-intern-edullm-gpu"
GPU_EXECUTION_ROLE_NAME = "sbsandbox-intern-edullm-batch-gpu-execution"
GPU_WORKLOAD_ROLE_NAME = "sbsandbox-intern-edullm-batch-gpu-workload"
GPU_INSTANCE_ROLE_NAME = "sbsandbox-intern-edullm-batch-gpu-instance"
GPU_BATCH_ROLES_PATH = IAM_ROOT / "batch-gpu-roles.yaml"
EXECUTION_ROLE_NAME = "sbsandbox-intern-edullm-batch-execution"
WORKLOAD_ROLE_NAME = "sbsandbox-intern-edullm-batch-workload"
INSTANCE_ROLE_NAME = "sbsandbox-intern-edullm-batch-instance"
LIFECYCLE_ROLE_NAME = "sbsandbox-intern-edullm-lifecycle-lambda"
RULE_NAME = "sbsandbox-intern-edullm-batch-lifecycle"
LIFECYCLE_QUEUE_NAME = "sbsandbox-intern-edullm-batch-lifecycle"
DEAD_LETTER_QUEUE_NAME = "sbsandbox-intern-edullm-batch-lifecycle-dlq"
RECORDER_FUNCTION_NAME = "sbsandbox-intern-edullm-lifecycle-recorder"
OUTPUTS_BUCKET = "sbsandbox-intern-edullm-outputs"
LINEAGE_BUCKET = "sbsandbox-intern-edullm-lineage"

#: The three actions an image is pulled with. ``ecr:GetAuthorizationToken`` is deliberately
#: not among them: it has no resource type and is granted on ``"*"``, so the statement
#: carrying it says nothing about which repository an identity may pull from.
IMAGE_PULL_ACTIONS = frozenset(
    {
        "ecr:BatchCheckLayerAvailability",
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer",
    }
)

#: Every identity that pulls a research image, across both files that create one. The GPU
#: trio is a copy of the CPU trio, and the copy is the exposure: a scope widened in one file
#: and not the other leaves a whole queue unable to start a container, and a test reading
#: one file reports that everything is covered.
IMAGE_PULLING_ROLES = (
    (BATCH_ROLES_PATH, EXECUTION_ROLE_NAME),
    (BATCH_ROLES_PATH, INSTANCE_ROLE_NAME),
    (GPU_BATCH_ROLES_PATH, GPU_EXECUTION_ROLE_NAME),
    (GPU_BATCH_ROLES_PATH, GPU_INSTANCE_ROLE_NAME),
)

#: c7i.8xlarge is offered in these five and not in us-east-1e. Measured against the account
#: on 2026-07-27; the consequence of getting it wrong is a job that waits rather than errors.
INSTANCE_CAPABLE_ZONES = ("us-east-1a", "us-east-1b", "us-east-1c", "us-east-1d", "us-east-1f")
ZONE_WITHOUT_THE_INSTANCE_TYPE = "us-east-1e"

#: Every zone the network stack declares a subnet in, which since 2026-08-03 is one more than
#: the CPU environment may use. The two lists were the same until p5 was backed, and the tests
#: below keep them apart on purpose: this one is what the VPC has, the one above is what
#: c7i.8xlarge can be placed into.
DECLARED_ZONES = (*INSTANCE_CAPABLE_ZONES, ZONE_WITHOUT_THE_INSTANCE_TYPE)

#: Every zone EC2 offers each backed instance type in, read off
#: ``describe-instance-type-offerings --location-type availability-zone`` against this account
#: on 2026-08-04. A measurement, not a policy: re-read it, do not reason about it.
#:
#: THIS REPLACED A SET OF NAMES AND THE REPLACEMENT IS WHAT MADE A SECOND INSTANCE TYPE SAFE.
#: What stood here was ``ZONE_1E_CAPABLE_INSTANCE_TYPES``, the two p5 sizes, and the rule it
#: fed was that an environment importing the us-east-1e subnet must contain nothing else. That
#: is the correct rule for an environment with one instance type and the wrong one for an
#: environment with two: p5en.48xlarge is sold in us-east-1b and us-east-1d only, and under
#: the old rule adding it to gpu-8xh100 would have read as an error when it takes nothing
#: away -- p5.48xlarge still reaches all six zones and every zone stays placeable.
#:
#: A full map rather than a widened set, because two rules need it and they pull opposite
#: ways. A zone is legitimate if *some* listed type is offered there; a type is legitimate if
#: it is offered in *some* imported zone. Only the second catches the mistake this change was
#: one step away from making -- p5e.48xlarge is the obvious third H100 alternate and EC2
#: offers it in no us-east-1 zone at all, so listing it would have added a name Batch could
#: never launch and no set of zone names would have noticed.
ZONES_OFFERING_INSTANCE_TYPE: dict[str, frozenset[str]] = {
    "c7i.8xlarge": frozenset(INSTANCE_CAPABLE_ZONES),
    "g4dn.xlarge": frozenset(INSTANCE_CAPABLE_ZONES),
    "g4dn.12xlarge": frozenset(INSTANCE_CAPABLE_ZONES),
    "g4dn.metal": frozenset(INSTANCE_CAPABLE_ZONES),
    "g5.xlarge": frozenset(INSTANCE_CAPABLE_ZONES),
    "g5.12xlarge": frozenset(INSTANCE_CAPABLE_ZONES),
    "g5.48xlarge": frozenset(INSTANCE_CAPABLE_ZONES),
    "g6.xlarge": frozenset(INSTANCE_CAPABLE_ZONES),
    "g6.12xlarge": frozenset(INSTANCE_CAPABLE_ZONES),
    "g6.48xlarge": frozenset(INSTANCE_CAPABLE_ZONES),
    "g6e.xlarge": frozenset({"us-east-1a", "us-east-1b", "us-east-1c", "us-east-1d"}),
    "g6e.12xlarge": frozenset({"us-east-1a", "us-east-1b", "us-east-1c", "us-east-1d"}),
    "g6e.48xlarge": frozenset({"us-east-1a", "us-east-1b", "us-east-1c", "us-east-1d"}),
    "p4d.24xlarge": frozenset({"us-east-1a", "us-east-1b", "us-east-1c", "us-east-1d"}),
    "p4de.24xlarge": frozenset({"us-east-1c", "us-east-1d"}),
    "p5.4xlarge": frozenset(DECLARED_ZONES),
    "p5.48xlarge": frozenset(DECLARED_ZONES),
    "p5en.48xlarge": frozenset({"us-east-1b", "us-east-1d"}),
}

#: The instance types EC2 offers in us-east-1e, derived from the map above rather than written
#: out again, so the two cannot disagree. An environment importing the 1e subnet with none of
#: these is a job that waits in RUNNABLE forever with no error anywhere.
ZONE_1E_CAPABLE_INSTANCE_TYPES = frozenset(
    instance_type
    for instance_type, zones in ZONES_OFFERING_INSTANCE_TYPE.items()
    if ZONE_WITHOUT_THE_INSTANCE_TYPE in zones
)

CONDITIONAL_WRITE_PARAMETERS = {"ChecksumAlgorithm": "SHA256", "IfNoneMatch": "*"}

#: The keys a submit request carries. Named here so the SubmitToBatch test can assert that
#: the state machine mentions none of them -- the whole point of passing the request through
#: rather than rebuilding it is that this file is the only place they appear.
SUBMIT_REQUEST_FIELDS = (
    "JobName",
    "JobQueue",
    "JobDefinition",
    "ContainerOverrides",
    "Timeout",
    "RetryStrategy",
    "Tags",
    "PropagateTags",
    "ArrayProperties",
)

#: AWS's own documented example account, which tests/test_evidence.py allows as a literal.
EXAMPLE_ACCOUNT = "123456789012"

#: One run id, used wherever a seam test needs a delivery that projects. Reused rather than
#: reinvented per test, so a projection that started depending on the shape of the id would
#: fail in one place.
SEAM_RUN_ID = "run_019fa439-203e-70c7-bf8a-9ce33bc71f20"


def terminal_envelope() -> dict[str, Any]:
    """One ``Batch Job State Change`` for a job that finished, as EventBridge delivers it.

    Carries the ``attempts`` array, which is the fact two of the tests below turn on: the
    recorder reads the attempt window and the container exit code out of the event, so the
    ``batch:DescribeJobs`` grant the plan argued for has nothing to fetch.
    """
    return {
        "version": "0",
        "id": "5f2c1b7e-9a34-4d61-8f0b-72c3e5a91d48",
        "detail-type": "Batch Job State Change",
        "source": "aws.batch",
        "account": EXAMPLE_ACCOUNT,
        "time": "2026-07-27T20:15:30Z",
        "region": "us-east-1",
        "resources": [],
        "detail": {
            "jobArn": (
                f"arn:aws:batch:us-east-1:{EXAMPLE_ACCOUNT}:job/"
                "3f9d1f1e-6b18-4a63-9c0d-2f6d4a1b8c70"
            ),
            "jobId": "3f9d1f1e-6b18-4a63-9c0d-2f6d4a1b8c70",
            "jobName": SEAM_RUN_ID,
            "jobQueue": (
                f"arn:aws:batch:us-east-1:{EXAMPLE_ACCOUNT}:job-queue/{JOB_QUEUE_NAME}"
            ),
            "status": "SUCCEEDED",
            "createdAt": 1_785_182_695_000,
            # The top-level container, which AWS lists among BatchJobStateChange's required
            # properties and this fixture did not have. The recorder reads the output prefix
            # out of it, so a succeeded event without one is refused -- which is the right
            # behaviour and made this fixture's omission visible.
            "container": {
                "environment": [
                    {"name": "EDULLM_RUN_ID", "value": SEAM_RUN_ID},
                    {
                        "name": "EDULLM_OUTPUT_PREFIX",
                        "value": (
                            f"s3://{OUTPUTS_BUCKET}/teams/platform/runs/{SEAM_RUN_ID}/"
                        ),
                    },
                ]
            },
            "attempts": [
                {
                    "container": {"exitCode": 0},
                    "startedAt": 1_785_182_700_000,
                    "stoppedAt": 1_785_183_060_000,
                }
            ],
        },
    }


class AcceptingStore:
    """An object store that accepts every write, for the one test that needs a working one."""

    def put_object(self, **arguments: Any) -> Any:
        del arguments
        return {}


def resources_of_type(path: Path, resource_type: str) -> dict[str, dict[str, Any]]:
    """Every resource of one type, by logical id. Unlike ``resource_of_type``, many."""
    template = load_template(path)
    return {
        logical_id: resource
        for logical_id, resource in template["Resources"].items()
        if isinstance(resource, dict) and resource.get("Type") == resource_type
    }


def properties_of(path: Path, resource_type: str) -> dict[str, Any]:
    _logical_id, resource = resource_of_type(load_template(path), resource_type)
    properties = resource["Properties"]
    assert isinstance(properties, dict)
    return properties


def role_named(path: Path, name: str) -> dict[str, Any]:
    matching = [role for role in iam_roles(load_template(path)) if role["RoleName"] == name]
    assert len(matching) == 1, f"expected exactly one role named {name}"
    return matching[0]


def role_actions(path: Path, name: str) -> list[str]:
    return [
        action
        for policy in role_named(path, name)["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
        for action in statement_actions(statement)
    ]


def resource_arns(resource: object) -> list[str]:
    """The ARN strings a statement's Resource names, with the Fn::Sub wrappers unwrapped."""
    if isinstance(resource, str):
        return [resource]
    if isinstance(resource, list):
        return [arn for item in resource for arn in resource_arns(item)]
    assert isinstance(resource, dict), f"unexpected Resource shape: {resource}"
    assert list(resource) == ["Fn::Sub"], f"unexpected Resource shape: {resource}"
    return [resource["Fn::Sub"]]


def named_after(arn: str, resource_type: str) -> str | None:
    """The name in ``.../<resource_type>/<name>``, with any Batch revision suffix removed."""
    marker = f":{resource_type}/"
    if marker not in arn:
        return None
    return arn.split(marker, 1)[1].removesuffix(":*")


def state_machine_definition() -> dict[str, Any]:
    template = load_template(STATE_MACHINE_PATH)
    _logical_id, machine = resource_of_type(template, "AWS::StepFunctions::StateMachine")
    definition = machine["Properties"]["DefinitionString"]["Fn::Sub"]
    assert isinstance(definition, str)
    parsed = json.loads(definition)
    assert isinstance(parsed, dict)
    return parsed


def execution_target_bindings() -> dict[str, dict[str, Any]]:
    """Every backed target, keyed by compute profile.

    This returned the single Phase 3 binding and asserted there was exactly one, which was
    the right shape while one profile was promoted and the wrong one the moment a second
    was. The assertion it carried -- "a second entry needs its own queue" -- is now checked
    where it belongs, by comparing every binding against the templates rather than by
    refusing to look at more than one.
    """
    loaded = yaml.safe_load(EXECUTION_TARGETS_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    targets = loaded["targets"]
    assert isinstance(targets, list) and targets
    bindings = {}
    for binding in targets:
        assert isinstance(binding, dict)
        bindings[binding["compute_profile"]] = binding
    assert len(bindings) == len(targets), "two targets claim the same compute profile"
    return bindings


def names_across_compute_templates(resource_type: str, key: str) -> set[str]:
    """One named property from ``resource_type``, gathered across every compute template.

    Every resource of the type in each template rather than the one, which was the same thing
    until infra/batch-compute-gpu-shapes.yaml declared thirteen of each. Reading only the
    first would leave twelve queues out of every comparison below, and the comparisons are
    what stop a queue existing with no event rule matching it and no role permitted to submit
    to it.
    """
    return {
        resource["Properties"][key]
        for path in COMPUTE_PATHS
        for resource in resources_of_type(path, resource_type).values()
    }


def seam_target(manifest: RunManifest) -> ExecutionTarget:
    return ExecutionTarget(
        compute_profile=manifest.compute_profile,
        region="us-east-1",
        job_queue_arn=f"arn:aws:batch:us-east-1:{EXAMPLE_ACCOUNT}:job-queue/{JOB_QUEUE_NAME}",
        job_definition_arn=(
            f"arn:aws:batch:us-east-1:{EXAMPLE_ACCOUNT}:job-definition/{JOB_DEFINITION_NAME}"
        ),
        execution_role_arn=f"arn:aws:iam::{EXAMPLE_ACCOUNT}:role/{EXECUTION_ROLE_NAME}",
        workload_role_arn=f"arn:aws:iam::{EXAMPLE_ACCOUNT}:role/{WORKLOAD_ROLE_NAME}",
        log_group=BATCH_LOG_GROUP,
    )


def cpu_manifest(**overrides: Any) -> RunManifest:
    payload = yaml.safe_load(CPU_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    payload.update(overrides)
    return RunManifest.model_validate(payload)


@pytest.fixture(scope="module")
def submit_request() -> dict[str, Any]:
    """The request Python builds, so the seam tests can compare it to what AWS declares."""
    manifest = cpu_manifest()
    target = seam_target(manifest)
    return batch_submit_request(
        manifest=manifest,
        target=target,
        run_id=SEAM_RUN_ID,
        job_definition=target.job_definition_arn,
    )


@pytest.fixture(scope="module")
def every_submit_request_field() -> frozenset[str]:
    """Every key ``batch_submit_request`` can put in a request, across both its shapes.

    Derived rather than listed. ``SUBMIT_REQUEST_FIELDS`` is what the seam test below
    searches the ASL for, and a hand-written list is a third copy of a key set that already
    exists in two places -- a tenth field added to the Python and not to the list would be
    a field the ASL was never checked for, which is the failure the seam test exists to
    prevent, reintroduced one level up.

    Both shapes, because ``ArrayProperties`` is absent for a single container on purpose:
    reading one shape would leave the fan-out key unsearched.
    """
    single = cpu_manifest()
    fanned = cpu_manifest(fanout={"size": 2, "index_parameter": "SEED"})
    return frozenset(
        key
        for manifest in (single, fanned)
        for key in batch_submit_request(
            manifest=manifest,
            target=seam_target(manifest),
            run_id=SEAM_RUN_ID,
            job_definition=seam_target(manifest).job_definition_arn,
        )
    )


# --------------------------------------------------------------------------------------
# The compute environment, the queue and the job definition
# --------------------------------------------------------------------------------------


def test_the_compute_environment_holds_no_capacity_when_it_is_idle() -> None:
    """Mutation: set ``MinvCpus`` to 1.

    That is the held floor the bounded capacity window has to decide about, and setting it
    here would bill continuously from the day it merged -- a monthly figure nobody could
    attribute to a change, because nothing else in the repository would look different.
    """
    resources = properties_of(COMPUTE_PATH, "AWS::Batch::ComputeEnvironment")["ComputeResources"]

    assert resources["MinvCpus"] == 0
    assert resources["MaxvCpus"] > 0
    # Batch owns DesiredvCpus and moves it on every scaling decision. A value in the
    # template is one CloudFormation tries to restore on the next deploy.
    assert "DesiredvCpus" not in resources


def test_the_job_definition_pins_the_image_by_digest_and_never_by_a_tag() -> None:
    """Mutation: replace the digest with ``:latest``, or with any tag at all.

    A tag is a mutable pointer, so the bytes that run stop being the bytes the decision
    record names. The assertion is on the shape rather than on the exact digest, because
    releasing a new image is meant to be an edit somebody reviews and not a test failure.
    """
    container = properties_of(COMPUTE_PATH, "AWS::Batch::JobDefinition")["ContainerProperties"]
    image = container["Image"]["Fn::Sub"]
    repository_and_reference = image.rsplit("/", 1)[1]

    assert "@sha256:" in repository_and_reference
    digest = repository_and_reference.split("@", 1)[1]
    assert len(digest) == len("sha256:") + 64
    # A reference may carry a tag and a digest at once, and only the digest decides which
    # bytes run -- so a tag here would be decoration that reads as the source of truth.
    assert ":" not in repository_and_reference.split("@", 1)[0]


def test_the_job_definition_names_two_different_roles_for_two_different_jobs() -> None:
    """Mutation: point ``JobRoleArn`` and ``ExecutionRoleArn`` at the same role.

    The execution role pulls the image and opens the log stream; the workload role is what
    the container's own command runs as. One role doing both hands the workload the registry
    credentials, which is the separation ECS task roles exist to provide.
    """
    container = properties_of(COMPUTE_PATH, "AWS::Batch::JobDefinition")["ContainerProperties"]

    execution_role = container["ExecutionRoleArn"]["Fn::Sub"]
    workload_role = container["JobRoleArn"]["Fn::Sub"]
    assert execution_role != workload_role
    assert execution_role.endswith(f"role/{EXECUTION_ROLE_NAME}")
    assert workload_role.endswith(f"role/{WORKLOAD_ROLE_NAME}")


def test_the_job_definition_carries_a_timeout_and_a_retry_floor_of_its_own() -> None:
    """Mutation: drop the ``Timeout`` block.

    Every real submission overrides both of these, so removing them changes nothing that any
    fixture would notice -- and a job submitted by hand during an incident would then run
    unbounded. The floor is the point.
    """
    properties = properties_of(COMPUTE_PATH, "AWS::Batch::JobDefinition")

    assert properties["Timeout"]["AttemptDurationSeconds"] > 0
    assert properties["RetryStrategy"]["Attempts"] == 1
    assert properties["PlatformCapabilities"] == ["EC2"]
    assert properties["Type"] == "container"


def test_nothing_in_the_batch_stack_survives_a_stack_delete() -> None:
    """Mutation: add ``DeletionPolicy: Retain`` to any resource here.

    Unlike the lineage bucket, every resource in this stack is meant to be removable, and
    that is what makes the rollback a deploy rather than a rescue. A retained resource
    blocks the next create on a name that already exists, which is exactly what stranded the
    Phase 1 ECR stack.
    """
    for logical_id, resource in load_template(COMPUTE_PATH)["Resources"].items():
        assert "DeletionPolicy" not in resource, logical_id
        assert "UpdateReplacePolicy" not in resource, logical_id


# --------------------------------------------------------------------------------------
# The launch template the two eight-device P shapes boot from
# --------------------------------------------------------------------------------------


def launch_templates() -> dict[str, dict[str, Any]]:
    return resources_of_type(GPU_SHAPES_PATH, "AWS::EC2::LaunchTemplate")


def gpu_shape_environments() -> dict[str, dict[str, Any]]:
    return resources_of_type(GPU_SHAPES_PATH, "AWS::Batch::ComputeEnvironment")


def environment_named(name: str) -> dict[str, Any]:
    matching = [
        resource["Properties"]
        for resource in gpu_shape_environments().values()
        if resource["Properties"]["ComputeEnvironmentName"] == name
    ]
    assert len(matching) == 1, f"expected exactly one compute environment named {name}"
    return matching[0]


def user_data_of(launch_template: dict[str, Any]) -> str:
    encoded = launch_template["Properties"]["LaunchTemplateData"]["UserData"]
    assert set(encoded) == {"Fn::Base64"}, "user data must go through Fn::Base64"
    return str(encoded["Fn::Base64"])


#: The two that boot from it, and the only two. Written out rather than derived from the
#: shape list, because the whole content of the scope is which names are in it.
P_FAMILY_PROFILES = ("gpu-8xa100", "gpu-8xh100")

#: 37.2 GiB of corpus is what a job already tried to stage, so a root volume has to be
#: comfortably above that and not merely above it. The number below is a floor rather than
#: the 500 the template sets, because raising the volume is a change nobody should have to
#: come here to permit and lowering it back under the corpus is the one that has to fail.
MINIMUM_P_FAMILY_ROOT_GIB = 200

#: Written by AWS Batch itself into /etc/ecs/ecs.config and documented as reserved. Setting
#: either from a launch template is documented to break scheduling and scaling.
BATCH_RESERVED_ECS_VARIABLES = ("ECS_CLUSTER", "ECS_INSTANCE_ATTRIBUTES")


def test_the_two_p_shapes_boot_from_a_root_volume_the_corpus_actually_fits_in() -> None:
    """Mutation: drop ``VolumeSize`` back under the corpus, or delete the mapping entirely.

    This is the constraint a future edit would break in silence, which is why it is pinned
    here rather than left to the comment that argues it. With no launch template at all --
    which is how all sixteen environments in this account stood until 2026-08-04 -- Batch
    takes the ECS AL2023 GPU AMI's own mapping, a single 30 GiB gp3 volume, leaving roughly
    13 GiB free once the OS and the training image are on it. A run staging 9,989,799,834
    tokens at four bytes each needs 37.2 GiB and died in ``stage_input`` with ENOSPC; it
    could not have succeeded on a clean instance either.

    Nothing about that failure is visible from this template without this test. The
    environment reports VALID, the job is placed, the container starts, and the only symptom
    is an ``OSError`` from inside somebody's training script.
    """
    templates = launch_templates()
    assert len(templates) == 1, "one launch template, shared, so a size change cannot be half-made"
    data = next(iter(templates.values()))["Properties"]["LaunchTemplateData"]
    mappings = data["BlockDeviceMappings"]

    assert len(mappings) == 1
    root = mappings[0]
    # The AMI's own RootDeviceName, read from describe-images rather than assumed. A device
    # name the AMI does not use is not an error: it silently attaches a second volume beside
    # an untouched 30 GiB root, so the job dies of ENOSPC while the disk looks bought.
    assert root["DeviceName"] == "/dev/xvda"
    assert root["Ebs"]["VolumeSize"] >= MINIMUM_P_FAMILY_ROOT_GIB
    assert root["Ebs"]["VolumeType"] == "gp3"
    # gp3's free baseline is 125 MB/s, which makes staging the corpus five and a half
    # minutes of a job doing nothing else.
    assert root["Ebs"]["Throughput"] > 125
    assert root["Ebs"]["DeleteOnTermination"] is True
    # Absent rather than false. The account has EbsEncryptionByDefault off and this AMI's
    # snapshot is unencrypted, so omitting the flag matches the account today and follows it
    # if the account default is ever turned on. `false` would refuse to launch on that day.
    assert "Encrypted" not in root["Ebs"]


def test_the_agent_settings_reach_the_instance_as_mime_multipart_and_nothing_else_does() -> None:
    """Mutation: replace the MIME wrapper with a bare ``#!/bin/bash`` script.

    AWS Batch merges its own user data with the launch template's, and documents that user
    data in a launch template must be MIME multi-part for that merge to happen. A bare script
    is not rejected -- it is discarded. The instance boots on the agent defaults, a stopped
    container's writable layer is kept for three hours, and this template still reads as
    though fifteen minutes were configured.

    Parsed rather than pattern-matched, because the format's load-bearing parts are the blank
    lines, and a regular expression over the text would pass on a document cloud-init's own
    parser rejects.
    """
    body = user_data_of(next(iter(launch_templates().values())))
    message = email.message_from_string(body)
    parts = [part for part in message.walk() if not part.is_multipart()]

    assert message.is_multipart()
    assert message.get_content_type() == "multipart/mixed"
    assert not message.defects
    assert len(parts) == 1
    assert parts[0].get_content_type() == "text/x-shellscript"
    assert not parts[0].defects

    script = str(parts[0].get_payload())
    assert script.startswith("#!/bin/bash")
    assert "/etc/ecs/ecs.config" in script
    # The one that fixes the second failure: three jobs landed on one instance and the second
    # and third died in six seconds each on the first's leftover partial download, because
    # ECS_ENGINE_TASK_CLEANUP_WAIT_DURATION defaults to three hours. The reference also says
    # image cleanup cannot touch an image while a container still references it, so this
    # setting gates the three below rather than sitting beside them.
    assert "ECS_ENGINE_TASK_CLEANUP_WAIT_DURATION=15m" in script
    # Each of these is a real variable in the agent's configuration reference and each is
    # inside the bounds it documents: the cleanup interval may not go below 10m, and the
    # number deleted per cycle may not go below 1.
    assert "ECS_IMAGE_CLEANUP_INTERVAL=15m" in script
    assert "ECS_IMAGE_MINIMUM_CLEANUP_AGE=30m" in script
    assert "ECS_NUM_IMAGES_DELETE_PER_CYCLE=25" in script
    # Batch writes both of these itself and names them reserved. Setting either here is
    # documented to break scheduling and scaling, and would do it on the two most expensive
    # shapes in the account.
    for reserved in BATCH_RESERVED_ECS_VARIABLES:
        assert reserved not in script
    # Not an ECS agent variable at all. It appears nowhere in the configuration reference, so
    # a line naming it would sit in ecs.config being ignored while reading like a guard.
    assert "ECS_DISK_FREE_SPACE_THRESHOLD" not in script


def test_only_the_two_idle_p_shapes_were_given_the_launch_template() -> None:
    """Mutation: attach it to a third environment, or to all fourteen.

    Attaching a launch template is an infrastructure update, and an infrastructure update
    replaces every container instance in the environment. ``TerminateJobsOnUpdate`` is false
    and ``JobExecutionTimeoutMinutes`` is 360, so a deploy waits up to six hours for running
    jobs and then kills what is left. The two below were idle when this landed and ``gpu``
    and ``gpu-4xl40s`` were not, which is the whole reason the scope is two rather than
    fourteen -- and a later edit that widens it silently would kill somebody's training run.

    Widening it is allowed and is meant to be: what this test asks is that it be done in a
    commit that says so.
    """
    attached = sorted(
        properties["ComputeEnvironmentName"].removeprefix("sbsandbox-intern-edullm-")
        for properties in (
            resource["Properties"] for resource in gpu_shape_environments().values()
        )
        if "LaunchTemplate" in properties["ComputeResources"]
    )

    assert attached == sorted(P_FAMILY_PROFILES)
    assert len(gpu_shape_environments()) == len(GPU_SHAPE_PROFILES)


def test_the_two_p_shapes_name_a_version_that_moves_when_the_template_is_edited() -> None:
    """Mutation: replace the ``Fn::GetAtt`` with ``$Latest``.

    Batch resolves ``$Latest`` and ``$Default`` once, when the compute environment is created
    or updated, and documents that a later change to the launch template is not picked up
    until something calls ``UpdateComputeEnvironment``. So the two environments would stay on
    the version they were born with while this file described another one, and the next
    person to raise the volume would watch a green deploy change nothing.

    A concrete version number is a property of the compute environment, so editing the
    launch template changes both environments in the same deploy.
    """
    logical_id = next(iter(launch_templates()))

    for profile in P_FAMILY_PROFILES:
        specification = environment_named(f"sbsandbox-intern-edullm-{profile}")["ComputeResources"][
            "LaunchTemplate"
        ]
        assert specification["LaunchTemplateId"] == {"Ref": logical_id}
        assert specification["Version"] == {"Fn::GetAtt": f"{logical_id}.LatestVersionNumber"}
        assert "$Latest" not in str(specification)
        assert "$Default" not in str(specification)


def test_the_launch_template_leaves_batch_the_settings_batch_owns() -> None:
    """Mutation: put ``InstanceType`` or ``IamInstanceProfile`` in the launch template.

    AWS Batch documents four launch template parameters it ignores outright -- instance type,
    instance role, network interface subnets and instance market options -- and each of those
    is set on the compute environment a few lines away in the same file. A copy here would
    not fail and would not take effect; it would sit in the template as a second, silently
    inoperative answer to a question the environment already answers, and the next reader
    would have no way to tell which one the account is using.
    """
    data = next(iter(launch_templates().values()))["Properties"]["LaunchTemplateData"]

    assert set(data) == {"BlockDeviceMappings", "UserData"}
    for ignored in (
        "InstanceType",
        "IamInstanceProfile",
        "NetworkInterfaces",
        "InstanceMarketOptions",
    ):
        assert ignored not in data
    # ImageId specifically: a launch template AMI takes precedence over Ec2Configuration, so
    # one here would silently override ECS_AL2023_NVIDIA -- and an AMI with no NVIDIA driver
    # runs the job on the CPU at $21.96/hour with nothing erroring anywhere.
    assert "ImageId" not in data


# --------------------------------------------------------------------------------------
# Networking
# --------------------------------------------------------------------------------------


def test_the_network_declares_one_subnet_per_zone_and_no_zone_twice() -> None:
    """Mutation: drop a subnet, or declare two in one zone.

    This used to assert that us-east-1e was absent, and that assertion was correct for as
    long as every backed shape stopped at five zones. p5.48xlarge and p5.4xlarge are offered
    in all six, so 1e stopped being a zone nothing could use and became a sixth of a scarce
    on-demand pool that no environment could ask for.

    What the old test was protecting has not gone away -- it has moved one file over. A
    subnet Batch will consider and can never place into still produces a job in ``RUNNABLE``
    with no error, and the two tests below are now what prevent it: the CPU environment is
    held to the five zones c7i.8xlarge is offered in, and the 1e subnet is importable only by
    an environment whose instance type EC2 offers there.
    """
    subnets = resources_of_type(NETWORK_PATH, "AWS::EC2::Subnet")
    zones = [resource["Properties"]["AvailabilityZone"] for resource in subnets.values()]

    assert sorted(zones) == sorted(DECLARED_ZONES)
    assert len(set(zones)) == len(zones), "two subnets in one zone is not six zones"


def test_the_zone_the_cpu_shape_cannot_use_is_declared_but_not_imported_by_it() -> None:
    """Mutation: add the 1e import to infra/batch-compute.yaml, which deploys clean.

    This is the assertion that replaces the old "1e is absent" one, and it is the half that
    was load-bearing. Declaring the subnet is harmless; handing it to an environment running
    a shape EC2 does not offer in that zone is the silent failure, because Batch does not
    fail a job it cannot place -- it waits, with no error and no statusReason worth reading.

    Asserted against the CPU environment by name rather than against every environment,
    because the GPU shapes file is where an environment legitimately may import it and the
    test below is what governs that.
    """
    cpu = properties_of(COMPUTE_PATH, "AWS::Batch::ComputeEnvironment")["ComputeResources"]
    imported = [entry["Fn::ImportValue"] for entry in cpu["Subnets"]]

    assert not any(name.endswith(ZONE_WITHOUT_THE_INSTANCE_TYPE) for name in imported), (
        "c7i.8xlarge is not offered in us-east-1e, so this import is a zone Batch will "
        "consider and can never place into"
    )
    assert len(imported) == len(INSTANCE_CAPABLE_ZONES)


def test_only_a_shape_offered_in_that_zone_imports_the_us_east_1e_subnet() -> None:
    """Reads every compute template. Mutation: paste the p5 subnet list onto another shape.

    The 1e subnet exists for p5 and for nothing else. Copying a working six-subnet list onto
    the next environment somebody adds is the easiest possible mistake to make and the
    hardest to see afterwards: the deploy succeeds, the environment reports healthy, and the
    only symptom is that some fraction of that shape's jobs wait forever because Batch chose
    a zone the instance type is not sold in.

    ``ZONES_OFFERING_INSTANCE_TYPE`` is a measurement rather than a policy, so backing a
    shape that is offered in 1e means adding it there in the same change as the subnet, which
    is a line a reviewer reads rather than a deploy that quietly works.

    ASKS FOR ONE OFFERED TYPE RATHER THAN FOR ALL OF THEM, WHICH IS THE MULTI-TYPE FORM OF THE
    SAME QUESTION. What makes an imported zone dangerous is that nothing the environment may
    launch can be placed there; a zone that one of two listed types reaches is a zone the
    environment uses. Requiring every type to reach every zone would forbid p5en.48xlarge on
    gpu-8xh100, which is sold in two of the six and costs that environment nothing, while
    still permitting the mistake the test below catches.
    """
    offenders: list[str] = []
    for path in COMPUTE_PATHS:
        for resource in resources_of_type(path, "AWS::Batch::ComputeEnvironment").values():
            properties = resource["Properties"]
            compute = properties["ComputeResources"]
            imports = [entry["Fn::ImportValue"] for entry in compute["Subnets"]]
            if not any(name.endswith(ZONE_WITHOUT_THE_INSTANCE_TYPE) for name in imports):
                continue
            if not set(compute["InstanceTypes"]) & ZONE_1E_CAPABLE_INSTANCE_TYPES:
                offenders.append(
                    f"{path.name}: {properties['ComputeEnvironmentName']} imports the "
                    f"us-east-1e subnet with {sorted(compute['InstanceTypes'])}, none of "
                    "which EC2 offers there"
                )

    assert not offenders, "; ".join(offenders)


def test_every_listed_instance_type_has_a_subnet_it_can_actually_be_placed_in() -> None:
    """Mutation: add p5e.48xlarge to gpu-8xh100, or drop the 1c and 1d subnets from gpu-8xa100.

    THE FAILURE A SECOND INSTANCE TYPE INTRODUCES, AND THE ONE NO ZONE-SIDE RULE CATCHES. The
    test above asks whether each imported zone is reachable by something. This asks the
    converse -- whether each listed type is reachable at all -- and only the converse notices
    a name Batch can never launch, because a type offered in no imported zone subtracts no
    zone from the environment and so leaves every zone-side check passing.

    It is not hypothetical. p5e.48xlarge reads as the natural alternate for gpu-8xh100: it is
    the 141 GB H200 part, it is the name every capacity discussion reaches for, and
    ``describe-instance-type-offerings`` returns nothing for it in any us-east-1 zone.
    Listing it would have looked like a second option and been a line of dead configuration,
    with the environment continuing to fail exactly as it does now and a template that reads
    as though the problem had been addressed.
    """
    offenders: list[str] = []
    for path in COMPUTE_PATHS:
        for resource in resources_of_type(path, "AWS::Batch::ComputeEnvironment").values():
            properties = resource["Properties"]
            compute = properties["ComputeResources"]
            zones = {
                entry["Fn::ImportValue"].rsplit("subnet-", 1)[-1] for entry in compute["Subnets"]
            }
            for instance_type in compute["InstanceTypes"]:
                offered = ZONES_OFFERING_INSTANCE_TYPE.get(instance_type)
                if offered is None:
                    offenders.append(
                        f"{path.name}: {properties['ComputeEnvironmentName']} lists "
                        f"{instance_type}, which has no measured zone list"
                    )
                elif not offered & zones:
                    offenders.append(
                        f"{path.name}: {properties['ComputeEnvironmentName']} lists "
                        f"{instance_type}, which EC2 offers only in {sorted(offered)} and "
                        f"this environment imports {sorted(zones)}"
                    )

    assert not offenders, "; ".join(offenders)


def test_every_environment_that_may_take_the_sixth_zone_actually_takes_it() -> None:
    """Mutation: drop the 1e import, leaving the subnet declared and used by nothing.

    The test above is a prohibition and passes vacuously if no environment imports the 1e
    subnet at all, which is exactly the state this change was made to leave behind. This is
    the matching obligation, so the subnet cannot be quietly stranded: it was added because
    on-demand p5 could not be obtained in any of the other five zones, and a declared subnet
    nothing places into buys none of that back.

    Driven off the instance type rather than off a list of environment names, so backing a
    third p5 shape gets the sixth zone by construction instead of by somebody remembering.

    THE PREDICATE MOVED FROM SUBSET TO INTERSECTION WHEN gpu-8xh100 GAINED A SECOND TYPE, AND
    UNDER THE OLD ONE THIS TEST WOULD HAVE GONE QUIET RATHER THAN RED. ``InstanceTypes`` on
    gpu-8xh100 is no longer a subset of the 1e-capable names, because p5en.48xlarge is not one
    of them, so the environment would simply have stopped being checked -- ``checked`` would
    have fallen to one and only the count below would have caught it. An environment holding a
    type offered in all six zones owes the sixth zone whatever else it also lists.
    """
    every_zone = sorted(
        f"sbsandbox-intern-edullm-batch-subnet-{zone}" for zone in DECLARED_ZONES
    )
    checked = 0
    for resource in resources_of_type(GPU_SHAPES_PATH, "AWS::Batch::ComputeEnvironment").values():
        properties = resource["Properties"]
        compute = properties["ComputeResources"]
        if not set(compute["InstanceTypes"]) & ZONE_1E_CAPABLE_INSTANCE_TYPES:
            continue
        checked += 1
        imported = sorted(entry["Fn::ImportValue"] for entry in compute["Subnets"])
        assert imported == every_zone, (
            f"{properties['ComputeEnvironmentName']} runs "
            f"{compute['InstanceTypes']}, which EC2 offers in all six zones, so all six "
            "belong here"
        )

    assert checked == 2, (
        "expected gpu-8xh100 and gpu-1xh100 to be the p5 environments; a change to that set "
        "is a change to which environments may import the us-east-1e subnet"
    )


def test_the_vpc_is_created_unconditionally_because_the_quota_landed() -> None:
    """Mutation: put the VPC behind a ``Condition``, as the phase plan originally said to.

    That instruction is stale and this test is where it stays stale. The plan was written
    while us-east-1 held five VPCs against a quota of five; the L-F678F1CE increase to 10
    was filed and applied on 2026-07-27 and confirmed by creating a VPC and deleting it. A
    condition would mean a compute environment placed in somebody else's ephemeral VPC, which
    is the dependency the increase removed.
    """
    template = load_template(NETWORK_PATH)

    assert "Conditions" not in template
    for logical_id, resource in template["Resources"].items():
        assert "Condition" not in resource, logical_id
    assert properties_of(NETWORK_PATH, "AWS::EC2::VPC")["CidrBlock"] == "10.20.0.0/16"


def test_the_subnets_are_public_and_routed_without_paying_for_a_nat_gateway() -> None:
    """Mutation: drop ``MapPublicIpOnLaunch``, or drop the default route.

    Either one produces instances that boot, join nothing, and pull nothing, which Batch
    again reports as a job that waits. A NAT gateway would carry the same traffic in the same
    direction for about thirty dollars a month, and the security group's absent ingress is
    what actually keeps these hosts unreachable.
    """
    template = load_template(NETWORK_PATH)
    subnets = resources_of_type(NETWORK_PATH, "AWS::EC2::Subnet")
    _route_id, route = resource_of_type(template, "AWS::EC2::Route")
    associations = resources_of_type(NETWORK_PATH, "AWS::EC2::SubnetRouteTableAssociation")

    assert all(
        resource["Properties"]["MapPublicIpOnLaunch"] is True for resource in subnets.values()
    )
    assert route["Properties"]["DestinationCidrBlock"] == "0.0.0.0/0"
    assert "GatewayId" in route["Properties"]
    # A route to an internet gateway is refused until the gateway is attached, and
    # CloudFormation infers no ordering from a Ref to the gateway itself.
    assert route["DependsOn"]
    assert len(associations) == len(subnets)
    assert not resources_of_type(NETWORK_PATH, "AWS::EC2::NatGateway")


def test_the_security_group_allows_egress_and_no_ingress_at_all() -> None:
    """Mutation: add any ``SecurityGroupIngress`` rule, SSH first among them.

    These hosts are created and destroyed by the scheduler, so a session on one is a session
    on something about to stop existing. Nothing reaches a Batch container host: the ECS
    agent opens outbound connections, and there is no inbound path anybody needs.
    """
    properties = properties_of(NETWORK_PATH, "AWS::EC2::SecurityGroup")

    assert properties["GroupName"] == "sbsandbox-intern-edullm-batch"
    assert "SecurityGroupIngress" not in properties
    # Egress is written out rather than left to the implicit allow-all, so the rule set is
    # visible; omitting it would leave the same access with nothing to read.
    assert properties["SecurityGroupEgress"]
    assert all(rule["IpProtocol"] == "tcp" for rule in properties["SecurityGroupEgress"])


def test_every_security_group_description_uses_only_characters_ec2_accepts() -> None:
    """Mutation: write an apostrophe in any description here, as the first two versions did.

    EC2 accepts a description only from ``a-zA-Z0-9. _-:/()#,@[]+=&;{}!$*``. An apostrophe
    -- the obvious way to write "the compute environment's hosts" or "the ECS agent's
    control channel" -- is not in that set, and nothing catches it before the account does:
    a description is a plain string to YAML, and ``cloudformation validate-template`` checks
    that a document is a template rather than that a property is valid, so both pass.

    This cost two deploys rather than one, and the second is the reason this test is written
    over every description instead of over ``GroupDescription`` alone. The group description
    was fixed, the egress rules were not looked at, and the next run failed the same stack at
    the same resource -- EC2 reports the rule case as "Invalid rule description" against the
    identical character set. A test narrower than the constraint it is checking buys one
    round trip and no more, so this walks the group description and every ingress and egress
    rule description together.

    Each failure rolled ``sbsandbox-intern-edullm-phase3-network`` back to
    ``ROLLBACK_COMPLETE``, cancelling the five subnets, the route table and the gateway
    behind it, and blocking the stack name until it was deleted by hand.
    """
    permitted = set(string.ascii_letters + string.digits + ". _-:/()#,@[]+=&;{}!$*")
    properties = properties_of(NETWORK_PATH, "AWS::EC2::SecurityGroup")

    described = {"GroupDescription": properties["GroupDescription"]}
    for key in ("SecurityGroupEgress", "SecurityGroupIngress"):
        for index, rule in enumerate(properties.get(key, [])):
            if "Description" in rule:
                described[f"{key}[{index}].Description"] = rule["Description"]

    # The group description plus both egress rules. Asserted so that deleting a description
    # does not turn this test into one that checks nothing and still passes.
    assert len(described) == 3

    rejected = {
        where: sorted(set(value) - permitted)
        for where, value in described.items()
        if set(value) - permitted
    }
    assert not rejected, f"EC2 refuses these characters: {rejected}"
    assert all(len(value) < 256 for value in described.values())


# --------------------------------------------------------------------------------------
# Seam: the compute environment against the network stack
# --------------------------------------------------------------------------------------


def test_the_compute_environment_places_into_every_exported_subnet_its_shape_can_use() -> None:
    """Reads BOTH files. Mutation: drop a subnet from the import list, or import an unexport.

    The two templates are separate stacks and the only thing joining them is an export name
    spelled identically in both. An import of an export that does not exist fails the
    deploy, which is the safe half; the unsafe half is a network stack that exports a subnet
    the compute environment never uses, which deploys clean and quietly narrows the zones
    Batch can place into.

    This compared the two lists for equality until 2026-08-03, when the network stack gained
    a us-east-1e subnet that only p5 may use. Equality would now force the CPU environment to
    import a zone c7i.8xlarge is not offered in, which is the opposite failure and a worse
    one. So the comparison is against the exports this shape *can* use, and the narrowing it
    was written to catch is still caught: dropping 1a from the import list still fails here.
    """
    subnets = resources_of_type(NETWORK_PATH, "AWS::EC2::Subnet")
    exported = {
        output["Export"]["Name"]: output["Value"]["Ref"]
        for output in load_template(NETWORK_PATH)["Outputs"].values()
        if "Export" in output and "-subnet-" in output["Export"]["Name"]
    }
    usable = {
        name: logical_id
        for name, logical_id in exported.items()
        if subnets[logical_id]["Properties"]["AvailabilityZone"] in INSTANCE_CAPABLE_ZONES
    }
    imported = [
        entry["Fn::ImportValue"]
        for entry in properties_of(COMPUTE_PATH, "AWS::Batch::ComputeEnvironment")[
            "ComputeResources"
        ]["Subnets"]
    ]

    assert sorted(imported) == sorted(usable)
    assert len(imported) == len(set(imported)) == len(INSTANCE_CAPABLE_ZONES)
    # Every export is either usable by this shape or accounted for as one that is not, so an
    # export nothing imports cannot hide behind the filter above.
    for export_name, logical_id in exported.items():
        zone = subnets[logical_id]["Properties"]["AvailabilityZone"]
        if export_name in usable:
            assert zone in INSTANCE_CAPABLE_ZONES, f"{export_name} is in {zone}"
        else:
            assert zone == ZONE_WITHOUT_THE_INSTANCE_TYPE, f"{export_name} is in {zone}"


def test_the_compute_environment_uses_the_security_group_the_network_stack_exports() -> None:
    """Reads BOTH files. Mutation: import a name the network stack does not export.

    Borrowing a security group somebody else owns would be a rule set that can be edited
    under us, which is the reason this one is created rather than reused.
    """
    exported = {
        output["Export"]["Name"]
        for output in load_template(NETWORK_PATH)["Outputs"].values()
        if "Export" in output
    }
    imported = [
        entry["Fn::ImportValue"]
        for entry in properties_of(COMPUTE_PATH, "AWS::Batch::ComputeEnvironment")[
            "ComputeResources"
        ]["SecurityGroupIds"]
    ]

    assert len(imported) == 1
    assert imported[0] in exported


# --------------------------------------------------------------------------------------
# Seam: the job queue name, in the three files that spell it
# --------------------------------------------------------------------------------------


def rule_queue_arns() -> list[str]:
    pattern = properties_of(EVENTS_PATH, "AWS::Events::Rule")["EventPattern"]
    return [entry["Fn::Sub"] for entry in pattern["detail"]["jobQueue"]]


def states_role_arns_for(action: str) -> list[str]:
    statements = [
        statement
        for policy in role_named(SERVICE_ROLES_PATH, "sbsandbox-intern-edullm-admission-states")[
            "Policies"
        ]
        for statement in policy["PolicyDocument"]["Statement"]
        if action in statement_actions(statement)
    ]
    assert len(statements) == 1, f"{action} belongs in exactly one statement"
    assert statement_actions(statements[0]) == [action]
    return resource_arns(statements[0]["Resource"])


def states_role_submit_arns() -> list[str]:
    return states_role_arns_for("batch:SubmitJob")


def test_the_tag_scope_is_the_submit_scope_because_it_is_the_same_call() -> None:
    """Mutation: widen or narrow one of the two lists and leave the other.

    ``batch:TagResource`` is not a capability to tag Batch resources. It is the half of
    ``SubmitJob`` that AWS bills to a different action name, because every submission
    carries Tags and Batch authorizes tagging-on-creation separately. The two lists
    therefore describe one call against one pair of resources, and the failure mode of
    letting them drift is a role that can submit and cannot -- which the first run through
    the whole path measured, at ``SubmitToBatch``, as a 403 naming the job definition.

    Nothing else in the phase catches it. Each list is well-formed on its own, the queue
    seam below reads only the submit list, and a template test cannot reach the account.
    """
    assert states_role_arns_for("batch:TagResource") == states_role_submit_arns()


def test_a_batch_refusal_tells_the_submitter_what_batch_said() -> None:
    """Mutation: restore the static Cause naming a lineage prefix.

    This refusal happens after WriteIntent and WriteDecision, so the run is recorded as
    admitted and no job exists -- the worst-shaped failure the platform has. The static
    Cause answered it by naming `submission-failure/` in the lineage bucket, which no
    researcher can read, so the only possible next step was asking somebody with
    credentials.

    It cost exactly that on 2026-08-01. A submission was refused with `Evaluate on exit
    condition contains restricted characters` -- a leading asterisk in an EvaluateOnExit
    pattern -- and finding that out took an operator reading the bucket by hand.

    The error text can name an ARN, which is why the workflow masks the cause before it
    reaches a step summary; that masking is asserted in the workflow's own tests. Withholding
    the text instead was the previous answer and it optimised for a leak that masking already
    prevents, at the cost of every refusal being unreadable.
    """
    states = state_machine_definition()["States"]
    failed = states["SubmissionFailed"]

    assert failed["Type"] == "Fail"
    assert failed["Error"] == "BatchSubmissionFailed"
    assert "Cause" not in failed, (
        "a static Cause is back, so a refused submitter is again being pointed at a bucket "
        "they cannot read"
    )
    # Read from the state the Catch populated, not from thin air: SubmitToBatch merges the
    # error output under submission_failure before RecordSubmissionFailure runs, and both
    # paths into this state come through there.
    assert "$.submission_failure.Cause" in failed["CausePath"]


#: The three `statusReason` values Batch will act on automatically. The three it refuses --
#: MISCONFIGURATION:SERVICE_ROLE_PERMISSIONS, ACTION_REQUIRED and UNDETERMINED -- need a
#: person, which is why they are absent from the templates rather than forgotten.
REMEDIABLE_STUCK_REASONS = {
    "CAPACITY:INSUFFICIENT_INSTANCE_CAPACITY",
    "MISCONFIGURATION:COMPUTE_ENVIRONMENT_MAX_RESOURCE",
    "MISCONFIGURATION:JOB_RESOURCE_REQUIREMENT",
}


def test_every_queue_cancels_a_job_stuck_for_every_reason_batch_will_act_on() -> None:
    """Reads EVERY compute file. Mutation: write a sentence in Reason, which broke the deploy.

    A RUNNABLE job Batch cannot place stays queued forever with no notification and no
    terminal state, and from the submitter's side that is identical to waiting its turn.

    THE FIRST ATTEMPT PUT AN EXPLANATORY SENTENCE IN `Reason` AND COST A BROKEN DEPLOY CHAIN
    ON 2026-08-01 -- CloudFormation refused the CPU stack update and skipped the GPU stack,
    the events stack and the state machine behind it. The reference invites that mistake: it
    calls the field "the reason to log for the action being taken", Type String, with no
    allowed values listed. Batch instead matches it against the `statusReason` the job is
    actually stuck with, and only three of those support remediation.

    So this asserts the set rather than the presence. One entry per remediable cause is not
    stylistic: a job stuck for a reason not listed here is not covered by the others, so a
    missing entry is a class of stuck job that still waits forever.

    The thirty minutes is asserted as a floor, because what it must not do is interrupt a
    legitimate wait -- a cold environment took two to three minutes to bring up a g5 -- and
    what it must do is terminate eventually.
    """
    queues = [
        (path, resource["Properties"])
        for path in COMPUTE_PATHS
        for resource in resources_of_type(path, "AWS::Batch::JobQueue").values()
    ]
    assert len(queues) == len(GPU_SHAPE_QUEUE_NAMES) + 2, (
        "every queue needs its own time limit actions, so a queue this loop does not see is "
        "a queue where an unplaceable job waits forever"
    )
    for path, queue in queues:
        name = queue["JobQueueName"]
        actions = queue.get("JobStateTimeLimitActions")

        assert actions, f"{path.name}: {name} leaves an unplaceable job queued forever"
        assert {action["Reason"] for action in actions} == REMEDIABLE_STUCK_REASONS, (
            f"{path.name}: {name} does not cover every reason Batch will act on. Reason is "
            "an enum of statusReason values, not free text -- a sentence here is refused at "
            "deploy time, and a missing value is a stuck job nothing cancels."
        )
        for action in actions:
            # CANCEL rather than TERMINATE: the job never started, so there is nothing
            # running to terminate, and the two produce different lifecycle records.
            assert action["Action"] == "CANCEL"
            assert action["State"] == "RUNNABLE"
            assert action["MaxTimeSeconds"] >= 600, (
                "a limit under ten minutes would cancel runs that were legitimately "
                "waiting for a cold compute environment to scale up"
            )


def test_the_event_rule_matches_the_job_queue_the_compute_stack_creates() -> None:
    """Reads BOTH files. Mutation: rename the queue in one of them.

    This is the worst rename in the phase because it half-works. Submission keeps succeeding
    against the new queue while the rule matches nothing, so jobs run to completion and no
    lifecycle event, attempt or result record is ever written. The run looks fine in Batch
    and vanishes from lineage; nothing errors anywhere.
    """
    created = names_across_compute_templates("AWS::Batch::JobQueue", "JobQueueName")
    matched = {named_after(arn, "job-queue") for arn in rule_queue_arns()}

    assert created == {JOB_QUEUE_NAME, GPU_JOB_QUEUE_NAME, *GPU_SHAPE_QUEUE_NAMES}
    # Set equality in both directions, which is the whole test now that one rule serves every
    # queue. A queue created and not matched is the silent half above. A queue matched and
    # not created is the other direction and is not harmless either: it is how a rule keeps
    # a pattern for a queue somebody deleted, so nothing fails and the next queue to take
    # that name inherits a delivery path nobody meant to grant it.
    assert matched == created
    # An account-wide aws.batch pattern would deliver other teams' job state changes to our
    # recorder, which would then read a foreign job name as one of our run ids.
    pattern = properties_of(EVENTS_PATH, "AWS::Events::Rule")["EventPattern"]
    assert pattern["source"] == ["aws.batch"]
    assert pattern["detail-type"] == ["Batch Job State Change"]


def test_the_queue_the_states_role_may_submit_to_is_the_queue_that_exists() -> None:
    """Reads THREE files. Mutation: rename the queue in any one of them.

    The queue name appears in infra/batch-compute.yaml, in the event pattern, and in the
    admission states role's ``batch:SubmitJob`` scope, and no CloudFormation reference
    connects them. Renaming it without the role fails every submission closed, which is
    survivable; renaming it without the pattern is the silent half above. This compares all
    three against each other rather than each against a constant.
    """
    created = names_across_compute_templates("AWS::Batch::JobQueue", "JobQueueName")
    from_the_rule = {named_after(arn, "job-queue") for arn in rule_queue_arns()}
    granted = states_role_submit_arns()
    from_the_role = {
        name for name in (named_after(arn, "job-queue") for arn in granted) if name is not None
    }

    assert from_the_role == created
    assert from_the_rule == created


def test_the_job_definition_the_states_role_may_submit_is_the_one_that_is_registered() -> None:
    """Reads BOTH files. Mutation: rename the job definition in either.

    ``SubmitJob`` authorizes against the queue and the job definition together, so a rename
    on one side denies every submission -- which fails closed and still costs a live run to
    diagnose. Each name is granted with a trailing wildcard because RegisterJobDefinition
    mints a revision on every deploy and the revision is part of the ARN; an IAM wildcard
    matches ``:`` like any other character, so one ``-run*`` covers the bare definition and
    every revision of it. That used to be spelled as two ARNs each, and the pair is what
    took the rendered policy over IAM's 10240-byte cap on 2026-08-02.

    **A third name the templates do not create, and it is the point of this phase.** An
    accepted run registers a definition of its own so that the digest its manifest declared
    is the image its container is given -- AWS Batch has no submit-time image override, so
    registering is the only mechanism. That definition is minted at admission and appears in
    no template, so this can no longer assert that the role submits only to what the
    templates deploy. What it asserts instead is that the role submits to those two and to
    the minted shape and to nothing else: ``run_`` is what ``job_definition_name`` produces
    and is a prefix neither deployed definition begins with, so a fourth name is still a
    visible edit here.
    """
    registered = names_across_compute_templates("AWS::Batch::JobDefinition", "JobDefinitionName")
    from_the_role = {
        name
        for name in (named_after(arn, "job-definition") for arn in states_role_submit_arns())
        if name is not None
    }
    assert registered == {
        JOB_DEFINITION_NAME,
        GPU_JOB_DEFINITION_NAME,
        *GPU_SHAPE_JOB_DEFINITION_NAMES,
    }
    assert from_the_role == {f"{name}*" for name in registered} | {PER_RUN_JOB_DEFINITION_NAME}
    assert all(name.endswith("*") for name in from_the_role), (
        "a grant on the bare definition name authorizes nothing once a second revision "
        "exists, so every definition this role may submit to is named with a trailing "
        "wildcard -- which covers the revision because an IAM wildcard matches ':'"
    )


# --------------------------------------------------------------------------------------
# Seam: the deployed configuration the validator reads, against what the templates create
# --------------------------------------------------------------------------------------


def test_execution_targets_config_names_exactly_what_the_templates_create() -> None:
    """Reads FIVE files. Mutation: rename anything in config/execution-targets.yaml.

    That file is deployed policy: the validator loads it inside the Lambda and resolves a
    queue, a job definition, two roles and a log group from it. Every one of those names is
    created by a template in this repository, and nothing checks that the two agree at
    deploy time -- a mismatch is an accepted decision whose submission is refused, or worse,
    a binding record naming a log group nobody can read.
    """
    bindings = execution_target_bindings()
    # Which compute template backs which profile, and which IAM template holds its roles.
    # Written out rather than derived, because a derivation would have to guess the pairing
    # from the names -- and the names agreeing is the thing being checked.
    backing = {
        "cpu-32vcpu": (COMPUTE_PATH, BATCH_ROLES_PATH, EXECUTION_ROLE_NAME, WORKLOAD_ROLE_NAME),
        "gpu-1xa10g": (
            GPU_COMPUTE_PATH,
            GPU_BATCH_ROLES_PATH,
            GPU_EXECUTION_ROLE_NAME,
            GPU_WORKLOAD_ROLE_NAME,
        ),
        **{
            profile: (
                GPU_SHAPES_PATH,
                GPU_BATCH_ROLES_PATH,
                GPU_EXECUTION_ROLE_NAME,
                GPU_WORKLOAD_ROLE_NAME,
            )
            for profile in BACKED_GPU_SHAPE_PROFILES
        },
    }

    assert set(bindings) == set(backing), (
        "every backed profile needs a compute template and a role template named here; a "
        "target with neither is a validator resolving names nothing creates"
    )
    # The other direction, which this test could not previously distinguish because the two
    # lists were one list. A shape the template creates and nothing binds is deliberate and
    # has to stay possible; a shape nothing creates and something binds is the defect above.
    assert set(GPU_SHAPE_PROFILES) - set(bindings) == set(UNOBTAINABLE_GPU_SHAPE_PROFILES), (
        "the only compute environments this repository creates without routing to them are "
        "the ones whose instance type the account cannot obtain; withdrawing any other shape "
        "from config/execution-targets.yaml leaves a queue nothing can reach and no record "
        "of why"
    )

    for profile, (compute, roles, execution_name, workload_name) in backing.items():
        binding = bindings[profile]
        execution_role = role_named(roles, execution_name)
        workload_role = role_named(roles, workload_name)
        # Named rather than positional, because one template now carries thirteen queues and
        # thirteen definitions and the profile is what picks the pair out of it. Reading the
        # first of each would compare every shape against gpu-1xt4's names and pass for one.
        queues = {
            resource["Properties"]["JobQueueName"]
            for resource in resources_of_type(compute, "AWS::Batch::JobQueue").values()
        }
        definitions = {
            resource["Properties"]["JobDefinitionName"]
            for resource in resources_of_type(compute, "AWS::Batch::JobDefinition").values()
        }

        assert binding["region"] == "us-east-1", profile
        assert binding["job_queue"] in queues, profile
        assert binding["job_definition"] in definitions, profile
        assert binding["execution_role"] == execution_role["RoleName"], profile
        assert binding["workload_role"] == workload_role["RoleName"], profile
        # Read off the job definition rather than off a AWS::Logs::LogGroup resource, because
        # infra/batch-compute-gpu-shapes.yaml creates no group. It names the one
        # infra/batch-compute-gpu.yaml creates, and the test below is what holds every name
        # used by any container to a group some template declares.
        driver = next(
            resource["Properties"]["ContainerProperties"]["LogConfiguration"]
            for resource in resources_of_type(compute, "AWS::Batch::JobDefinition").values()
            if resource["Properties"]["JobDefinitionName"] == binding["job_definition"]
        )
        assert binding["log_group"] == driver["Options"]["awslogs-group"], profile

    # A queue and a definition apiece, which is what keeps one profile's submissions off
    # another's capacity. The roles and the log group are deliberately shared and are checked
    # for sharing rather than for uniqueness below.
    for field in ("job_queue", "job_definition"):
        values = [binding[field] for binding in bindings.values()]
        assert len(set(values)) == len(values), field

    # THE CPU AND GPU HALVES SHARE NO IDENTITY, WHICH IS THE SEPARATION THAT MATTERS. Their
    # execution roles read different secrets, their workload roles reach different objects,
    # and each execution role is scoped to one log group, so a GPU definition naming a CPU
    # role would hand the training container the wrong output scope and let either principal
    # write into the record of the other's jobs.
    #
    # The fourteen GPU targets share that trio with each other, and that is a decision
    # recorded in config/execution-targets.yaml: they run the same image, resume from the same
    # output prefix and read the same W&B secret, so a role per shape would be fourteen copies
    # of one policy and fourteen log groups would need fourteen grants on the role that
    # already exists.
    for field in ("execution_role", "workload_role", "log_group"):
        cpu_side = bindings["cpu-32vcpu"][field]
        gpu_side = {
            binding[field] for profile, binding in bindings.items() if profile != "cpu-32vcpu"
        }
        assert cpu_side not in gpu_side, field
        assert len(gpu_side) == 1, field


def test_the_log_group_the_config_names_is_the_one_the_container_writes_to() -> None:
    """Reads BOTH halves of one file plus the config. Mutation: change one and not the other.

    The awslogs driver names a group as a string and creates nothing, so a group name that
    does not match the one the log group resource declares means a container that starts and
    logs nowhere -- and a binding record pointing at a group that holds nothing.
    """
    bindings = execution_target_bindings()
    declared = {
        resource["Properties"]["LogGroupName"]
        for path in COMPUTE_PATHS
        for resource in resources_of_type(path, "AWS::Logs::LogGroup").values()
    }
    driven = set()
    for path in COMPUTE_PATHS:
        for resource in resources_of_type(path, "AWS::Batch::JobDefinition").values():
            container = resource["Properties"]["ContainerProperties"]
            name = resource["Properties"]["JobDefinitionName"]

            assert container["LogConfiguration"]["LogDriver"] == "awslogs", name
            driven.add(container["LogConfiguration"]["Options"]["awslogs-group"])

    assert declared == {BATCH_LOG_GROUP, GPU_BATCH_LOG_GROUP}
    # EVERY GROUP A CONTAINER WRITES TO IS ONE SOME TEMPLATE DECLARES, WHICH IS NO LONGER THE
    # SAME FILE. infra/batch-compute-gpu-shapes.yaml creates no group and names the one
    # infra/batch-compute-gpu.yaml creates, and CloudFormation knows nothing about that
    # dependency because the awslogs driver takes a string. So this is the assertion that
    # would fail if the group were renamed on one side, and the cross-stack reference is the
    # reason the two stacks cannot be deleted in either order.
    assert driven == declared
    assert {binding["log_group"] for binding in bindings.values()} == declared


# --------------------------------------------------------------------------------------
# Seam: the submit request Python builds, against what the state machine sends
# --------------------------------------------------------------------------------------


def test_the_searched_field_list_is_every_field_the_python_can_actually_send(
    every_submit_request_field: frozenset[str],
) -> None:
    """The seam test below is only worth the list it searches for.

    Mutation: add a tenth key to ``batch_submit_request`` and not to
    ``SUBMIT_REQUEST_FIELDS``. Without this case the ASL would never be searched for the
    new key, and the pass-through property would be reported for a field nobody checked --
    the same defect the seam test exists to catch, moved one level up into the test's own
    constant.
    """
    assert every_submit_request_field == set(SUBMIT_REQUEST_FIELDS)


def test_submit_to_batch_passes_the_request_through_and_names_no_field_of_it(
    submit_request: dict[str, Any],
    every_submit_request_field: frozenset[str],
) -> None:
    """Reads the ASL and the Python. Mutation: replace it with a ``Parameters`` block.

    A Parameters block reconstructs, in a template nobody unit-tests, a structure
    ``batch_submit_request`` already builds -- and the way it fails is by silently dropping a
    key added on one side and not the other. The mandatory attempt timeout is precisely such
    a key. Passing the whole request through means there is nothing here to fall out of step,
    so the assertion is that this state mentions none of the request's field names at all.

    The planned spelling was ``InputPath`` with no ``Parameters``, which the Step Functions
    validator refuses: "Parameters field is required for resource ARN
    arn:aws:states:::aws-sdk:batch:submitJob". JSONata ``Arguments`` is the shape that both
    validates and enumerates nothing.
    """
    state = state_machine_definition()["States"]["SubmitToBatch"]
    text = json.dumps(state)

    assert "Parameters" not in state
    assert state["Resource"].endswith(":aws-sdk:batch:submitJob")
    # One reference and nothing else, so the request arrives as Python built it. Asserted as
    # the whole value rather than as a substring: a reference wrapped in an expression that
    # reshaped it would satisfy "contains the path" and defeat the point.
    assert state["Arguments"] == "{% $states.input.execution.submit_request %}"
    assert submit_request  # the Python side builds something to be passed through
    # Searched over the union of the recorded list and what the Python actually produced,
    # so a key added on one side alone is still searched for rather than skipped.
    for field in sorted(every_submit_request_field | set(SUBMIT_REQUEST_FIELDS)):
        assert field not in text, f"SubmitToBatch names {field}, so it can drop {field}"


def test_the_container_override_keys_are_keys_the_job_definition_declares(
    submit_request: dict[str, Any],
) -> None:
    """Reads the job definition and the Python. Mutation: rename ``Command`` on either side.

    ``ContainerOverrides`` can only override a key the definition declares. A ``Commands``
    against a ``Command`` is not an error anywhere: Batch takes the override it recognises,
    ignores the one it does not, and runs the definition's default command instead -- so the
    job succeeds while running something nobody asked for.
    """
    container = properties_of(COMPUTE_PATH, "AWS::Batch::JobDefinition")["ContainerProperties"]
    overridden = set(submit_request["ContainerOverrides"])

    assert overridden
    assert overridden <= set(container), (
        f"overrides name keys the job definition does not declare: {overridden - set(container)}"
    )


def test_the_default_command_is_a_list_of_strings_rather_than_yaml_of_some_other_shape() -> None:
    """Mutation: unquote the ``print(...)`` element, which is how it was first written.

    That element contains a colon followed by a space, and unquoted that is YAML's mapping
    syntax rather than text. It parsed as
    ``{'print("...cpu-run': 'no command override was supplied")'}`` -- a dict, in a property
    Batch requires to be a list of strings -- and the stack was refused at change set
    creation with ``AWS::EarlyValidation::PropertyValidation`` and no indication of which
    property was wrong.

    The test above this one already read ``Command`` and did not notice, because it compares
    the override *keys* against the definition's keys and never looks at a value. A key
    whose value is the wrong type is exactly the gap between the two, which is why this is a
    separate test rather than an extra assertion there.

    Types rather than contents: what the default command prints is not a contract, but that
    the three elements are strings is.
    """
    container = properties_of(COMPUTE_PATH, "AWS::Batch::JobDefinition")["ContainerProperties"]
    command = container["Command"]

    assert isinstance(command, list) and command
    offenders = {
        index: type(element).__name__
        for index, element in enumerate(command)
        if not isinstance(element, str)
    }
    assert not offenders, f"Command elements must be strings; these are not: {offenders}"

    # Environment is the other list here whose entries carry free text, and a colon in an
    # unquoted Value would fail the same way.
    for entry in container["Environment"]:
        assert isinstance(entry["Name"], str) and isinstance(entry["Value"], str)


def test_the_binding_record_the_state_machine_writes_is_the_contract_it_claims_to_be() -> None:
    """Reads the ASL and the contract. Mutation: add a field to ``BatchJobBinding``.

    This is the one lineage record whose bytes the template decides, because the Batch job id
    does not exist until the submit has run. Nothing validates it at run time, so a field
    added to the contract and not here is a record that fails validation only when somebody
    reads it back -- long after the job it describes has gone.
    """
    states = state_machine_definition()["States"]
    # The fan-out path writes every field; the single-container path writes every field
    # except array_size, which the contract makes optional. Checked against the contract
    # separately so that a field added to BatchJobBinding and to only one of the two write
    # states fails here rather than on whichever kind of run happens to go first.
    fan_out = states["WriteBindingForFanOut"]["Parameters"]["Body"]
    single = states["WriteBindingForSingleContainer"]["Parameters"]["Body"]
    declared = set(BatchJobBinding.model_fields)

    assert {key.removesuffix(".$") for key in fan_out} == declared
    assert {key.removesuffix(".$") for key in single} == declared - {"array_size"}
    for body in (fan_out, single):
        assert body["schema_version"] == 1
        # The job name is the run id, so the S3 key, the execution name and the Batch job
        # name all carry one identifier and any two disagreeing is visible.
        assert body["batch_job_name.$"] == "$.submission.JobName"
        assert body["run_id.$"] == "$.admission.run_id"


# --------------------------------------------------------------------------------------
# The amended state machine
# --------------------------------------------------------------------------------------


def test_the_validator_payload_is_built_field_by_field_and_never_forwarded() -> None:
    """Mutation: restore ``"Payload.$": "$"``.

    That is what the state said before Phase 3 and it was correct while every field in the
    input was the caller's own claim. It stops being correct once one field is not: the image
    scan findings are read from ECR by this state machine, and a forwarded payload would let
    a caller supply an ``image_scan`` key of its own and declare its own image clean.
    """
    states = state_machine_definition()["States"]
    payload = states["ValidateAndDecide"]["Parameters"]["Payload"]

    assert "Payload.$" not in states["ValidateAndDecide"]["Parameters"]
    assert payload["image_scan.$"] == "$.image_scan"
    # ReadImageScan runs in JSONata since the paged-read fix, so what puts the findings under
    # $.image_scan is an Output merge rather than a ResultPath. The property being held is
    # the same one and it is the reason this line sits in this test: the key the validator
    # reads is written by that state, so a rename on either side is caught here.
    assert states["ReadImageScan"]["Output"].startswith(
        "{% $merge([$states.input, {'image_scan':"
    )
    # Every other field is the caller's and is read from the execution input, one at a time,
    # so a missing one fails here rather than reaching a handler that defaults it.
    assert {key.removesuffix(".$") for key in payload} == {
        "run_id",
        "submitter",
        "approver",
        "approving_environment",
        "approved_manifest_sha256",
        "manifest",
        # Beside the manifest rather than inside it, because the manifest is hashed and the
        # digest is what the approver released. This set is an equality assertion and it
        # still passed when `experiment` was added to the form, the request and the handler
        # but not to the payload -- pinning the set says nothing about whether the set is
        # the right one. test_phase2_admission_handler.py compares it to the fields the
        # handler actually reads, which is the half that catches an omission.
        "experiment",
        "workflow_run",
        # Forwarded so the validator can disagree with it. ReadImageScan reads the scan from
        # the repository this names, and the validator re-derives the same name from
        # manifest.repository against the registry in its own zip -- a check it cannot make
        # unless the value it was read with arrives here too.
        "ecr_repository",
        "image_scan",
    }


def test_the_execution_block_is_carried_through_the_selector_and_never_selected() -> None:
    """Mutation: add ``"execution.$": "$.Payload.execution"`` to the ResultSelector.

    That is the natural spelling and it fails every rejected run. The handler returns
    ``execution`` only when the run is accepted and omits the key otherwise -- which is the
    right shape, because a rejected submission carrying a submit request would be one
    InputPath away from being submitted anyway. A payload template resolves every ``.$``
    reference before the state completes, an unresolvable one is a States.Runtime failure,
    and there is no defaulting form; so selecting it directly breaks the common case in a
    state machine whose whole job is to refuse things, and breaks it after the Lambda has
    already decided correctly.

    The carrier is the answer: ResultSelector keeps the whole payload under a key that
    always exists, and the lift happens on the accepted branch where ``execution`` is
    guaranteed to be there.
    """
    states = state_machine_definition()["States"]
    selector = states["ValidateAndDecide"]["ResultSelector"]
    lift = states["ResolveExecutionTarget"]

    assert "execution.$" not in selector
    assert selector["payload.$"] == "$.Payload"
    assert lift["Type"] == "Pass"
    assert lift["InputPath"] == "$.admission.payload.execution"
    assert lift["ResultPath"] == "$.execution"
    # RegisterJobDefinition rather than SubmitToBatch since an accepted run registers the
    # definition it is executed on. What this line is protecting is unchanged and is not
    # the name: it is that the lift feeds the submission path and that the whole path is
    # downstream of the Choice, so the carrier is only ever read where `execution` is
    # guaranteed to exist. The register state is the first reader of it now, and
    # tests/test_phase5_infrastructure.py is where its own shape is held.
    assert lift["Next"] == "RegisterJobDefinition"
    assert states["RegisterJobDefinition"]["Next"] == "SubmitToBatch"
    # Only reachable from the accepted branch of the Choice, which is what makes the key
    # guaranteed rather than hoped for.
    assert states["AdmissionAccepted"]["Choices"][0]["Next"] == "ResolveExecutionTarget"
    assert states["AdmissionAccepted"]["Default"] == "Rejected"
    # And nothing else reads out of the carrier: the six named keys beside it are the
    # interface, and a second reader of $.admission.payload would be a path that works today
    # and stops working the moment the selector is narrowed.
    carried = [
        value
        for name, state in states.items()
        if name != "ResolveExecutionTarget"
        for value in walk_strings(state)
        if "$.admission.payload" in value
    ]
    assert carried == []


def test_the_image_scan_is_read_before_anything_is_judged_and_fails_open_to_closed() -> None:
    """Mutation: remove the ``Catch``, or route it somewhere other than ValidateAndDecide.

    An image with no scan yet returns ``ScanNotFoundException``. Failing the execution there
    would refuse the run with an error rather than with a decision, and no decision record
    would be written -- so the refusal would exist only in an execution history. Catching to
    the validator puts the error object where the findings would have been, which
    ``image_scan_summary_from_ecr`` reads as None and the policy reads as nobody having
    looked.
    """
    definition = state_machine_definition()
    read = definition["States"]["ReadImageScan"]

    assert definition["StartAt"] == "ReadImageScan"
    assert read["Resource"].endswith(":aws-sdk:ecr:describeImageScanFindings")
    assert read["Arguments"]["ImageId"]["ImageDigest"] == (
        "{% $states.input.manifest.image_digest %}"
    )
    assert read["Next"] == "ValidateAndDecide"
    assert read["Catch"] == [
        {
            "ErrorEquals": ["States.ALL"],
            # The JSONata spelling of "ResultPath": "$.image_scan", which is what this said
            # before the read was paged. The error object still lands where the findings
            # would have been, which is the whole point of the catch.
            "Output": "{% $merge([$states.input, {'image_scan': $states.errorOutput}]) %}",
            "Next": "ValidateAndDecide",
        }
    ]


def test_the_read_asks_ecr_for_as_many_findings_as_it_will_return_in_one_answer() -> None:
    """Mutation: drop ``MaxResults``, which is what this said until 2026-08-01.

    ECR pages ``DescribeImageScanFindings`` and answers a request without ``maxResults``
    with a default page. Measured that day against the digest
    ``sha256:2e79cc82f71db61f385067a803330435c816c1e36c689430d4314d314143af7f`` in
    ``sbsandbox-intern-edullm-olmo-eval-full``: 100 findings of 213, carrying 4 of the
    image's 13 criticals. Every submission against that repository was denied outright, and
    no test said so, because the missing findings are invisible from the shape of the state.

    1000 is ECR's documented ceiling, so this is a whole answer rather than a bigger page.
    There is deliberately no ``NextToken`` loop: the count guard in ``image_scan_verdict``
    already refuses when a finding that gates has not arrived, and it refuses on exactly
    that rather than on a token, which can come back for an image whose every blocking
    finding was read.
    """
    read = state_machine_definition()["States"]["ReadImageScan"]

    assert read["Arguments"]["MaxResults"] == 1000


def test_the_findings_are_narrowed_to_what_the_validator_reads_so_a_full_page_fits() -> None:
    """Mutation: drop the projection and forward ``$states.result`` whole.

    A Step Functions state's payload is capped at 256 KB and an unprojected describe result
    runs about 1 KB per finding, most of it a ``Description`` paragraph and a ``Uri`` nobody
    downstream opens: 209 KB for the 213 findings above, so a full 1000-finding page would
    fail the state with ``States.DataLimitExceeded``. Asking for 1000 without narrowing what
    comes back would trade a silent truncation for a loud one.

    Measured through ``aws stepfunctions test-state`` on the same image: 22 KB projected,
    104 bytes a finding, so a full page is about 104 KB and the state has room for more
    findings than ECR will return at once.

    What the projection keeps is what ``blocking_findings_from_ecr`` reads, which is a
    coupling with a test on the other end of it --
    ``test_a_projected_describe_result_reads_the_same_as_the_whole_one`` runs the mapping
    over a result captured from this state. This half asserts the projection has not
    quietly widened past ``package_name``, because a projection that kept everything would
    pass that test and reintroduce the size problem.
    """
    output = state_machine_definition()["States"]["ReadImageScan"]["Output"]

    assert "|ImageScanFindings.Findings|" in output
    assert "['Description', 'Uri']" in output
    assert "Attributes[Key='package_name']" in output


def test_the_scan_is_read_from_the_repository_the_submission_names_rather_than_a_pinned_one() -> (
    None
):
    """Mutation: put any ECR repository name back as a literal ``RepositoryName``.

    This state spelled ``sbsandbox-intern-edullm-olmo-core`` out. That was invisible while
    one registered repository had a workload profile and fails in the worst available way
    once a second does -- not by refusing the submission, but by admitting the question and
    answering it about the wrong image. The manifest compiles, the digest resolves out of
    the correct repository, the describe is aimed at OLMo-core's, ECR replies
    ``ImageNotFoundException``, the ``Catch`` above routes it into ``$.image_scan`` exactly
    as designed, and the run is denied on unreviewed findings *after* a lead released it.

    Nothing in the suite caught that, and the test above is why: it checks the resource, the
    digest path, the ``Next`` and the ``Catch``, all four of which stay correct. The
    repository name was the one parameter nobody asserted.
    """
    arguments = state_machine_definition()["States"]["ReadImageScan"]["Arguments"]

    # A JSONata state has no ".$" suffix to distinguish a literal from a reference, so the
    # shape this used to assert -- that the plain key is absent -- says nothing here. What
    # separates the two is the "{% ... %}" wrapper, and a literal name is a string without
    # one, so that is what is asserted instead.
    assert arguments["RepositoryName"] == "{% $states.input.ecr_repository %}", (
        "ReadImageScan names an ECR repository literally, so every submission's scan is "
        "read from that one repository whatever the manifest says"
    )


def test_the_binding_write_is_conditional_and_checksummed_like_every_lineage_write() -> None:
    """Mutation: drop ``IfNoneMatch``.

    The lineage bucket denies any write that does not carry it, so dropping it does not
    produce an overwrite -- it produces a refusal on the happy path, and the conflict record
    then says a duplicate was refused when nothing was duplicated.
    """
    states = state_machine_definition()["States"]

    binding_writes = ("WriteBindingForFanOut", "WriteBindingForSingleContainer")
    for name in ("WriteIntent", "WriteDecision", *binding_writes, "RecordSubmissionFailure"):
        parameters = states[name]["Parameters"]
        assert parameters["Bucket"] == LINEAGE_BUCKET
        assert {key: parameters[key] for key in CONDITIONAL_WRITE_PARAMETERS} == (
            CONDITIONAL_WRITE_PARAMETERS
        )
    # Both binding writes, because they are one record written from two states and a
    # conditional write applied to only one of them would leave the other able to overwrite.
    for name in binding_writes:
        assert states[name]["Parameters"]["Key.$"] == (
            "States.Format('binding/{}.json', $.admission.run_id)"
        )
        assert states[name]["Catch"] == [
            {
                "ErrorEquals": ["S3.S3Exception"],
                "ResultPath": "$.write_failure",
                "Next": "RecordConflict",
            }
        ]


def test_a_fan_out_binding_records_its_size_and_a_single_container_omits_the_key() -> None:
    """Mutation: reintroduce a single write state fed by a Pass with ``"Result": null``.

    THE VERSION OF THIS TEST THAT SHIPPED ASSERTED THE DEFECT. It read
    ``states["RecordSingleContainer"]["Result"] is None`` and passed, because that is
    exactly what the template said -- and ``"Result": null`` is precisely the construct
    that broke. Amazon States Language does not distinguish a null ``Result`` from an
    absent one, so the Pass state passed its whole input through and every binding this
    platform wrote carried the entire execution payload where an integer belongs. A test
    that reads the template back and asserts what it finds will agree with any defect
    expressed in the template; only reading the written record against its own contract
    finds this class, which is what the committed capture test now does.

    So this asserts the shape that cannot express the bug: no ``Result`` anywhere on this
    path, and a single container routed to a write state that has no ``array_size`` key at
    all. The contract permits the omission -- ``array_size`` is ``int | None`` with a
    default -- and an omitted key cannot be the wrong type.
    """
    states = state_machine_definition()["States"]
    choice = states["BindingIsFanOut"]

    assert choice["Choices"] == [
        {
            "Variable": "$.execution.submit_request.ArrayProperties",
            "IsPresent": True,
            "Next": "RecordFanOutSize",
        }
    ]
    # Straight to the write, with no Pass in between to get a literal null wrong in.
    assert choice["Default"] == "WriteBindingForSingleContainer"
    assert "RecordSingleContainer" not in states

    fan_out = states["RecordFanOutSize"]
    assert fan_out["InputPath"] == "$.execution.submit_request.ArrayProperties.Size"
    assert fan_out["ResultPath"] == "$.binding_array_size"
    assert fan_out["Next"] == "WriteBindingForFanOut"
    # The one surviving Pass carries no Result either, because it has an InputPath.
    assert "Result" not in fan_out

    fan_out_body = states["WriteBindingForFanOut"]["Parameters"]["Body"]
    single_body = states["WriteBindingForSingleContainer"]["Parameters"]["Body"]

    assert fan_out_body["array_size.$"] == "$.binding_array_size"
    assert not [key for key in single_body if key.startswith("array_size")]
    # Identical in every other respect, so the two states cannot drift into writing
    # different records for the same phase.
    assert set(fan_out_body) - {"array_size.$"} == set(single_body)


def test_a_refused_submission_is_recorded_and_the_execution_fails_saying_so() -> None:
    """Mutation: delete the ``Catch`` on SubmitToBatch.

    Without it a refused submission fails the execution with whatever Batch said and writes
    nothing, so a run that was admitted and never launched leaves a decision record saying
    accepted and no trace of what happened next.
    """
    states = state_machine_definition()["States"]

    assert states["SubmitToBatch"]["Catch"][0]["ErrorEquals"] == ["States.ALL"]
    assert states["SubmitToBatch"]["Catch"][0]["Next"] == "RecordSubmissionFailure"
    assert states["RecordSubmissionFailure"]["Next"] == "SubmissionFailed"
    assert states["SubmissionFailed"]["Type"] == "Fail"
    assert states["SubmissionFailed"]["Error"] == "BatchSubmissionFailed"


def test_every_state_is_reachable_and_every_transition_names_a_real_state() -> None:
    """Mutation: leave the old ``Admitted`` Succeed state behind after rewiring the Choice.

    Step Functions rejects a dangling transition at CreateStateMachine, and deploys an
    unreachable state quietly. An orphaned Admitted would read as a terminal state meaning
    "admitted and then nothing", which is exactly the state this amendment removes.
    """
    definition = state_machine_definition()
    states = definition["States"]
    reachable = {definition["StartAt"]}
    for state in states.values():
        reachable.update(state[key] for key in ("Next", "Default") if key in state)
        reachable.update(choice["Next"] for choice in state.get("Choices", []))
        reachable.update(catch["Next"] for catch in state.get("Catch", []))

    assert sorted(reachable - set(states)) == []
    assert sorted(set(states) - reachable) == []
    assert "Admitted" not in states
    assert states["AdmissionAccepted"]["Choices"][0]["Next"] == "ResolveExecutionTarget"
    assert states["Submitted"] == {"Type": "Succeed"}


# --------------------------------------------------------------------------------------
# Events, the recorder and the alarms
# --------------------------------------------------------------------------------------


def test_the_recorder_is_attached_by_an_event_source_mapping_and_not_by_a_permission() -> None:
    """Mutation: target the Lambda directly from the rule and add an AWS::Lambda::Permission.

    That needs ``lambda:AddPermission`` on the deployer, which the Phase 2 policy excludes
    deliberately: "the deployer creates the validator but may neither run it nor change who
    may run it". Option 2 of D6 adds a capability instead of removing a restriction, and the
    queue buys real retry and dead-letter semantics as a side effect.

    The rule carries two targets since the notifier arrived on it, so the recorder's is
    picked by ``Id`` rather than by being the only one. Which targets exist at all is
    asserted as an exact set in ``tests/test_notifications_infrastructure.py``, so an
    unnoticed third is still caught, and it is caught in the file that names why there are
    two. What this test still holds is the claim in its own name: the recorder is reached
    through a queue and nothing here grants anybody the right to invoke it.
    """
    template = load_template(EVENTS_PATH)
    targets = properties_of(EVENTS_PATH, "AWS::Events::Rule")["Targets"]
    recorder = [target for target in targets if target["Id"] == "lifecycle-queue"]
    mapping = properties_of(EVENTS_PATH, "AWS::Lambda::EventSourceMapping")

    assert not [
        logical_id
        for logical_id, resource in template["Resources"].items()
        if resource["Type"] == "AWS::Lambda::Permission"
    ]
    assert len(recorder) == 1
    assert recorder[0]["Arn"] == {"Fn::GetAtt": ["LifecycleQueue", "Arn"]}
    assert mapping["EventSourceArn"] == {"Fn::GetAtt": ["LifecycleQueue", "Arn"]}
    assert mapping["Enabled"] is True
    # One event per invocation, so the retry unit is the event -- which is the unit the
    # conditional write and the derived event id deduplicate on.
    assert mapping["BatchSize"] == 1


def test_an_undeliverable_or_unprojectable_event_lands_somewhere_a_person_can_read() -> None:
    """Mutation: remove the ``RedrivePolicy``, or the target's ``DeadLetterConfig``.

    Those are two different failures with the same meaning -- something happened to a job and
    no lineage record says so. Without them the event is retried until the queue's retention
    expires and then disappears, leaving a gap nobody can date.
    """
    queues = resources_of_type(EVENTS_PATH, "AWS::SQS::Queue")
    names = {resource["Properties"]["QueueName"] for resource in queues.values()}
    main = next(
        resource
        for resource in queues.values()
        if resource["Properties"]["QueueName"] == LIFECYCLE_QUEUE_NAME
    )
    target = properties_of(EVENTS_PATH, "AWS::Events::Rule")["Targets"][0]

    assert names == {LIFECYCLE_QUEUE_NAME, DEAD_LETTER_QUEUE_NAME}
    assert main["Properties"]["RedrivePolicy"]["maxReceiveCount"] >= 1
    assert main["Properties"]["RedrivePolicy"]["deadLetterTargetArn"] == {
        "Fn::GetAtt": ["LifecycleDeadLetterQueue", "Arn"]
    }
    assert target["DeadLetterConfig"]["Arn"] == {
        "Fn::GetAtt": ["LifecycleDeadLetterQueue", "Arn"]
    }


def test_the_queue_accepts_deliveries_only_from_our_own_rule_in_our_own_account() -> None:
    """Mutation: drop the ``aws:SourceArn`` or ``aws:SourceAccount`` condition.

    Without the first, any rule in this shared account could feed our recorder foreign job
    state changes. Without the second, a rule in somebody else's account could, which is the
    confused-deputy shape a resource policy naming a service principal always has.
    """
    document = properties_of(EVENTS_PATH, "AWS::SQS::QueuePolicy")["PolicyDocument"]
    statement = document["Statement"][0]

    assert len(document["Statement"]) == 1
    assert statement["Effect"] == "Allow"
    assert statement["Principal"] == {"Service": "events.amazonaws.com"}
    assert statement_actions(statement) == ["sqs:SendMessage"]
    assert statement["Condition"]["ArnEquals"]["aws:SourceArn"] == {
        "Fn::GetAtt": ["LifecycleRule", "Arn"]
    }
    assert statement["Condition"]["StringEquals"]["aws:SourceAccount"] == {"Ref": "AWS::AccountId"}


def test_the_recorder_is_pinned_to_a_versioned_artifact_object() -> None:
    """Mutation: remove ``S3ObjectVersion``.

    Without it, re-uploading a new zip to the same key leaves this resource byte-identical,
    the change set comes back empty, and a deploy that reports success keeps running the old
    projection code.
    """
    properties = properties_of(EVENTS_PATH, "AWS::Lambda::Function")

    assert properties["FunctionName"] == RECORDER_FUNCTION_NAME
    assert properties["Runtime"] == "python3.12"
    assert properties["Handler"] == "edullm_platform.lifecycle_handler.handler"
    assert properties["Code"]["S3Key"].endswith(".zip")
    assert properties["Code"]["S3ObjectVersion"]
    # The visibility timeout has to clear the function timeout, or a message becomes visible
    # again while the recorder is still working on it and the same event is projected twice.
    queues = resources_of_type(EVENTS_PATH, "AWS::SQS::Queue")
    main = next(
        resource
        for resource in queues.values()
        if resource["Properties"]["QueueName"] == LIFECYCLE_QUEUE_NAME
    )
    assert main["Properties"]["VisibilityTimeout"] >= properties["Timeout"]


def test_every_alarm_watches_a_metric_the_deployed_services_actually_publish() -> None:
    """Mutation: add an alarm on an ``AWS/Batch`` metric.

    The phase plan asks for a queue-wait alarm and AWS Batch publishes no CloudWatch metric
    for queue depth or job state -- the documented ways to read RUNNABLE are
    GetJobQueueSnapshot, ListJobs and the EventBridge stream, none of which an alarm can
    reach. An alarm on a metric that is never published sits in INSUFFICIENT_DATA forever,
    which reads as green, so this asserts every alarm names a namespace something here emits.
    """
    alarms = resources_of_type(EVENTS_PATH, "AWS::CloudWatch::Alarm")
    namespaces = {resource["Properties"]["Namespace"] for resource in alarms.values()}

    assert len(alarms) == 3
    assert namespaces == {"AWS/SQS", "AWS/Lambda"}
    for logical_id, resource in alarms.items():
        properties = resource["Properties"]
        assert properties["AlarmName"].startswith("sbsandbox-intern-edullm-batch-"), logical_id
        assert properties["AlarmDescription"], logical_id
        # An empty queue publishes no datapoints, and an alarm that read that as a breach
        # would fire continuously while nothing was wrong.
        assert properties["TreatMissingData"] == "notBreaching", logical_id


# --------------------------------------------------------------------------------------
# The four new roles
# --------------------------------------------------------------------------------------


def test_the_workload_role_writes_only_under_a_runs_prefix_of_the_outputs_bucket() -> None:
    """Mutation: widen the resource to the whole outputs bucket, or drop the runs segment.

    THE SCOPE THIS ASSERTS IS THE WIDE ONE, AND THE TEST SAYS SO RATHER THAN IMPLYING
    OTHERWISE. Open decision 2 was answered on 2026-07-28 in favour of ``teams/*/runs/*``,
    so a workload may write under any team's prefix and under any run id including one that
    belongs to somebody else. That is a recorded limitation, and the Phase 4 isolation
    criterion is a gap because of it.

    What is still worth holding, and what this therefore checks, is the part that is real:
    the grant reaches only the outputs bucket, only below ``teams/``, and only below a
    ``runs/`` segment. Widening it to the bucket would let a workload write over another
    project's data entirely, and dropping ``runs/`` would put it beside the prefix layout
    that ``contracts/results.py::output_prefix`` is the single author of -- which is what
    makes tightening this later an IAM change rather than a migration of keys already
    written into lineage records nothing rewrites.

    THE LISTING HALF IS ABOUT THIS BUCKET AND NOT ABOUT LISTING, which it did not have to
    distinguish until the role could list a second one. It counted every ``s3:ListBucket``
    statement on the role and required exactly one, which was the same assertion for as long
    as the outputs bucket was the only bucket in reach. It stopped being the same assertion
    when ``read-the-dataset-airlock`` was added, and the difference is what the condition is
    for: the outputs bucket is partitioned by team, so the prefix *is* the partition, while
    the two airlock buckets have no team dimension to scope by. The same narrowing was made
    for the same reason in ``tests/test_phase5_team_isolation.py``; the airlock's own listing
    is asserted in ``tests/test_workload_dataset_reach.py`` rather than left implied.
    """
    role = role_named(BATCH_ROLES_PATH, WORKLOAD_ROLE_NAME)
    statements = [
        statement
        for policy in role["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
    ]
    written = [
        arn
        for statement in statements
        if "s3:PutObject" in statement_actions(statement)
        for arn in resource_arns(statement["Resource"])
    ]

    assert written == [f"arn:${{AWS::Partition}}:s3:::{OUTPUTS_BUCKET}/teams/*/runs/*"]
    # Not the bucket root, which is the widening this still refuses.
    assert not any(arn.endswith(f"{OUTPUTS_BUCKET}/*") for arn in written)
    # Listing is a bucket-level action that no object ARN can scope, so the prefix condition
    # is the only thing keeping it below the same layout.
    listing = [
        statement
        for statement in statements
        if "s3:ListBucket" in statement_actions(statement)
        and any(
            arn.endswith(f":{OUTPUTS_BUCKET}") for arn in resource_arns(statement["Resource"])
        )
    ]
    assert len(listing) == 1
    assert listing[0]["Condition"]["StringLike"]["s3:prefix"] == "teams/*/runs/*"


def test_the_workload_role_can_neither_reach_lineage_nor_start_anything() -> None:
    """Mutation: add ``s3:PutObject`` on the lineage bucket, or any ``batch:`` action.

    The container is what an untrusted command runs inside. It may write its results and it
    may not start anything, stop anything, publish anything, or put a byte into the store
    whose entire property is that only the admission state machine writes to it.
    """
    role = role_named(BATCH_ROLES_PATH, WORKLOAD_ROLE_NAME)
    actions = role_actions(BATCH_ROLES_PATH, WORKLOAD_ROLE_NAME)
    reachable = list(walk_strings(role["Policies"]))

    assert actions
    assert all(action.startswith("s3:") for action in actions)
    assert not [value for value in reachable if LINEAGE_BUCKET in value]
    assert not [
        action
        for action in actions
        if action.startswith(("batch:", "states:", "ecr:", "iam:", "logs:"))
    ]


def test_only_the_execution_and_instance_roles_may_pull_the_image() -> None:
    """Mutation: give the workload role ``ecr:BatchGetImage``.

    The registry credentials belong to the identity that starts the task and to the host that
    runs it, never to the process inside the container. A workload that can pull can also
    enumerate what else this project has published.
    """
    pulling = {
        name
        for name in (EXECUTION_ROLE_NAME, WORKLOAD_ROLE_NAME, INSTANCE_ROLE_NAME)
        if [action for action in role_actions(BATCH_ROLES_PATH, name) if action.startswith("ecr:")]
    }

    assert pulling == {EXECUTION_ROLE_NAME, INSTANCE_ROLE_NAME}
    for name in pulling:
        actions = [
            action for action in role_actions(BATCH_ROLES_PATH, name) if action.startswith("ecr:")
        ]
        # Read-only. ecr:PutImage and the rest of the push path belong to the publisher role
        # and to no identity that runs a container.
        assert set(actions) <= {
            "ecr:BatchCheckLayerAvailability",
            "ecr:BatchGetImage",
            "ecr:GetAuthorizationToken",
            "ecr:GetDownloadUrlForLayer",
        }


def test_the_recorder_role_writes_lineage_and_cannot_make_anything_happen() -> None:
    """Mutation: add ``batch:SubmitJob``, ``batch:TerminateJob`` or ``s3:DeleteObject``.

    The recorder is reached by an EventBridge delivery rather than by an execution somebody
    approved, so it is the component furthest from the gate. The only thing it may do with
    that distance is append a record of something that already happened.

    THE LISTING IS PART OF OBSERVING RATHER THAN AN EXCEPTION TO IT. ``s3:ListBucket`` on
    the outputs bucket is how ``lifecycle_projection`` says which checkpoints a run left,
    and enumerating objects some other principal already wrote makes nothing happen. It
    starts no job, changes no state, and nothing downstream can tell that it was called.
    The property this test holds is that the recorder cannot cause an effect, which is a
    statement about writes and about reaching the schedulers, so a bounded read passes it
    unchanged. Widening the list without saying that would leave the next reader unable to
    tell an argued grant from a drifted one.

    Bounded is doing work in that sentence, so the condition is asserted beside the action.
    ``s3:ListBucket`` is authorized against the bucket ARN, so an unconditioned grant
    enumerates every team's output and the claim above would be false. The prefix pattern
    is the one ``checkpoints_under`` sends, which is the path of ``EDULLM_CHECKPOINT_DIR``.

    ``s3:GetObject`` is absent, and it is the read the exact list still refuses. The
    projection reads keys, sizes and write times and never an object's contents, so the
    grant would buy nothing and would let the recorder read what every run produced.
    """
    actions = role_actions(LIFECYCLE_ROLE_PATH, LIFECYCLE_ROLE_NAME)
    s3_actions = [action for action in actions if action.startswith("s3:")]
    listing = [
        statement
        for policy in role_named(LIFECYCLE_ROLE_PATH, LIFECYCLE_ROLE_NAME)["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
        if "s3:ListBucket" in statement_actions(statement)
    ]

    assert s3_actions == ["s3:PutObject", "s3:ListBucket"]
    assert "sqs:ReceiveMessage" in actions
    assert "sqs:DeleteMessage" in actions
    assert len(listing) == 1
    assert resource_arns(listing[0]["Resource"]) == [
        f"arn:${{AWS::Partition}}:s3:::{OUTPUTS_BUCKET}"
    ]
    assert listing[0]["Condition"]["StringLike"]["s3:prefix"] == "teams/*/runs/*/checkpoints/*"


def test_the_recorder_role_holds_no_batch_action_at_all() -> None:
    """Mutation: re-add ``batch:DescribeJobs``, which the plan asked for and argued wrongly.

    The plan justified the grant as "the recorder needs the job's attempt detail", on the
    premise that a ``Batch Job State Change`` detail does not carry the attempts array. It
    does, and it is where every instant the recorder writes comes from -- which the
    companion assertion below checks, so this pair reads both the template and the code
    rather than restating a claim about either.

    Reading those from a describe would not merely be redundant. A describe answers with
    the job as it is when asked, so a redelivered event would project from different inputs
    than its first delivery, produce different bytes under the same derived key, and be
    refused by the conditional write -- keeping whichever projection happened to arrive
    first. Derived-key deduplication is the whole of "event duplicates do not create
    conflicting terminal state", and that grant is how it would be lost.
    """
    actions = role_actions(LIFECYCLE_ROLE_PATH, LIFECYCLE_ROLE_NAME)

    assert [action for action in actions if action.startswith("batch:")] == []
    # The other side of it: the attempt really does come out of the event, so the grant is
    # unnecessary rather than merely undesirable. A projection that started calling a
    # describe would fail here before it failed in the account.
    envelope = terminal_envelope()
    attempts = envelope["detail"]["attempts"]
    projection = project_batch_event(envelope)

    assert projection.attempt is not None
    assert projection.attempt.started_at.timestamp() * 1000 == attempts[0]["startedAt"]
    assert projection.attempt.ended_at.timestamp() * 1000 == attempts[0]["stoppedAt"]
    # The exit code the plan named is in the event too, and no Phase 0 contract has a field
    # for it, so it is captured evidence rather than something a describe would supply.
    assert "exitCode" in attempts[0]["container"]
    assert "exit_code" not in SchedulerAttempt.model_fields


def test_the_mapping_honours_the_partial_response_the_recorder_returns() -> None:
    """Reads the handler and the template. Mutation: drop ``FunctionResponseTypes``.

    The handler answers a partially-failed batch with a per-message verdict list. Lambda
    discards that answer entirely unless the event source mapping declares the response
    type: without it a returned list is an ordinary successful return, every message in the
    batch is deleted, and the failed ones are lost with no retry and no dead-letter.

    At the ``BatchSize: 1`` this mapping also sets, the two configurations behave
    identically -- "some failed" and "all failed" are the same event, and the handler
    raises. The two halves were designed apart and would agree only by that coincidence,
    which is what makes this worth pinning: the next person to raise the batch size would
    change one number and turn a lossless path into a lossy one, with nothing failing.

    Both sides are read rather than asserted. The key comes off an invocation the handler
    actually answers, and the response type off the property the template actually sets.
    """
    answered = handler(
        {
            "Records": [
                {"messageId": "unreadable", "body": "not an EventBridge envelope"},
                {"messageId": "projectable", "body": json.dumps(terminal_envelope())},
            ]
        },
        store=AcceptingStore(),
    )
    mapping = properties_of(EVENTS_PATH, "AWS::Lambda::EventSourceMapping")

    assert list(answered) == [BATCH_ITEM_FAILURES_KEY]
    assert answered[BATCH_ITEM_FAILURES_KEY] == [{"itemIdentifier": "unreadable"}]
    assert mapping["FunctionResponseTypes"] == [BATCH_ITEM_FAILURES_RESPONSE_TYPE]
    assert mapping["BatchSize"] == 1


def test_the_recorder_writes_only_the_four_prefixes_this_phase_records() -> None:
    """Mutation: widen the resource to ``sbsandbox-intern-edullm-lineage/*``.

    That would let the recorder write an intent or a decision record, which are the two
    things the admission state machine writes and the recorder must never be able to forge.
    """
    statements = [
        statement
        for policy in role_named(LIFECYCLE_ROLE_PATH, LIFECYCLE_ROLE_NAME)["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
        if statement_actions(statement) == ["s3:PutObject"]
    ]

    assert len(statements) == 1
    prefixes = {
        arn.split(f"{LINEAGE_BUCKET}/", 1)[1] for arn in resource_arns(statements[0]["Resource"])
    }
    assert prefixes == {"binding/*", "events/*", "attempt/*", "result/*"}


def test_the_recorder_cannot_drain_the_queue_that_records_its_own_failures() -> None:
    """Mutation: add the dead-letter queue ARN to the SQS statement.

    An event that failed projection is meant to sit in the dead-letter queue until a person
    looks. A recorder that could receive and delete from it could erase the evidence that it
    failed, which is the one thing the queue exists to preserve.
    """
    reachable = [
        arn
        for policy in role_named(LIFECYCLE_ROLE_PATH, LIFECYCLE_ROLE_NAME)["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
        if any(action.startswith("sqs:") for action in statement_actions(statement))
        for arn in resource_arns(statement["Resource"])
    ]

    assert len(reachable) == 1
    assert reachable[0].endswith(f":{LIFECYCLE_QUEUE_NAME}")
    assert not [arn for arn in reachable if arn.endswith(DEAD_LETTER_QUEUE_NAME)]


def test_the_instance_role_can_only_join_the_cluster_this_environment_creates() -> None:
    """Reads BOTH files. Mutation: rename the compute environment in infra/batch-compute.yaml.

    Batch names the ECS cluster it manages ``AWSBatch-<compute environment>-<id>``, so the
    environment's name is what makes the cluster scope writable at all. A rename on one side
    produces instances that register nowhere and a queue whose jobs sit in RUNNABLE -- the
    same silent half-failure the job queue name has.
    """
    created = properties_of(COMPUTE_PATH, "AWS::Batch::ComputeEnvironment")[
        "ComputeEnvironmentName"
    ]
    clusters = [
        arn
        for policy in role_named(BATCH_ROLES_PATH, INSTANCE_ROLE_NAME)["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
        for arn in resource_arns(statement["Resource"])
        if ":cluster/" in arn
    ]

    assert created == COMPUTE_ENVIRONMENT_NAME
    assert len(clusters) == 1
    assert clusters[0].endswith(f":cluster/AWSBatch-{created}-*")


def test_the_instance_profile_wraps_the_role_the_compute_environment_names() -> None:
    """Reads BOTH files. Mutation: rename the instance profile in either.

    An EC2 instance holds a role only through an instance profile, and Batch takes the
    profile's ARN. A name that does not exist fails CreateComputeEnvironment with a message
    about the profile and not about the rename.
    """
    profile = properties_of(BATCH_ROLES_PATH, "AWS::IAM::InstanceProfile")
    named = properties_of(COMPUTE_PATH, "AWS::Batch::ComputeEnvironment")["ComputeResources"][
        "InstanceRole"
    ]["Fn::Sub"]

    assert profile["InstanceProfileName"] == INSTANCE_ROLE_NAME
    assert profile["Roles"] == [{"Ref": "BatchInstanceRole"}]
    assert named.endswith(f":instance-profile/{profile['InstanceProfileName']}")


def test_the_states_role_gains_batch_and_ecr_reads_and_no_way_to_stop_a_job() -> None:
    """Mutation: add ``batch:TerminateJob`` to the admission states role.

    Cancellation is its own path with its own principal. An admission execution that could
    also stop a job could end a run whose decision it had just recorded as accepted, and
    nothing in the lineage store would say why.
    """
    actions = [
        action
        for policy in role_named(SERVICE_ROLES_PATH, "sbsandbox-intern-edullm-admission-states")[
            "Policies"
        ]
        for statement in policy["PolicyDocument"]["Statement"]
        for action in statement_actions(statement)
    ]

    # Three Batch actions, and only the first is a capability of its own.
    # batch_submit_request always sends Tags, and Batch authorizes tagging-on-creation
    # under its own action name, so a role holding SubmitJob without TagResource is refused
    # every submission -- measured on the first run through the whole path, which reached
    # SubmitToBatch and got a 403 naming the job definition. RegisterJobDefinition arrived
    # later, because Batch has no submit-time image override and it is the only mechanism
    # that can put a run on the digest its manifest declared; its scope, the iam:PassRole
    # that call needs, and the deregister verb it deliberately does not come with are
    # asserted in tests/test_phase5_infrastructure.py.
    #
    # Asserted as an exact list so a fourth action cannot arrive unnoticed on the strength
    # of these three having been allowed. That is the property being re-armed here, and it
    # is why the list is extended rather than turned into a subset check.
    assert [action for action in actions if action.startswith("batch:")] == [
        "batch:SubmitJob",
        "batch:TagResource",
        "batch:RegisterJobDefinition",
    ]
    assert [action for action in actions if action.startswith("ecr:")] == [
        "ecr:DescribeImageScanFindings"
    ]
    # UntagResource joins the two that were already refused. The tags are lineage: a
    # principal that could remove them could detach a running job from the run that paid
    # for it, which is the same harm as terminating one and harder to see afterwards.
    # DeregisterJobDefinition replaces RegisterJobDefinition in this set for the reason
    # tests/test_phase5_infrastructure.py gives: revisions accumulate against no quota, so
    # nothing needs the verb, and a role that could retire the definition a running job is
    # bound to could make a completed run unreadable.
    assert not {
        "batch:TerminateJob",
        "batch:CancelJob",
        "batch:DeregisterJobDefinition",
        "batch:UntagResource",
    } & set(actions)


# --------------------------------------------------------------------------------------
# Seam: the one ECR repository these roles name, against the registry that maps them all
# --------------------------------------------------------------------------------------


def submittable_ecr_repositories() -> dict[str, str]:
    """Every repository a submission can actually name, mapped to where its images live.

    Submittable is the intersection of two files, because it takes both to reach Batch.
    ``config/repositories.yaml`` is what gives a repository an ECR repository at all, and
    ``config/workload-catalog.yaml`` is what gives it a profile a manifest can name -- a
    submission naming a repository with no workload profile cannot be compiled. Two
    repositories are registered and one of them has a profile, and that one-member set is
    the only reason the repository name hardcoded in four places has never been wrong.

    Duplicated verbatim in ``tests/test_phase3_execution.py`` rather than lifted into
    ``tests/infrastructure_support.py``. Both modules already load both of these files, and
    three lines repeated once is a smaller thing to keep true than a shared support module
    that neither of them owns.
    """
    registry = load_yaml(REPOSITORY_REGISTRY_PATH, RepositoryRegistry)
    catalog = load_yaml(WORKLOAD_CATALOG_PATH, WorkloadCatalog)
    named = {workload.repository for workload in catalog.workloads}
    submittable = {
        entry.repository: entry.ecr_repository
        for entry in registry.repositories
        if entry.repository in named
    }
    assert submittable, "no registered repository has a workload profile to be named by"
    return submittable


def arn_covers(granted: str, ecr_repository: str) -> bool:
    """Whether one ``Resource`` entry authorizes an action against one ECR repository.

    IAM's ``*`` is honoured because IAM honours it. The fix these two tests exist to force
    may well be a single scope over the project prefix rather than an entry per repository
    -- infra/iam/image-resolver-role.yaml is already written that way -- and a comparison
    that accepted only exact names would go red against a role that genuinely reaches
    everything. That is the shape of failure somebody closes by editing the expectation.
    """
    if granted == "*":
        return True
    if ":repository/" not in granted:
        return False
    return fnmatchcase(ecr_repository, granted.rsplit(":repository/", 1)[1])


def image_pull_arns(path: Path, role_name: str) -> list[str]:
    """Every ``Resource`` entry the statements granting one role a pull action name."""
    return [
        arn
        for policy in role_named(path, role_name)["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
        if IMAGE_PULL_ACTIONS.intersection(statement_actions(statement))
        for arn in resource_arns(statement["Resource"])
    ]


def test_the_states_role_may_read_a_scan_for_every_submittable_repository() -> None:
    """Reads BOTH sides. Mutation: give a second registered repository a workload profile
    and leave this grant scoped to the first.

    Modelled on ``test_the_publisher_role_may_push_to_every_registered_destination`` in
    tests/test_repository_registry.py, and asking that question one step further down the
    path. That one compares a grant against every *registered* repository, because a
    registration is all publishing needs. This compares one against every *submittable*
    repository, because reading a scan needs a submission to exist to read it for -- and
    the day those two sets coincide is the day this grant is wrong.

    This module already unwraps ``Resource`` for the S3, SQS, Batch and ECS statements it
    checks, and the ECR statement is the one it skipped, which is how a scope naming one
    repository survived a second registration landing in config/repositories.yaml.

    The denial never reaches anybody as a denial. ``ReadImageScan`` catches ``States.ALL``,
    so an AccessDenied arrives at the validator in the same branch an unscanned image does,
    and ``image_scan_is_reviewed`` consults config/image-exceptions.yaml before it looks at
    the summary at all -- so a digest with an exception is admitted on a scan nobody read.
    """
    granted = states_role_arns_for("ecr:DescribeImageScanFindings")
    scoped_to = sorted(arn.rsplit(":repository/", 1)[-1] for arn in granted)
    unreadable = sorted(
        f"{repository} (images in {ecr_repository})"
        for repository, ecr_repository in submittable_ecr_repositories().items()
        if not any(arn_covers(arn, ecr_repository) for arn in granted)
    )

    assert not unreadable, (
        "the admission states role may read scan findings for "
        f"{', '.join(scoped_to)} and no other repository, which leaves "
        f"{', '.join(unreadable)} uncovered. infra/iam/admission-service-roles.yaml, the "
        "ecr:DescribeImageScanFindings statement's Resource, is what would have to change."
    )


def test_every_role_that_pulls_an_image_may_pull_from_every_submittable_repository() -> None:
    """Reads THREE files. Mutation: give a second registered repository a workload profile
    and leave either roles file scoped to the first.

    The execution role is ECS's identity while it starts the task and the instance role is
    the agent's on the host, and both roles are spelled twice, once per compute stack. All
    four are compared in one pass so the GPU trio cannot drift from the CPU one: widening
    infra/iam/batch-roles.yaml alone leaves every GPU submission unable to start, and a
    test that read one file would report the seam as covered.

    THIS IS THE ONE THAT FAILS LAST AND READS LEAST LIKE ITS CAUSE. Everything upstream has
    already succeeded by the time it fires -- the scan was read, the decision was recorded
    as accepted in an immutable lineage store, the job definition registered, the job was
    submitted, the queue found capacity and an instance scaled up and joined the cluster.
    What arrives is a ``CannotPullContainerError`` inside a job that has already cost money,
    naming a registry path rather than a policy, and it reproduces identically on every
    resubmission -- so it reads as a broken image rather than as a repository whose images
    no identity in this account was ever authorised to fetch.
    """
    submittable = submittable_ecr_repositories()
    unreachable = sorted(
        f"{repository} (images in {ecr_repository}) is unreachable by {role_name} in "
        f"{path.relative_to(PROJECT_ROOT)}"
        for path, role_name in IMAGE_PULLING_ROLES
        for repository, ecr_repository in submittable.items()
        if not any(arn_covers(arn, ecr_repository) for arn in image_pull_arns(path, role_name))
    )

    assert not unreachable, (
        "a container cannot start from an image the roles that fetch it may not pull, and "
        "these grants do not cover every repository a submission can name: "
        + "; ".join(unreachable)
    )


# --------------------------------------------------------------------------------------
# House rules that apply to every template in the phase
# --------------------------------------------------------------------------------------


def test_every_phase3_role_carries_the_permissions_boundary_and_a_capped_session() -> None:
    """Mutation: drop the ``PermissionsBoundary``.

    iam:CreateRole is denied outright unless the request carries this exact boundary, so a
    template that omits it does not create a weaker role -- it fails. Asserting it here means
    the failure is a red test rather than a laptop deploy that stops halfway.
    """
    roles = [
        role
        for path in (BATCH_ROLES_PATH, LIFECYCLE_ROLE_PATH)
        for role in iam_roles(load_template(path))
    ]

    assert [role["RoleName"] for role in roles] == [
        EXECUTION_ROLE_NAME,
        WORKLOAD_ROLE_NAME,
        INSTANCE_ROLE_NAME,
        LIFECYCLE_ROLE_NAME,
    ]
    for role in roles:
        assert role["PermissionsBoundary"] == BOUNDARY
        assert role["MaxSessionDuration"] <= 3600
        assert role["Policies"]
        assert "ManagedPolicyArns" not in role


def test_no_phase3_template_uses_a_managed_policy_it_could_never_amend() -> None:
    """Mutation: replace an inline policy with an ``AWS::IAM::ManagedPolicy``.

    InternSandboxBoundary denies ``iam:CreatePolicyVersion`` on every policy, so a customer
    managed policy here is a one-way door: it can be created once and never amended, and the
    first permission change fails the stack update permanently.
    """
    for path in PHASE3_TEMPLATE_PATHS:
        for logical_id, resource in load_template(path).get("Resources", {}).items():
            assert resource.get("Type") != "AWS::IAM::ManagedPolicy", (
                f"{path.relative_to(PROJECT_ROOT)}: {logical_id}"
            )


def test_no_ci_deployed_phase3_template_creates_an_iam_resource() -> None:
    """Mutation: move a role out of infra/iam/ into one of the CI templates.

    The deployer holds no ``iam:CreateRole``, so this fails at deploy time either way -- but
    it fails at InsufficientCapabilities or at an AccessDenied minutes into a rollback,
    rather than here.
    """
    for path in CI_DEPLOYED_TEMPLATE_PATHS:
        for logical_id, resource in load_template(path)["Resources"].items():
            assert not str(resource["Type"]).startswith("AWS::IAM::"), (
                f"{path.relative_to(PROJECT_ROOT)}: {logical_id}"
            )
    # A queue policy is a resource policy and not an IAM entity, which is what lets the
    # events stack deploy with no capability acknowledgement at all.
    assert resources_of_type(EVENTS_PATH, "AWS::SQS::QueuePolicy")


def test_no_phase3_template_declares_a_cloudformation_parameter() -> None:
    """Mutation: parameterise the subnet IDs instead of importing them.

    Names in this repository are hardcoded literals. A parameter would let the same template
    deploy against infrastructure no committed file describes, which is how a stack ends up
    pointing at somebody else's VPC. The subnet and security group IDs are the one class of
    value no file can spell, and they cross the boundary as exports rather than as arguments.
    """
    for path in PHASE3_TEMPLATE_PATHS:
        assert "Parameters" not in load_template(path), path.relative_to(PROJECT_ROOT)


def test_no_phase3_template_carries_an_aws_account_id_literal() -> None:
    """Mutation: write the account into the image reference instead of using ``Fn::Sub``.

    Every ARN written here reaches the account through the pseudo-parameter, which is what
    keeps the account out of a public repository and lets the same template work in any
    account that has the boundary.
    """
    for path in PHASE3_TEMPLATE_PATHS:
        source = path.read_text(encoding="utf-8")
        assert not ACCOUNT_LITERAL.search(source), path.relative_to(PROJECT_ROOT)


def test_the_outputs_bucket_is_private_versioned_and_not_the_lineage_store() -> None:
    """Mutation: point the workload role at the lineage bucket instead of this one.

    The lineage store is write-once by bucket policy and holds admission records. Job output
    goes somewhere a workload may overwrite it, because a workload that cannot overwrite its
    own output is a workload that fails on retry.
    """
    template = load_template(OUTPUTS_PATH)
    _logical_id, bucket = resource_of_type(template, "AWS::S3::Bucket")
    properties = bucket["Properties"]

    assert properties["BucketName"] == OUTPUTS_BUCKET
    assert bucket["DeletionPolicy"] == "Retain"
    assert properties["VersioningConfiguration"] == {"Status": "Enabled"}
    assert properties["PublicAccessBlockConfiguration"] == {
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True,
    }
    # Deliberately not Object Lock and deliberately no conditional-write policy: this store
    # has no immutability guarantee to protect, and claiming one it does not enforce would be
    # worse than claiming none.
    assert "ObjectLockEnabled" not in properties
    assert LINEAGE_BUCKET not in list(walk_strings(template))


def test_every_log_group_a_job_writes_to_is_created_by_some_template() -> None:
    """No stack may send logs to a group only another stack happens to create.

    The awslogs driver takes the group as a plain string, so CloudFormation sees no
    dependency between the stack that names a group and the stack that creates it. Thirteen
    GPU job definitions in batch-compute-gpu-shapes.yaml log to a group created in
    batch-compute-gpu.yaml, and deleting the older stack would leave all thirteen writing
    nowhere. Nothing about that failure is visible at deploy time: jobs keep running, and the
    logs simply stop, which is the worst moment to discover it because the reason a run failed
    is exactly what the group was holding.

    The coupling is allowed and was chosen deliberately, since these shapes run the same image
    under the same approvals and a group per shape would mean thirteen more grants on a role
    that already exists. What is not allowed is the coupling being invisible, so this reads both
    sides out of the templates rather than trusting the comment that records it.
    """
    templates = {
        path.relative_to(PROJECT_ROOT): load_template(path)
        for path in sorted(INFRA_ROOT.glob("*.yaml")) + sorted(IAM_ROOT.glob("*.yaml"))
    }

    created: dict[str, Path] = {}
    for source, template in templates.items():
        for resource in template.get("Resources", {}).values():
            if not isinstance(resource, dict) or resource.get("Type") != "AWS::Logs::LogGroup":
                continue
            name = resource.get("Properties", {}).get("LogGroupName")
            # A computed name cannot be compared against a driver string, and treating it as
            # covering everything would turn this check into a rubber stamp.
            assert isinstance(name, str), f"{source} creates a log group with a computed name"
            created[name] = source

    def referenced(value: object) -> Iterator[tuple[str, object]]:
        if isinstance(value, dict):
            for key, nested in value.items():
                if key == "awslogs-group":
                    yield "awslogs-group", nested
                yield from referenced(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from referenced(nested)

    seen = 0
    for source, template in templates.items():
        for _driver_key, group in referenced(template):
            assert isinstance(group, str), f"{source} names a log group this cannot read"
            seen += 1
            assert group in created, (
                f"{source} sends job logs to {group}, which no template under infra/ creates. "
                "Either create it beside the stack that writes to it, or the first thing "
                "anyone learns about the missing group is a failed run with no logs."
            )

    # The count is asserted so that a refactor which stops finding awslogs-group at all fails
    # here rather than passing on an empty loop, which is the way a check like this dies quietly.
    assert seen >= 9, f"expected the GPU shapes and the CPU stack to be found, saw {seen}"
