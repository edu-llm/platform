"""The stack a purchased capacity block is consumed through, resolved and read back.

**NOTHING IN THIS REPOSITORY READ ``infra/batch-capacity-block.yaml`` UNTIL THIS MODULE, AND
THAT FILE'S OWN HEADER IS AN ARGUMENT FOR WHY THAT WAS THE WRONG GAP TO LEAVE.** It opens by
saying that two lines in ``LaunchTemplateData`` are the difference between using a block and
paying for it twice, and that the failure mode is silent: a compute environment pointed at the
right instance type in the right zone with no reservation target does not fail, it launches
ordinary on-demand instances beside a block already paid for in full. No refusal, no
``INVALID`` status, no job stuck in ``RUNNABLE``. The only trace is the bill, weeks later,
after the window has expired and the money has gone.

``tests/test_deployed_stacks.py`` holds the four stack *names* against the four block-backed
profiles, which is a different question and was the only question anything asked. A template
that deployed cleanly and targeted nothing would pass every check that existed.

**THIS RESOLVES THE TEMPLATE RATHER THAN GREPPING IT, WHICH IS THE ONLY WAY TO ASK THE
QUESTION THAT MATTERS.** The reservation id is a parameter, so the string ``cr-...`` appears
nowhere in the file and a text search for it proves nothing. What has to be true is that the
``Ref`` reaches ``CapacityReservationTarget`` in the launch template that the compute
environment actually names, and that is a path through three resources. :func:`resolved`
substitutes the parameters a real purchase supplies and returns the document CloudFormation
would act on, so every assertion below reads a value rather than a template expression.

The parameters are the 9 August 2026 ``p6-b200.48xlarge`` offering, because a worked example
with the shape of a real purchase catches a mistake that a placeholder does not: 192 vCPU is
one instance's worth and ``us-east-1d`` is a zone this VPC has a subnet in. The ids are not
real -- no block has been bought -- and nothing here depends on them being real.

**WHAT THIS STILL CANNOT SAY.** Whether AWS Batch honours ``InstanceMarketOptions`` in a launch
template at all. The Batch launch template reference lists it among the parameters Batch
ignores and the AWS HPC blog for this exact pattern says to set it; the template follows the
blog and argues why. If the reference turns out to be right, everything below still passes and
the reservation's used-capacity count stays at zero. That check needs an AWS credential, a
purchased block, and a person reading the EC2 console, and it is written down in
``guides/capacity-blocks.md`` rather than here.
"""

from __future__ import annotations

from typing import Any

import pytest
from infrastructure_support import INFRA_ROOT, load_template

from edullm_platform.execution import CONTAINER_SHAPES

TEMPLATE_PATH = INFRA_ROOT / "batch-capacity-block.yaml"

#: One purchase's worth of parameters, shaped like the 9 August 2026 B200 offering.
#:
#: ``MaxvCpus`` is the whole block expressed in vCPU -- one ``p6-b200.48xlarge`` at 192 -- which
#: is AWS's guidance and is not a spend control: the window is charged upfront in full, so a
#: lower number returns nothing and only decides how much of what was bought can be used.
#:
#: The four container values are read from ``CONTAINER_SHAPES`` rather than typed, because that
#: is where the deploy workflow reads them from: the point of the parameters is that the figures
#: exist once. Typing them here would make this module a second place they are written down, and
#: the memory figure in particular is one this platform has already paid to get wrong.
SHAPE = CONTAINER_SHAPES["gpu-8xb200"]

PARAMETERS = {
    "InstanceType": "p6-b200.48xlarge",
    "CapacityReservationId": "cr-0123456789abcdef0",
    "AvailabilityZone": "us-east-1d",
    "SubnetId": "subnet-0123456789abcdef0",
    "MaxvCpus": 192,
    "ContainerVcpus": SHAPE.vcpus,
    "ContainerMemoryMiB": SHAPE.memory_mib,
    "GpuCount": SHAPE.gpus,
    "SharedMemoryMiB": SHAPE.shared_memory_mib,
}

STACK_NAME = "sbsandbox-intern-edullm-capacity-block-gpu-8xb200"
ACCOUNT = "123456789012"
PARTITION = "aws"
REGION = "us-east-1"

PSEUDO_PARAMETERS = {
    "AWS::StackName": STACK_NAME,
    "AWS::AccountId": ACCOUNT,
    "AWS::Partition": PARTITION,
    "AWS::Region": REGION,
}


def substitute(template: str) -> str:
    """One ``Fn::Sub`` body with every ``${...}`` this template can carry replaced.

    Refuses an unresolved one rather than leaving it in the output, because a ``${Something}``
    surviving into a value under test is the shape of every assertion that passes by comparing
    two pieces of unresolved template to each other.
    """
    resolved = template
    for name, value in {**PSEUDO_PARAMETERS, **{k: str(v) for k, v in PARAMETERS.items()}}.items():
        resolved = resolved.replace("${" + name + "}", value)
    assert "${" not in resolved, f"{template!r} still carries an unresolved substitution"
    return resolved


def resolve(node: object, *, references: dict[str, str]) -> Any:
    """The template with ``Ref``, ``Fn::Sub`` and ``Fn::GetAtt`` evaluated.

    ``Fn::ImportValue`` is left as a marker rather than resolved, because what it imports is
    another stack's output and this module has no account to read one from. Naming it in the
    output is enough for the one assertion that cares, which is that the security group comes
    from the network stack rather than from a literal somebody typed.
    """
    if isinstance(node, dict):
        if set(node) == {"Ref"}:
            name = node["Ref"]
            if name in PARAMETERS:
                return PARAMETERS[name]
            if name in PSEUDO_PARAMETERS:
                return PSEUDO_PARAMETERS[name]
            assert name in references, f"Ref to {name}, which is neither a parameter nor a resource"
            return references[name]
        if set(node) == {"Fn::Sub"}:
            body = node["Fn::Sub"]
            # The two-argument form, whose second element is a map of names computed from other
            # intrinsics. Resolved by evaluating the map first and then substituting, which is
            # the order CloudFormation uses; the log stream prefix is built this way so that it
            # derives from the stack name rather than from a parameter that could disagree.
            if isinstance(body, list):
                text, variables = body
                for name, value in resolve(variables, references=references).items():
                    text = text.replace("${" + name + "}", str(value))
                return substitute(text)
            return substitute(body)
        if set(node) == {"Fn::Select"}:
            index, values = node["Fn::Select"]
            return resolve(values, references=references)[int(index)]
        if set(node) == {"Fn::Split"}:
            delimiter, value = node["Fn::Split"]
            return resolve(value, references=references).split(delimiter)
        if set(node) == {"Fn::GetAtt"}:
            attribute = node["Fn::GetAtt"]
            path = attribute if isinstance(attribute, str) else ".".join(attribute)
            return f"<GetAtt {path}>"
        if set(node) == {"Fn::ImportValue"}:
            return f"<ImportValue {node['Fn::ImportValue']}>"
        if set(node) == {"Fn::Base64"}:
            return node["Fn::Base64"]
        return {key: resolve(value, references=references) for key, value in node.items()}
    if isinstance(node, list):
        return [resolve(value, references=references) for value in node]
    return node


@pytest.fixture(scope="module")
def template() -> dict[str, Any]:
    return load_template(TEMPLATE_PATH)


def logical_ids(template: dict[str, Any]) -> dict[str, str]:
    """Each resource's ``Ref`` value as a marker naming the resource it came from.

    A marker rather than a made-up ARN, so an assertion reads "this points at that resource"
    instead of comparing two strings this module invented.
    """
    return {logical_id: f"<{logical_id}>" for logical_id in template["Resources"]}


@pytest.fixture(scope="module")
def resolved(template: dict[str, Any]) -> dict[str, Any]:
    """The template as CloudFormation would act on it for one real purchase."""
    return resolve(template["Resources"], references=logical_ids(template))


def launch_template_data(resolved: dict[str, Any]) -> dict[str, Any]:
    data = resolved["CapacityBlockLaunchTemplate"]["Properties"]["LaunchTemplateData"]
    assert isinstance(data, dict)
    return data


# --------------------------------------------------------------------------------------
# The two lines the whole stack exists for
# --------------------------------------------------------------------------------------


def test_the_launch_names_the_capacity_block_market(resolved: dict[str, Any]) -> None:
    """Mutation: drop ``InstanceMarketOptions``, or spell the market anything else.

    The first of the two. Without ``capacity-block`` the launch is an ordinary on-demand
    launch and EC2 has no reason to draw it from a reservation it was not asked for -- which
    succeeds, at $113.93 an hour, beside a block sitting idle for the whole window.
    """
    assert launch_template_data(resolved)["InstanceMarketOptions"] == {
        "MarketType": "capacity-block"
    }


def test_the_reservation_id_reaches_the_launch_template_as_a_target(
    resolved: dict[str, Any],
) -> None:
    """Mutation: drop ``CapacityReservationTarget``, or point the ``Ref`` at another parameter.

    The second, and the one with the sharper failure. A capacity block is a *targeted*
    reservation: nothing consumes it unless the launch names it by id. This asserts the
    resolved value rather than the ``Ref``, because the parameter is what a purchase supplies
    and a ``Ref`` to the wrong parameter is a template that deploys and targets nothing.
    """
    assert launch_template_data(resolved)["CapacityReservationSpecification"] == {
        "CapacityReservationTarget": {"CapacityReservationId": PARAMETERS["CapacityReservationId"]}
    }


def test_no_capacity_reservation_preference_sits_beside_the_target(
    resolved: dict[str, Any],
) -> None:
    """Mutation: add ``CapacityReservationPreference: open`` beside the target.

    The sibling property, and it is the double-bill written down as configuration. It takes
    ``open`` or ``none`` and both are mutually exclusive with a target; ``open`` means EC2 uses
    any reservation that happens to match, which for a targeted block is none of them. Every
    instance would launch on demand next to the paid block and nothing would report it.

    Asserted as an absence because an absence is what the template has to keep, and an absence
    is the one thing a reader skimming for the reservation id will not notice arriving.
    """
    specification = launch_template_data(resolved)["CapacityReservationSpecification"]

    assert "CapacityReservationPreference" not in specification


# --------------------------------------------------------------------------------------
# Everything else in the file is in service of those two reaching a real instance
# --------------------------------------------------------------------------------------


def test_the_compute_environment_uses_the_launch_template_this_stack_creates(
    resolved: dict[str, Any],
) -> None:
    """Mutation: delete the ``LaunchTemplate`` block from the compute environment.

    The path the two lines above travel. A compute environment naming no launch template
    deploys cleanly, launches instances, and draws none of them from the reservation -- and it
    would also lose the 500 GiB root, which is the failure
    ``infra/batch-compute-gpu-shapes.yaml`` records as a 37.2 GiB corpus that did not fit.

    The version is pinned to ``LatestVersionNumber`` rather than ``$Latest`` because Batch
    resolves it once, when the environment is created, so a later edit to the launch template
    would not otherwise reach an environment that named the moving alias.
    """
    resources = resolved["CapacityBlockComputeEnvironment"]["Properties"]["ComputeResources"]

    assert resources["LaunchTemplate"] == {
        "LaunchTemplateId": "<CapacityBlockLaunchTemplate>",
        "Version": "<GetAtt CapacityBlockLaunchTemplate.LatestVersionNumber>",
    }


def test_the_environment_is_pinned_to_the_one_type_and_the_one_zone_the_block_covers(
    resolved: dict[str, Any],
) -> None:
    """Mutation: add the shape's on-demand instance type as a fallback. Mutation: add a subnet.

    A reservation covers one instance type in one availability zone, so both lists are one
    entry and the sibling GPU environments' habits are wrong here rather than worth copying.
    ``gpu-8xh100`` lists two types deliberately, on the strength of 6,815 consecutive capacity
    refusals; a block is the purchase that makes that scarcity not apply, and the fallback that
    rescues an on-demand environment is the one that wastes a reserved one. Every other GPU
    environment lists four to six subnets, and a second subnet here is a zone the reservation
    does not exist in.

    ``Placement`` restates the zone inside the launch template, so a ``SubnetId`` that disagrees
    with ``AvailabilityZone`` is a launch that fails rather than an instance outside the block.
    """
    resources = resolved["CapacityBlockComputeEnvironment"]["Properties"]["ComputeResources"]

    assert resources["InstanceTypes"] == [PARAMETERS["InstanceType"]]
    assert resources["Subnets"] == [PARAMETERS["SubnetId"]]
    assert launch_template_data(resolved)["Placement"] == {
        "AvailabilityZone": PARAMETERS["AvailabilityZone"]
    }


def test_the_environment_boots_the_gpu_ami_and_allocates_the_way_a_block_requires(
    resolved: dict[str, Any],
) -> None:
    """Mutation: drop ``Ec2Configuration``. Mutation: make the strategy progressive.

    ``ECS_AL2023_NVIDIA`` is the line the whole GPU estate turns on: omitted, this defaults to
    the plain AL2023 AMI, which has no NVIDIA driver and no container runtime hook, so the
    instance joins the cluster, accepts the job and runs it on the CPU at GPU prices. On a
    capacity block that costs the window rather than an hour, and it costs it after the money
    is spent.

    ``BEST_FIT`` is not a preference: AWS documents it as a requirement for an environment
    backed by a capacity block. It is also why the environment declares no name -- BEST_FIT
    cannot take an in-place infrastructure update, so every edit is a CloudFormation
    replacement, and a fixed physical name makes a replacement collide with itself.
    """
    environment = resolved["CapacityBlockComputeEnvironment"]["Properties"]
    resources = environment["ComputeResources"]

    assert resources["Ec2Configuration"] == [{"ImageType": "ECS_AL2023_NVIDIA"}]
    assert resources["AllocationStrategy"] == "BEST_FIT"
    assert resources["MaxvCpus"] == PARAMETERS["MaxvCpus"]
    assert resources["MinvCpus"] == 0
    assert "ComputeEnvironmentName" not in environment


def test_the_queue_takes_its_name_from_the_stack_and_attaches_only_this_environment(
    resolved: dict[str, Any],
) -> None:
    """Mutation: add the shape's on-demand queue's environment as a second entry.

    The queue name is the string that gets pasted into ``config/execution-targets.yaml``, and
    taking it from the stack is what makes two concurrent blocks deployable at all -- a Batch
    queue name is unique per account and region. A stack named anything other than the row in
    ``src/edullm_platform/stack_templates.py`` deploys perfectly and produces a queue no
    reviewed configuration can reach.

    One environment, because the obvious "fallback" second entry is the double-bill again: a
    job that could not be placed on the block would launch the same shape on demand beside one
    already paid for.
    """
    queue = resolved["CapacityBlockJobQueue"]["Properties"]

    assert queue["JobQueueName"] == STACK_NAME
    assert queue["State"] == "ENABLED"
    assert queue["ComputeEnvironmentOrder"] == [
        {"Order": 1, "ComputeEnvironment": "<CapacityBlockComputeEnvironment>"}
    ]


def test_the_queue_does_not_cancel_a_job_waiting_for_its_window_to_open(
    resolved: dict[str, Any],
) -> None:
    """Mutation: carry the third cancel the on-demand GPU queues have.

    Every other GPU queue in this account cancels a ``RUNNABLE`` job after 1800 seconds under
    ``CAPACITY:INSUFFICIENT_INSTANCE_CAPACITY``, because on an on-demand queue a job nobody can
    place for half an hour is a job nobody is going to place. On this queue that state is the
    intended operating mode: the procedure is to submit the day before so the job is admitted,
    approved and waiting when the block activates, and before the window EC2 declines every
    allocation. Carrying that rule would destroy the one submission the guide asks for, thirty
    minutes after it was made and about twenty-three hours before the machine arrives.

    The two ``MISCONFIGURATION`` cancels stay: both describe a job that is statically wrong,
    and neither becomes right when a window opens.
    """
    reasons = {
        action["Reason"]
        for action in resolved["CapacityBlockJobQueue"]["Properties"]["JobStateTimeLimitActions"]
    }

    assert reasons == {
        "MISCONFIGURATION:COMPUTE_ENVIRONMENT_MAX_RESOURCE",
        "MISCONFIGURATION:JOB_RESOURCE_REQUIREMENT",
    }


def test_the_root_volume_is_the_one_measured_on_a_real_p_family_host(
    resolved: dict[str, Any],
) -> None:
    """Mutation: take the 500 back to the AMI's 30, or to anything else.

    A second copy of ``PFamilyLaunchTemplate``'s block device mapping, and the copy is the
    thing to be careful about: a compute environment takes one launch template, that one lives
    in another stack, and a size change applied there and not here is a block window that runs
    out of disk. ``infra/batch-compute-gpu-shapes.yaml`` measured this on a real host after a
    37.2 GiB corpus did not fit in 30 GiB and three jobs inherited each other's leftovers.
    """
    assert launch_template_data(resolved)["BlockDeviceMappings"] == [
        {
            "DeviceName": "/dev/xvda",
            "Ebs": {
                "VolumeSize": 500,
                "VolumeType": "gp3",
                "Throughput": 250,
                "DeleteOnTermination": True,
            },
        }
    ]


def test_the_ecs_agent_settings_reserved_to_batch_are_not_set_here(
    resolved: dict[str, Any],
) -> None:
    """Mutation: add ``ECS_CLUSTER`` to the user data, which is what a copy from a blog does.

    Batch writes ``ECS_CLUSTER`` and ``ECS_INSTANCE_ATTRIBUTES`` itself and names them
    reserved; setting either from a launch template is documented to break scheduling and
    scaling. The four that are set are the cleanup settings, and the first gates the other
    three -- image cleanup cannot touch an image a container still references.
    """
    user_data = launch_template_data(resolved)["UserData"]

    assert "ECS_ENGINE_TASK_CLEANUP_WAIT_DURATION=15m" in user_data
    assert "ECS_CLUSTER" not in user_data
    assert "ECS_INSTANCE_ATTRIBUTES" not in user_data


def test_every_parameter_a_purchase_supplies_is_shaped_before_it_is_deployed(
    template: dict[str, Any],
) -> None:
    """Mutation: drop any ``AllowedPattern``, or widen the zone one past us-east-1.

    CloudFormation cannot ask EC2 whether a reservation id is real and neither can this
    repository, so these patterns are the only check anything makes on four values typed off a
    console at the start of a paid window. The zone pattern is deliberately region-locked:
    every subnet, queue and role on this platform is in ``us-east-1``, so a block bought
    elsewhere is a block nothing here can reach, and a refusal at deploy time naming the
    parameter is a better place to find that out than a compute environment that comes up and
    never places anything.
    """
    parameters = template["Parameters"]

    assert set(parameters) == set(PARAMETERS)
    assert parameters["CapacityReservationId"]["AllowedPattern"] == r"^cr-[0-9a-f]{8,17}$"
    assert parameters["AvailabilityZone"]["AllowedPattern"] == r"^us-east-1[a-f]$"
    assert parameters["SubnetId"]["AllowedPattern"] == r"^subnet-[0-9a-f]{8,17}$"
    # The five numbers, none of which may be zero or negative. Nothing here can check them
    # against the hardware -- that is what the seam test against CONTAINER_SHAPES is for -- but
    # a zero is the one wrong value CloudFormation will accept and Batch will not.
    for name in ("MaxvCpus", "ContainerVcpus", "ContainerMemoryMiB", "GpuCount", "SharedMemoryMiB"):
        assert parameters[name]["Type"] == "Number", name
        assert parameters[name]["MinValue"] == 1, name


def test_the_outputs_print_what_reviewed_configuration_and_the_post_deploy_check_need(
    resolved: dict[str, Any], template: dict[str, Any]
) -> None:
    """Mutation: drop the queue name output, or the reservation id one.

    Two of these are read by a person rather than by a machine and both are read at the worst
    moment. The queue name is what goes into ``config/execution-targets.yaml`` and is not
    readable off this file, because it is derived from the stack name. The reservation id is
    echoed so that the one check nothing here can perform -- that the block's used capacity
    moves off zero when the first job places -- is made against the stack rather than against
    whatever note the parameter was typed from.
    """
    outputs = resolve(template["Outputs"], references=logical_ids(template))

    assert outputs["JobQueueName"]["Value"] == STACK_NAME
    assert outputs["CapacityReservationId"]["Value"] == PARAMETERS["CapacityReservationId"]


# ---------------------------------------------------------------------------------------
# The job definition, which is here rather than in the on-demand estate
# ---------------------------------------------------------------------------------------


def job_definition(resolved: dict[str, Any]) -> dict[str, Any]:
    properties = resolved["CapacityBlockJobDefinition"]["Properties"]
    assert isinstance(properties, dict)
    return properties


def test_the_job_definition_asks_for_exactly_what_the_container_shape_declares(
    resolved: dict[str, Any],
) -> None:
    """Mutation: ask for the advertised instance memory instead of the measured ceiling.

    The four numbers reach this template as parameters filled from ``CONTAINER_SHAPES``, and
    this is the assertion that the template spends them where it said it would rather than
    carrying a literal beside them. Memory is the one that matters: over the host's registered
    figure the job parks in RUNNABLE with nothing telling the submitter why, which inside a paid
    window is the window; under it, an out-of-memory kill hours in.
    """
    properties = job_definition(resolved)
    container = properties["ContainerProperties"]

    assert container["ResourceRequirements"] == [
        {"Type": "VCPU", "Value": str(SHAPE.vcpus)},
        {"Type": "MEMORY", "Value": str(SHAPE.memory_mib)},
        {"Type": "GPU", "Value": str(SHAPE.gpus)},
    ]
    assert container["LinuxParameters"]["SharedMemorySize"] == SHAPE.shared_memory_mib
    # Both roles, because Batch takes them when a definition is registered and nowhere else,
    # and they are different identities on purpose: the execution role pulls the image and the
    # workload role is what an untrusted training command runs as.
    assert container["ExecutionRoleArn"].endswith(
        ":role/sbsandbox-intern-edullm-batch-gpu-execution"
    )
    assert container["JobRoleArn"].endswith(":role/sbsandbox-intern-edullm-batch-gpu-workload")
    assert container["Privileged"] is False


def test_the_definition_is_named_and_files_its_logs_the_way_a_run_will_look_for_them(
    resolved: dict[str, Any],
) -> None:
    """Mutation: hardcode the stream prefix, or create a log group in this stack.

    Neither of these is checkable from AWS after the fact without a run to look at, and both
    fail quietly. ``execution.py`` derives a run's stream prefix from the deployed definition's
    name with this project's resource prefix removed, so the two have to agree or a run's output
    lands under a name ``edullm logs`` does not read. And the group is the shared GPU one that
    ``infra/batch-compute-gpu.yaml`` creates: declaring it here would both scatter one project's
    runs across groups that come and go with purchases, and fail this deploy on a group that
    already exists.
    """
    properties = job_definition(resolved)
    container = properties["ContainerProperties"]

    assert properties["JobDefinitionName"] == f"{STACK_NAME}-run"
    options = container["LogConfiguration"]["Options"]
    assert options["awslogs-group"] == "/aws/batch/sbsandbox-intern-edullm-gpu"
    assert options["awslogs-stream-prefix"] == "capacity-block-gpu-8xb200-run"
    assert "AWS::Logs::LogGroup" not in {
        resource.get("Type") for resource in resolved.values() if isinstance(resource, dict)
    }


def test_the_definition_declares_the_keys_a_submission_has_to_override(
    resolved: dict[str, Any],
) -> None:
    """Mutation: drop Command, or drop Environment.

    ``ContainerOverrides`` can only override a key the definition already declares, so an
    omission here is not a default -- it is a submission whose command or output prefix is
    silently dropped, and a job that runs the image's entrypoint against a bucket path nobody
    reads. The default command names the definition so an unoverridden run says so in its log.
    """
    container = job_definition(resolved)["ContainerProperties"]

    assert container["Command"] == [
        "python",
        "-c",
        f'print("{STACK_NAME}-run: no command override was supplied")',
    ]
    assert [entry["Name"] for entry in container["Environment"]] == [
        name for name, _ in SHAPE.default_environment
    ]
    assert [entry["Name"] for entry in container["Secrets"]] == [name for name, _ in SHAPE.secrets]
